"""
Configuration for the Local -> Cloud API boundary only.

Deliberately does not import anything from `src.common.config` (which declares
POSTGRES_*/REDIS_* for this repo's legacy direct-DB paths, see p.md's P06 section) -
code reachable from here must never gain a path to central DB/Redis credentials.
"""
import os

# No production Cloud hostname/ingress exists yet anywhere in the Browseterm repos (P22 owns
# final Cloud infra). This default is Cloud's own container port (infra/cloud/cloud.yaml),
# suitable for local development against a Cloud instance running on this machine; override via
# env var for any other environment.
BROWSETERM_CLOUD_API_URL: str = os.getenv("BROWSETERM_CLOUD_API_URL", "http://localhost:9999")

# The exact cookie name Local's existing session flow sets/reads
# (src/authentication/authentication_helpers.py: request.cookies.get('session')).
SESSION_COOKIE_NAME: str = "session"
