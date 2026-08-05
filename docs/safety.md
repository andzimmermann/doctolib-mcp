# The write path

Two tools in this server change something in the real world. `book_appointment` puts a real
person into a real clinic's calendar. `cancel_appointment` takes them back out and releases
the slot to whoever grabs it next. Everything else here reads.

This page is written for whoever or whatever is holding the tools. Most of it is aimed at an
agent, because an agent is the thing most likely to call a tool out of curiosity when nobody
asked it to.

## What is actually at stake

A successful booking means a clinician has reserved time and a human being is expected to
physically show up at that address at that hour. Missing it wastes the slot, can incur a
no-show fee, and can get a patient marked at that practice.

A successful cancellation is irreversible. The slot goes back into the pool and may be taken
within seconds. At some practices rebooking means waiting months. Cancelling under roughly 24 hours' notice counts as a late
cancellation, which some practices penalise. The appointment's own `warn_late_modification`
field marks when that applies.

Neither of these is undoable by calling the other tool again.

## The confirmation step

Both tools take `confirm`, and it defaults to `False`.

On the default path, `book_appointment` makes no network call whatsoever. It parses the
identifiers you gave it, returns the exact payload that a real booking would send, and stops.
`cancel_appointment` on the default path makes one read, fetching the valid cancellation
reasons for that appointment, and issues no `DELETE`.

The point of the preview is not to check that the call is well-formed. It is to give you
something to read back to the person in plain language before anything binding happens. Who
the practitioner is, what date and time, at which address, for which visit motive. Then get
an explicit yes to that specific appointment, in the conversation, and only then call again
with `confirm=True`.

## Rules for an agent

Do not call either tool with `confirm=True` unless the person has asked for that specific
appointment in the current conversation. A general instruction like "sort out my hand
follow-up" is not consent to a particular slot at a particular clinic.

Do not call `book_appointment` to find out whether booking works, to explore the API, or as
a natural-looking next step after a search. There is no safe way to test it. The first real
call is the test.

Do not retry a failed booking. A failure partway through the funnel may have left a
temporary hold, and a blind retry can produce a second draft or a duplicate appointment.
Read the failure, report it verbatim, and hand back to the human.

Do not invent values for a medical intake form. The checkout step deliberately submits only
what Doctolib's own server prefilled. When a practice requires something the server left
blank, the call fails and the person finishes in a browser. That is the designed outcome.

When in doubt, reach for `booking_url` instead. It hands over a link and commits nothing.

## What protects you, and what does not

The real guards are the confirmation step and the requirement for a live login session. That
is the whole list.

An earlier version of the README claimed that a missing `DOCTOLIB_COOKIE` environment
variable would stop a booking in the constructor before any HTTP call. That described an
older design. `DoctolibBookingClient` now takes no cookie at all and only validates the
country, because the funnel needs real session state rather than a replayed cookie. Do not
treat an unset environment variable as a safety measure.

On the pi box there is one more layer, though it is a matter of reach rather than of
correctness: neither write tool appears in that server's `directTools` list, so they stay
behind the proxy instead of sitting in the default tool list.

### Unattended callers must turn auto-login off

An authenticated tool that finds no live session now opens a real browser window and blocks
for up to two minutes waiting for someone to sign in. At a terminal that is the behaviour
you want. In anything running without a human in front of it, a sentinel, a cron job, a
dispatch drone, it is a process that hangs on a window nobody will ever see.

Set `DOCTOLIB_AUTO_LOGIN=0` in the environment of any such caller. It restores the previous
behaviour, where a missing session fails immediately with `NotLoggedIn`.

This matters beyond tidiness. An unattended agent that blocks for two minutes per call, and
whose failure looks like a timeout instead of a missing login, is an agent that will be
debugged in the wrong place. Auto-login is a convenience for interactive use and should be
switched off everywhere else.

## Booking, in detail

The funnel is four stateful steps that share a server-side draft. Step 1 creates a temporary
hold, step 3b commits. The full contract is in [endpoints.md](endpoints.md).

**Verification status, stated plainly.** Every path and payload key was read out of
the funnel bundles Doctolib serves to browsers. The transport underneath is correct by construction,
because it reuses the same in-page CSRF write that a real cancellation proved works. The
funnel itself has never been executed end to end, and it cannot be, because testing it means
booking a real appointment. Treat the first real call as the test. Expect step 3, the
checkout, to be the fragile one.

The test suite covers everything around this that can be covered offline: the step order,
the paths, the payload construction, the patient choice, and the cleanup behaviour. That
narrows where a first real failure can come from, but it does not make the funnel proven.

**Partial failure.** If any step after the draft is created fails, the client tears the draft
down with `DELETE /online_booking/draft.json` so a broken run never leaves a slot held or a
stale draft blocking a later attempt. The result dict reports whether that cleanup succeeded,
under `draft_cleaned`, and carries a `resume_url` so a human can finish or discard the
attempt in a browser.

A draft is only ever a temporary hold. It is never a confirmed appointment, so releasing one
is safe and idempotent. `abort_booking_draft` exposes the same cleanup manually, for the case
where something was abandoned rather than failed.

**One failure report you must not take at face value.** `book()` wraps the commit and the
final confirmation read in the same error handler. If `patient_confirm` succeeds but the
`confirmation` GET that follows it raises, you get `booked: False` and an instruction not to
retry, even though the appointment was already committed server-side. So a failure whose
trace reaches `checkout/patient_confirm: COMMITTED` means the opposite of what the return
value says. Check `my_appointments` or the Doctolib web account before concluding that
nothing was booked, and never re-book on the strength of that result alone. The five
self-clean tests pin this behaviour uniformly across every step, so it is current and
intended rather than an accident, but it is the one place where the returned flag can
mislead.

**Who the appointment is for.** An account can carry several patients, for example family
members. With exactly one patient the choice is automatic. With several, the client picks the
one whose `kind` is `"main"` if that is unambiguous, and otherwise refuses and asks for a
`patient_name` instead of guessing. Guessing here books a clinical slot for the wrong human,
which is why that function is the most heavily tested thing in the repository.

## Cancelling, in detail

```
GET    /appointment_modification_reasons/cancellations?appointment_id={id}
DELETE /account/appointments/{id}                      body: {reason, message}
```

Some appointments return an empty reasons array. That means no preset reason is required,
and nothing has gone wrong.

This write has been executed once for real, on 05.08.2026, as an explicitly authorised
cancellation. That run is where the CSRF requirement was discovered, since Rails rejects a
tokenless state-changing request with an opaque `HTTP 500` that names nothing.

Before cancelling, read the appointment back from `my_appointments` and confirm it with the
person. Check `warn_late_modification` and tell them if the cancellation counts as late.

## The session directory

`.session/<country>/` is a persistent browser profile holding a live medical-account login.
It is gitignored. It must never be committed, copied into a scratch directory, attached to a
message, or handed to another agent or worker process. Anything holding it can read a real
person's medical calendar and cancel their appointments.

Tests must not read it either. The suite repoints `SESSION_ROOT` at a temporary directory and
blocks Playwright from launching, so a test run cannot reach the real profile even by
accident.
