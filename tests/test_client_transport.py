"""Transport-layer behaviour of DoctolibClient.

Every test installs an httpx.MockTransport via the `make_client` fixture, so nothing
reaches the wire. The kill-switch in conftest is the backstop: a transport test that
forgets the fixture blows up on socket.connect instead of leaking to doctolib.de.
"""
from __future__ import annotations

import types

import httpx
import pytest

import doctolib_mcp.client as client_mod
from doctolib_mcp.client import DoctolibClient, DoctolibError


# --------------------------------------------------------------------------- #
# Status-code handling in _request
# --------------------------------------------------------------------------- #

def test_request_accepts_206_partial_content(make_client):
    """The search endpoint answers 206 on success; a naive `== 200` check would break it."""
    handler = lambda req: httpx.Response(206, json={"total": 0, "healthcareProviders": []})
    c = make_client(handler)
    out = c._request("POST", c.base + "/patient-health-search/api/v1/hcp/search")
    assert out == {"total": 0, "healthcareProviders": []}


def test_403_names_datadome_and_points_to_a_real_browser_session(make_client):
    handler = lambda req: httpx.Response(403, text="dd-blocked")
    c = make_client(handler)
    with pytest.raises(DoctolibError) as ei:
        c._request("GET", c.base + "/anything")
    msg = str(ei.value)
    assert "403" in msg
    assert "DataDome" in msg
    # Tell the reader the fix is a real browser session, not more header guessing.
    assert "stop" in msg.lower() and "do not bypass" in msg.lower()


def test_other_error_status_raises_with_status_and_truncated_body(make_client):
    body = "x" * 500
    c = make_client(lambda req: httpx.Response(500, text=body))
    with pytest.raises(DoctolibError) as ei:
        c._request("GET", c.base + "/anything")
    msg = str(ei.value)
    assert "500" in msg
    assert body[:200] not in msg      # never expose upstream bodies
    assert body[250:] not in msg      # ...and the tail is dropped


def test_non_json_200_raises_doctoliberror_not_valueerror(make_client):
    """A 200 HTML page must surface as DoctolibError, not a bare ValueError from r.json()."""
    c = make_client(lambda req: httpx.Response(200, text="<html>not json"))
    with pytest.raises(DoctolibError):
        c._request("GET", c.base + "/anything")


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #

def test_unsupported_country_raises_and_names_the_supported_ones():
    with pytest.raises(DoctolibError) as ei:
        DoctolibClient(country="es")
    msg = str(ei.value)
    for cc in ("de", "fr", "it"):
        assert cc in msg


def test_cookie_sets_header_and_marks_authenticated():
    # `authenticated` is surfaced to the user as provenance, so it must not lie.
    c = DoctolibClient(country="de", cookie="sid=abc; __dd=xyz")
    try:
        assert c.authenticated is True
        assert c._http.headers.get("Cookie") is None
        request = c._http.build_request("GET", c.base + "/")
        assert request.headers.get("Cookie") == "sid=abc; __dd=xyz"
    finally:
        c.close()


def test_no_cookie_means_anonymous_and_no_cookie_header():
    c = DoctolibClient(country="de")
    try:
        assert c.authenticated is False
        assert c._http.headers.get("Cookie") is None
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# _Throttle: per-host minimum interval
# --------------------------------------------------------------------------- #

def test_throttle_enforces_per_host_minimum_interval(monkeypatch):
    """A second hit on the same host waits; a first hit on a different host does not.

    Uses an injected clock and a recording sleep — never the real timer. Etiquette toward
    a healthcare provider's undocumented endpoints is a stated design value of this client.
    """
    clock = [100.0]
    sleeps: list[float] = []
    fake_time = types.SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda s: sleeps.append(s),
    )
    fake_random = types.SimpleNamespace(uniform=lambda a, b: 0.0)
    monkeypatch.setattr(client_mod, "time", fake_time)
    monkeypatch.setattr(client_mod, "random", fake_random)

    t = client_mod._Throttle(min_interval=0.6, jitter=0.4)

    # First hit on host A: last.get(host, 0.0) defaults to 0.0, so with a large monotonic
    # baseline the gap is hugely negative and nothing is waited.
    t.wait("https://www.doctolib.de/a")
    assert sleeps == []

    # Second hit on the same host soon after must wait the remainder.
    clock[0] = 100.1
    t.wait("https://www.doctolib.de/b")
    assert sleeps and pytest.approx(sleeps[-1], rel=1e-6) == 0.5  # 0.6 - 0.1 elapsed

    # A different host is untouched (its baseline is also 0.0 -> negative gap -> no wait).
    clock[0] = 100.2
    sleeps.clear()
    t.wait("https://www.doctolib.fr/c")
    assert sleeps == []
