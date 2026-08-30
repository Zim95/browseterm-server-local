# builtins
from typing import Dict, Any, Optional
from functools import wraps

# modules
from fastapi import Request
from fastapi.responses import RedirectResponse

# local
from src.common.logging_setup import set_request_context
from src.cloud_client.client import CloudClient, CloudClientError

# dtos
from src.authentication.dto.session_dto import SessionResponseModel
from src.authentication.dto.user_info_dto import UserInfoModel


async def process_user_info(user_info: UserInfoModel) -> SessionResponseModel:
    '''
    Turn a verified OAuth profile into a session.

    Local no longer creates/updates the user row, resolves their subscription, or writes the
    Redis session itself - Cloud's POST /auth/sessions does all of that (it's the only process
    allowed to touch Postgres/Redis). Local's job is just the OAuth token exchange that produced
    `user_info` (see oauth_service.py) and handing the resulting verified profile to Cloud.

    Raises:
        Exception: if the Cloud session-creation call fails
    '''
    try:
        client = CloudClient()
        payload = user_info.model_dump(mode="json")
        response = client.create_session(payload)
        return SessionResponseModel(**response)
    except CloudClientError as e:
        raise Exception(f"Error creating session: {e.message}")
    except Exception as e:
        raise Exception(f"Error processing user info: {str(e)}")


async def validate_session(session_id: str) -> Dict[str, Any]:
    '''
    Ask Cloud whether this session is still valid, extending it on success (Cloud's
    /auth/sessions/validate does both in one call - matching the previous decorator's
    validate-then-extend behavior).

    Returns the raw {"is_valid": bool, "user_info"?, "subscription_info"?,
    "current_subscription_plan"?} dict - never raises for an invalid/expired session, only for a
    genuine Cloud-call failure.
    '''
    try:
        client = CloudClient()
        return client.validate_session(session_id)
    except CloudClientError:
        return {"is_valid": False}


async def delete_session(session_id: str) -> None:
    '''Ask Cloud to delete this session (logout).'''
    try:
        client = CloudClient()
        client.delete_session(session_id)
    except CloudClientError as e:
        raise Exception(f"Error deleting session: {e.message}")


# this decorator can be used to authenticate the session
def authenticate_session(func: callable) -> callable:
    @wraps(func)
    async def wrapper(*args: tuple, **kwargs: dict) -> any:
        '''
        Authenticate the request by asking Cloud to validate the session cookie.
        If not authenticated, redirect to login page.
        '''
        request: Request = kwargs.get('request')
        session_id: str = request.cookies.get('session')
        if not session_id:
            return RedirectResponse(url="/login", status_code=302)

        validation: Dict[str, Any] = await validate_session(session_id)

        if not validation.get("is_valid") or not validation.get("user_info"):
            return RedirectResponse(url="/login", status_code=302)

        # Set the logging correlation context for this request: accept an inbound X-Request-Id
        # (else mint one) and record the acting user, so every log line in this handler — and any
        # gRPC call it makes — is tagged with the same request_id + user for cross-service tracing.
        ui = validation["user_info"]
        set_request_context(
            request_id=request.headers.get("X-Request-Id"),
            user=f"{ui.get('name') or 'user'}:{ui.get('email') or '-'}",
        )

        # Add session data to request.state
        request.state.user_info = validation["user_info"]
        request.state.subscription_info = validation.get("subscription_info", {})
        request.state.current_subscription_plan = validation.get("current_subscription_plan", {})
        request.state.session_id = session_id
        return await func(*args, **kwargs)
    return wrapper
