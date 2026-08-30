"""
Configuration for the Local -> Cloud API boundary only.

Deliberately does not import anything from `src.common.config` (which declares
POSTGRES_*/REDIS_* for this repo's legacy direct-DB paths, see p.md's P06 section) -
code reachable from here must never gain a path to central DB/Redis credentials.
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
