'''
GET /device-quota -- lets the terminals page re-check the active device's remaining quota without
a full page reload (see src/api_handlers.py:get_device_quota). Same "mock the boundary" convention
as test_ownership_idor.py: call the undecorated handler via `.__wrapped__`, patch CloudClient at
its import site in src.api_handlers.
'''
import asyncio
from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import Request

import src.api_handlers as api_handlers
from src.cloud_client.client import CloudClient, CloudClientError

USER_A = 'user-a'


def _mock_request(user_id: str = USER_A) -> MagicMock:
    request = MagicMock(spec=Request)
    request.state.user_info = {'id': user_id}
    return request


class TestGetDeviceQuota(TestCase):
    def test_returns_the_active_device(self) -> None:
        device = {'id': 'device-1', 'available_cpu': 4, 'available_memory_bytes': 8_000_000_000}
        mock_client = MagicMock(spec=CloudClient)
        mock_client.get_active_device.return_value = device
        with patch('src.api_handlers.CloudClient', return_value=mock_client):
            result = asyncio.run(api_handlers.get_device_quota.__wrapped__(request=_mock_request()))
        self.assertEqual(result.status_code, 200)
        import json
        self.assertEqual(json.loads(result.body)['device'], device)
        mock_client.get_active_device.assert_called_once_with(USER_A)

    def test_fails_open_to_null_device_on_cloud_error(self) -> None:
        mock_client = MagicMock(spec=CloudClient)
        mock_client.get_active_device.side_effect = CloudClientError(500, 'Cloud unreachable')
        with patch('src.api_handlers.CloudClient', return_value=mock_client):
            result = asyncio.run(api_handlers.get_device_quota.__wrapped__(request=_mock_request()))
        self.assertEqual(result.status_code, 200)
        import json
        self.assertIsNone(json.loads(result.body)['device'])


if __name__ == '__main__':
    import unittest
    unittest.main()
