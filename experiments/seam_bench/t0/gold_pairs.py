"""gold_pairs.py — the T0 input universe: (intent, gold protocol) pairs
extracted from `experiments/cases/`.

SEAM_TRAINING_EXECUTION_PLAN.md W4 task card: "Input source: a JSONL of
(intent, gold) pairs — default to the 23 gold pairs (the 17 named cases
with case.yaml `intent` + v1.scr, and the 6 skills_safety sub-cases);
provide a small builder that extracts these from experiments/cases/."

Discovery rule (no hardcoded case-name list — driven entirely by case.yaml
presence, so it self-updates if a new named case is added later):
every immediate subdirectory of `experiments/cases/` that itself has a
`case.yaml` is one case (the 17 top-level cases: auction, banking, ...,
travel_saga). Every immediate subdirectory of a directory that does NOT
itself have a `case.yaml` but has children that do is also scanned one
level down (this is what picks up the 6 `skills_safety/<subcase>` cases).
`_corpus/` (seed skeletons only, no case.yaml anywhere under it — no
`intent` field to translate from) and `composition/` (raw incremental-
composition `.scr` fragments, no case.yaml anywhere under it either) are
excluded by construction, not by name.

Verified against experiments/cases/ on this checkout: exactly 23 case
dirs, all 23 have a non-empty `intent` and a resolvable gold protocol file
(see `_protocol_path`) — see the extraction report in
docs/reference/reports/seam/W4_t0_runner.md.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # experiments/seam_bench/t0/gold_pairs.py -> repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CASES_DIR = REPO_ROOT / "experiments" / "cases"


@dataclass(frozen=True)
class GoldPair:
    """One T0 evaluation item: a hand-authored (intent, gold Scribble
    protocol) pair sourced from a named case directory."""
    id: str                    # case.yaml's case_id (unique across all 23)
    family: str                # case dir path relative to CASES_DIR,
                                # e.g. "auction" or "skills_safety/pr_merge"
    intent: str
    protocol: str               # gold Scribble source (protocols/v1.scr, or
                                 # protocols/<protocol_name>.scr as fallback)
    refn: Optional[str]         # protocols/v1.refn text, if present
    seed_case: str               # == family (kept as its own field to match
                                  # DatasetRecord's field name in schema.py)


def find_case_dirs(cases_root: Path = CASES_DIR) -> list[Path]:
    """Every case directory under `cases_root`, by case.yaml presence (see
    module docstring for the exact two-level rule)."""
    out: list[Path] = []
    for child in sorted(p for p in cases_root.iterdir() if p.is_dir()):
        if (child / "case.yaml").exists():
            out.append(child)
        else:
            for grandchild in sorted(p for p in child.iterdir() if p.is_dir()):
                if (grandchild / "case.yaml").exists():
                    out.append(grandchild)
    return out


def _protocol_path(case_dir: Path, protocol_name: Optional[str]) -> Optional[Path]:
    """The gold protocol file: `protocols/v1.scr` if present (the common
    case, 21/23 dirs), else `protocols/<protocol_name>.scr` (the fallback
    two skills_safety sub-cases — pr_merge, doc_pipeline — use, since their
    protocol files are named after the protocol, not versioned)."""
    v1 = case_dir / "protocols" / "v1.scr"
    if v1.exists():
        return v1
    if protocol_name:
        alt = case_dir / "protocols" / f"{protocol_name}.scr"
        if alt.exists():
            return alt
    return None


def _load_case(case_dir: Path) -> GoldPair:
    case_yaml = case_dir / "case.yaml"
    data = yaml.safe_load(case_yaml.read_text(encoding="utf-8")) or {}
    intent = (data.get("intent") or "").strip()
    if not intent:
        raise ValueError(f"{case_yaml}: no non-empty 'intent' field")
    proto_path = _protocol_path(case_dir, data.get("protocol_name"))
    if proto_path is None:
        raise ValueError(
            f"{case_dir}: no protocols/v1.scr and no "
            f"protocols/<protocol_name>.scr found (protocol_name="
            f"{data.get('protocol_name')!r})")
    refn_path = case_dir / "protocols" / "v1.refn"
    refn = refn_path.read_text(encoding="utf-8") if refn_path.exists() else None
    case_id = str(data.get("case_id") or case_dir.name)
    family = case_dir.relative_to(CASES_DIR).as_posix()
    return GoldPair(
        id=case_id, family=family, intent=intent,
        protocol=proto_path.read_text(encoding="utf-8"), refn=refn,
        seed_case=family)


def extract_gold_pairs(cases_root: Path = CASES_DIR) -> list[GoldPair]:
    """Build the T0 gold-pair set. Fails loud (raises ValueError) on any
    discovered case dir missing an intent or a resolvable gold protocol —
    a silently-skipped case would quietly shrink the 23-pair set without
    anyone noticing."""
    pairs = [_load_case(d) for d in find_case_dirs(cases_root)]
    ids = [p.id for p in pairs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case_id(s) across cases/: {sorted(dupes)}")
    return pairs


def to_dataset_records(pairs: list[GoldPair], split: str = "dev"):
    """GoldPairs -> W1's `DatasetRecord`s (eval/schema.py), for anyone who
    wants the 23 pairs in the eval harness's own JSONL shape. `source` is
    "synthetic" (schema.py's vocabulary has no "hand-authored" category;
    these are neither D1-D3 synthetic generation nor D5 mined — flagged in
    `gen.kind` below so the distinction is not lost) and `split` defaults to
    "dev" since these 23 pairs predate the D4 train/dev/test-syn/test-real
    split build (W3) and are explicitly a pre-training measurement set, not
    a phase-gated test split — see W4_t0_runner.md for the full rationale.
    """
    from experiments.seam_bench.eval.schema import DatasetRecord
    return [
        DatasetRecord(
            id=p.id, family=p.family, split=split, intent=p.intent,
            protocol=p.protocol, refn=p.refn, source="synthetic",
            seed_case=p.seed_case,
            gen={"kind": "hand_authored_case", "case_dir": p.family})
        for p in pairs]


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: dump the 23 gold pairs as DatasetRecord JSONL.

    Usage: python -m experiments.seam_bench.t0.gold_pairs OUT.jsonl [--split dev]
    """
    import argparse

    from experiments.seam_bench.eval import schema

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("out", help="output JSONL path")
    ap.add_argument("--split", default="dev", choices=schema.SPLIT_VALUES)
    args = ap.parse_args(argv)

    pairs = extract_gold_pairs()
    records = to_dataset_records(pairs, split=args.split)
    n = schema.write_jsonl(args.out, records)
    print(f"wrote {n} gold-pair DatasetRecords -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
