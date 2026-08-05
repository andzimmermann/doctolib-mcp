# The endpoint contract

Everything here was read out of Doctolib's own patient frontend: the network calls it makes,
and the rspack chunks it makes them from. This file records how the API behaved in August
2026. Doctolib documents none of it and promises none of it, so when something breaks, this
is the page to correct.

Hosts are per country and selected by the `country` argument:

```
de  https://www.doctolib.de
fr  https://www.doctolib.fr
it  https://www.doctolib.it
```

## Reads, no authentication required

| Method | Path | Returns |
|---|---|---|
| `POST` | `/patient-health-search/api/v1/hcp/search?page=N` | practitioners |
| `GET` | `/online_booking/api/slot_selection_funnel/v1/info.json?profile_slug=` | visit motives, agendas, places |
| `GET` | `/availabilities.json` | open slots |

### Search answers 206, not 200

The search endpoint returns `206 Partial Content` on success, because results are paged. A
client that checks for `200` breaks on every single search. Both `200` and `206` count as
success here.

The request body is:

```json
{
  "keyword": "handchirurg",
  "location": { "place": { "viewport": { "northeast": {...}, "southwest": {...} } } },
  "filters": { "insuranceSector": "public", "languages": ["tr"], "telehealth": true }
}
```

Filters are omitted entirely when unset. Note that `telehealth: false` is meaningfully
different from an absent `telehealth`, so the client tests for `is not None` rather than
truthiness.

### The bounding box is the filter

`location.place.viewport` is what actually narrows results to a city. Doctolib's own
frontend fills it from Google Places, which is why its requests carry a `placeId` of the
form `ChIJ...`. Any geocoder that returns a bounding box works just as well, and this client
uses Nominatim because it is free and needs no key.

The trap is that passing latitude and longitude without a viewport does not fail. It
returns nationwide results, quietly. A missing box is not an error, it is a wrong answer.

Nominatim orders its `boundingbox` array as `[south, north, west, east]`, which maps to
Doctolib's corners like this:

```
        west                    east
         |                        |
north  --+------------------------+--   northeast = {lat: north, lng: east}
         |                        |
         |      bounding box      |
         |                        |
south  --+------------------------+--   southwest = {lat: south, lng: west}
```

Transposing any pair of those produces the wrong city with no error anywhere.

### Search results chain straight into availability

Each result already carries the three identifiers that `availabilities.json` needs, so there
is no per-practitioner round trip in between:

```
search result
  matchedVisitMotive.visitMotiveId  ->  visit_motive_ids
  onlineBooking.agendaIds           ->  agenda_ids     (hyphen-joined: "12-34-56")
  references.practiceId             ->  practice_ids
```

`onlineBooking.agendaIds` takes precedence over `matchedVisitMotive.agendaIds` when both are
present.

### Availabilities is windowed

One call covers roughly two weeks. Longer horizons are walked by advancing `start_date`
rather than by raising `limit`, in chunks of at most 15 days:

```
days=30, start=2026-08-05

  call 1   start_date=2026-08-05  limit=15   [2026-08-05, 2026-08-19]
  call 2   start_date=2026-08-20  limit=15   [2026-08-20, 2026-09-03]
```

Two constraints on `limit`. It must be at least 2, because Doctolib answers `HTTP 400`
below that. And a trailing chunk shorter than 2 days gets floored to 2, which means a
horizon like `days=16` reaches one day further than asked. That is a real off-by-one, it is
pinned by a test, and it has not been changed because over-fetching a day is harmless while
changing the walk is not.

A response carrying `reason: "not_opened_availability"` means the practice has not released
slots that far ahead yet. That is a different situation from being fully booked, and the
distinction is preserved all the way out to the caller.

### One dead endpoint

`GET /booking/{slug}.json`, the endpoint used by the 2021 COVID slot-hunter projects, now
returns `400`. It was replaced by `slot_selection_funnel/v1/info.json`. If you find a blog
post or a GitHub project describing the old one, it is stale.

## Reads that need a session

| Method | Path | Returns |
|---|---|---|
| `GET` | `/account/appointments.json` | confirmed, pending and past appointments |
| `GET` | `/account/master_patients.json` | bookable patients on the account |
| `GET` | `/appointment_modification_reasons/cancellations?appointment_id=` | valid cancellation reasons |

`/account/appointments.json` doubles as the login probe. It answers `200` with a `data`
envelope only for an authenticated session, and redirects or 401s otherwise, which is how
`session_status` decides whether a login is still live.

Two payload quirks worth knowing. `speciality` arrives as a bare string in the appointments
payload and as a `{name, slug}` dict in search results, so any code reading it has to accept
both. And `/appointment_modification_reasons/cancellations` legitimately returns `[]` for
some appointments. That means no preset reason is required, and it is not an error.

The same session also improves the public reads. Sent with a logged-in cookie, they return
existing-patient visit motives, slots reserved for known patients, the right insurance
sector and personalised ranking. That is why the read tools prefer the session when one
exists.

## Writes

Every write is state-changing, and Doctolib runs on Rails, so every write needs a CSRF
token. This is not optional and it is not obvious from the outside.

### CSRF, and the 500 that taught us

A state-changing request without `X-CSRF-Token` is rejected with an opaque `HTTP 500`. No
message, nothing naming the token. The first real cancellation attempt hit exactly this and
looked like a server fault.

The fix is to issue writes as an in-page `fetch` from a real logged-in page, reading the
token out of `<meta name="csrf-token">`. The server rotates the token and returns the new
one in a response header, so each call writes the rotated value back into the meta tag
before the next call reads it:

```
page load        meta[csrf-token] = T0
  POST step 1    sends T0     -> response header X-CSRF-Token: T1   -> meta = T1
  POST step 2    sends T1     -> response header X-CSRF-Token: T2   -> meta = T2
```

Playwright's `context.request` does not carry the token, so it is used for reads only.

### Cancelling

```
DELETE /account/appointments/{id}      body: {"reason": ..., "message": ...}
```

Executed once for real, on 05.08.2026. The contract came out of account bundles 41443 and
563944.

### The booking funnel

Booking is not one POST. It is four sequential steps sharing a server-side draft that is
keyed to the session, which is why `patient_selection/v1/info` takes no parameters at all:
it reads the draft back out of the session.

```
  1  POST /online_booking/api/slot_selection_funnel/v1/select_slot     creates the draft hold
       |
  2a GET  /account/master_patients.json                                who can be booked
  2b POST /online_booking/api/patient_selection/v1/select              pick the patient
       |
  3a GET  /online_booking/api/checkout/v1/patient_form                 server-prefilled fields
  3b POST /online_booking/api/checkout/v1/patient_confirm              *** COMMITS ***
       |
  4  GET  /online_booking/api/confirmation/v1/booking                  final state

  on any failure after step 1:
     DELETE /online_booking/draft.json                                 release the hold
```

Because the draft lives in the session and the token rotates between steps, the whole funnel
runs inside a single persistent browser context (`session.AuthedBrowser`). A per-step
browser launch, or an httpx cookie replay, drops both the draft and the token. That was the
original bug in this module.

Three things that are easy to get wrong here:

**Bookable patients come from `/account/master_patients.json`, not from
`patient_selection/v1/info`.** The info response returns the draft, and a `redirection` when
the step auto-resolves. The UI's patient picker loads from the account endpoint, where each
patient carries the `signed_id` that `select` needs plus a `kind` of `"main"` for the account
holder. The first real booking failed precisely because this module read patients off the
info response.

**Step 3's payload is echoed back from step 3's own GET.** The form carries practice-specific
custom fields and qualification questions that cannot be known in advance. Inventing values
for a medical intake form is the wrong kind of creative, so the client submits exactly what
the server prefilled and drops anything the server left empty. When a practice requires a
field the server did not fill, the confirm call fails and a human finishes in a browser.
That outcome is correct. Do not patch it by guessing.

**Some failures arrive as `HTTP 200` with a non-empty `errors` array.** Checking the status
code alone is not enough.

Endpoint paths and payload keys came out of funnel bundles 93204, 58900, 96195 and 31180.
The transport is correct by construction, since it reuses the in-page CSRF write that a real
cancellation proved works. The funnel as a whole has still never run end to end, because
that cannot be tested without booking a real appointment. Expect step 3 to be the fragile
one, and report server errors verbatim instead of retrying.

## DataDome

The site sits behind DataDome. As of August 2026 it only rejects obviously non-browser
clients: a default curl or python User-Agent draws a `403` JS challenge, while a normal
Chrome User-Agent plus a `Referer` passes on the JSON endpoints with no cookie, no browser
and no TLS impersonation.

That is a description of current behaviour and not a promise. When these endpoints start
returning `403`, the answer is to route the call through the real browser session, which
carries a genuine TLS fingerprint and DataDome device token. More header guessing is not the
answer.

The HTML pages are blocked considerably harder than the JSON. Never scrape the markup.
