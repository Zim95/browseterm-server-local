'''
P13 (see ~/browseterm/p.md's "P13" section): create-container is a two-step process across two
separate authenticated requests (POST /create-container-in-db, then POST /create-container-in-k8s
- see api_handlers.py's docstrings on each). Before this task, a failure in the second step (the
real ContainerMaker/K8s call) left the first step's DB row - and the Cloud-side device resource
reservation P12 added - permanently leaked: nothing ever released them, and the row's name would
block any retry under the same name.

Same "mock the boundary, call the undecorated handler" convention as test_ownership_idor.py.
'''
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

import src.api_handlers as api_handlers

USER_A = 'user-a'


def _mock_request(body: dict) -> MagicMock:
    request = MagicMock(spec=Request)
    request.json = AsyncMock(return_value=body)
    request.state.user_info = {'id': USER_A}
    return request


def _body(container_id: str = 'container-1') -> dict:
    return {
        'container_id': container_id, 'image_id': 'image-1', 'container_name': 'my-container',
        'resource_requirements': {},
    }


class TestCreateContainerK8sFailureReleasesDbRow(TestCase):
    def test_k8s_failure_deletes_the_pending_db_row(self) -> None:
        mock_service = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(side_effect=RuntimeError("ContainerMaker unreachable"))
        mock_service.delete_container_in_db = AsyncMock(return_value={'success': True, 'container_id': 'container-1'})

        request = _mock_request(_body())
        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value={'id': 'container-1', 'user_id': USER_A})), \
             patch('src.api_handlers.ContainerService', return_value=mock_service):
            result = asyncio.run(api_handlers.create_container_in_k8s.__wrapped__(request=request))

        self.assertEqual(result.status_code, 500)
        mock_service.delete_container_in_db.assert_called_once()
        called_request = mock_service.delete_container_in_db.call_args.args[0]
        self.assertEqual(called_request.container_id, 'container-1')
        self.assertEqual(called_request.user_id, USER_A)

    def test_release_failure_does_not_hide_the_original_k8s_error(self) -> None:
        '''Best-effort: if the release itself also fails, the caller still sees the real k8s
        error, not a release-failure error.'''
        mock_service = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(side_effect=RuntimeError("ContainerMaker unreachable"))
        mock_service.delete_container_in_db = AsyncMock(side_effect=RuntimeError("Cloud unreachable too"))

        request = _mock_request(_body())
        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value={'id': 'container-1', 'user_id': USER_A})), \
             patch('src.api_handlers.ContainerService', return_value=mock_service):
            result = asyncio.run(api_handlers.create_container_in_k8s.__wrapped__(request=request))

        self.assertEqual(result.status_code, 500)
        self.assertIn("ContainerMaker unreachable", result.body.decode())

    def test_k8s_success_never_triggers_a_release(self) -> None:
        from src.containers.dto.container_response_dto import ContainerResponseModel
        mock_service = MagicMock()
        mock_service.create_container_in_k8s = AsyncMock(return_value=ContainerResponseModel(
            container_name='my-container', container_id='pod-uid', container_ip='10.0.0.1',
            container_network=f'{USER_A}-namespace', container_ports=[], associated_resources=[],
        ))
        mock_service.delete_container_in_db = AsyncMock()

        request = _mock_request(_body())
        with patch('src.api_handlers.get_container_by_id', AsyncMock(return_value={'id': 'container-1', 'user_id': USER_A})), \
             patch('src.api_handlers.ContainerService', return_value=mock_service):
            result = asyncio.run(api_handlers.create_container_in_k8s.__wrapped__(request=request))

        self.assertEqual(result.status_code, 200)
        mock_service.delete_container_in_db.assert_not_called()


if __name__ == '__main__':
    import unittest
    unittest.main()
