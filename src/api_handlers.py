'''
Route handlers.
Their job is to parse request data, call some class and return response data.
'''

import asyncio
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response

from src.containers.containers_service import ContainerService
from src.data_models.containers import CreateContainerDBRequest, CreateContainerK8SRequest, GetContainerRequest, ResourceLimits, UpdateContainerRequest, UpdateContainerFilters, UpdateContainerData, ListUserContainersRequest, DeleteContainerDBRequest, DeleteContainerK8SRequest, SaveContainerK8SRequest
from browseterm_db.models.containers import SaveStatus, ContainerStatus
from src.data_models.echo import EchoRequestData, EchoResponseData
from src.data_models.payments import CreatePaymentRequest
from src.payments.payments_service import PaymentService
from src.common.exceptions import PaymentGatewayException, PaymentGatewayUnavailableException
from src.authentication.authentication_helpers import authenticate_session
from src.authentication.authentication_service import AuthenticationService, CSRF_COOKIE_NAME
from src.cloud_client.client import CloudClient, CloudClientError
from src.cloud_client.config import BROWSETERM_CLOUD_API_URL
from src.common.logging_setup import get_logger, request_id_var
from src.db_ops.container_db_ops import get_container_by_id, list_user_containers as list_user_containers_db, update_container_fields
from src.db_ops.subscription_db_ops import get_user_current_subscription_plan
from src.db_ops.dto.subscription_dto import GetUserSubscriptionPlanModel
from kubernetes.utils.quantity import parse_quantity

logger = get_logger("api_handlers")


def _csrf_ok(request: Request) -> bool:
    '''Double-submit CSRF check (p07.md section 30) for cookie-authenticated state-changing
    routes: the non-HttpOnly csrf_token cookie set at login must be echoed back as a header by
    same-origin JS. A cross-site form/fetch riding the ambient session cookie cannot read that
    cookie to echo it, so this fails closed for it. Only applied to cookie-authenticated routes -
    device Bearer-token requests are explicitly NOT subjected to this (p07.md section 30).'''
    header_token = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    return bool(header_token) and bool(cookie_token) and header_token == cookie_token


async def auth_provider_redirect(request: Request) -> RedirectResponse:
    '''
    GET /auth/{provider} -- p07.md section 7: Local's login buttons no longer initiate provider
    OAuth themselves, they just redirect to Cloud, which is the sole OAuth authority.
    '''
    provider = request.path_params["provider"]
    return RedirectResponse(url=f"{BROWSETERM_CLOUD_API_URL}/auth/{provider}/start?target=local", status_code=302)


async def auth_callback(request: Request) -> Response:
    '''
    GET /auth/callback?code=<handoff> -- Cloud redirects the browser here after finishing OAuth
    itself. Redeems the one-time handoff against Cloud (never touches provider tokens or Cloud's
    Postgres/Redis directly) and establishes the local browser session.
    '''
    code = request.query_params.get("code")
    if not code:
        return RedirectResponse(
            url="/login?auth_result=error&error_message=Missing+authentication+code", status_code=302
        )
    auth_service = AuthenticationService()
    login_response = await auth_service.complete_login_from_handoff(code)
    if login_response.status_code != 200:
        return RedirectResponse(
            url="/login?auth_result=error&error_message=Authentication+failed", status_code=302
        )
    redirect = RedirectResponse(url="/?auth_result=success", status_code=302)
    for cookie_header in login_response.headers.getlist("set-cookie"):
        redirect.headers.append("set-cookie", cookie_header)
    return redirect


async def auth_refresh(request: Request) -> JSONResponse:
    '''
    POST /auth/refresh -- the original plan's P07 scope explicitly included "session refresh"
    as its own item (FINAL_BROWSETERM_V2_IMPLEMENTATION_PLAN.md), not just the incidental
    extend-on-any-authenticated-call side effect `authenticate_session` already has. Needed for
    long-lived pages (the terminal page, most importantly) where the user may not trigger any
    other authenticated HTTP call for the whole 30-minute session window - they're just typing
    over an already-established WebSocket to socket-ssh. Frontend JS polls this periodically
    (see templates/static/js/base.js's session refresh heartbeat) to keep the session alive
    without a full page navigation.

    Deliberately does NOT use @authenticate_session - that redirects (302) to /login on an
    invalid session, which is right for a page load but wrong for an XHR/fetch call (the browser
    silently follows the redirect and hands the caller login-page HTML instead of a clean
    signal). Returns plain JSON instead: 200 on success, 401 on a missing/invalid/expired
    session, so the frontend can detect it and navigate to /login itself.
    '''
    session_id = request.cookies.get("session")
    if not session_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    auth_service = AuthenticationService()
    validation = await auth_service.validate_session(session_id)
    if not validation.get("is_valid"):
        return JSONResponse(content={"error": "Session expired"}, status_code=401)
    return JSONResponse(content={"status": "ok"}, status_code=200)


async def logout(request: Request) -> Response:
    '''
    Logout user: revoke the session server-side (Cloud) and clear the session + CSRF cookies.
    p07.md section 31 - the previous implementation only ever cleared the cookie, since this
    handler never read the session cookie to pass along; fixed here.
    '''
    if not _csrf_ok(request):
        return JSONResponse(content={"error": "Invalid CSRF token"}, status_code=403)
    session_id = request.cookies.get("session")
    auth_service = AuthenticationService()
    return await auth_service.logout(session_id=session_id)


@authenticate_session
async def device_bootstrap(request: Request) -> JSONResponse:
    '''
    POST /device/bootstrap -- p07.md section 21: the smallest secure bridge from an already-
    authenticated browser/WebView session to a native device credential. Desktop calls this
    directly with the session cookie it already extracted from the WebView (see
    browseterm-desktop's desktop/app.py), gets back a one-time bootstrap code, and immediately
    redeems that against Cloud's public POST /auth/device-bootstrap/redeem - Desktop never uses
    the session cookie itself as its ongoing device credential.
    '''
    if not _csrf_ok(request):
        return JSONResponse(content={"error": "Invalid CSRF token"}, status_code=403)
    user_id = request.state.user_info["id"]
    try:
        code = CloudClient().create_device_bootstrap(user_id)
    except CloudClientError as e:
        logger.error("device bootstrap start failed", extra={"error": e.message})
        return JSONResponse(content={"error": "Could not start device bootstrap"}, status_code=502)
    return JSONResponse(content={"code": code})


async def echo(request: EchoRequestData) -> EchoResponseData:
    '''
    Simply echo the request message.
    '''
    return EchoResponseData(message=request.message)


@authenticate_session
async def create_payment(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Calls payment-gateway's makePayment RPC and returns the (currently hardcoded) result.

    v0: amount_minor/currency are hardcoded server-side here since no real plans/pricing
    exist yet.
    # TODO: once a plans table exists, resolve amount_minor/currency server-side from
    # plan_id instead of hardcoding them — never trust a browser-provided amount.
    '''
    try:
        request_data: dict = await request.json()
        create_payment_request: CreatePaymentRequest = CreatePaymentRequest(**request_data)

        payment_service = PaymentService()
        payment_response = await payment_service.make_payment(
            user_id=request.state.user_info['id'],
            plan_id=create_payment_request.plan_id,
            amount_minor=49900,
            currency="INR",
            idempotency_key=create_payment_request.idempotency_key,
        )
        return JSONResponse(content=payment_response.model_dump())
    except PaymentGatewayUnavailableException as e:
        return JSONResponse(content={'error': f"Payment service unavailable: {str(e)}"}, status_code=503)
    except PaymentGatewayException as e:
        return JSONResponse(content={'error': f"Error processing payment: {str(e)}"}, status_code=500)
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error processing payment: {str(e)}"}, status_code=500)


@authenticate_session
async def get_container_info(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Gets container info by container ID.
    '''
    try:
        # build the get container request
        get_container_request: GetContainerRequest = GetContainerRequest(
            container_id=request.path_params['container_id'],
            user_id=request.state.user_info['id']  # never trust a client-supplied user_id; derive from session
        )
        # get container info using ContainerService
        container_service = ContainerService()
        container_info: dict = await container_service.get_container_info(get_container_request)
        return JSONResponse(content=container_info)
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error getting container info: {str(e)}"}, status_code=500)

@authenticate_session
async def create_container_in_db(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Creates a container in the database with PENDING status.
    This is the first step of the two-step container creation process.
    '''
    try:
        # get the create container request
        request_data: dict = await request.json()
        create_container_db_request: CreateContainerDBRequest = CreateContainerDBRequest(
            # never trust a client-supplied user_id (would let a caller create a container
            # "owned" by any other user) -- ownership always comes from the authenticated session.
            user_id=request.state.user_info['id'],
            image_id=request_data['image_id'],
            container_name=request_data['name'],
            cpu_limit=request_data.get('cpu_limit', '1'),
            memory_limit=request_data.get('memory_limit', '1Gi'),
            storage_limit=request_data.get('storage_limit', '2Gi'),
            publish_information=request_data.get('port_mappings', []),
            environment_variables=request_data.get('environment_variables', {})
        )
        # create a container in the database using ContainerService
        container_service = ContainerService()
        create_container_db_result: dict = await container_service.create_container_in_db(create_container_db_request)
        # return the response
        return JSONResponse(content=create_container_db_result)
    except HTTPException as e:
        # need to add logging here later
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        # need to add logging here later
        return JSONResponse(content={'error': f"Error creating container in database: {str(e)}"}, status_code=500)


@authenticate_session
async def create_container_in_k8s(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Creates a container in Kubernetes and updates the database record.
    This is the second step of the two-step container creation process.

    The status sidecar will update the container status via pg_notify,
    which will be pushed to the frontend via SSE.
    '''
    container_id = None
    try:
        request_data: dict = await request.json()

        # Extract required data
        container_id = request_data['container_id']
        user_id = request.state.user_info['id']
        resource_requirements = request_data.get('resource_requirements', {})

        # Ownership check BEFORE any k8s side effect: container_id is stamped as the
        # browseterm/container-id pod label that the central status_monitor uses to update this
        # row's status, so creating a pod for a container_id the caller doesn't own would let
        # them hijack another user's container row. Scoped lookup avoids leaking whether the id
        # exists at all if it isn't the caller's.
        owned_row = await get_container_by_id(container_id, user_id)
        if not owned_row:
            return JSONResponse(content={'error': f'Container {container_id} not found'}, status_code=404)

        # Build resource limits
        resource_limits = ResourceLimits(
            cpu_limit=resource_requirements.get('cpu_limit', '1'),
            memory_limit=resource_requirements.get('memory_limit', '1Gi'),
            storage_limit=resource_requirements.get('ephemeral_limit', '2Gi'),
            snapshot_size_limit=resource_requirements.get('snapshot_size_limit', '2Gi')
        )
        # No DB credentials are injected into the user pod. Status is written by the central
        # status_monitor (which reads pod phase from the k8s API), not by an in-pod sidecar, so the
        # untrusted user pod never receives database credentials. CONTAINER_ID is passed only so
        # container-maker can stamp it as the browseterm/container-id pod label the monitor reads.
        environment_variables: dict = {
            **request_data.get('environment_variables', {}),
            'CONTAINER_ID': container_id,
        }
        # Build the K8S request. network_name is always derived from the authenticated user
        # (matching the resume_container convention), never taken from the client body -- a
        # client-supplied network_name would let a pod be created in another user's namespace.
        # container-maker's pod/service/ingress lookups are namespace-scoped, so this also fully
        # confines delete/save's own ownership checks (see those handlers) to the caller's tenant.
        create_container_k8s_request = CreateContainerK8SRequest(
            image_id=request_data['image_id'],
            container_name=request_data['container_name'],
            network_name=f"{user_id}-namespace",
            exposure_level=request_data.get('exposure_level', 2),
            publish_information=request_data.get('publish_information', []),
            environment_variables=environment_variables,
            resource_limits=resource_limits
        )

        # Create container in K8s using ContainerService
        container_service = ContainerService()
        try:
            container_response = await container_service.create_container_in_k8s(create_container_k8s_request)
        except Exception:
            # P13 (see ~/browseterm/p.md's "P13" section): the DB-row+resource-reservation step
            # (create_container_in_db, a separate prior request) already succeeded by the time
            # this handler runs - a failure here is exactly the "partial failure" the plan's P13
            # entry calls out. Without this, a failed K8s/ContainerMaker creation would leak a
            # permanently-PENDING row and its Cloud-side device resource reservation forever
            # (nothing else ever calls delete on it), and the container's name would block any
            # retry under the same name. Best-effort - a failure to clean up here does not hide
            # the real k8s error from the caller below.
            try:
                await container_service.delete_container_in_db(
                    DeleteContainerDBRequest(container_id=container_id, user_id=user_id)
                )
            except Exception:
                logger.error(
                    "failed to release container row/reservation after k8s creation failure",
                    extra={"container_id": container_id}, exc_info=True,
                )
            raise
        return JSONResponse(content=container_response.model_dump())
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error creating container in Kubernetes: {str(e)}"}, status_code=500)


@authenticate_session
async def update_container(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Updates a container in the database.
    '''
    try:
        request_data: dict = await request.json()

        # Build filters. user_id is ALWAYS the authenticated session's id -- never the client
        # body's (which the frontend doesn't even send today): an id/kubernetes_id/name-only
        # filter would let any authenticated user update ANY container's fields.
        filters_data = request_data.get('filters', {})
        filters = UpdateContainerFilters(
            container_id=filters_data.get('container_id'),
            user_id=request.state.user_info['id'],
            kubernetes_id=filters_data.get('kubernetes_id'),
            name=filters_data.get('name')
        )

        # Build update data
        data_dict = request_data.get('data', {})
        data = UpdateContainerData(
            image_id=data_dict.get('image_id'),
            name=data_dict.get('name'),
            status=data_dict.get('status'),
            cpu_limit=data_dict.get('cpu_limit'),
            memory_limit=data_dict.get('memory_limit'),
            storage_limit=data_dict.get('storage_limit'),
            ip_address=data_dict.get('ip_address'),
            port_mappings=data_dict.get('port_mappings'),
            environment_vars=data_dict.get('environment_vars'),
            associated_resources=data_dict.get('associated_resources'),
            kubernetes_id=data_dict.get('kubernetes_id'),
            saved_image=data_dict.get('saved_image')
        )

        # Build the request
        update_request = UpdateContainerRequest(filters=filters, data=data)

        # Update via ContainerService
        container_service = ContainerService()
        result = await container_service.update_container(update_request)
        return JSONResponse(content=result)
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error updating container: {str(e)}"}, status_code=500)


@authenticate_session
async def list_user_containers(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Lists all containers for a specific user.
    '''
    try:
        # never trust a client-supplied user_id (query param) -- it would let a caller list
        # any other user's containers. Always derive from the authenticated session.
        user_id = request.state.user_info['id']

        limit = request.query_params.get('limit')
        offset = request.query_params.get('offset')

        # Convert to int if provided
        limit = int(limit) if limit else None
        offset = int(offset) if offset else None

        list_containers_request = ListUserContainersRequest(
            user_id=user_id,
            limit=limit,
            offset=offset
        )

        container_service = ContainerService()
        containers = await container_service.list_user_containers(list_containers_request)
        return JSONResponse(content={'containers': containers})
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error listing containers: {str(e)}"}, status_code=500)


@authenticate_session
async def delete_container_in_db(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Deletes a container from the database.
    This is the first step of the two-step container deletion process.
    '''
    try:
        request_data: dict = await request.json()

        # never trust a client-supplied user_id: the downstream delete is scoped by
        # (container_id, user_id), so a client-supplied user_id would let an authenticated
        # attacker delete another user's container by supplying that user's id + container_id.
        delete_container_db_request = DeleteContainerDBRequest(
            container_id=request_data['container_id'],
            user_id=request.state.user_info['id']
        )

        container_service = ContainerService()
        result = await container_service.delete_container_in_db(delete_container_db_request)
        return JSONResponse(content=result)
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error deleting container from database: {str(e)}"}, status_code=500)


@authenticate_session
async def delete_container_in_k8s(request: Request) -> JSONResponse:
    '''
    Authentication: This handler needs to be authenticated.
    Deletes a container from Kubernetes.
    This is the second step of the two-step container deletion process.
    '''
    try:
        request_data: dict = await request.json()

        # Note: The frontend sends 'container_id' but it's actually the kubernetes_id (pod UID)
        # The naming is confusing but we maintain backward compatibility with frontend
        #
        # network_name is always derived from the authenticated session, never the client body:
        # container-maker's delete only ever looks up pods/services/ingress WITHIN the given
        # namespace (see container-maker/src/containers/containers.py), so confining it to the
        # caller's own namespace fully prevents deleting another user's k8s resources regardless
        # of which pod UID is supplied.
        delete_container_k8s_request = DeleteContainerK8SRequest(
            container_id=request_data['container_id'],  # This is the pod UID from K8s
            network_name=f"{request.state.user_info['id']}-namespace"
        )

        container_service = ContainerService()
        result = await container_service.delete_container_in_k8s(delete_container_k8s_request)
        return JSONResponse(content=result.model_dump())
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error deleting container from Kubernetes: {str(e)}"}, status_code=500)


async def _set_save_status(container_id: str, user_id: str, save_status: str, save_error: str = None, stamp_attempt: bool = False) -> None:
    """Update a container's save_status/save_error via Cloud's container API. The save-status
    trigger fires the SSE. stamp_attempt=True records last_save_attempted_at (when this attempt
    started) -- set only from save_container, at the single moment a save is actually initiated
    (Pending)."""
    data = {"save_status": save_status, "save_error": save_error, "last_request_id": request_id_var.get()}
    if stamp_attempt:
        data["last_save_attempted_at"] = datetime.now(timezone.utc)
    await update_container_fields(container_id, user_id, data)


async def _run_save(container_service, save_request, container_id: str, user_id: str) -> None:
    """Background task: run the (blocking) gRPC save. The Job records SUCCEEDED/FAILED via the DB;
    if the gRPC call itself fails before the Job records anything, mark FAILED here."""
    try:
        await container_service.save_container_in_k8s(save_request)
    except Exception as e:
        try:
            await _set_save_status(container_id, user_id, SaveStatus.FAILED.value, save_error=str(e)[:1000])
        except Exception as db_e:
            logger.error("failed to record save failure", extra={"container_id": container_id}, exc_info=True)


@authenticate_session
async def save_container(request: Request) -> JSONResponse:
    '''
    Authentication required. Triggers an asynchronous container save/snapshot.
    Sets save_status=PENDING immediately and fires the save in the background; progress is
    delivered to the frontend via the container status SSE ('save_status_change' events).
    '''
    try:
        request_data: dict = await request.json()
        container_id = request_data['container_id']   # DB container id
        user_id = request.state.user_info['id']

        # Ownership check BEFORE any side effect: Cloud's container API update is itself always
        # ownership-scoped ({id, user_id} together, see container_db_ops.update_container_fields),
        # but container-maker performs a real snapshot Job, so an unscoped lookup would still let
        # any authenticated user trigger another user's save side effect. Scoped lookup avoids
        # leaking whether the id exists at all if it isn't the caller's.
        owned_row = await get_container_by_id(container_id, user_id)
        if not owned_row:
            return JSONResponse(content={'error': f'Container {container_id} not found'}, status_code=404)

        # Mark PENDING now so the frontend can show the spinner immediately, and stamp
        # last_save_attempted_at -- this is the one place a save is actually initiated.
        await _set_save_status(container_id, user_id, SaveStatus.PENDING.value, stamp_attempt=True)

        # network_name is always derived from the authenticated (and now ownership-verified)
        # user, never the client body -- see create/delete-container-in-k8s for why.
        save_request = SaveContainerK8SRequest(container_id=container_id, network_name=f"{user_id}-namespace")
        container_service = ContainerService()

        # container-maker blocks until the snapshot Job completes, so run it in the background.
        asyncio.create_task(_run_save(container_service, save_request, container_id, user_id))

        return JSONResponse(content={'status': 'pending', 'container_id': container_id}, status_code=202)
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse(content={'error': f"Error starting container save: {str(e)}"}, status_code=500)


# Statuses that have (or are about to have) a live pod -- what counts against a tier's
# max_containers concurrency limit. HIBERNATED/FAILED/etc. don't count, by design: hibernating
# (deliberately or via crash recovery) is exactly how a user frees a slot without losing a
# container's data, so it would be self-defeating for a hibernated row to still count as "active".
_ACTIVE_CONTAINER_STATUSES = {ContainerStatus.PENDING.value, ContainerStatus.RUNNING.value, ContainerStatus.RESUMING.value}


async def _count_active_containers(user_id: str, exclude_container_id: str) -> int:
    '''Count this user's containers that currently have (or are about to have) a live pod,
    excluding exclude_container_id itself -- the one about to be resumed, which is not active yet.'''
    rows = await list_user_containers_db(user_id) or []
    return sum(
        1 for r in rows
        if r["id"] != exclude_container_id
        and r.get("deleted_at") is None
        and r.get("status") in _ACTIVE_CONTAINER_STATUSES
    )


def _exceeds_tier_spec(container_row: dict, subscription_type: dict) -> bool:
    '''True if this container's recorded resource limits exceed what the given subscription type
    currently allows per container -- e.g. it was created/saved under a higher tier the user has
    since downgraded from or lost. Fails open (returns False, i.e. allowed) on any unparseable
    value (e.g. the Pro tier's placeholder "Configurable" limits) rather than incorrectly
    blocking a resume over a value that was never meant to be compared numerically.'''
    checks = (
        ("cpu_limit", "cpu_limit_per_container"),
        ("memory_limit", "memory_limit_per_container"),
        ("storage_limit", "storage_limit_per_container"),
    )
    for container_key, tier_key in checks:
        try:
            container_value = parse_quantity(container_row.get(container_key))
            tier_value = parse_quantity(subscription_type.get(tier_key))
        except (ValueError, TypeError, ArithmeticError):
            logger.warning(
                "could not compare container spec against tier limit, allowing",
                extra={"container_key": container_key, "tier_key": tier_key},
            )
            continue
        if container_value > tier_value:
            return True
    return False


@authenticate_session
async def resume_container(request: Request) -> JSONResponse:
    '''
    Authentication required. Resume a HIBERNATED container: recreate its pod from the saved snapshot
    image (falls back to the base image if it was never saved), reconstructing the create request
    from the stored row. The surviving/new Service routes to it via the app=<name> label.

    Gated by the user's current subscription plan before anything is created: resuming must not
    push them over their plan's concurrent-container limit, and the container's own recorded
    resource spec must still fit within what the plan allows per container (covers a downgrade
    since this container was last saved). Both checks fail open (log + allow) if the subscription
    lookup itself errors -- a payments-system hiccup should never block a user from recovering
    their own workspace, especially since this same endpoint is what crash recovery resumes
    through too.
    '''
    container_id = None
    user_id = None
    try:
        request_data: dict = await request.json()
        container_id = request_data['container_id']
        logger.info("resume requested", extra={"container_id": container_id})

        # Ownership-scoped lookup: an id-only lookup here would let any authenticated user
        # resume (and consume the compute/quota of) ANY other user's hibernated container just
        # by knowing its id, before ever calling get_user_current_subscription_plan below with
        # THAT container's own row['user_id'] -- i.e. it would use the victim's entitlement to
        # authorize an action performed by the attacker's session. Scoping the lookup up front
        # keeps every subsequent row['user_id'] reference (subscription/plan checks, network_name,
        # environment, DB updates) tied to the caller who actually owns this container.
        user_id = request.state.user_info['id']
        row: dict = await get_container_by_id(container_id, user_id)
        if not row:
            logger.warning("resume: container not found", extra={"container_id": container_id})
            return JSONResponse(content={'error': f'Container {container_id} not found'}, status_code=404)

        try:
            subscription_type = await get_user_current_subscription_plan(
                GetUserSubscriptionPlanModel(user_id=row['user_id'])
            )
            active_count = await _count_active_containers(row['user_id'], container_id)
            if active_count >= subscription_type['max_containers']:
                logger.info(
                    "resume blocked: over plan's container limit",
                    extra={
                        "container_id": container_id, "user_id": row['user_id'],
                        "active_count": active_count, "max_containers": subscription_type['max_containers'],
                    },
                )
                return JSONResponse(
                    content={'error': (
                        f"Only {subscription_type['max_containers']} terminal(s) allowed on "
                        f"{subscription_type['name']}. Please hibernate or delete another "
                        f"container to activate this one."
                    )},
                    status_code=409,
                )
            if _exceeds_tier_spec(row, subscription_type):
                logger.info(
                    "resume blocked: container spec exceeds current plan",
                    extra={"container_id": container_id, "user_id": row['user_id'], "subscription_type": subscription_type.get('type')},
                )
                return JSONResponse(
                    content={'error': (
                        f"This terminal is ineligible for {subscription_type['name']}. "
                        f"Please upgrade to continue using this container."
                    )},
                    status_code=409,
                )
        except Exception:
            logger.error("entitlement check failed, allowing resume", extra={"container_id": container_id}, exc_info=True)

        # mark RESUMING so the UI can show progress
        await update_container_fields(
            container_id, user_id, {"status": ContainerStatus.RESUMING, "last_request_id": request_id_var.get()}
        )

        # No DB credentials in the user pod (same as create): status is written by the central
        # status_monitor, not an in-pod sidecar. CONTAINER_ID rides only so container-maker can stamp
        # the browseterm/container-id label. Keep any stored env vars.
        environment_variables: dict = {
            **(row.get('environment_vars') or {}),
            'CONTAINER_ID': container_id,
        }
        resource_limits = ResourceLimits(
            cpu_limit=row.get('cpu_limit') or '1',
            memory_limit=row.get('memory_limit') or '1Gi',
            storage_limit=row.get('storage_limit') or '2Gi',
            snapshot_size_limit='2Gi',
        )
        k8s_request = CreateContainerK8SRequest(
            image_id=row['image_id'],
            container_name=row['name'],
            network_name=f"{row['user_id']}-namespace",
            exposure_level=2,
            publish_information=row.get('port_mappings') or [],
            environment_variables=environment_variables,
            resource_limits=resource_limits,
        )
        container_service = ContainerService()
        # recreate the pod FROM the snapshot (saved_image); base image if it was never saved.
        response = await container_service.create_container_in_k8s(
            k8s_request, image_name_override=row.get('saved_image')
        )
        # sync the new pod identity back to the row so the next save resolves it; RUNNING (the
        # central status_monitor keeps the status accurate thereafter). ip_address MUST be updated
        # here: resume creates a brand-new Service with a new ClusterIP, and the monitor only touches
        # status — without this the terminal keeps dialing the old (deleted) IP and SSH times out.
        await update_container_fields(
            container_id,
            user_id,
            {
                "kubernetes_id": response.container_id,
                "ip_address": response.container_ip,
                "associated_resources": response.associated_resources,
                "status": ContainerStatus.RUNNING,
                "last_request_id": request_id_var.get(),
            },
        )
        logger.info("resume complete", extra={"container_id": container_id, "kubernetes_id": response.container_id})
        return JSONResponse(
            content={'status': 'resumed', 'container_id': container_id, 'kubernetes_id': response.container_id},
            status_code=200,
        )
    except HTTPException as e:
        return JSONResponse(content={'error': e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error("resume failed", extra={"container_id": container_id}, exc_info=True)
        if container_id and user_id:
            try:
                await update_container_fields(container_id, user_id, {"status": ContainerStatus.FAILED})
            except Exception:
                pass
        return JSONResponse(content={'error': f"Error resuming container: {str(e)}"}, status_code=500)


@authenticate_session
async def container_activity(request: Request) -> JSONResponse:
    '''
    Authenticated activity heartbeat, called by the terminal page while a terminal is in use.
    Does two things from one call:
      1. Refreshes the login session TTL — via the @authenticate_session decorator, so a user
         working only in a terminal (WebSocket traffic, no other HTTP requests) is NOT logged out.
      2. Stamps last_active_at on the container — the idle signal the reaper reads to decide what
         to hibernate. Scoped to the caller's own container (id + user_id).
    '''
    try:
        request_data: dict = await request.json()
        container_id = request_data.get('container_id')
        if not container_id:
            return JSONResponse(content={'error': 'container_id is required'}, status_code=400)
        user_info = request.state.user_info
        user_id = user_info.id if hasattr(user_info, 'id') else user_info['id']
        await update_container_fields(container_id, user_id, {"last_active_at": datetime.now(timezone.utc)})
        return JSONResponse(content={'status': 'ok'}, status_code=200)
    except Exception as e:
        return JSONResponse(content={'error': f"Error recording activity: {str(e)}"}, status_code=500)
