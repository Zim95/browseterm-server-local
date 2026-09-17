# builtins
import asyncio
from unittest import TestCase
from src.cloud_client.client import CloudClient
from unittest.mock import AsyncMock, MagicMock, patch

# fastapi
from fastapi import Request

# browseterm_db enums
from browseterm_db.models.containers import ContainerStatus, SaveStatus

# module under test
import src.api_handlers as api_handlers


def _mock_request(body: dict, user_id: str = 'user-42') -> MagicMock:
    '''Mirrors test_resume_container.py's own request stand-in.'''
    request: MagicMock = MagicMock(spec=Request)
    request.json = AsyncMock(return_value=body)
    request.state.user_info = {'id': user_id}
    return request


class TestHibernateContainer(TestCase):
    '''
    Handler-level tests for api_handlers.hibernate_container: the manual counterpart to
    reaper.py's automatic idle-hibernation, exercising the same save -> confirm Succeeded ->
    delete -> Cloud hibernate ordering.

    get_container_by_id is called twice on the happy path - once for the initial ownership/status
    check, once inside the save-status poll loop - so `self.row` already carries
    save_status=SUCCEEDED from the start: the poll loop's first read already sees a terminal
    status and returns immediately, with no real asyncio.sleep ever awaited. Tests that need a
    non-immediate outcome (failed save, timeout) override save_status or patch the poll/timeout
    constants explicitly instead of relying on real wall-clock delay.
    '''

    def setUp(self) -> None:
        self.container_id: str = 'container-123'
        self.row: dict = {
            'id': self.container_id,
            'user_id': 'user-42',
            'name': 'my-container',
            'status': ContainerStatus.RUNNING.value,
            'kubernetes_id': 'pod-uid-1',
            'save_status': SaveStatus.SUCCEEDED.value,
        }

    def _run(self, body: dict = None, mock_service: MagicMock = None, mock_cloud_client: MagicMock = None,
              get_container_side_effect=None):
        if body is None:
            body = {'container_id': self.container_id}
        if mock_service is None:
            mock_service = MagicMock()
            mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
            mock_service.delete_container_in_k8s = AsyncMock(return_value=MagicMock())
        if mock_cloud_client is None:
            mock_cloud_client = MagicMock(spec=CloudClient)

        get_container_mock = AsyncMock()
        if get_container_side_effect is not None:
            get_container_mock.side_effect = get_container_side_effect
        else:
            get_container_mock.return_value = self.row

        with patch('src.api_handlers.get_container_by_id', get_container_mock), \
             patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update, \
             patch('src.api_handlers.ContainerService', return_value=mock_service), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(api_handlers.hibernate_container.__wrapped__(request=_mock_request(body)))
        return result, mock_update, mock_service, mock_cloud_client

    def test_hibernate_happy_path_returns_200(self) -> None:
        result, _update, mock_service, mock_cloud = self._run()
        self.assertEqual(result.status_code, 200)
        mock_service.delete_container_in_k8s.assert_called_once()
        mock_cloud.hibernate_container.assert_called_once_with(self.container_id)

    def test_hibernate_deletes_by_db_id_not_stale_kubernetes_id(self) -> None:
        '''
        Regression guard: container-maker now resolves the live pod via the stable
        browseterm/container-id label (the container's DB id), not the DB's own cached
        kubernetes_id - which can go stale after a resume recreates the pod under a new UID (see
        container-maker's delete() docstring). The delete call must carry the DB id, never
        whatever kubernetes_id happens to be cached on the row.
        '''
        self.row['kubernetes_id'] = 'some-other-pod-uid-entirely'
        _result, _update, mock_service, _cloud = self._run()
        delete_request = mock_service.delete_container_in_k8s.call_args.args[0]
        self.assertEqual(delete_request.container_id, self.container_id)
        self.assertNotEqual(delete_request.container_id, self.row['kubernetes_id'])

    def test_hibernate_marks_save_pending_before_saving(self) -> None:
        '''Same PENDING/last_save_attempted_at contract save_container's own handler uses - this
        IS a real save, not a lighter-weight variant.'''
        _result, mock_update, _service, _cloud = self._run()
        pending = [c.args[2] for c in mock_update.call_args_list
                   if c.args[2].get('save_status') == SaveStatus.PENDING.value]
        self.assertTrue(pending, 'hibernate_container did not mark save_status=PENDING before saving')
        self.assertIn('last_save_attempted_at', pending[0])

    def test_hibernate_ordering_save_then_delete_then_cloud_hibernate(self) -> None:
        '''The one safety-critical invariant: delete only ever happens after save_container_in_k8s,
        and Cloud's hibernate transition only after delete - mirrors reaper.py's own ordering.'''
        manager = MagicMock()
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        mock_service.delete_container_in_k8s = AsyncMock(return_value=MagicMock())
        manager.attach_mock(mock_service.save_container_in_k8s, 'save_container_in_k8s')
        manager.attach_mock(mock_service.delete_container_in_k8s, 'delete_container_in_k8s')
        mock_cloud_client = MagicMock(spec=CloudClient)
        manager.attach_mock(mock_cloud_client.hibernate_container, 'hibernate_container')

        result, _update, _service, _cloud = self._run(mock_service=mock_service, mock_cloud_client=mock_cloud_client)
        self.assertEqual(result.status_code, 200)
        call_names = [c[0] for c in manager.mock_calls]
        self.assertLess(call_names.index('save_container_in_k8s'), call_names.index('delete_container_in_k8s'))
        self.assertLess(call_names.index('delete_container_in_k8s'), call_names.index('hibernate_container'))

    def test_hibernate_missing_container_returns_404(self) -> None:
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock()
        result, _update, _service, mock_cloud = self._run(
            get_container_side_effect=[None], mock_service=mock_service
        )
        self.assertEqual(result.status_code, 404)
        mock_service.save_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_non_running_container_returns_409(self) -> None:
        self.row['status'] = ContainerStatus.HIBERNATED.value
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock()
        result, _update, _service, mock_cloud = self._run(mock_service=mock_service)
        self.assertEqual(result.status_code, 409)
        self.assertIn('running', result.body.decode().lower())
        mock_service.save_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_no_kubernetes_id_returns_409(self) -> None:
        self.row['kubernetes_id'] = None
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock()
        result, _update, _service, mock_cloud = self._run(mock_service=mock_service)
        self.assertEqual(result.status_code, 409)
        mock_service.save_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_failed_save_leaves_pod_running(self) -> None:
        '''A save that reaches a confirmed FAILED (not just "not yet succeeded") must never lead
        to deleting the pod - same rule reaper.py's own _hibernate_one enforces.'''
        row_ownership_check = {**self.row}
        row_poll = {**self.row, 'save_status': SaveStatus.FAILED.value}
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        mock_service.delete_container_in_k8s = AsyncMock()
        result, _update, _service, mock_cloud = self._run(
            mock_service=mock_service,
            get_container_side_effect=[row_ownership_check, row_poll],
        )
        self.assertEqual(result.status_code, 502)
        self.assertIn('did not complete successfully', result.body.decode())
        mock_service.delete_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_save_wait_timeout_leaves_pod_running(self) -> None:
        '''If save_status never reaches a terminal state within the wait window, the handler must
        give up (not hang forever, not delete the pod) and say so.'''
        row_ownership_check = {**self.row}
        row_poll_pending = {**self.row, 'save_status': SaveStatus.PENDING.value}
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        mock_service.delete_container_in_k8s = AsyncMock()

        with patch('src.api_handlers._HIBERNATE_SAVE_WAIT_TIMEOUT_SECONDS', 0.05), \
             patch('src.api_handlers._HIBERNATE_SAVE_POLL_INTERVAL_SECONDS', 0.01):
            result, _update, _service, mock_cloud = self._run(
                mock_service=mock_service,
                # First call: ownership check. Every call after: still Pending, forever - the real
                # exit condition here is the (patched, tiny) real wall-clock deadline elapsing.
                get_container_side_effect=[row_ownership_check] + [row_poll_pending] * 50,
            )
        self.assertEqual(result.status_code, 502)
        # Reports the last-known (still non-terminal) save_status rather than a bare "timed out" -
        # more informative for whoever reads the error, and still correctly never a green light
        # to delete the pod.
        self.assertIn('did not complete successfully', result.body.decode())
        self.assertIn('Pending', result.body.decode())
        mock_service.delete_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_save_wait_timeout_with_no_status_reports_timed_out(self) -> None:
        '''If the poll loop never even sees a save_status value at all (e.g. get_container_by_id
        returns None on every poll after the initial check), the fallback wording is "timed out",
        not a blank/confusing status.'''
        row_ownership_check = {**self.row}
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        mock_service.delete_container_in_k8s = AsyncMock()

        with patch('src.api_handlers._HIBERNATE_SAVE_WAIT_TIMEOUT_SECONDS', 0.05), \
             patch('src.api_handlers._HIBERNATE_SAVE_POLL_INTERVAL_SECONDS', 0.01):
            result, _update, _service, mock_cloud = self._run(
                mock_service=mock_service,
                get_container_side_effect=[row_ownership_check] + [None] * 50,
            )
        self.assertEqual(result.status_code, 502)
        self.assertIn('timed out', result.body.decode())
        mock_service.delete_container_in_k8s.assert_not_called()
        mock_cloud.hibernate_container.assert_not_called()

    def test_hibernate_ownership_scoped_lookup(self) -> None:
        '''user_id always comes from the authenticated session, never the request body - passed
        straight through to get_container_by_id, same convention as every other handler here.'''
        get_container_mock = AsyncMock(return_value=self.row)
        mock_service = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        mock_service.delete_container_in_k8s = AsyncMock(return_value=MagicMock())
        with patch('src.api_handlers.get_container_by_id', get_container_mock), \
             patch('src.api_handlers.update_container_fields', AsyncMock()), \
             patch('src.api_handlers.ContainerService', return_value=mock_service), \
             patch('src.api_handlers.CloudClient', return_value=MagicMock()):
            asyncio.run(api_handlers.hibernate_container.__wrapped__(
                request=_mock_request({'container_id': self.container_id}, user_id='user-42')
            ))
        get_container_mock.assert_any_call(self.container_id, 'user-42')
