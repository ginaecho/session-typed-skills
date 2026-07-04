"""Offline tests for the bottom-up pipeline:
existing skills → compaction → local types → global synthesis → Scribble.

The LLM is exercised only via an injected fake (canned JSON); everything else
is deterministic. Scribble (vendored) is the final judge, as in the other
offline tests.
"""
import json
import sys
import tempfile
from pathlib import Path

# --- bootstrap: make 'stjp_core' importable when run directly ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# --- end bootstrap ---

from stjp_core.compiler.local_type import LocalType, LAction, LChoice
from stjp_core.compiler.global_synthesizer import (
    check_compatibility, synthesize_global, SynthesisError)
from stjp_core.generation.skill_compactor import (
    compact_skill_file, compact_and_synthesize, _parse_localtype_block)
from stjp_core.compiler.validator import ScribbleValidator


# ── fixtures ────────────────────────────────────────────────────────────────

BUYER = """# Buyer
You are the Buyer in an escrow-protected trade.

```localtype
Escrow!Deposit(Double);
Carrier?DeliverGoods(String);
Escrow!ConfirmReceipt(String);
Escrow?SettlementComplete(String);
```
"""

SELLER = """# Seller
```localtype
Escrow?PaymentSecured(String);
Carrier!ShipGoods(String);
Escrow?SettlementComplete(String);
```
"""

ESCROW = """# Escrow
```localtype
Buyer?Deposit(Double);
Seller!PaymentSecured(String);
Buyer?ConfirmReceipt(String);
Buyer!SettlementComplete(String);
Seller!SettlementComplete(String);
```
"""

CARRIER = """# Carrier
```localtype
Seller?ShipGoods(String);
Buyer!DeliverGoods(String);
```
"""


def _write_skills(d: Path, files: dict[str, str]) -> Path:
    sk = d / "skills"
    sk.mkdir(exist_ok=True)
    for f in sk.glob("*.md"):
        f.unlink()
    for name, text in files.items():
        (sk / f"{name}.md").write_text(text, encoding="utf-8")
    return sk


# ── tests ───────────────────────────────────────────────────────────────────

def test_localtype_block_parsing_with_choice():
    body = _parse_localtype_block(
        "Server!Request(String);\n"
        "choice {\n"
        "    Server?Accept(String);\n"
        "} or {\n"
        "    Server?Reject(String);\n"
        "}\n")
    assert isinstance(body[0], LAction) and body[0].direction == "send"
    assert isinstance(body[1], LChoice) and len(body[1].branches) == 2
    print("[localtype] fenced block with choice parses")


def test_compatibility_catches_mismatches():
    lts = {
        "A": LocalType(role="A", body=[LAction("send", "B", "Ping", "String"),
                                       LAction("recv", "B", "Pong", "Double")]),
        "B": LocalType(role="B", body=[LAction("recv", "A", "Ping", "String"),
                                       LAction("send", "A", "Pong", "String")]),
        "C": LocalType(role="C", body=[LAction("recv", "A", "Never", "")]),
    }
    findings = check_compatibility(lts)
    msgs = "\n".join(f.message for f in findings)
    assert "payload type conflict" in msgs          # Pong: Double vs String
    assert "never sends" in msgs                    # C waits forever
    print(f"[compat] {len(findings)} mismatch(es) caught")


def test_deterministic_synthesis_sequential():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        sk = _write_skills(d, {"Buyer": BUYER, "Seller": SELLER,
                               "Escrow": ESCROW, "Carrier": CARRIER})
        result = compact_and_synthesize(sk, d / "Trade.scr",
                                        protocol_name="Trade")
        assert result.synthesis_mode == "deterministic"
        assert result.valid, result.error
        text = result.protocol_text
        # escrow-first ordering must be reconstructed
        assert text.index("Deposit") < text.index("PaymentSecured") \
            < text.index("ShipGoods") < text.index("DeliverGoods")
        # local types were persisted as checkable artifacts
        assert (d / "local_types" / "Buyer.localtype.json").exists()
        print("[synthesis] 4-role escrow trade reconstructed + Scribble VALID")


def test_synthesis_with_internal_choice_and_suffix_factoring():
    client = LocalType(role="Client", body=[
        LAction("send", "Server", "Request", "String"),
        LChoice(branches=[
            [LAction("recv", "Server", "Accept", "String"),
             LAction("send", "Server", "Ack", "String")],
            [LAction("recv", "Server", "Reject", "String")],
        ]),
        LAction("recv", "Server", "Done", "String"),
    ])
    server = LocalType(role="Server", body=[
        LAction("recv", "Client", "Request", "String"),
        LChoice(branches=[
            [LAction("send", "Client", "Accept", "String"),
             LAction("recv", "Client", "Ack", "String")],
            [LAction("send", "Client", "Reject", "String")],
        ]),
        LAction("send", "Client", "Done", "String"),
    ])
    result = synthesize_global({"Client": client, "Server": server},
                               protocol_name="ReqResp", module_name="ReqResp")
    text = result.protocol_text
    assert "choice at Server" in text
    # the common Done suffix must be factored OUT of the choice block
    assert text.count("Done(String) from Server to Client;") == 1
    assert text.index("}") < text.index("Done(String)")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ReqResp.scr"
        p.write_text(text, encoding="utf-8")
        ok, err = ScribbleValidator().validate_protocol(p)
        assert ok, f"Scribble rejected synthesized choice protocol: {err}"
    print("[synthesis] internal choice + common-suffix factoring VALID")


def test_synthesis_detects_circular_wait():
    """The trade_deadlock story bottom-up: each skill is locally plausible,
    together they deadlock — caught at composition time, before any run."""
    buyer = LocalType(role="Buyer", body=[
        LAction("recv", "Carrier", "DeliverGoods", "String"),
        LAction("send", "Seller", "Payment", "Double")])
    seller = LocalType(role="Seller", body=[
        LAction("recv", "Buyer", "Payment", "Double"),
        LAction("send", "Carrier", "ShipGoods", "String")])
    carrier = LocalType(role="Carrier", body=[
        LAction("recv", "Seller", "ShipGoods", "String"),
        LAction("send", "Buyer", "DeliverGoods", "String")])
    try:
        synthesize_global({"Buyer": buyer, "Seller": seller, "Carrier": carrier})
        raise AssertionError("circular wait must fail synthesis")
    except SynthesisError as e:
        msg = str(e)
        assert "Buyer" in msg and "Seller" in msg and "Carrier" in msg
        print("[synthesis] circular wait diagnosed per-role before runtime")


class _FakeCompactionLLM:
    """Returns canned local-type JSON per role — the LLM path, offline."""

    ANSWERS = {
        "Buyer": {"role": "Buyer", "flow": [
            {"kind": "send", "peer": "Escrow", "label": "Deposit", "payload": "Double"},
            {"kind": "recv", "peer": "Carrier", "label": "DeliverGoods", "payload": "String"},
            {"kind": "send", "peer": "Escrow", "label": "ConfirmReceipt", "payload": "String"},
        ]},
        "Escrow": {"role": "Escrow", "flow": [
            {"kind": "recv", "peer": "Buyer", "label": "Deposit", "payload": "Double"},
            {"kind": "recv", "peer": "Buyer", "label": "ConfirmReceipt", "payload": "String"},
        ]},
        "Carrier": {"role": "Carrier", "flow": [
            {"kind": "send", "peer": "Buyer", "label": "DeliverGoods", "payload": "String"},
        ]},
    }

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        for role, ans in self.ANSWERS.items():
            if f"ROLE NAME HINT (from the filename): {role}" in user_prompt:
                return json.dumps(ans)
        return "{}"


def test_llm_compaction_path():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        sk = _write_skills(d, {
            "Buyer": "You are the Buyer. Deposit funds with the escrow, wait "
                     "for the goods, then confirm receipt.",
            "Escrow": "You hold the Buyer's deposit and wait for their "
                      "confirmation of receipt.",
            "Carrier": "You deliver the goods to the Buyer.",
        })
        result = compact_and_synthesize(sk, d / "Mini.scr",
                                        protocol_name="Mini",
                                        llm_client=_FakeCompactionLLM())
        assert result.valid, result.error
        assert all(lt.notes and "LLM" in lt.notes[-1]
                   for lt in result.local_types.values())
        print("[compaction] LLM path (fake client) → VALID protocol")


def test_stjp_skills_format_extraction():
    md = """# Verifier skills
**Protocol**: `P1_v0.scr`

## Role Purpose
Verify audits.

## Receives
- `AuditRequest(String)` from Analyst - the audit to verify

## Sends
- `AuditApproval(String)` to Analyst - the verdict

## Execution Flow
1. Receive `AuditRequest` from Analyst.
2. Send `AuditApproval` to Analyst.
"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "Verifier_skills.md"
        p.write_text(md, encoding="utf-8")
        lt = compact_skill_file(p, ["Verifier", "Analyst"], llm_client=None)
        assert lt.role == "Verifier" and lt.confidence == "high"
        acts = lt.all_actions()
        assert [a.direction for a in acts] == ["recv", "send"]
        assert acts[0].payload_type == "String"
        print("[compaction] STJP skills format extracted deterministically")


if __name__ == "__main__":
    test_localtype_block_parsing_with_choice()
    test_compatibility_catches_mismatches()
    test_deterministic_synthesis_sequential()
    test_synthesis_with_internal_choice_and_suffix_factoring()
    test_synthesis_detects_circular_wait()
    test_llm_compaction_path()
    test_stjp_skills_format_extraction()
    print("ALL PASS")
