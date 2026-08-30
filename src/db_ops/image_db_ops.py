'''
Image database operations - now a thin CloudClient wrapper.

Local holds no Postgres client at all: image catalog data comes from Cloud's read-only
/catalog/images. Images are a small, mostly-static catalog, so `get_image` filters client-side
over the full list rather than needing a dedicated Cloud filter-by-field endpoint.
'''

# builtins
from typing import Any, Dict, Optional, List

# modules
from src.cloud_client.client import CloudClient, CloudClientError
from src.common.logging_setup import get_logger

# DTOs
from src.db_ops.dto.image_dto import GetImageDataModel

logger = get_logger("image_db_ops")


async def get_image(filters: GetImageDataModel) -> Optional[Dict[str, Any]]:
    '''
    Get a single image matching the given filters (id, name, and/or image), from the full
    catalog Cloud returns.
    '''
    try:
        images = await list_all_existing_images()
        for image in images:
            if filters.id and image.get("id") != filters.id:
                continue
            if filters.name and image.get("name") != filters.name:
                continue
            if filters.image and image.get("image") != filters.image:
                continue
            return image
        return None
    except Exception as e:
        logger.error("error getting image", exc_info=True)
        raise Exception(f"Database operation failed: {str(e)}")


async def list_all_existing_images() -> List[Dict[str, Any]]:
    '''List all existing images via Cloud's read-only catalog API.'''
    try:
        client = CloudClient()
        return client.list_images()
    except CloudClientError as e:
        logger.error("error listing images", exc_info=True)
        raise Exception(f"Database operation failed: {e.message}")
