# Revised **Hotel** skill (STJP-validated)

Fix vs original: HOLD the room first, then let Payment capture the charge, then
confirm — breaking the pay-vs-reserve circular wait.

```localtype
Traveler?RequestBooking(String);
Payment!RoomHeld(String);
Payment?PaymentCaptured(Double);
Traveler!BookingConfirmed(String);
```
