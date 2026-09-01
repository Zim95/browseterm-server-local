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

    def test_no_desktop_cookie_set_for_a_plain_browser_login(self):
        '''Cloud never learns about desktop mode either way - the redirect target stays
        target=local regardless.'''
        request = _mock_request()
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertIn("/auth/google/start?target=local", result.headers["location"])
        self.assertEqual(result.headers.getlist("set-cookie"), [])

    def test_desktop_target_with_valid_port_sets_short_lived_cookie(self):
        request = _mock_request(query_params={"target": "desktop", "desktop_port": "54321"})
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertIn("/auth/google/start?target=local", result.headers["location"])
        set_cookie = " ".join(result.headers.getlist("set-cookie"))
        self.assertIn("desktop_login_port=54321", set_cookie)
        self.assertIn("Max-Age=300", set_cookie)

    def test_desktop_target_without_a_port_sets_no_cookie(self):
        request = _mock_request(query_params={"target": "desktop"})
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertEqual(result.headers.getlist("set-cookie"), [])

    def test_desktop_target_with_a_non_numeric_port_sets_no_cookie(self):
        '''The port is the only thing that ends up in the eventual 127.0.0.1 redirect URL - must
        be strictly validated, not passed through.'''
        request = _mock_request(query_params={"target": "desktop", "desktop_port": "not-a-port; evil"})
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertEqual(result.headers.getlist("set-cookie"), [])

    def test_desktop_target_with_out_of_range_port_sets_no_cookie(self):
        request = _mock_request(query_params={"target": "desktop", "desktop_port": "99999"})
        request.path_params = {"provider": "google"}
        result = _run(api_handlers.auth_provider_redirect(request))
        self.assertEqual(result.headers.getlist("set-cookie"), [])


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

    def _login_response(self, user_id: str = "u1") -> Response:
        body = json.dumps({
            "session_id": "s1",
            "user_info": {"id": user_id},
            "subscription_info": {},
            "current_subscription_plan": {},
        })
        login_response = Response(content=body, media_type="application/json", status_code=200)
        login_response.set_cookie(key="session", value="s1", httponly=True)
        login_response.set_cookie(key="csrf_token", value=CSRF_TOKEN, httponly=False)
        return login_response

    @patch("src.api_handlers.CloudClient")
    @patch("src.api_handlers.AuthenticationService")
    def test_desktop_login_port_cookie_redirects_to_loopback_with_bootstrap_code(
        self, mock_service_cls, mock_client_cls
    ):
        mock_service = MagicMock()
        mock_service.complete_login_from_handoff = AsyncMock(return_value=self._login_response(user_id="u1"))
        mock_service_cls.return_value = mock_service
        mock_client = MagicMock()
        mock_client.create_device_bootstrap.return_value = "bootstrap-code-1"
        mock_client_cls.return_value = mock_client

        request = _mock_request(
            query_params={"code": "good-code"}, cookies={"desktop_login_port": "54321"}
        )
        result = _run(api_handlers.auth_callback(request))
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.headers["location"], "http://127.0.0.1:54321/callback?code=bootstrap-code-1")
        mock_client.create_device_bootstrap.assert_called_once_with("u1")
        # one-shot: the cookie must be cleared regardless of outcome
        set_cookie_headers = " ".join(result.headers.getlist("set-cookie"))
        self.assertIn("desktop_login_port=", set_cookie_headers)
        self.assertIn('Max-Age=0', set_cookie_headers)
        # the real Local session cookies are still forwarded (browser stays logged in on Local too)
        self.assertIn("session=s1", set_cookie_headers)

    @patch("src.api_handlers.CloudClient")
    @patch("src.api_handlers.AuthenticationService")
    def test_desktop_bootstrap_failure_redirects_to_login_with_error(self, mock_service_cls, mock_client_cls):
        from src.cloud_client.client import CloudClientError

        mock_service = MagicMock()
        mock_service.complete_login_from_handoff = AsyncMock(return_value=self._login_response())
        mock_service_cls.return_value = mock_service
        mock_client = MagicMock()
        mock_client.create_device_bootstrap.side_effect = CloudClientError(502, "boom")
        mock_client_cls.return_value = mock_client

        request = _mock_request(
            query_params={"code": "good-code"}, cookies={"desktop_login_port": "54321"}
        )
        result = _run(api_handlers.auth_callback(request))
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login", result.headers["location"])
        self.assertIn("auth_result=error", result.headers["location"])

    @patch("src.api_handlers.CloudClient")
    @patch("src.api_handlers.AuthenticationService")
    def test_no_desktop_cookie_never_calls_cloud_client_for_bootstrap(self, mock_service_cls, mock_client_cls):
        '''A plain (non-desktop) login must never touch the device-bootstrap path at all.'''
        mock_service = MagicMock()
        mock_service.complete_login_from_handoff = AsyncMock(return_value=self._login_response())
        mock_service_cls.return_value = mock_service

        request = _mock_request(query_params={"code": "good-code"}, cookies={})
        result = _run(api_handlers.auth_callback(request))
        self.assertIn("/?auth_result=success", result.headers["location"])
        mock_client_cls.return_value.create_device_bootstrap.assert_not_called()


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
