'''
Authentication Service - Local's browser-facing session handling (P07).

P07 change: Local no longer performs Google/GitHub token exchange itself (that code - and the
GOOGLE/GITHUB client id/secret it needed - moved to Cloud entirely, see p07.md). Login now
completes by redeeming a one-time handoff code Cloud already minted after finishing OAuth itself
(`complete_login_from_handoff`), not by exchanging a provider code
(`GoogleAuthenticationService`/`GithubAuthenticationService` and `login()` are removed).
'''

# builtins
import json
import secrets
from typing import Optional

# fastapi
from fastapi import HTTPException
from fastapi.responses import Response

# local services
from src.authentication.authentication_helpers import delete_session, validate_session as validate_session_via_cloud
from src.cloud_client.client import CloudClient, CloudClientError

# dtos
from src.authentication.dto.logout_dto import LogoutResponseModel

# config
from src.common.config import SESSION_COOKIE_MAX_AGE, COOKIE_SECURE, COOKIE_SAMESITE

# logging
from src.common.logging_setup import get_logger

logger = get_logger("authentication_service")

CSRF_COOKIE_NAME = "csrf_token"


class AuthenticationService:
    '''Local's browser-facing session handling: complete login from a Cloud-issued handoff, log
    out, validate. Never talks to Google/GitHub or Cloud's Postgres/Redis directly.'''

    async def complete_login_from_handoff(self, code: str) -> Response:
        '''
        Redeem a one-time handoff code from Cloud's OAuth callback and establish the local
        browser session cookie. Also sets a non-HttpOnly CSRF cookie (double-submit pattern -
        see api_handlers.py's CSRF check on /logout and /device/bootstrap).
        '''
        try:
            session_response: dict = await CloudClient().redeem_handoff(code)
        except CloudClientError as e:
            logger.warning("handoff redemption failed", extra={"status_code": e.status_code})
            return Response(
                content=json.dumps({"error": "Login failed. Please try again.", "detail": e.message}),
                media_type="application/json",
                status_code=400 if e.status_code < 500 else 500,
            )

        response = Response(
            content=json.dumps(session_response), media_type="application/json", status_code=200
        )
        response.set_cookie(
            key="session",
            value=session_response["session_id"],
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
        )
        # Deliberately NOT httponly - the double-submit CSRF pattern requires JS to be able to
        # read this and echo it back as a header (see api_handlers.py). It is not a secret on its
        # own (an attacker who can read this cookie cross-site could already read the response
        # body); its only job is proving the request came from same-origin JS, not a cross-site
        # form/fetch riding the ambient session cookie.
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=secrets.token_urlsafe(32),
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=False,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
        )
        return response

    async def logout(self, session_id: Optional[str] = None) -> Response:
        try:
            if session_id:
                await delete_session(session_id)
            logout_data = LogoutResponseModel(message="Logged out successfully", success=True)
            response = Response(
                content=json.dumps(logout_data.model_dump()), media_type="application/json", status_code=200
            )
            response.set_cookie(
                key="session", value="", max_age=0, httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE
            )
            response.set_cookie(
                key=CSRF_COOKIE_NAME, value="", max_age=0, httponly=False, secure=COOKIE_SECURE,
                samesite=COOKIE_SAMESITE,
            )
            return response
        except Exception:
            logger.error("logout error", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def validate_session(self, session_id: str) -> dict:
        return await validate_session_via_cloud(session_id)
