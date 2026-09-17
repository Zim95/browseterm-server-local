# builtins
import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

# fastapi
from fastapi import Request

# browseterm_db enums (RESUMING/RUNNING/HIBERNATED must exist in the installed browseterm_db)
from browseterm_db.models.containers import ContainerStatus, SaveStatus

# module under test (imports cleanly off-cluster: CertificateUtils now inits its k8s clients lazily)
import src.api_handlers as api_handlers

# dto used to build a realistic ContainerService response
from src.containers.dto.container_response_dto import ContainerResponseModel
from src.cloud_client.client import CloudClient


def _mock_request(body: dict, user_id: str = 'user-42') -> MagicMock:
    '''
    A FastAPI Request stand-in whose .json() coroutine returns `body`, mirroring how
    api_handlers reads request data (await request.json()). request.state.user_info['id'] is
    set explicitly (resume_container subscripts it) rather than left as an unconfigured
    MagicMock, since P19's Cloud calls now assert the exact user_id they were invoked with.
    '''
    request: MagicMock = MagicMock(spec=Request)
    request.json = AsyncMock(return_value=body)
    request.state.user_info = {'id': user_id}
    return request


class TestResumeContainer(TestCase):
    '''
    Handler-level tests for api_handlers.resume_container.

    We call the UNDECORATED handler via resume_container.__wrapped__ (the
    @authenticate_session decorator uses functools.wraps) so no Cloud session validation is
    needed, and patch get_container_by_id/update_container_fields/ContainerService at their
    import site in src.api_handlers so no live Cloud API, gRPC, or k8s is touched (the repo's
    "mock the boundary" convention).
    '''

    def setUp(self) -> None:
        self.container_id: str = 'container-123'
        self.saved_image: str = 'registry/my-container:snap'

        # Stored container row for a HIBERNATED container that WAS saved.
        self.row: dict = {
            'id': self.container_id,
            'user_id': 'user-42',
            'image_id': 'image-1',
            'name': 'my-container',
            'status': ContainerStatus.HIBERNATED.value,
            'cpu_limit': '1',
            'memory_limit': '1Gi',
            'storage_limit': '2Gi',
            'ip_address': '10.0.0.5',              # OLD (stale) ClusterIP
            'port_mappings': [{'publish_port': 2222, 'target_port': 22, 'protocol': 'TCP'}],
            'environment_vars': {'FOO': 'bar'},
            'associated_resources': [{'kind': 'Service', 'name': 'old-svc'}],
            'kubernetes_id': 'old-pod-uid',
            'saved_image': self.saved_image,
            'save_status': SaveStatus.SUCCEEDED.value,
        }

        # create_container_in_k8s response: NEW pod identity + NEW ip.
        self.response: ContainerResponseModel = ContainerResponseModel(
            container_name='my-container',
            container_id='new-pod-uid',
            container_ip='10.0.0.99',              # NEW ClusterIP
            container_network='user-42-namespace',
            container_ports=[],
            associated_resources=[{'kind': 'Service', 'name': 'new-svc'}],
        )

    def _run_resume(self, body: dict, mock_cloud_client: MagicMock = None, mock_service: MagicMock = None):
        if mock_service is None:
            mock_service = MagicMock()
            mock_service.create_container_in_k8s = AsyncMock(return_value=self.response)
        if mock_cloud_client is None:
            mock_cloud_client = MagicMock(spec=CloudClient)
            mock_cloud_client.resume_container.return_value = {**self.row, 'status': 'Resuming'}

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value=self.row)), \
             patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update, \
             patch('src.api_handlers.ContainerService', return_value=mock_service), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(
                api_handlers.resume_container.__wrapped__(request=_mock_request(body))
            )
        return result, mock_update, mock_service, mock_cloud_client

    def _final_update_data(self, mock_update: MagicMock) -> dict:
        '''The fields dict of the update_container_fields call that set status=RUNNING (the
        final sync).'''
        for call in mock_update.call_args_list:
            fields = call.args[2]
            if fields.get('status') == ContainerStatus.RUNNING:
                return fields
        raise AssertionError('resume_container never issued the final RUNNING update')

    def test_resume_recreates_pod_from_saved_image(self) -> None:
        '''A HIBERNATED container with a saved_image is recreated FROM that snapshot.'''
        result, _update, mock_service, _cloud = self._run_resume({'container_id': self.container_id})
        self.assertEqual(result.status_code, 200)
        mock_service.create_container_in_k8s.assert_called_once()
        self.assertEqual(
            mock_service.create_container_in_k8s.call_args.kwargs['image_name_override'],
            self.saved_image,
        )

    def test_resume_updates_ip_address_and_kubernetes_id(self) -> None:
        '''
        Regression: resume creates a brand-new Service (new ClusterIP), so the row MUST get
        the NEW ip_address + kubernetes_id and status RUNNING. The bug was ip_address not
        being updated, leaving the terminal dialing the deleted pod's IP (SSH handshake timeout).
        '''
        _result, mock_update, _service, _cloud = self._run_resume({'container_id': self.container_id})
        data = self._final_update_data(mock_update)
        self.assertEqual(data['ip_address'], self.response.container_ip)   # 10.0.0.99, not 10.0.0.5
        self.assertNotEqual(data['ip_address'], self.row['ip_address'])
        self.assertEqual(data['kubernetes_id'], self.response.container_id)
        self.assertEqual(data['associated_resources'], self.response.associated_resources)
        self.assertEqual(data['status'], ContainerStatus.RUNNING)

    def test_resume_calls_cloud_before_recreate(self) -> None:
        '''P19: the row flips to RESUMING (via Cloud's own resume transition) before the (slow)
        pod recreate, so the UI can show progress and a failed pod-start has something to roll
        back. Cloud's resume_container is called with (container_id, user_id) before
        ContainerService.create_container_in_k8s.'''
        manager = MagicMock()
        mock_cloud_client = MagicMock(spec=CloudClient)
        mock_cloud_client.resume_container.return_value = {**self.row, 'status': 'Resuming'}
        manager.attach_mock(mock_cloud_client.resume_container, 'resume_container')

        mock_service = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(return_value=self.response)
        manager.attach_mock(mock_service.create_container_in_k8s, 'create_container_in_k8s')

        result, _update, _service, cloud = self._run_resume(
            {'container_id': self.container_id}, mock_cloud_client=mock_cloud_client, mock_service=mock_service
        )
        self.assertEqual(result.status_code, 200)
        cloud.resume_container.assert_called_once_with(self.container_id, 'user-42')
        call_names = [c[0] for c in manager.mock_calls]
        self.assertLess(
            call_names.index('resume_container'), call_names.index('create_container_in_k8s'),
            'Cloud resume_container must be called before the pod-start step',
        )

    def test_resume_missing_container_returns_404(self) -> None:
        '''No row -> 404, and k8s is never touched.'''
        mock_service: MagicMock = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock()
        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value=None)), \
             patch('src.api_handlers.ContainerService', return_value=mock_service):
            result = asyncio.run(
                api_handlers.resume_container.__wrapped__(request=_mock_request({'container_id': 'nope'}))
            )
        self.assertEqual(result.status_code, 404)
        mock_service.create_container_in_k8s.assert_not_called()

    def test_resume_after_crash_still_resumes_from_saved_image(self) -> None:
        '''
        save -> crash -> resume (server-testable slice): even if the pod died unexpectedly
        (row left non-RUNNING, e.g. UNKNOWN), resume takes the SAME path as hibernate-resume:
        recreate from saved_image, write the new identity + ip, RUNNING. Crash detection itself
        is kubelet-driven and lives in container-maker, not here.
        '''
        self.row['status'] = ContainerStatus.UNKNOWN.value
        result, mock_update, mock_service, _cloud = self._run_resume({'container_id': self.container_id})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            mock_service.create_container_in_k8s.call_args.kwargs['image_name_override'],
            self.saved_image,
        )
        self.assertEqual(self._final_update_data(mock_update)['status'], ContainerStatus.RUNNING)

    def test_resume_rejected_by_cloud_surfaces_error_before_k8s(self) -> None:
        '''P19: if Cloud rejects the resume transition (e.g. 409 - lost a concurrent resume
        race, or 400 - resolved device lacks capacity), nothing was reserved on Cloud's side, so
        Local must surface that status/error verbatim and never touch k8s.'''
        from src.cloud_client.client import CloudClientError
        mock_cloud_client = MagicMock(spec=CloudClient)
        mock_cloud_client.resume_container.side_effect = CloudClientError(409, 'Container is not hibernated')
        result, _update, mock_service, _cloud = self._run_resume(
            {'container_id': self.container_id}, mock_cloud_client=mock_cloud_client
        )
        self.assertEqual(result.status_code, 409)
        self.assertIn('Container is not hibernated', result.body.decode())
        mock_service.create_container_in_k8s.assert_not_called()

    def test_resume_pod_start_failure_rolls_back_via_hibernate(self) -> None:
        '''P19: once Cloud's resume transition succeeded (reserved capacity, set
        device_id/RESUMING), a subsequent pod-start failure must roll that back via the existing
        hibernate endpoint (resumed=True path) rather than just marking FAILED, which would leave
        a dangling device reservation forever.'''
        mock_service: MagicMock = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(side_effect=RuntimeError('pod start boom'))
        mock_cloud_client = MagicMock(spec=CloudClient)
        mock_cloud_client.resume_container.return_value = {**self.row, 'status': 'Resuming'}

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value=self.row)), \
             patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update, \
             patch('src.api_handlers.ContainerService', return_value=mock_service), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client):
            result = asyncio.run(
                api_handlers.resume_container.__wrapped__(
                    request=_mock_request({'container_id': self.container_id})
                )
            )
        self.assertEqual(result.status_code, 500)
        mock_cloud_client.hibernate_container.assert_called_once_with(self.container_id)
        # never falls back to the old FAILED-marking behavior once Cloud's transition succeeded
        statuses = [c.args[2].get('status') for c in mock_update.call_args_list]
        self.assertNotIn(ContainerStatus.FAILED, statuses)


class TestCountActiveContainers(TestCase):
    '''
    Unit tests for api_handlers._count_active_containers -- what actually counts against a
    plan's max_containers limit.
    '''

    def _rows(self, statuses_and_ids, deleted_ids=frozenset()):
        return [
            {'id': cid, 'status': status, 'deleted_at': ('x' if cid in deleted_ids else None)}
            for cid, status in statuses_and_ids
        ]

    def test_counts_running_pending_resuming_only(self) -> None:
        rows = self._rows([
            ('a', ContainerStatus.RUNNING.value),
            ('b', ContainerStatus.PENDING.value),
            ('c', ContainerStatus.RESUMING.value),
            ('d', ContainerStatus.HIBERNATED.value),
            ('e', ContainerStatus.FAILED.value),
        ])
        with patch('src.api_handlers.list_user_containers_db', AsyncMock(return_value=rows)):
            count = asyncio.run(api_handlers._count_active_containers('user-42', exclude_container_id='none'))
        self.assertEqual(count, 3)

    def test_excludes_the_container_being_resumed(self) -> None:
        rows = self._rows([
            ('target', ContainerStatus.RUNNING.value),
            ('other', ContainerStatus.RUNNING.value),
        ])
        with patch('src.api_handlers.list_user_containers_db', AsyncMock(return_value=rows)):
            count = asyncio.run(api_handlers._count_active_containers('user-42', exclude_container_id='target'))
        self.assertEqual(count, 1)

    def test_excludes_soft_deleted_rows(self) -> None:
        rows = self._rows(
            [('a', ContainerStatus.RUNNING.value), ('b', ContainerStatus.RUNNING.value)],
            deleted_ids={'b'},
        )
        with patch('src.api_handlers.list_user_containers_db', AsyncMock(return_value=rows)):
            count = asyncio.run(api_handlers._count_active_containers('user-42', exclude_container_id='none'))
        self.assertEqual(count, 1)

    def test_raises_on_db_error(self) -> None:
        with patch('src.api_handlers.list_user_containers_db', AsyncMock(side_effect=Exception('db down'))):
            with self.assertRaises(Exception):
                asyncio.run(api_handlers._count_active_containers('user-42', exclude_container_id='none'))


class TestExceedsTierSpec(TestCase):
    '''Unit tests for api_handlers._exceeds_tier_spec -- pure comparison, no mocking needed.'''

    def _tier(self, cpu='1', memory='1Gi', storage='2Gi'):
        return {
            'cpu_limit_per_container': cpu,
            'memory_limit_per_container': memory,
            'storage_limit_per_container': storage,
        }

    def _container(self, cpu='1', memory='1Gi', storage='2Gi'):
        return {'cpu_limit': cpu, 'memory_limit': memory, 'storage_limit': storage}

    def test_within_limits_is_allowed(self) -> None:
        self.assertFalse(api_handlers._exceeds_tier_spec(self._container(), self._tier()))

    def test_cpu_over_limit_is_blocked(self) -> None:
        self.assertTrue(api_handlers._exceeds_tier_spec(self._container(cpu='2'), self._tier(cpu='1')))

    def test_memory_over_limit_is_blocked(self) -> None:
        self.assertTrue(api_handlers._exceeds_tier_spec(self._container(memory='4Gi'), self._tier(memory='1Gi')))

    def test_storage_over_limit_is_blocked(self) -> None:
        self.assertTrue(api_handlers._exceeds_tier_spec(self._container(storage='10Gi'), self._tier(storage='2Gi')))

    def test_exactly_at_limit_is_allowed(self) -> None:
        self.assertFalse(api_handlers._exceeds_tier_spec(self._container(cpu='1'), self._tier(cpu='1')))

    def test_unparseable_tier_value_fails_open(self) -> None:
        '''The Pro tier's seed data uses the literal string "Configurable" -- must not crash
        or incorrectly block on it, just skip that comparison.'''
        self.assertFalse(api_handlers._exceeds_tier_spec(self._container(cpu='1'), self._tier(cpu='Configurable')))


class TestResumeEntitlementChecks(TestCase):
    '''
    Integration-level tests for the two entitlement gates wired into resume_container itself:
    the plan's concurrent-container limit, and whether the container's own recorded spec still
    fits the current plan. Both must block with 409 (not touch k8s) when violated, and must not
    interfere with a normal resume when the plan permits it.
    '''

    def setUp(self) -> None:
        self.container_id: str = 'container-123'
        self.row: dict = {
            'id': self.container_id,
            'user_id': 'user-42',
            'image_id': 'image-1',
            'name': 'my-container',
            'status': ContainerStatus.HIBERNATED.value,
            'cpu_limit': '1',
            'memory_limit': '1Gi',
            'storage_limit': '2Gi',
            'ip_address': '10.0.0.5',
            'port_mappings': [],
            'environment_vars': {},
            'associated_resources': [],
            'kubernetes_id': 'old-pod-uid',
            'saved_image': 'registry/my-container:snap',
            'save_status': SaveStatus.SUCCEEDED.value,
        }
        self.free_plan: dict = {
            'name': 'Free Plan', 'type': 'free', 'max_containers': 1,
            'cpu_limit_per_container': '1', 'memory_limit_per_container': '1Gi',
            'storage_limit_per_container': '2Gi',
        }

    def _run(self, plan: dict, active_rows: list):
        mock_service = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(return_value=ContainerResponseModel(
            container_name='my-container', container_id='new-pod-uid', container_ip='10.0.0.99',
            container_network='user-42-namespace', container_ports=[], associated_resources=[],
        ))
        mock_cloud_client = MagicMock(spec=CloudClient)
        mock_cloud_client.resume_container.return_value = {**self.row, 'status': 'Resuming'}

        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value=self.row)), \
             patch('src.api_handlers.update_container_fields', AsyncMock()), \
             patch('src.api_handlers.list_user_containers_db', AsyncMock(return_value=active_rows)), \
             patch('src.api_handlers.ContainerService', return_value=mock_service), \
             patch('src.api_handlers.CloudClient', return_value=mock_cloud_client), \
             patch('src.api_handlers.get_user_current_subscription_plan', AsyncMock(return_value=plan)):
            result = asyncio.run(
                api_handlers.resume_container.__wrapped__(request=_mock_request({'container_id': self.container_id}))
            )
        return result, mock_service

    def test_blocks_when_over_container_limit(self) -> None:
        '''Free plan, max_containers=1, user already has one Running -> 409, k8s never touched.'''
        active_rows = [{'id': 'other-container', 'status': ContainerStatus.RUNNING.value, 'deleted_at': None}]
        result, mock_service = self._run(self.free_plan, active_rows)
        self.assertEqual(result.status_code, 409)
        self.assertIn('Free Plan', result.body.decode())
        mock_service.create_container_in_k8s.assert_not_called()

    def test_blocks_when_spec_exceeds_current_plan(self) -> None:
        '''Container recorded 2 CPU (e.g. saved under a higher tier), current plan only allows 1.'''
        self.row['cpu_limit'] = '2'
        result, mock_service = self._run(self.free_plan, active_rows=[])
        self.assertEqual(result.status_code, 409)
        self.assertIn('ineligible', result.body.decode())
        mock_service.create_container_in_k8s.assert_not_called()

    def test_allows_resume_within_plan_limits(self) -> None:
        '''No other active containers, spec fits -- resume proceeds exactly as before.'''
        result, mock_service = self._run(self.free_plan, active_rows=[])
        self.assertEqual(result.status_code, 200)
        mock_service.create_container_in_k8s.assert_called_once()

    def test_resumed_container_itself_not_counted_against_its_own_limit(self) -> None:
        '''The row being resumed is HIBERNATED, but even if list_user_containers_db somehow
        still returned it, it must be excluded from its own limit check by id.'''
        active_rows = [{'id': self.container_id, 'status': ContainerStatus.HIBERNATED.value, 'deleted_at': None}]
        result, mock_service = self._run(self.free_plan, active_rows)
        self.assertEqual(result.status_code, 200)
        mock_service.create_container_in_k8s.assert_called_once()


class TestSaveContainerHandler(TestCase):
    '''
    Handler-level tests for api_handlers.save_container (the server-testable half of
    save -> crash -> resume): the handler marks save_status=PENDING immediately and fires the
    blocking gRPC save in the background; a gRPC failure records save_status=FAILED.
    '''

    def setUp(self) -> None:
        self.container_id: str = 'container-123'
        self.user_id: str = 'user-42'

    def test_save_marks_pending_and_returns_202(self) -> None:
        mock_service: MagicMock = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(return_value=MagicMock())
        with patch('src.api_handlers.get_container_by_id',
                   AsyncMock(return_value={'id': self.container_id, 'user_id': self.user_id})), \
             patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update, \
             patch('src.api_handlers.ContainerService', return_value=mock_service):
            result = asyncio.run(
                api_handlers.save_container.__wrapped__(
                    request=_mock_request(
                        {'container_id': self.container_id, 'network_name': 'user-42-namespace'}
                    )
                )
            )
        self.assertEqual(result.status_code, 202)
        pending = [c.args[2] for c in mock_update.call_args_list
                   if c.args[2].get('save_status') == SaveStatus.PENDING.value]
        self.assertTrue(pending, 'save_container did not mark save_status=PENDING')
        self.assertIn(
            'last_save_attempted_at', pending[0],
            'save_container must stamp last_save_attempted_at -- this is the one moment a save is initiated',
        )

    def test_run_save_records_failed_on_grpc_error(self) -> None:
        mock_service: MagicMock = MagicMock()
        mock_service.save_container_in_k8s = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update:
            asyncio.run(api_handlers._run_save(mock_service, MagicMock(), self.container_id, self.user_id))
        failed = [c.args[2] for c in mock_update.call_args_list
                  if c.args[2].get('save_status') == SaveStatus.FAILED.value]
        self.assertTrue(failed, '_run_save did not record save_status=FAILED')
        self.assertIn('boom', failed[0]['save_error'])
        self.assertNotIn(
            'last_save_attempted_at', failed[0],
            'only the PENDING write (save_container) should stamp last_save_attempted_at, not a later failure',
        )


class TestContainerActivity(TestCase):
    '''
    Handler-level tests for api_handlers.container_activity: it stamps last_active_at scoped to the
    caller's own container (id + user_id), and — via @authenticate_session — refreshes the login
    session (tested there). Uses .__wrapped__ to skip auth and injects request.state.user_info.
    '''

    def _run(self, body: dict, user_id: str = "user-42"):
        req: MagicMock = MagicMock(spec=Request)
        req.json = AsyncMock(return_value=body)
        req.state.user_info = SimpleNamespace(id=user_id)
        with patch('src.api_handlers.update_container_fields', AsyncMock()) as mock_update:
            result = asyncio.run(api_handlers.container_activity.__wrapped__(request=req))
        return result, mock_update

    def test_stamps_last_active_at_scoped_to_user(self) -> None:
        result, mock_update = self._run({'container_id': 'c-1'})
        self.assertEqual(result.status_code, 200)
        mock_update.assert_called_once()
        container_id, user_id, fields = mock_update.call_args.args
        self.assertEqual((container_id, user_id), ("c-1", "user-42"))
        self.assertIn('last_active_at', fields)

    def test_missing_container_id_is_400(self) -> None:
        result, mock_update = self._run({})
        self.assertEqual(result.status_code, 400)
        mock_update.assert_not_called()
