# Personal Doctolib MCP — one Cloudflare Worker

A direct API MCP in the **ChangePoint.eu Cloudflare account**. One deployed Worker handles
MCP requests and its own five-minute Cron schedule. An exported SQLite Durable Object in
that same Worker stores private jobs, serializes the personal account and records booking
outcomes. No separate backend server or permanently running browser service is used. A temporary
Cloudflare Browser Run session is used only when the owner renews their login.

Endpoint: `https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/mcp`

The API paths and booking funnel were adapted from
[esinecan/doctolib-mcp](https://github.com/esinecan/doctolib-mcp), upstream `3ee910d`.
The Python implementation remains a tested reference. The production runtime is in
`cloudflare/`: `worker.mjs`, `direct-mcp.mjs`, `direct-api.mjs`, `direct-state.mjs`.

## What works and what is still required

A live unauthenticated search sent **from the ChangePoint Worker to Doctolib** returned
HTTP 206 with the expected search schema. Live MCP initialize, all 15 tools, empty job
storage and rejection of unauthenticated clients were checked. Personal account APIs,
booking and cancellation still require a real, valid Doctolib session and live acceptance.
No real medical appointment has been booked or cancelled and no live job is configured.
General writes remain disabled during account setup.

Doctolib's internal web APIs use session cookies and CSRF state, not a publicly documented
personal API key or OAuth connector. The Worker uses a valid account session directly.
It does not invent an OAuth flow, send your password to a custom form, impersonate a
browser or bypass access challenges. If access is denied or the session expires, it stops.
Direct anonymous search success does not establish authenticated booking compatibility.

## MCP tools

- `list_specialties`, `search_doctors`, `get_practitioner`, `get_availabilities`,
  `find_earliest_appointment`, `booking_url`.
- `my_appointments`, `session_status`, `list_patients`.
- `book_appointment`, `cancel_appointment`, `abort_booking_draft`.
- `create_appointment_monitor`, `list_appointment_monitors`, `stop_appointment_monitor`.

The MCP is protected by a personal Bearer token. The same token grants all exposed tools
for this one personal Doctolib account; it is not a shared multi-user service. Session
cookies are not accepted as model-facing tool arguments or returned to the model.

## Connect your Doctolib account

Open [the connection page](https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/connect)
on your iPhone or computer. No shell, Python installation or local helper is required:

1. Unlock the connection page with your personal MCP token. A Secure, HttpOnly,
   SameSite=Strict setup cookie remembers that browser for up to 30 days. Rotating the
   MCP credential invalidates it; it never grants MCP/account-read access.
2. Prepare the login window and select **Doctolib öffnen**. This opens a temporary
   Cloudflare Live View in another tab. Sign into Doctolib there, including any 2FA.
3. Return to the connection page and select **Sitzung übernehmen**. The Worker verifies
   the imported account session, encrypts cookies and closes the temporary browser.
   An incomplete or rejected login never reports success. You can cancel the window.

The same Worker's `BROWSER` binding starts a guarded session limited to Doctolib domains,
including the login assets on `assets.doctolib.fr`, and shared CDNs. No browser session recording is requested. Neither passwords nor cookies
are sent through ChatGPT tools. The temporary browser idles out after three minutes;
the server rejects and cleans up the handoff after twenty minutes. Live View links are
short-lived credentials and must not be published or logged. Reopening the handoff renews
the link to the same browser. Pending handoffs are restored after a mobile tab reload;
status exposes only safe stages/error codes. Cookie import uses the installed SDK's
URL-scoped `Page.cookies()` API. Authenticated MCP errors return the public `reconnect_url`
and explain this flow instead of referring users to ChatGPT plugin settings.

This is session renewal, not a Doctolib OAuth/refresh-token grant. Doctolib can still expire
or revoke the session; no unlimited login lifetime is claimed. The existing
`scripts/connect-doctolib.py` remains an optional desktop fallback, not a prerequisite.
A private `DOCTOLIB_COOKIE` Worker Secret also supports owner-managed imports.

## Create an exact standing instruction

Use `list_patients` to choose the account holder, spouse, child or other linked person by
stable `patient_id`. Availability/search and booking inherit that person's insurance
when `patient_id` is supplied. If Doctolib has no recognized insurance status, provide
an explicit user-confirmed override; another family member's status is never reused.
The same filter is forwarded for each availability window and scheduled check.
Use `get_practitioner` to choose the exact doctor/practice, calendar,
location and visit motive. The monitor binds those IDs to an inclusive date range (maximum
90 days), local start-time window, timezone, weekdays and new/existing-patient status.

`auto_book=false` finds and stops. `auto_book=true` is standing user authorization to book
a matching slot for **that exact person and target** without another prompt. It requires
server writes to be enabled after account setup. The same Worker checks due jobs every
five minutes, stops after successful booking and pauses on expired login, changed target,
errors or an uncertain booking. Empty appointment schedules make no appointment searches.

With `SESSION_KEEP_ALIVE=true`, the same Cron additionally makes one authenticated, read-only
session check per 24 hours, even without appointment monitors. Rotated cookies are encrypted
and saved. Attempt times survive restarts; network errors wait until the next daily attempt.
Authentication or access-denial errors pause this background check until a successful session
import. `session_status` reports its timestamps and pause state. This can help with inactivity
expiry only if Doctolib renews the session; it cannot extend a fixed expiry or replace a new
login after revocation. No notification channel or guaranteed session lifetime is provided.

`booking` or `needs_review` statuses require checking the account before any replacement
instruction. Pending or unknown writes survive runtime restart and are never automatically
repeated. A duplicate target/person job cannot dispatch a second booking. Stopping monitoring
never retracts an in-flight booking or cancels an appointment. Manual booking/cancellation
preview by default; `confirm=true` requires the user's specific instruction.

The broad earliest-appointment tool only checks a bounded first page, not every doctor.
Practice-specific monitoring uses the chosen calendar directly. No notifications channel
is configured; results are stored and available through MCP.

## Verify and deploy

```sh
npm ci --ignore-scripts
npm run types:worker
npm run test:worker
PYTHONPATH=src .venv/bin/python -m pytest -q
```

The Worker suite executes in workerd with actual MCP transport/SQLite and simulated
Doctolib responses. It verifies exact family-member selection, uncertainty after commit,
persistence across restart, monitor stop-after-booking, authentication, session import,
no credential leakage, mobile connection authorization, handoff lifecycle and denial handling. Python reference tests block real network I/O.
GitHub-hosted test workflows are included; no self-hosted runner is required.
The personal fork is https://github.com/andzimmermann/doctolib-mcp.

Deploy only to account `e663440b575a5919d90eac9d7ce69f58` (ChangePoint), using the explicit
config in `cloudflare/wrangler.jsonc`. Worker Secrets: `MCP_TOKEN_SHA256` (client token
verifier), `SESSION_KEY` (session encryption key). `ENABLE_WRITES=false` is the setup default.
The direct API probe is owner-authenticated at `POST /setup/check-api`. No medical data,
credentials, body or browser challenge is returned by that probe.

See [ChangePoint setup and evidence](docs/CHANGEPOINT-SETUP.md). Secret/state/profile files
are ignored and excluded from source archives. The upstream supplied no license; attribution
and history are preserved. This fork does not invent a license for unrelated distribution.
