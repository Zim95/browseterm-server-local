"""
The ONLY intended boundary through which Local code talks to central Cloud state.

    Local Handler
          |
          v
      CloudClient
          |
        HTTPS
          |
          v
    Cloud browseterm-server (browseterm.cloud.com)

Local holds no PostgreSQL/Redis client at all - every read or write of central state (sessions,
users, containers, images, subscriptions) goes through here. Two different auth modes, matching
who's asking:

- Handoff redemption (`redeem_handoff`): public but possession-gated - no credential of Local's
  own involved, security comes from holding the one-time code Cloud's OAuth callback minted.
- Everything else (session validate/delete, device-bootstrap start, container/catalog/
  subscription API): called by Local's own backend server-to-server, gated by a shared secret
  (CLOUD_INTERNAL_API_TOKEN / X-Internal-Service-Token) - Local already knows the authenticated
  user_id from Cloud's own session validation by the time it calls these.

P07 (see `~/browseterm/p07.md`) moved OAuth issuance and the Device Cloud API off this client
entirely: Local no longer performs token exchange (so `create_session` here now has no caller -
Cloud's own OAuth callback creates the session in-process instead) and no longer calls the Device
API at all (that moved to Bearer-device-token auth, called by `browseterm-desktop` directly
through its own separate `CloudClient` - see that repo's README).
"""
from typing import Any, Optional

import httpx

from src.cloud_client.config import BROWSETERM_CLOUD_API_URL, CLOUD_INTERNAL_API_TOKEN, SESSION_COOKIE_NAME


class CloudClientError(Exception):
    """Raised for any non-2xx Cloud API response, or a transport-level failure (status_code=0)."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Cloud API error {status_code}: {message}")


class CloudClient:
    """Thin authenticated HTTP client for the Cloud API."""

    def __init__(
        self,
        base_url: str = BROWSETERM_CLOUD_API_URL,
        session_cookie: Optional[str] = None,
        internal_token: str = CLOUD_INTERNAL_API_TOKEN,
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._session_cookie = session_cookie
        self._internal_token = internal_token
        self._timeout = timeout

    def _cookies(self) -> dict:
        if not self._session_cookie:
            return {}
        return {SESSION_COOKIE_NAME: self._session_cookie}

    def _headers(self) -> dict:
        if not self._internal_token:
            return {}
        return {"X-Internal-Service-Token": self._internal_token}

    async def _request(
        self, method: str, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None
    ) -> dict:
        url = f"{self._base_url}{path}"
        # A real httpx.AsyncClient() is opened and closed per call, same one-shot-connection
        # shape the old httpx.request(...) call had - deliberately not a shared/pooled client,
        # to keep this change to "make the existing behavior non-blocking" and nothing more.
        # Now that Cloud is a real remote host (not the same machine/cluster), a plain sync
        # httpx.request(...) here blocked FastAPI's single event loop for the full network
        # round-trip on every single call site (session validation runs on nearly every
        # authenticated request) - freezing every other in-flight request, including kubelet's
        # own liveness/readiness probes, and crash-looping the pod under any real concurrency.
        client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.request(
                method,
                url,
                json=json_body,
                params=params,
                cookies=self._cookies(),
                headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise CloudClientError(0, str(e)) from e
        finally:
            await client.aclose()
        if response.status_code >= 400:
            try:
                message = response.json().get("error", response.text)
            except Exception:
                message = response.text
            raise CloudClientError(response.status_code, message)
        return response.json()

    # ---- Session/auth API - internal-service auth ----

    async def create_session(self, user_info: dict[str, Any]) -> dict:
        """POST /auth/sessions. Returns {session_id, user_info, subscription_info,
        current_subscription_plan}."""
        return await self._request("POST", "/auth/sessions", json_body=user_info)

    async def validate_session(self, session_id: str) -> dict:
        """POST /auth/sessions/validate. Returns {is_valid, user_info?, subscription_info?,
        current_subscription_plan?} - never raises on an invalid session, check is_valid."""
        return await self._request("POST", "/auth/sessions/validate", json_body={"session_id": session_id})

    async def delete_session(self, session_id: str) -> None:
        """POST /auth/sessions/delete."""
        await self._request("POST", "/auth/sessions/delete", json_body={"session_id": session_id})

    async def create_websocket_token(self, session_id: str) -> str:
        """POST /auth/websocket-tokens. One-time, 60s-TTL token linking to the session, consumed
        by socket-ssh."""
        return (await self._request("POST", "/auth/websocket-tokens", json_body={"session_id": session_id}))["token"]

    async def create_sse_token(self, session_id: str) -> str:
        """POST /auth/sse-tokens (P10). NOT single-use, unlike create_websocket_token - the
        browser's EventSource presents the same token again on every automatic reconnect. Used to
        authenticate the browser's direct connection to Cloud's GET /events/stream."""
        return (await self._request("POST", "/auth/sse-tokens", json_body={"session_id": session_id}))["token"]

    # ---- OAuth handoff (P07) - public but possession-gated, no internal token needed ----

    async def redeem_handoff(self, code: str) -> dict:
        """POST /auth/handoff/redeem. Returns {session_id, user_info, subscription_info,
        current_subscription_plan} - raises CloudClientError(status_code=401) for an invalid/
        expired/already-used code."""
        return await self._request("POST", "/auth/handoff/redeem", json_body={"code": code})

    async def create_device_bootstrap(self, user_id: str) -> str:
        """POST /auth/device-bootstrap. Internal-service-token auth (same trust as
        create_session/delete_session) - Local has already verified the caller's browser session
        itself before calling this. Returns a one-time bootstrap code for Desktop to redeem
        directly against Cloud's public POST /auth/device-bootstrap/redeem."""
        return (await self._request("POST", "/auth/device-bootstrap", json_body={"user_id": user_id}))["code"]

    # ---- Container/workspace metadata API - internal-service auth ----

    async def create_container(self, container: dict[str, Any]) -> dict:
        """POST /containers. Raises CloudClientError(status_code=409) on a duplicate
        (user_id, name)."""
        return (await self._request("POST", "/containers", json_body=container))["container"]

    async def get_container(self, container_id: str, user_id: str) -> Optional[dict]:
        try:
            return (await self._request("GET", f"/containers/{container_id}", params={"user_id": user_id}))["container"]
        except CloudClientError as e:
            if e.status_code == 404:
                return None
            raise

    async def list_containers(
        self, user_id: str, limit: Optional[int] = None, offset: Optional[int] = None
    ) -> list[dict]:
        params = {"user_id": user_id}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return (await self._request("GET", "/containers", params=params))["containers"]

    async def update_container(self, container_id: str, user_id: str, fields: dict[str, Any]) -> Optional[dict]:
        try:
            return (await self._request(
                "POST", f"/containers/{container_id}", json_body={**fields, "user_id": user_id}
            ))["container"]
        except CloudClientError as e:
            if e.status_code == 404:
                return None
            raise

    async def delete_container(self, container_id: str, user_id: str) -> bool:
        try:
            await self._request("POST", f"/containers/{container_id}/delete", json_body={"user_id": user_id})
            return True
        except CloudClientError as e:
            if e.status_code == 404:
                return False
            raise

    async def resume_container(self, container_id: str, user_id: str) -> dict:
        """POST /containers/{container_id}/resume (P19). device_id is deliberately omitted -
        Cloud auto-resolves the caller's currently-ACTIVE device, same pattern create_container
        already uses (P13) - Local has no established way to know a device_id of its own either.
        Raises CloudClientError (409 if the container isn't currently HIBERNATED or another
        request won a concurrent resume race, 400 if the resolved device lacks capacity)."""
        return (await self._request(
            "POST", f"/containers/{container_id}/resume", json_body={"user_id": user_id}
        ))["container"]

    async def create_terminal_session(self, container_id: str, user_id: str) -> dict:
        """POST /internal/containers/{container_id}/terminal-session (remotetunelling.md Phase
        5/6). Returns {websocket_url, ticket, expires_at} - Cloud has already validated ownership,
        that the container is RUNNING, and that its device's tunnel is currently online. Raises
        CloudClientError (404 not found/not owned, 409 not running or device offline)."""
        return await self._request(
            "POST", f"/internal/containers/{container_id}/terminal-session", json_body={"user_id": user_id}
        )

    async def hibernate_container(self, container_id: str) -> None:
        """POST /internal/containers/{container_id}/hibernate (P18). No user_id needed - this is
        the same trusted-SYSTEM-caller route the reaper uses; Local reuses it as-is for P19's
        resume rollback-on-failure path (see api_handlers.resume_container)."""
        await self._request("POST", f"/internal/containers/{container_id}/hibernate", json_body={})

    # ---- Catalog / subscription API - internal-service auth ----

    async def list_images(self) -> list[dict]:
        return (await self._request("GET", "/catalog/images"))["images"]

    async def list_subscription_types(self) -> list[dict]:
        return (await self._request("GET", "/catalog/subscription-types"))["subscription_types"]

    async def get_current_subscription(self, user_id: str) -> dict:
        return (await self._request("GET", "/subscriptions/current", params={"user_id": user_id}))["subscription_type"]

    async def get_active_device(self, user_id: str) -> Optional[dict]:
        '''GET /internal/users/{user_id}/active-device. Local never holds a device Bearer token
        (that credential belongs to Desktop alone) so it can't call Cloud's Bearer-gated /devices
        route directly - this is the trusted-SYSTEM-caller route instead, same internal-token
        pattern as every other call in this file. Returns None (not an error) when the user has
        no active device -- Profile's normal "nothing to show" case.'''
        return (await self._request("GET", f"/internal/users/{user_id}/active-device"))["device"]
