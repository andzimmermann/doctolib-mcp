# Personal Doctolib MCP

Personal appointment search, family-member selection, booking, cancellation and persistent
monitoring for a **specific doctor or practice**. Derived from
[esinecan/doctolib-mcp](https://github.com/esinecan/doctolib-mcp), upstream commit
`3ee910d`. This is an unofficial integration with Doctolib's internal web endpoints.

## What changed

- All original capabilities remain: search, profiles, availability, appointment list,
  browser login status, booking, cancellation and draft cleanup.
- `list_patients` returns stable patient UUIDs so each instruction selects the intended
  account holder, spouse, child or other linked person. Monitor jobs never infer a person
  from a partial name or fall back to the account holder.
- Persistent monitoring is scoped to an exact profile, practice, calendar, visit motive,
  patient, date range, local start-time window, weekdays and new/existing-patient status.
  Automatic booking requires explicit `auto_book=true` in that standing instruction.
- Monitoring checks at most once per five minutes per job, stops after success, and pauses
  on expired login, changed criteria, failures or uncertain booking outcomes. It never
  blindly retries a booking, including after a process restart.
- Account search fails closed if login expires. Cookies are scoped to the Doctolib host,
  never sent to the external Nominatim geocoder and never accepted as MCP tool arguments.
- Failed availability checks are reported as failures, not as an absence of appointments.
  Partial searches are marked. Dictionary-shaped slots sort by their start timestamp.
- A successful booking is not reported as failed merely because the subsequent
  confirmation fetch fails. An interrupted commit is explicitly unknown.
- Honest assistant identification; no browser impersonation or stealth flags. An access
  challenge stops the request. These changes can cause an internal endpoint to reject
  automated use; no live compatibility is claimed.

## Remote access via ChangePoint.eu

The authenticated Worker gateway and Python HTTP backend are prepared in this branch.
See [ChangePoint setup](docs/CHANGEPOINT-SETUP.md) for the separate MCP/backend tokens,
persistent browser hosting, and exact deployment gates. No remote deployment is claimed.

## Run locally

Python 3.12+ and a local browser-capable machine are required. A GitHub Actions runner
runs tests; it is **not** the always-on appointment monitor. A plain Cloudflare Worker
cannot run this persistent local browser profile.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install 'playwright>=1.44'
.venv/bin/python -m playwright install chromium
PYTHONPATH=src .venv/bin/python -m doctolib_mcp.cli login --country de
PYTHONPATH=src .venv/bin/python -m doctolib_mcp.mcp_server
```

The MCP uses stdio. Configure your MCP client with the absolute venv Python executable,
arguments `-m doctolib_mcp.mcp_server`, `PYTHONPATH` set to this checkout's `src` directory,
and `DOCTOLIB_AUTO_LOGIN=0` for unattended use. Complete login/2FA in the browser yourself.
No password, cookie or token belongs in a chat or repository.

## A standing automatic-booking instruction

1. Use `list_patients` and select the exact person's `patient_id` (UUID).
2. Use `get_practitioner` to inspect the chosen practice's profile, available visit motives,
   calendars and location IDs. Choose a calendar belonging to that exact practice/motive.
3. Use `list_appointment_monitors` to avoid creating duplicate jobs.
4. Create one `create_appointment_monitor` with the exact IDs and desired dates, weekdays,
   timezone, local start-time window, insurance sector and `new_patient` status.
   `auto_book=false` finds and stops; `auto_book=true` is the user's standing authorization
   to immediately book the first matching slot for this exact person and target.
5. Keep the scheduler running on the same machine/check-out as the MCP and browser profile:

```sh
PYTHONPATH=src DOCTOLIB_AUTO_LOGIN=0 .venv/bin/python -m doctolib_mcp.cli watch
```

Use a service manager for unattended operation/restarts. The process must be running and
have network access; creating a job in chat alone does not start background monitoring.
Jobs persist in `.state/monitors.sqlite3`. `active` means eligible for checking, not proof
that a watcher is running. The scheduler reads due jobs every 30 seconds and honors each
job's interval (minimum 300 seconds). It is deliberately modest polling, not a guarantee
of winning a slot against other patients.

`list_appointment_monitors` reports `found`, `booked`, `expired`, `needs_review` or
`booking`. A `booking` job surviving a crash and any `needs_review` result require checking
the Doctolib account before creating another booking instruction. No notification channel
is configured; the watcher prints job ID/status and the MCP lists stored results.

`stop_appointment_monitor` stops future checks, not an already dispatched booking or an
existing appointment. Existing appointments are never automatically cancelled/replaced.
Manual booking and cancellation retain their `confirm=false` preview; use `confirm=true`
only for the user's explicit specific instruction. The draft-release tool remains.

## Tests and limits

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Tests use simulated HTTP/browser responses and block real connections. They cover patient
selection, date/time limits, persistent deduplication and restart behavior, uncertain
writes, cookie scope, login failure and search errors. A GitHub-hosted test workflow is
included; no self-hosted runner is needed.

No live account was connected and no appointment was booked or cancelled during development.
End-to-end live booking remains unverified. The internal API may change or block automated
access. Search is not exhaustive: the broad earliest-appointment tool examines a bounded
first page; practice-specific monitoring uses the selected calendar directly.

The upstream repository supplies **no license**. Its attribution/history is retained;
this fork does not invent a license or claim general redistribution rights. Check licensing
before distributing a separate package or offering it as a service.
