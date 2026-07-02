"""smoke_delm_runtime.py — offline proof of the v3 DeLM-style STJP runtime.

Demonstrates (no LLM, deterministic oracle) that the decentralized substrate with
STJP as the safety layer:
  1. SCHEDULES by the EFSM enabled-set (polls only enabled senders) — big call cut;
  2. VERIFIES every write with the monitor (admits conforming, blocks wrong);
  3. is deadlock-free and terminates on both branches;
  4. enforce vs observe behaves like the foundry gate vs observer arms.

Run: python scripts/smoke_delm_runtime.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from stjp_core.runtime.delm_runner import STJPRuntime, ContractOracle, BadOracle

SCR = ROOT / "experiments/cases/finance/protocols/llm_drafts/valid/v1.scr"
ROLES = ["Fetcher", "RevenueAnalyst", "ExpenseAnalyst", "Writer", "TaxVerifier", "TaxSpecialist"]
PN = "QuarterlyFinanceReport"
def payloads(branch):
    rev = "75000" if branch == "high" else "42000"
    return {"RawRevenueData": rev, "ExpenseData": "12000",
            "HighRevenueNotification": "high", "StandardRevenueNotification": "standard",
            "FinalRevenueAnalysis": f"analysis of {rev} revenue and 12000 expense"}
PL = payloads("high")


def line(label, res):
    eff = 100 * (1 - res.polls / res.roundrobin_polls) if res.roundrobin_polls else 0
    print(f"  {label:34s} term={str(res.terminated):5s} reason={res.reason:18s} "
          f"events={len(res.events):2d} viol={res.violations} rejected={res.rejected} "
          f"| polls {res.polls} vs RR {res.roundrobin_polls} (-{eff:.0f}%)")


def main():
    print("DeLM-style STJP runtime — offline mechanics smoke\n")
    print("1) Contract-following oracle (the substrate works + scheduling cost):")
    for br in ("high", "standard"):
        rt = STJPRuntime(SCR, PN, ROLES, "GenerateReport", enforce=True)
        res = rt.run(ContractOracle(rt, branch=br, payloads=payloads(br)))
        line(f"good oracle, {br} branch", res)

    print("\n2) Wrong-branch oracle (standard on high data) — the verifier's job:")
    rt = STJPRuntime(SCR, PN, ROLES, "GenerateReport", enforce=True)
    res = rt.run(BadOracle(rt, branch="high", payloads=PL))
    line("ENFORCE: blocked + recovered", res)
    rt = STJPRuntime(SCR, PN, ROLES, "GenerateReport", enforce=False)
    res = rt.run(BadOracle(rt, branch="high", payloads=PL))
    line("OBSERVE: delivered + flagged", res)

    print("\nTakeaways:")
    print("  - EFSM enabled-set scheduling polls only roles that can act now")
    print("    (idle/WAIT roles never polled) -> ~80%+ fewer agent calls vs round-robin.")
    print("  - The monitor admits conforming writes and blocks the value-wrong branch")
    print("    BEFORE it lands (enforce) or records it (observe) — same as the gate arm.")
    print("  - Order-jumping is structurally impossible: a role with no enabled send")
    print("    is never offered a turn, so only value-wrong writes reach the verifier.")
    print("  - Next (online): swap ContractOracle for the Foundry LLM agent.")


if __name__ == "__main__":
    main()
