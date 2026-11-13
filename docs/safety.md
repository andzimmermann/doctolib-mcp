# Booking behavior

Booking, cancellation and draft release remain available. They modify real appointments.
Manual booking and cancellation preview by default; `confirm=true` requires the user's
specific instruction. Monitoring with `auto_book=true` is a standing instruction for the
exact person, practice, motive and date/time constraints, not blanket booking authority.

Monitor configuration makes no network call. The separate `watch` process performs due
checks, verifies the chosen person's exact UUID and the configured practice/calendar/motive,
then reserves a durable `booking` state before dispatch. Duplicate target/person jobs and
process restarts cannot automatically repeat a dispatched booking. Every uncertain result
pauses for account verification; there is no automatic retry of a financial/medical write.

The exact patient UUID is re-resolved in the live account before selecting that patient.
The booking funnel submits only server-prefilled fields; it never invents intake answers.
An absent or ambiguous UUID cannot select the account holder as a fallback.

Only failures before the commit attempt clean up drafts. A commit response followed by a
failed confirmation fetch remains `booked=true`; a failed commit response is `booked=null`
(outcome unknown). Both forbid automatic retry. A draft release cannot undo a committed
appointment. Stopping a monitor cannot retract an already dispatched booking.

`.session/` holds browser login state; `.state/` holds private patient identifiers and job
instructions. Neither belongs in Git, CI artifacts, chat, a source archive or a public service.
The scheduler never opens a login window; sign in interactively again if the session expires.

No live booking, cancellation, login or medical record access was used for these tests.
The upstream internal endpoint implementation remains unverified against a real booking.
