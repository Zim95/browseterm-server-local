# builtins
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

# module under test
from src.containers.containers_service import ContainerService
from src.data_models.containers import ListUserContainersRequest


class TestContainerServiceLazyGrpcClient(TestCase):
    '''
    Regression coverage: ContainerService() must NOT read container-maker's mTLS certs from its
    Kubernetes Secret (or open a gRPC channel) at construction time. Only the three methods that
    actually call container-maker (create/delete/save _in_k8s) need it - DB-only operations like
    list_user_containers/get_container_info/update_container/create_container_in_db/
    delete_container_in_db never touch self.stub at all.

    Previously __init__ read the cert Secret unconditionally, so ANY ContainerService()
    instantiation - including a plain "list my containers" call - required container-maker's
    certs Secret to exist, breaking the terminal list entirely wherever that Secret wasn't
    provisioned (e.g. container-maker not deployed on that cluster) with a raw Kubernetes
    ApiException 404 surfaced to the user.
    '''

    def test_init_does_not_touch_k8s_secrets(self) -> None:
        with patch('src.containers.containers_service.read_cert_from_k8s_secret') as mock_read:
            ContainerService()
        mock_read.assert_not_called()

    def test_list_user_containers_works_without_container_maker_secret(self) -> None:
        '''The bug this regresses: listing containers must succeed even when container-maker's
        certs Secret genuinely does not exist on this cluster.'''
        with patch('src.containers.containers_service.read_cert_from_k8s_secret',
                   side_effect=Exception('secrets "container-maker-service-certs" not found')):
            service = ContainerService()
            with patch('src.containers.containers_service.list_user_containers_db',
                       AsyncMock(return_value=[{'id': 'c-1'}])):
                result = asyncio.run(
                    service.list_user_containers(
                        ListUserContainersRequest(user_id='user-42', limit=None, offset=None)
                    )
                )
        self.assertEqual(result, [{'id': 'c-1'}])

    def test_create_container_in_k8s_still_requires_the_secret(self) -> None:
        '''The methods that genuinely need container-maker must still fail (cleanly) when the
        Secret is missing - this fix only defers the read, it doesn't skip it for k8s calls.'''
        from fastapi import HTTPException
        with patch('src.containers.containers_service.read_cert_from_k8s_secret',
                   side_effect=Exception('secrets "container-maker-service-certs" not found')), \
             patch('src.containers.containers_service.get_image',
                   AsyncMock(return_value={'image': 'zim95/ssh_ubuntu:latest'})):
            service = ContainerService()
            request = MagicMock(
                image_id='image-1',
                container_name='c', network_name='ns', exposure_level=2,
                publish_information=[], environment_variables={},
                resource_limits=MagicMock(cpu_limit='1', memory_limit='1Gi', storage_limit='2Gi', snapshot_size_limit='2Gi'),
            )
            with self.assertRaises(HTTPException):
                asyncio.run(service.create_container_in_k8s(request))

    def test_grpc_client_only_initialized_once(self) -> None:
        '''A second _ensure_grpc_client() call (e.g. from a second k8s method call on the same
        instance) must not re-read the certs - the lazy init happens exactly once per instance.'''
        with patch('src.containers.containers_service.read_cert_from_k8s_secret', return_value=b'x') as mock_read, \
             patch('src.containers.containers_service.GRPCUtils') as mock_grpc_utils:
            mock_grpc_utils.return_value.channel = MagicMock()
            mock_grpc_utils.return_value.stub = MagicMock()
            service = ContainerService()
            service._ensure_grpc_client()
            service._ensure_grpc_client()
        self.assertEqual(mock_read.call_count, 3)  # client.key, client.crt, ca.crt - once only
        mock_grpc_utils.assert_called_once()
