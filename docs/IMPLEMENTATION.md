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
