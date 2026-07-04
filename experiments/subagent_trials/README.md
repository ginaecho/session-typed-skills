# Subagent trials — agent-interaction testing without Foundry

Validation harness in which the deterministic STJP machinery (Scribble
projections, EFSM scheduler, enforcement gate, monitors, runtime Critic)
is driven by **externally supplied agents** — in our runs, independent
Claude subagents — instead of Azure AI Foundry. No `stjp_core/foundry/`
import anywhere in this directory.

| file | purpose |
|---|---|
| `engine.py` | turn engine: `init` / `next` (emit polls) / `submit` (gate + advance) / `report` (monitors + Critic + goals) |
| `cases.py` | the `escrow_trade` case (arms: unchecked / bare / stjp) and its `escrow_trade_ext` extension |
| `setup_ext_case.py` | builds the extended case via the REAL incremental pipeline (`compiler/incremental.py`) — composed protocol + regenerated contracts + standalone monitors land in `protocols/` |
| `compaction_gauntlet.py` | judges a subagent's compaction of the prose trade skills (compatibility → synthesis → Scribble) |
| `protocols/` | committed artifacts produced by `setup_ext_case.py` |
| `reports/` | committed results: `SUBAGENT_TRIALS_REPORT.md` + per-experiment JSON |
| `runs/` | local run state/traces (gitignored) |

Results summary: `reports/SUBAGENT_TRIALS_REPORT.md` (full method,
numbers, limitations) and `docs/results/RESULT_5_SUBAGENT_VALIDATION.md`
(plain-English version).

The engine's poll/submit contract is model-agnostic: anything that can read
a prompt and return `{"action": "send"|"wait", ...}` JSON can play a role —
a human, a scripted oracle (see the dry-run in the report), or any LLM.
