"""Client for Doctolib's internal JSON API.

There is no public Doctolib API — the official one is partner-only (medical software
vendors, hospital information systems). What exists is the JSON the patient frontend
talks to. Three endpoints carry the whole read path:

    POST /patient-health-search/api/v1/hcp/search?page=N   -> practitioners
    GET  /online_booking/api/slot_selection_funnel/v1/info.json?profile_slug=  -> motives/agendas
    GET  /availabilities.json?visit_motive_ids&agenda_ids&practice_ids&start_date&limit -> slots

Two things about this surface are worth knowing before touching it:

DataDome. The site is behind DataDome, but as of 2026-08 it only rejects obviously
non-browser clients — a default curl/python User-Agent gets a 403 JS challenge, while a
normal Chrome UA plus a Referer sails through on the JSON endpoints. No cookie, no
browser, no TLS impersonation needed. That is a behavioural observation, not a contract:
if these start returning 403, the fix is a real browser session, not more header
guessing. Note the *HTML* pages are blocked harder than the JSON, so never scrape markup.

Geocoding. The search body wants `location.place.viewport`, a bounding box. Doctolib's
own frontend fills this from Google Places (the `placeId: "ChIJ..."` in its requests is a
Google Place ID), so any geocoder that yields a bbox works. We use Nominatim: free, no
key. Passing only lat/lng without a viewport silently returns nationwide results — the
filter is the box, so a missing box is not an error, just wrong answers.

This module is read-only. The write path (booking, cancelling) lives in booking.py and
session.py, needs a real logged-in browser session, and is guarded by an explicit
confirmation argument. See docs/safety.md before touching it.
"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

# Doctolib caps a single availabilities call at a couple of weeks; longer horizons are
# walked by advancing start_date rather than raising limit.
MAX_DAYS_PER_CALL = 15

COUNTRY_HOSTS = {
    "de": "https://www.doctolib.de",
    "fr": "https://www.doctolib.fr",
    "it": "https://www.doctolib.it",
}


class DoctolibError(RuntimeError):
    pass


class _Throttle:
    """Per-host minimum interval with jitter. Shared across threads."""

    def __init__(self, min_interval: float = 0.6, jitter: float = 0.4) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
            if gap > 0:
                time.sleep(gap + random.uniform(0, self.jitter))
            self._last[host] = time.monotonic()


class DoctolibClient:
    """Read client for the public endpoints.

    Pass `cookie` (a logged-in session's Cookie header) to make the *same* read endpoints
    authenticated — the backend then personalises results to the patient: existing-patient
    ("Bestandspatient") visit motives, slots some practices only expose to logged-in known
    patients, the right insurance sector, personalised ranking. Anonymous still works and
    returns the public view; authenticated is strictly the more faithful one where a
    practice personalises at all. Tools source the cookie from the login session
    automatically and fall back to anonymous when there is none.
    """

    def __init__(self, country: str = "de", timeout: float = 30.0, cookie: Optional[str] = None) -> None:
        if country not in COUNTRY_HOSTS:
            raise DoctolibError(f"unsupported country {country!r}; try one of {sorted(COUNTRY_HOSTS)}")
        self.country = country
        self.base = COUNTRY_HOSTS[country]
        self.authenticated = bool(cookie)
        self._throttle = _Throttle()
        headers = {
            "User-Agent": CHROME_UA,
            "Accept": "application/json",
            "Accept-Language": f"{country}-{country.upper()},{country};q=0.9,en;q=0.8",
            "Referer": self.base + "/",
        }
        if cookie:
            headers["Cookie"] = cookie
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, headers=headers)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DoctolibClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- transport

    def _request(self, method: str, url: str, **kw: Any) -> Any:
        self._throttle.wait(url)
        r = self._http.request(method, url, **kw)
        if r.status_code == 403:
            raise DoctolibError(
                "403 from Doctolib: DataDome is now challenging this client. "
                "Header spoofing is no longer enough; drive a real browser session instead."
            )
        # The search endpoint answers 206 Partial Content on success (paged results).
        if r.status_code not in (200, 206):
            raise DoctolibError(f"HTTP {r.status_code} for {url}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise DoctolibError(f"non-JSON response from {url}: {r.text[:200]}") from e

    # ---------------------------------------------------------------- geocoding

    def resolve_place(self, city: str) -> dict:
        """Geocode a city/address into the `place` object Doctolib's search expects.

        The bounding box is what actually filters results, so we carry it verbatim.
        """
        r = self._http.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{city}, {self.country.upper()}", "format": "json", "limit": 1},
            headers={"User-Agent": "doctolib-mcp/0.1 (personal appointment search)"},
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            raise DoctolibError(f"could not geocode {city!r}")
        g = hits[0]
        south, north, west, east = (float(x) for x in g["boundingbox"])
        return {
            "name": city.title(),
            "slug": city.lower().replace(" ", "-"),
            "country": self.country,
            "type": "locality",
            "viewport": {
                "northeast": {"lat": north, "lng": east},
                "southwest": {"lat": south, "lng": west},
            },
            "gpsPoint": {"lat": float(g["lat"]), "lng": float(g["lon"])},
        }

    # ------------------------------------------------------------------ search

    def search(
        self,
        specialty: str,
        city: Optional[str] = None,
        place: Optional[dict] = None,
        insurance_sector: Optional[str] = None,
        languages: Optional[list[str]] = None,
        telehealth: Optional[bool] = None,
        page: int = 0,
    ) -> dict:
        """Search practitioners. `specialty` is a Doctolib slug ('allgemeinmedizin',
        'zahnarzt', 'chirurg', ...) or a free-text keyword.

        Returns the raw payload plus a `providers` list normalised to the ids the
        availabilities endpoint needs.
        """
        if place is None:
            if not city:
                raise DoctolibError("pass either city or place")
            place = self.resolve_place(city)

        filters: dict[str, Any] = {}
        if insurance_sector:
            filters["insuranceSector"] = insurance_sector
        if languages:
            filters["languages"] = languages
        if telehealth is not None:
            filters["telehealth"] = telehealth

        payload = {
            "keyword": specialty,
            "location": {"place": place},
            "filters": filters,
        }
        data = self._request(
            "POST",
            f"{self.base}/patient-health-search/api/v1/hcp/search",
            params={"page": page},
            json=payload,
        )
        return {
            "total": data.get("total"),
            "page": page,
            "authenticated": self.authenticated,
            "providers": [self._normalise_provider(h) for h in data.get("healthcareProviders", [])],
        }

    def _normalise_provider(self, h: dict) -> dict:
        motive = h.get("matchedVisitMotive") or {}
        booking = h.get("onlineBooking") or {}
        refs = h.get("references") or {}
        loc = h.get("location") or {}
        link = h.get("link") or ""
        slug = link.split("?")[0].rstrip("/").split("/")[-1] if link else None
        name = " ".join(p for p in (h.get("firstName"), h.get("name")) if p).strip()
        return {
            "name": name,
            "profile_slug": slug,
            "profile_url": (self.base + link) if link else None,
            "speciality": h.get("speciality") or h.get("subtitle"),
            "city": loc.get("city"),
            "address": loc.get("address"),
            "zipcode": loc.get("zipcode"),
            "languages": h.get("languages"),
            "telehealth": booking.get("telehealth"),
            "bookable_online": "onlineBooking" in (h.get("services") or []),
            # the three ids that unlock availabilities.json
            "visit_motive_id": motive.get("visitMotiveId"),
            "visit_motive_name": motive.get("name"),
            "agenda_ids": booking.get("agendaIds") or motive.get("agendaIds") or [],
            "practice_id": refs.get("practiceId"),
            "accepts_new_patients": motive.get("allowNewPatients"),
        }

    # ----------------------------------------------------------------- profile

    def practitioner(self, profile_slug: str, locale: Optional[str] = None) -> dict:
        """Visit motives, agendas and places for one practitioner.

        Use this when the motive matched by search is not the one you want — e.g. search
        matched 'Akutsprechstunde' but you need 'Erstuntersuchung Neupatient:in'.
        """
        data = self._request(
            "GET",
            f"{self.base}/online_booking/api/slot_selection_funnel/v1/info.json",
            params={"profile_slug": profile_slug, "locale": locale or self.country},
        )
        d = data.get("data", {})
        prof = d.get("profile", {})
        return {
            "profile_id": prof.get("id"),
            "name": prof.get("name_with_title"),
            "speciality": prof.get("subtitle"),
            "telehealth_enabled": prof.get("telehealth_enabled"),
            "profile_url": (self.base + prof["redirect_url"]) if prof.get("redirect_url") else None,
            "visit_motives": [
                {
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "telehealth": m.get("telehealth"),
                    "allow_new_patients": m.get("allow_new_patients"),
                }
                for m in d.get("visit_motives", [])
            ],
            "agendas": [
                {
                    "id": a.get("id"),
                    "practice_id": a.get("practice_id"),
                    "visit_motive_ids": a.get("visit_motive_ids"),
                }
                for a in d.get("agendas", [])
            ],
            "places": [
                {"id": p.get("id"), "name": p.get("name"), "address": p.get("address"),
                 "city": p.get("city"), "practice_ids": p.get("practice_ids")}
                for p in d.get("places", [])
            ],
        }

    # ------------------------------------------------------------ availability

    def availabilities(
        self,
        visit_motive_id: int,
        agenda_ids: list[int] | int,
        practice_id: int,
        start_date: Optional[str] = None,
        days: int = 14,
    ) -> dict:
        """Open slots for one motive/agenda/practice triple.

        Doctolib serves a limited window per call, so longer horizons are walked by
        advancing start_date. `reason: not_opened_availability` means the practice has
        not released that far ahead yet — distinct from genuinely being full.

        Doctolib rejects `limit < 2` (HTTP 400), so days is floored at 2 and each call's
        limit is floored at 2 as well — the latter also covers a trailing 1-day chunk on a
        long horizon (e.g. days=16 → 15 + 1).
        """
        if isinstance(agenda_ids, int):
            agenda_ids = [agenda_ids]
        start = start_date or date.today().isoformat()
        cursor = datetime.fromisoformat(start).date()
        remaining = max(2, days)
        out: list[dict] = []
        total = 0
        reason = None

        while remaining > 0:
            chunk = min(remaining, MAX_DAYS_PER_CALL)
            data = self._request(
                "GET",
                f"{self.base}/availabilities.json",
                params={
                    "start_date": cursor.isoformat(),
                    "visit_motive_ids": visit_motive_id,
                    "agenda_ids": "-".join(str(a) for a in agenda_ids),
                    "practice_ids": practice_id,
                    "limit": max(2, chunk),
                },
            )
            for day in data.get("availabilities", []):
                if day.get("slots"):
                    out.append({"date": day["date"], "slots": day["slots"]})
            total += data.get("total") or 0
            reason = data.get("reason") or reason
            cursor += timedelta(days=chunk)
            remaining -= chunk

        return {
            "total": total,
            "days_with_slots": out,
            "first_slot": out[0]["slots"][0] if out else None,
            "reason": reason,
        }

    # ------------------------------------------------------------- convenience

    def earliest(
        self,
        specialty: str,
        city: Optional[str] = None,
        place: Optional[dict] = None,
        within_days: int = 14,
        insurance_sector: Optional[str] = None,
        languages: Optional[list[str]] = None,
        telehealth: Optional[bool] = None,
        limit: int = 10,
        scan: int = 20,
    ) -> dict:
        """Search, then fan out over availabilities and return the soonest slots.

        This is the tool worth having: the website makes you open practitioners one at a
        time, which is exactly the part that does not scale when you want the first free
        appointment rather than a particular doctor.
        """
        found = self.search(
            specialty, city=city, place=place, insurance_sector=insurance_sector,
            languages=languages, telehealth=telehealth,
        )
        candidates = [
            p for p in found["providers"]
            if p["visit_motive_id"] and p["agenda_ids"] and p["practice_id"]
        ][:scan]

        def probe(p: dict) -> Optional[dict]:
            try:
                av = self.availabilities(
                    p["visit_motive_id"], p["agenda_ids"], p["practice_id"], days=within_days
                )
            except DoctolibError as e:
                return {**p, "error": str(e)[:120], "first_slot": None}
            return {
                **p,
                "first_slot": av["first_slot"],
                "slot_count": av["total"],
                "days_with_slots": av["days_with_slots"][:3],
                "reason": av["reason"],
            }

        with ThreadPoolExecutor(max_workers=4) as pool:
            probed = [r for r in pool.map(probe, candidates) if r]

        with_slots = sorted(
            (p for p in probed if p.get("first_slot")), key=lambda p: p["first_slot"]
        )
        return {
            "searched": len(candidates),
            "with_availability": len(with_slots),
            "total_matching_practitioners": found["total"],
            "authenticated": self.authenticated,
            "results": with_slots[:limit],
        }
