"""The booking funnel — the highest-stakes code in the repo.

Every test runs against a FakeBrowser that records (method, path, body) tuples. The real
AuthedBrowser drives a logged-in Playwright session and is structurally blocked by the
conftest kill-switch; we never want to drive the real funnel from a test, because the only
faithful end-to-end run is a real booking.

Two pure functions matter most: _pick_patient (booking a clinical slot for the wrong family
member is the catastrophic failure) and _build_confirm_payload (inventing values onto a
medical intake form is exactly the wrong kind of creative).
"""
from __future__ import annotations

import pytest

from doctolib_mcp.booking import BookingError, DoctolibBookingClient

# The exact paths the module documents for the funnel. Pinning these means a refactor that
# silently re-points a step is caught here rather than during a real booking.
SELECT_SLOT = ("POST", "/online_booking/api/slot_selection_funnel/v1/select_slot")
MASTER_PATIENTS = ("GET", "/account/master_patients.json")
SELECT_PATIENT = ("POST", "/online_booking/api/patient_selection/v1/select")
PATIENT_FORM = ("GET", "/online_booking/api/checkout/v1/patient_form")
PATIENT_CONFIRM = ("POST", "/online_booking/api/checkout/v1/patient_confirm")
CONFIRMATION = ("GET", "/online_booking/api/confirmation/v1/booking")
DELETE_DRAFT = ("DELETE", "/online_booking/draft.json")
PATIENT_INFO = ("GET", "/online_booking/api/patient_selection/v1/info")


def _client(fake) -> DoctolibBookingClient:
    """A booking client wired straight to a FakeBrowser, bypassing the real session open.

    book() and its steps use self._browser exclusively, so assigning it is enough; the
    context-manager dance in __enter__ (which would launch Playwright) is never taken.
    """
    c = DoctolibBookingClient("de")
    c._browser = fake
    return c


def _full_responses(patient_form: dict, master_patients: list) -> dict:
    return {
        SELECT_SLOT: {"draft_id": "d1", "start_date": "2026-08-20T09:00:00.000+02:00"},
        MASTER_PATIENTS: master_patients,  # a list with a single "main" -> auto-selected
        SELECT_PATIENT: {"ok": True},
        PATIENT_FORM: patient_form,
        PATIENT_CONFIRM: {"id": "appt-new"},
        CONFIRMATION: {"id": "appt-new", "status": "confirmed"},
    }


_BOOK_ARGS = dict(
    start_date="2026-08-20T09:00:00.000+02:00", profile_id=554433,
    visit_motive_ids=[55012], agenda_ids=[101], practice_ids=[901], new_patient=False,
)


# --------------------------------------------------------------------------- #
# _pick_patient — one test per branch
# --------------------------------------------------------------------------- #

def test_pick_patient_raises_on_empty_list():
    with pytest.raises(BookingError):
        DoctolibBookingClient._pick_patient([], None)


def test_pick_patient_hint_matching_exactly_one_returns_it(master_patients):
    chosen = DoctolibBookingClient._pick_patient(master_patients, "Erika")
    assert chosen["first_name"] == "Erika"


def test_pick_patient_hint_matching_none_raises(master_patients):
    with pytest.raises(BookingError):
        DoctolibBookingClient._pick_patient(master_patients, "NobodyMatchesThis")


def test_pick_patient_hint_matching_several_raises(master_patients):
    # "Mustermann" matches all three family members.
    with pytest.raises(BookingError):
        DoctolibBookingClient._pick_patient(master_patients, "Mustermann")


def test_pick_patient_auto_selects_a_single_patient():
    one = [{"first_name": "Erika", "last_name": "Mustermann", "kind": "main"}]
    chosen = DoctolibBookingClient._pick_patient(one, None)
    assert chosen["first_name"] == "Erika"


def test_pick_patient_selects_the_single_main_among_several():
    patients = [
        {"first_name": "A", "last_name": "B", "kind": "main"},
        {"first_name": "C", "last_name": "D", "kind": "dependent"},
    ]
    chosen = DoctolibBookingClient._pick_patient(patients, None)
    assert chosen["first_name"] == "A"


def test_pick_patient_raises_when_several_patients_and_no_unambiguous_main():
    # Two "main" entries -> the picker refuses to guess.
    patients = [
        {"first_name": "A", "last_name": "B", "kind": "main"},
        {"first_name": "C", "last_name": "D", "kind": "main"},
        {"first_name": "E", "last_name": "F", "kind": "dependent"},
    ]
    with pytest.raises(BookingError):
        DoctolibBookingClient._pick_patient(patients, None)


# --------------------------------------------------------------------------- #
# _build_confirm_payload — invents nothing
# --------------------------------------------------------------------------- #

def test_build_confirm_payload_invents_nothing(patient_form):
    master_patient, _draft = DoctolibBookingClient._build_confirm_payload(patient_form, 554433, False)
    # Every emitted patient key is one the server prefilled...
    assert set(master_patient.keys()).issubset(set(patient_form["patient"].keys()))
    # ...None values are dropped...
    assert "maiden_name" not in master_patient  # null in fixture
    assert all(v is not None for v in master_patient.values())
    # ...and a key the form never carried is never invented.
    assert "national_health_identifier" not in master_patient


def test_build_confirm_payload_echoes_appointment_fields(patient_form):
    _master, draft = DoctolibBookingClient._build_confirm_payload(patient_form, 554433, False)
    assert draft["custom_fields_values"] == patient_form["appointment"]["custom_fields_values"]
    assert draft["qualification_answers"] == patient_form["appointment"]["qualification_answers"]
    assert draft["profile_id"] == 554433
    assert draft["new_patient"] is False  # from appointment.new_patient, not the arg


def test_build_confirm_payload_falls_back_from_appointment_to_draft():
    form = {"draft": {"new_patient": True, "profile_id": 888, "custom_fields_values": {"x": 1}},
            "patient": {}}
    _master, draft = DoctolibBookingClient._build_confirm_payload(form, 888, True)
    assert draft["new_patient"] is True
    assert draft["profile_id"] == 888
    assert draft["custom_fields_values"] == {"x": 1}


def test_build_confirm_payload_falls_back_to_passed_args_when_no_appointment_or_draft():
    _master, draft = DoctolibBookingClient._build_confirm_payload({"patient": {}}, 777, True)
    assert draft["new_patient"] is True
    assert draft["profile_id"] == 777
    assert draft["custom_fields_values"] == {}
    assert draft["qualification_answers"] == {}


# --------------------------------------------------------------------------- #
# book() — the funnel
# --------------------------------------------------------------------------- #

def test_book_runs_the_funnel_steps_in_documented_order(make_fake_browser, patient_form, master_patients):
    fake = make_fake_browser(_full_responses(patient_form, master_patients))
    result = _client(fake).book(**_BOOK_ARGS)
    assert result["booked"] is True
    assert fake.method_path_seq == [
        SELECT_SLOT, MASTER_PATIENTS, SELECT_PATIENT, PATIENT_FORM, PATIENT_CONFIRM, CONFIRMATION,
    ]


def test_book_reads_patients_from_master_patients_not_patient_selection_info(
    make_fake_browser, patient_form, master_patients
):
    # This exact confusion broke the first real booking. Stub the info path too, so a
    # regression that reads it shows up in the recording rather than slipping through.
    responses = _full_responses(patient_form, master_patients)
    responses[PATIENT_INFO] = {"draft": {}}
    fake = make_fake_browser(responses)
    _client(fake).book(**_BOOK_ARGS)
    paths = [p for (_m, p) in fake.method_path_seq]
    assert "/account/master_patients.json" in paths
    assert "/online_booking/api/patient_selection/v1/info" not in paths


# --- self-cleaning: one test per failing step after select_slot --- #

def _run_failing_at(make_fake_browser, patient_form, master_patients, failing_key) -> tuple[dict, object]:
    responses = _full_responses(patient_form, master_patients)
    responses[failing_key] = RuntimeError("boom at " + repr(failing_key))
    fake = make_fake_browser(responses)
    result = _client(fake).book(**_BOOK_ARGS)
    return result, fake


@pytest.mark.parametrize("failing_step", [MASTER_PATIENTS, SELECT_PATIENT, PATIENT_FORM],
                         ids=["master_patients", "select_patient", "patient_form"])
def test_book_self_cleans_when_a_post_select_slot_step_fails(
    make_fake_browser, patient_form, master_patients, failing_step
):
    result, fake = _run_failing_at(make_fake_browser, patient_form, master_patients, failing_step)
    # The failure is reported, not raised...
    assert result["booked"] is False
    assert "boom" in result["error"]
    assert result["draft_cleaned"] is True
    # ...and the draft hold was torn down.
    assert fake.method_path_seq[-1] == DELETE_DRAFT


def test_book_returns_a_failure_dict_rather_than_raising(make_fake_browser, patient_form, master_patients):
    # Explicit standalone pin: callers depend on the dict shape, never on an exception.
    responses = _full_responses(patient_form, master_patients)
    responses[MASTER_PATIENTS] = RuntimeError("nope")
    fake = make_fake_browser(responses)
    result = _client(fake).book(**_BOOK_ARGS)
    assert isinstance(result, dict)
    assert result["booked"] is False


# --------------------------------------------------------------------------- #
# _call — 200 with an errors array is a failure
# --------------------------------------------------------------------------- #

def test_call_treats_http_200_with_errors_array_as_failure(make_fake_browser):
    fake = make_fake_browser({("POST", "/x"): {"errors": [{"detail": "rejected"}]}})
    with pytest.raises(BookingError):
        _client(fake)._call("POST", "/x", {})


@pytest.mark.parametrize('step,expected', [(PATIENT_CONFIRM,None),(CONFIRMATION,True)])
def test_commit_or_confirmation_failure_never_claims_unbooked_or_cleans_draft(
    make_fake_browser,patient_form,master_patients,step,expected):
    result,fake=_run_failing_at(make_fake_browser,patient_form,master_patients,step)
    assert result['booked'] is expected
    assert result['retry_allowed'] is False
    assert DELETE_DRAFT not in fake.method_path_seq
