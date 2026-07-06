# Revised **Payment** skill (STJP-validated)

Fix vs original: capture the charge as soon as the room is held (no longer waits
on a confirmation that the Hotel was withholding until payment).

```localtype
Hotel?RoomHeld(String);
Hotel!PaymentCaptured(Double);
```
