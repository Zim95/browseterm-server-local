'''
Subscription database operations - now a thin CloudClient wrapper.

Local holds no Postgres client at all. Only the two functions actually used by Local's active
code paths are kept (see git history for the pre-migration versions of
create_free_subscription/get_or_create_free_subscription/get_current_subscription_plan/
update_subscription - those moved entirely into Cloud's process_user_info and
GET /subscriptions/current, since they were only ever used by the OAuth login flow, which no
longer runs any of this locally).
'''

# builtins
from typing import Any, Dict, List, Optional

# modules
from src.cloud_client.client import CloudClient, CloudClientError
from src.common.logging_setup import get_logger

# DTOs
from src.db_ops.dto.subscription_dto import GetUserSubscriptionPlanModel

logger = get_logger("subscription_db_ops")


async def list_all_existing_subscription_types() -> Optional[List[Dict[str, Any]]]:
    '''List all existing subscription types via Cloud's read-only catalog API.'''
    try:
        client = CloudClient()
        return client.list_subscription_types()
    except CloudClientError as e:
        logger.error("error listing all existing subscription types", exc_info=True)
        raise Exception(f"Database operation failed: {e.message}")


async def get_user_current_subscription_plan(data: GetUserSubscriptionPlanModel) -> Optional[Dict[str, Any]]:
    '''
    Get the current (effective) subscription plan of the user via Cloud's GET
    /subscriptions/current - Cloud resolves get-or-create-free-subscription +
    get-current-subscription-plan server-side in one call.
    '''
    try:
        client = CloudClient()
        return client.get_current_subscription(data.user_id)
    except CloudClientError as e:
        logger.error("error getting user current subscription plan", extra={"user_id": data.user_id}, exc_info=True)
        raise Exception(f"Database operation failed: {e.message}")
