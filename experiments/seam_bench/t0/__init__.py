"""seam_bench.t0 — T0 prompt-era baseline runner.

SEAM_TRAINING_EXECUTION_PLAN.md §4 "T0" and §7 metric block. Measures how
well prompt-only systems (no weights trained) translate a user intent into
a Scribble-valid global protocol, so the plan's pending E5 cells (first-
draft validity, repair rounds, guard co-emission) get real measured numbers
before any training run starts.

This package builds ONLY the harness. It does not call any LLM API itself
(no ANTHROPIC_API_KEY is assumed present in this environment) — drafting is
behind the `Drafter` interface in `drafter.py`; the planner's separate
subscription-subagent drafting workflow produces real drafts into a JSONL
that `FileDrafter` replays through this runner. See `drafter.py`'s module
docstring for the exact JSONL schema.
"""
