'''
Container database operations - now a thin CloudClient wrapper.

Local holds no Postgres client at all: every read/write of container/workspace rows goes
through Cloud's container API (src/cloud_client/client.py). Function names/signatures here are
unchanged from the pre-migration direct-DB version so containers_service.py and api_handlers.py
did not need to change their call sites.
'''

# builtins
from typing import Dict, Any, Optional

# modules
from src.cloud_client.client import CloudClient, CloudClientError
from src.common.exceptions import ContainerDBException
from src.common.logging_setup import get_logger

# DTOs
from src.db_ops.dto.container_dto import CreateContainerDBModel, GetContainerDBModel, UpdateContainerDBModel

logger = get_logger("container_db_ops")


async def create_container_in_db(container_info: CreateContainerDBModel) -> Optional[Dict[str, Any]]:
    '''
    Create a container via Cloud's container API.
    Cloud enforces the same duplicate (name, user_id) rejection the direct-DB version did.
    '''
    if not container_info.name or not container_info.user_id:
        raise ValueError("Container name and user_id are required")
    try:
        client = CloudClient()
        return await client.create_container(container_info.to_dict())
    except CloudClientError as e:
        if e.status_code == 409:
            raise ContainerDBException(f"Container with name '{container_info.name}' already exists for this user.")
        raise ContainerDBException(f"Database operation failed: {e.message}")


async def update_container_in_db(update_data: UpdateContainerDBModel) -> Optional[Dict[str, Any]]:
    '''
    Update a container via Cloud's container API.

    Cloud's PUT-by-id contract requires the container's own id + user_id (matching P03's
    ownership-hardening convention exactly - {id, user_id} together, never id alone or a
    kubernetes_id/name-only filter, which the pre-migration version technically allowed but no
    real caller actually relied on).
    '''
    container_id = update_data.filters.id
    user_id = update_data.filters.user_id
    update_dict: Dict[str, Any] = update_data.data.to_update_dict()

    if not container_id:
        raise ValueError("A container id filter is required")
    if not user_id:
        raise ValueError("A user_id filter is required")
    if not update_dict:
        raise ValueError("No fields to update")

    try:
        client = CloudClient()
        return await client.update_container(container_id, user_id, update_dict)
    except CloudClientError as e:
        raise ContainerDBException(f"Database operation failed: {e.message}")


async def delete_container(container_id: str, user_id: str) -> bool:
    '''
    Delete a container via Cloud's container API. Ensures that only the owner can delete it.

    Returns:
        True if container was successfully deleted.
    Raises:
        ValueError: If container not found or user doesn't own the container.
    '''
    try:
        client = CloudClient()
        deleted = await client.delete_container(container_id, user_id)
        if not deleted:
            raise ValueError(
                f"Container with ID '{container_id}' not found or you don't have permission to delete it."
            )
        return True
    except CloudClientError as e:
        logger.error("error deleting container", extra={"container_id": container_id, "user_id": user_id})
        raise Exception(f"Database operation failed: {e.message}")


async def get_container(get_container_data: GetContainerDBModel) -> Optional[Dict[str, Any]]:
    '''Get a container via Cloud's container API. Ensures that only the owner can access it.'''
    try:
        client = CloudClient()
        return await client.get_container(get_container_data.container_id, get_container_data.user_id)
    except CloudClientError as e:
        logger.error(
            "error getting container",
            extra={"container_id": get_container_data.container_id, "user_id": get_container_data.user_id},
        )
        raise Exception(f"Database operation failed: {e.message}")


async def list_user_containers(user_id: str, limit: Optional[int] = None, offset: Optional[int] = None) -> Optional[list]:
    '''List all containers for a specific user via Cloud's container API.'''
    try:
        client = CloudClient()
        return await client.list_containers(user_id, limit=limit, offset=offset)
    except CloudClientError as e:
        logger.error("error listing containers", extra={"user_id": user_id})
        raise Exception(f"Database operation failed: {e.message}")


async def get_container_by_id(container_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    '''
    Thin ownership-scoped lookup, for the handlers (api_handlers.py) that need only a quick
    "does this container belong to this user" check before a k8s side effect - replaces the
    direct `ContainerOps(DB_CONFIG).find_one({"id": ..., "user_id": ...})` call sites.
    '''
    return await get_container(GetContainerDBModel(container_id=container_id, user_id=user_id))


async def update_container_fields(container_id: str, user_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    '''
    Thin ownership-scoped update for ad-hoc status/timestamp fields (save_status, last_active_at,
    kubernetes_id/ip_address after resume, etc.) - replaces the direct
    `ContainerOps(DB_CONFIG).update(...)` call sites in api_handlers.py. Unlike some of those
    original call sites (e.g. _set_save_status, which updated by {"id": container_id} alone),
    this is always ownership-scoped by {id, user_id} together, matching Cloud's contract.
    '''
    try:
        client = CloudClient()
        return await client.update_container(container_id, user_id, fields)
    except CloudClientError as e:
        logger.error(
            "error updating container fields", extra={"container_id": container_id, "user_id": user_id}
        )
        raise Exception(f"Database operation failed: {e.message}")
