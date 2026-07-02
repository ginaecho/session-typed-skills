"""Governance plane (STJP v3, Plane A).

Turns a Scribble-verified, projected STJP protocol into governance artifacts that
the Microsoft Agent Governance Toolkit's Policy Engine can consume — making STJP a
*policy generator* (deadlock-free, ordered, value-constrained policies that a human
could not reliably hand-write). See docs/STJP_V3_PLAN.md §2 and
docs/GOVERNANCE_TOOLKIT_ASSESSMENT.md.

Import `export_policy_document` from `stjp_core.governance.policy_export`
(kept out of this __init__ so `python -m stjp_core.governance.policy_export`
does not double-import).
"""
__all__ = ["policy_export"]
