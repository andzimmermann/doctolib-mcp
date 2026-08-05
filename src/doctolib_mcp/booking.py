"""The booking (write) path — reconstructed from Doctolib's own funnel JavaScript.

READ THIS BEFORE TOUCHING ANYTHING HERE.

Booking commits a real clinical slot at a real practice under Eren's real name. A
successful call means a human being is expected to physically show up at that address at
that time, and a no-show is rude at best and billable at worst. Nothing in this module
should ever run as a side effect of a search.

## Why it is shaped like this

The funnel is **stateful and session-bound**, not a single POST. `select_slot` creates a
server-side *draft* (a temporary hold) keyed to the session — note that
`patient_selection/v1/info` takes no parameters at all, it reads the draft back out of the
session. Four steps, in order:

    1. POST /online_booking/api/slot_selection_funnel/v1/select_slot   -> creates draft
    2. GET  /account/master_patients.json                             -> who can be booked
       POST /online_booking/api/patient_selection/v1/select            -> pick master patient
    3. GET  /online_booking/api/checkout/v1/patient_form               -> server-prefilled fields
       POST /online_booking/api/checkout/v1/patient_confirm            -> COMMITS THE BOOKING
    4. GET  /online_booking/api/confirmation/v1/booking                -> final state

The bookable patients do NOT come from `patient_selection/v1/info` (that returns the draft
and, when the step is auto-resolved, a `redirection`) — the UI's patient picker loads them
from `/account/master_patients.json`, each carrying the `signed_id` that `select` needs and
a `kind` ("main" for the account holder). The first real booking failed precisely because
this module read patients off the info response; fixed to use the account endpoint.

Self-cleaning: if any step after `select_slot` fails, the funnel tears the draft down via
`DELETE /online_booking/draft.json` so a broken run never leaves a slot held or a stale
draft that could interfere with a later booking. A draft is only a temporary hold — never a
confirmed appointment — so clearing it is safe and idempotent.

Because it is stateful and Doctolib rotates the CSRF token between steps, the whole funnel
runs inside **one** `AuthedBrowser` session (a single persistent browser context). Each
call is an in-page fetch carrying the live CSRF token — the same mechanism that the first
real `cancel_appointment` needed. A per-step browser launch, or an httpx cookie replay,
would drop the draft and the rotating token; that was the original bug here.

Step 3's payload is deliberately echoed back from what step 3's GET returned rather than
assembled from our own guesses. The form carries practice-specific custom fields and
qualification questions that we cannot know in advance, and inventing values for a medical
intake form is exactly the wrong kind of creative.

## Verification status — be honest with the user about this

Every endpoint path and payload key here was read out of Doctolib's shipped funnel bundles
(chunks 93204 / 58900 / 96195 / 31180). The transport is now correct-by-construction: it
reuses the exact in-page CSRF write that a real cancellation proved works. But **the funnel
itself has still never been executed end to end** — that cannot be tested without making a
real booking. Treat the first real call as the test, expect step 3 (checkout) to be the
fragile one, and surface server errors verbatim.

## Partial failure

If a step after `select_slot` fails, a draft hold may exist server-side. It expires on its
own, but the result dict says so and carries `resume_url` so the human can finish or
cancel it in a browser. Never silently retry a half-finished funnel.
"""
from __future__ import annotations

from typing import Any, Optional

from .client import CHROME_UA, COUNTRY_HOSTS, DoctolibError

import httpx


class BookingError(DoctolibError):
    pass


def normalize_appointments(data: dict) -> dict:
    """Shape /account/appointments.json into a flat, recommendation-friendly view.

    A free function so both the in-browser session path and the explicit-cookie httpx path
    share it.
    """
    def norm(a: dict) -> dict:
        prof = a.get("profile") or {}
        practice = a.get("practice") or {}
        motive = a.get("visit_motive") or {}
        patient = a.get("patient") or {}
        # speciality is a string in the appointments payload but a {name,slug} dict in
        # search results — accept either.
        spec = prof.get("speciality")
        spec_name = spec if isinstance(spec, str) else (spec or {}).get("name")
        return {
            "id": a.get("id"),
            "start_date": a.get("start_date"),
            "end_date": a.get("end_date"),
            "practitioner": prof.get("name_with_title") or prof.get("name"),
            "speciality": prof.get("subtitle") or spec_name,
            "profile_path": prof.get("link"),
            "visit_motive": motive.get("name"),
            "telehealth": motive.get("telehealth"),
            "place": practice.get("name"),
            "address": ", ".join(
                x for x in (practice.get("address"), practice.get("zipcode"), practice.get("city")) if x
            ) or None,
            "insurance_sector": a.get("insurance_sector"),
            "patient": " ".join(x for x in (patient.get("first_name"), patient.get("last_name")) if x) or None,
            "status": (
                "confirmed" if a.get("confirmed")
                else "awaiting_practitioner" if a.get("awaiting_confirmation_by_practitioner")
                else "canceled" if a.get("canceled")
                else "past" if a.get("past") else "unknown"
            ),
            "cancelable": a.get("warn_late_modification") is not None and not a.get("past"),
        }

    return {
        "confirmed": [norm(a) for a in data.get("confirmed", [])],
        "pending": [norm(a) for a in data.get("pending", [])],
        "done": [norm(a) for a in data.get("done", [])],
        "has_more_past": data.get("has_next_page", False),
    }


def appointments_via_cookie(cookie: str, country: str = "de") -> dict:
    """Read /account/appointments.json with an explicit session cookie (httpx).

    The lighter escape hatch behind `my_appointments`'s `cookie` / DOCTOLIB_COOKIE override.
    The default path goes through the login session instead (session.authed_request).
    """
    if country not in COUNTRY_HOSTS:
        raise BookingError(f"unsupported country {country!r}")
    base = COUNTRY_HOSTS[country]
    r = httpx.get(
        base + "/account/appointments.json",
        headers={"User-Agent": CHROME_UA, "Accept": "application/json",
                 "Referer": base + "/", "Cookie": cookie},
        timeout=30.0,
    )
    if r.status_code in (401, 403):
        raise BookingError("the provided session cookie is missing or expired")
    if r.status_code >= 400:
        raise BookingError(f"HTTP {r.status_code}: {r.text[:200]}")
    return normalize_appointments(r.json().get("data", {}))


class DoctolibBookingClient:
    """The appointment-booking funnel, run inside one logged-in browser session.

    Auth is the dedicated login profile (`doctolib login`); there is no cookie parameter,
    because the funnel needs a live browser session for CSRF + token rotation + draft state,
    not a replayed cookie. Use as a context manager so the session opens and closes cleanly:

        with DoctolibBookingClient("de") as c:
            c.book(...)
    """

    def __init__(self, country: str = "de") -> None:
        if country not in COUNTRY_HOSTS:
            raise BookingError(f"unsupported country {country!r}")
        self.country = country
        self.base = COUNTRY_HOSTS[country]
        self._browser = None

    def __enter__(self) -> "DoctolibBookingClient":
        from . import session as _session
        self._browser = _session.AuthedBrowser(self.country).__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser is not None:
            self._browser.__exit__(*exc)
            self._browser = None

    def _call(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        body = self._browser.request(method, path, json_body=json_body)
        # The funnel signals some failures as a 200 with an `errors` array, not an HTTP code.
        if isinstance(body, dict) and body.get("errors"):
            raise BookingError(f"{path} rejected: {body['errors']}")
        return body

    # ------------------------------------------------------------------ steps

    def select_slot(
        self,
        start_date: str,
        profile_id: int,
        visit_motive_ids: list[int],
        agenda_ids: list[int],
        practice_ids: list[int],
        insurance_sector: Optional[str] = None,
        new_patient: bool = True,
        master_patient_uuid: Optional[str] = None,
        source_action: str = "profile",
    ) -> dict:
        """Step 1 — create the draft hold. `start_date` is the full ISO slot timestamp."""
        payload: dict[str, Any] = {
            "source_action": source_action,
            "start_date": start_date,
            "profile_id": profile_id,
            "insurance_sector": insurance_sector,
            "visit_motive_ids": visit_motive_ids,
            "new_patient": new_patient,
            "agenda_ids": agenda_ids,
            "practice_ids": practice_ids,
        }
        if master_patient_uuid:
            payload["master_patient_uuid"] = master_patient_uuid
        return self._call("POST", "/online_booking/api/slot_selection_funnel/v1/select_slot", payload)

    def patient_info(self) -> dict:
        """The draft's own view (draft details, or a `redirection` when auto-resolved).

        NOT the source of bookable patients — see `master_patients()`. Kept for diagnostics.
        """
        return self._call("GET", "/online_booking/api/patient_selection/v1/info")

    def master_patients(self) -> list[dict]:
        """Step 2a — the account's bookable patients (the picker's real source).

        Each carries `signed_id` (what `select` needs), `uuid`, `kind` ("main" = the account
        holder), name and insurance_sector.
        """
        r = self._call("GET", "/account/master_patients.json")
        return r if isinstance(r, list) else (r.get("data") or [])

    def abort_draft(self) -> bool:
        """Tear down the current draft hold. Best-effort and idempotent — a draft is only a
        temporary slot hold, never a confirmed appointment, and deleting a non-existent one
        is a no-op. Returns True if the delete succeeded."""
        try:
            self._browser.request("DELETE", "/online_booking/draft.json")
            return True
        except Exception:
            return False

    def select_patient(self, master_patient_signed_id: str) -> dict:
        """Step 2b — choose who the appointment is for."""
        return self._call("POST", "/online_booking/api/patient_selection/v1/select",
                          {"master_patient_id": master_patient_signed_id})

    def patient_form(self, master_patient_uuid: Optional[str] = None) -> dict:
        """Step 3a — the server-prefilled intake form, including practice custom fields."""
        path = "/online_booking/api/checkout/v1/patient_form"
        if master_patient_uuid:
            path += f"?master_patient_uuid={master_patient_uuid}"
        return self._call("GET", path)

    def patient_confirm(self, master_patient: dict, draft: dict) -> dict:
        """Step 3b — **THIS COMMITS THE APPOINTMENT.**"""
        return self._call("POST", "/online_booking/api/checkout/v1/patient_confirm",
                          {"master_patient": master_patient, "draft": draft})

    def confirmation(self) -> dict:
        """Step 4 — final state. May report `waiting_for_ack` while the practice acknowledges."""
        return self._call("GET", "/online_booking/api/confirmation/v1/booking")

    # ------------------------------------------------------------------- flow

    def book(
        self,
        start_date: str,
        profile_id: int,
        visit_motive_ids: list[int],
        agenda_ids: list[int],
        practice_ids: list[int],
        insurance_sector: Optional[str] = None,
        new_patient: bool = True,
        patient_name: Optional[str] = None,
        resume_url: Optional[str] = None,
    ) -> dict:
        """Run the whole funnel. Assumes the caller has already gated on user consent.

        `patient_name`: if the account has several patients (family members), pick the one
        whose name contains this. With one patient it is chosen automatically; with several
        and no hint, this raises rather than guessing who is seeing the doctor.
        """
        trace: list[str] = []

        draft = self.select_slot(
            start_date=start_date, profile_id=profile_id, visit_motive_ids=visit_motive_ids,
            agenda_ids=agenda_ids, practice_ids=practice_ids,
            insurance_sector=insurance_sector, new_patient=new_patient,
        )
        trace.append("select_slot: draft created")

        # From here on a hold exists. On any failure, tear it down so it can't linger.
        try:
            patients = self.master_patients()
            trace.append(f"master_patients: {len(patients)} on account")

            chosen = self._pick_patient(patients, patient_name)
            signed_id = chosen.get("signed_id")
            if not signed_id:
                raise BookingError(f"master patient has no signed_id: {list(chosen)}")
            self.select_patient(signed_id)
            trace.append(f"patient_selection/select: {chosen.get('first_name')} {chosen.get('last_name')}")

            form = self.patient_form(chosen.get("uuid"))
            trace.append("checkout/patient_form: ok")

            master_patient, draft_payload = self._build_confirm_payload(form, profile_id, new_patient)
            result = self.patient_confirm(master_patient, draft_payload)
            trace.append("checkout/patient_confirm: COMMITTED")

            final = self.confirmation()
            return {
                "booked": True,
                "trace": trace,
                "confirm_response": result,
                "confirmation": final,
                "warning": "A real appointment now exists. Eren is expected to attend in person.",
            }
        except Exception as e:
            # Self-clean: release the draft so the held slot frees immediately and no stale
            # draft blocks a later booking. Safe — a draft is never a confirmed appointment.
            draft_cleaned = self.abort_draft()
            trace.append(f"abort_draft: {'released' if draft_cleaned else 'nothing to release / failed'}")
            return {
                "booked": False,
                "trace": trace,
                "error": str(e),
                "draft_cleaned": draft_cleaned,
                "resume_url": resume_url,
                "warning": (
                    "The funnel failed after the draft was created. The temporary hold was torn "
                    "down (see draft_cleaned) so it will not block later attempts. Do NOT blindly retry."
                ),
            }

    @staticmethod
    def _pick_patient(patients: list[dict], hint: Optional[str]) -> dict:
        if not patients:
            raise BookingError("no bookable patient on this account")
        names = [
            "{} {}".format(p.get("first_name") or "", p.get("last_name") or "").strip()
            for p in patients
        ]
        if hint:
            h = hint.strip().lower()
            matches = [p for p, n in zip(patients, names) if h in n.lower()]
            if len(matches) == 1:
                return matches[0]
            raise BookingError(f"patient hint {hint!r} matched {len(matches)} of {names}")
        if len(patients) == 1:
            return patients[0]
        mains = [p for p in patients if p.get("kind") == "main"]
        if len(mains) == 1:
            return mains[0]
        raise BookingError(
            "several patients on this account. Pass patient_name to say who this "
            f"appointment is for: {names}"
        )

    @staticmethod
    def _build_confirm_payload(form: dict, profile_id: int, new_patient: bool) -> tuple[dict, dict]:
        """Echo the server's own prefill back at it.

        Practice-specific custom fields and qualification questions are part of a medical
        intake form; we submit exactly what the server prefilled and never invent values.
        If the practice requires a field the server left blank, the confirm call fails and
        the user finishes in a browser — which is the correct outcome, not a bug to patch
        by guessing.
        """
        appt = form.get("appointment") or form.get("draft") or {}
        patient = form.get("patient") or {}

        master_patient = {
            k: patient[k]
            for k in (
                "gender", "biological_sex", "first_name", "last_name", "maiden_name",
                "email", "phone_number", "birthdate", "city_of_birth_id",
                "country_of_birth_id", "place_of_birth_unknown", "address", "zipcode",
                "city", "national_health_identifier",
            )
            if k in patient and patient[k] is not None
        }

        draft_payload: dict[str, Any] = {
            "new_patient": appt.get("new_patient", new_patient),
            "custom_fields_values": appt.get("custom_fields_values") or {},
            "qualification_answers": appt.get("qualification_answers") or {},
            "profile_id": appt.get("profile_id", profile_id),
        }
        return master_patient, draft_payload
