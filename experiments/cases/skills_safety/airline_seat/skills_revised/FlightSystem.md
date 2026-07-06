# Revised **Flight System** skill (STJP-validated)

Applies the seat change and confirms it. Only reachable after a flight has been
assigned (guaranteed by the protocol ordering, not by hope).

```localtype
SeatBooking?UpdateSeat(String);
SeatBooking!SeatConfirmed(String);
```
