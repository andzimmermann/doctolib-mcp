# doctolib-mcp

I injured myself the other day, and now we have a Doctolib MCP server.

MCP tools over Doctolib's internal JSON API. Search practitioners, read their visit
motives, and find the earliest bookable slot across an entire specialty in one call. With
a logged-in session it also reads your own appointment calendar, and behind an explicit
confirmation step it can book and cancel.

Works against `doctolib.de`, `doctolib.fr` and `doctolib.it` through the `country`
argument. The specialty slug list is German only, but free-text keywords work everywhere.

## Why this exists

Doctolib's official API is partner-only, restricted to medical software vendors and
hospital information systems. There is no self-serve tier to sign up for. The only thing
on offer elsewhere was an Apify scraper actor, which returns directory data with no
availability and no booking, at roughly $30 per 1000 records.

What does exist is the JSON that Doctolib's own patient frontend talks to, and it turns
out to be enough for the whole job.

The tool worth having is `find_earliest_appointment`. The website makes you open
practitioners one at a time, which is precisely the part that stops working when what you
want is the first free appointment instead of one particular doctor. This searches, fans
out over every candidate's availability in parallel, and returns them sorted by soonest
slot.

## Tools

| Tool | What it does | Writes? |
|---|---|---|
| `find_earliest_appointment` | search plus parallel availability fan-out, sorted by soonest slot | no |
| `search_doctors` | practitioners only, no availability check | no |
| `get_practitioner` | every visit motive, agenda and place for one profile | no |
| `get_availabilities` | open slots for one motive/agenda/practice triple | no |
| `list_specialties` | common German specialty slugs | no |
| `booking_url` | the URL a human opens to book in a browser | no |
| `my_appointments` | your own confirmed, pending and past appointments | no, but authenticated |
| `session_status` | whether the stored login is still live | no |
| `abort_booking_draft` | release a lingering temporary slot hold | yes, but harmless |
| `book_appointment` | **books a real appointment.** See [docs/safety.md](docs/safety.md) | **yes, binding** |
| `cancel_appointment` | **cancels a real appointment.** See [docs/safety.md](docs/safety.md) | **yes, binding** |

Search results already carry `visitMotiveId`, `agendaIds` and `practiceId`, so a search
chains straight into availabilities without a per-practitioner round trip. The full
reverse-engineered endpoint contract lives in [docs/endpoints.md](docs/endpoints.md).

## Install

```bash
cd ~/dev/doctolib-mcp
uv venv
uv pip install "mcp>=2.0.0" "httpx>=0.27" "typer>=0.12"
```

The project sets `package = false` under `[tool.uv]`, so it is never installed into the
venv. Everything runs with `PYTHONPATH=src`, which is why the MCP registration below sets
that environment variable. There is no installed entry point to fall back on.

For the authenticated tools, add Playwright:

```bash
uv pip install "playwright>=1.44"
./.venv/Scripts/python.exe -m playwright install chromium
```

Registered in `~/.claude.json` as:

```json
"doctolib": {
  "command": "/absolute/path/to/doctolib-mcp/.venv/Scripts/python.exe",
  "args": ["-m", "doctolib_mcp.mcp_server"],
  "env": { "PYTHONPATH": "/absolute/path/to/doctolib-mcp/src" }
}
```

pi registers the same server lazily, with a `directTools` list that deliberately omits
`book_appointment` and `cancel_appointment` so the two binding writes stay behind the
proxy instead of sitting one token away in the default tool list.

## Logging in

Both binding writes and `my_appointments` need a real logged-in session. Chrome wraps its
own cookie store in App-Bound Encryption (`v20` / `APPB` keys), so there is no reliable way
to lift a session cookie out of it offline, and the session cookie is `httpOnly` so
JavaScript cannot read it either. Rather than fight that, this server owns a separate
browser profile: a persistent Playwright user-data-dir under `.session/<country>/` that you
log into once.

```bash
PYTHONPATH=src ./.venv/Scripts/python.exe -m doctolib_mcp.cli login
```

A real browser window opens. Sign in, complete any 2FA, and the command returns once the
session goes live. The profile survives restarts, so this is a one-time step until Doctolib
actually expires the session. `session_status` reports when that has happened.

### You do not have to run that by hand

When an authenticated tool finds no live session, it opens the login window itself and
retries the call once the login exits successfully. So `my_appointments` against an expired
session shows you a browser, waits while you sign in, and then answers, instead of returning
an error that tells you to go and run a command.

Three details govern how that behaves:

It runs the login as a **subprocess** and treats **exit code 0** as the completion signal,
which `doctolib login` gives only after it has probed the session and found it live. That
also sidesteps a hard constraint. Chromium takes an exclusive lock on the profile directory,
so a login cannot start while another context is open on it, and every caller closes its own
context before the login is spawned.

It is **opt-out** through `DOCTOLIB_AUTO_LOGIN=0`. A blocking browser window is right at a
terminal and wrong in an unattended worker, so anything running without a human in front of
it should set that and get the immediate failure instead.

It **backs off and serialises**. A dismissed window is not reopened for 90 seconds, and
concurrent callers produce one window between them rather than one each.

Public reads deliberately do not trigger any of this. They work anonymously, so a plain
search never pops a browser at you. Only the tools that genuinely cannot proceed without
authentication will.

If your MCP client's own request timeout is shorter than the login takes, the tool call will
fail while the window is still open. Finish signing in anyway. The session persists, and the
next call picks it up.

`.session/` is gitignored and holds a live login. It must never be committed, copied into a
scratch directory, or handed to another agent.

### The public reads use the session too

Once you are logged in, the read tools stop querying anonymously and go through the session.
Without it they miss whatever the backend personalises for a known patient: existing-patient
("Bestandspatient") visit motives, slots that some practices only show to logged-in known
patients, the correct insurance sector, and personalised ranking. Every read result carries
`"authenticated": true` or `false` so it is always clear which view produced the answer.

With no live session the read tools fall back to the anonymous public view silently. They
remain fully functional, just not personalised.

Mechanically the reads take the fast path. The session cookie is read out of the login
profile, cached in-process for five minutes so a fan-out like `find_earliest_appointment`
costs one browser launch instead of twenty, and replayed over httpx. Only the write path
insists on running inside the real browser context.

## Writes

`book_appointment` commits a real clinical slot at a real practice under a real name. If it
succeeds, a clinic has put you in their calendar and a human being expects you to physically
show up. `cancel_appointment` is the inverse and releases that slot irreversibly.

Three things guard them, and it is worth being precise about which is which:

1. **`confirm=False` is the default**, and on that path `book_appointment` performs no
   network call at all. It returns the payload that would be sent so an agent can read the
   appointment back to you in plain language and get an explicit yes. `cancel_appointment`
   with `confirm=False` performs one read, fetching the valid cancellation reasons, and
   issues no `DELETE`.
2. **A live login session is required.** There is no cookie parameter on the booking client,
   because the funnel is session-bound and needs real CSRF tokens and draft state rather
   than a replayed cookie. Without a session the call fails before it can commit anything.
3. **Neither tool is in pi's `directTools`**, so on that box they stay behind the proxy.

One thing that does **not** protect you: `DoctolibBookingClient` takes no cookie parameter
and reads no `DOCTOLIB_COOKIE`, so leaving that environment variable unset guards nothing.
The confirmation step and the login requirement are the whole list.
[docs/safety.md](docs/safety.md) records where that particular misconception came from.

The booking funnel is four stateful steps sharing a server-side draft, and it has never been
executed end to end, because that cannot be tested without making a real booking. Cancelling
has been executed once, on 05.08.2026, as an explicitly authorised real cancellation. Both
contracts, the failure modes, and the rules an agent should follow are written up in
[docs/safety.md](docs/safety.md).

## CLI

Every tool is reachable without an MCP client, so a misbehaving tool reproduces in one shell
command:

```bash
PYTHONPATH=src ./.venv/Scripts/python.exe -m doctolib_mcp.cli earliest handchirurg --city Berlin --within-days 21
```

Available commands: `earliest`, `search`, `practitioner`, `slots`, `login`, `status`,
`appointments`, `abort-draft`, `cancel`. The `cancel` command is a preview unless you pass
`--confirm`.

## Tests

```bash
uv pip install "pytest>=8.0"
PYTHONPATH=src ./.venv/Scripts/python.exe -m pytest tests -q
```

111 tests, about six seconds, no network. The suite runs entirely offline because a
`conftest.py` fixture blocks real socket connections,
blocks Playwright from launching, and repoints the session directory at a temporary path, so
a test that reaches for the network or for the real login fails loudly instead of quietly
succeeding. For a repository that can cancel real medical appointments, a test run that can
touch the live account is itself the hazard.

What the tests are there to catch, in descending order of what it costs when they go red:

- The confirmation step never writes. A regression that makes `confirm=False` commit
  something is the worst failure this codebase has available to it.
- `_pick_patient` chooses the right person. On an account with family members, the wrong
  branch books a clinical slot for the wrong human.
- `_build_confirm_payload` invents nothing. Every field submitted to a medical intake form
  has to have come from the server's own prefill.
- The booking funnel calls its four steps in the documented order, at the documented paths.
  This is as close to end-to-end as is reachable without making a real booking, which makes
  it the most useful thing the suite does.
- Failures after step one tear the draft down, so a broken run never leaves a slot held.
- Answers that would be silently wrong rather than loudly broken: the Nominatim bounding-box
  transposition, the multi-chunk availability walk, and the `speciality` field that arrives
  as a string in one payload and a dict in another.

`session.login()` and `session.status()` are deliberately untested. They drive a visible
interactive browser, and mocking Playwright deeply enough to cover them would only test the
mock. The commit hop itself is also untested, for the obvious reason.

## Etiquette

Requests are throttled per host with jitter, and the availability fan-out is capped at four
workers. Keep it that way. This is a personal-use tool hitting a healthcare provider's
undocumented endpoints, and the polite failure mode is being slow rather than being blocked.
