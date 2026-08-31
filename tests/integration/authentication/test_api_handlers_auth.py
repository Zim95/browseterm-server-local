'''
P07 auth routes in src/api_handlers.py - previously untested at the handler level (only
AuthenticationService's own methods had tests). Covers: CSRF gating (_csrf_ok, logout,
device_bootstrap), auth_provider_redirect, auth_callback's success/failure paths, and the new
POST /auth/refresh (session-refresh heartbeat, see base.js's SessionRefreshManager).
'''
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.responses import Response

import src.api_handlers as api_handlers

CSRF_TOKEN = "csrf-abc123"


def _mock_request(cookies: dict = None, headers: dict = None, query_params: dict = None, user_id: str = "u1") -> MagicMock:
    request = MagicMock(spec=Request)
    request.cookies = cookies if cookies is not None else {}
    request.headers = headers if headers is not None else {}
    request.query_params = query_params or {}
    request.path_params = {}
    request.state.user_info = {"id": user_id}
    return request


def _run(coro):
    return asyncio.run(coro)


class TestCsrfOk(unittest.TestCase):
    def test_matching_header_and_cookie_passes(self):
        request = _mock_request(cookies={"csrf_token": CSRF_TOKEN}, headers={"X-CSRF-Token": CSRF_TOKEN})
        self.assertTrue(api_handlers._csrf_ok(request))

    def test_missing_header_fails(self):
        request = _mock_request(cookies={"csrf_token": CSRF_TOKEN}, headers={})
        self.assertFalse(api_handlers._csrf_ok(request))

    def test_missing_cookie_fails(self):
        request = _mock_request(cookies={}, headers={"X-CSRF-Token": CSRF_TOKEN})
        self.assertFalse(api_handlers._csrf_ok(request))

    def test_mismatched_values_fail(self):
        request = _mock_request(cookies={"csrf_token": CSRF_TOKEN}, headers={"X-CSRF-Token": "different"})
        self.assertFalse(api_handlers._csrf_ok(request))


class TestAuthProviderRedirect(unittest.TestCase):
    def test_redirects_to_cloud_start_with_target_local(self):
        request = _mock_request()
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertEqual(result.status_code, 302)
        self.assertIn("/auth/google/start?target=local", result.headers["location"])


class TestAuthCallback(unittest.TestCase):
    def test_missing_code_redirects_to_login_with_error(self):
        request = _mock_request(query_params={})
        result = _run(api_handlers.auth_callback(request))
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login", result.headers["location"])
        self.assertIn("auth_result=error", result.headers["location"])

    @patch("src.api_handlers.AuthenticationService")
    def test_failed_handoff_redirects_to_login_with_error(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service.complete_login_from_handoff = AsyncMock(return_value=Response(status_code=400))
        mock_service_cls.return_value = mock_service

        request = _mock_request(query_params={"code": "bad-code"})
        result = _run(api_handlers.auth_callback(request))
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login", result.headers["location"])

    @patch("src.api_handlers.AuthenticationService")
    def test_successful_handoff_redirects_home_with_cookies_forwarded(self, mock_service_cls):
        login_response = Response(status_code=200)
        login_response.set_cookie(key="session", value="s1", httponly=True)
        login_response.set_cookie(key="csrf_token", value=CSRF_TOKEN, httponly=False)
        mock_service = MagicMock()
        mock_service.complete_login_from_handoff = AsyncMock(return_value=login_response)
        mock_service_cls.return_value = mock_service

        request = _mock_request(query_params={"code": "good-code"})
        result = _run(api_handlers.auth_callback(request))
        self.assertEqual(result.status_code, 302)
        self.assertIn("/?auth_result=success", result.headers["location"])
        set_cookie_headers = " ".join(result.headers.getlist("set-cookie"))
        self.assertIn("session=s1", set_cookie_headers)
        self.assertIn(f"csrf_token={CSRF_TOKEN}", set_cookie_headers)


class TestLogoutCsrf(unittest.TestCase):
    def test_missing_csrf_rejected_before_touching_session(self):
        request = _mock_request(cookies={"session": "s1"}, headers={})
        result = _run(api_handlers.logout(request))
        self.assertEqual(result.status_code, 403)

    @patch("src.api_handlers.AuthenticationService")
    def test_valid_csrf_proceeds_to_logout(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service.logout = AsyncMock(return_value=Response(status_code=200))
        mock_service_cls.return_value = mock_service

        request = _mock_request(cookies={"session": "s1", "csrf_token": CSRF_TOKEN}, headers={"X-CSRF-Token": CSRF_TOKEN})
        result = _run(api_handlers.logout(request))
        self.assertEqual(result.status_code, 200)
        mock_service.logout.assert_called_once_with(session_id="s1")


class TestDeviceBootstrapCsrf(unittest.TestCase):
    def test_missing_csrf_rejected(self):
        request = _mock_request(cookies={}, headers={})
        result = _run(api_handlers.device_bootstrap.__wrapped__(request=request))
        self.assertEqual(result.status_code, 403)

    @patch("src.api_handlers.CloudClient")
    def test_valid_csrf_calls_cloud_client(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.create_device_bootstrap.return_value = "bootstrap-code-1"
        mock_client_cls.return_value = mock_client

        request = _mock_request(
            cookies={"csrf_token": CSRF_TOKEN}, headers={"X-CSRF-Token": CSRF_TOKEN}, user_id="u1"
        )
        result = _run(api_handlers.device_bootstrap.__wrapped__(request=request))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads(result.body)["code"], "bootstrap-code-1")
        mock_client.create_device_bootstrap.assert_called_once_with("u1")


class TestAuthRefresh(unittest.TestCase):
    def test_no_session_cookie_returns_401(self):
        request = _mock_request(cookies={})
        result = _run(api_handlers.auth_refresh(request))
        self.assertEqual(result.status_code, 401)

    @patch("src.api_handlers.AuthenticationService")
    def test_invalid_session_returns_401(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service.validate_session = AsyncMock(return_value={"is_valid": False})
        mock_service_cls.return_value = mock_service

        request = _mock_request(cookies={"session": "expired-session"})
        result = _run(api_handlers.auth_refresh(request))
        self.assertEqual(result.status_code, 401)

    @patch("src.api_handlers.AuthenticationService")
    def test_valid_session_returns_200_and_extends(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service.validate_session = AsyncMock(return_value={"is_valid": True, "user_info": {"id": "u1"}})
        mock_service_cls.return_value = mock_service

        request = _mock_request(cookies={"session": "valid-session"})
        result = _run(api_handlers.auth_refresh(request))
        self.assertEqual(result.status_code, 200)
        mock_service.validate_session.assert_called_once_with("valid-session")


if __name__ == "__main__":
    unittest.main()
