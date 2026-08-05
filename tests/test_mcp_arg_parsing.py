"""MCP tool argument parsing — the strings a human or an agent actually types.

Doctolib joins agenda ids with "-" in its own URLs; humans type ",". The tools must accept
both, plus the single-id case and stray whitespace, because an unparseable id string here
means a search that silently returns nothing rather than an obvious error.
"""
from __future__ import annotations

import doctolib_mcp.mcp_server as mcp_server
import doctolib_mcp.session as session_mod


class _RecordingClient:
    """A DoctolibClient stand-in that records the args each tool forwards to it."""

    def __init__(self):
        self.recorded: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def availabilities(self, visit_motive_id, agenda_ids, practice_id, **kw):
        self.recorded["agenda_ids"] = agenda_ids
        return {"echo_agenda_ids": agenda_ids}

    def search(self, specialty, **kw):
        self.recorded["search"] = kw
        return {}

    def earliest(self, **kw):
        self.recorded["earliest"] = kw
        return {}


def _wire(monkeypatch) -> _RecordingClient:
    rc = _RecordingClient()
    monkeypatch.setattr(mcp_server, "_read_client", lambda country: rc)
    return rc


# --------------------------------------------------------------------------- #
# get_availabilities — agenda_ids accepted in every form a caller types
# --------------------------------------------------------------------------- #

def test_get_availabilities_accepts_hyphen_separated_agenda_ids(monkeypatch):
    rc = _wire(monkeypatch)
    mcp_server.get_availabilities(visit_motive_id=1, agenda_ids="1-2-3", practice_id=9)
    assert rc.recorded["agenda_ids"] == [1, 2, 3]


def test_get_availabilities_accepts_comma_separated_agenda_ids(monkeypatch):
    rc = _wire(monkeypatch)
    mcp_server.get_availabilities(visit_motive_id=1, agenda_ids="1,2,3", practice_id=9)
    assert rc.recorded["agenda_ids"] == [1, 2, 3]


def test_get_availabilities_accepts_a_single_agenda_id(monkeypatch):
    rc = _wire(monkeypatch)
    mcp_server.get_availabilities(visit_motive_id=1, agenda_ids="42", practice_id=9)
    assert rc.recorded["agenda_ids"] == [42]


# --------------------------------------------------------------------------- #
# book_appointment preview — same parsing for all three id arguments
# --------------------------------------------------------------------------- #

def test_book_appointment_preview_parses_hyphen_and_comma_ids(monkeypatch):
    # Guard: the preview must not reach the session.
    monkeypatch.setattr(session_mod, "authed_request",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("preview reached session")))
    out = mcp_server.book_appointment(
        start_date="x", profile_id=1,
        visit_motive_ids="10-20", agenda_ids="1,2", practice_ids="5", confirm=False,
    )
    p = out["payload"]
    assert p["visit_motive_ids"] == [10, 20]
    assert p["agenda_ids"] == [1, 2]
    assert p["practice_ids"] == [5]


def test_book_appointment_preview_tolerates_empty_segments_and_whitespace(monkeypatch):
    out = mcp_server.book_appointment(
        start_date="x", profile_id=1,
        visit_motive_ids="1,,2", agenda_ids=" 1 , 2 ", practice_ids="3", confirm=False,
    )
    p = out["payload"]
    assert p["visit_motive_ids"] == [1, 2]
    assert p["agenda_ids"] == [1, 2]
    assert p["practice_ids"] == [3]


# --------------------------------------------------------------------------- #
# languages
# --------------------------------------------------------------------------- #

def test_languages_split_on_commas_and_stripped(monkeypatch):
    rc = _wire(monkeypatch)
    mcp_server.search_doctors(specialty="hausarzt", languages="tr, gb")
    assert rc.recorded["search"]["languages"] == ["tr", "gb"]


def test_languages_none_stays_none_rather_than_becoming_empty_list(monkeypatch):
    rc = _wire(monkeypatch)
    mcp_server.search_doctors(specialty="hausarzt", languages=None)
    assert rc.recorded["search"]["languages"] is None  # not [""]


# --------------------------------------------------------------------------- #
# list_specialties
# --------------------------------------------------------------------------- #

def test_list_specialties_returns_non_empty_mapping_and_admits_free_text():
    out = mcp_server.list_specialties()
    assert isinstance(out["specialties"], dict) and len(out["specialties"]) > 0
    # The description promises this is a convenience list, not a whitelist.
    assert "free" in out["note"].lower()
