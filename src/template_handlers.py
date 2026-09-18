'''
Route handlers.
Their job is to parse request data, call some class and return response data.
'''

import asyncio
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from src.authentication.authentication_helpers import authenticate_session
from src.cloud_client.client import CloudClient, CloudClientError
from src.cloud_client.config import BROWSETERM_CLOUD_API_URL
from src.db_ops.image_db_ops import list_all_existing_images
from src.db_ops.subscription_db_ops import list_all_existing_subscription_types
from src.db_ops.container_db_ops import get_container
from src.db_ops.dto.container_dto import GetContainerDBModel
from src.common.logging_setup import get_logger

logger = get_logger("template_handlers")


templates = Jinja2Templates(directory="templates")


@authenticate_session
async def home(request: Request) -> HTMLResponse:
    '''
    Home page template.
    '''
    return templates.TemplateResponse("home.html", {"request": request})


@authenticate_session
async def terminals(request: Request) -> HTMLResponse:
    '''
    Terminals page template.

    CPU/memory/storage controls are bounded by the active device's remaining quota (available =
    allocated - used, see browseterm-server/src/cloud/device_handlers.py's _serialize_device) --
    not by subscription plan any more, since subscriptions no longer gate anything about terminal
    creation (per explicit request). Cloud's own POST /containers is still the real enforcement
    (validates + reserves against the device's actual available capacity at creation time no
    matter what this page shows) -- this is only about showing a realistic bound up front instead
    of the flat static max the resource inputs' own HTML attribute used to have regardless of
    whether the device could actually support it.
    '''
    images: list = await list_all_existing_images()
    active_device = None
    try:
        active_device = await CloudClient().get_active_device(request.state.user_info['id'])
    except CloudClientError:
        logger.error("could not fetch active device for terminals page", exc_info=True)
    # P10: one-time-ish SSE token (see ~/browseterm/p.md's "P10" section) so the browser can
    # connect directly to Cloud's GET /events/stream for real-time container status updates -
    # Local no longer relays/polls for this itself.
    session_id = request.cookies.get('session')
    sse_token = await CloudClient().create_sse_token(session_id) if session_id else ''
    return templates.TemplateResponse(
        "terminals.html",
        {
            "request": request,
            "images": images,
            "userInfo": request.state.user_info,
            "activeDevice": active_device,
            "sseToken": sse_token,
            "cloudApiUrl": BROWSETERM_CLOUD_API_URL,
        }
    )


@authenticate_session
async def terminalpage(request: Request) -> HTMLResponse:
    '''
    Terminal page template - shows xterm.js terminal with ad banners.
    '''
    # Get terminal ID from query params
    terminal_id = request.query_params.get('id', '')

    if not terminal_id:
        # If no terminal ID provided, show error page
        terminal_info = {
            "id": "",
            "name": "Error",
            "ipAddress": "N/A",
            "port": "N/A",
            "error": "No terminal ID provided"
        }
    else:
        try:
            # Fetch actual terminal info from database
            get_container_data = GetContainerDBModel(
                container_id=terminal_id,
                user_id=request.state.user_info['id']  # user_info is a dict
            )
            container_data = await get_container(get_container_data)
            
            if not container_data:
                terminal_info = {
                    "id": terminal_id,
                    "name": "Not Found",
                    "ipAddress": "N/A",
                    "port": "N/A",
                    "error": "Terminal not found or you don't have access"
                }
            else:
                # Extract SSH credentials from environment variables
                env_vars = container_data.get('environment_vars', {})  # DB column is environment_vars
                ssh_username = env_vars.get('SSH_USERNAME', '')
                ssh_password = env_vars.get('SSH_PASSWORD', '')
                
                # Get port from port_mappings
                port_mappings = container_data.get('port_mappings', [])
                ssh_port = port_mappings[0].get('publish_port') if port_mappings else 2222
                
                terminal_info = {
                    "id": container_data.get('id'),
                    "name": container_data.get('name'),
                    "ipAddress": container_data.get('ip_address', 'Pending...'),
                    "port": str(ssh_port),
                    "sshUsername": ssh_username,
                    "sshPassword": ssh_password,
                    "status": container_data.get('status', 'Unknown'),
                    "saveStatus": container_data.get('save_status'),
                    "savedImage": container_data.get('saved_image'),
                    "saveError": container_data.get('save_error'),
                    "lastSavedAt": container_data.get('last_saved_at'),
                    "lastSaveAttemptedAt": container_data.get('last_save_attempted_at'),
                }
        except Exception as e:
            logger.error("error fetching terminal info", extra={"container_id": terminal_id}, exc_info=True)
            terminal_info = {
                "id": terminal_id,
                "name": "Error",
                "ipAddress": "N/A",
                "port": "N/A",
                "error": f"Error loading terminal: {str(e)}"
            }
    
    # remotetunelling.md Phase 6: no WebSocket token minted at page-render time any more -
    # terminalpage.js now calls POST /terminal-session (api_handlers.terminal_session) right
    # before each connection attempt to get a fresh single-use ticket + this device's current
    # tunnel URL, rather than embedding a token in the page that could go stale before use.
    session_id = request.cookies.get('session')
    # P10: SSE token for the browser's direct connection to Cloud's GET /events/stream.
    sse_token = await CloudClient().create_sse_token(session_id) if session_id else ''
    return templates.TemplateResponse(
        "terminalpage.html",
        {
            "request": request,
            "terminalInfo": terminal_info,
            "sseToken": sse_token,
            "cloudApiUrl": BROWSETERM_CLOUD_API_URL,
            "userInfo": request.state.user_info
        }
    )


@authenticate_session
async def subscriptions(request: Request) -> HTMLResponse:
    '''
    Subscriptions page template.
    '''
    subscriptions: list = await list_all_existing_subscription_types()
    return templates.TemplateResponse(
        "subscriptions.html",
        {
            "request": request,
            "subscriptions": subscriptions,
            "userInfo": request.state.user_info,
            "subscriptionInfo": request.state.subscription_info,
            "currentSubscriptionPlan": request.state.current_subscription_plan
        }
    )


@authenticate_session
async def payment(request: Request) -> HTMLResponse:
    '''
    Payment page template — reached by selecting a plan on /subscriptions.

    Resolves the plan server-side from plan_id (query param) against the same
    list_all_existing_subscription_types() source /subscriptions uses, rather than trusting a
    browser-supplied amount, per PAYMENTS.md. This is display-only: /create-payment still
    hardcodes the actual charged amount server-side for v0 (see api_handlers.py TODO), so the
    breakdown shown here is illustrative until real plan-based pricing lands.
    '''
    plan_id = request.query_params.get('plan_id', '')
    subscriptions: list = await list_all_existing_subscription_types()
    selected_plan = next((s for s in subscriptions if s.get('id') == plan_id), None)
    return templates.TemplateResponse(
        "payment.html",
        {
            "request": request,
            "selectedPlan": selected_plan,
            "userInfo": request.state.user_info,
        }
    )


@authenticate_session
async def profile(request: Request) -> HTMLResponse:
    '''
    User profile page template.

    Shows the user's currently active device (Desktop's "at most one ACTIVE device per user"
    invariant, see browseterm-server/src/cloud/device_handlers.py's _demote_other_devices) instead
    of a subscription plan -- subscriptions are commented out for now, see app.py. Local holds no
    device Bearer token itself (that credential belongs to Desktop alone), so this goes through
    Cloud's internal-token-gated GET /internal/users/{user_id}/active-device rather than the
    Bearer-gated /devices route. Fails open to None on any Cloud error -- a Profile page that
    can't reach Cloud should still render, just without a device name to show.
    '''
    active_device = None
    try:
        active_device = await CloudClient().get_active_device(request.state.user_info['id'])
    except CloudClientError:
        logger.error("could not fetch active device for profile page", exc_info=True)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "userInfo": request.state.user_info,
            "activeDevice": active_device,
        }
    )


async def login(request: Request) -> HTMLResponse:
    '''
    Login page template.

    P07: the buttons on this page are now plain links to Local's own /auth/{provider}
    (src/api_handlers.py:auth_provider_redirect), which redirects to Cloud - Local no longer
    builds a provider OAuth URL itself (no client_id/scope/redirect_uri to pass to the template
    any more; Cloud is the sole OAuth authority).
    '''
    return templates.TemplateResponse("login.html", {"request": request})


async def js_test(request: Request) -> HTMLResponse:
    '''
    JavaScript test runner page.
    Hidden route - not in sidebar navigation.
    '''
    return templates.TemplateResponse("js_test.html", {"request": request})
