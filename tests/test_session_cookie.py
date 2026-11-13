"""session.try_cookie / cookie_header — the gate behind every read tool.

try_cookie sits in front of every public read, so a raise there takes down the whole read
surface. These tests pin that it never raises and caches sensibly.

session.login() and session.status() are deliberately NOT covered: they drive a real,
visible Playwright browser and are interactive by design. Mocking Playwright deeply enough
to exercise them would test the mock rather than the code, so the omission is a decision,
not an oversight.
"""
from __future__ import annotations

import time as real_time

import doctolib_mcp.session as session_mod


# --------------------------------------------------------------------------- #
# try_cookie never raises
# --------------------------------------------------------------------------- #

def test_try_cookie_returns_none_when_no_session_dir():
    # SESSION_ROOT is a fresh tmp (conftest) with no country subdir.
    assert session_mod.try_cookie("de") is None


def test_try_cookie_never_raises_when_cookie_header_raises(monkeypatch, tmp_path):
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)

    def boom(country="de"):
        raise RuntimeError("cookie export blew up")

    monkeypatch.setattr(session_mod, "cookie_header", boom)
    assert session_mod.try_cookie("de") is None  # swallowed by the bare except


def test_try_cookie_never_raises_when_playwright_is_missing(monkeypatch, tmp_path):
    """With the kill-switch active, the real cookie_header raises via _require_playwright;
    try_cookie must swallow that too (the read surface cannot depend on Playwright)."""
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)
    # cookie_header is intentionally NOT patched -> the real one runs -> _require_playwright
    # raises (conftest kill-switch) -> caught -> None.
    assert session_mod.try_cookie("de") is None


# --------------------------------------------------------------------------- #
# caching (uses an injected clock, never a real sleep)
# --------------------------------------------------------------------------- #

def _freeze_clock(monkeypatch, start=1000.0):
    t = [start]
    # try_cookie does a local `import time; now = time.monotonic()`, which resolves to this
    # same module object, so patching time.monotonic is seen by it.
    monkeypatch.setattr(real_time, "monotonic", lambda: t[0])
    return t


def test_try_cookie_caches_a_positive_result_within_ttl(monkeypatch, tmp_path):
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)
    calls = {"n": 0}

    def fake_cookie_header(country="de"):
        calls["n"] += 1
        return "sid=abc"

    monkeypatch.setattr(session_mod, "cookie_header", fake_cookie_header)
    _freeze_clock(monkeypatch)

    first = session_mod.try_cookie("de")
    second = session_mod.try_cookie("de")
    assert first == "sid=abc" and second == "sid=abc"
    assert calls["n"] == 1  # second call served from cache


def test_try_cookie_caches_a_negative_result_too(monkeypatch, tmp_path):
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)
    calls = {"n": 0}

    def boom(country="de"):
        calls["n"] += 1
        raise RuntimeError("no cookies")

    monkeypatch.setattr(session_mod, "cookie_header", boom)
    _freeze_clock(monkeypatch)

    assert session_mod.try_cookie("de") is None
    assert session_mod.try_cookie("de") is None
    assert calls["n"] == 1  # the miss is cached, so no second browser attempt


def test_try_cookie_rechecks_after_ttl_expires(monkeypatch, tmp_path):
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)
    calls = {"n": 0}

    def fake_cookie_header(country="de"):
        calls["n"] += 1
        return "sid=xyz"

    monkeypatch.setattr(session_mod, "cookie_header", fake_cookie_header)
    clock = _freeze_clock(monkeypatch)

    session_mod.try_cookie("de")   # populates cache at t=1000
    session_mod.try_cookie("de")   # cache hit
    assert calls["n"] == 1

    clock[0] = 1000.0 + 301.0      # past the 300s default TTL
    session_mod.try_cookie("de")   # cache stale -> recheck
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# REVIEWED, NOT A BUG: cookie_header's domain filter is a substring test
#
# Raised as suspect, then cleared on two independent grounds. Playwright's
# ctx.cookies(base) already returns only cookies that would be sent to that URL, so this
# filter is a second pass over an already-correct list and can only narrow it. And the
# profile is dedicated to this server, which never navigates it anywhere except Doctolib,
# so there are no foreign-origin cookies in it to admit. The test below pins the loose
# behaviour anyway: if either of those premises stops holding, tighten this to a suffix
# match and this test is the one to change.
# --------------------------------------------------------------------------- #

# Minimal fake Playwright chain so cookie_header runs its real filter logic on crafted
# cookies without launching a browser. _is_authed needs ctx.request.get -> ok + {"data":...};
# cookie_header needs ctx.cookies(base) -> the crafted list.

class _OkResp:
    ok = True

    def json(self):
        return {"data": [{"id": 1}]}

    def text(self):
        return ""


class _CtxRequest:
    def get(self, url, **kw):
        return _OkResp()


class _FakeCtx:
    def __init__(self, cookies):
        self._cookies = cookies
        self.request = _CtxRequest()

    def cookies(self, base=None):
        return self._cookies

    def close(self):
        pass


class _FakeBrowserType:
    """playwright.chromium — has launch_persistent_context."""

    def __init__(self, ctx):
        self._ctx = ctx

    def launch_persistent_context(self, **kw):
        return self._ctx


class _FakePlaywright:
    """The object sync_playwright().__enter__() returns; exposes .chromium."""

    def __init__(self, ctx):
        self.chromium = _FakeBrowserType(ctx)


class _FakePlaywrightCM:
    """The object sync_playwright() returns; used as a context manager."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __enter__(self):
        return _FakePlaywright(self._ctx)

    def __exit__(self, *a):
        return False


def _install_fake_playwright(monkeypatch, cookies):
    ctx = _FakeCtx(cookies)
    # _require_playwright() returns sync_playwright; sync_playwright() returns the CM.
    monkeypatch.setattr(session_mod, "_require_playwright",
                        lambda: (lambda: _FakePlaywrightCM(ctx)))


def test_cookie_header_filter_rejects_a_short_substring_domain(monkeypatch, tmp_path):
    # The filter is
    #   c["domain"].lstrip(".") in host or "doctolib" in c["domain"]
    # with host = "www.doctolib.de", so a cookie whose domain is a short substring of that
    # host is swept in regardless of provenance. Harmless here for the reasons in the block
    # above. This constructs a cookie list Playwright would never hand us, to pin what the
    # filter does on its own.
    (tmp_path / "de").mkdir()
    monkeypatch.setattr(session_mod, "SESSION_ROOT", tmp_path)
    cookies = [
        {"name": "sid", "value": "1", "domain": ".doctolib.de"},        # legit -> in
        {"name": "suspicious", "value": "2", "domain": "de"},           # loose match -> in
        {"name": "other", "value": "3", "domain": "example.com"},       # unrelated -> out
    ]
    _install_fake_playwright(monkeypatch, cookies)

    header = session_mod.cookie_header("de")
    assert "sid=1" in header
    assert "other=3" not in header
    # The suspect cookie leaks in because "de" is a substring of "www.doctolib.de".
    assert "suspicious=2" not in header
