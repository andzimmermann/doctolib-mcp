# ChangePoint.eu deployment preparation

Deployment destination is **ChangePoint.eu only**. No resource may be created in the
MySupply account (`833dd808aa2112d0b1618215253f1795`). The ChangePoint account is now connected: `e663440b575a5919d90eac9d7ce69f58`.
The gateway setup is deployed there; no browser backend is configured yet.

The GitHub fork `andzimmermann/doctolib-mcp` is still absent. The connected GitHub app
cannot create forks (HTTP 403); the owner must create the fork before uploading the commits.

## Components

1. `cloudflare/worker.mjs`: authenticated MCP gateway in the ChangePoint account.
2. `doctolib_mcp.http_server`: Python/browser service on the user's chosen persistent host.
3. `doctolib_mcp.cli watch`: scheduler on the same host/profile/state as the backend.
4. An interactive browser on that host for Doctolib login and possible 2FA.

A Worker alone does not run the persistent Python/browser service. A GitHub Actions job
is a build/test runner and is not the persistent scheduler. Hosting for the browser backend
is not selected yet. A local computer must stay on and needs a secure reachable connection
for Worker access; a ChangePoint server needs HTTPS and a way for the owner to complete
browser login. Do not expose a browser desktop or debugging port publicly.

## Credentials

Run on the intended private host/check-out:

```sh
python3 scripts/create-access-secrets.py
```

The helper creates `.secrets/mcp-token`, `.secrets/backend-token` and
`.secrets/mcp-token-sha256`. They are ignored by Git and owner-readable only. It prints no
credential values and does not overwrite existing tokens. Never upload these files as
source/CI artifacts or paste their contents into chat.

- The user puts the MCP token into the MCP client's Bearer authentication setting.
- Set Worker secret `MCP_TOKEN_SHA256` from the SHA-256 file. The Worker stores a verifier,
  not the user's raw MCP token.
- Set Worker secret `BACKEND_TOKEN` from the independent backend token.
- On the browser host set `DOCTOLIB_BACKEND_TOKEN` to the same backend token using the
  service environment/secret store. The backend independently validates it before the SDK.
- The Doctolib login/password/2FA are entered **only in the normal browser login**, not in
  Worker Secrets or MCP tool arguments. They are not the MCP or backend token.

Bearer access is a personal single-account connection: anyone holding the MCP token can
invoke that user's exposed tools. Keep it private and rotate if compromised. Do not turn
this into a shared multi-user service; per-user sessions/storage/authorization would be
required first.

## Backend

Install the pinned Python requirements and Playwright/Chromium as described in README.
Complete CLI `login --country de` interactively as the same OS user and with the same
check-out/profile that the services will use. Test login status before configuring jobs.

Run the HTTP module with these environment settings:

- `DOCTOLIB_BACKEND_TOKEN`: secret described above.
- `DOCTOLIB_BACKEND_HOSTS`: exact hostname presented by the backend HTTPS proxy; no wildcards.
- `DOCTOLIB_AUTO_LOGIN=0` (also enforced by HTTP entry point and watcher).

```sh
PYTHONPATH=src .venv/bin/python -m doctolib_mcp.http_server
PYTHONPATH=src .venv/bin/python -m doctolib_mcp.cli watch
```

The HTTP process listens only on `127.0.0.1:8765`. An HTTPS reverse proxy must expose only
`/mcp` from the selected host, strip any request logging of headers/bodies, reject other
paths and preserve the exact configured Host. No login UI or arbitrary browsing endpoint
is exposed by this application. Keep `.session/` and `.state/` persistent/private.
The HTTP endpoint authenticates all calls, bounds bodies, rejects browser Origin requests,
and removes credentials before handing a request to the SDK. It uses stateless HTTP MCP
transport; monitor state and browser credentials remain persistent in their existing stores.

Example service-manager units in `deploy/` assume an administrator-prepared `doctolib`
user and installation at `/srv/doctolib-mcp`. They have not been installed or started.
`/etc/doctolib/backend.env` must be private and contain the backend token and allowed host.
Adapt installation paths/user before using them; do not run from a temporary chat sandbox.

## Worker

Only after confirming the ChangePoint account, fill:

- `CLOUDFLARE_ACCOUNT_ID` for that account (explicit for every deployment/secret command).
- `PUBLIC_ORIGIN`: exact canonical HTTPS Worker/custom origin.
- `BACKEND_URL`: fixed HTTPS URL ending in `/mcp`, without queries or embedded credentials.
- `BACKEND_HOST`: exact hostname of that URL.
- `DEPLOYMENT_OWNER` remains `changepoint.eu`.

Never default to an account from an unrelated Wrangler login. The source config explicitly selects the ChangePoint account and public gateway origin.
Backend routing remains blank; it is not ready for a functional Doctolib connection.
Configure the actual target in a private deployment configuration before running Wrangler.
Review existing DNS before creating a custom hostname. Do not assume a hostname is free.

The gateway replaces the client bearer token with the independent backend token. It does
not forward client cookies or arbitrary headers, never follows upstream redirects, bounds
requests to 512 KiB and never retries a failed/uncertain request. The backend alone executes
the 15 MCP tools. No patient data is intentionally logged; Worker invocation logs are off,
query strings are redacted, and sampled traces contain only infrastructure metadata.

## Verification

```sh
npm ci
npm run types:worker
npm run build:worker
npm run test:worker
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Local checks do not establish deployment or live Doctolib compatibility. After setup verify:
missing/wrong MCP token denied, independent backend authentication, MCP initialize/tool list,
interactive Doctolib login and exact linked-patient identities. Only then create a specifically
authorized practice/person/date/time monitor. No live appointment has been booked or cancelled
and no monitor is active during this preparation. Never book a real appointment merely to
check connectivity.

## Deployment evidence — 2026-11-08

- Account: `changepoint.eu account`, `e663440b575a5919d90eac9d7ce69f58`.
- Created account workers.dev subdomain `changepoint-eu`.
- Worker: `doctolib-mcp-changepoint`.
- Public URL: `https://doctolib-mcp-changepoint.changepoint-eu.workers.dev/mcp`.
- Initial deployment: `837c301088504a29934eccddb17f0259`. Secret updates create subsequent versions.
- Source module SHA-256: `8db79d014bf809612869f5889d6b59cdbff4478412ff9d73968ab5a7b17fc388`.
- Confirmed encrypted secret binding names `MCP_TOKEN_SHA256` and `BACKEND_TOKEN`; values not printed.
- workers.dev enabled; preview URLs disabled.
- Initial public checks failed during TLS negotiation of the newly created subdomain.
  This was a transport failure, not an application health response. Public readiness is
  not yet verified. Do not disable certificate verification.
- Backend URL/host intentionally blank. No Doctolib session or job is active.
- GitHub fork retry still failed with HTTP 403 from the connected integration.
