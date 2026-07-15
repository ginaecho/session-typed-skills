# W15 — recursion generator for D1 (recursion axis was ~9 families, now 200+)

Worker: W15 (implementation). Branch `gc/seam-w15-recursion-gen`.
Spec: `docs/reference/SEAM_TRAINING_EXECUTION_PLAN.md` §3 (D1 structural-
diversity floor) + task card. Diagnosis this fixes:
`docs/reference/reports/seam/W3_data_builders.md`'s "recursion axis
saturates at ~9 families" finding (the only recursion-bearing D1 operator
was `d1_expand.py::retry_shape`, a single near-deterministic grid cell).

**Branch-base note for the planner:** `origin/main` at the time this
worker started did NOT yet contain W3's data builders (they live on
`origin/gc/seam-w3-data-builders`, which diverged from `origin/main` at
merge-base `592dc313`). Branching off `origin/main` literally as instructed
would have produced a worktree with no `d1_expand.py` to extend. This
branch is `origin/gc/seam-w3-data-builders` + a merge of `origin/main` on
top (commit `1f75003`, zero conflicts — `origin/main`'s 7 extra commits
never touch `experiments/seam_bench/data/`), so it carries both W3's code
and the latest plan docs. Flagging this so the eventual `main` merge order
is deliberate, not a surprise.

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [What exists](#what-exists)
- [Design — studied the 4 real recursive protocols in the repo first](#design--studied-the-4-real-recursive-protocols-in-the-repo-first)
- [A real bug found (and fixed) during development](#a-real-bug-found-and-fixed-during-development)
- [Signature correctness on recursion — verified, not assumed](#signature-correctness-on-recursion--verified-not-assumed)
- [D1-integration bug found and fixed (found because of this work)](#d1-integration-bug-found-and-fixed-found-because-of-this-work)
- [Numbers — recursion-focused build (well past the ≥60 target)](#numbers--recursion-focused-build-well-past-the-60-target)
- [Mixed-build integration (the operator as it lives in `d1_expand.py`)](#mixed-build-integration-the-operator-as-it-lives-in-d1_expandpy)
- [Sample protocols (one per shape, from `samples/d1_recursive.jsonl`)](#sample-protocols-one-per-shape-from-samplesd1_recursivejsonl)
- [Tests](#tests)
- [Exact build commands (reproduction)](#exact-build-commands-reproduction)
- [Done-criteria vs task card](#done-criteria-vs-task-card)
<!-- MENU:END -->

## What exists

New file `experiments/seam_bench/data/recursion_gen.py`, imported by
`d1_expand.py` as a fourth D1 operator (`recursive`, alongside `sweep`/
`compose`/`crossover`). New test file
`experiments/seam_bench/data/tests/test_recursion_gen.py` (10 tests). Two
new tests added to the existing `test_d1_expand.py` (integration + a
real bug fix, below). Toolchain preflight (`bash tools/setup_scribble_cloud.sh`)
run in this worktree before any of the numbers below.

## Design — studied the 4 real recursive protocols in the repo first

Read `experiments/cases/{retry_loop,iterative_polling,nested_retry,rag}/
protocols/v1.scr` directly (all four `rec`-containing protocols that exist
anywhere under `experiments/cases/**/protocols/*.scr`) plus the grammar
(`stjp_core/compiler/scribble_grammar.lark`) and the "unsafe" broadcast
counter-example in `experiments/CLAUDE.md`, before writing any generator
code. Findings that shaped the design:

- Well-formedness in this fragment is enforced entirely by real
  scribble-java — there is no Python-side recursion/projectability logic
  anywhere in the repo (`validator.py` is a 95-line subprocess wrapper).
  So "gate every candidate through the real validator" is not optional
  scaffolding here, it is the ONLY check that exists.
- Every branch of a `choice` inside a loop must broadcast its messages to
  every role that later needs to act differently — confirmed by the
  `finance/protocols/llm_drafts/unsafe/v1.scr` counter-example (a role
  left uninformed on one branch produces a Scribble wait-for-cycle
  rejection).
- `nested_retry.scr` proved a rule NOT visible from the flatter examples:
  nesting a second `choice at <role>` inside a branch requires a message
  to have been sent to `<role>` **earlier in that same branch** first —
  Scribble rejects an un-preceded nested subject with `Subject not
  enabled: <role>`. This was found the hard way (see "bug found" below),
  not assumed.
- `rag.scr` proved a role can be declared and used only in the protocol's
  prefix, never touched again inside `rec` (peripheral, non-looping
  roles) — and that a 3-branch choice where only the middle branch
  continues is a valid shape (multiway exit).
- `continue X;` is always the literal last statement of the block it's
  in, in every real example — matched, not deviated from.
- nuscr's "non tail-recursive protocol not implemented" restriction is
  irrelevant here: nuscr is an opt-in cross-check/projection engine, never
  the validity oracle (SEAM_TRAINING_EXECUTION_PLAN.md §3); `nested_retry`
  itself isn't tail-recursive in the naive sense and validates fine
  against the actual oracle, `ScribbleValidator`.

`recursion_gen.py::gen_recursive(idx, seed)` then generalizes all four
shapes into a randomized generator (deterministic per `(seed, idx)` via
`random.Random(f"{seed}-recursive-{idx}")`, matching the existing
`gen_sweep`/`gen_compose`/`gen_crossover` contract) varying, independently:

| axis | values |
|---|---|
| total roles | 2, 3, 4, 5 |
| peripheral (prefix-only) roles | 0 or 1 |
| loop body shape | `linear` (retry/iterative_polling), `branching` (nested_retry, needs ≥3 loop roles), `multiway` (rag, needs ≥2 loop roles) |
| loop position | `prefix` (setup messages before `rec`) or `immediate` (`rec` is the protocol's first interaction) |
| choice branch order | exit-branch-first vs continue-branch-first |
| controller role | rotates across loop roles, not fixed to "the first role" |
| double loop | two SEQUENTIAL (non-nested) `rec` blocks, linear body each, separated by a handoff broadcast |

## A real bug found (and fixed) during development

The first version of the `branching` shape (nested choice, generalizing
`nested_retry`) nested `choice at <second role>` directly, with no message
to that role first. Every such candidate was rejected: `Subject not
enabled: L0`. Fixed by broadcasting a branch-marking message
(`Revise`/`Accept`) to every role — including the next decider — before
nesting, exactly mirroring what `nested_retry.scr` already does
(`Revise(String) from Editor to Author;` precedes `choice at Author`).
After the fix: 0 rejections across every subsequent run (see below).

## Signature correctness on recursion — verified, not assumed

Two independent checks, both against the repo's real pairwise E5 checker
(`experiments/scripts/efsm_equiv.py::protocols_equivalent`), the same
mechanism W3 used for the non-recursive population:

1. **Positive check** (`test_signatures_distinguish_shape_and_role_count`,
   `test_gen_recursive_varies_across_idx`): different body shapes / role
   counts / loop positions get DIFFERENT signatures — no accidental
   collapsing.
2. **Negative check** (`test_branch_order_collapses_to_same_signature`):
   two protocols differing ONLY in which choice branch is listed first
   (behaviourally identical) get the SAME signature — this is what
   BFS-canonicalized, sorted-transition signatures are supposed to do,
   and it is exactly the kind of case a naive text-order-sensitive
   signature would get wrong.
3. **Full-machinery cross-check**: ran
   `signature.py::verify_against_checker` (the same mechanism from
   `sig_verify_report.json`, this time restricted to the recursive
   population) over 80 of the 200 built recursive protocols, 150 random
   pairs, plus a 20-pair spot-check against the *unoptimized* checker
   (fresh Scribble calls both sides):

   ```
   python -c "
   import json, signature as sig
   recs = [json.loads(l) for l in open('samples/d1_recursive.jsonl')]
   print(sig.verify_against_checker([r['protocol'] for r in recs[:80]],
                                     n_pairs=150, seed=0))"
   ```
   Result (`samples/recursion_sig_verify_report.json`):
   `agreement_rate_pct: 100.0` (150/150), `spot_check_matches_real_checker:
   true` (20/20).

**Conclusion: `signature.py` handles `rec`/`continue` correctly.** No
signature bug was found on this population — `stjp_core.compiler.
incremental.efsm_signature` (the per-role canonicalization
`protocol_signature` reuses) already BFS-relabels states reached via
`continue` back-edges the same way it relabels any other state, so cyclic
EFSMs canonicalize the same way acyclic ones do.

## D1-integration bug found and fixed (found because of this work)

Wiring `recursive` into `d1_expand.py`'s mixed operator pool exposed a
**pre-existing, unrelated** concurrency bug in `d1_expand.py::build()`:
`as_completed()` returns futures in thread-completion order, an OS
scheduling race, not a function of `(seed, idx)`. The old code appended
results straight from that race, so two `build()` calls with the *same
seed* could commit results in different orders whenever task latency
varied enough for completion order to flip — which recursive candidates
(validate a bigger, nestier text than a plain sweep candidate) made
happen ~75% of the time instead of "rare flake." Confirmed by running
`test_build_is_deterministic_given_seed` in isolation 4x before the fix
(3/4 failures) and 5x after (5/5 pass). Fixed with a small in-order commit
buffer (`pending: dict[int, dict]` keyed by task idx, only folded into
`records`/`seen_sigs`/counters once every lower idx has committed) —
concurrency is unchanged, only the order results are folded into the
deterministic output is now actually deterministic. `d1_expand.py`'s
full 34-test suite passes after the fix (`python -m pytest tests/ -q` →
`34 passed`).

## Numbers — recursion-focused build (well past the ≥60 target)

```
python experiments/seam_bench/data/recursion_gen.py \
    --target 200 --max-candidates 800 --seed 1 --workers 4 \
    --curve-every 40 -o out/d1_recursive.jsonl
```

Real output:

```
[recursion_gen] 200 unique recursive families from 208 candidates
(0 rejected, 8 duplicate) in 301.1s -> out/d1_recursive.jsonl
```

- **200/200 target reached, 0 validator rejections across the whole run**
  (every candidate the generator produced was well-formed Scribble).
- Duplicate rate at the end: 8/203 ≈ 3.9%, still low and not visibly
  accelerating (see `samples/d1_recursive.stats.json`'s
  `saturation_curve`) — **no near-term ceiling observed**. The fragment
  this generator explores is comfortably larger than the ≥60-family
  target; there was no need to report a saturation curve as an honest
  shortfall because the run didn't hit one. (A longer run would very
  plausibly clear 1,000+; not run here since 200 already gives ~3.3x
  headroom over the target and the marginal value for THIS report is
  low — the reproduction command above is exact and cheap to extend via
  `--target`.)
- Structural coverage across the 200 families (`samples/d1_recursive.stats.json`,
  `samples/d1_recursive.jsonl`):
  - shape: linear 108, branching 30, multiway 62
  - role count: 2:33, 3:63, 4:60, 5:44 (all 4 required values present)
  - loop position: prefix 152, immediate 48
  - double_loop: 40 present (sequential, non-nested `rec` blocks — the
    untested-territory axis; all 40 validated)
  - peripheral (prefix-only) roles: 53 records carry one
  - guard sidecars (`.refn`): 56 records
  - **(role_count, depth_bucket) cross-tab has 7 cells, every cell has
    ≥3 families** (min cell = 6, at role_count=5/deep) — this is the
    concrete check for the plan's "every (topology, role-count) cell in
    train has a counterpart in dev and test-syn" requirement:
    `splitter.py` keeps strata with <3 families train-only by design; with
    this population every recursion stratum clears that bar, so recursion
    families will actually land in dev and test-syn on a real split, not
    just train.

## Mixed-build integration (the operator as it lives in `d1_expand.py`)

`_PATTERN` now allocates 3 of 12 task slots (25%) to `recursive`, under
the §3 "none exceeding 30%" cap. Verified end-to-end with a mixed
`d1_expand.build()` run:

```
python -c "
import d1_expand as d1
records, stats = d1.build(target=30, max_candidates=100, seed=1, workers=4,
                          curve_every=200, cache_path=None)
print(stats['operator_breakdown'])"
# {'sweep': 15, 'compose': 6, 'crossover': 6, 'recursive': 7}
```

`recursive` records carry `has_recursion=True` and validator-clean
`efsmv1:` signatures through the exact same pipeline (dedupe,
checkpointing, `DatasetRecord` shape) as every other operator — see
`test_mixed_build_yields_recursive_records`.

## Sample protocols (one per shape, from `samples/d1_recursive.jsonl`)

**linear** (2 roles, `loop_position=immediate`):
```
global protocol Recur(role L0, role L1) {
    rec LoopA {
        Report(Bool) from L1 to L0;
        choice at L0 {
            Accept(Bool) from L0 to L1;
            FinalSummary(String) from L0 to L1;
        } or {
            Retry(Bool) from L0 to L1;
            continue LoopA;
        }
    }
}
```

**branching** (3 roles — nested_retry-generalized, the shape that exposed
the `Subject not enabled` bug above):
```
global protocol Recur(role L0, role L1, role L2) {
    rec LoopA {
        Update(Double) from L1 to L0;
        Update(Double) from L1 to L2;
        choice at L1 {
            Revise(String) from L1 to L0;
            Revise(String) from L1 to L2;
            choice at L0 {
                MajorEdit(String) from L0 to L1;
                MajorEdit(String) from L0 to L2;
                continue LoopA;
            } or {
                MinorEdit(String) from L0 to L1;
                MinorEdit(String) from L0 to L2;
                continue LoopA;
            }
        } or {
            Accept(String) from L1 to L0;
            Accept(String) from L1 to L2;
            choice at L2 {
                Publish(String) from L2 to L0;
                Publish(String) from L2 to L1;
            } or {
                Schedule(String) from L2 to L0;
                Schedule(String) from L2 to L1;
            }
        }
    }
}
```

**multiway** (4 roles — rag-generalized, 3-branch choice, only the middle
branch continues):
```
global protocol Recur(role L0, role L1, role L2, role L3) {
    rec LoopA {
        Draft(Int) from L1 to L2;
        choice at L2 {
            Verified(String) from L2 to L0;
            Verified(String) from L2 to L1;
            Verified(String) from L2 to L3;
        } or {
            Revise(String) from L2 to L0;
            Revise(String) from L2 to L1;
            Revise(String) from L2 to L3;
            continue LoopA;
        } or {
            CannotAnswer(String) from L2 to L0;
            CannotAnswer(String) from L2 to L1;
            CannotAnswer(String) from L2 to L3;
        }
    }
}
```

## Tests

```
python -m pytest experiments/seam_bench/data/tests/ -q
# 34 passed in ~250s (all hit the real Scribble validator)
```

`test_recursion_gen.py` (10 new tests): determinism given `(seed, idx)`;
shape/role-count variety across idx; a 30-candidate real-validator smoke
(a quick end-to-end check) covering all 3 shapes; positive + negative signature-correctness checks
(different shapes differ, branch-order swap collapses to the same
family); targeted checks that `double_loop` and `branching` candidates
validate; peripheral-role containment (never leaks into the loop body);
tiny-budget `build()` validity/dedupe; `build()` determinism;
`make_refn` parses. `test_d1_expand.py` gained
`test_recursive_operator_reachable_in_pattern_and_under_30pct` and
`test_mixed_build_yields_recursive_records` (the mixed-pipeline
integration check above), plus the completion-order determinism fix
described earlier which makes the pre-existing
`test_build_is_deterministic_given_seed` reliably pass again.

## Exact build commands (reproduction)

```bash
bash tools/setup_scribble_cloud.sh                      # once per checkout
D=experiments/seam_bench/data

# standalone recursion-focused build (what produced the numbers above)
python $D/recursion_gen.py --target 200 --max-candidates 800 --seed 1 \
    --workers 4 --curve-every 40 -o out/d1_recursive.jsonl

# signature-vs-E5-checker cross-check on the recursive population
python -c "
import json, sys; sys.path.insert(0, '$D')
import signature as sig
recs = [json.loads(l) for l in open('out/d1_recursive.jsonl')]
print(sig.verify_against_checker([r['protocol'] for r in recs[:80]],
                                  n_pairs=150, seed=0))"

# mixed D1 build (recursive now a first-class operator, 25% share)
python $D/d1_expand.py --target 5000 --max-candidates 20000 --seed 1 \
    --workers 4 -o out/d1.jsonl
```

## Done-criteria vs task card

| criterion | status |
|---|---|
| new `recursive` generator, structurally varied (body shape, position, branch order, roles 2-5, single/double loop) | PASS — 7-axis grid above, see table |
| every candidate through the real validator | PASS — `validate_text` → real `ScribbleValidator`; 0 rejections in the 208-candidate run |
| dedupe by `signature.py`'s EFSM signature; verify signature handles `rec`/`continue` correctly | PASS — 100% agreement with the E5 checker (150 pairs, 20-pair spot-check vs unoptimized checker); no signature bug found |
| ≥60 distinct recursive families (or honest ceiling) | PASS, well past floor — 200/200 target reached, 0 rejections, dup rate still ~4% (no ceiling observed in this run) |
| cell coverage across a 599/76/76-style split (recursion in dev + test-syn, not just train) | PASS — every (role_count, depth_bucket) cell has ≥3 families, clearing `splitter.py`'s train-only threshold |
| tests: generated protocols validate, signatures distinguish loops, determinism per seed | PASS — 10 new tests in `test_recursion_gen.py`, 2 new in `test_d1_expand.py`, 34/34 total |
