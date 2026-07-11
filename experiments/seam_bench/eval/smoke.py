"""smoke.py — the W1 done-criterion: metrics reproduce on the 30-corpus
smoke set; opened-test log exists.

Builds a tiny dev-split Seam-Bench slice out of the 30 seed skeletons in
`experiments/cases/_corpus/corpus_*.scr` (SEAM_TRAINING_EXECUTION_PLAN.md §3
"Ground truth inventory"), treating each as a DatasetRecord with
`intent=None`, `split="dev"`, `family=<filename stem>` (no back-translation
or family-signature dedupe yet — that is W3's job). For each corpus protocol
it then runs the REAL Scribble-java validator (via validity.py — the actual
org.scribble.cli.CommandLine, subprocess-wrapped; wire a fresh checkout with
`bash tools/setup_scribble_cloud.sh`) on the protocol *as its own draft* —
every one of these must validate, since they are exactly the protocols the
validator already accepts; a failure here means the corpus or the adapter
regressed and is reported as a smoke FAILURE, not silently skipped — plus
two kinds of deliberately corrupted copies (brace deletion; an
undeclared-role swap) that must fail. It also runs the E5 bisimulation check
on each valid item against itself (a trivial True — the smoke set has no
independent gold, so this only exercises the adapter's wiring, not
equivalence discrimination; W3/W4 exercise bisim@k against real mutants).

All 90 validator calls go through `validity.validate_many` (the thread-pool
bulk path), so this script is also the live test of the pool + verdict
cache.

Usage:
    python -m experiments.seam_bench.eval.smoke [--out-dir DIR] [--workers N]

Writes, under `--out-dir` (default `experiments/seam_bench/eval/smoke_out/`):
    dataset.dev.jsonl   DatasetRecord per corpus protocol
    run.dev.jsonl        RunRecord per (protocol, corruption) draft attempt
    report.md            report_gen output over run.dev.jsonl

Exit code 0 iff every uncorrupted corpus protocol validated (as expected)
and every corrupted copy failed to validate (as expected). Both conditions
are printed explicitly either way.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))

from experiments.seam_bench.eval import report_gen, schema, validity  # noqa: E402
from experiments.seam_bench.eval.schema import DatasetRecord, RunRecord  # noqa: E402

CORPUS_DIR = REPO_ROOT / "experiments" / "cases" / "_corpus"
DEFAULT_OUT_DIR = HERE / "smoke_out"

_MSG_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<label>\w+)\((?P<type>\w+)\)\s+from\s+(?P<from>\w+)"
    r"\s+to\s+(?P<to>\w+)\s*;\s*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _corrupt_delete_brace(text: str) -> str:
    """Delete the last closing brace — breaks the protocol block structure,
    expected to be a syntax-level rejection."""
    idx = text.rstrip().rfind("}")
    if idx == -1:
        raise ValueError("no closing brace found to delete")
    return text[:idx] + text[idx + 1:]


def _corrupt_swap_role(text: str) -> str:
    """Retarget the first message's receiver to an undeclared role name —
    Scribble must reject a reference to a role that was never declared in
    the protocol's role list."""
    m = _MSG_LINE.search(text)
    if not m:
        raise ValueError("no message line found to corrupt")
    bogus_role = m.group("to") + "Undeclared"
    start, end = m.span("to")
    return text[:start] + bogus_role + text[end:]


def _dataset_record(path: Path) -> DatasetRecord:
    text = path.read_text(encoding="utf-8")
    stem = path.stem
    return DatasetRecord(
        id=stem, family=stem, split="dev", intent=None, protocol=text,
        refn=None, source="synthetic", seed_case=stem,
        gen={"kind": "corpus_skeleton", "path": str(path.relative_to(REPO_ROOT))},
        provenance=None)


def _run_record(*, item_id: str, draft: str, valid: bool, msg: str,
                 bisim: bool | None) -> RunRecord:
    # No LLM was called anywhere in this script (deterministic smoke set) —
    # token/cost fields are a word-count proxy, never a billed API call.
    tokens = len(draft.split())
    return RunRecord(
        system="smoke-selfcheck", model="none (deterministic, zero API spend)",
        item_id=item_id, split="dev", k=1, draft=draft, valid=valid,
        validator_msg=msg, bisim=bisim, repair_rounds=0 if valid else None,
        tokens_in=tokens, tokens_out=0, usd=0.0, ts=_now())


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--workers", type=int, default=validity.DEFAULT_MAX_WORKERS)
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    validity.require_toolchain()  # fail loud + first, not per-item

    corpus_paths = sorted(CORPUS_DIR.glob("corpus_*.scr"))
    if not corpus_paths:
        print(f"FAIL: no corpus protocols found under {CORPUS_DIR}")
        return 2
    print(f"found {len(corpus_paths)} corpus protocols under {CORPUS_DIR}")

    dataset_records = [_dataset_record(p) for p in corpus_paths]
    n_ds = schema.write_jsonl(out_dir / "dataset.dev.jsonl", dataset_records)
    print(f"wrote {n_ds} DatasetRecords -> {out_dir / 'dataset.dev.jsonl'}")

    # Build the full draft battery: (item_id, draft, expect_valid) triples.
    battery: list[tuple[str, str, bool]] = []
    unexpected: list[str] = []
    for path in corpus_paths:
        text = path.read_text(encoding="utf-8")
        stem = path.stem
        battery.append((stem, text, True))
        try:
            battery.append((f"{stem}::corrupt-brace",
                             _corrupt_delete_brace(text), False))
        except ValueError as e:
            unexpected.append(f"{stem}: brace-deletion corruption failed: {e}")
        try:
            battery.append((f"{stem}::corrupt-role",
                             _corrupt_swap_role(text), False))
        except ValueError as e:
            unexpected.append(f"{stem}: role-swap corruption failed: {e}")

    # Validate everything through the bulk thread-pool path.
    t0 = time.monotonic()
    verdicts = validity.validate_many([d for _, d, _ in battery],
                                       max_workers=args.workers)
    validate_s = time.monotonic() - t0
    print(f"validated {len(battery)} drafts in {validate_s:.1f}s "
          f"(workers={args.workers}, cache={validity.cache_stats()})")

    # E5 self-equivalence for the drafts that validated with an expected-True
    # verdict (gold = the draft itself; wiring check only, see docstring).
    bisim_targets = [(item_id, draft)
                     for (item_id, draft, expect), (ok, _msg)
                     in zip(battery, verdicts) if expect and ok]
    t0 = time.monotonic()
    bisim_verdicts = validity.bisim_many([(d, d) for _, d in bisim_targets],
                                          max_workers=args.workers)
    bisim_s = time.monotonic() - t0
    bisim_by_id = {item_id: ok for (item_id, _d), (ok, _why)
                   in zip(bisim_targets, bisim_verdicts)}
    print(f"bisim self-checked {len(bisim_targets)} valid drafts in "
          f"{bisim_s:.1f}s")

    run_records: list[RunRecord] = []
    for (item_id, draft, expect), (ok, msg) in zip(battery, verdicts):
        rec = _run_record(item_id=item_id, draft=draft, valid=ok, msg=msg,
                           bisim=bisim_by_id.get(item_id))
        run_records.append(rec)
        if ok != expect:
            if expect:
                unexpected.append(f"{item_id}: expected valid=True, got "
                                   f"{ok} ({msg[:200]!r})")
            else:
                unexpected.append(f"{item_id}: expected valid=False, got {ok} "
                                   f"(silently accepted a corrupted protocol!)")
        print(f"  {item_id}: valid={ok} expect={expect} "
              f"bisim={bisim_by_id.get(item_id)}")

    n_run = schema.write_jsonl(out_dir / "run.dev.jsonl", run_records)
    print(f"wrote {n_run} RunRecords -> {out_dir / 'run.dev.jsonl'} "
          f"({validate_s + bisim_s:.1f}s total validator/bisim wall time)")

    # Done-criterion "opened-test log exists": the smoke set is dev-only, so
    # the guard correctly logs nothing above. Demonstrate the mechanism once
    # with an explicitly-labeled synthetic dummy: one fake test-syn record,
    # read through the sanctioned guarded reader, which appends the entry to
    # the real opened-test log. No real test split exists yet (W3 builds the
    # splits), so nothing is actually being leaked.
    from experiments.seam_bench.eval import test_access_log
    dummy = run_records[0]
    dummy_path = out_dir / "opened_test_demo.test-syn.jsonl"
    schema.write_jsonl(dummy_path, [RunRecord(**{
        **{f: getattr(dummy, f) for f in (
            "system", "model", "draft", "valid", "validator_msg", "bisim",
            "repair_rounds", "tokens_in", "tokens_out", "usd", "ts", "k")},
        "item_id": "smoke-dummy", "split": "test-syn"})])
    test_access_log.guarded_read_jsonl(
        dummy_path, RunRecord, caller="smoke.main",
        reason="W1 smoke: demonstrate opened-test logging on a synthetic "
               "dummy item (no real test split exists yet)")
    print(f"opened-test log demonstrated -> {test_access_log.DEFAULT_LOG_PATH}")

    report = report_gen.build_report(
        run_records, n_resamples=2000,
        title="Seam-Bench smoke report (30-corpus dev slice)")
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"wrote report -> {report_path}")

    print()
    n_gold = sum(1 for _, _, expect in battery if expect)
    n_gold_pass = sum(1 for (_, _, expect), (ok, _) in zip(battery, verdicts)
                      if expect and ok)
    n_corrupt = sum(1 for _, _, expect in battery if not expect)
    n_corrupt_rejected = sum(
        1 for (_, _, expect), (ok, _) in zip(battery, verdicts)
        if not expect and not ok)
    print(f"corpus protocols validated: {n_gold_pass}/{n_gold}")
    print(f"corrupted copies rejected:  {n_corrupt_rejected}/{n_corrupt}")
    if unexpected:
        print(f"SMOKE FAILURE — {len(unexpected)} unexpected outcome(s):")
        for line in unexpected:
            print(f"  - {line}")
        return 1

    print("SMOKE OK — every corpus protocol validated under the real "
          "Scribble-java CLI; every corrupted copy was rejected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
