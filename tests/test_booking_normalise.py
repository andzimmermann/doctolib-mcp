"""normalize_appointments() — the shape that drives scheduling recommendations.

Silently wrong output here is worse than a crash: a missed status rung or a transposed
speciality field changes which appointment the agent tells the user about next.
"""
from __future__ import annotations

import pytest

from doctolib_mcp.booking import normalize_appointments


def _envelope(*appts: dict) -> dict:
    return {"confirmed": list(appts), "pending": [], "done": [], "has_next_page": False}


def _one(appt: dict) -> dict:
    """Normalise a single appointment as a confirmed entry and return it."""
    return normalize_appointments(_envelope(appt))["confirmed"][0]


# --------------------------------------------------------------------------- #
# speciality polymorphism
# --------------------------------------------------------------------------- #

def test_normalize_accepts_speciality_as_bare_string():
    appt = {"profile": {"speciality": "Kardiologie"}}  # subtitle absent
    assert _one(appt)["speciality"] == "Kardiologie"


def test_normalize_accepts_speciality_as_name_slug_dict():
    appt = {"profile": {"speciality": {"name": "Kardiologie", "slug": "kardiologe"}}}
    assert _one(appt)["speciality"] == "Kardiologie"


def test_subtitle_takes_precedence_over_speciality_when_present(appointments):
    # confirmed[0] has subtitle "Allgemeinmedizin" AND a speciality dict.
    out = normalize_appointments(appointments)
    assert out["confirmed"][0]["speciality"] == "Allgemeinmedizin"
    # pending[0] has subtitle null and a bare-string speciality.
    assert out["pending"][0]["speciality"] == "Zahnmedizin"


# --------------------------------------------------------------------------- #
# status ladder (documented precedence)
# --------------------------------------------------------------------------- #

def test_status_confirmed_wins_over_all_other_flags():
    a = {"confirmed": True, "canceled": True, "past": True,
         "awaiting_confirmation_by_practitioner": True}
    assert _one(a)["status"] == "confirmed"


def test_status_awaiting_practitioner_when_not_confirmed():
    a = {"awaiting_confirmation_by_practitioner": True}
    assert _one(a)["status"] == "awaiting_practitioner"


def test_status_canceled_when_not_confirmed_or_awaiting():
    a = {"canceled": True}
    assert _one(a)["status"] == "canceled"


def test_status_past_when_none_of_the_above_set():
    a = {"past": True}
    assert _one(a)["status"] == "past"


def test_status_unknown_when_no_flag_is_set():
    assert _one({})["status"] == "unknown"


# --------------------------------------------------------------------------- #
# cancelable — flag reads oddly (see report)
# --------------------------------------------------------------------------- #

# cancelable is derived from warn_late_modification, whose actual meaning is "warn the user
# that modifying now counts as late", not "this appointment is cancelable". Pinning the
# current behaviour and flagging the oddness in the final report.
def test_cancelable_true_only_when_warn_late_modification_present_and_not_past():
    a = {"warn_late_modification": "2026-08-19T20:00:00.000+02:00"}
    assert _one(a)["cancelable"] is True


def test_cancelable_false_when_warn_late_modification_absent():
    assert _one({})["cancelable"] is False


def test_cancelable_false_for_a_past_appointment_even_when_warn_present():
    a = {"warn_late_modification": "2026-08-19T20:00:00.000+02:00", "past": True}
    assert _one(a)["cancelable"] is False


# --------------------------------------------------------------------------- #
# address assembly
# --------------------------------------------------------------------------- #

def test_address_assembled_from_all_parts():
    a = {"practice": {"address": "Kantstr. 12", "zipcode": "10625", "city": "Berlin"}}
    assert _one(a)["address"] == "Kantstr. 12, 10625, Berlin"


def test_address_skips_missing_parts_without_stray_commas():
    a = {"practice": {"city": "Berlin"}}  # only city present
    assert _one(a)["address"] == "Berlin"


def test_address_is_none_when_nothing_present():
    a = {"practice": {}}
    assert _one(a)["address"] is None  # the empty join falls through to None, not ""


# --------------------------------------------------------------------------- #
# empty envelope
# --------------------------------------------------------------------------- #

def test_empty_envelope_yields_empty_lists_without_raising():
    out = normalize_appointments({})
    assert out == {"confirmed": [], "pending": [], "done": [], "has_more_past": False}
