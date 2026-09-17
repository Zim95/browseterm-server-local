import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.cloud_client.client import CloudClient, CloudClientError


def _mock_response(status_code: int, json_body: dict):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def _mock_async_client(response=None, side_effect=None):
    '''httpx.AsyncClient(...) is instantiated fresh per call (see client.py's own comment on
    why), so the mock target is the class itself - .return_value is the instance _request()
    goes on to call .request()/.aclose() on.'''
    instance = MagicMock()
    if side_effect is not None:
        instance.request = AsyncMock(side_effect=side_effect)
    else:
        instance.request = AsyncMock(return_value=response)
    instance.aclose = AsyncMock()
    mock_async_client_cls = MagicMock(return_value=instance)
    return mock_async_client_cls, instance


class TestCloudClientBoundary(unittest.IsolatedAsyncioTestCase):
    """The CloudClient must be the only place cookie/HTTP wiring happens, and must never touch
    browseterm_db/DB_CONFIG/POSTGRES_*/REDIS_* - verified statically (no such import exists in
    src/cloud_client/*) and behaviorally here (every request goes through a real
    httpx.AsyncClient - async so a slow/remote Cloud never blocks FastAPI's event loop, see
    _request's own comment for why this matters).

    P07: the Device Cloud API is no longer called from here at all (moved to Bearer-device-token
    auth, called by browseterm-desktop's own separate CloudClient) - these boundary properties are
    now exercised via handoff_redeem/validate_session instead of the removed register_device/
    list_devices/etc."""

    async def test_redeem_handoff_sends_code_no_cookie_needed(self):
        mock_cls, instance = _mock_async_client(_mock_response(200, {"session_id": "s1", "user_info": {"id": "u1"}}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test")
            result = await client.redeem_handoff("handoff-code-1")

        self.assertEqual(result["session_id"], "s1")
        args, kwargs = instance.request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "http://cloud.test/auth/handoff/redeem")
        self.assertEqual(kwargs["json"], {"code": "handoff-code-1"})

    async def test_invalid_handoff_raises_401_not_swallowed(self):
        mock_cls, _ = _mock_async_client(_mock_response(401, {"error": "Invalid or expired handoff code"}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test")
            with self.assertRaises(CloudClientError) as ctx:
                await client.redeem_handoff("bogus")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_create_device_bootstrap_sends_internal_token_and_user_id(self):
        mock_cls, instance = _mock_async_client(_mock_response(200, {"code": "bootstrap-code-1"}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="tok")
            result = await client.create_device_bootstrap("u1")

        self.assertEqual(result, "bootstrap-code-1")
        args, kwargs = instance.request.call_args
        self.assertEqual(args[1], "http://cloud.test/auth/device-bootstrap")
        self.assertEqual(kwargs["json"], {"user_id": "u1"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Service-Token": "tok"})

    async def test_transport_failure_raises_cloud_client_error_status_zero(self):
        import httpx

        mock_cls, _ = _mock_async_client(side_effect=httpx.ConnectError("connection refused"))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test")
            with self.assertRaises(CloudClientError) as ctx:
                await client.redeem_handoff("x")
        self.assertEqual(ctx.exception.status_code, 0)


class TestCloudClientSessionContainerCatalog(unittest.IsolatedAsyncioTestCase):
    """New session/container/catalog/subscription methods - internal-service auth."""

    async def test_create_session_sends_internal_token(self):
        mock_cls, instance = _mock_async_client(_mock_response(201, {"session_id": "s1", "user_info": {}}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            await client.create_session({"provider_id": "p1", "provider": "google"})
        self.assertEqual(instance.request.call_args.kwargs["headers"], {"X-Internal-Service-Token": "secret"})

    async def test_no_internal_token_sends_no_header(self):
        mock_cls, instance = _mock_async_client(_mock_response(200, {"is_valid": False}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test")
            await client.validate_session("s1")
        self.assertEqual(instance.request.call_args.kwargs["headers"], {})

    async def test_get_container_404_returns_none_not_raise(self):
        mock_cls, _ = _mock_async_client(_mock_response(404, {"error": "Container not found"}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            self.assertIsNone(await client.get_container("c1", "u1"))

    async def test_get_container_other_error_raises(self):
        mock_cls, _ = _mock_async_client(_mock_response(500, {"error": "boom"}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            with self.assertRaises(CloudClientError):
                await client.get_container("c1", "u1")

    async def test_update_container_sends_user_id_alongside_fields(self):
        mock_cls, instance = _mock_async_client(_mock_response(200, {"container": {"id": "c1", "status": "Running"}}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            await client.update_container("c1", "u1", {"status": "Running"})
        self.assertEqual(instance.request.call_args.kwargs["json"], {"status": "Running", "user_id": "u1"})

    async def test_delete_container_404_returns_false(self):
        mock_cls, _ = _mock_async_client(_mock_response(404, {"error": "Container not found"}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            self.assertFalse(await client.delete_container("c1", "u1"))

    async def test_list_containers_passes_pagination_params(self):
        mock_cls, instance = _mock_async_client(_mock_response(200, {"containers": []}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            await client.list_containers("u1", limit=5, offset=10)
        self.assertEqual(instance.request.call_args.kwargs["params"], {"user_id": "u1", "limit": 5, "offset": 10})

    async def test_get_current_subscription(self):
        mock_cls, _ = _mock_async_client(_mock_response(200, {"subscription_type": {"type": "free"}}))
        with patch("src.cloud_client.client.httpx.AsyncClient", mock_cls):
            client = CloudClient(base_url="http://cloud.test", internal_token="secret")
            result = await client.get_current_subscription("u1")
        self.assertEqual(result, {"type": "free"})


if __name__ == "__main__":
    unittest.main()
