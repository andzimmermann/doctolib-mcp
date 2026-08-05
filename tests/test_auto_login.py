"""Auto-login: an authenticated call with no session opens the login window and retries.

Every test here patches `subprocess.run`. Nothing may actually spawn `doctolib login`, which
would pop a real browser window at whoever is running the suite.
"""
from __future__ import annotations

import os

import pytest

import doctolib_mcp.session as session_mod
from doctolib_mcp.session import NotLoggedIn


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


@pytest.fixture(autouse=True)
def reset_auto_login_state(monkeypatch):
    """Backoff state and the enable flag are module globals; isolate them per test."""
    session_mod._auto_login_last_failure.clear()
    session_mod._auto_login_last_success.clear()
    monkeypatch.delenv("DOCTOLIB_AUTO_LOGIN", raising=False)
    yield
    session_mod._auto_login_last_failure.clear()
    session_mod._auto_login_last_success.clear()


class _Spawns(list):
    """The recorded login spawns, with `.control.returncode` to set the child's exit code."""


@pytest.fixture
def spawns(monkeypatch):
    """Record login spawns and control their exit code. Returns the call list."""
    calls = _Spawns()

    def fake_run(cmd, **kw):
        calls.append({"cmd": cmd, **kw})
        return _FakeProc(fake_run.returncode)

    fake_run.returncode = 0
    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
    calls.control = fake_run
    return calls


# --------------------------------------------------------------------------- #
# The retry itself
# --------------------------------------------------------------------------- #

def test_authed_request_logs_in_then_retries_once(monkeypatch, spawns):
    """The first call finds no session, the login succeeds, the second call goes through."""
    attempts = []

    def once(method, path, country="de", json_body=None):
        attempts.append(path)
        if len(attempts) == 1:
            raise NotLoggedIn("no live session")
        return {"data": {"confirmed": []}}

    monkeypatch.setattr(session_mod, "_authed_request_once", once)
    out = session_mod.authed_request("GET", "/account/appointments.json")

    assert out == {"data": {"confirmed": []}}
    assert len(attempts) == 2          # tried, logged in, tried again
    assert len(spawns) == 1            # exactly one login window, not one per attempt


def test_authed_request_retries_only_once(monkeypatch, spawns):
    """A session that is still dead after a successful login must not loop forever."""
    attempts = []

    def always_dead(method, path, country="de", json_body=None):
        attempts.append(path)
        raise NotLoggedIn("no live session")

    monkeypatch.setattr(session_mod, "_authed_request_once", always_dead)
    with pytest.raises(NotLoggedIn):
        session_mod.authed_request("GET", "/account/appointments.json")

    assert len(attempts) == 2
    assert len(spawns) == 1


def test_authed_request_propagates_when_login_fails(monkeypatch, spawns):
    """A dismissed login window leaves the original NotLoggedIn intact for the caller."""
    spawns.control.returncode = 1
    calls = []

    def once(method, path, country="de", json_body=None):
        calls.append(path)
        raise NotLoggedIn("no live session")

    monkeypatch.setattr(session_mod, "_authed_request_once", once)
    with pytest.raises(NotLoggedIn):
        session_mod.authed_request("GET", "/account/appointments.json")

    assert len(calls) == 1              # no retry after a failed login
    assert len(spawns) == 1


def test_authed_browser_enter_logs_in_then_retries(monkeypatch, spawns):
    opens = []

    def fake_open(self):
        opens.append(1)
        if len(opens) == 1:
            raise NotLoggedIn("no live session")
        return self

    monkeypatch.setattr(session_mod.AuthedBrowser, "_open", fake_open)
    b = session_mod.AuthedBrowser("de").__enter__()

    assert b is not None
    assert len(opens) == 2
    assert len(spawns) == 1


# --------------------------------------------------------------------------- #
# The opt-out, for unattended callers
# --------------------------------------------------------------------------- #

def test_disabled_by_env_never_spawns(monkeypatch, spawns):
    """An unattended worker sets DOCTOLIB_AUTO_LOGIN=0 and gets the old fail-fast."""
    monkeypatch.setenv("DOCTOLIB_AUTO_LOGIN", "0")
    calls = []

    def once(method, path, country="de", json_body=None):
        calls.append(path)
        raise NotLoggedIn("no live session")

    monkeypatch.setattr(session_mod, "_authed_request_once", once)
    with pytest.raises(NotLoggedIn):
        session_mod.authed_request("GET", "/account/appointments.json")

    assert len(calls) == 1
    assert spawns == []                # no window opened at all


@pytest.mark.parametrize("value,enabled", [
    ("0", False), ("false", False), ("no", False), ("FALSE", False),
    ("1", True), ("yes", True), ("", True),
])
def test_auto_login_enabled_reads_the_env_flag(monkeypatch, value, enabled):
    monkeypatch.setenv("DOCTOLIB_AUTO_LOGIN", value)
    assert session_mod.auto_login_enabled() is enabled


def test_enabled_by_default_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("DOCTOLIB_AUTO_LOGIN", raising=False)
    assert session_mod.auto_login_enabled() is True


# --------------------------------------------------------------------------- #
# Reads must never pop a window
# --------------------------------------------------------------------------- #

def test_try_cookie_never_triggers_auto_login(spawns, tmp_path):
    """Public reads fall back to anonymous silently. A search must not open a browser."""
    assert session_mod.try_cookie("de") is None
    assert spawns == []


# --------------------------------------------------------------------------- #
# Subprocess contract
# --------------------------------------------------------------------------- #

def test_login_child_cannot_recurse(spawns):
    """The child *is* the login, so its env disables auto-login or it would spawn itself."""
    session_mod.try_auto_login("de")
    env = spawns[0]["env"]
    assert env["DOCTOLIB_AUTO_LOGIN"] == "0"


def test_login_child_gets_src_on_pythonpath(spawns):
    """pyproject sets package = false, so the child cannot import without PYTHONPATH."""
    session_mod.try_auto_login("de")
    env = spawns[0]["env"]
    first = env["PYTHONPATH"].split(os.pathsep)[0]
    assert first.endswith("src")
    assert os.path.isdir(first)


def test_login_child_is_invoked_with_country_and_timeout(spawns):
    session_mod.try_auto_login("fr", timeout_s=45)
    cmd = spawns[0]["cmd"]
    assert cmd[1:4] == ["-m", "doctolib_mcp.cli", "login"]
    assert "--country" in cmd and cmd[cmd.index("--country") + 1] == "fr"
    assert "--timeout" in cmd and cmd[cmd.index("--timeout") + 1] == "45"


def test_only_exit_zero_counts_as_logged_in(spawns):
    spawns.control.returncode = 0
    assert session_mod.try_auto_login("de") is True
    session_mod._auto_login_last_failure.clear()
    session_mod._auto_login_last_success.clear()
    spawns.control.returncode = 1
    assert session_mod.try_auto_login("de") is False


def test_try_auto_login_never_raises(monkeypatch):
    """A crashing or timing-out subprocess degrades to False, never an exception, so the
    caller surfaces its own NotLoggedIn instead of a confusing OSError."""
    def boom(*_a, **_kw):
        raise OSError("no such executable")

    monkeypatch.setattr(session_mod.subprocess, "run", boom)
    assert session_mod.try_auto_login("de") is False


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #

def test_failed_login_is_not_retried_during_the_cooldown(spawns):
    """A dismissed window must not reopen on every subsequent tool call."""
    spawns.control.returncode = 1
    assert session_mod.try_auto_login("de") is False
    assert session_mod.try_auto_login("de") is False
    assert len(spawns) == 1            # second attempt suppressed by the cooldown


def test_cooldown_is_per_country(spawns):
    spawns.control.returncode = 1
    session_mod.try_auto_login("de")
    session_mod.try_auto_login("fr")
    assert len(spawns) == 2            # a dead German session says nothing about French


def test_a_second_caller_reuses_a_just_completed_login(spawns):
    """Two calls that both find no session must produce one window, not two. A second window
    would fail anyway, because Chromium holds an exclusive lock on the profile directory."""
    spawns.control.returncode = 0
    assert session_mod.try_auto_login("de") is True
    assert session_mod.try_auto_login("de") is True
    assert len(spawns) == 1


def test_concurrent_callers_open_one_window(monkeypatch):
    """The real race: several threads hitting an expired session at once."""
    import threading

    spawned = []
    barrier = threading.Barrier(4)

    def fake_run(cmd, **kw):
        spawned.append(cmd)
        return _FakeProc(0)

    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)

    def worker():
        barrier.wait()               # maximise the overlap
        session_mod.try_auto_login("de")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(spawned) == 1


def test_success_clears_the_backoff_and_the_negative_cookie_cache(spawns):
    """try_cookie caches None for 5 minutes. A fresh login must not be masked by that."""
    session_mod._cookie_cache["de"] = (None, 0.0)
    spawns.control.returncode = 0

    assert session_mod.try_auto_login("de") is True
    assert "de" not in session_mod._cookie_cache
    assert "de" not in session_mod._auto_login_last_failure
