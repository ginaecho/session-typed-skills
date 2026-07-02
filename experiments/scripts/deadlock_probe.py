"""deadlock_probe.py — does an UNCHECKED circular spec actually deadlock LLM agents,
and is it costly? The critical assumption behind the STJP deadlock thesis.

Two 2-agent rounds on the classic pay-vs-ship circular dependency, driven by a
round-robin loop (same shape as the benchmark runner). Counts tokens + whether
anyone ever acts.

  UNCHECKED: Buyer waits for goods before paying; Seller waits for payment before
             shipping. Each strictly waits for the other -> deadlock.
  ESCROW   : an Escrow holds funds first, breaking the cycle -> completes.

Run: python scripts/deadlock_probe.py
"""
import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / "stjp_core" / ".env")
from stjp_core.foundry.foundry_client import FoundryLLMClient

OUT_RULES = (
    '\nReply with ONE JSON object only: '
    '{"send_to":"<Role|null>","label":"<Label>","payload":"<v>","rationale":"<1 line>"}. '
    'If your rule says you must wait, reply send_to=null, label="WAIT".')


def parse(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"label": "WAIT"}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"label": "WAIT"}


def run(name, roles, skills, terminal, max_steps=14):
    client = FoundryLLMClient()
    history = []
    waits = 0
    tokens = 0
    queue = list(roles)
    print(f"\n=== {name} ===")
    for step in range(max_steps):
        actor = queue[step % len(roles)]
        view = "Messages so far:\n" + (
            "\n".join(f"  {h['from']}->{h['to']}: {h['label']}({h.get('payload','')})"
                      for h in history if h['to'] == actor or h['from'] == actor)
            or "  (none)")
        sysmsg = skills[actor] + OUT_RULES
        try:
            resp, usage = client.generate(sysmsg, view + "\n\nYour next action?",
                                          return_usage=True)
        except TypeError:
            resp = client.generate(sysmsg, view + "\n\nYour next action?")
            usage = None
        tokens += (usage.get("total_tokens", 0) if isinstance(usage, dict) else 0)
        act = parse(resp)
        lbl = act.get("label", "WAIT")
        to = act.get("send_to")
        if not to or lbl == "WAIT":
            waits += 1
            print(f"  step {step+1}: {actor} WAITs ({act.get('rationale','')[:50]})")
            if waits >= 2 * len(roles):
                print(f"  --> DEADLOCK: {waits} consecutive waits, no progress. STOP.")
                return {"completed": False, "tokens": tokens, "waits": waits, "steps": step+1}
            continue
        waits = 0
        history.append({"from": actor, "to": to, "label": lbl, "payload": act.get("payload", "")})
        print(f"  step {step+1}: {actor} -> {to}: {lbl}")
        if lbl == terminal:
            print(f"  --> COMPLETED at step {step+1}")
            return {"completed": True, "tokens": tokens, "waits": 0, "steps": step+1}
    return {"completed": False, "tokens": tokens, "waits": waits, "steps": max_steps}


UNCHECKED = {
  "Buyer": ("You are the Buyer in a trade with the Seller. STRICT RULE: you must "
            "NOT send Payment until you have RECEIVED a message labelled 'GoodsDelivered'. "
            "Until then you must WAIT."),
  "Seller": ("You are the Seller in a trade with the Buyer. STRICT RULE: you must "
             "NOT send GoodsDelivered until you have RECEIVED a message labelled 'Payment'. "
             "Until then you must WAIT."),
}
ESCROW = {
  "Buyer": ("You are the Buyer. First send 'FundEscrow' to Escrow (payload: amount). "
            "After you receive 'GoodsDelivered' from Seller, send 'ConfirmReceipt' to Escrow."),
  "Escrow": ("You are the Escrow. When you receive 'FundEscrow' from Buyer, send "
             "'FundsSecured' to Seller. When you receive 'ConfirmReceipt' from Buyer, "
             "send 'ReleasePayment' to Seller, then send 'SettlementComplete' to Buyer."),
  "Seller": ("You are the Seller. After you receive 'FundsSecured' from Escrow, send "
             "'GoodsDelivered' to Buyer. Then wait for payment."),
}


def main():
    r1 = run("UNCHECKED circular skills (no validator)", ["Buyer", "Seller"],
             UNCHECKED, terminal="SettlementComplete")
    r2 = run("ESCROW-validated skills (cycle broken)", ["Buyer", "Escrow", "Seller"],
             ESCROW, terminal="SettlementComplete")
    print("\n================ RESULT ================")
    print(f"  UNCHECKED: completed={r1['completed']}  tokens={r1['tokens']}  "
          f"waits={r1['waits']}  (deadlock = no completion, tokens burned)")
    print(f"  ESCROW   : completed={r2['completed']}  tokens={r2['tokens']}  steps={r2['steps']}")


if __name__ == "__main__":
    main()
