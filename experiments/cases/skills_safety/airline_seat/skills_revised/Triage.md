# Revised **Triage** skill (STJP-validated)

Fix vs original: the seat change is gated behind flight assignment. Triage
assigns the flight to the booking before Seat Booking touches the seat.

```localtype
SeatBooking!AssignFlight(String);
```
