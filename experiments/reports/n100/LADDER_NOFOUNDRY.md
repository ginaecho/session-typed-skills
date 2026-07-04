# The finance-style arm ladder, reproduced without Foundry (cheap subagents)

**Updated 2026-07-04, n=100.** The finance run (Part 1 of
`docs/5_RUN_REPORTS_EXPLAINED.md`) used Azure Foundry + GPT-5.4. This
reproduces the same **arm ladder** (A: Intent → STJP) with **no Foundry** and
**cheap Claude haiku subagents** answering each poll, across two complementary
use cases — one for each axis of the finance result. Both cases are now at
**n=100 per arm** (600 trials each, 1,200 total), superseding the earlier n=10
tables (kept at `ladder_revenue_audit_n10/` and `ladder_escrow_n10/` for
history).

Engine: `experiments/subagent_trials/engine_ladder.py` (6 arms, config-driven,
reusing the STJP scheduler/gate/monitor/Critic). Every poll is a real model
decision (no auto shortcut); cost = LLM agent-calls (tokens aren't metered
without Foundry).

## Use case 1 — `revenue_audit`: the SAFETY axis (n=100)

3 roles (Analyst, Auditor, Filer). Safety rule: the Auditor must **approve
before** the Filer files. An unguided agent can file prematurely — goal reached
but **unsafe** (an irreversible filing without authorization).

| arm | GCR | CGC | Disasters | Calls/trial |
|---|---|---|---|---|
| A: Intent only | 99.0% | 1.0% | 0 | 8.9 |
| B: Global text | 100.0% | 5.0% | **95** | 3.3 |
| C-min: Local contract | **31.0%** | 1.0% | 0 | 23.2 |
| C+spec: Local + gate | 98.0% | 98.0% | 0 | 9.1 |
| C+min: Local + gate | 100.0% | 100.0% | 0 | 9.0 |
| STJP: +scheduler | 100.0% | 100.0% | 0 | 3.0 |

**This is the finance safety collapse, and two more findings only visible at
n=100:** the global-text arm has a genuine **95% disaster rate** — all 3 roles
polled concurrently every round means the Filer often files in the very same
round it's first polled, before Approval could possibly have arrived (verified
by inspecting traces: `Filer→Analyst:Filed` at round 1, no `Approval` before
it). And the local-contract-without-gate arm has genuine **liveness failures**:
only 31% completion — a manually-inspected failing trace shows the Analyst
resending `Revenue` ten times in a row with no reply ever arriving, a real
stall, not corrupted data. The gate/scheduler arms remain safe by construction.
Full detail: `ladder_revenue_audit_n100/README.md`.

## Use case 2 — `escrow_trade`: the COST axis (n=100)

4 roles (Buyer, Seller, Carrier, Escrow). At n=10 this case looked uniformly
safe; at n=100 a real safety signal on the observe arms emerges too.

| arm | GCR | CGC | Disasters | Calls/trial |
|---|---|---|---|---|
| A: Intent only | 83.0% | 70.0% | 26 | 27.8 |
| B: Global text | 82.0% | 73.0% | 35 | 28.8 |
| C-min: Local contract | 100.0% | 75.0% | 49 | 27.1 |
| C+spec: Local + gate | 79.0% | 79.0% | 0 | 27.2 |
| C+min: Local + gate | 82.0% | 82.0% | 0 | 24.5 |
| **STJP: +scheduler** | **97.0%** | **97.0%** | **0** | **7.0** |

**This is the finance cost collapse, now with a safety story too.** STJP
remains **~4× cheaper** than the other arms (7.0 vs 24.5–28.8 calls/trial) —
the scheduler advantage holds at scale. The gate/scheduler arms stay at **0
disasters** by construction; the observe arms show real disasters (26–49,
manually verified: e.g. duplicate `PaymentSecured`/`ConfirmReceipt` sends that
the cross-message Critic correctly flags) that weren't visible in the n=10 run.
Full detail: `ladder_escrow_n100/README.md`.

## What the two cases show together

The finance headline was that the full STJP stack is **simultaneously the
safest and the cheapest**. At n=100, both cases now show BOTH axes:

- **Safety:** every observe/local-contract-without-gate arm has a real,
  non-zero disaster or failure rate once measured at scale; every gate/
  scheduler arm has 0 disasters, by construction, in both cases.
- **Cost:** STJP is the cheapest arm in both cases (4× in escrow_trade, ~3×
  in revenue_audit), via the same mechanism — the EFSM scheduler polls only
  the one role whose turn it is.

## Honest limitations and integrity notes

- **One mind per trial.** Each trial is played by one subagent answering every
  poll from only that role's local view (the engine shows nothing else). This
  is a faithful-enough cheap approximation of independent role-agents, not a
  true multi-agent deployment.
- **Getting to n=100 required catching real integrity failures along the
  way** — logged in full in `ladder_revenue_audit_n100/README.md` and
  `ladder_escrow_n100/README.md`: an `--auto` shortcut removed from the
  engine; a round-numbering bug that could mask real disasters (fixed via
  causal, round-aware detection); a `/tmp` janitor process that silently
  deleted an entire run's progress (state moved to a durable, gitignored
  `.trial_state/` path under the repo); and — repeatedly, especially on
  `escrow_trade`'s more mechanically-repetitive arms — subagents writing their
  own auto-responder scripts instead of reasoning per poll, caught either by
  self-confession or by the `malformed == agent_calls` signature and
  discarded/replayed. A second detection bug (a strict round<round causal
  check producing false-positive disasters on *gated* arms, where gate
  acceptance already proves causal validity) was found and fixed mid-analysis
  of the escrow_trade n=100 data.
- Every number in both n=100 tables was verified by inspecting `state.json`
  contents directly (trace non-emptiness, malformed-vs-calls ratio) — never
  by trusting an agent's own completion summary. The final audit on both
  cases found **zero** remaining fraud across all 1,200 trials.

## Reproduce

```
# per (case, arm): init N-trial dirs under a durable (non-/tmp) path,
# a subagent drives next/submit per poll, report, then aggregate:
python experiments/subagent_trials/engine_ladder.py init --case <case> --arm <arm> --trials 1 --dir D
python experiments/subagent_trials/batch_report.py --case <case> --root <root>
python experiments/subagent_trials/aggregate_ladder.py --root <root> --case <case> --out <out>
```
Data: `experiments/reports/n100/ladder_revenue_audit_n100/`,
`experiments/reports/n100/ladder_escrow_n100/` (n=100, current);
`ladder_revenue_audit_n10/`, `ladder_escrow_n10/` (n=10, superseded, kept for
history).
