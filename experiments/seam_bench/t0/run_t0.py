"""run_t0.py — orchestrates the T0 systems x items matrix.

SEAM_TRAINING_EXECUTION_PLAN.md §4 T0 / §7 systems matrix. For every
(item, system) pair: draw `k` candidate drafts (best-of-k), validate every
one through the REAL Scribble-java CLI (never a weaker approximation —
`experiments.seam_bench.eval.validity`), check bisim-to-gold with W1's E5
adapter for any that validate; if the system is configured with
`use_repair=True`, additionally run the T+R production loop
(`repair_loop.run_repair_chain`) starting from the k=1 candidate, under a
DISTINCT system label (`<label>+repair`) so best-of-k and repair-loop
metrics land in separate report rows, per the plan's S2 (best-of-k) vs S3
(+repair loop) systems-matrix rows. Writes every attempt as a W1 RunRecord,
then calls `eval.report_gen` for the standard §7 metric block plus this
package's own guard-co-emission table (see module docstring in
`drafter.py::split_guard_sidecar` for why that one lives here and not in
W1's fixed metric block).

This script never calls an LLM API. Every system in the matrix is backed
by a `Drafter` (drafter.py) — for a real baseline run that means
`FileDrafter` reading a drafts JSONL the planner's subscription-subagent
drafting workflow produced (see `drafter.py::FileDrafter`'s docstring for
the exact schema); for tests/smoke it means `MockDrafter`.

Systems-config JSON schema (one JSON array, each element one system row):

    [
      {"label": "s0-sonnet-zeroshot", "jsonl": "path/to/drafts.jsonl",
       "k": 1, "few_shot_k": 0, "use_repair": true},
      {"label": "s1-sonnet-fewshot3", "jsonl": "path/to/drafts.jsonl",
       "k": 1, "few_shot_k": 3, "use_repair": false},
      {"label": "s2-sonnet-bestof10", "jsonl": "path/to/drafts.jsonl",
       "k": 10, "few_shot_k": 0, "use_repair": false}
    ]

Each row's `jsonl` is read with `FileDrafter(jsonl, system=label, ...)` —
the JSONL's own "system" field must equal that row's "label" (one file may
hold rows for many systems; FileDrafter filters). `few_shot_k` only
controls what this script retrieves and passes as `exemplars=` to
`drafter.draft()` — it does not by itself make a FileDrafter's replayed
text different; the planner's drafting workflow must have generated that
system's drafts WITH the corresponding few-shot prompt for the number to
mean anything as a systems-matrix axis.

Exact command the planner runs once a drafts JSONL exists:

    python -m experiments.seam_bench.t0.run_t0 \\
        --systems-config systems.json \\
        --out-dir experiments/seam_bench/t0/t0_out \\
        --resamples 10000
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.seam_bench.eval import report_gen, schema, validity  # noqa: E402
from experiments.seam_bench.eval.schema import DatasetRecord, RunRecord  # noqa: E402
from experiments.seam_bench.t0 import repair_loop  # noqa: E402
from experiments.seam_bench.t0.drafter import (Drafter, estimate_usage,  # noqa: E402
                                                split_guard_sidecar)
from experiments.seam_bench.t0.exemplars import (ExemplarCandidate,  # noqa: E402
                                                  ExemplarIndex)
from experiments.seam_bench.t0.gold_pairs import (GoldPair,  # noqa: E402
                                                   extract_gold_pairs,
                                                   to_dataset_records)

ValidateFn = Callable[[str], tuple[bool, str]]
BisimFn = Callable[[str, str], tuple[bool, str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SystemConfig:
    """One row of the T0 systems matrix — a drafter config, per the task
    card: "system = drafter config: model label, k, few-shot on/off"."""
    label: str
    drafter: Drafter
    k: int = 1
    few_shot_k: int = 0
    use_repair: bool = False


def item_as_dataset_record(pair: GoldPair, split: str = "dev") -> DatasetRecord:
    return to_dataset_records([pair], split=split)[0]


def build_exemplar_index(pairs: Sequence[GoldPair]) -> ExemplarIndex:
    """BM25 index over ALL gold-pair intents. T0 has no dedicated
    train-split intent pool yet (that is W3's D4 split build) so few-shot
    retrieval here is leave-one-out over the same 23-item set: `top_k`
    always excludes the querying item's own id. This is a documented T0
    scope choice, not the eventual §3 D4 train-split retrieval."""
    return ExemplarIndex([
        ExemplarCandidate(item_id=p.id, intent=p.intent, protocol=p.protocol)
        for p in pairs])


def run_item(system: SystemConfig, pair: GoldPair, *, split: str,
             exemplar_index: Optional[ExemplarIndex] = None,
             max_repair_rounds: int = repair_loop.MAX_REPAIR_ROUNDS,
             validate_fn: ValidateFn = validity.validate,
             bisim_fn: BisimFn = validity.bisim_equivalent) -> list[RunRecord]:
    """Run one (item, system) cell: best-of-k, then (if configured) the
    repair chain seeded from candidate #1. Returns every RunRecord
    produced (best-of-k rows under `system.label`, repair rows — if any —
    under `system.label + "+repair"`)."""
    exemplars = None
    if system.few_shot_k > 0:
        if exemplar_index is None:
            raise ValueError(
                f"system {system.label!r} has few_shot_k="
                f"{system.few_shot_k} but no exemplar_index was given")
        exemplars = exemplar_index.top_k_pairs(
            pair.intent, system.few_shot_k, exclude_item_ids=[pair.id])

    texts = system.drafter.draft(pair.intent, system.k, exemplars=exemplars)
    if len(texts) != system.k:
        raise ValueError(
            f"system {system.label!r}: draft() returned {len(texts)} "
            f"texts, expected k={system.k}")

    ts = _now()
    records: list[RunRecord] = []
    for i, text in enumerate(texts, start=1):
        protocol_text, _refn = split_guard_sidecar(text)
        valid, msg = validate_fn(protocol_text)
        bisim: Optional[bool] = None
        if valid:
            bisim, _reason = bisim_fn(protocol_text, pair.protocol)
        usage = estimate_usage(system.drafter, pair.intent, text)
        records.append(RunRecord(
            system=system.label, model=usage.model, item_id=pair.id,
            split=split, k=i, draft=text, valid=valid, validator_msg=msg,
            bisim=bisim, repair_rounds=None, tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out, usd=usage.usd, ts=ts))

    if system.use_repair:
        records.extend(repair_loop.run_repair_chain(
            system.drafter, system=f"{system.label}+repair", item_id=pair.id,
            split=split, intent=pair.intent, gold=pair.protocol,
            initial_draft=texts[0], max_rounds=max_repair_rounds,
            validate_fn=validate_fn, bisim_fn=bisim_fn))

    return records


def run_matrix(systems: Sequence[SystemConfig], pairs: Sequence[GoldPair], *,
                split: str = "dev", use_exemplars: bool = True,
                max_repair_rounds: int = repair_loop.MAX_REPAIR_ROUNDS,
                validate_fn: ValidateFn = validity.validate,
                bisim_fn: BisimFn = validity.bisim_equivalent
                ) -> list[RunRecord]:
    """The full systems x items sweep. `validate_fn`/`bisim_fn` are
    injectable so tests can run this offline against a fake verifier."""
    exemplar_index = build_exemplar_index(pairs) if use_exemplars else None
    records: list[RunRecord] = []
    for system in systems:
        for pair in pairs:
            records.extend(run_item(
                system, pair, split=split, exemplar_index=exemplar_index,
                max_repair_rounds=max_repair_rounds, validate_fn=validate_fn,
                bisim_fn=bisim_fn))
    return records


# ── guard co-emission (T0-only derived stat; not part of W1's fixed
#    RunRecord/metrics.py schema — see drafter.py::split_guard_sidecar) ──

def guard_co_emission_rate(records: Sequence[RunRecord],
                            gold_has_refn: dict[str, bool]
                            ) -> tuple[float, int]:
    """Fraction of items whose GOLD protocol has a non-null `.refn` guard
    sidecar (`gold_has_refn[item_id] is True`) for which this system's k=1
    draft ALSO co-emitted a guard sidecar (a `split_guard_sidecar` match).
    n = items with a k=1 record whose gold has refn; items with no k=1
    record, or whose gold has no refn, are excluded from both axes (§7's
    own convention for "n per cell — a data gap is not a rejection")."""
    by_item: dict[str, list[RunRecord]] = {}
    for r in records:
        by_item.setdefault(r.item_id, []).append(r)
    n = hits = 0
    for item_id, recs in by_item.items():
        if not gold_has_refn.get(item_id):
            continue
        first = [r for r in recs if r.k == 1]
        if not first:
            continue
        n += 1
        _protocol, refn = split_guard_sidecar(first[0].draft)
        if refn is not None:
            hits += 1
    return (hits / n if n else 0.0), n


def guard_co_emission_table(records: Sequence[RunRecord],
                             gold_has_refn: dict[str, bool]) -> str:
    by_system_split: dict[tuple[str, str], list[RunRecord]] = {}
    for r in records:
        by_system_split.setdefault((r.system, r.split), []).append(r)
    lines = ["## Guard co-emission (T0-only; not in W1's fixed metric "
             "block — see drafter.py::split_guard_sidecar)", "",
             "Fraction of items whose GOLD protocol has a `.refn` guard "
             "sidecar for which the system's k=1 draft also emitted one "
             "(SEAM_TRAINING_EXECUTION_PLAN.md §2: \"Scribble `.scr` + "
             "`.refn` guard sidecar when the intent implies value "
             "constraints\").", "",
             "| system | split | guard co-emission | n |",
             "|---|---|---:|---:|"]
    for (system, split) in sorted(by_system_split):
        value, n = guard_co_emission_rate(by_system_split[(system, split)],
                                           gold_has_refn)
        cell = f"{value * 100:.1f}%" if n else "n/a"
        lines.append(f"| {system} | {split} | {cell} | {n} |")
    lines.append("")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────

def _load_systems_config(path: Path, *, intent_to_item_id: dict[str, str]
                          ) -> list[SystemConfig]:
    from experiments.seam_bench.t0.drafter import FileDrafter

    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    systems = []
    for row in rows:
        missing = [k for k in ("label", "jsonl") if k not in row]
        if missing:
            raise ValueError(f"{path}: system row missing {missing}: {row}")
        drafter = FileDrafter(row["jsonl"], system=row["label"],
                               intent_to_item_id=intent_to_item_id)
        systems.append(SystemConfig(
            label=row["label"], drafter=drafter, k=int(row.get("k", 1)),
            few_shot_k=int(row.get("few_shot_k", 0)),
            use_repair=bool(row.get("use_repair", False))))
    return systems


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--systems-config", required=True,
                     help="JSON file describing the systems matrix (see "
                          "module docstring for schema)")
    ap.add_argument("--out-dir", default=str(HERE / "t0_out"))
    ap.add_argument("--split", default="dev", choices=schema.SPLIT_VALUES)
    ap.add_argument("--resamples", type=int, default=10_000,
                     help="bootstrap resamples per cell (§7: 10k at a real "
                          "phase gate; the T0 deliverable IS a phase gate)")
    ap.add_argument("--no-exemplars", action="store_true",
                     help="skip BM25 exemplar retrieval even for systems "
                          "with few_shot_k>0 (they will error instead)")
    args = ap.parse_args(argv)

    validity.require_toolchain()  # fail loud, first, not per-item

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = extract_gold_pairs()
    intent_to_item_id = {p.intent: p.id for p in pairs}
    if len(intent_to_item_id) != len(pairs):
        raise ValueError("duplicate intents across gold pairs — "
                          "FileDrafter's intent->item_id map would be lossy")

    dataset_records = [item_as_dataset_record(p, split=args.split) for p in pairs]
    schema.write_jsonl(out_dir / f"dataset.{args.split}.jsonl", dataset_records)

    systems = _load_systems_config(Path(args.systems_config),
                                    intent_to_item_id=intent_to_item_id)
    print(f"loaded {len(systems)} system(s) from {args.systems_config}: "
          f"{[s.label for s in systems]}")

    records = run_matrix(systems, pairs, split=args.split,
                          use_exemplars=not args.no_exemplars)
    run_path = out_dir / f"run.{args.split}.jsonl"
    schema.write_jsonl(run_path, records)
    print(f"wrote {len(records)} RunRecords -> {run_path}")

    report = report_gen.build_report(
        records, n_resamples=args.resamples,
        title="Seam-Bench T0 baselines (prompt-era, no training)")
    gold_has_refn = {p.id: p.refn is not None for p in pairs}
    report += "\n" + guard_co_emission_table(records, gold_has_refn)
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"wrote report -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
