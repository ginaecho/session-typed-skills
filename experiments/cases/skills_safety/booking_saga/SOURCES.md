# Source provenance — booking_saga

The `skills_original/` files are adapted from the public, MIT-licensed LangGraph
supervisor + booking/saga examples (an orchestrator coordinating reservation and
payment services with saga-style ordering and compensation). The unsafety is a
property the individually-sensible service skills already have: the reserve-vs-
pay ordering is left implicit, and two defensive "wait for the other side first"
rules produce a circular wait.

| File | Source repo | Basis | License | Retrieved |
|---|---|---|---|---|
| Traveler.md | langchain-ai/langgraph | supervisor/orchestrator node | MIT | 2026-07-06 |
| Hotel.md | langchain-ai/langgraph | reservation worker (hold/confirm room) | MIT | 2026-07-06 |
| Payment.md | langchain-ai/langgraph | payment worker (capture charge) | MIT | 2026-07-06 |

Safety review: benign booking-coordination logic only. No secrets, no
exfiltration, no jailbreak content.
