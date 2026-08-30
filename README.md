# browseterm-server-local

**Local control plane.** Serves the existing Browseterm browser UI, talks to local
ContainerMaker/Socket-SSH/Kubernetes, and (new in P06) ships the Mac Desktop Resource MVP. Must
never hold Cloud PostgreSQL/Redis credentials for central state - see "Trust boundary" below for
what that actually means today vs. the target end-state.

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
  plus two genuinely new P06 additions: `src/cloud_client/` and `desktop/`.

## Trust boundary

The target architecture (plan section 2.2): Local never holds Cloud PostgreSQL/Redis
credentials; all central-state access happens through authenticated Cloud HTTPS APIs.

**Today, this repo is not fully there yet - and that's intentional, documented debt, not an
oversight.** Everything this repo inherited from the old combined `app.py` (OAuth login,
container/workspace CRUD, SSE, payments) still talks to Postgres/Redis directly, because their
Cloud-API replacements are explicitly later tasks:

| Legacy direct-DB path | Removed by |
|---|---|
| `src/authentication/session_manager.py`/`authentication_helpers.py` (session issuance/validation), transitively `db_ops/user_db_ops.py`/`subscription_db_ops.py` | P07 (Cloud OAuth/session migration) |
| `src/status_listener.py` (Postgres LISTEN/NOTIFY -> SSE) | P10 (Cloud SSE) |
| `src/api_handlers.py`, `src/db_ops/container_db_ops.py`, `src/db_ops/image_db_ops.py` (container/workspace CRUD) | P12/P13 (Cloud workspace metadata/create APIs, Local create path) |

**The one new P06 code path is exempt from this by construction**: `src/cloud_client/` and
`desktop/` never import `browseterm_db`, `src.common.config.DB_CONFIG`, or any
`POSTGRES_*`/`REDIS_*` setting - they talk to Cloud exclusively over HTTPS through
`src.cloud_client.CloudClient`. Do not add a DB/Redis import to either package; if a future task
needs one, that's a sign it belongs back in the legacy tree above, not here.

## `src/cloud_client/` - the Local -> Cloud boundary

```
Local Handler -> CloudClient -> HTTPS -> Cloud browseterm-server
```

The only intended way Local/Desktop code reaches central Cloud state. Currently wraps just the
P05 Device Cloud API (register/list/get/update/heartbeat) - do not add unrelated Cloud endpoints
here without a corresponding Cloud API existing first.

Auth is interim (pre-P07): `CloudClient` takes the same opaque `session` cookie value the
browser already holds after logging in through this repo's existing OAuth flow (Cloud and Local
share one Redis pre-P07). See `src/cloud_client/client.py`'s module docstring for the full
rationale and what P07 replaces it with.

## `desktop/` - Desktop Resource MVP (P06)

Mac-only menu-bar app - a machine/runtime/resource manager, **not** the Browseterm workspace UI
(no workspace list/create/terminal UI; that stays in the browser). Built with
[`rumps`](https://github.com/jaredks/rumps) - the smallest available Mac menu-bar app
microframework, chosen over any cross-platform packaging framework (Electron/Tauri/etc) since
this repo's stack is already all-Python/poetry and Windows/Linux desktop support is explicitly
out of scope (plan section 20).

Responsibilities: detect macOS/architecture/CPU/memory/storage (`desktop/hardware.py`, stable
stdlib/`sysctl`/`shutil` calls only - no GUI-output parsing), edit the Browseterm allocation with
client-side validation as defense-in-depth (`desktop/allocation.py` - P05's server-side
validation is authoritative), register/update the device and send heartbeats through
`CloudClient` (`desktop/device_registration.py` - handles P05's real, non-idempotent 409
duplicate semantics by finding and updating the existing device instead), report (not manage)
local server/k3s health (`desktop/runtime_health.py`), and open Browseterm in the system browser.

Run it:
```
poetry install
export BROWSETERM_SESSION_COOKIE=<value of the "session" cookie after logging in via the browser>
poetry run python -m desktop.app
```

`BROWSETERM_LOCAL_URL` (default `http://browseterm.local.com:9999`, matching this repo's own
`AUTH_REDIRECT_BASE_URI`/`INGRESS_HOST` convention) and `BROWSETERM_CLOUD_API_URL` (default
`http://localhost:9999` - no production Cloud hostname exists yet anywhere in these repos, P22
owns that) are both overridable env vars.

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

1. To Develop inside kubernetes, you need to first install Docker Desktop and follow this guideline: `https://docs.docker.com/desktop/features/kubernetes/`.

2. Once `kubectl` is setup and you have the `docker-desktop` cluster ready. We can proceed further.

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

    # GOOGLE CREDENTIALS
    AUTH_REDIRECT_BASE_URI=http://localhost:9999
    GOOGLE_CLIENT_ID=<your-google-client-id>
    GOOGLE_CLIENT_SECRET=<your-google-client-secret>
    GITHUB_CLIENT_ID=<your-github-client-id>
    GITHUB_CLIENT_SECRET=<your-github-client-secret>

    # REDIS CREDENTIALS
    REDIS_HOST=browseterm-redis-service
    REDIS_PORT=6379	
    REDIS_PASSWORD=test123
    REDIS_USERNAME=namah
    REDIS_DB=0

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
    ```

5. Run the development build script, if not already done.
    ```
    make dev_build
    ```
    This will build the docker image required for k8s development.

6. Run the development setup script.
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
These are integration tests under `tests/integration/` (e.g. `containers/`, `authentication/`, `status_listener/`). They are NOT standalone unit tests: they require live dependencies (Postgres, Redis, and in some cases the running services / cluster) to be up before running them.
```
poetry install
poetry run python -m unittest discover -s tests/integration -p "test_*.py"
```

## New backend unit tests (P06: `cloud_client`, `desktop`)
Standalone - every OS/network boundary is mocked (`sysctl`/`shutil`/`subprocess`/`httpx`), no
live Postgres/Redis/Cloud instance needed:
```
poetry install
poetry run python -m pytest tests/unit/ -v
```
