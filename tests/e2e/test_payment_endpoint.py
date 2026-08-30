'''
End to end tests send actual api input and compare the response with the expected response.
'''

from unittest import TestCase
from unittest.mock import patch
from fastapi import Response
from fastapi.testclient import TestClient
from app import app


class TestPaymentEndpoint(TestCase):
    '''
    End to end test for the /create-payment endpoint.

    The route registration is currently commented out in app.py (payments disabled per explicit
    direction; the handler code itself is kept, not deleted, so re-enabling is a one-line
    uncomment) - so the endpoint 404s regardless of authentication. This test now guards that
    the route is genuinely absent and payment-gateway is never contacted, rather than testing
    the (currently unreachable) auth-redirect behavior.
    '''
    def setUp(self) -> None:
        '''
        Setup the API test client.
        '''
        self.client: TestClient = TestClient(app, follow_redirects=False)

    def test_create_payment_route_disabled(self) -> None:
        '''
        /create-payment is not registered while payments are disabled, and payment-gateway must
        never be contacted.
        '''
        with patch("src.payments.payments_service.PaymentService") as mock_payment_service_cls:
            response: Response = self.client.post(
                "/create-payment", json={"plan_id": "developer", "idempotency_key": "idem-1"}
            )

            self.assertEqual(response.status_code, 404)
            mock_payment_service_cls.assert_not_called()
