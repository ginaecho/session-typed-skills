"""draft_llm_protocols.py — LLM-draft + Scribble-validate harness.

Drives architect.py multiple times on a case's intent to produce two
artefacts for the LLM-+-validator experiment:

  - v1_llm_valid.scr  : the first draft that PASSES Scribble validation
  - v1_llm_unsafe.scr : the first draft that FAILS Scribble validation
                        (preference: errors with deadlock / choice / role-
                        participation suggestions, since those are the
                        unsafe modes we want agents to behaviourally fail on)

Both come from the SAME intent so the only variable between arm-2 and
arm-3 in the downstream experiment is whether Scribble approved the
LLM's draft.

Output:
  experiments/cases/<case>/protocols/llm_drafts/v1_llm_valid.scr
  experiments/cases/<case>/protocols/llm_drafts/v1_llm_unsafe.scr
  experiments/cases/<case>/protocols/llm_drafts/drafts_log.json

Usage:
  python scripts/draft_llm_protocols.py <case_id> [max_attempts]
  python scripts/draft_llm_protocols.py finance 10
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


HERE = Path(__file__).resolve().parent
EXPERIMENTS_DIR = HERE.parent
TESTING_IDEAS = EXPERIMENTS_DIR.parent
STJP_CORE = TESTING_IDEAS / "stjp_core"
CASES_DIR = EXPERIMENTS_DIR / "cases"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TESTING_IDEAS))
load_dotenv(STJP_CORE / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from stjp_core.authoring.architect import ArchitectAgent
from stjp_core.compiler.validator import ScribbleValidator
from case_loader import Case


# Heuristic keywords in Scribble errors that suggest the failure is a
# safety/deadlock issue (vs e.g. a parser error). Prefer drafts whose
# errors hit these — they're the "interesting" unsafe protocols.
DEADLOCK_HINT_KEYWORDS = (
    "deadlock", "race", "choice", "branch", "role", "subject",
    "participant", "projection", "wf",  # well-formedness
)


def _score_unsafe_error(error: str) -> int:
    """Higher = more interesting (more likely a deadlock/safety issue)."""
    if not error:
        return 0
    err_lower = error.lower()
    return sum(1 for kw in DEADLOCK_HINT_KEYWORDS if kw in err_lower)


def draft_protocols(case: Case, max_attempts: int = 10) -> dict:
    """Run architect N times, collect valid + unsafe drafts."""
    drafts_dir = case.case_dir / "protocols" / "llm_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "valid").mkdir(exist_ok=True)
    (drafts_dir / "unsafe").mkdir(exist_ok=True)

    # Scribble requires module-name == filename stem, so each kept draft sits
    # in its own subdir named v1.scr (matching the inline `module v1;`).
    valid_path = drafts_dir / "valid" / "v1.scr"
    unsafe_path = drafts_dir / "unsafe" / "v1.scr"
    log_path = drafts_dir / "drafts_log.json"

    # Constrain the LLM to the case's actual role names + terminal label, so
    # arm-2 / arm-3 agents can actually follow the drafted protocol (their
    # identities are bound to these names). This is a fairness fix: without
    # it, the LLM invents role names like "DataProcessor" that no agent answers to.
    constrained_intent = (
        f"{case.intent}\n\n"
        f"PROTOCOL CONSTRAINTS (must be obeyed):\n"
        f"- Use EXACTLY these role names (no others, no synonyms, case-sensitive): "
        f"{', '.join(case.roles)}.\n"
        f"- The protocol must terminate with a message labelled "
        f"'{case.terminal_label}'.\n"
        f"- Use the module name 'v1' and the global protocol name "
        f"'{case.protocol_name}'."
    )

    print(f"  intent (constrained, sent to LLM):")
    for line in constrained_intent.splitlines()[:12]:
        print(f"    {line}")
    print(f"  max attempts: {max_attempts}")
    print(f"  output dir: {drafts_dir.relative_to(TESTING_IDEAS)}")
    print()

    validator = ScribbleValidator()
    architect = ArchitectAgent()

    attempts: list[dict] = []
    best_valid: tuple[str, dict] | None = None  # (text, info)
    best_unsafe: tuple[str, dict] | None = None  # (text, info)

    for attempt in range(1, max_attempts + 1):
        print(f"--- attempt {attempt}/{max_attempts} ---")
        t0 = time.time()

        # Reset the architect each call so attempts are independent draws,
        # not iteratively-refined ones (we want raw first-draft behaviour).
        architect.reset()

        # Scribble requires module-name == filename stem. Give each attempt
        # its own subdir named v1.scr so we can validate without renames.
        tmp_dir = drafts_dir / f"_attempt_{attempt:02d}"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / "v1.scr"
        try:
            draft = architect.draft_protocol(
                requirement=constrained_intent,
                module_name="v1",
            )
            tmp_path.write_text(draft, encoding="utf-8")
            elapsed = time.time() - t0

            is_valid, error = validator.validate_protocol(tmp_path)
        except Exception as e:
            elapsed = time.time() - t0
            attempts.append({"attempt": attempt, "outcome": "exception",
                             "error": f"{type(e).__name__}: {e}",
                             "elapsed_s": round(elapsed, 1)})
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
            tmp_path.unlink(missing_ok=True)
            continue

        info = {
            "attempt": attempt,
            "valid": is_valid,
            "error": (error or "")[:600],
            "deadlock_score": _score_unsafe_error(error) if not is_valid else 0,
            "draft_chars": len(draft),
            "elapsed_s": round(elapsed, 1),
        }
        attempts.append(info)

        if is_valid:
            print(f"  PASS ({elapsed:.1f}s, {len(draft)} chars)")
            if best_valid is None:
                best_valid = (draft, info)
                print(f"    -> kept as v1_llm_valid.scr")
        else:
            print(f"  FAIL (score={info['deadlock_score']}, {elapsed:.1f}s)")
            print(f"    error: {(error or '')[:200]}")
            if best_unsafe is None or info["deadlock_score"] > best_unsafe[1]["deadlock_score"]:
                best_unsafe = (draft, info)
                print(f"    -> kept as v1_llm_unsafe.scr (best score so far)")

        # Leave the per-attempt subdir on disk so the validation reasoning
        # is auditable after the fact; small files, low cost.

        if best_valid is not None and best_unsafe is not None \
                and best_unsafe[1]["deadlock_score"] >= 2:
            print(f"  Got both (unsafe score >= 2); stopping early.")
            break

    # ------------------------------------------------------------------
    # Iterative fix mode: if fresh drafting never produced a valid one,
    # use Scribble's error messages as feedback (the architect's "fix mode").
    # This IS the "LLM + validator" co-design loop the experiment is testing.
    # ------------------------------------------------------------------
    if best_valid is None and best_unsafe is not None:
        print()
        print("=" * 72)
        print("  FIX MODE: feeding Scribble errors back to architect for refinement")
        print("=" * 72)
        fix_attempts = max(3, max_attempts // 2)
        current_proto = best_unsafe[0]
        current_err = best_unsafe[1]["error"]
        for fix_i in range(1, fix_attempts + 1):
            print(f"--- fix iter {fix_i}/{fix_attempts} ---")
            t0 = time.time()
            architect.reset()
            try:
                fixed = architect.draft_protocol(
                    requirement=constrained_intent,
                    module_name="v1",
                    previous_protocol=current_proto,
                    previous_error=current_err,
                )
                fix_dir = drafts_dir / f"_fix_{fix_i:02d}"
                fix_dir.mkdir(exist_ok=True)
                fix_path = fix_dir / "v1.scr"
                fix_path.write_text(fixed, encoding="utf-8")
                is_valid, error = validator.validate_protocol(fix_path)
                elapsed = time.time() - t0
            except Exception as e:
                print(f"  EXCEPTION: {type(e).__name__}: {e}")
                attempts.append({"attempt": f"fix_{fix_i}", "outcome": "exception",
                                 "error": f"{type(e).__name__}: {e}",
                                 "elapsed_s": round(time.time() - t0, 1)})
                continue
            info = {"attempt": f"fix_{fix_i}", "valid": is_valid,
                    "error": (error or "")[:600],
                    "draft_chars": len(fixed),
                    "elapsed_s": round(elapsed, 1)}
            attempts.append(info)
            if is_valid:
                print(f"  PASS after {fix_i} fix iter(s) ({elapsed:.1f}s)")
                best_valid = (fixed, info)
                break
            else:
                print(f"  STILL FAILS ({elapsed:.1f}s): {(error or '')[:160]}")
                current_proto = fixed
                current_err = error

    # Write artefacts
    if best_valid is not None:
        valid_path.write_text(best_valid[0], encoding="utf-8")
        print(f"\nWROTE {valid_path.relative_to(TESTING_IDEAS)}")
    else:
        print(f"\n(no valid draft produced in {max_attempts} fresh + fix attempts)")

    if best_unsafe is not None:
        unsafe_path.write_text(best_unsafe[0], encoding="utf-8")
        print(f"WROTE {unsafe_path.relative_to(TESTING_IDEAS)}")
    else:
        print(f"(no unsafe draft produced -- all drafts passed Scribble!)")

    log = {
        "case_id": case.case_id,
        "intent": case.intent,
        "module_name": "v1",
        "max_attempts": max_attempts,
        "attempts_used": len(attempts),
        "attempts": attempts,
        "kept_valid": {"path": str(valid_path.relative_to(TESTING_IDEAS)),
                       "from_attempt": best_valid[1]["attempt"]} if best_valid else None,
        "kept_unsafe": {"path": str(unsafe_path.relative_to(TESTING_IDEAS)),
                        "from_attempt": best_unsafe[1]["attempt"],
                        "deadlock_score": best_unsafe[1]["deadlock_score"]}
                        if best_unsafe else None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"WROTE {log_path.relative_to(TESTING_IDEAS)}")
    return log


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: draft_llm_protocols.py <case_id> [max_attempts]")
        sys.exit(2)
    case_id = args[0]
    max_attempts = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10

    case = Case.load(CASES_DIR / case_id)
    print("=" * 72)
    print(f"  DRAFT LLM PROTOCOLS — case={case.case_id}")
    print("=" * 72)
    draft_protocols(case, max_attempts=max_attempts)


if __name__ == "__main__":
    main()
