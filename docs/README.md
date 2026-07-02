# docs/ — STJP documentation index

Cleaned up 2026-06-12: superseded/historical docs moved to `archive/` (nothing
deleted). All research/design/report documents live HERE (standing policy
since 2026-06-12); `pitch/` keeps only presentation assets (demo HTML, decks).

## Current

| doc | what it is |
|---|---|
| `TOKEN_EFFICIENCY_DEMO.md` | **Centerpiece (efficiency)**: same task 100% done by all, lean projected contract uses 8.8k tokens vs 24.1k intent-only (−63%); mechanism = less deliberation + smaller prompt. |
| `DEADLOCK_DEMO.md` | **The centerpiece**: unchecked skills deadlock (0/6, 0 msgs) vs Scribble-validated (6/6); cost-of-deadlock; what a violation/forbidden interaction is. |
| `STJP_RESEARCH_REPORT.md` / `.docx` | The full technical report (pipeline, benchmark, all runs incl. n=5 gate result, lessons). |
| `TESTING_STRATEGY.md` | **Read first for benchmarking**: STJP's 4 claims (deadlock/static-check/token/time), the fairness rules, the 2-axis design, and an audit of which arm comparisons are fair vs confounded. |
| `BENCHMARK_DESIGN.md` | Benchmark scoring spec: gated layers, GCR/adherence/cost + v2.1 S0–S4 consequence grading. |
| `BENCHMARK_DESIGN_V2_FROZEN.md` | Frozen snapshot of the design behind the grand n=10 run — the fall-back/revert point. |
| `BENCHMARK_DESIGN_V3_CRITICALITY.md` | Criticality-aware redesign: C1/C2/C3 critical-property gates, two-variant fairness design, CGC metric. |
| `GOVERNANCE_TOOLKIT_ASSESSMENT.md` | STJP × MS Agent Governance Toolkit (Policy Engine): reuse + enhance analysis. |
| `RELATED_WORK_DELM.md` | DeLM (arXiv 2606.10662) — not a threat; how STJP and DeLM compose. |
| `RUN_REPORT_2026-06-17.md` | Smoke results: drafting prompt v1-vs-v2 A/B, criticality gates on grand traces. |
| `SESSION_2026-06-17.md` | Index of the 2026-06-17 session (seven workstreams). |
| `STJP_V3_PLAN.md` | Next-version architecture: STJP as governance policy-generator (toolkit) + verifier for a DeLM-style decentralized runtime. Roadmap. |
| `DRAFTING_IMPROVEMENTS.md` | Why drafts failed ("Unfinished roles"), the deterministic fan-out normalizer (first-pass 0/4→3/4), and the SLM recommendation. |
| `RUN_REPORT_2026-06-11.md` | Run-by-run report: cost anatomy, severity re-scoring, banking, why-typed-agents-still-err. |
| `EVOLUTION_DEMO_DESIGN.md` | "The demand changed on Tuesday" demo/benchmark design (banking + ComplianceScreen). |
| `GLOSSARY.md` | Plain-language meaning of every term/acronym used across the docs. |
| `DIARY.md` | Project journal, newest first. The history of every decision. |
| `EXPERIMENT_DESIGN_v2.md` | The working 8-arm experiment design (supersedes v1, now in archive). |
| `EXPERIMENT_DESIGN_V3_EXECUTION.md` | **Pre-registered** unconfounded finance demo: same decentralized plane for all arms + the EFSM enabled-sender scheduler wired to real agents (`min_llmvalid_gate`, `min_llmvalid_sched` arms); predictions P1–P4. |
| `RESULTS_finance_n10.md` | Canonical finance n=10 results with trace-level examples. |
| `SCRIBBLE_EXTENSIONS.md` | How STJP extends Scribble: refinements, composition, higher-order sessions. |
| `PROTOCOL_EVOLUTION.md` | Change-request → updated, re-validated global type (basis of the evolution demo). |
| `GAP_CLOSED.md` | Refinement call-site closure record (referenced by `experiments/README.md`, `stjp_core/README.md`, copilot-instructions). |
| `CHOICE_GUARDS_AND_GATE.md` | Value-dependent choice guards (.refn `[choice at Role]`) + the enforcement gate arm — which script does what, why, how (2026-06-12). |
| `FOUNDRY_VISIBILITY.md` | How to make agents/threads/traces visible in the Azure AI Foundry portal. |
| `monitoring_tool_from_intent.png` | Figure cited by `GAP_CLOSED.md`. |
| `STJP topic collections.pptx` | Talk/topic material. |

## archive/

| doc | why archived |
|---|---|
| `EXPERIMENT_DESIGN.md` | v1 (4-scenario) design — superseded by `EXPERIMENT_DESIGN_v2.md`. |
| `RESULTS.md` | Results from the deleted legacy runner (`experiment_4_scenarios.py`, `P1_v2.scr`). |
| `SKILLS_COMPILER_PROPOSAL.md` | Skills files were retired from the live path on 2026-05-29. |
| `APPLICATION_SCENE_VIEW_PROPOSAL.md` | Superseded by the built demo (`pitch/STJP_Benchmark_Demo.html` + `pitch/demo_build/`). |
| `STJP_discussion_13May2025.md` | Meeting record (2026-05-13); decisions absorbed into GAP_CLOSED + v2 design. |
