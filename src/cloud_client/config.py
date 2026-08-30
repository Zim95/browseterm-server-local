"""
Configuration for the Local -> Cloud API boundary only.

Deliberately does not import anything from `src.common.config` (which declares
POSTGRES_*/REDIS_* - no longer used by any active Local code as of this task, since Local holds
no DB/Redis client at all: every read/write of central state goes through Cloud's HTTP API via
CloudClient) - code reachable from here must never gain a path to central DB/Redis credentials.
"""
import os

# Cloud's public hostname convention, mirroring the existing browseterm.local.com convention for
# Local (browseterm-monorepo/env.mk.example). No Cloud ingress/DNS has actually been stood up yet
# anywhere in these repos (that's real infra work, not done by this change) - override via env
# var for local development (e.g. http://localhost:9999 against a Cloud instance on this
# machine).
BROWSETERM_CLOUD_API_URL: str = os.getenv("BROWSETERM_CLOUD_API_URL", "http://browseterm.cloud.com:9999")

# The exact cookie name Local's existing session flow sets/reads
# (src/authentication/authentication_helpers.py: request.cookies.get('session')).
SESSION_COOKIE_NAME: str = "session"

# Interim shared secret proving to Cloud that a call genuinely comes from Local's own backend
# (not an arbitrary internet client) for the server-to-server routes (session issuance/
# validation, container/subscription writes) where Local is trusted to already know the real
# user_id. Must match Cloud's CLOUD_INTERNAL_API_TOKEN. Not a substitute for the real "Cloud is
# the OAuth client" redesign (plan section 7.1 / P07) - see src/cloud_client/client.py's
# docstring.
CLOUD_INTERNAL_API_TOKEN: str = os.getenv("CLOUD_INTERNAL_API_TOKEN", "")
