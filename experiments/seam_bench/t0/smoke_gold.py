"""smoke_gold.py — the W4 gold-pair done-criterion.

Task card item 6: "run the extractor and validate all 23 golds under real
Scribble (they must pass — if any fails, investigate/report). Do NOT
attempt real drafting (no API)." This script does exactly that and nothing
more: no Drafter, no repair loop, no LLM call anywhere.

Usage:
    python -m experiments.seam_bench.t0.smoke_gold [--out DIR]

Writes, under `--out` (default `experiments/seam_bench/t0/smoke_out/`):
    gold_pairs.dev.jsonl   DatasetRecord per extracted gold pair
    gold_verdicts.json     {id: {valid, validator_msg}} for every pair

Exit code 0 iff every one of the 23 gold protocols validates under the
real Scribble-java CLI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.seam_bench.eval import schema, validity  # noqa: E402
from experiments.seam_bench.t0.gold_pairs import (extract_gold_pairs,  # noqa: E402
                                                   to_dataset_records)

DEFAULT_OUT_DIR = HERE / "smoke_out"


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--split", default="dev", choices=schema.SPLIT_VALUES)
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    validity.require_toolchain()

    pairs = extract_gold_pairs()
    print(f"extracted {len(pairs)} gold pairs from experiments/cases/")
    if len(pairs) != 23:
        print(f"WARNING: expected 23 gold pairs, got {len(pairs)} — "
              f"experiments/cases/ has changed since the W4 report was "
              f"written; investigate before trusting downstream T0 numbers")

    records = to_dataset_records(pairs, split=args.split)
    schema.write_jsonl(out_dir / f"gold_pairs.{args.split}.jsonl", records)

    verdicts = validity.validate_many([p.protocol for p in pairs])
    by_id = {}
    n_pass = 0
    for pair, (ok, msg) in zip(pairs, verdicts):
        by_id[pair.id] = {"valid": ok, "validator_msg": msg}
        status = "PASS" if ok else "FAIL"
        if ok:
            n_pass += 1
        print(f"  {status}  {pair.id:24s} ({pair.family})"
              + ("" if ok else f"  -- {msg[:200]}"))

    (out_dir / "gold_verdicts.json").write_text(
        json.dumps(by_id, indent=2, sort_keys=True), encoding="utf-8")

    print()
    print(f"gold protocols validated: {n_pass}/{len(pairs)}")
    if n_pass != len(pairs):
        print("SMOKE FAILURE — at least one gold protocol failed real "
              "Scribble validation; see gold_verdicts.json")
        return 1
    print("SMOKE OK — all extracted gold protocols validate under the "
          "real Scribble-java CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
