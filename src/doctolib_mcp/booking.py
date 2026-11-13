"""Stateful appointment booking through the dedicated authenticated browser.

select_slot creates a temporary hold; patient_confirm may commit an appointment. The
commit attempt is never automatically retried. Failure after dispatch is UNKNOWN, and a
successful commit followed by failed confirmation remains BOOKED, requiring account
verification. Only failures strictly before commit attempt clean up the temporary draft.
The upstream internal API has no stability guarantee and has not been live-validated here.
"""
from __future__ import annotations

from typing import Any, Optional

from .client import COUNTRY_HOSTS, DoctolibError



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
        patient_id: Optional[str] = None,
        resume_url: Optional[str] = None,
    ) -> dict:
        """Run the whole funnel. Assumes the caller has already gated on user consent.

        `patient_name`: if the account has several patients (family members), pick the one
        whose name contains this. With one patient it is chosen automatically; with several
        and no hint, this raises rather than guessing who is seeing the doctor.
        """
        trace: list[str] = []
        commit_attempted = False
        committed = False

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

            chosen = self._pick_patient(patients, patient_name, patient_id)
            signed_id = chosen.get("signed_id")
            if not signed_id:
                raise BookingError(f"master patient has no signed_id: {list(chosen)}")
            self.select_patient(signed_id)
            trace.append(f"patient_selection/select: {chosen.get('first_name')} {chosen.get('last_name')}")

            form = self.patient_form(chosen.get("uuid"))
            returned_patient = (form.get("patient") or {}).get("uuid")
            if returned_patient is not None and returned_patient != chosen.get("uuid"):
                raise BookingError("Patient form does not match the selected person")
            trace.append("checkout/patient_form: ok")

            master_patient, draft_payload = self._build_confirm_payload(form, profile_id, new_patient)
            commit_attempted = True
            result = self.patient_confirm(master_patient, draft_payload)
            committed = True
            trace.append("checkout/patient_confirm: COMMITTED")

            final = self.confirmation()
            return {
                "booked": True,
                "trace": trace,
                "confirm_response": result,
                "confirmation": final,
                "warning": "A real appointment now exists. The selected patient is expected to attend in person.",
            }
        except Exception as e:
            # Self-clean: release the draft so the held slot frees immediately and no stale
            # draft blocks a later booking. Safe — a draft is never a confirmed appointment.
            if commit_attempted:
                return {"booked": True if committed else None, "outcome": "committed_confirmation_unavailable" if committed else "unknown",
                        "trace": trace, "error": "Booking result needs account verification",
                        "draft_cleaned": False, "retry_allowed": False,
                        "warning": "Check my_appointments or the website. Never automatically repeat this booking."}
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
    def _pick_patient(patients: list[dict], hint: Optional[str], patient_id: Optional[str] = None) -> dict:
        if not patients:
            raise BookingError("no bookable patient on this account")
        if patient_id:
            matches = [p for p in patients if p.get("uuid") == patient_id]
            if len(matches) != 1:
                raise BookingError("Selected patient ID is absent or ambiguous in the logged-in account")
            return matches[0]
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
        if appt.get("profile_id", profile_id) != profile_id:
            raise BookingError("Patient form changed the selected practice profile")
        if appt.get("new_patient", new_patient) != new_patient:
            raise BookingError("Patient form changed the instructed new-patient status")

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
