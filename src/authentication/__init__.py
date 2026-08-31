'''
P07 (see ~/browseterm/p07.md) moved OAuth authority to Cloud entirely. Local no longer holds
GOOGLE_CLIENT_ID/SECRET, GITHUB_CLIENT_ID/SECRET, provider URLs, or a Redis client - see
src/common/config.py and src/cloud_client/config.py for what Local's auth config actually is now.

Current flow:

1. User clicks "Sign in with Google/GitHub" -> browser GETs Local's `/auth/{provider}`
   (src/api_handlers.py:auth_provider_redirect), which 302s to
   `{BROWSETERM_CLOUD_API_URL}/auth/{provider}/start?target=local`.
2. Cloud (browseterm-server/src/cloud/oauth_handlers.py) generates OAuth state, redirects the
   browser to Google/GitHub.
3. User logs in and authorizes. The provider redirects to Cloud's own
   `/auth/{provider}/callback` - never to Local, never to Desktop.
4. Cloud validates state, exchanges the code (Cloud-only secrets), fetches the provider profile,
   creates/updates the user + session (existing process_user_info), mints a one-time handoff
   code, and redirects the browser to Local's `/auth/callback?code=<handoff>`.
5. Local's `/auth/callback` (src/api_handlers.py:auth_callback) redeems that code against Cloud's
   `POST /auth/handoff/redeem` (src/cloud_client/client.py:CloudClient.redeem_handoff) - this
   just hands back the session Cloud already created in step 4, it does not create a second one.
6. Local sets the HttpOnly `session` cookie (+ a non-HttpOnly CSRF cookie) on its own response
   and redirects the browser to `/`.

The Desktop WebView loads this exact same flow (browseterm-desktop just loads Local's real URL -
no separate OAuth implementation, p07.md section 19). Desktop's own device credential (separate
from this browser session) is bootstrapped afterward via `/device/bootstrap` - see
src/api_handlers.py:device_bootstrap and browseterm-desktop's desktop/keychain.py.
'''
