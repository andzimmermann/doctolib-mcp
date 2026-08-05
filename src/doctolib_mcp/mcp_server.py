"""MCP server over Doctolib's internal JSON API.

Run:  python -m doctolib_mcp.mcp_server   (stdio)

Most of these tools read. Two of them do not: book_appointment commits a real clinical
slot under Eren's name, and cancel_appointment releases one irreversibly. Both default to
confirm=False, which performs no write and returns the payload that would be sent, so the
agent can read the appointment back to him and get an explicit yes first. Neither should
ever be called to "check whether it works". Read docs/safety.md before changing either.

The safe alternative is always available: every result carries `profile_url`, and
`booking_url` turns that into a one-click handoff so a human finishes in a browser.

Built on `MCPServer`, the high-level API the `mcp` package exposes since 2.0.0 — it
replaced the bundled `mcp.server.fastmcp.FastMCP`, which 2.0.0 removed.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from mcp.server import MCPServer

from .client import DoctolibClient

# httpx logs every request at INFO; on stdio that buries the useful stderr.
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer("doctolib")


def _read_client(country: str) -> DoctolibClient:
    """A read client authenticated with the login session when one is live, else anonymous.

    Now that a session exists, the public reads go through it too, so results reflect the
    backend's patient-level personalisation. Falls back to anonymous silently when there is
    no login — the tools still work, just without the personalised view.
    """
    from . import session as _session
    return DoctolibClient(country=country, cookie=_session.try_cookie(country))

# Slugs Doctolib uses as search keywords. Free text also works; these are just the ones
# worth naming so the model does not have to guess German medical vocabulary.
SPECIALTY_SLUGS = {
    "allgemeinmedizin": "GP / family doctor",
    "hausarztlich-tatige-internist-in": "internist working as GP",
    "zahnarzt": "dentist",
    "chirurg": "surgeon",
    "handchirurg": "hand surgeon",
    "orthopade": "orthopaedist",
    "dermatologe": "dermatologist",
    "augenarzt": "ophthalmologist",
    "hno-arzt": "ENT",
    "gynakologe": "gynaecologist",
    "psychotherapeut": "psychotherapist",
    "radiologe": "radiologist",
    "urologe": "urologist",
    "kardiologe": "cardiologist",
    "physiotherapeut": "physiotherapist",
}


@mcp.tool()
def list_specialties() -> dict:
    """Common Doctolib specialty slugs to use as the `specialty` argument.

    Any free-text keyword also works; this is a convenience list, not a whitelist.
    """
    return {"specialties": SPECIALTY_SLUGS, "note": "free-text keywords are also accepted"}


@mcp.tool()
def find_earliest_appointment(
    specialty: str,
    city: str = "Berlin",
    within_days: int = 14,
    insurance_sector: Optional[str] = None,
    languages: Optional[str] = None,
    telehealth: Optional[bool] = None,
    limit: int = 10,
    country: str = "de",
) -> dict:
    """Find the soonest bookable appointments for a specialty in a city.

    Searches practitioners, then checks each one's open slots in parallel and returns
    them sorted by earliest slot. This is the main tool — use it instead of chaining
    search_doctors and get_availabilities by hand.

    insurance_sector: 'public' (gesetzlich) or 'private'.
    languages: comma-separated Doctolib codes, e.g. 'tr,gb' for Turkish/English.
    """
    langs = [s.strip() for s in languages.split(",")] if languages else None
    with _read_client(country) as c:
        return c.earliest(
            specialty, city=city, within_days=within_days,
            insurance_sector=insurance_sector, languages=langs,
            telehealth=telehealth, limit=limit,
        )


@mcp.tool()
def search_doctors(
    specialty: str,
    city: str = "Berlin",
    insurance_sector: Optional[str] = None,
    languages: Optional[str] = None,
    telehealth: Optional[bool] = None,
    page: int = 0,
    country: str = "de",
) -> dict:
    """Search practitioners without checking availability.

    Each result carries visit_motive_id, agenda_ids and practice_id — the triple
    get_availabilities needs.
    """
    langs = [s.strip() for s in languages.split(",")] if languages else None
    with _read_client(country) as c:
        return c.search(
            specialty, city=city, insurance_sector=insurance_sector,
            languages=langs, telehealth=telehealth, page=page,
        )


@mcp.tool()
def get_practitioner(profile_slug: str, country: str = "de") -> dict:
    """All visit motives, agendas and practice locations for one practitioner.

    Use when the motive matched by search is not the one wanted — e.g. search matched an
    acute-care slot but a first-visit motive is needed, which often has different
    availability entirely.
    """
    with _read_client(country) as c:
        return c.practitioner(profile_slug)


@mcp.tool()
def get_availabilities(
    visit_motive_id: int,
    agenda_ids: str,
    practice_id: int,
    start_date: Optional[str] = None,
    days: int = 14,
    country: str = "de",
) -> dict:
    """Open slots for one motive/agenda/practice triple.

    agenda_ids: comma-separated ids from search_doctors or get_practitioner.
    A `reason` of 'not_opened_availability' means the practice has not released slots
    that far ahead yet — which is different from being fully booked.
    """
    ids = [int(a) for a in str(agenda_ids).replace("-", ",").split(",") if a.strip()]
    with _read_client(country) as c:
        return c.availabilities(visit_motive_id, ids, practice_id, start_date=start_date, days=days)


@mcp.tool()
def my_appointments(cookie: Optional[str] = None, country: str = "de") -> dict:
    """Eren's own Doctolib appointments — confirmed, pending and past.

    Read-only, but authenticated: it returns his real medical calendar. Use it to factor
    existing appointments into scheduling advice, so as to avoid clashes and spot follow-ups
    that are already booked.

    Auth comes from the dedicated Doctolib login profile. If there is no live session this
    opens a browser window for him to sign into and then answers, so there is no need to
    tell him to run anything first. That can take a minute or two of real time. An explicit
    `cookie` or DOCTOLIB_COOKIE overrides all of it and takes a lighter path.
    """
    explicit = cookie or os.environ.get("DOCTOLIB_COOKIE")
    if explicit:
        from .booking import appointments_via_cookie
        return appointments_via_cookie(explicit, country)
    # No explicit cookie: read inside the logged-in browser context (most robust).
    from . import session as _session
    from .booking import normalize_appointments
    data = _session.authed_request("GET", "/account/appointments.json", country=country)
    return normalize_appointments(data.get("data", {}))


@mcp.tool()
def session_status(country: str = "de") -> dict:
    """Whether the dedicated Doctolib login profile currently holds a live session.

    Reports only. It never opens a login window itself, so it is the safe way to check.

    A logged_in=false here does not mean the authenticated tools are unusable: they open the
    login window themselves and retry once the user signs in. Call one of them and let it
    handle it. Unattended callers that cannot show a window set DOCTOLIB_AUTO_LOGIN=0 and get
    an immediate failure instead.
    """
    from . import session as _session
    return _session.status(country)


@mcp.tool()
def cancel_appointment(
    appointment_id: str,
    reason: Optional[str] = None,
    message: Optional[str] = None,
    confirm: bool = False,
    country: str = "de",
) -> dict:
    """⚠️ BINDING ACTION FOR THE USER — THIS CANCELS A REAL APPOINTMENT. ⚠️

    Cancelling releases Eren's slot back to the practice. It is irreversible — the slot may
    be taken immediately, and at some practices (e.g. a fertility clinic) rebooking can mean
    a long wait. If the appointment is within ~24h it is a late cancellation, which some
    practices penalise (the appointment's `warn_late_modification` flag marks this).

    DO NOT call with confirm=True unless the user has, in this conversation, explicitly told
    you to cancel this specific appointment. Read the appointment back to them (who, when,
    where) and get an explicit yes first.

    confirm=False (default) performs NO write: it fetches the valid cancellation reasons and
    returns exactly what a real cancel would send, for you to confirm with the user. Only
    confirm=True issues the DELETE.

    `appointment_id` is the signed id from my_appointments. `reason` should be one of the
    codes returned in the preview's `valid_reasons`; `message` is an optional free-text note.
    Auth comes from the login session (same as my_appointments/booking).
    """
    from . import session as _session

    reasons = None
    try:
        reasons = _session.authed_request(
            "GET", f"/appointment_modification_reasons/cancellations?appointment_id={appointment_id}",
            country=country,
        )
    except Exception as e:
        reasons = {"_error": str(e)[:200]}

    if not confirm:
        return {
            "would_cancel": True,
            "confirm": False,
            "appointment_id": appointment_id,
            "reason": reason,
            "message": message,
            "valid_reasons": reasons,
            "endpoint": f"DELETE /account/appointments/{appointment_id}",
            "warning": (
                "PREVIEW ONLY — nothing was cancelled. This would irreversibly cancel a REAL "
                "appointment. Confirm who/when/where with the user and get an explicit yes, "
                "then call again with confirm=True."
            ),
        }

    result = _session.authed_request(
        "DELETE", f"/account/appointments/{appointment_id}",
        country=country, json_body={"reason": reason, "message": message},
    )
    return {
        "canceled": True,
        "appointment_id": appointment_id,
        "response": result,
        "warning": "The appointment is now cancelled and the slot released. This cannot be undone.",
    }


@mcp.tool()
def abort_booking_draft(country: str = "de") -> dict:
    """Release any lingering booking draft (a temporary slot hold) on the session.

    Safe and idempotent: a draft is only a temporary hold created mid-booking, never a
    confirmed appointment — clearing it just frees the held slot early. Use it after a failed
    or abandoned booking so the hold doesn't sit on a slot or block a later attempt. No-op if
    there is no draft. (book_appointment already self-cleans on failure; this is the manual
    escape hatch.)
    """
    from . import session as _session
    try:
        _session.authed_request("DELETE", "/online_booking/draft.json", country=country)
        return {"aborted": True}
    except Exception as e:
        return {"aborted": False, "note": f"no draft to release, or delete failed: {str(e)[:150]}"}


@mcp.tool()
def book_appointment(
    start_date: str,
    profile_id: int,
    visit_motive_ids: str,
    agenda_ids: str,
    practice_ids: str,
    confirm: bool = False,
    insurance_sector: Optional[str] = None,
    new_patient: bool = True,
    patient_name: Optional[str] = None,
    resume_url: Optional[str] = None,
    country: str = "de",
) -> dict:
    """⚠️ BINDING ACTION FOR THE USER — THEY WILL ACTUALLY REALLY NEED TO GO THERE. ⚠️

    This books a REAL medical appointment at a REAL practice under Eren's real name. It is
    not a reservation, a query, or a draft. If this succeeds, a clinic has put him in their
    calendar and a human being expects him to physically show up at that address at that
    time. Missing it wastes a clinician's slot, can incur a no-show fee, and can get a
    patient flagged at that practice.

    DO NOT call this to "check if booking works", to explore the API, or as a follow-up to
    a search unless the user has asked for this specific appointment in this conversation.
    Confirm the practitioner, the date, the time and the visit motive with the user in
    plain language and get an explicit yes BEFORE calling with confirm=True.

    Call with confirm=False first (the default): that performs NO writes and returns
    exactly what would be sent, for you to read back to the user. Only pass confirm=True
    after they have agreed to that specific slot.

    Auth comes from the dedicated login profile (`doctolib login`), same as my_appointments
    and cancel — there is no way to book without a live session.

    Note: the whole funnel is reconstructed from Doctolib's JavaScript and has never been
    executed end to end (that can't be tested without a real booking). Report failures
    verbatim rather than retrying; a failed run may leave a temporary hold that expires on
    its own.

    Args as returned by search_doctors / find_earliest_appointment: `start_date` is the full
    ISO slot timestamp; the three id arguments are comma-separated.
    """
    ints = lambda s: [int(x) for x in str(s).replace("-", ",").split(",") if str(x).strip()]
    motives, agendas, practices = ints(visit_motive_ids), ints(agenda_ids), ints(practice_ids)

    if not confirm:
        return {
            "would_book": True,
            "confirm": False,
            "warning": (
                "PREVIEW ONLY — nothing was sent. This would book a REAL appointment the "
                "user must physically attend. Read the details back to them and get an "
                "explicit yes before calling again with confirm=True."
            ),
            "payload": {
                "start_date": start_date, "profile_id": profile_id,
                "visit_motive_ids": motives, "agenda_ids": agendas,
                "practice_ids": practices, "insurance_sector": insurance_sector,
                "new_patient": new_patient, "patient_name": patient_name,
            },
        }

    from .booking import DoctolibBookingClient

    with DoctolibBookingClient(country=country) as c:
        return c.book(
            start_date=start_date, profile_id=profile_id, visit_motive_ids=motives,
            agenda_ids=agendas, practice_ids=practices, insurance_sector=insurance_sector,
            new_patient=new_patient, patient_name=patient_name, resume_url=resume_url,
        )


@mcp.tool()
def booking_url(profile_slug: str, country: str = "de") -> dict:
    """The URL a human opens to book with this practitioner in a browser.

    The safe alternative to book_appointment: this only hands Eren a link, it commits
    nothing. Prefer it whenever he has not explicitly asked you to book a named slot.
    """
    with DoctolibClient(country=country) as c:
        p = c.practitioner(profile_slug)
        fallback = f"{c.base}/booking/{profile_slug}"
    return {
        "name": p["name"],
        "booking_url": p["profile_url"] or fallback,
        "note": "Open in a browser and complete the booking manually.",
    }


if __name__ == "__main__":
    mcp.run()
