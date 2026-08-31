import unittest
from unittest.mock import patch, MagicMock

from src.cloud_client.client import CloudClient, CloudClientError


def _mock_response(status_code: int, json_body: dict):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestCloudClientBoundary(unittest.TestCase):
    """The CloudClient must be the only place cookie/HTTP wiring happens, and must never touch
    browseterm_db/DB_CONFIG/POSTGRES_*/REDIS_* - verified statically (no such import exists in
    src/cloud_client/*) and behaviorally here (every request goes through httpx.request).

    P07: the Device Cloud API is no longer called from here at all (moved to Bearer-device-token
    auth, called by browseterm-desktop's own separate CloudClient) - these boundary properties are
    now exercised via handoff_redeem/validate_session instead of the removed register_device/
    list_devices/etc."""

    @patch("src.cloud_client.client.httpx.request")
    def test_redeem_handoff_sends_code_no_cookie_needed(self, mock_request):
        mock_request.return_value = _mock_response(200, {"session_id": "s1", "user_info": {"id": "u1"}})
        client = CloudClient(base_url="http://cloud.test")

        result = client.redeem_handoff("handoff-code-1")

        self.assertEqual(result["session_id"], "s1")
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "http://cloud.test/auth/handoff/redeem")
        self.assertEqual(kwargs["json"], {"code": "handoff-code-1"})

    @patch("src.cloud_client.client.httpx.request")
    def test_invalid_handoff_raises_401_not_swallowed(self, mock_request):
        mock_request.return_value = _mock_response(401, {"error": "Invalid or expired handoff code"})
        client = CloudClient(base_url="http://cloud.test")

        with self.assertRaises(CloudClientError) as ctx:
            client.redeem_handoff("bogus")
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("src.cloud_client.client.httpx.request")
    def test_create_device_bootstrap_sends_internal_token_and_user_id(self, mock_request):
        mock_request.return_value = _mock_response(200, {"code": "bootstrap-code-1"})
        client = CloudClient(base_url="http://cloud.test", internal_token="tok")

        result = client.create_device_bootstrap("u1")

        self.assertEqual(result, "bootstrap-code-1")
        args, kwargs = mock_request.call_args
        self.assertEqual(args[1], "http://cloud.test/auth/device-bootstrap")
        self.assertEqual(kwargs["json"], {"user_id": "u1"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Service-Token": "tok"})

    @patch("src.cloud_client.client.httpx.request")
    def test_transport_failure_raises_cloud_client_error_status_zero(self, mock_request):
        import httpx

        mock_request.side_effect = httpx.ConnectError("connection refused")
        client = CloudClient(base_url="http://cloud.test")

        with self.assertRaises(CloudClientError) as ctx:
            client.redeem_handoff("x")
        self.assertEqual(ctx.exception.status_code, 0)


class TestCloudClientSessionContainerCatalog(unittest.TestCase):
    """New session/container/catalog/subscription methods - internal-service auth."""

    @patch("src.cloud_client.client.httpx.request")
    def test_create_session_sends_internal_token(self, mock_request):
        mock_request.return_value = _mock_response(201, {"session_id": "s1", "user_info": {}})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        client.create_session({"provider_id": "p1", "provider": "google"})
        self.assertEqual(mock_request.call_args.kwargs["headers"], {"X-Internal-Service-Token": "secret"})

    @patch("src.cloud_client.client.httpx.request")
    def test_no_internal_token_sends_no_header(self, mock_request):
        mock_request.return_value = _mock_response(200, {"is_valid": False})
        client = CloudClient(base_url="http://cloud.test")
        client.validate_session("s1")
        self.assertEqual(mock_request.call_args.kwargs["headers"], {})

    @patch("src.cloud_client.client.httpx.request")
    def test_get_container_404_returns_none_not_raise(self, mock_request):
        mock_request.return_value = _mock_response(404, {"error": "Container not found"})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        self.assertIsNone(client.get_container("c1", "u1"))

    @patch("src.cloud_client.client.httpx.request")
    def test_get_container_other_error_raises(self, mock_request):
        mock_request.return_value = _mock_response(500, {"error": "boom"})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        with self.assertRaises(CloudClientError):
            client.get_container("c1", "u1")

    @patch("src.cloud_client.client.httpx.request")
    def test_update_container_sends_user_id_alongside_fields(self, mock_request):
        mock_request.return_value = _mock_response(200, {"container": {"id": "c1", "status": "Running"}})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        client.update_container("c1", "u1", {"status": "Running"})
        self.assertEqual(mock_request.call_args.kwargs["json"], {"status": "Running", "user_id": "u1"})

    @patch("src.cloud_client.client.httpx.request")
    def test_delete_container_404_returns_false(self, mock_request):
        mock_request.return_value = _mock_response(404, {"error": "Container not found"})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        self.assertFalse(client.delete_container("c1", "u1"))

    @patch("src.cloud_client.client.httpx.request")
    def test_list_containers_passes_pagination_params(self, mock_request):
        mock_request.return_value = _mock_response(200, {"containers": []})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        client.list_containers("u1", limit=5, offset=10)
        self.assertEqual(mock_request.call_args.kwargs["params"], {"user_id": "u1", "limit": 5, "offset": 10})

    @patch("src.cloud_client.client.httpx.request")
    def test_get_current_subscription(self, mock_request):
        mock_request.return_value = _mock_response(200, {"subscription_type": {"type": "free"}})
        client = CloudClient(base_url="http://cloud.test", internal_token="secret")
        result = client.get_current_subscription("u1")
        self.assertEqual(result, {"type": "free"})


if __name__ == "__main__":
    unittest.main()
