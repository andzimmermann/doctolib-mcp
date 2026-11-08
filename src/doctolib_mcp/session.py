"""Dedicated user-controlled browser login for the personal appointment assistant.

No credentials are accepted through MCP tool arguments. Interactive login uses a normal
visible browser without stealth flags. Search requires a live account session. Unattended
monitoring disables auto-login and pauses on missing/expired sessions or access denials.
The profile contains sensitive credentials and must never be committed or shared.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .client import COUNTRY_HOSTS, DoctolibError

SESSION_ROOT = Path(__file__).resolve().parent.parent.parent / ".session"

# How we know login finished: the account appointments API answers 200 with a data
# envelope only for an authenticated session (it 401/redirects otherwise).
_AUTH_PROBE = "/account/appointments.json"


class NotLoggedIn(DoctolibError):
    pass


def _profile_dir(country: str) -> Path:
    _base(country)
    d = SESSION_ROOT / country
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return sync_playwright
    except ImportError as e:
        raise DoctolibError(
            "Playwright is not installed. `pip install playwright && playwright install "
            "chromium` in the doctolib-mcp venv, or install the 'auth' extra."
        ) from e


def _base(country: str) -> str:
    if country not in COUNTRY_HOSTS:
        raise DoctolibError(f"unsupported country {country!r}")
    return COUNTRY_HOSTS[country]


def _is_authed(context, base: str) -> bool:
    """True if the persistent context currently holds a live session."""
    try:
        r = context.request.get(base + _AUTH_PROBE, headers={"Accept": "application/json"})
    except Exception:
        return False
    if not r.ok:
        return False
    try:
        return "data" in r.json()
    except Exception:
        return False


def login(country: str = "de", timeout_s: int = 300) -> dict:
    """Open a visible browser, let the user log in, and persist the session.

    Blocks until the session goes live or `timeout_s` elapses. Meant to be run from the
    CLI (`doctolib login`) — it deliberately shows a real window.
    """
    sync_playwright = _require_playwright()
    base = _base(country)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir(country)),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base + "/account/appointments", wait_until="domcontentloaded")

        import time
        deadline = time.monotonic() + timeout_s
        authed = False
        while time.monotonic() < deadline:
            if _is_authed(ctx, base):
                authed = True
                break
            time.sleep(2)
        ctx.close()

    if not authed:
        raise NotLoggedIn(
            f"login not detected within {timeout_s}s. Re-run `doctolib login` and complete "
            "sign-in (including any 2FA) in the window."
        )
    return {"logged_in": True, "country": country, "profile": str(_profile_dir(country))}


def status(country: str = "de") -> dict:
    """Report whether the stored session is currently live, without prompting."""
    if not (SESSION_ROOT / country).exists():
        return {"logged_in": False, "reason": "no session profile. Run `doctolib login`"}
    sync_playwright = _require_playwright()
    base = _base(country)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir(country)), headless=True,
        )
        try:
            live = _is_authed(ctx, base)
        finally:
            ctx.close()
    return {"logged_in": live, "country": country,
            "reason": None if live else "session expired. Run `doctolib login`"}


def cookie_header(country: str = "de") -> str:
    """Export the current session as a `name=value; ...` Cookie header for httpx replay.

    Raises NotLoggedIn if the stored profile has no live session.
    """
    sync_playwright = _require_playwright()
    base = _base(country)
    host = base.split("://", 1)[1]
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir(country)), headless=True,
        )
        try:
            if not _is_authed(ctx, base):
                raise NotLoggedIn("no live session. Run `doctolib login`")
            cookies = ctx.cookies(base)
        finally:
            ctx.close()
    pairs = [f"{c['name']}={c['value']}" for c in cookies
             if c.get("domain", "").lstrip(".") in (host, host.removeprefix("www."))]
    if not pairs:
        raise NotLoggedIn("session profile holds no doctolib cookies. Run `doctolib login`")
    return "; ".join(pairs)


# Small in-process cache so the read tools don't launch a browser on every call. The MCP
# server is a long-lived process, so this is warm across tool calls within a session.
_cookie_cache: dict[str, tuple[Optional[str], float]] = {}
_COOKIE_TTL = 300.0  # seconds


def try_cookie(country: str = "de", ttl: float = _COOKIE_TTL) -> Optional[str]:
    """Best-effort session cookie for authenticating the public read endpoints.

    Returns the cookie header string, or None if there is no live login. Never raises and
    never prompts — the read tools use this to *prefer* the authenticated view and fall
    back to anonymous silently. Cached for `ttl` seconds (including negative results), so a
    logged-in user pays at most one browser launch per window and a logged-out user pays
    nothing after the first miss.
    """
    import time
    now = time.monotonic()
    hit = _cookie_cache.get(country)
    if hit and now - hit[1] < ttl:
        return hit[0]
    if not (SESSION_ROOT / country).exists():
        _cookie_cache[country] = (None, now)
        return None
    try:
        val: Optional[str] = cookie_header(country)
    except Exception:
        val = None
    _cookie_cache[country] = (val, now)
    return val


# --------------------------------------------------------------------- auto-login
#
# When an authenticated call finds no live session, run the interactive login instead of
# telling the user to go and do it by hand. Three things shape how this works.
#
# It runs as a SUBPROCESS, not in-process. Chromium takes an exclusive lock on the profile
# directory, so a login cannot start while another persistent context is open on the same
# dir. Every caller here therefore closes its context (the NotLoggedIn escapes through a
# finally/teardown) before the login is spawned. Running it out-of-process also avoids
# re-entering sync_playwright, which is not reentrant, and gives us the exit code as the
# completion signal: `doctolib login` exits 0 only once the session is actually live.
#
# It is OPT-OUT via DOCTOLIB_AUTO_LOGIN=0. A visible browser window that blocks for minutes
# waiting for a human is the right behaviour at a terminal and the wrong behaviour in an
# unattended worker (cron, a sentinel, a dispatch drone). Unattended callers set that to 0
# and get the old NotLoggedIn immediately.
#
# It BACKS OFF. If a login attempt fails, because the window was dismissed or 2FA was not
# completed, we do not reopen it on the next call. One prompt per cooldown window.

# It SERIALISES. Two authenticated calls that both find no session would otherwise open two
# windows, and the second would fail anyway because Chromium holds an exclusive lock on the
# profile dir. The lock also lets a caller that arrives just after a successful login reuse
# it instead of prompting the user a second time.

_AUTO_LOGIN_TIMEOUT = 120.0
_AUTO_LOGIN_COOLDOWN = 90.0
_auto_login_lock = threading.Lock()
_auto_login_last_failure: dict[str, float] = {}
_auto_login_last_success: dict[str, float] = {}


def auto_login_enabled() -> bool:
    """False when DOCTOLIB_AUTO_LOGIN is set to 0/false/no. Defaults to enabled."""
    return os.environ.get("DOCTOLIB_AUTO_LOGIN", "1").strip().lower() not in ("0", "false", "no")


def try_auto_login(country: str = "de", timeout_s: float = _AUTO_LOGIN_TIMEOUT) -> bool:
    """Spawn the interactive login and report whether it produced a live session.

    Returns True only on exit code 0, which `doctolib login` gives only after the session
    probe succeeds. Never raises: a False return means the caller should surface its own
    NotLoggedIn, so a failed auto-login degrades to the previous behaviour.

    MUST be called with no persistent browser context open on this country's profile.
    """
    if not auto_login_enabled():
        return False
    with _auto_login_lock:
        return _try_auto_login_locked(country, timeout_s)


def _try_auto_login_locked(country: str, timeout_s: float) -> bool:
    now = time.monotonic()
    won = _auto_login_last_success.get(country)
    if won is not None and now - won < _AUTO_LOGIN_COOLDOWN:
        return True   # another caller just logged in; reuse it instead of prompting again
    last = _auto_login_last_failure.get(country)
    if last is not None and now - last < _AUTO_LOGIN_COOLDOWN:
        return False

    src_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
    # The child must not try to auto-login again; it *is* the login.
    env["DOCTOLIB_AUTO_LOGIN"] = "0"

    # CREATE_NO_WINDOW keeps the helper's console from flashing. The browser it launches is
    # a separate process and stays visible, which is the whole point.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "doctolib_mcp.cli", "login",
             "--country", country, "--timeout", str(int(timeout_s))],
            env=env, capture_output=True, text=True,
            timeout=timeout_s + 30, creationflags=flags,
        )
        ok = proc.returncode == 0
    except Exception:
        ok = False

    if ok:
        _auto_login_last_failure.pop(country, None)
        _auto_login_last_success[country] = time.monotonic()
        _cookie_cache.pop(country, None)  # the old negative cache would mask the new session
    else:
        _auto_login_last_success.pop(country, None)
        _auto_login_last_failure[country] = time.monotonic()
    return ok


def authed_request(
    method: str,
    path: str,
    country: str = "de",
    json_body: Optional[dict] = None,
) -> Any:
    """Run one authenticated API call, opening a login window first if there is no session.

    On NotLoggedIn this spawns the interactive login once and retries. Set
    DOCTOLIB_AUTO_LOGIN=0 to keep the old behaviour of failing immediately, which is what an
    unattended caller wants. See `try_auto_login`.
    """
    try:
        return _authed_request_once(method, path, country, json_body)
    except NotLoggedIn:
        if not try_auto_login(country):
            raise
        return _authed_request_once(method, path, country, json_body)


def _authed_request_once(
    method: str,
    path: str,
    country: str = "de",
    json_body: Optional[dict] = None,
) -> Any:
    """Run one authenticated API call inside the logged-in browser context.

    The robust path — real browser TLS/cookies/DataDome. Returns parsed JSON. Raises
    NotLoggedIn if the session is not live.

    State-changing methods (POST/PUT/DELETE) go through an *in-page* fetch so they carry
    Doctolib's CSRF anti-forgery token from `<meta name="csrf-token">`. `ctx.request` does
    not carry that token, and Rails rejects a tokenless write — which surfaced as an
    opaque HTTP 500 on the first real cancel. Reads stay on the lighter `ctx.request` path.

    For a *multi-step* authenticated flow (the booking funnel) use `AuthedBrowser` instead,
    which keeps one session open across steps so the server-side draft and rotating CSRF
    token stay coherent — one call per launch here would drop that state.
    """
    sync_playwright = _require_playwright()
    base = _base(country)
    is_write = method.upper() not in ("GET", "HEAD")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir(country)), headless=True,
        )
        try:
            if not _is_authed(ctx, base):
                raise NotLoggedIn("no live session. Run `doctolib login`")

            if not is_write:
                r = ctx.request.fetch(base + path, method=method,
                                      headers={"Accept": "application/json"})
                status_code = r.status
                try:
                    body = r.json()
                except Exception:
                    body = {"_non_json": r.text()[:300]}
            else:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                # Land on an account page so the CSRF meta tag is present for this origin.
                page.goto(base + "/account/appointments", wait_until="domcontentloaded")
                out = page.evaluate(_INPAGE_FETCH_JS,
                                    {"path": path, "method": method.upper(), "body": json_body})
                status_code = out["status"]
                body = out["data"]
        finally:
            ctx.close()
    if status_code >= 400:
        raise DoctolibError(f"HTTP {status_code} on {path}: {str(body)[:300]}")
    return body


# One in-page fetch that carries the CSRF token and mirrors the app's token rotation:
# it reads `<meta name="csrf-token">` for the request and, if the server hands back a
# rotated token in the response headers, writes it back to the meta so the next call in the
# same page stays valid. Shared by authed_request (single write) and AuthedBrowser (funnel).
_INPAGE_FETCH_JS = """async (a) => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const csrf = meta ? meta.content : '';
    const r = await fetch(a.path, {
        method: a.method,
        headers: {'Accept':'application/json','Content-Type':'application/json','X-CSRF-Token':csrf},
        body: (a.body === null || a.body === undefined) ? undefined : JSON.stringify(a.body),
    });
    const rotated = r.headers.get('X-CSRF-Token');
    if (rotated && meta) meta.content = rotated;
    let data; try { data = await r.json(); } catch(e) { data = {_non_json:(await r.text()).slice(0,300)}; }
    return {status: r.status, data};
}"""


class AuthedBrowser:
    """One logged-in browser session held open across a multi-step authenticated flow.

    The booking funnel is four sequential calls sharing a server-side draft, and Doctolib
    rotates the CSRF token between them. A per-call browser launch would break both, so this
    keeps a single persistent context + page alive for the whole flow. Every call goes
    through the same in-page fetch as the write path, so cookies, CSRF, token rotation and
    DataDome device are all the real browser's. Use as a context manager:

        with AuthedBrowser("de") as b:
            b.request("POST", "/online_booking/api/.../select_slot", {...})
            b.request("GET",  "/online_booking/api/patient_selection/v1/info")
            ...
    """

    def __init__(self, country: str = "de") -> None:
        self.country = country
        self.base = _base(country)
        self._pw = None
        self._ctx = None
        self._page = None

    def __enter__(self) -> "AuthedBrowser":
        """Open the session, running the interactive login once if there is not one.

        `_open` tears its own context down before NotLoggedIn escapes, so the profile lock is
        released by the time the login subprocess needs it. Chromium would refuse otherwise.
        """
        try:
            return self._open()
        except NotLoggedIn:
            if not try_auto_login(self.country):
                raise
            return self._open()

    def _open(self) -> "AuthedBrowser":
        sync_playwright = _require_playwright()
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(_profile_dir(self.country)), headless=True,
            )
            if not _is_authed(self._ctx, self.base):
                raise NotLoggedIn("no live session. Run `doctolib login`")
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            # Same-origin page so the CSRF meta tag exists; the funnel calls are same-origin.
            self._page.goto(self.base + "/account/appointments", wait_until="domcontentloaded")
        except BaseException:
            self._teardown()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._teardown()

    def _teardown(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._ctx = self._pw = self._page = None

    def request(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        out = self._page.evaluate(
            _INPAGE_FETCH_JS, {"path": path, "method": method.upper(), "body": json_body}
        )
        if out["status"] >= 400:
            raise DoctolibError(f"HTTP {out['status']} on {path}: {str(out['data'])[:300]}")
        return out["data"]


def require_cookie(country: str = "de") -> str:
    """Account searches fail closed instead of silently changing to an anonymous view."""
    try:
        return cookie_header(country)
    except NotLoggedIn:
        if not try_auto_login(country):
            raise
        return cookie_header(country)
