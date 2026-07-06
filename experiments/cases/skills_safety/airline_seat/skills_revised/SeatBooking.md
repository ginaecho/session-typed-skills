# Revised **Seat Booking** skill (STJP-validated)

Fix vs original: WAIT for the flight assignment from Triage BEFORE applying the
seat change. The original skill jumped straight to UpdateSeat.

```localtype
Triage?AssignFlight(String);
FlightSystem!UpdateSeat(String);
FlightSystem?SeatConfirmed(String);
```
