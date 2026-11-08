> Publication update: the owner has created the personal fork at
> https://github.com/andzimmermann/doctolib-mcp. Push permission is now confirmed.
> Earlier HTTP 403 fork-creation entries below are historical.

# Current deployment — single direct-API Worker

Updated 2026-11-08. The external Python backend and Browser Run proposals below are
historical and superseded. Current endpoint:
`https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/mcp`.

- ChangePoint account: `e663440b575a5919d90eac9d7ce69f58`.
- Final deployment: `734fc00e1b52424bbabbbb02c0b9afb0`; migration `direct-v1`.
- Bundle SHA-256: `c3745a149b414206c960b980ac1dd24afff4fade010e309ab76570c2597ef422`.
- One Worker, 15 MCP tools, same-Worker Cron every five minutes and SQLite state class.
- Direct anonymous Doctolib API search from Worker: HTTP 206, schema recognized.
- Live initialize and tools/list: HTTP 200; 15 tools; wrong/missing bearer denied.
- Live state: no monitor jobs. Account session required; general writes disabled.
- Session import endpoint rejects invalid payloads; actual personal login remains pending.
- 14 workerd tests and 137 Python reference tests passed. Browser login helper compiles.
- Runtime tests cover exact linked-person selection, checkout identity checks, preflight
  failures, uncertain commit, stop-after-success, duplicate suppression and restart,
  credential isolation and validated encrypted session import. They use mocked Doctolib.
- No real patient records read or medical bookings/cancellations made. No active job.
- `MCP_TOKEN_SHA256` and `SESSION_KEY` are provisioned. An attempted cleanup of the unused
  legacy `BACKEND_TOKEN` was rejected by automatic approval review for lack of explicit
  authorization to delete that exact secret. It remains stored, is not used by the current
  code, and was not removed through another route. No approval is needed to keep it unused.
- GitHub fork creation remains HTTP 403 for the connected integration; no PR or CI run
  is claimed. Source/history are packaged locally, without secrets, session or state.

## Historical implementation evidence

# Implementation evidence — 2026-11-08

Upstream: esinecan/doctolib-mcp at 3ee910d. All original tool capabilities retained.
Added exact linked-patient selection and persistent practice-specific monitoring, with
explicit standing auto-book authorization and no automatic repeat of an uncertain write.

Validation completed locally:

- 137 offline tests passed, including original parsing/booking/login tests and regression
  checks for cookie scoping, full/partial availability failures, account-only search,
  confirmed versus uncertain commit outcomes, exact patient selection, practice checks,
  time/date constraints, duplicate jobs, concurrent schedulers and process restart.
- A real stdio MCP client initialized the server and listed all 15 tools successfully.
- No cookie input argument appears in any MCP tool schema.
- No live Doctolib login, medical record read, booking, cancellation or active monitor.
- No state/profile files are tracked; source archives are generated only from Git.

GitHub publication remains blocked: POST /repos/esinecan/doctolib-mcp/forks returned
HTTP 403 Resource not accessible by integration. The connected identity is
andzimmermann. No fork or PR was created, no CI run has executed remotely, and the
upstream repository was not modified. The included GitHub-hosted workflow requires no
self-hosted runner, but it is for tests, not continuous appointment monitoring.

Live acceptance remains necessary with the user's own login and a specifically authorized
appointment instruction. The upstream internal API has no stability contract. Honest
assistant identification may be rejected; do not bypass anti-automation challenges.

A booking dispatched by an active auto_book instruction can reserve a real appointment.
Stopping a monitor cannot reverse it; booking and cancellation are separate actions.

## ChangePoint deployment preparation

Prepared a Bearer-authenticated Worker gateway and independently authenticated Python
HTTP backend, plus private credential generation and example service units. The MCP
client token and backend token were generated into ignored, owner-only `.secrets/` files.
No credential values were printed or committed. Worker config deliberately has no
account ID and blank endpoint settings until the ChangePoint account/backend are selected.

142 Python tests passed, including actual HTTP MCP initialize and all 15 tools, with
no live Doctolib calls. Seven workerd gateway tests passed: authentication, header/cookie
isolation, setup ownership, Origin rejection, body limits, no redirects and no write retry.
Wrangler dry-run build passed; this is not a deployment.

Cloudflare account enumeration returned only `mysupply.ai account`
(`833dd808aa2112d0b1618215253f1795`). It was not used for any Doctolib mutation.
No ChangePoint account, persistent browser host or remote endpoint is confirmed yet.
No remote services, login or monitor have been started. The GitHub fork remains absent.

## ChangePoint retry — 2026-11-08

The connection now lists ChangePoint account e663440b575a5919d90eac9d7ce69f58.
A new workers.dev subdomain and the tested gateway module were deployed there.
Two secrets were provisioned without displaying their values; binding names verified.
Initial external TLS handshake failed; application readiness is not yet established.
Backend hosting and GitHub fork creation remain unresolved. No MySupply mutation,
Doctolib login, monitor activation, booking or cancellation took place.
