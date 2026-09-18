'''
Handler-level tests for api_handlers.terminal_session (remotetunelling.md Phase 5/6).

Same "undecorated handler + patch the boundary" convention test_resume_container.py already
establishes: authenticate_session is bypassed via .__wrapped__, get_container_by_id and
CloudClient are patched at their import site in src.api_handlers.
'''
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

import src.api_handlers as api_handlers
from src.cloud_client.client import CloudClientError


def _mock_request(body: dict, user_id: str = 'user-42') -> MagicMock:
    request: MagicMock = MagicMock(spec=Request)
    request.json = AsyncMock(return_value=body)
    request.state.user_info = {'id': user_id}
    return request


class TestTerminalSession(TestCase):
    def test_owner_gets_the_ticket_cloud_returns(self) -> None:
        cloud_result = {
            "websocket_url": "wss://abcd.ngrok-free.app",
            "ticket": "a-real-ticket",
            "expires_at": "2026-01-01T00:00:30+00:00",
        }
        mock_cloud_client = MagicMock()
        mock_cloud_client.create_terminal_session = AsyncMock(return_value=cloud_result)
        request = _mock_request({'container_id': 'c1'}, user_id='user-42')

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value={'id': 'c1', 'user_id': 'user-42'})), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(api_handlers.terminal_session.__wrapped__(request=request))

        self.assertEqual(result.status_code, 200)
        mock_cloud_client.create_terminal_session.assert_called_once_with('c1', 'user-42')

    def test_container_not_owned_is_not_found_before_ever_calling_cloud(self) -> None:
        mock_cloud_client = MagicMock()
        mock_cloud_client.create_terminal_session = AsyncMock()
        request = _mock_request({'container_id': 'c1'}, user_id='attacker')

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value=None)), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(api_handlers.terminal_session.__wrapped__(request=request))

        self.assertEqual(result.status_code, 404)
        mock_cloud_client.create_terminal_session.assert_not_called()

    def test_cloud_rejection_is_surfaced_with_its_own_status_code(self) -> None:
        mock_cloud_client = MagicMock()
        mock_cloud_client.create_terminal_session = AsyncMock(
            side_effect=CloudClientError(409, "Device is offline")
        )
        request = _mock_request({'container_id': 'c1'}, user_id='user-42')

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value={'id': 'c1', 'user_id': 'user-42'})), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(api_handlers.terminal_session.__wrapped__(request=request))

        self.assertEqual(result.status_code, 409)


if __name__ == "__main__":
    import unittest
    unittest.main()
