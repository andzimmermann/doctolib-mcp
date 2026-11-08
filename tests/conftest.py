"""Shared pytest infrastructure for doctolib-mcp.

The first job of this file is the network kill-switch (below). This repo can book and
cancel real medical appointments and holds a live authenticated session on disk, so every
test runs fully offline: a test that forgets to install an httpx.MockTransport must fail
loudly rather than reach the internet.

The second job is a handful of shared fakes (FakeBrowser for the booking funnel, a
DoctolibClient-with-MockTransport factory, JSON fixture loaders).
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx
import pytest

from doctolib_mcp import session as _session_module
from doctolib_mcp.client import DoctolibClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Network kill-switch
# --------------------------------------------------------------------------- #

def _block(*_a: Any, **_kw: Any) -> Any:
    raise RuntimeError("test attempted a real network connection")


def _block_playwright(*_a: Any, **_kw: Any) -> Any:
    raise RuntimeError("test attempted to launch a browser")


@pytest.fixture(scope="session", autouse=True)
def kill_network() -> None:
    """Make real I/O structurally impossible for the whole session.

    - socket.connect / socket.create_connection raise, so any stray httpx.get or curl-like
      call that forgot to install a MockTransport explodes instead of leaking to the wire.
    - session._require_playwright raises, so no test can spin up the real Playwright
      profile that drives booking/cancel.
    """
    # connect is a method on socket instances; create_connection is a module function.
    socket.socket.connect = _block  # type: ignore[method-assign]
    socket.create_connection = _block  # type: ignore[assignment]
    _session_module._require_playwright = _block_playwright  # type: ignore[assignment]
    yield
    # pytest does not restore module attributes we set by hand, but the process exits with
    # the session, so leaving them is harmless. Tests that need a fake Playwright (the
    # cookie-filter test) override _require_playwright locally via monkeypatch.


@pytest.fixture(autouse=True)
def isolated_session_root(tmp_path, monkeypatch) -> None:
    """Per-test redirect of SESSION_ROOT and reset of the cookie cache.

    The session-scoped kill_network never lets the real .session/ be read, but the cookie
    cache (_cookie_cache) and any created profile dirs are module globals that would bleed
    between tests. Point SESSION_ROOT at a fresh tmp dir each test and clear the cache so
    the session/cookie tests are hermetic.
    """
    monkeypatch.setattr(_session_module, "SESSION_ROOT", tmp_path)
    _session_module._cookie_cache.clear()


# --------------------------------------------------------------------------- #
# Shared fakes
# --------------------------------------------------------------------------- #

class FakeBrowser:
    """A stand-in for session.AuthedBrowser that records calls and returns canned data.

    The booking funnel's every hop goes through DoctolibBookingClient._call -> .request,
    and abort_draft calls .request directly, so this single chokepoint is enough to drive
    the whole funnel offline. `responses` maps (method, path) to either a value to return,
    an Exception instance to raise, or a callable taking (method, path, json_body).

    It deliberately raises nothing on its own: a path with no entry returns `default`
    ({}), so a test that forgets to stub a step fails on the funnel's own validation
    rather than on a confusing KeyError from the fake.
    """

    def __init__(
        self,
        responses: Optional[dict[tuple[str, str], Any]] = None,
        default: Any = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default if default is not None else {}
        self.calls: list[tuple[str, str, Optional[dict]]] = []

    def request(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        # Normalise to the path component (drop any query string) so a step is identified by
        # its route, not its parameters — e.g. checkout/v1/patient_form is always the same
        # step whether or not it carries a master_patient_uuid query.
        path_only = urlsplit(path).path
        self.calls.append((method, path_only, json_body))
        key = (method, path_only)
        if key in self._responses:
            resp = self._responses[key]
            if isinstance(resp, BaseException):
                raise resp
            if callable(resp) and not isinstance(resp, type):
                return resp(method, path, json_body)
            return resp
        return self._default

    @property
    def method_path_seq(self) -> list[tuple[str, str]]:
        """Just the (method, path) of each call, in order — for funnel-order assertions."""
        return [(m, p) for (m, p, _b) in self.calls]


@pytest.fixture
def make_fake_browser() -> Callable[..., FakeBrowser]:
    def _make(
        responses: Optional[dict[tuple[str, str], Any]] = None,
        default: Any = None,
    ) -> FakeBrowser:
        return FakeBrowser(responses=responses, default=default)

    return _make


@pytest.fixture
def fake_browser(make_fake_browser: Callable[..., FakeBrowser]) -> FakeBrowser:
    """A bare FakeBrowser with no canned responses (returns {} for everything)."""
    return make_fake_browser()


@pytest.fixture
def make_client() -> Callable[..., DoctolibClient]:
    """Build a DoctolibClient whose http is an httpx.MockTransport.

    The throttle is neutralised (wait -> noop) so transport/parsing tests never sleep.
    Tests that want to exercise _Throttle itself construct one directly and patch the
    clock — see test_client_transport.py.
    """

    def _make(
        handler: Callable[[httpx.Request], httpx.Response],
        country: str = "de",
        cookie: Optional[str] = None,
    ) -> DoctolibClient:
        c = DoctolibClient(country=country, cookie=cookie)
        transport = httpx.MockTransport(handler)
        # Preserve the headers __init__ assembled (UA, Accept-Language, Cookie, ...).
        c._http = httpx.Client(
            transport=transport,
            headers=c._http.headers,
            cookies=c._http.cookies,
            follow_redirects=True,
            timeout=30.0,
        )
        c._throttle.wait = lambda url: None  # type: ignore[assignment]
        return c

    return _make


# --------------------------------------------------------------------------- #
# Fixture loaders
# --------------------------------------------------------------------------- #

def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def fix() -> Callable[[str], Any]:
    return _load


@pytest.fixture
def search_response() -> Any:
    return _load("search_response.json")


@pytest.fixture
def practitioner_info() -> Any:
    return _load("practitioner_info.json")


@pytest.fixture
def availabilities() -> Any:
    return _load("availabilities.json")


@pytest.fixture
def availabilities_not_opened() -> Any:
    return _load("availabilities_not_opened.json")


@pytest.fixture
def appointments() -> Any:
    return _load("appointments.json")


@pytest.fixture
def patient_form() -> Any:
    return _load("patient_form.json")


@pytest.fixture
def master_patients() -> Any:
    return _load("master_patients.json")
