"""Parsing correctness in DoctolibClient — the failures here are silently wrong answers.

A transposed bounding box or a None leaking into a filter would not crash; it would return
the wrong city or the wrong result set with no error at all, which is worse than a crash.
"""
from __future__ import annotations

import json

import httpx
import pytest

from doctolib_mcp.client import DoctolibClient, DoctolibError


def _client() -> DoctolibClient:
    c = DoctolibClient(country="de")
    return c


# --------------------------------------------------------------------------- #
# resolve_place / geocoding
# --------------------------------------------------------------------------- #

def test_resolve_place_maps_boundingbox_onto_viewport_without_transposition(make_client):
    """Nominatim's boundingbox is ordered [south, north, west, east]. A transposition
    silently returns the wrong city; the four numbers below are deliberately mutually
    distinguishable so a lat/lng or N/S swap changes the assertion."""
    nominatim = [{
        "lat": "52.5170", "lon": "13.3889",
        "boundingbox": ["52.3500", "52.6800", "13.0900", "13.7600"],
    }]
    c = make_client(lambda req: httpx.Response(200, json=nominatim))
    place = c.resolve_place("Berlin")
    assert place["viewport"]["northeast"] == {"lat": 52.68, "lng": 13.76}  # north, east
    assert place["viewport"]["southwest"] == {"lat": 52.35, "lng": 13.09}  # south, west
    assert place["gpsPoint"] == {"lat": 52.517, "lng": 13.3889}


def test_resolve_place_raises_doctoliberror_on_empty_geocoder_result(make_client):
    c = make_client(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(DoctolibError):
        c.resolve_place("Nirwana")


# --------------------------------------------------------------------------- #
# _normalise_provider
# --------------------------------------------------------------------------- #

def test_normalise_provider_extracts_slug_from_link_with_query_string():
    p = _client()._normalise_provider({"link": "/medecin/dr-anna-mueller?ref=search", "name": "Müller"})
    assert p["profile_slug"] == "dr-anna-mueller"
    assert p["profile_url"].endswith("/medecin/dr-anna-mueller?ref=search")


def test_normalise_provider_strips_trailing_slash_from_link():
    p = _client()._normalise_provider({"link": "/zahnarzt/dr-schmidt/", "name": "Schmidt"})
    assert p["profile_slug"] == "dr-schmidt"


def test_normalise_provider_returns_none_slug_and_url_when_link_absent():
    p = _client()._normalise_provider({"name": "Schmidt"})
    assert p["profile_slug"] is None
    assert p["profile_url"] is None


def test_normalise_provider_prefers_booking_agendaid_over_motive():
    p = _client()._normalise_provider({
        "matchedVisitMotive": {"visitMotiveId": 1, "agendaIds": [201]},
        "onlineBooking": {"agendaIds": [101, 102]},
    })
    assert p["agenda_ids"] == [101, 102]


def test_normalise_provider_falls_back_to_motive_agendaid():
    p = _client()._normalise_provider({
        "matchedVisitMotive": {"visitMotiveId": 1, "agendaIds": [301]},
        "onlineBooking": {},
    })
    assert p["agenda_ids"] == [301]


def test_normalise_provider_yields_empty_list_when_no_agendaid_anywhere():
    p = _client()._normalise_provider({"matchedVisitMotive": {"visitMotiveId": 1}})
    assert p["agenda_ids"] == []


def test_normalise_provider_joins_names_without_leading_or_doubled_space():
    n = _client()._normalise_provider
    assert n({"firstName": "Anna", "name": "Müller"})["name"] == "Anna Müller"
    assert n({"name": "Müller"})["name"] == "Müller"
    assert n({"firstName": "Anna"})["name"] == "Anna"


# --------------------------------------------------------------------------- #
# search — outgoing body
# --------------------------------------------------------------------------- #

def test_search_propagates_supplied_filters_and_keeps_telehealth_false(make_client):
    """`telehealth is False` must travel as False, not be dropped like an unset None.
    The code tests `is not None`, so this distinction is load-bearing."""
    captured: dict = {}

    def handler(req):
        captured["body"] = json.loads(req.content) if req.content else {}
        return httpx.Response(206, json={"total": 0, "healthcareProviders": []})

    c = make_client(handler)
    c.search("hausarzt", place={"name": "Berlin"}, insurance_sector="public",
             languages=["tr", "gb"], telehealth=False)
    filters = captured["body"]["filters"]
    assert filters["insuranceSector"] == "public"
    assert filters["languages"] == ["tr", "gb"]
    assert filters["telehealth"] is False  # not omitted, not None


def test_search_omits_telehealth_entirely_when_not_supplied(make_client):
    captured: dict = {}

    def handler(req):
        captured["body"] = json.loads(req.content) if req.content else {}
        return httpx.Response(206, json={"total": 0, "healthcareProviders": []})

    c = make_client(handler)
    c.search("hausarzt", place={"name": "Berlin"}, telehealth=None)
    assert "telehealth" not in captured["body"]["filters"]


def test_search_raises_when_neither_city_nor_place_given():
    with pytest.raises(DoctolibError):
        _client().search("hausarzt")  # city and place both default to None


# --------------------------------------------------------------------------- #
# practitioner — robustness to partial payloads
# --------------------------------------------------------------------------- #

def test_practitioner_tolerates_payload_missing_motives_agendas_places(make_client):
    payload = {"data": {"profile": {"id": 1, "name_with_title": "Dr. X"}}}
    c = make_client(lambda req: httpx.Response(200, json=payload))
    out = c.practitioner("dr-x")
    assert out["profile_id"] == 1
    assert out["visit_motives"] == []
    assert out["agendas"] == []
    assert out["places"] == []
