# W4 — T0 baseline runner

*(Codenames: the "seam" is the intent-to-protocol translation step — a plain-language request becomes a Scribble-validated protocol; `W4` is this report's worker task-card id in the seam-training program, [`SEAM_TRAINING_EXECUTION_PLAN.md`](../../SEAM_TRAINING_EXECUTION_PLAN.md).)*


**Task card:** `docs/reference/SEAM_TRAINING_EXECUTION_PLAN.md` §9, row W4 —
"T0 baselines + E5 cell fill; done when `T0_baselines.md` + JSONL." §4 "T0"
and §7 metric block are the design source. Branch: `gc/seam-w4-t0-baselines`,
based on `origin/gc/paper-v8-iclr-reposition-concurrent-work` (the feature
branch carrying the merged eval harness — `experiments/seam_bench/eval/` is
not yet on `main`) @ `20ff7eb`.

**The transport constraint this package is built around:** this environment
has no `ANTHROPIC_API_KEY` and cannot spawn drafting sub-agents. Nothing in
`experiments/seam_bench/t0/` calls an LLM API. The package builds and
mock-tests the *runner*; the planner's own subscription-subagent drafting
workflow produces the real drafts, replayed through this harness via
`FileDrafter`.

---

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [1. What was built — `experiments/seam_bench/t0/`](#1-what-was-built--experimentsseam_bencht0)
- [2. The drafts-JSONL schema the planner's workflow must produce](#2-the-drafts-jsonl-schema-the-planners-workflow-must-produce)
- [3. The 23-pair extraction — discovery rule and real-Scribble result](#3-the-23-pair-extraction--discovery-rule-and-real-scribble-result)
- [4. Test suite](#4-test-suite)
- [5. End-to-end integration check (not part of the committed test suite)](#5-end-to-end-integration-check-not-part-of-the-committed-test-suite)
- [6. Exact command the planner will run once drafts exist](#6-exact-command-the-planner-will-run-once-drafts-exist)
- [7. Deviations from the brief, and why](#7-deviations-from-the-brief-and-why)
- [8. Artifacts](#8-artifacts)
<!-- MENU:END -->

## 1. What was built — `experiments/seam_bench/t0/`

| module | contents |
|---|---|
| `drafter.py` | The `Drafter` ABC: `draft(intent, k, exemplars=None) -> list[str]`, `repair(intent, broken, counterexample) -> str`, per the card's exact signatures. `MockDrafter` — deterministic, scripted per-intent draft/repair sequences, for offline tests. `FileDrafter` — replays a planner-produced drafts JSONL (schema below), keyed by an explicit `intent_to_item_id` map (the Drafter interface carries no item_id by design, so FileDrafter needs this to do exact lookups rather than fuzzy intent matching). `UsageInfo`/`estimate_usage()` — optional real-usage hook (`Drafter.usage_for(text)`) so a real drafting run's token/$ numbers survive into RunRecords when available, falling back to a word-count-at-$0 estimate (W1 smoke.py's own convention) with the estimate flagged in `RunRecord.model`. `split_guard_sidecar()` / `GUARD_SIDECAR_SENTINEL` — the `=== REFN ===` convention letting a single `draft()` string carry both the `.scr` protocol and an optional co-emitted `.refn` guard sidecar (§2: "Scribble `.scr` + `.refn` guard sidecar when the intent implies value constraints"), used by both validation (protocol-only half) and the guard-co-emission measurement (full text). |
| `repair_loop.py` | `run_repair_chain()` — the T+R production loop (§2/§4: draft → validate → on reject, repair with the validator's counterexample → revalidate, capped at 3 rounds). Returns one `RunRecord` per attempt; every record in a chain carries the chain's *final* total-rounds-used in `repair_rounds` (metrics.py reads it off the highest-`k` record). Always validates/bisims the protocol-only half of a guard-sidecar-bearing draft; `RunRecord.draft` keeps the full text. |
| `exemplars.py` | Stdlib-only Okapi BM25 (`re`+`math`+`collections.Counter`, no third-party dependency) over `(item_id, intent, protocol)` triples, for the +few-shot systems (S1: "BM25 over train-split intents, 3 exemplars"). `ExemplarIndex.top_k`/`top_k_pairs`, with `exclude_item_ids` so an item never retrieves itself. |
| `gold_pairs.py` | The T0 input-universe builder: `extract_gold_pairs()` walks `experiments/cases/` by `case.yaml` presence only (no hardcoded case-name list — see §3 below), reads `intent` + the gold (a known-correct reference answer) protocol (`protocols/v1.scr`, falling back to `protocols/<protocol_name>.scr` for the two skills_safety sub-cases that don't have a `v1.scr`), and the optional `.refn` sidecar. Fails loud (raises) on any case missing an intent, a resolvable protocol file, or a duplicate `case_id`. `to_dataset_records()` converts to W1's `DatasetRecord` shape. CLI dumps the set as JSONL. |
| `run_t0.py` | The systems×items orchestrator. `SystemConfig(label, drafter, k, few_shot_k, use_repair)` — one systems-matrix row. `run_item()`: best-of-k phase (k drafts, validated + bisim'd, `repair_rounds=None`) under `system.label`; if `use_repair=True`, additionally runs the repair chain seeded from candidate #1 under the **distinct** label `f"{label}+repair"` — so best-of-k (S2-style) and repair-loop (S3-style) land in separate report rows from one drafter config, per the card's "run best-of-k + repair" per item. `run_matrix()` sweeps every (system, item) cell. `guard_co_emission_rate()`/`guard_co_emission_table()` — the T0-only derived stat (deliberately NOT added to W1's fixed `RunRecord`/`metrics.py`, which is a planner-owned schema — see W1's report: "do not redesign the field sets"). CLI (`main()`) connects everything: gold-pair extraction → systems-config JSON → `run_matrix` → RunRecord JSONL → `eval.report_gen.build_report` (the standard §7 block) + the guard-co-emission table appended. |
| `smoke_gold.py` | Card item 6: extract the 23 pairs, validate every one under the **real** Scribble-java CLI, no drafting. |
| `tests/` | 47 pytest tests (below), fully offline via `MockDrafter` + fake validate/bisim callables, except `test_gold_pairs.py`'s one real-Scribble regression test. |

## 2. The drafts-JSONL schema the planner's workflow must produce

One JSON object per line, read by `FileDrafter(path, system=<label>, intent_to_item_id=...)`:

```json
{
  "item_id":    "auction",              // REQUIRED. Must match a GoldPair.id
  "system":     "s0-sonnet-zeroshot",   // REQUIRED. Filters rows per FileDrafter instance
  "kind":       "draft",                 // REQUIRED. "draft" | "repair"
  "k_index":    1,                       // REQUIRED. draft: 1..k best-of-k slot.
                                          //           repair: 1..3 repair round.
  "draft_text": "module auction; global protocol ...",  // REQUIRED
  "tokens_in":  null,   // OPTIONAL real usage (default null -> word-count estimate)
  "tokens_out": null,
  "usd":        null,
  "model":      null    // e.g. "claude-sonnet-5-20260115"
}
```

One file may hold rows for several systems (`system` field distinguishes
them); `run_t0.py`'s `--systems-config` JSON maps each system label to a
`(jsonl_path, k, few_shot_k, use_repair)` row (schema in `run_t0.py`'s
module docstring). A draft that wants to co-emit a `.refn` guard sidecar
appends it to `draft_text` after a line containing exactly `=== REFN ===`
(`drafter.py::GUARD_SIDECAR_SENTINEL`) — everything before is the `.scr`
protocol (what gets validated/bisim'd), everything after is the sidecar
(what guard-co-emission measures).

## 3. The 23-pair extraction — discovery rule and real-Scribble result

No hardcoded case-name list: every immediate subdirectory of
`experiments/cases/` with its own `case.yaml` is a case (the 17 top-level
named cases); every immediate subdirectory of a directory that does *not*
have its own `case.yaml` but has children that do is scanned one level
down (picks up the 6 `skills_safety/<subcase>` cases). `_corpus/` (seed
skeletons, no `case.yaml` anywhere under it) and `composition/` (raw
`.scr` fragments for incremental-composition testing, likewise no
`case.yaml`) are excluded by construction, not by name.

Verified against this checkout: **exactly 23 case dirs**, all 23 have a
non-empty `intent` and a resolvable gold protocol, all unique `case_id`.

```
$ bash tools/setup_scribble_cloud.sh
scribble-java smoke: gold PASS, corrupt REJECTED  [real toolchain OK]

$ python -m experiments.seam_bench.t0.smoke_gold
extracted 23 gold pairs from experiments/cases/
  PASS  auction                  (auction)
  PASS  banking                  (banking)
  PASS  clinical_enrollment      (clinical_enrollment)
  PASS  code_review              (code_review)
  PASS  finance                  (finance)
  PASS  finance_nested           (finance_nested)
  PASS  intel_report             (intel_report)
  PASS  iterative_polling        (iterative_polling)
  PASS  nested_retry             (nested_retry)
  PASS  rag                      (rag)
  PASS  report_pipeline          (report_pipeline)
  PASS  report_pipeline_large    (report_pipeline_large)
  PASS  retry_loop               (retry_loop)
  PASS  airline_seat             (skills_safety/airline_seat)
  PASS  booking_saga             (skills_safety/booking_saga)
  PASS  code_execution           (skills_safety/code_execution)
  PASS  content_pipeline         (skills_safety/content_pipeline)
  PASS  doc_pipeline             (skills_safety/doc_pipeline)
  PASS  pr_merge                 (skills_safety/pr_merge)
  PASS  trade_deadlock           (trade_deadlock)
  PASS  trade_settlement         (trade_settlement)
  PASS  travel                   (travel)
  PASS  travel_saga              (travel_saga)

gold protocols validated: 23/23
SMOKE OK — all extracted gold protocols validate under the real Scribble-java CLI.
```

23/23 — nothing to investigate. `doc_pipeline` and `pr_merge` (the two
skills_safety sub-cases whose protocol file is named after the protocol,
not `v1.scr`) correctly exercise the `<protocol_name>.scr` fallback path
(regression-guarded by `test_pr_merge_and_doc_pipeline_resolve_the_protocol_name_fallback`).

Artifacts: `experiments/seam_bench/t0/smoke_out/gold_pairs.dev.jsonl` (23
DatasetRecords) and `gold_verdicts.json` (per-id valid/validator_msg),
committed.

**Deviation, documented:** `DatasetRecord.source` has no "hand-authored"
value in W1's fixed vocabulary (`{"synthetic", "mined"}`); these 23 pairs
are neither D1-D3 synthetic generation nor D5 mined, so they're recorded
as `source="synthetic"` with `gen={"kind": "hand_authored_case", ...}`
flagging the distinction. `split` defaults to `"dev"` — these pairs predate
W3's D4 split build and are a pre-training measurement set, not a
phase-gated test split; nothing here touches `test-syn`/`test-real`.

## 4. Test suite

```
$ python -m pytest experiments/seam_bench/t0/tests/ -q
...............................................  [100%]
47 passed in 8.25s
```

Coverage against the card's named scenarios (item 5):
- **repair loop terminates and caps at 3** —
  `test_repair_loop_terminates_and_caps_at_3` (a drafter that never
  produces anything valid still stops at 1 initial + 3 repairs = 4
  records) and `test_repair_loop_respects_a_smaller_max_rounds_cap`.
- **MockDrafter returning gold → validity@1=1.0** —
  `test_mock_drafter_returning_gold_yields_validity_at_1_of_1`.
- **garbage then gold → validity@1=0, repair-rounds=1** —
  `test_garbage_then_gold_repair_yields_validity_at_1_zero_repair_rounds_one`.
- **metrics connect through** — every repair_loop/run_t0 test asserts the
  produced `RunRecord`s round-trip through W1's real `metrics.py`
  functions (`validity_at_1`, `validity_at_k`, `repair_rounds_mean`), not
  a reimplementation.
- **23-pair extractor: shape + real Scribble** — `test_gold_pairs.py`
  (offline shape/discovery tests + the one real-toolchain-backed
  regression test asserting all 23 validate).

Also covered, beyond the letter of the card: `FileDrafter`'s JSONL
parsing/filtering/ordering and its `KeyError` fail-loud paths (missing
item, insufficient pre-generated k/rounds); `split_guard_sidecar`;
`estimate_usage`'s real-vs-estimated branch; BM25 ranking/exclusion/`k`
edge cases; `run_t0.run_item`/`run_matrix` wiring (best-of-k-only,
best-of-k+repair under the split system label, exemplar retrieval passed
through to `draft()`, exemplar-index-required-when-few_shot_k>0);
guard-co-emission counting only items whose *gold* has a refn.

Full harness regression (unaffected by this branch): `python -m pytest
experiments/seam_bench/eval/tests/ -q` → 86 passed.

## 5. End-to-end integration check (not part of the committed test suite)

Ran the actual `run_t0.py` CLI against a synthetic "echo" drafter
(drafts = the gold `.scr` text verbatim, `use_repair=True`) across all 23
gold pairs, real Scribble validation + real E5 bisim throughout:

```
$ python -m experiments.seam_bench.t0.run_t0 \
    --systems-config <echo-system.json> --out-dir <tmp> --resamples 500
loaded 1 system(s) from <echo-system.json>: ['s0-echo']
wrote 46 RunRecords -> <tmp>/run.dev.jsonl
wrote report -> <tmp>/report.md

real  2m47.991s
```

Report: `validity@1`/`bisim@1` = 100% (n=23) for both `s0-echo` (best-of-k
row) and `s0-echo+repair` (repair-loop row, `repair-rounds=0.00` since the
echoed draft is already valid on the first attempt); `guard co-emission` =
0.0% (n=21 — the echo drafts never appended a `=== REFN ===` sidecar,
correctly counted only over the 21 gold pairs whose own `.refn` is
non-null). This is not committed as a fixture (it is a synthetic wiring
check, not a baseline number) but proves the full pipeline — extraction →
matrix → real-verifier scoring → W1 report_gen → guard-co-emission table —
runs clean end-to-end with real Scribble/E5 in the loop. At ~2.9s/item
serially (validate + bisim, unbatched per item — `run_item` does not use
W1's `validate_many`/`bisim_many` thread pool), a real multi-system sweep
should batch across items if wall-clock matters; not done here since T0's
correctness, not throughput, was the deliverable.

## 6. Exact command the planner will run once drafts exist

```
python -m experiments.seam_bench.t0.run_t0 \
    --systems-config systems.json \
    --out-dir experiments/seam_bench/t0/t0_out \
    --resamples 10000
```

where `systems.json` is a JSON array of
`{"label", "jsonl", "k", "few_shot_k", "use_repair"}` rows (§2/§6 of
`run_t0.py`'s module docstring) pointing at drafts JSONL(s) matching §2's
schema, produced by the planner's subscription-subagent drafting workflow.
`--systems-config` is the only required flag; `run_t0.py` builds the
23-item gold-pair set itself via `gold_pairs.extract_gold_pairs()` (no
need to pre-materialize a gold-pairs JSONL, though `gold_pairs.py`'s own
CLI can dump one if useful: `python -m experiments.seam_bench.t0.gold_pairs
OUT.jsonl --split dev`).

## 7. Deviations from the brief, and why

1. **Guard co-emission is a T0-only derived stat, not a W1 schema
   addition.** `RunRecord`/`metrics.py` are planner-fixed (W1's own
   report: "do not redesign the field sets"); there is no per-record
   guard-emission field to add without a schema change requiring planner
   sign-off. `run_t0.py::guard_co_emission_rate/_table` compute it purely
   from `RunRecord.draft` (via `split_guard_sidecar`) plus each item's
   `gold.refn is not None`, and append their own markdown section after
   `report_gen.build_report`'s output rather than living inside it.
2. **The `=== REFN ===` sidecar convention is new** — the card specifies
   `Drafter.draft/repair` return plain `str`/`list[str]`, which has no
   room for a separate guard-sidecar field. This is the minimal
   convention that keeps the literal interface, keeps `RunRecord.draft`
   as the single source of truth for "what did the system emit", and
   still makes guard-co-emission measurable. The planner's drafting
   workflow needs to know this convention to get credit for guards it
   emits — flagged here and in `drafter.py`'s docstring.
3. **`Drafter.usage_for()` is an addition beyond the literal
   `draft()`/`repair()` signatures** — an optional hook (default `None`)
   so `FileDrafter` can carry real token/$ numbers through when the
   planner's workflow records them, without changing the required
   signatures or forcing every implementation to track usage.
4. **Few-shot retrieval in T0 is leave-one-out over the 23 gold pairs
   themselves**, not a dedicated train-split intent pool — W3's D4 split
   build does not exist yet. `build_exemplar_index()` documents this as a
   T0 scope choice; it will need to point at the real `train` split once
   W3 lands.
5. **`run_item` does not batch validate/bisim calls across items** (uses
   `validity.validate`/`bisim_equivalent` directly, not
   `validate_many`/`bisim_many`). Correctness over throughput was the
   brief; a real multi-system run should wrap `run_matrix` in a thread
   pool over items if wall-clock becomes a problem (§5's ~2.9s/item
   serial figure is the number to plan against).
6. **Zero API spend** — confirmed; nothing in this package calls an LLM.

## 8. Artifacts

- `experiments/seam_bench/t0/` — the package + tests (all committed).
- `experiments/seam_bench/t0/smoke_out/{gold_pairs.dev.jsonl,
  gold_verdicts.json}` — the 23-pair real-Scribble smoke result,
  committed so it's reviewable without re-running the toolchain.

Repro: `bash tools/setup_scribble_cloud.sh`, then
`python -m pytest experiments/seam_bench/t0/tests/ -q` and
`python -m experiments.seam_bench.t0.smoke_gold` from the repo root.
