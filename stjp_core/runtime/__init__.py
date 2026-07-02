"""Execution plane (STJP v3, Plane B).

A DeLM-style decentralized runtime — shared verified context + async task
claiming — made *safe* by STJP: the monitor is the write-admission verifier and
the projected EFSM's enabled-set is the claim predicate. See docs/STJP_V3_PLAN.md
§3 and docs/RELATED_WORK_DELM.md.
"""
