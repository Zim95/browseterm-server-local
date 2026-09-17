# builtins
from unittest import TestCase
from typing import Dict, Any
import asyncio
from unittest.mock import patch, MagicMock
import json

# local
from src.authentication.authentication_service import AuthenticationService, CSRF_COOKIE_NAME
from src.cloud_client.client import CloudClient, CloudClientError
from fastapi.responses import Response


class TestCompleteLoginFromHandoff(TestCase):
    '''
    P07: login now completes by redeeming a one-time handoff code Cloud already minted, not by
    exchanging a provider code directly (that moved to Cloud - see
    browseterm-server/src/cloud/oauth_handlers.py).
    '''

    def setUp(self) -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.service = AuthenticationService()

    def tearDown(self) -> None:
        self.loop.close()

    @patch('src.authentication.authentication_service.CloudClient')
    def test_valid_handoff_establishes_session_and_csrf_cookies(self, mock_client_cls) -> None:
        mock_client = MagicMock(spec=CloudClient)
        mock_client.redeem_handoff.return_value = {
            'session_id': 'test-session-123',
            'user_info': {'id': 'u1', 'name': 'Test User'},
            'subscription_info': {'id': 'sub1'},
            'current_subscription_plan': {'id': 'plan1'},
        }
        mock_client_cls.return_value = mock_client

        response: Response = self.loop.run_until_complete(self.service.complete_login_from_handoff('handoff-code-1'))

        self.assertEqual(response.status_code, 200)
        joined = ' '.join(response.headers.getlist('set-cookie'))
        self.assertIn('session=', joined)
        self.assertIn(f'{CSRF_COOKIE_NAME}=', joined)
        body: Dict[str, Any] = json.loads(response.body)
        self.assertEqual(body['session_id'], 'test-session-123')
        mock_client.redeem_handoff.assert_called_once_with('handoff-code-1')

    @patch('src.authentication.authentication_service.CloudClient')
    def test_invalid_or_expired_handoff_returns_error_response(self, mock_client_cls) -> None:
        mock_client = MagicMock(spec=CloudClient)
        mock_client.redeem_handoff.side_effect = CloudClientError(401, 'Invalid or expired handoff code')
        mock_client_cls.return_value = mock_client

        response: Response = self.loop.run_until_complete(self.service.complete_login_from_handoff('bogus'))

        self.assertEqual(response.status_code, 400)
        body = json.loads(response.body)
        self.assertIn('error', body)


class TestLogout(TestCase):
    def setUp(self) -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.service = AuthenticationService()

    def tearDown(self) -> None:
        self.loop.close()

    @patch('src.authentication.authentication_service.delete_session')
    def test_logout_with_session_id_deletes_it_server_side(self, mock_delete_session) -> None:
        '''p07.md section 31: logout must actually revoke the session server-side, not just clear
        the cookie (a pre-existing bug this migration fixes - api_handlers.logout() previously
        called this with no session_id at all).'''
        mock_delete_session.return_value = None

        response: Response = self.loop.run_until_complete(self.service.logout(session_id='test-session-123'))

        self.assertEqual(response.status_code, 200)
        mock_delete_session.assert_called_once_with('test-session-123')
        cookies = response.headers.get('set-cookie', '')
        self.assertIn('max-age=0', cookies.lower())
        body: Dict[str, Any] = json.loads(response.body)
        self.assertTrue(body['success'])

    @patch('src.authentication.authentication_service.delete_session')
    def test_logout_without_session_id_still_clears_cookie(self, mock_delete_session) -> None:
        response: Response = self.loop.run_until_complete(self.service.logout())
        self.assertEqual(response.status_code, 200)
        mock_delete_session.assert_not_called()
        cookies = response.headers.get('set-cookie', '')
        self.assertIn('max-age=0', cookies.lower())


if __name__ == '__main__':
    import unittest
    unittest.main()
