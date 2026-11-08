# Personal Doctolib MCP — one Cloudflare Worker

A direct API MCP in the **ChangePoint.eu Cloudflare account**. One deployed Worker handles
MCP requests and its own five-minute Cron schedule. An exported SQLite Durable Object in
that same Worker stores private jobs, serializes the personal account and records booking
outcomes. No separate backend server or permanently running browser service is used.

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

Use the normal Doctolib website login with password and possible 2FA. The optional helper
opens a **temporary visible browser for that login only**, validates the session, transfers
only Doctolib cookies over HTTPS to the authenticated ChangePoint Worker and removes its
local temporary profile. The Worker validates the session again and encrypts it at rest.
There is no permanently running local process or external hosting requirement.

On your own machine, in this source checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/connect-doctolib.py
```

Enter the MCP token in the hidden terminal prompt; enter your Doctolib password/2FA only
in Doctolib's own browser page. No passwords or tokens belong in chat. The helper does
not follow upload redirects or print cookies/tokens. Session expiration requires a fresh
login; no refresh-token capability is claimed. A private `DOCTOLIB_COOKIE` Worker Secret
is also supported for owner-managed imports, but the validated connector is preferred.

## Create an exact standing instruction

Use `list_patients` to choose the account holder, spouse, child or other linked person by
stable `patient_id`. Use `get_practitioner` to choose the exact doctor/practice, calendar,
location and visit motive. The monitor binds those IDs to an inclusive date range (maximum
90 days), local start-time window, timezone, weekdays and new/existing-patient status.

`auto_book=false` finds and stops. `auto_book=true` is standing user authorization to book
a matching slot for **that exact person and target** without another prompt. It requires
server writes to be enabled after account setup. The same Worker checks due jobs every
five minutes, stops after successful booking and pauses on expired login, changed target,
errors or an uncertain booking. Empty schedules make no Doctolib calls.

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
no credential leakage and denial handling. Python reference tests block real network I/O.
GitHub-hosted test workflows are included; no self-hosted runner is required. The fork is
still blocked by the connected GitHub app's HTTP 403 on fork creation.

Deploy only to account `e663440b575a5919d90eac9d7ce69f58` (ChangePoint), using the explicit
config in `cloudflare/wrangler.jsonc`. Worker Secrets: `MCP_TOKEN_SHA256` (client token
verifier), `SESSION_KEY` (session encryption key). `ENABLE_WRITES=false` is the setup default.
The direct API probe is owner-authenticated at `POST /setup/check-api`. No medical data,
credentials, body or browser challenge is returned by that probe.

See [ChangePoint setup and evidence](docs/CHANGEPOINT-SETUP.md). Secret/state/profile files
are ignored and excluded from source archives. The upstream supplied no license; attribution
and history are preserved. This fork does not invent a license for unrelated distribution.
