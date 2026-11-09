# ChangePoint single-Worker setup

One Worker calls Doctolib's internal APIs directly. Its temporary Browser Run binding is
used only for interactive login from an iPhone or desktop. It closes after validated
session import; MCP requests and appointment Cron checks remain direct API operations.
No external Python server, extra Worker, or permanent browser service is required.

- Account: changepoint.eu, `e663440b575a5919d90eac9d7ce69f58`.
- Worker: `doctolib-mcp-changepoint`.
- Endpoint: `https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/mcp`.
- Same-Worker exported state class: `DoctolibAccount`, SQLite migration `direct-v1`.
- Same-Worker Cron trigger: `*/5 * * * *`.
- General writes: disabled during setup; no live monitor configured.
- MySupply resources were not modified for this project.

## Authentication

The client supplies its own private MCP Bearer token. Its SHA-256 verifier is the Worker
Secret `MCP_TOKEN_SHA256`. The token was generated locally into ignored `.secrets/mcp-token`
with owner-only permissions and never printed. `SESSION_KEY` is an independent AES-GCM key
stored as a Worker Secret. It encrypts imported/rotated Doctolib cookies in the same Worker's
state. No session credential is exposed as an MCP tool argument or result.

The primary login entry point is `/connect`. Unlock it with the existing personal MCP
token; a signed, Secure/HttpOnly/SameSite=Strict setup cookie remembers the device for up
to 30 days. The cookie is bound to the current MCP credential and cannot authorize MCP
requests. Browser actions require exact same-origin POSTs and bounded JSON bodies.

The `BROWSER` binding starts a twenty-minute maximum handoff using `@cloudflare/puppeteer`
1.4.0 and a Doctolib/CDN guardrail policy. The mobile connection page displays the Doctolib login image and provides a native
keyboard input. Text is relayed transiently over authenticated same-origin HTTPS into
the selected Doctolib input, without SQL/log storage or MCP exposure. Password inputs
are masked and cleared after submission. Click/key actions stay on authentication pages.
Cloudflare Live View remains an alternative for desktop users.
No browser recording is enabled. The setup page receives a short-lived Live View link,
which must be treated as a secret and never logged. It reuses the current browser when
renewing that link. Completion filters cookies to the exact Doctolib domain/path,
validates the account API, encrypts them in the existing state object and closes the
browser. Cancellation, idle timeout and server expiry limit its lifetime. Pending handoff
state and a separate login-operation lease survive restarts. Incomplete authentication
never overwrites the existing session or reports success.

`POST /setup/session` remains a bounded MCP-token-protected import for the optional
desktop helper, not a requirement for iPhone users. This is not a Doctolib OAuth endpoint.

The real authenticated APIs may reject replayed cookies or expire them. Anonymous search
success is not proof that the personal-account booking flow works from this runtime. Do not
add stealth, fake browser identities, challenge bypasses or blind retries in response.

## Evidence

- Earlier public TLS provisioning issues resolved. `/health` reaches the application.
- Direct Doctolib search **from this Worker**: HTTP 206, expected healthcareProviders schema,
  no account session, no authorization header or client cookies forwarded, no retry.
- MCP initialize: HTTP 200, server changepoint-doctolib 0.3.0, protocol 2025-11-25.
- MCP tools/list: HTTP 200, all 15 tools present.
- MCP list_appointment_monitors: empty list.
- MCP session_status: DOCTOLIB_SESSION_REQUIRED, accurately reflecting missing login.
- Missing client Bearer token: HTTP 401.
- Initial direct deployment: `f73ea7156b024e1a9eb50e9b995f87ce`; subsequent tested connector
  deployment is recorded in IMPLEMENTATION.md.
- Cron was configured and confirmed by Cloudflare. No job means no medical API work.

The personal fork is https://github.com/andzimmermann/doctolib-mcp. The owner installed
the connector for the personal account (installation 179896653), after which the feature
branch feat/personal-appointment-monitor was successfully uploaded. Earlier HTTP 403
permission failures are resolved. GitHub-hosted workflows validate this branch without
Cloudflare credentials or any live Doctolib session.


## Final acceptance

Connect a real session, check session_status/list_patients, verify the chosen person and
practice, then enable writes only for the user's specific real booking/monitor instruction.
No appointment should be booked merely to prove connectivity. Unknown outcomes must be
checked in the account, never blindly repeated. Session expiry pauses account operations.

Earlier direct-only deployment: `734fc00e1b52424bbabbbb02c0b9afb0`.
Bundle SHA-256: `c3745a149b414206c960b980ac1dd24afff4fade010e309ab76570c2597ef422`.
The unused legacy BACKEND_TOKEN remains stored because automatic approval review
rejected deletion without explicit authorization for that exact credential. Current
code does not reference it; no workaround was used to delete it.


## Mobile login deployment

Deployment `17c88640a7c34788ab2ad1c1a2047c37` adds the mobile `/connect` flow and the same
Worker's Browser Run binding. Bundle SHA-256:
`f3246850c2555c9a681a57ac0eae6e8936c335ebfddef89c68f16945127b53d0`.
All 27 local tests pass; the 390-pixel touch viewport flow was exercised with simulated
login/import responses and no horizontal overflow. Live unlock, browser availability,
Doctolib page navigation and generation of a signed Live View link succeeded. Temporary
test browsers are closed afterward. An actual patient login/import still requires the
owner to complete password/2FA; no real booking or cancellation was performed.


## Native iPhone keyboard acceptance

The hosted Live View did not reliably open the iPhone keyboard. The same Worker now
provides private `/connect/frame` and `/connect/interact` endpoints with exact handoff
IDs, same-origin protection, bounded validated actions and the existing interaction lease.
No arbitrary URLs/scripts or keys are accepted. Screenshots and input text are transient.
Thirty-three tests passed. A 390-pixel touch browser check verified native focus, password
masking, clearing and form completion with fixtures. A fresh isolated live browser then
rendered Doctolib's login flow, selected its username field and accepted reserved test text
through the relay with HTTP 200. The test did not submit authentication or medical actions
and the browser was closed. The owner must still complete their actual password/2FA.
