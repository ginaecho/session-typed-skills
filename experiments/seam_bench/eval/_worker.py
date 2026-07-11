"""_worker.py — out-of-process body for validity.py's two verifier calls.

Not a public API; always invoked as `python -m
experiments.seam_bench.eval._worker` with a JSON payload on stdin and a JSON
result on stdout. Running the verifier calls in their own process (rather
than in-process inside validity.py) is what lets validity.py bound them with
a real wall-clock timeout and kill the whole process group — including any
`java` child the Scribble validator or the E5 checker spawns — if something
hangs. See validity.py's module docstring for the guard mechanics.

Two modes, selected by payload["mode"]:
  "validate"  -> runs the real Scribble validator
                 (stjp_core/compiler/validator.py::ScribbleValidator) on
                 payload["text"].
  "bisim"     -> runs the E5 EFSM-bisimulation / conversation-equivalence
                 checker (experiments/scripts/efsm_equiv.py::
                 protocols_equivalent) on payload["text_a"] / payload["text_b"].
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # experiments/seam_bench/eval/_worker.py -> repo root
sys.path.insert(0, str(REPO_ROOT))

from stjp_core.compiler.validator import ScribbleValidator  # noqa: E402
from experiments.scripts.efsm_equiv import protocols_equivalent  # noqa: E402


def _module_stem(text: str) -> str:
    m = re.search(r"module\s+(\w+)\s*;", text)
    return m.group(1) if m else "proto"


def _strip_jvm_banner(msg: str) -> str:
    """Drop the JVM's per-environment stderr noise from a validator error
    message — in some environments every `java` invocation prints a
    "Picked up JAVA_TOOL_OPTIONS: ..." banner (proxy/truststore config)
    ahead of anything else. The verdict is untouched (validator.py's pass
    rule is returncode==0 AND empty stdout); this only keeps the recorded
    `validator_msg` to Scribble's own diagnostics so JSONL artifacts don't
    embed environment-specific paths."""
    lines = [ln for ln in msg.splitlines()
             if not ln.startswith("Picked up JAVA_TOOL_OPTIONS")]
    return "\n".join(lines).strip()


def _cmd_validate(text: str) -> dict:
    stem = _module_stem(text)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{stem}.scr"
        p.write_text(text, encoding="utf-8")
        ok, err = ScribbleValidator().validate_protocol(p)
    return {"ok": bool(ok), "msg": _strip_jvm_banner(err or "")}


def _cmd_bisim(text_a: str, text_b: str) -> dict:
    ok, why = protocols_equivalent(text_a, text_b)
    return {"ok": bool(ok), "msg": why}


def main() -> int:
    payload = json.loads(sys.stdin.read())
    mode = payload.get("mode")
    try:
        if mode == "validate":
            result = _cmd_validate(payload["text"])
        elif mode == "bisim":
            result = _cmd_bisim(payload["text_a"], payload["text_b"])
        else:
            result = {"ok": False, "msg": f"unknown worker mode {mode!r}"}
    except Exception as e:  # noqa: BLE001 - report the failure, don't crash silently
        result = {"ok": False, "msg": f"worker exception: {type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
