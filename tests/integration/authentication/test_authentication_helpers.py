# builtins
from unittest import TestCase
from typing import Dict, Any
import asyncio
from unittest.mock import patch, MagicMock

# local
from src.authentication.authentication_helpers import (
    validate_session,
    delete_session,
    authenticate_session,
)
from src.cloud_client.client import CloudClientError
from fastapi import Request
from fastapi.responses import RedirectResponse


class TestAuthenticationHelpers(TestCase):
    '''
    Test authentication helper functions with the Postgres/Redis clients replaced by CloudClient
    (Local holds no DB/Redis client at all - every session operation is a Cloud HTTP call).

    P07: session issuance (process_user_info) moved entirely to Cloud (it now happens inside
    Cloud's own OAuth callback) - Local's authentication_helpers.py no longer has that function at
    all, see src/api_handlers.py:auth_callback for the handoff-redemption flow that replaces it.
    '''

    def setUp(self) -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self) -> None:
        self.loop.close()

    @patch('src.authentication.authentication_helpers.CloudClient')
    def test_validate_session_valid(self, mock_client_cls) -> None:
        mock_client = MagicMock()
        mock_client.validate_session.return_value = {
            'is_valid': True, 'user_info': {'id': 'u1'}, 'subscription_info': {}, 'current_subscription_plan': {},
        }
        mock_client_cls.return_value = mock_client

        result: Dict[str, Any] = self.loop.run_until_complete(validate_session('s1'))
        self.assertTrue(result['is_valid'])

    @patch('src.authentication.authentication_helpers.CloudClient')
    def test_validate_session_cloud_error_treated_as_invalid(self, mock_client_cls) -> None:
        '''A Cloud-call failure never raises out of validate_session - treated as not-valid, so
        a Cloud hiccup logs a user out rather than crashing the request.'''
        mock_client = MagicMock()
        mock_client.validate_session.side_effect = CloudClientError(0, 'connection refused')
        mock_client_cls.return_value = mock_client

        result: Dict[str, Any] = self.loop.run_until_complete(validate_session('s1'))
        self.assertFalse(result['is_valid'])

    @patch('src.authentication.authentication_helpers.CloudClient')
    def test_delete_session_calls_cloud_client(self, mock_client_cls) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        self.loop.run_until_complete(delete_session('s1'))
        mock_client.delete_session.assert_called_once_with('s1')

    @patch('src.authentication.authentication_helpers.CloudClient')
    def test_authenticate_session_decorator_success(self, mock_client_cls) -> None:
        mock_client = MagicMock()
        mock_client.validate_session.return_value = {
            'is_valid': True, 'user_info': {'id': 'u1', 'name': 'Test User'},
            'subscription_info': {'id': 'sub1'}, 'current_subscription_plan': {'id': 'plan1'},
        }
        mock_client_cls.return_value = mock_client

        @authenticate_session
        async def test_handler(request: Request):
            return {'message': 'success', 'user_id': request.state.user_info['id']}

        mock_request: MagicMock = MagicMock(spec=Request)
        mock_request.cookies = {'session': 'valid-session-123'}
        mock_request.state = MagicMock()
        mock_request.headers = {}

        result: Dict[str, Any] = self.loop.run_until_complete(test_handler(request=mock_request))
        self.assertEqual(result['message'], 'success')
        self.assertEqual(result['user_id'], 'u1')

    def test_authenticate_session_decorator_no_cookie(self) -> None:
        @authenticate_session
        async def test_handler(request: Request):
            return {'message': 'success'}

        mock_request: MagicMock = MagicMock(spec=Request)
        mock_request.cookies = {}

        result = self.loop.run_until_complete(test_handler(request=mock_request))
        self.assertIsInstance(result, RedirectResponse)
        self.assertEqual(result.status_code, 302)
        self.assertIn('/login', result.headers['location'])

    @patch('src.authentication.authentication_helpers.CloudClient')
    def test_authenticate_session_decorator_invalid_session(self, mock_client_cls) -> None:
        mock_client = MagicMock()
        mock_client.validate_session.return_value = {'is_valid': False}
        mock_client_cls.return_value = mock_client

        @authenticate_session
        async def test_handler(request: Request):
            return {'message': 'success'}

        mock_request: MagicMock = MagicMock(spec=Request)
        mock_request.cookies = {'session': 'invalid-session-123'}

        result = self.loop.run_until_complete(test_handler(request=mock_request))
        self.assertIsInstance(result, RedirectResponse)
        self.assertEqual(result.status_code, 302)
        self.assertIn('/login', result.headers['location'])
