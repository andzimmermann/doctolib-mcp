"""CLI mirror of the MCP tools — the recon/debug surface.

Same pattern as apthunt: everything is reachable without an MCP client, so a broken
tool can be reproduced in one shell command.
"""
from __future__ import annotations

import json
from typing import Optional

import typer

from .client import DoctolibClient

app = typer.Typer(add_completion=False, help="Doctolib read-only search")


def _out(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False))


def _client(country: str) -> DoctolibClient:
    """Authenticated with the login session if one is live, else anonymous — mirrors the
    MCP read tools so the CLI sees the same personalised results."""
    from .session import try_cookie
    return DoctolibClient(country=country, cookie=try_cookie(country))


@app.command()
def earliest(
    specialty: str,
    city: str = "Berlin",
    within_days: int = 14,
    insurance: Optional[str] = None,
    languages: Optional[str] = None,
    limit: int = 10,
    country: str = "de",
) -> None:
    """Soonest bookable appointments for a specialty in a city."""
    langs = [s.strip() for s in languages.split(",")] if languages else None
    with _client(country) as c:
        _out(c.earliest(specialty, city=city, within_days=within_days,
                        insurance_sector=insurance, languages=langs, limit=limit))


@app.command()
def search(
    specialty: str,
    city: str = "Berlin",
    insurance: Optional[str] = None,
    page: int = 0,
    country: str = "de",
) -> None:
    """Search practitioners."""
    with _client(country) as c:
        _out(c.search(specialty, city=city, insurance_sector=insurance, page=page))


@app.command()
def practitioner(profile_slug: str, country: str = "de") -> None:
    """Visit motives, agendas and places for one practitioner."""
    with _client(country) as c:
        _out(c.practitioner(profile_slug))


@app.command()
def login(country: str = "de", timeout: int = 300) -> None:
    """Open a browser, sign into Doctolib once, and persist the session for the tools."""
    from .session import login as do_login
    _out(do_login(country=country, timeout_s=timeout))


@app.command()
def status(country: str = "de") -> None:
    """Report whether the stored Doctolib session is currently live."""
    from .session import status as do_status
    _out(do_status(country=country))


@app.command()
def appointments(country: str = "de") -> None:
    """Show Eren's own Doctolib appointments (needs a logged-in session)."""
    from .session import authed_request
    from .booking import normalize_appointments
    data = authed_request("GET", "/account/appointments.json", country=country)
    _out(normalize_appointments(data.get("data", {})))


@app.command("abort-draft")
def abort_draft(country: str = "de") -> None:
    """Release any lingering booking draft (temporary slot hold). Safe / idempotent."""
    from .session import authed_request
    try:
        authed_request("DELETE", "/online_booking/draft.json", country=country)
        _out({"aborted": True})
    except Exception as e:
        _out({"aborted": False, "note": str(e)[:150]})


@app.command()
def cancel(
    appointment_id: str,
    reason: Optional[str] = None,
    message: Optional[str] = None,
    confirm: bool = False,
    country: str = "de",
) -> None:
    """Cancel a real appointment. Dry-run unless --confirm is passed (BINDING, irreversible)."""
    from .session import authed_request
    reasons = authed_request(
        "GET", f"/appointment_modification_reasons/cancellations?appointment_id={appointment_id}",
        country=country,
    )
    if not confirm:
        _out({"would_cancel": True, "appointment_id": appointment_id, "reason": reason,
              "valid_reasons": reasons,
              "note": "PREVIEW: nothing cancelled. Re-run with --confirm to actually cancel."})
        return
    _out(authed_request("DELETE", f"/account/appointments/{appointment_id}",
                        country=country, json_body={"reason": reason, "message": message}))


@app.command()
def slots(
    visit_motive_id: int,
    agenda_ids: str,
    practice_id: int,
    start_date: Optional[str] = None,
    days: int = 14,
    country: str = "de",
) -> None:
    """Open slots for one motive/agenda/practice triple."""
    ids = [int(a) for a in agenda_ids.replace("-", ",").split(",") if a.strip()]
    with _client(country) as c:
        _out(c.availabilities(visit_motive_id, ids, practice_id, start_date=start_date, days=days))


if __name__ == "__main__":
    app()
