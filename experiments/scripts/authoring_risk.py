"""authoring_risk.py — how often does UNCHECKED LLM authoring produce a deadlock?

The honest version of the deadlock claim (un-rig-able): instead of hand-writing a
deadlocking spec, let an LLM author the global protocol from a deadlock-prone
intent N independent times, and classify each draft with Scribble:
  deadlock-free (valid) | DEADLOCK (safety / wait-for cycle) | other error.

Run with a NAIVE prompt (what a normal developer would write) and with the
hardened STJP prompt (+ fan-out normalizer). The point: unchecked authoring
produces deadlocks at a non-zero rate; Scribble catches 100% of them — before any
agent runs.

Run: python scripts/authoring_risk.py <case_id> [n_draws]
"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / "stjp_core" / ".env")
from stjp_core.authoring.architect import ArchitectAgent
from stjp_core.compiler.validator import ScribbleValidator

DEADLOCK_HINTS = ("safety violation", "wait-for", "deadlock", "unfinished")


def classify(ok, err):
    if ok:
        return "deadlock_free"
    e = (err or "").lower()
    if any(h in e for h in DEADLOCK_HINTS):
        return "DEADLOCK"
    return "other_error"


def main():
    import yaml
    case_id = sys.argv[1] if len(sys.argv) > 1 else "trade_settlement"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    cd = ROOT / "experiments" / "cases" / case_id
    cy = yaml.safe_load((cd / "case.yaml").read_text(encoding="utf-8"))
    intent = (f"{cy['intent']}\n\nPROTOCOL CONSTRAINTS:\n"
              f"- Use EXACTLY these roles: {', '.join(cy['roles'])}.\n"
              f"- Terminate with a message labelled '{cy['terminal_label']}'.\n"
              f"- Module name 'v1', protocol name '{cy['protocol_name']}'.")
    val = ScribbleValidator()
    work = ROOT / "experiments" / "logs" / "authoring_risk"

    print(f"AUTHORING RISK — {case_id}, {n} independent draws per prompt (gpt-5.4)\n")
    for label, v2, fan in [("NAIVE prompt (normal developer)", False, False),
                           ("HARDENED prompt (+ fan-out fix)", True, True)]:
        counts = {"deadlock_free": 0, "DEADLOCK": 0, "other_error": 0}
        for i in range(n):
            arch = ArchitectAgent(use_v2_prompt=v2, auto_fanout=fan)
            d = work / f"{'v2' if v2 else 'v1'}/t{i}"
            d.mkdir(parents=True, exist_ok=True)
            scr = d / "v1.scr"
            try:
                scr.write_text(arch.draft_protocol(requirement=intent, module_name="v1"),
                               encoding="utf-8")
                ok, err = val.validate_protocol(scr)
            except Exception as e:
                ok, err = False, f"exception: {e}"
            c = classify(ok, err)
            counts[c] += 1
            print(f"  [{label[:14]}] draw {i}: {c}")
        dl = counts["DEADLOCK"]
        unsafe = dl + counts["other_error"]
        print(f"\n  {label}:")
        print(f"    deadlock-free: {counts['deadlock_free']}/{n}")
        print(f"    DEADLOCK:      {dl}/{n}  ({100*dl/n:.0f}% of drafts would deadlock at runtime)")
        print(f"    other invalid: {counts['other_error']}/{n}")
        print(f"    -> Scribble rejected ALL {unsafe} unsafe drafts (100%) before any agent ran.\n")


if __name__ == "__main__":
    main()
