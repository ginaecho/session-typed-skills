# W1 — Seam-Bench evaluation harness

**Task card:** `docs/reference/SEAM_TRAINING_EXECUTION_PLAN.md` §9, row W1 —
"eval harness: metric block + splits + JSONL schema + report generator; done
when metrics reproduce on the 30-corpus smoke set [smoke set = the fixture
used for a smoke test, a quick end-to-end check]; opened-test log exists."
Branch: `gc/seam-w1-eval-harness` (based on `origin/main` @ 592dc31, which
includes the real-toolchain mandate and `tools/setup_scribble_cloud.sh`).

---

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [1. What was built](#1-what-was-built)
- [2. Where the E5 checker was found](#2-where-the-e5-checker-was-found)
- [3. Toolchain setup (this worktree)](#3-toolchain-setup-this-worktree)
- [4. Smoke validation — exact commands and real output](#4-smoke-validation--exact-commands-and-real-output)
  - [4.1 Test suite](#41-test-suite)
  - [4.2 Smoke entry script](#42-smoke-entry-script)
  - [4.3 Smoke metric table (from `smoke_out/report.md`)](#43-smoke-metric-table-from-smoke_outreportmd)
  - [4.4 Opened-test log (done-criterion)](#44-opened-test-log-done-criterion)
- [5. A real finding from the smoke run](#5-a-real-finding-from-the-smoke-run)
- [6. Deviations from the brief, and why](#6-deviations-from-the-brief-and-why)
- [7. Artifacts](#7-artifacts)
<!-- MENU:END -->

## 1. What was built

Package `experiments/seam_bench/eval/`:

| module | contents |
|---|---|
| `schema.py` | `DatasetRecord` / `RepairRecord` / `RunRecord` dataclasses (field sets fixed by the planner; extension only via the optional `gen`/`provenance` dicts), split/source vocabulary checks, JSONL read/write/append with unknown-field rejection (typos fail loud). |
| `metrics.py` | The §7 standing metric block from RunRecord streams: validity@1/@k, semantic-validity@1, bisim@1/@k, repair-rounds (mean, capped 3), tokens-to-accepted, $-to-accepted, transfer gap. Panel metrics (`panel-score`, `probe-pass-rate`, `probe-compile-rate`) are explicit not-yet-instrumented stubs pointing at W6/W7. Statistics: paired bootstrap (item-level, shared `item_id`s, 10k-resample capable, 95% CI) for two-system deltas; exact McNemar (stdlib `math.comb`; scipy is not a repo dep) for validity@1 flips; single-sample bootstrap CI per table cell; unpaired bootstrap CI for the transfer gap (different item universes — no pairing exists). numpy used (already in `stjp_core/requirements-core.txt`). |
| `validity.py` | Adapters over the two REAL verifiers, per the real-toolchain mandate: (a) `validate()` → `stjp_core/compiler/validator.py::ScribbleValidator.validate_protocol` (shells to `org.scribble.cli.CommandLine`; pass rule = returncode 0 AND empty stdout — the JVM's `Picked up JAVA_TOOL_OPTIONS` stderr banner is ignored by construction); (b) `bisim_equivalent()` → the E5 checker (§2 below). Both run in a fresh subprocess in its own **process group** with a wall-clock timeout; on timeout the whole group is SIGKILLed so no orphaned `java` survives, and the verdict degrades to `(False, <timeout msg>)` instead of crashing the sweep. `require_toolchain()` raises `ToolchainMissing` (never warns, never falls back) if the jars are missing, with the `setup_scribble_cloud.sh` pointer in the message. Bulk path: `validate_many()`/`bisim_many()` fan out over a `ThreadPoolExecutor` (default 4 workers), dedupe inputs, and all verdicts are cached in-process keyed by protocol-text SHA-256 (timeouts are never cached — a transient stall must not brand a text invalid for the process lifetime). |
| `_worker.py` | The out-of-process body `python -m experiments.seam_bench.eval._worker` (stdin JSON → stdout JSON) that actually invokes the two verifiers. |
| `report_gen.py` | RunRecord JSONL(s) → markdown report in the `docs/results/RESULT_*.md` house style: one metric table per (system, split) with value, 95% CI, and n per cell; a transfer-gap table for any system holding both `test-syn` and `test-real` slices; the cumulative opened-test log appended as its own table. CLI: `python -m experiments.seam_bench.eval.report_gen OUT.md RUN.jsonl [...] --resamples 10000`. |
| `test_access_log.py` | The §7 gate-discipline guard: `guarded_read_jsonl()` is the sanctioned reader for any file that may contain `test-syn`/`test-real` records; it appends `{ts, split, caller, reason, path, n_records}` to `experiments/seam_bench/eval/opened_test.log.jsonl` per restricted split found, before returning records. `train`/`dev` reads log nothing. Content-level (inspects `.split` per record), so it works for one-split-per-file and mixed files alike. `report_gen` always loads through it. |
| `smoke.py` | The done-criterion script (§4 below). |
| `tests/` | 86 pytest tests: schema round-trip + validation, every metric + both statistics, guard-log semantics, report generation, and the verifier adapters exercised end-to-end against the real Scribble CLI (including the full 30-corpus regression, timeout-guard behavior, cache-hit proof via a sabotaged worker, pool order/dedupe, and fail-loud on missing toolchain). |

## 2. Where the E5 checker was found

`experiments/scripts/efsm_equiv.py` — module docstring self-identifies as
"BENCHMARK_PLAN_V2 §6 / E5". `protocols_equivalent(a, b)` = same role set +
per-role projected-EFSM bisimulation (product BFS, exact for Scribble's
deterministic EFSMs) + identical global conversation language (loops
unrolled once), built on `stjp_core/compiler/efsm_parser.py` and
`stjp_core/critic/protocol_paths.py`. It internally re-validates both
protocols through `ScribbleValidator`, so `bisim_equivalent()` is also
real-toolchain-backed end to end. `validity.py` wraps exactly this function.

## 3. Toolchain setup (this worktree)

```
$ bash tools/setup_scribble_cloud.sh
scribble-java smoke: gold PASS, corrupt REJECTED  [real toolchain OK]
nuscr smoke: binary runs  [OK]
export STJP_NUSCR_BIN=/workspace/bin/nuscr   # for the opt-in nuscr backend
```

(The /workspace shared build already existed; the script connected this
checkout's `scribble-java/scribble-dist/target/lib` symlink and re-ran its
self-test that a gold (a known-correct reference answer) protocol passes
and a corrupted one is rejected.)

## 4. Smoke validation — exact commands and real output

### 4.1 Test suite

```
$ python -m pytest experiments/seam_bench/eval/tests/ -q
........................................................................ [ 83%]
..............                                                           [100%]
86 passed in 35.82s
```

### 4.2 Smoke entry script

```
$ python -m experiments.seam_bench.eval.smoke
found 30 corpus protocols under .../experiments/cases/_corpus
wrote 30 DatasetRecords -> .../experiments/seam_bench/eval/smoke_out/dataset.dev.jsonl
validated 90 drafts in 13.8s (workers=4, cache={'validate': 90, 'bisim': 0})
bisim self-checked 30 valid drafts in 89.0s
  corpus_000: valid=True expect=True bisim=True
  corpus_000::corrupt-brace: valid=False expect=False bisim=None
  corpus_000::corrupt-role: valid=False expect=False bisim=None
  ... (30 x 3 lines, all as expected) ...
wrote 90 RunRecords -> .../experiments/seam_bench/eval/smoke_out/run.dev.jsonl (102.8s total validator/bisim wall time)
opened-test log demonstrated -> .../experiments/seam_bench/eval/opened_test.log.jsonl
wrote report -> .../experiments/seam_bench/eval/smoke_out/report.md
corpus protocols validated: 30/30
corrupted copies rejected:  60/60
SMOKE OK — every corpus protocol validated under the real Scribble-java CLI; every corrupted copy was rejected.
```

The battery: each of the 30 `_corpus` skeletons as its own draft (must
validate — all 30 did), plus per skeleton one brace-deletion corruption and
one undeclared-role retarget (all 60 rejected, with genuine Scribble error
text captured in `validator_msg`). E5 self-equivalence on the 30 valid
drafts (gold = the draft itself): 30/30 `bisimilar`/`equivalent` — wiring
check only; equivalence *discrimination* is exercised later against W3's
real mutants.

### 4.3 Smoke metric table (from `smoke_out/report.md`)

`smoke-selfcheck — dev (n_items=90, n_records=90)`; CIs are 95% item-level
bootstrap, 2000 resamples (the CLI's `--resamples 10000` is for real phase
gates):

| metric | value | 95% CI | n |
|---|---:|---:|---:|
| validity@1 | 33.3% | [25.5%, 40.4%] | 90 |
| validity@5/@10/@25 | 33.3% | [25.5%, 40.4%] | 90 |
| semantic-validity@1 | 50.0% | [40.0%, 59.5%] | 60 |
| bisim@1/@5/@10/@25 | 100.0% | [100.0%, 100.0%] | 30 |
| repair-rounds | 0.00 | [0.00, 0.00] | 30 |
| tokens-to-accepted | 92 | [84, 100] | 30 |
| usd-to-accepted | $0.0000 | [$0.0000, $0.0000] | 30 |
| panel-score / probe-pass-rate / probe-compile-rate | n/a (not-yet-instrumented; W6/W7) | — | 0 |

Sanity reading: validity@1 = 30/90 = 33.3% (exactly the uncorrupted third);
@k equals @1 because every item has a single k=1 draft; semantic-validity@1
= 30/60 (the 30 brace-corruptions are syntax-level rejects and drop out of
both numerator and denominator — see the heuristic note in §6); bisim n=30
(only golds have a non-null `bisim`); tokens are a word-count proxy
(deterministic run, zero API spend). The numbers reproduce by hand from the
JSONL, which is the point of the smoke set.

### 4.4 Opened-test log (done-criterion)

`experiments/seam_bench/eval/opened_test.log.jsonl` exists (append-only,
cumulative — one entry per smoke run performed while building this
harness): the smoke set is dev-only (so the guard correctly logs nothing
for it), and the mechanism is demonstrated on an explicitly-labeled
synthetic dummy `test-syn` record read through the sanctioned
`guarded_read_jsonl`:

```
{"caller": "smoke.main", "n_records": 1, "path": ".../smoke_out/opened_test_demo.test-syn.jsonl",
 "reason": "W1 smoke: demonstrate opened-test logging on a synthetic dummy item (no real test split exists yet)",
 "split": "test-syn", "ts": "2026-07-11T19:06:23+00:00"}
```

No real test split exists yet (W3 builds the splits), so nothing was leaked.
The report generator appends this log as its own table in every report.

## 5. A real finding from the smoke run

On the first pooled run, `corpus_003`'s E5 self-check exceeded the 30s
timeout under 4-way pool contention (it takes ~20s solo — E5 re-validates
and projects BOTH protocols per role, then walks the conversation language)
and was correctly degraded to `bisim=False` rather than hanging or crashing
— the guard behaved exactly as designed, and because timeouts are not
cached, a solo re-run returned `(True, 'equivalent')` in 20.0s. Consequence:
`DEFAULT_BISIM_TIMEOUT_S = 120.0` is now separate from the validator's 30s
guard. W5 (reward fn) should budget accordingly: bisim calls are ~10-40x a
plain validation.

## 6. Deviations from the brief, and why

1. **`semantic-validity@1` is a documented heuristic until W2's GCD lands.**
   §7 defines it as "validity under GCD (syntax impossible)". No
   grammar-constrained system exists yet and `RunRecord` carries no GCD
   flag (schema is fixed), so for non-GCD systems the metric excludes k=1
   drafts whose `validator_msg` matches ANTLR/parse-error phrasing from
   both numerator and denominator — "under GCD this draft could not have
   been sampled". For a genuine GCD run it degenerates to validity@1, which
   is the spec's definition. Flagged in the docstring.
2. **Per-cell CIs added beyond the letter of §7.** The spec names the paired
   bootstrap for deltas and McNemar for flips; the house rule "report 95%
   CIs, never bare means" is applied to the metric table itself via a
   single-sample item bootstrap, and to the transfer gap via an unpaired
   bootstrap (test-syn/test-real share no item_ids, so pairing is
   impossible there by construction).
3. **McNemar is the exact binomial form** (stdlib `math.comb`), with the
   continuity-corrected chi-square also reported; scipy is not a repo
   dependency and per-cell n at phase gates will be small enough that the
   exact test is the right default anyway.
4. **Guard is convention-plus-audit, not a hard deny.** `test_access_log`
   cannot stop code from calling `schema.read_jsonl` directly; it
   guarantees an audit trail for every read that goes through the
   sanctioned path (and `report_gen` only reads through it). Same
   enforcement model as the rest of the repo's persistence policies.
5. **Bisim timeout raised to 120s** after the §5 finding (the brief said
   "a hang cannot stall an eval run", which still holds — the guard fires
   and degrades, it just no longer fires on the slowest honest corpus item
   under pool contention).
6. **`tools/setup_scribble_cloud.sh` and the plan-doc mandate are not in my
   commits** — the branch was fast-forwarded onto origin/main @ 592dc31,
   which already contains them.
7. **Recorded `validator_msg` strips the JVM banner.** In this environment
   every `java` invocation prints `Picked up JAVA_TOOL_OPTIONS: ...`
   (proxy/truststore config) on stderr ahead of Scribble's own diagnostics.
   Per the mandate the banner never affects the verdict (pass rule =
   returncode 0 AND empty stdout); `_worker.py` additionally filters those
   lines out of the *recorded message* so committed JSONL artifacts carry
   only Scribble's text (e.g. `line 27:0 mismatched input '<EOF>'
   expecting '}'`, `Role not bound: R1Undeclared`) and no
   environment-specific paths.
8. **Zero API spend** — confirmed; nothing in the harness or its tests
   calls any LLM. The smoke RunRecords' token fields are word-count
   proxies and say so in the `model` field.

## 7. Artifacts

- `experiments/seam_bench/eval/` — the package + tests (all committed).
- `experiments/seam_bench/eval/smoke_out/{dataset.dev.jsonl, run.dev.jsonl,
  report.md, opened_test_demo.test-syn.jsonl}` — committed so the smoke
  numbers are reviewable without re-running the 2-minute battery.
- `experiments/seam_bench/eval/opened_test.log.jsonl` — the opened-test log.

Repro: `bash tools/setup_scribble_cloud.sh`, then
`python -m pytest experiments/seam_bench/eval/tests/` and
`python -m experiments.seam_bench.eval.smoke` from the repo root
(numpy + pytest required; numpy is already in
`stjp_core/requirements-core.txt`).
