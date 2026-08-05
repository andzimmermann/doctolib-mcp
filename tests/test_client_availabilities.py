"""availabilities() — chunked walking, merging, and the not_opened/full distinction.

Every test drives the client through an httpx.MockTransport that serves canned
availabilities payloads in order and records the (start_date, limit, agenda_ids) params
of each request, so the walk is observed without ever touching doctolib.de.
"""
from __future__ import annotations

from typing import Any

import httpx


def _recording_handler(responses: list[dict], requests: list[dict]):
    """Serve `responses` in order; record the params of each request into `requests`."""
    it = iter(responses)

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append({
            "start_date": req.url.params.get("start_date"),
            "limit": req.url.params.get("limit"),
            "agenda_ids": req.url.params.get("agenda_ids"),
        })
        return httpx.Response(200, json=next(it))

    return handler


# --------------------------------------------------------------------------- #
# Walk shape
# --------------------------------------------------------------------------- #

def test_short_horizon_issues_a_single_request(make_client, availabilities):
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities], requests))
    out = c.availabilities(55012, [101, 102], 901, start_date="2026-08-10", days=14)

    assert len(requests) == 1
    assert requests[0]["start_date"] == "2026-08-10"
    assert requests[0]["limit"] == "14"
    assert out["total"] == availabilities["total"]
    # The 2026-08-12 day in the fixture carries slots:[] and must be dropped.
    assert [d["date"] for d in out["days_with_slots"]] == ["2026-08-10", "2026-08-11", "2026-08-13"]
    assert out["first_slot"]["start_date"] == "2026-08-10T09:00:00.000+02:00"


def test_long_horizon_walks_in_15day_chunks_and_merges_days(make_client):
    requests: list[dict] = []
    chunk1 = {"total": 2, "reason": None,
              "availabilities": [{"date": "2026-08-10",
                                  "slots": [{"start_date": "2026-08-10T09:00:00.000+02:00"}]}]}
    chunk2 = {"total": 3, "reason": None,
              "availabilities": [{"date": "2026-08-25",
                                  "slots": [{"start_date": "2026-08-25T09:00:00.000+02:00"}]}]}
    c = make_client(_recording_handler([chunk1, chunk2], requests))
    out = c.availabilities(55012, [101], 901, start_date="2026-08-10", days=30)

    assert len(requests) == 2
    assert [r["start_date"] for r in requests] == ["2026-08-10", "2026-08-25"]  # +15 days
    assert [r["limit"] for r in requests] == ["15", "15"]
    assert out["total"] == 5  # summed across chunks
    assert [d["date"] for d in out["days_with_slots"]] == ["2026-08-10", "2026-08-25"]


# --------------------------------------------------------------------------- #
# Empty / not-opened handling
# --------------------------------------------------------------------------- #

def test_empty_slots_day_is_dropped(make_client, availabilities):
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=14)
    assert "2026-08-12" not in [d["date"] for d in out["days_with_slots"]]


def test_first_slot_is_none_when_nothing_open(make_client):
    requests: list[dict] = []
    empty = {"total": 0, "reason": "full", "availabilities": []}
    c = make_client(_recording_handler([empty, empty], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=30)
    assert out["first_slot"] is None
    assert out["days_with_slots"] == []
    assert out["total"] == 0


def test_first_slot_comes_from_a_later_chunk_when_earlier_chunks_empty(make_client):
    requests: list[dict] = []
    empty = {"total": 0, "reason": None, "availabilities": []}
    later = {"total": 1, "reason": None,
             "availabilities": [{"date": "2026-08-25",
                                 "slots": [{"start_date": "2026-08-25T08:00:00.000+02:00"}]}]}
    c = make_client(_recording_handler([empty, later], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=30)
    assert out["first_slot"]["start_date"] == "2026-08-25T08:00:00.000+02:00"


# --------------------------------------------------------------------------- #
# Aggregation across chunks
# --------------------------------------------------------------------------- #

def test_total_sums_across_chunks_and_reason_from_earlier_chunk_survives(make_client):
    requests: list[dict] = []
    c1: Any = {"total": 2, "reason": "full", "availabilities": []}
    c2: Any = {"total": 3, "reason": None, "availabilities": []}
    c = make_client(_recording_handler([c1, c2], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=30)
    assert out["total"] == 5
    assert out["reason"] == "full"  # set in chunk1, None in chunk2 -> survives


def test_reason_from_later_chunk_overrides_when_earlier_was_none(make_client):
    requests: list[dict] = []
    c1: Any = {"total": 0, "reason": None, "availabilities": []}
    c2: Any = {"total": 0, "reason": "not_opened_availability", "availabilities": []}
    c = make_client(_recording_handler([c1, c2], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=30)
    assert out["reason"] == "not_opened_availability"


def test_not_opened_reason_is_preserved_and_distinct_from_full(make_client, availabilities_not_opened):
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities_not_opened], requests))
    out = c.availabilities(1, [1], 1, start_date="2026-08-10", days=14)
    assert out["reason"] == "not_opened_availability"
    assert out["first_slot"] is None
    assert out["total"] == 0


# --------------------------------------------------------------------------- #
# agenda_ids encoding
# --------------------------------------------------------------------------- #

def test_agenda_ids_are_sent_hyphen_joined(make_client, availabilities):
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities], requests))
    c.availabilities(55012, [101, 102, 301], 901, start_date="2026-08-10", days=14)
    assert requests[0]["agenda_ids"] == "101-102-301"


def test_agenda_ids_accept_a_bare_int(make_client, availabilities):
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities], requests))
    c.availabilities(55012, 101, 901, start_date="2026-08-10", days=14)
    assert requests[0]["agenda_ids"] == "101"


# --------------------------------------------------------------------------- #
# SUSPECT: trailing-chunk overshoot
# --------------------------------------------------------------------------- #

def test_days_16_overshoots_by_one_day_due_to_limit_floor(make_client, availabilities):
    # SUSPECT: with days=16 the walk emits a 15-day chunk then a 1-day chunk, but
    # limit=max(2, chunk) floors the trailing request to 2. The client therefore asks the
    # server for day 17 (one beyond the requested 16-day horizon: 08-10 .. 08-25 inclusive).
    # Pinning current behaviour; the docstring even calls this flooring intentional.
    requests: list[dict] = []
    c = make_client(_recording_handler([availabilities, availabilities], requests))
    c.availabilities(1, [1], 1, start_date="2026-08-10", days=16)
    assert len(requests) == 2
    assert requests[0]["start_date"] == "2026-08-10"
    assert requests[0]["limit"] == "15"
    assert requests[1]["start_date"] == "2026-08-25"  # 08-10 + 15 days
    assert requests[1]["limit"] == "2"  # floored from the 1-day chunk -> fetches 08-25 AND 08-26
