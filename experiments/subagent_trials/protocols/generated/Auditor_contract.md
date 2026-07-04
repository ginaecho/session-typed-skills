# Auditor — local contract

**Protocol**: `EscrowTrade`  
Generated from the Scribble-validated projection — do not edit; regenerate from the protocol.

## Your state machine

Start in state `52`. At each state you may ONLY do the listed action(s); anything else is off-protocol.

- state `52`: wait to receive `AuditRequest(String)` from **Escrow** → state `54`
- state `53`: TERMINAL — stop.
- state `54`: send `AuditApproval(String)` to **Escrow** → state `53`
