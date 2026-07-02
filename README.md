# Session-Typed Skills

**A session-typed static compiler for safe interactions in skills for agents.**

Multi-agent systems fail in the spaces *between* agents: one agent acts before
its authorization, two agents wait on each other forever, everyone burns tokens
asking "shall I proceed?". This project type-checks the **conversation itself**
— before any agent runs — and compiles it into one **interaction-safe skill
per agent**, plus a runtime guard and a scheduler, all derived from the same
proof.

```
intent (natural language)
   │  LLM drafts a global protocol; the Scribble compiler rejects unsafe drafts
   ▼     (deadlocks, stuck roles) and the LLM revises until it proves clean
validated protocol ──static projection──►  per-agent skill   (the prompt: only YOUR sends/receives + payload guards)
                                           runtime gate      (off-protocol messages rejected before delivery)
                                           scheduler         (poll only agents that can act now — no idle polling)
```

The LLM writes the *specification once*; every run after that is governed by
deterministic artifacts — one finite-automaton step per message, no LLM judge.

## Results on real agents (Azure AI Foundry, gpt-5.4)

- **Deadlock: only a static checker catches it.** Hand-written agent skills
  with a circular wait: **0/6** trials, zero messages, unbounded cost. Same
  intent through the compiler: draft rejected, fixed, **6/6**. → [`docs/DEADLOCK_DEMO.md`](docs/DEADLOCK_DEMO.md)
- **Intent-only prompting is unsafe.** Task-description-only agents: **0%**
  completion and **15–22 unauthorized irreversible actions** per run. Any arm
  holding the validated protocol: **100%, zero**. → [`docs/RESULTS_finance_n10.md`](docs/RESULTS_finance_n10.md)
- **Lean projected skills cut tokens −63%.** Same task, everyone succeeds:
  24.1k tokens (intent-only) vs **8.8k** (projected skill) — agents stop
  deliberating about *how to coordinate*. → [`docs/TOKEN_EFFICIENCY_DEMO.md`](docs/TOKEN_EFFICIENCY_DEMO.md)
- **The projection also schedules.** The local types say who can act at every
  state, so the runtime never polls idle agents. Pre-registered n=10 result:
  the full stack (projected skill + gate + scheduler) is **100% goal-complete
  with 0 disasters AND the cheapest, fastest arm in the study** — 13.3k tokens
  / 11.4 calls / 32s per delivered report, **−65% tokens vs the identical
  prompts on round-robin and 9× cheaper than the same protocol pasted as
  text**. → [`docs/RUN_REPORT_2026-07-02.md`](docs/RUN_REPORT_2026-07-02.md)

We publish the negative results too: on a strong model, pasting the validated
protocol as plain text already fixes correctness — the protocol does that work;
projection buys the *guarantee*, the audit trail, and the token/scheduling
wins ([`docs/WHY_B_MATCHES_C_ANALYSIS.md`](docs/WHY_B_MATCHES_C_ANALYSIS.md)).
Every run persists every agent's full prompt to disk, so every claim is
auditable from the run directory.

## Try it

Java 17 + Python 3.13:

```powershell
git clone https://github.com/ginaecho/session-typed-skills && cd session-typed-skills
git clone https://github.com/scribble/scribble-java          # the checker (see SCRIBBLE.md to build)
pip install -r stjp_core/requirements-core.txt
```

With an Azure AI Foundry project (`az login` + `stjp_core/.env`, see
`stjp_core/CLAUDE.md`):

```powershell
python experiments/scripts/case_runner.py finance 1 --arms min_llmvalid_gate,min_llmvalid_sched
```

## Where things live

| path | what |
|---|---|
| `stjp_core/` | Library: Scribble integration, authoring loop, skill generation, runtime monitor, EFSM-scheduled runtime, Foundry plumbing |
| `experiments/` | Benchmark: arm registry, runners, cases (finance, banking, trade_deadlock, report_pipeline), graders |
| `docs/` | Designs, run reports, results — start at [`docs/README.md`](docs/README.md); plain-language [`docs/GLOSSARY.md`](docs/GLOSSARY.md) |
| `ROADMAP.md` · `SCRIBBLE.md` · `RESEARCH.md` | Phased plan · compiler integration · ~70-entry bibliography |

Internally the toolchain is called **STJP** — the *Session-Typed Judge Panel*.

## Join in

Multiparty session types have 20 years of theory and an industrial compiler;
LLM agents finally give them a killer application. Places to jump in:

- **New cases** — add a domain protocol (`experiments/cases/`) and see what
  the checker catches in *your* workflow.
- **Harness adapters** — the monitor/scheduler are framework-agnostic;
  LangGraph / AutoGen / CrewAI adapters are open.
- **Theory → practice** — static refinement discharge (Z3), async subtyping,
  protocol evolution (`ROADMAP.md` Phase 2–3).

Open an issue or a pull request — small experiments are as welcome as theory.

## How to cite

Session-Typed Skills was originally proposed and implemented by
**Tzu-Chun (Gina) Chen**. If you use the software or build on the idea, please
cite it — GitHub's "Cite this repository" button uses [`CITATION.cff`](CITATION.cff).

## License

[MIT](LICENSE) © Tzu-Chun Chen and contributors. The Scribble compiler
(`scribble-java/`, cloned separately) is the upstream Imperial College project,
Apache 2.0.
