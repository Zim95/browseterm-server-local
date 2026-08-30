"""
Status listener service - now polls Cloud's container API instead of listening to Postgres
NOTIFY directly. Provides the same SSE endpoint (api_handlers.container_status_sse) for
real-time-ish status updates to the frontend.

This is a deliberate simplification, not the final design: real-time (NOTIFY-driven) push is
P10's job (Cloud SSE) - Cloud would own the LISTEN/NOTIFY connection itself (it's allowed direct
Postgres access) and either push to Local over a server-sent stream or Local would subscribe to
Cloud's own SSE endpoint. Until that lands, this keeps Local's "no DB client at all" invariant by
polling Cloud's existing GET /containers HTTP API on an interval and diffing against the last
known state per user, at the cost of up to POLL_INTERVAL_SECONDS of latency instead of instant
push.
"""

import asyncio
from typing import Dict, Set, Optional
from collections import defaultdict
import threading

from src.cloud_client.client import CloudClient, CloudClientError
from src.common.logging_setup import get_logger

logger = get_logger("status_listener")

POLL_INTERVAL_SECONDS = 3.0

# Fields whose change is worth telling the frontend about, and which SSE event type they map to.
_STATUS_FIELDS = ("status",)
_SAVE_STATUS_FIELDS = ("save_status", "saved_image", "save_error", "last_saved_at", "last_save_attempted_at")


class StatusListenerService:
    """
    Singleton service that polls Cloud's container API on an interval and broadcasts changes to
    connected SSE clients, grouped by user_id.
    """
    _instance: Optional['StatusListenerService'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

        # Map of user_id -> set of asyncio.Queue for SSE clients
        self._client_queues: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._queues_lock = threading.Lock()

        # Last-seen container snapshots per user, to diff against on each poll.
        self._last_seen: Dict[str, Dict[str, dict]] = {}

    def start(self, loop: asyncio.AbstractEventLoop = None):
        """Start the background polling task."""
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.ensure_future(self._poll_loop(), loop=loop)
        logger.info("StatusListenerService started (polling, interval=%ss)", POLL_INTERVAL_SECONDS)

    def stop(self):
        """Stop the background polling task."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        self._running = False
        logger.info("StatusListenerService stopped")

    async def _poll_loop(self):
        client = CloudClient()
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                with self._queues_lock:
                    user_ids = list(self._client_queues.keys())
                for user_id in user_ids:
                    await self._poll_user(client, user_id)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("status poll iteration failed", exc_info=True)

    async def _poll_user(self, client: CloudClient, user_id: str) -> None:
        try:
            containers = await asyncio.to_thread(client.list_containers, user_id)
        except CloudClientError:
            logger.error("status poll: list_containers failed", extra={"user_id": user_id}, exc_info=True)
            return

        previous = self._last_seen.get(user_id, {})
        current = {c["id"]: c for c in containers}
        self._last_seen[user_id] = current

        for container_id, row in current.items():
            old_row = previous.get(container_id)
            if old_row is None:
                continue  # first time seeing this container - nothing to diff against yet
            if any(old_row.get(f) != row.get(f) for f in _STATUS_FIELDS):
                self._broadcast(user_id, {
                    'type': 'status_change',
                    'container_id': container_id,
                    'user_id': user_id,
                    'name': row.get('name'),
                    'old_status': old_row.get('status'),
                    'new_status': row.get('status'),
                    'updated_at': row.get('updated_at'),
                })
            if any(old_row.get(f) != row.get(f) for f in _SAVE_STATUS_FIELDS):
                self._broadcast(user_id, {
                    'type': 'save_status_change',
                    'container_id': container_id,
                    'user_id': user_id,
                    'name': row.get('name'),
                    'save_status': row.get('save_status'),
                    'saved_image': row.get('saved_image'),
                    'save_error': row.get('save_error'),
                    'last_saved_at': row.get('last_saved_at'),
                    'last_save_attempted_at': row.get('last_save_attempted_at'),
                    'updated_at': row.get('updated_at'),
                })

    def _broadcast(self, user_id: str, message: dict) -> None:
        with self._queues_lock:
            queues = self._client_queues.get(user_id, set()).copy()
        for queue in queues:
            queue.put_nowait(message)

    def subscribe(self, user_id: str) -> asyncio.Queue:
        """
        Subscribe a client to status updates for a specific user.
        Returns an asyncio.Queue that will receive status change messages.
        """
        queue = asyncio.Queue()
        with self._queues_lock:
            self._client_queues[user_id].add(queue)
        logger.info(
            "client subscribed",
            extra={"user_id": user_id, "total_clients": len(self._client_queues[user_id])},
        )
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue):
        """Unsubscribe a client from status updates."""
        with self._queues_lock:
            if user_id in self._client_queues:
                self._client_queues[user_id].discard(queue)
                if not self._client_queues[user_id]:
                    del self._client_queues[user_id]
                    self._last_seen.pop(user_id, None)
        logger.info("client unsubscribed", extra={"user_id": user_id})


# Global instance
status_listener_service = StatusListenerService()
