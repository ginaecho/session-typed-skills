# Escrow — local contract

**Protocol**: `EscrowTrade`  
Generated from the Scribble-validated projection — do not edit; regenerate from the protocol.

## Your state machine

Start in state `29`. At each state you may ONLY do the listed action(s); anything else is off-protocol.

- state `29`: wait to receive `Deposit(Double)` from **Buyer** → state `31`
- state `30`: TERMINAL — stop.
- state `31`: send `PaymentSecured(String)` to **Seller** → state `32`
- state `32`: wait to receive `ConfirmReceipt(String)` from **Buyer** → state `33`
- state `33`: send `AuditRequest(String)` to **Auditor** → state `34`
- state `34`: wait to receive `AuditApproval(String)` from **Auditor** → state `35`
- state `35`: send `SettlementComplete(String)` to **Buyer** → state `36`
- state `36`: send `SettlementComplete(String)` to **Seller** → state `30`
