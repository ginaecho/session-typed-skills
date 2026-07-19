# W2 — Lark grammar for Scribble surface + vLLM guided-decoding adapter

*(Codenames: the "seam" is the intent-to-protocol translation step — a plain-language request becomes a Scribble-validated protocol; `W2` is this report's worker task-card id in the seam-training program, [`SEAM_TRAINING_EXECUTION_PLAN.md`](../../SEAM_TRAINING_EXECUTION_PLAN.md).)*


Task card: `SEAM_TRAINING_EXECUTION_PLAN.md` §9 row W2 (see also §2
"Grammar-constrained decoding"). Done-when: corpus round-trip 100%; 1k samples
parse.

Deliverables (all on branch `gc/seam-w2-grammar-gcd`):

- `stjp_core/compiler/scribble_grammar.lark` — the grammar.
- `stjp_core/compiler/gcd_adapter.py` — Lark loader, GBNF emitter for
  xgrammar, deterministic sampler, `validate_text`.
- `stjp_core/tests/test_scribble_grammar.py` — round-trip + negative suite.
- `lark==1.2.2` added to `requirements-core.txt` and `requirements-secure.txt`.

---

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [1. Grammar coverage](#1-grammar-coverage)
  - [Known simplifications (also in the grammar file header)](#known-simplifications-also-in-the-grammar-file-header)
- [2. vLLM / xgrammar grammar-format finding (with evidence)](#2-vllm--xgrammar-grammar-format-finding-with-evidence)
- [3. Round-trip results (real numbers)](#3-round-trip-results-real-numbers)
- [4. `.refn` guard sidecar — scoped OUT (justified)](#4-refn-guard-sidecar--scoped-out-justified)
- [5. Skip-list rationale](#5-skip-list-rationale)
- [6. Requirements placement](#6-requirements-placement)
- [7. Open issues / notes for the planner](#7-open-issues--notes-for-the-planner)
<!-- MENU:END -->

## 1. Grammar coverage

The grammar covers exactly the constructs this repo emits/accepts, derived from
`nuscr_syntax.py`, `protocol_parser.py`, and every `.scr` in the corpus:

| construct | production | corpus evidence |
|---|---|---|
| module header (dotted) | `module_decl` | `module pipeline.FinancePipeline;` |
| `data <java> "..." from "..." as N;` | `data_decl` | all 116 real files |
| global protocol + role params | `protocol_decl`, `role_params` | all |
| `aux global protocol` | `AUX?` on `protocol_decl` | composition cases |
| message `Label(Sort?) from A to B;` | `message`, `payload?` | all; empty `()` too |
| `choice at R { } or { } …` (≥2 branches) | `choice`, `block` | 76 files |
| `rec L { } / continue L;` | `recursion`, `continue_stmt` | retry/polling/rag/nested |
| `do P(A, B);` sub-protocol call | `do_stmt` | composition/pipeline |
| `//` line comments (ignored) | `%ignore COMMENT` | unsafe drafts, headers |

Parser: LALR with contextual lexer (default), so protocol keywords (`module`,
`data`, `from`, `to`, `global`, `protocol`, `role`, `choice`, `at`, `or`,
`rec`, `continue`, `do`, `aux`) are lexically reserved — a message can't be
labelled `choice`. Earley also builds and gives identical corpus results
(sanity cross-check in tests).

### Known simplifications (also in the grammar file header)

Tight-is-right for training-time decoding. Deliberate departures from full
Scribble/nuscr:

1. Payloads are a single bare sort or empty — no `M(Int,String)`, no
   `M(x: Int)`, no delegation payloads. Matches `protocol_parser`'s message
   regex, which reads one payload field.
2. `data` decls restricted to the `data <java> "…" from "…" as N;` form the
   repo emits.
3. No `par { } and { }` parallel composition (corpus has none).
4. `do` args are role names only.
5. `choice` requires ≥2 branches.
6. Module names may be dotted; protocol/role/label/sort are simple identifiers.
7. `.refn` sidecar out of scope — see §4.
8. GBNF form for xgrammar omits comments on purpose — see §2.

## 2. vLLM / xgrammar grammar-format finding (with evidence)

**Finding: vLLM's `guided_grammar` with the default xgrammar backend consumes
GBNF (GGML BNF), not Lark's native `.lark` dialect. You cannot hand vLLM the
`.lark` file.** The two dialects differ in ways that break a naive paste:

| | Lark (`.lark`) | GBNF / xgrammar |
|---|---|---|
| rule operator | `:` | `::=` |
| entry rule | `start` | `root` (xgrammar default `root_rule_name`) |
| whitespace | `%ignore WS` auto-skips | **no `%ignore`** — must be explicit (`ws`/`sp`) |
| imports / terminals | `%import`, named terminals | inline char classes + string literals |

The whitespace point is the load-bearing gotcha: a Lark grammar relying on
`%ignore WS`, pasted into xgrammar verbatim, becomes *whitespace-forbidding*.
So `gcd_adapter.to_ebnf_for_xgrammar()` returns a hand-maintained GBNF mirror
(`_XGRAMMAR_GBNF`, entry rule `root`, explicit `ws`/`sp`) rather than the
`.lark` text. It is kept in sync with the Lark grammar and shape-checked by the
test suite; if xgrammar is importable, the test also compiles it via
`xgrammar.Grammar.from_ebnf(...)` (skipped here — not installed, no GPU).

Two intentional GBNF-vs-Lark differences, documented in code:

- GBNF omits `//` comments (a comment channel is a persuasion-smuggling risk at
  training time per §5.2; the canonical pretty-print is comment-free). The Lark
  path still accepts comments because the corpus contains them.
- GBNF's identifier class cannot reserve keywords as strictly as the LALR
  lexer, so e.g. a message labelled `choice` is not lexically excluded by the
  CFG. Negligible overgeneration (Scribble validator rejects it downstream),
  decoding-only.

The vLLM-facing helpers never import vllm at module load:
`vllm_guided_decoding_config()` returns a plain dict
(`{"guided_grammar": <gbnf>, "backend": "xgrammar", …}`) for a caller that owns
an engine; `build_vllm_sampling_params()` imports vllm lazily inside the body.

Sources (retrieved 2026-07):
- vLLM docs — Structured Outputs: `guided_grammar` example is written in
  `root ::= …` EBNF, xgrammar is the default CFG backend.
  https://docs.vllm.ai/en/latest/features/structured_outputs/
- XGrammar docs — `Grammar.from_ebnf(ebnf_string, root_rule_name='root')`;
  "XGrammar follows the GBNF (GGML BNF) format from llama.cpp".
  https://xgrammar.mlc.ai/docs/api/python/grammar.html ,
  https://xgrammar.mlc.ai/docs/tutorials/ebnf_guided_generation.html

## 3. Round-trip results (real numbers)

**Done-criterion 1 — corpus:** 118 `.scr` files under `experiments/cases/`.
**113 parse (100% of the non-skip set)**, 5 skip-listed (§5). All 4 curated
`unsafe/` drafts parse (semantically invalid, syntactically fine — as the plan
expects).

**Done-criterion 2 — sampled strings:** `sample_random(seed=20260711, n=1000)`
→ **1000/1000 parse under BOTH the Lark grammar AND `protocol_parser`**
(protocol name + roles recovered, every message endpoint a declared role).
Breadth across the 1000: choice 305, rec 341, continue 341, do 468,
empty-payload 527; avg 17 lines, max 93. Sampler is deterministic per seed.

**Done-criterion 3 — negatives:** 15 corrupted protocols all fail to parse
(unbalanced braces, unknown keyword `parallel`, role in message position,
missing `from`/`;`/parens, single-branch choice, multi-field & annotated
payloads, `type` data decl, no module, no protocol, stray text).

Full suite: `python -m pytest stjp_core/tests/test_scribble_grammar.py` →
23 passed, 1 skipped (xgrammar-compile, dep absent).

## 4. `.refn` guard sidecar — scoped OUT (justified)

Per the task card, `.refn` is covered only if it is a formal grammar in the
repo. It is not. `refinement_checker.py::parse_refn_text` is an ad-hoc
line-oriented reader (regex header match + `str.split(':', 1)`), and the guard
predicates themselves are **arbitrary sandboxed Python expressions** parsed by
`ast.parse` (a large allowed-node set: BoolOp, BinOp, Compare, Call,
Attribute, …). A tight formal grammar for that surface is not feasible without
either (a) reproducing a Python-expression grammar — which would massively
overgenerate for a training-time constraint — or (b) duplicating the ad-hoc
line parser. Neither serves the GCD goal, so `.refn` is out of scope for the
grammar. If refinement co-emission is later trained, the right move is a
separate, purpose-built narrow grammar for the specific predicate shapes the
corpus uses (`x >= 0.0`, `len(x) > 0`, `float(Label) > 50000`), not a general
one.

## 5. Skip-list rationale

5 files legitimately don't parse and shouldn't (a tight grammar rejecting
malformed drafts is correct). All are intermediate **rejected** LLM draft
attempts under `banking/…/llm_drafts/_attempt_*` — not curated protocols:

| file | why it can't parse |
|---|---|
| `_attempt_01/v1.scr` | bare ```` ``` ```` code fence, no protocol text |
| `_attempt_10/v1.scr` | ```` ```scribble ```` fence, no protocol text |
| `_attempt_03/v1.scr` | `… to Adminrole Tracker; }` — two bareword role tokens |
| `_attempt_07/v1.scr` | `Settled() AuditLog to Initiator;` (no `from`); `} or block-loop;` |
| `_attempt_09/v1.scr` | `BalanceAcknowledgement)}` — truncated/malformed message |

The test asserts each skip-listed file both exists and fails to parse, so the
list can't silently rot.

## 6. Requirements placement

`lark==1.2.2` added to `requirements-secure.txt` and `requirements-core.txt`
(both security-clean base lists, in their "Parsing and validation" sections
alongside `jsonschema`). Rationale: the compiler package now imports lark, so
it is a genuine core dependency; lark is pure-Python with no transitive deps
and no known CVEs, so it does not disturb the GitHub-passable security posture.
Least-invasive placement — not added to the Azure/demo tiers.

## 7. Open issues / notes for the planner

- The GBNF mirror is hand-maintained. Kept in sync by the shape test; if
  xgrammar lands in CI, `test_xgrammar_compiles_if_available` upgrades from
  skip to a real compile check. Consider pinning xgrammar in the GPU/serving
  requirements when W9/W10 stand up the vLLM box.
- The keyword-reservation asymmetry (§2) means decode-time is a hair looser
  than the Lark validator on identifier-vs-keyword. Downstream Scribble
  validation closes it; flagged for completeness.
- Sampler covers the message/choice/rec/continue/do core against
  `protocol_parser`. Multi-protocol `aux`/composition is validated only via the
  corpus (criterion 1), because `protocol_parser` grabs the first
  `global protocol` header and cannot round-trip multi-protocol files.
