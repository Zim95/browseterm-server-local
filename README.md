# browseterm-server-local

**Local control plane.** Serves the existing Browseterm browser UI and talks to local
ContainerMaker/Socket-SSH/Kubernetes. Holds **no PostgreSQL/Redis client at all** - every read
or write of central state (sessions, users, containers, images, subscriptions) goes through
Cloud's HTTP API via `src/cloud_client/`. Runs inside a separate Local k3s cluster
(`browseterm-k3s-local`) from Cloud's (`browseterm-k3s`) - see plan section 2 and
`browseterm-monorepo` for cluster bootstrap.

The Mac Desktop Resource MVP (auth + device registration + resource allocation) lives in its own
repository, [`browseterm-desktop`](https://github.com/Zim95/browseterm-desktop) - it was
originally built here during P06 and split out to match this project's one-component-per-repo
convention.

## Architecture correction (P06)

Through P04/P05 the Cloud Device API lived as a second entrypoint (`cloud_app.py`) inside the
`browseterm-server` repo, alongside the original combined local+auth+container `app.py`. P06
corrected that into two physically separate repositories:

- **`browseterm-server`** = Cloud control plane only (`app.py`, renamed from `cloud_app.py`;
  `src/cloud/*`). PostgreSQL/Redis credentials live only there and here (see below).
- **`browseterm-server-local`** (this repo) = the old combined `app.py` and everything it
  served, extracted as-is (a clean initial commit - not a history-preserving split, since the
  Local-owned files were scattered across the tree and several files needed to be *duplicated*
  rather than moved; see `p.md`'s P06 section in the main planning tree for the full rationale),
  plus one genuinely new P06 addition: `src/cloud_client/` (the Desktop Resource MVP was also
  built here originally, then split into `browseterm-desktop`).

## Trust boundary

The target architecture (plan section 2.2) is now the *actual* architecture: Local never holds
Cloud PostgreSQL/Redis credentials; all central-state access happens through Cloud's HTTP API.
Every former direct-DB path has been migrated:

| Former legacy direct-DB path | Now calls |
|---|---|
| `src/authentication/authentication_helpers.py` (session validate/delete) | `CloudClient.validate_session`/`delete_session` -> Cloud `POST /auth/sessions/validate`/`delete` |
| `src/template_handlers.py`'s one-time WebSocket token | `CloudClient.create_websocket_token` -> Cloud `POST /auth/websocket-tokens` |
| `src/status_listener.py` (was Postgres LISTEN/NOTIFY, then an interim polling relay) | **removed entirely as of P10** - the browser now connects directly to Cloud's own `GET /events/stream`, authenticated by an sse_token from `CloudClient.create_sse_token` -> Cloud `POST /auth/sse-tokens`. See `browseterm-server`'s README for the Cloud-side half. |
| `src/db_ops/container_db_ops.py`, `image_db_ops.py`, `subscription_db_ops.py` (container/image/subscription CRUD) | `CloudClient.create_container`/`get_container`/`list_containers`/`update_container`/`delete_container`/`list_images`/`list_subscription_types`/`get_current_subscription` |

`session_manager.py` and `db_ops/user_db_ops.py` were deleted outright (their only caller,
session issuance, moved entirely into Cloud's `process_user_info`).

Payments (`/create-payment`) are disabled - the route registration in `app.py` is commented out,
not deleted, so re-enabling is a one-line change. `PaymentService`/`payment-gateway` itself
never touched Postgres/Redis directly, so it needed no migration.

## P13 - workspace creation, device_id, and partial-failure cleanup

Workspace creation is two separate authenticated requests (`POST /create-container-in-db` then
`POST /create-container-in-k8s`, both in `src/api_handlers.py`), called sequentially by the
browser. This repo's create-container request to Cloud (`CloudClient.create_container`) does not
send `device_id` at all - Local has no established way to learn "its own" device_id (that's a
`browseterm-desktop` concept, a separate process with no IPC channel to this one); Cloud resolves
the caller's currently-active device automatically instead (see `browseterm-server`'s README's P13
section). If `create_container_in_k8s`'s real ContainerMaker/K8s call fails, the handler now
best-effort releases the DB row and its Cloud-side resource reservation (via the same
`ContainerService.delete_container_in_db` path the real delete flow uses) before returning the
error - previously that row and reservation leaked forever, and the container's name would
permanently block any retry under the same name. See `~/browseterm/p.md`'s P13 section.

**No file in this repo imports `browseterm_db.common.config`, `DB_CONFIG`, or any
`POSTGRES_*`/`REDIS_*` setting for an active connection** - verified by grep as part of this
migration (a couple of harmless leftovers remain: `src/common/config.py` still *declares*
`DB_CONFIG`/`POSTGRES_*`/`REDIS_*` as inert, never-imported values, matching the same pattern
already established for Cloud's local-only settings; `browseterm_db` itself stays a dependency
purely for shared enum/type imports like `SaveStatus`/`ContainerStatus`/`AuthProvider`, never a
DB client). Do not add a DB/Redis client import anywhere in this repo; if a future task seems to
need one, that's a sign the corresponding Cloud API is missing, not that this boundary should be
broken.

**P07 (done - see `~/browseterm/p07.md` and `p.md`'s "P07" section): Cloud is now the sole OAuth
authority.** This repo no longer performs Google/GitHub token exchange at all -
`src/authentication/oauth_service.py` and its GOOGLE/GITHUB client id/secret config are gone
entirely. The flow is now:

1. `GET /auth/{provider}` (`src/api_handlers.py:auth_provider_redirect`) redirects the browser to
   Cloud's `/auth/{provider}/start?target=local` - Cloud does the whole OAuth dance itself.
2. Cloud's callback redirects the browser back to this repo's `GET /auth/callback?code=<handoff>`
   (`src/api_handlers.py:auth_callback`), which redeems that one-time code against Cloud's public
   `POST /auth/handoff/redeem` (`CloudClient.redeem_handoff`) to pick up the session Cloud already
   created, and sets the HttpOnly `session` cookie (+ a non-HttpOnly `csrf_token` cookie, double-
   submit CSRF pattern - see `src/api_handlers.py:_csrf_ok`).
3. `POST /device/bootstrap` (`src/api_handlers.py:device_bootstrap`, session-cookie + CSRF
   protected) lets `browseterm-desktop` trade the already-authenticated WebView session for a
   one-time device-bootstrap code, which it redeems directly against Cloud's public
   `POST /auth/device-bootstrap/redeem` for its own long-lived per-device Bearer credential -
   Desktop never uses the browser session cookie as an ongoing credential (P06's interim
   `BROWSETERM_SESSION_COOKIE` mechanism is gone).

`POST /auth/sessions`/`/auth/sessions/validate`/`/auth/sessions/delete` still exist on Cloud and
are still internal-token-gated (unchanged) - `validate`/`delete` are still called from here
(`authenticate_session`, `logout`); `create` is called only by Cloud itself now (from its own
OAuth callback), never by this repo.

**Follow-up (2026-08-31): session refresh + a real logout CSRF bug.** The original plan's P07
scope also included "session refresh" as its own item, separate from "correct logout" - missed
in the first pass, which only had the incidental extend-on-any-authenticated-call side effect
`authenticate_session` already provides (insufficient for a long-lived page like the terminal
page, where the user may go the whole 30-minute session window without triggering another
authenticated HTTP call). Added `POST /auth/refresh` (`api_handlers.auth_refresh`, plain JSON
200/401, deliberately not `@authenticate_session`-decorated so a `fetch()` gets a clean signal
instead of silently following a 302 to `/login`) plus `templates/static/js/base.js`'s
`SessionRefreshManager`, which polls it every 10 minutes on any post-login page and redirects to
`/login` itself on a 401. While adding tests for this, found that `LogoutManager.handleLogout()`
never actually sent the `X-CSRF-Token` header the P07 CSRF check requires - clicking Logout in
the real UI 403'd on `/logout` (session never revoked) but still redirected to `/login`
regardless, so it silently *looked* like it worked. Fixed alongside a new `BaseUtilities.
getCookie()` helper. See `~/browseterm/p.md`'s P07 section for the full writeup and
`tests/integration/authentication/test_api_handlers_auth.py` for the handler-level test coverage
this gap revealed was missing (the first P07 pass only tested `AuthenticationService`'s own
methods, never `api_handlers.py`'s routes - CSRF check included - at the handler level).

## P19 - cross-device resume

`POST /resume-container` (`src/api_handlers.py:resume_container`) previously flipped a HIBERNATED
container to RESUMING itself via `update_container_fields` before recreating its pod. As of P19,
that transition moved to Cloud: after this repo's own entitlement gates (concurrent-container
limit, spec-still-fits-current-plan - both unchanged, still fail-open on a subscription-lookup
error), the handler calls the new `CloudClient.resume_container(container_id, user_id)` ->
Cloud's `POST /containers/{id}/resume`. `device_id` is deliberately omitted - Cloud auto-resolves
the caller's currently-ACTIVE device, the same pattern `create_container` already uses (P13) -
this repo has no established way to know a device_id of its own either. Cloud validates the
container is actually HIBERNATED, reserves the resolved device's capacity, and atomically sets
`device_id`/`status=RESUMING`, all before this repo attempts any pod-start (see
`browseterm-server`'s README's P19 section for the Cloud-side CAS/reservation detail). A non-2xx
`CloudClientError` here (409 - not hibernated / lost a concurrent resume race, 400 - resolved
device lacks capacity) means nothing was reserved on Cloud's side, so it's surfaced to the caller
verbatim and k8s is never touched.

If the pod-start step (`ContainerService.create_container_in_k8s`) fails **after** Cloud's resume
transition already succeeded, the handler rolls that back by calling
`CloudClient.hibernate_container(container_id)` - reusing the existing P18 internal hibernate
endpoint as-is (it already clears `device_id`, releases the reservation, and sets HIBERNATED)
rather than building a dedicated "cancel resume" endpoint or just marking the row FAILED, which
would leave the device's reservation dangling forever. This rollback only fires once Cloud's
transition is confirmed to have succeeded (tracked via a local `resumed` flag) - a pod-start
failure that happens because Cloud rejected the resume in the first place still falls back to the
old `status=FAILED` marking, since there is nothing on Cloud's side to unwind. See
`~/browseterm/p.md`'s P19 section for the full design writeup, including the live end-to-end
verification performed against Cloud's resume/hibernate endpoints directly (create device +
container -> hibernate -> resume with an explicit `device_id` -> confirmed `status`/`device_id`/
`used_cpu` transitioned correctly -> a second resume attempt on the same container correctly
409'd with no double-reservation -> a simulated rollback via hibernate confirmed `device_id`
cleared and `used_cpu` returned to 0).

## `src/cloud_client/` - the Local -> Cloud boundary

```
Local Handler -> CloudClient -> HTTPS -> Cloud browseterm-server (browseterm.cloud.com)
```

The only intended way this repo's code reaches central Cloud state. Wraps handoff redemption
(public, possession-gated) and the session/device-bootstrap-start/container/catalog/subscription
API this repo's own handlers use (internal-service-token auth - see "Trust boundary" above) - do
not add unrelated Cloud endpoints here without a corresponding Cloud API existing first. The
Device Cloud API itself (register/list/get/update/heartbeat) is **not** called from here as of
P07 - it's Bearer-device-token authenticated now, and only `browseterm-desktop` calls it, through
its own separate `CloudClient` (see that repo's README).

`CLOUD_INTERNAL_API_TOKEN` must match the same env var on Cloud; unset (empty string default),
`CloudClient` sends no `X-Internal-Service-Token` header at all, so the server-to-server routes
correctly reject it as unauthorized rather than silently proceeding.

Default `BROWSETERM_CLOUD_API_URL` is `http://browseterm.cloud.com:9999` (Cloud's DNS
convention, mirroring `browseterm.local.com` for Local); override for local development against
a Cloud instance on this machine.

## Cloning the repository
```
git clone --recurse-submodules https://github.com/Zim95/browseterm-server-local.git
```
If already cloned:
```
git submodule update --init --recursive
```

## Dev Setup - Kubernetes
NOTE: This setup is a little different on windows. Please use WSL in windows.
    Basically, the script files wont work on windows and therefore, you need to manually setup.
    The developer of this repository hates working with windows.

Current convention is two `k3d` clusters (Docker-based, not Docker Desktop's built-in Kubernetes
or a Multipass VM): `browseterm-k3s` for Cloud, `browseterm-k3s-local` for this repo (+
container-maker/socket-ssh/payment-gateway/workloads) - see `~/browseterm/p.md`'s P06 addendum
for the naming decision. `docker-desktop`'s own Kubernetes still works if you genuinely prefer
it (the manifests don't care which provider `kubectl config current-context` points at), but if
you use `k3d`, **disable k3s's bundled Traefik first** - its `svclb` squats on host ports 80/443
and ingress-nginx's own `svclb` will sit `Pending` forever otherwise:
`kubectl -n kube-system delete helmchart traefik` (already-running cluster) or
`--k3s-arg '--disable=traefik@server:*'` at `k3d cluster create` time. Build images locally and
`k3d image import <image> -c <cluster-name>` instead of pushing to a registry.

1. To Develop inside kubernetes, install Docker (Desktop or just the Docker engine) and either
   `k3d` (`brew install k3d`) or follow Docker Desktop's own Kubernetes guideline:
   `https://docs.docker.com/desktop/features/kubernetes/`.

2. Once `kubectl` is setup and you have your cluster ready (`k3d cluster create browseterm-k3s-local ...`
   or the `docker-desktop` context), we can proceed further.

3. Clone this repository. Follow the guide.

4. First of all, make sure `./infra/development/entrypoint-development.sh` is an executable.
    ```
    chmod +x ./infra/development/entrypoint-development.sh
    ```

4. Create an `env.mk` file with the following variables:
    ```
    REPO_NAME=zim95
    USER_NAME=zim95
    NAMESPACE=browseterm
    HOST_DIR=/Users/namahshrestha/test/browseterm/browseterm-server-local

    # CONTAINER MAKER CONFIG
    CONTAINER_MAKER_DEVELOPMENT_HOST=container-maker-development-service
    CONTAINER_MAKER_DEVELOPMENT_PORT=50052
    CONTAINER_MAKER_HOST=container-maker-service
    CONTAINER_MAKER_PORT=50052
    CONTAINER_MAKER_CERTS_SECRET_NAME=container-maker-development-service-certs

    # CLOUD (P07 - see ~/browseterm/p07.md: Cloud is the sole OAuth authority, Local holds no
    # Google/GitHub credential and no Redis client at all any more)
    BROWSETERM_CLOUD_API_URL=http://browseterm.cloud.com:9999

    # POSTGRES CREDENTIALS
    POSTGRES_HOST=browseterm-pg-service
    POSTGRES_PORT=5432
    POSTGRES_USER=namah
    POSTGRES_PASSWORD=test123
    POSTGRES_DB=browseterm

    # OTHER ENVs
    CERT_MANAGER_CRON_JOB_NAME=cert-manager
    SOCKET_SSH_WSS_URL=ws://socketssh.local:8000
    INGRESS_HOST=browseterm.local.com
    SOCKET_SSH_HOST=socket-ssh.local.com

    # P13 - if browseterm-k3s (Cloud) and browseterm-k3s-local (this repo) are two separate k3d
    # clusters on the SAME Mac (this project's actual dev convention - see above), a pod here
    # resolving BROWSETERM_CLOUD_API_URL's hostname does NOT see the host-only /etc/hosts entry
    # pointing it at 127.0.0.1 - it needs a hostAliases override pointing at the host machine
    # instead (see infra/deployment/deployment.yaml's P13 comment for the full why). Determine the
    # IP with: `kubectl --context k3d-browseterm-k3s-local -n <namespace> run tmp --rm -it
    # --image=busybox --restart=Never -- nslookup host.docker.internal` (or exec into any already-
    # running pod in that cluster and run the same). Not needed at all on a single-cluster setup
    # (docker-desktop context, or Cloud and Local sharing one cluster).
    CLOUD_INGRESS_HOST=browseterm.cloud.com
    CLOUD_INGRESS_HOST_IP=192.168.65.254
    ```

5. Create the `browseterm-internal-api-token` Secret in this cluster - not created by either
    setup script (same pattern as `browseterm-db-credentials`). Must hold the exact same value as
    Cloud's own `CLOUD_INTERNAL_API_TOKEN` (see `browseterm-server/infra/cloud/cloud.yaml`) - every
    internal-token-gated call this repo makes to Cloud (session validate/delete, container CRUD,
    catalog, sse-tokens) 401s otherwise:
    ```
    kubectl create secret generic browseterm-internal-api-token \
      --from-literal=CLOUD_INTERNAL_API_TOKEN=<same value as Cloud's> \
      -n <your-namespace>
    ```

6. Run the development build script, if not already done.
    ```
    make dev_build
    ```
    This will build the docker image required for k8s development.

7. Run the development setup script.
    ```
    make dev_setup
    ```
    This will setup the development environment.

7. Get inside the pod:
    First check the pod status:
    ```
    kubectl get pods -n <your-namespace>  --watch
    ```
    You should see the pod being created and then it will be running.
    ```
    NAME                                            READY   STATUS    RESTARTS   AGE
    browseterm-server-development-f8cd46fd4-cl4ht   1/1     Running   0          9s
    ```
    Once the pod is running, get inside the pod:
    ```
    kubectl exec -it browseterm-server-development-f8cd46fd4-cl4ht -n <your-namespace> -- bash
    ```
    Now you are inside the pod.

8. Now we test if your local working directory is mounted to the pod.
    In your text editor outside the pod (in your local machine - working directory), create a new file and save it as `test.txt`. Check if that file is present in the pod.
    ```
    ls
    ```
    You should see the `test.txt` file.
    This means that your local working directory is mounted to the pod. You can make changes in your working directory and they will be reflected in the pod.
    You are free to develop the code and test the workings.

9. Now, we need to activate teh virtual env once we are inside the container.
    ```
    source $(poetry env info --path)/bin/activate
    ```

10. Install all dependencies with poetry.
    ```
    poetry install
    ```

11. Once done you can run the teardown script.
    ```
    make dev_teardown
    ```

NOTE: To run anything inside the shell, activate the virtualenv. But to run anything as a container command, we need to use `poetry run`.


# Working with dependencies
1. Adding dependencies:
    ```
    poetry add dependency
    ```

2. Adding dependencies with specific versions:
    - Add the dependency with version in the `pyproject.toml` file.
    - Then run `poetry update`.

3. Removing a dependency
    - `poetry remove <package>`


# Running tests
This repo has three test areas: frontend (JavaScript), backend integration (Python,
`tests/integration/`), and backend unit tests for the new P06 modules (Python, `tests/unit/`).

## Frontend tests (Jest)
These are self-contained unit tests for the static JS. They run under the jsdom environment with the browser boundaries mocked (`fetch`, `EventSource`), so no live services are needed.
```
npm install
npx jest tests/frontend
```
Or use the configured script (equivalent to `jest`):
```
npm test
```

## Backend integration tests (unittest)
These are integration tests under `tests/integration/` (e.g. `containers/`, `authentication/`). They are NOT standalone unit tests: they require live dependencies (Postgres, Redis, and in some cases the running services / cluster) to be up before running them.
```
poetry install
poetry run python -m unittest discover -s tests/integration -p "test_*.py"
```

## New backend unit tests (P06: `cloud_client`)
Standalone - the HTTP boundary is mocked (`httpx`), no live Postgres/Redis/Cloud instance needed:
```
poetry install
poetry run python -m pytest tests/unit/ -v
```
