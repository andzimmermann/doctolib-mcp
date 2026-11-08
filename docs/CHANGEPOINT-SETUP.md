# ChangePoint single-Worker setup

Accepted target: one Worker calling Doctolib's internal APIs directly. The previous external
Python HTTP backend and the later Browser Run proposal are superseded. They are not required
components of the deployed architecture. Browser services and external servers were not created.

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

A validated login import is `POST /setup/session`, authenticated with the MCP token and
limited to the canonical origin. Only a bounded `cookie` field is accepted. The Worker
checks the account API before saving the session encrypted. This is not an OAuth endpoint.
The helper in `scripts/connect-doctolib.py` opens a transient normal browser, lets the owner
sign into Doctolib including possible 2FA, and transfers the resulting Doctolib cookies to
the fixed Worker endpoint. No password is sent to the Worker. No permanent local service
is started. An optional private `DOCTOLIB_COOKIE` secret supports owner-managed imports.

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

Final tested deployment: `734fc00e1b52424bbabbbb02c0b9afb0`.
Bundle SHA-256: `c3745a149b414206c960b980ac1dd24afff4fade010e309ab76570c2597ef422`.
The unused legacy BACKEND_TOKEN remains stored because automatic approval review
rejected deletion without explicit authorization for that exact credential. Current
code does not reference it; no workaround was used to delete it.
