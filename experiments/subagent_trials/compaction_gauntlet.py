"""Compaction gauntlet — score one subagent-compaction run (E1).

The orchestrating session asks an independent subagent to compact the four
prose trade skills (the trade_deadlock circular-wait set) into local-type
JSONs; this script judges the result with the DETERMINISTIC pipeline:

    python compaction_gauntlet.py check --file run<i>/localtypes.json \
        --out run<i>/verdict.json [--expect unsafe|safe]

For the RAW skills the correct verdict is UNSAFE (compatibility errors or a
synthesis deadlock diagnosis) — the skills genuinely deadlock. For the
REVISED skills (a second subagent repairs them given the diagnosis) the
correct verdict is SAFE: deterministic synthesis succeeds AND Scribble
accepts the composed global type.

Verdict JSON: {"verdict": "safe"|"unsafe", "stage": "...", "detail": "...",
               "expected": "...", "as_expected": bool}
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from stjp_core.compiler.local_type import LocalType, LocalTypeError    # noqa: E402
from stjp_core.compiler.global_synthesizer import (                     # noqa: E402
    check_compatibility, synthesize_global, SynthesisError)
from stjp_core.compiler.validator import ScribbleValidator              # noqa: E402


def judge(localtypes_path: Path) -> dict:
    raw = json.loads(localtypes_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "local_types" in raw:
        raw = raw["local_types"]
    if not isinstance(raw, list):
        return {"verdict": "unsafe", "stage": "parse",
                "detail": "reply was not a JSON array of local types"}
    try:
        lts = {}
        for item in raw:
            lt = LocalType.from_dict(item)
            lts[lt.role] = lt
    except (LocalTypeError, TypeError, KeyError) as e:
        return {"verdict": "unsafe", "stage": "parse", "detail": str(e)[:300]}

    findings = check_compatibility(lts)
    hard = [f for f in findings if f.severity == "ERROR"]
    if hard:
        return {"verdict": "unsafe", "stage": "compatibility",
                "detail": "; ".join(f.message for f in hard)[:600],
                "roles": sorted(lts)}
    try:
        result = synthesize_global(lts, protocol_name="Trade",
                                   module_name="gauntlet")
    except SynthesisError as e:
        return {"verdict": "unsafe", "stage": "synthesis",
                "detail": str(e)[:600], "roles": sorted(lts)}

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "gauntlet.scr"
        p.write_text(result.protocol_text, encoding="utf-8")
        ok, err = ScribbleValidator().validate_protocol(p)
    if not ok:
        return {"verdict": "unsafe", "stage": "scribble",
                "detail": err[:600], "roles": sorted(lts)}
    return {"verdict": "safe", "stage": "scribble",
            "detail": "deterministic synthesis + Scribble VALID",
            "roles": sorted(lts), "protocol": result.protocol_text}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--expect", choices=["safe", "unsafe"], default=None)
    args = ap.parse_args()

    v = judge(Path(args.file))
    if args.expect:
        v["expected"] = args.expect
        v["as_expected"] = (v["verdict"] == args.expect)
    Path(args.out).write_text(json.dumps(v, indent=2), encoding="utf-8")
    print(json.dumps({k: val for k, val in v.items() if k != "protocol"},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
