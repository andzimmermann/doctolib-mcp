"""Safety gates — the preview paths must never commit a real clinical action.

These are the highest-value tests in the repo: book_appointment and cancel_appointment sit
one `confirm=True` away from booking or cancelling a real medical appointment, so the
preview path is asserted to perform zero writes even when the session is live.
"""
from __future__ import annotations

import doctolib_mcp.booking as booking_mod
import doctolib_mcp.mcp_server as mcp
import doctolib_mcp.session as session_mod


def _trap(*_a, **_k):
    """A double that records nothing and explodes if the preview path reaches it."""
    raise AssertionError("preview path touched the live session or booking client")


# --------------------------------------------------------------------------- #
# book_appointment preview
# --------------------------------------------------------------------------- #

def test_book_appointment_preview_performs_no_writes(monkeypatch):
    monkeypatch.setattr(session_mod, "authed_request", _trap)
    monkeypatch.setattr(session_mod, "try_cookie", _trap)
    monkeypatch.setattr(booking_mod, "DoctolibBookingClient", _trap)

    out = mcp.book_appointment(
        start_date="2026-08-20T09:00:00.000+02:00", profile_id=554433,
        visit_motive_ids="55012", agenda_ids="101-102", practice_ids="901", confirm=False,
    )
    assert out["would_book"] is True
    assert out["confirm"] is False
    # _trap would have raised AssertionError if anything reached it; reaching this line
    # means the preview path committed nothing.


def test_book_appointment_preview_echoes_parsed_ids_back(monkeypatch):
    monkeypatch.setattr(session_mod, "authed_request", _trap)
    out = mcp.book_appointment(
        start_date="2026-08-20T09:00:00.000+02:00", profile_id=554433,
        visit_motive_ids="55012-55013", agenda_ids="101,102", practice_ids="901", confirm=False,
    )
    payload = out["payload"]
    assert payload["visit_motive_ids"] == [55012, 55013]
    assert payload["agenda_ids"] == [101, 102]
    assert payload["practice_ids"] == [901]
    assert payload["profile_id"] == 554433
    assert payload["start_date"] == "2026-08-20T09:00:00.000+02:00"


# --------------------------------------------------------------------------- #
# cancel_appointment preview
# --------------------------------------------------------------------------- #

def test_cancel_appointment_preview_issues_no_delete(monkeypatch):
    """The preview legitimately performs one read (fetching valid reasons). Assert that
    read happens and no DELETE does — the preview must not quietly become binding."""
    calls: list[tuple] = []

    def recording_authed(method, path, country="de", json_body=None):
        calls.append((method, path, json_body))
        return {"reasons": [{"code": "sick", "label": "Krankheit"}]}

    monkeypatch.setattr(session_mod, "authed_request", recording_authed)
    out = mcp.cancel_appointment(appointment_id="appt-1", confirm=False)

    assert out["would_cancel"] is True
    assert out["confirm"] is False
    assert len(calls) == 1
    method, path, _body = calls[0]
    assert method == "GET"                                   # reads allowed...
    assert "appointment_modification_reasons/cancellations" in path
    assert not any(m == "DELETE" for (m, _p, _b) in calls)  # ...writes forbidden


def test_cancel_appointment_preview_survives_reasons_fetch_failure(monkeypatch):
    """The reasons fetch is wrapped in a bare except; the preview must still be usable."""
    def boom(*_a, **_k):
        raise RuntimeError("session down")

    monkeypatch.setattr(session_mod, "authed_request", boom)
    out = mcp.cancel_appointment(appointment_id="appt-1", confirm=False)
    assert out["would_cancel"] is True
    assert out["confirm"] is False
    assert "_error" in out["valid_reasons"]
    assert "session down" in out["valid_reasons"]["_error"]


# --------------------------------------------------------------------------- #
# abort_booking_draft — idempotent
# --------------------------------------------------------------------------- #

def test_abort_booking_draft_returns_aborted_false_rather_than_raising(monkeypatch):
    """Documented as idempotent: no draft (or a failing delete) returns aborted: False
    with a note, never an exception."""
    def boom(*_a, **_k):
        raise RuntimeError("no live session / nothing to delete")

    monkeypatch.setattr(session_mod, "authed_request", boom)
    out = mcp.abort_booking_draft()
    assert out["aborted"] is False
    assert "note" in out
