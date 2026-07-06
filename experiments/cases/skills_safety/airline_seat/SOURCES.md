# Source provenance — airline_seat

The `skills_original/` files are adapted (near-verbatim) from real, public,
permissively-licensed agent skills. Nothing here was authored to be unsafe on
purpose — the unsafety is a property the source skills already have when read
in isolation, which is the entire point of the demo.

| File | Source repo | Path | License | Retrieved |
|---|---|---|---|---|
| Triage.md | openai/openai-agents-python | `examples/customer_service/main.py` (`triage_agent`) | MIT | 2026-07-06 |
| SeatBooking.md | openai/openai-agents-python | `examples/customer_service/main.py` (`seat_booking_agent`) | MIT | 2026-07-06 |
| FlightSystem.md | (derived) | the `update_seat` tool + `on_seat_booking_handoff` hook contract in the same file | MIT | 2026-07-06 |

Commit at retrieval: `main` (repo pins the example under
`examples/customer_service/main.py`). The real `seat_booking_agent`
`instructions` string contains the 3-step routine with no flight-assignment
precondition; the precondition lives only in code
(`assert context.context.flight_number is not None` inside `update_seat`, and
`on_seat_booking_handoff` which sets `flight_number` during the Triage->Seat
handoff). `FlightSystem` makes that code-only precondition explicit as a role.

Safety review: benign customer-service coordination logic only. No secrets,
no exfiltration, no jailbreak content.
