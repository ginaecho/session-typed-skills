# P0b — mid-tier replication of the revenue_audit B result (sonnet)

*2026-07-05. Per `VALIDATION_TODO.md` §P0b: the 95% B disaster rate is the
paper's most quotable number and came from cheap **haiku** subagents. This
re-runs arm **B (global text)** on `revenue_audit` at **n=30** with a
**stronger model (Claude sonnet)** to test whether the danger is
model-dependent. Opus orchestrated; sonnet played the three roles, one mind per
trial, per-poll reasoning, no scripts. All 30 verified from `state.json`
(not agent prose): 30/30 success, `malformed=0`, no stray scripts.*

## Result — the race is model-dependent

| model | n | Filed @ round 1 (race) | serialized (Filed after Approval) | disasters | avg calls/trial |
|---|---|---|---|---|---|
| **haiku** | 100 | **95** | 5 | **95** | 3.3 |
| **sonnet** | 30 | **0** | **30** | **0** | 9.0 |

**Every** sonnet trial serialized: `Revenue@r1 → Approval@r2 → Filed@r3`, filing
only after approval. **Zero round-1 races, zero disasters** — versus haiku's
95/100 premature files.

## Why — and why it *strengthens* the thesis

Under `schedule="all"` all three roles are polled every round. Handed the full
protocol text, the **weak** model's Filer fires immediately in round 1 (it does
not reason that approval must come first); the **stronger** model's Filer reads
the same protocol, recognises the ordering constraint, and **waits** — so the
pipeline serialises and stays safe.

This is plan outcome **(b)**, and it is the *stronger* story for the paper:

- **The unenforced arm's safety is model-dependent** — B is a coin toss that
  depends on how much the model reasons about ordering under concurrency.
- **Enforcement's safety is model-independent** — the gate/scheduler arms are
  **0 disasters at every tier by construction** (the disallowed send never
  lands, regardless of model strength). That invariance is exactly the value
  proposition: you don't get to assume a strong model in production.

## A secondary observation — safe behaviour costs the unenforced arm more

Sonnet's B is safe but uses **9.0 calls/trial** vs haiku's **3.3** — because
serialising correctly takes 3 rounds (9 polls) whereas racing reaches the goal
in one round. So for the *unenforced* arm, the model pays for safety in latency
and calls. STJP gets safety **and** low cost together (3.0 calls/trial, 0
disasters) because the EFSM scheduler only polls the role whose turn it is —
neither racing nor idling.

## Honest scope

- Mid-tier here is **Claude sonnet** (the in-environment stronger tier), not the
  plan's idealised gpt-4o; and the control arm **C+min** with sonnet is the
  next step (the gate makes it safe by construction regardless — low marginal
  information, run for completeness).
- Data: `.trial_state/p0b_sonnet/revenue_audit/global_text__trial_000..029/`
  (gitignored scratch); this report is the durable artifact.
