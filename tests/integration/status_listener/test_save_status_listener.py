# builtins
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

# module under test
from src.status_listener import StatusListenerService


class TestSaveStatusChangeBroadcast(IsolatedAsyncioTestCase):
    '''
    Unit tests for StatusListenerService._poll_user's diffing/broadcast logic.

    StatusListenerService no longer listens to Postgres NOTIFY directly (Local holds no DB
    client at all) - it polls Cloud's container API on an interval and diffs each container's
    row against the last-seen snapshot for that user, broadcasting a 'status_change' or
    'save_status_change' SSE message on any relevant field difference. StatusListenerService is
    a singleton, so we grab the instance, subscribe a fake client queue, and drive _poll_user
    directly with a mocked CloudClient (no live Cloud API needed).
    '''

    def setUp(self) -> None:
        self.service: StatusListenerService = StatusListenerService()
        self.user_id: str = 'user-42'
        with self.service._queues_lock:
            self.service._client_queues.clear()
        self.service._last_seen.clear()

    def tearDown(self) -> None:
        with self.service._queues_lock:
            self.service._client_queues.clear()
        self.service._last_seen.clear()

    def _row(self, **overrides) -> dict:
        row = {
            'id': 'container-123', 'user_id': self.user_id, 'name': 'my-container',
            'status': 'Running', 'save_status': None, 'saved_image': None, 'save_error': None,
            'last_saved_at': None, 'last_save_attempted_at': None, 'updated_at': '2026-07-18T00:00:00Z',
        }
        row.update(overrides)
        return row

    async def test_save_status_change_broadcast_to_user_queue(self) -> None:
        '''A save_status change between two polls enqueues a save_status_change message.'''
        queue: asyncio.Queue = self.service.subscribe(self.user_id)
        client = MagicMock()

        # First poll establishes the baseline (no diff possible yet -- nothing broadcast).
        client.list_containers.return_value = [self._row(save_status='PENDING')]
        await self.service._poll_user(client, self.user_id)
        self.assertTrue(queue.empty())

        # Second poll: save_status changed -- must broadcast.
        client.list_containers.return_value = [self._row(
            save_status='SUCCEEDED', saved_image='registry/my-container:snap',
            last_saved_at='2026-07-18T00:01:00Z', last_save_attempted_at='2026-07-18T00:00:00Z',
        )]
        await self.service._poll_user(client, self.user_id)

        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertEqual(message['type'], 'save_status_change')
        self.assertEqual(message['container_id'], 'container-123')
        self.assertEqual(message['user_id'], self.user_id)
        self.assertEqual(message['name'], 'my-container')
        self.assertEqual(message['save_status'], 'SUCCEEDED')
        self.assertEqual(message['saved_image'], 'registry/my-container:snap')
        self.assertIsNone(message['save_error'])
        self.assertEqual(message['last_saved_at'], '2026-07-18T00:01:00Z')

    async def test_save_status_change_not_sent_to_other_users(self) -> None:
        '''A save_status change for one user is never delivered to a different user's queue.'''
        other_queue: asyncio.Queue = self.service.subscribe('someone-else')
        client = MagicMock()

        client.list_containers.return_value = [self._row(save_status='PENDING')]
        await self.service._poll_user(client, self.user_id)

        client.list_containers.return_value = [self._row(save_status='SUCCEEDED')]
        await self.service._poll_user(client, self.user_id)

        self.assertTrue(other_queue.empty())

    async def test_status_change_broadcast_separately_from_save_status(self) -> None:
        '''A pod-status change (not save-related) emits a status_change message, not
        save_status_change.'''
        queue: asyncio.Queue = self.service.subscribe(self.user_id)
        client = MagicMock()

        client.list_containers.return_value = [self._row(status='Pending')]
        await self.service._poll_user(client, self.user_id)

        client.list_containers.return_value = [self._row(status='Running')]
        await self.service._poll_user(client, self.user_id)

        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertEqual(message['type'], 'status_change')
        self.assertEqual(message['old_status'], 'Pending')
        self.assertEqual(message['new_status'], 'Running')

    async def test_no_change_broadcasts_nothing(self) -> None:
        '''Two consecutive polls with identical data must not broadcast anything.'''
        queue: asyncio.Queue = self.service.subscribe(self.user_id)
        client = MagicMock()

        client.list_containers.return_value = [self._row()]
        await self.service._poll_user(client, self.user_id)
        await self.service._poll_user(client, self.user_id)

        self.assertTrue(queue.empty())
