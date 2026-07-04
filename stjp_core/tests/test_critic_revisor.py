"""Offline tests for the Critic (static + runtime) and the Revisor loop.

No Azure needed: policies + path enumeration + taint/ordering checks are pure
code; the Revisor is exercised in mock mode and with an injected fake LLM.
Scribble (vendored in scribble-java/) is used to judge revisions, exactly as
in test_change_request.py.
"""
import sys
import tempfile
from pathlib import Path

# --- bootstrap: make 'stjp_core' importable when run directly ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# --- end bootstrap ---

from stjp_core.critic.policies import (
    parse_policy_text, EdgePattern, FlowPolicy, SequencePolicy)
from stjp_core.critic.critic import run_static_critic, run_runtime_critic
from stjp_core.critic.revisor import revise_protocol, critic_revise_loop


UNSAFE = '''module fixture;

data <java> "java.lang.String" from "rt.jar" as String;

global protocol ReportPublish(role Analyst, role Approver, role Writer) {
    Draft(String) from Analyst to Writer;
    choice at Writer {
        PublishNow(String) from Writer to Analyst;
    } or {
        ApprovalRequest(String) from Writer to Approver;
        Approved(String) from Approver to Writer;
        PublishNow(String) from Writer to Analyst;
    }
}
'''

POLICY = '''
[sequence]
id: S1
description: approval must precede publication
before: Approver -> Writer : Approved
after: Writer -> Analyst : PublishNow

[flow]
id: F1
description: raw draft must not reach the Approver
source: Analyst -> Writer : Draft
forbidden_role: Approver
declassify: Writer -> Approver : ApprovalRequest

[separation]
id: D1
description: the approver must not also be the publisher
first: * -> * : Approved
second: * -> * : PublishNow

[aggregate]
id: A1
description: at most one publication per session
count: Writer -> Analyst : PublishNow
max: 1
'''


def test_policy_parsing():
    ps = parse_policy_text(POLICY)
    assert len(ps) == 4
    kinds = sorted(p.kind for p in ps)
    assert kinds == ["aggregate", "flow", "separation", "sequence"]
    ep = EdgePattern.parse("A -> * : Label")
    assert ep.matches("A", "Anything", "Label")
    assert not ep.matches("B", "Anything", "Label")
    print("[policies] 4 kinds parsed, wildcards match")


def test_static_critic_catches_branch_violation():
    ps = parse_policy_text(POLICY)
    report = run_static_critic(UNSAFE, ps)
    assert report.paths_checked == 2
    assert not report.passed
    ids = {f.policy_id for f in report.findings}
    assert "S1" in ids, "the approval-skipping branch must be flagged"
    # the declassified flow and the distinct-role separation must NOT fire
    assert "F1" not in ids and "D1" not in ids and "A1" not in ids
    print(f"[critic-static] {report.summary_line()}")


def test_static_critic_flow_without_declassify():
    ps = parse_policy_text(
        "[flow]\nid: F2\ndescription: draft must not reach Approver\n"
        "source: Analyst -> Writer : Draft\nforbidden_role: Approver\n")
    report = run_static_critic(UNSAFE, ps)
    assert not report.passed, "without declassify, taint reaches Approver"
    f = report.findings[0]
    assert f.policy_kind == "flow" and len(f.witness) >= 2
    print(f"[critic-static] flow witness chain: {f.witness}")


def test_runtime_critic():
    ps = parse_policy_text(POLICY)
    bad_trace = [
        {"sender": "Analyst", "receiver": "Writer", "label": "Draft"},
        {"sender": "Writer", "receiver": "Analyst", "label": "PublishNow(String)"},
        {"sender": "Writer", "receiver": "Analyst", "label": "PublishNow(String)"},
    ]
    report = run_runtime_critic(bad_trace, ps)
    ids = {f.policy_id for f in report.findings}
    assert "S1" in ids and "A1" in ids
    good_trace = [
        {"sender": "Analyst", "receiver": "Writer", "label": "Draft"},
        {"sender": "Writer", "receiver": "Approver", "label": "ApprovalRequest"},
        {"sender": "Approver", "receiver": "Writer", "label": "Approved"},
        {"sender": "Writer", "receiver": "Analyst", "label": "PublishNow"},
    ]
    assert run_runtime_critic(good_trace, ps).passed
    print("[critic-runtime] bad trace flagged (S1+A1), good trace clean")


def test_revisor_mock_loop():
    with tempfile.TemporaryDirectory() as d:
        scr = Path(d) / "fixture.scr"
        scr.write_text(UNSAFE, encoding="utf-8")
        pol = Path(d) / "fixture.policy"
        pol.write_text(POLICY, encoding="utf-8")

        result = critic_revise_loop(scr, pol, mock=True)
        assert result.success, f"revision failed: {result.error}"
        assert result.revised_path != scr
        revised = result.revised_path.read_text(encoding="utf-8")
        assert "Approved" in revised
        assert result.report_after is not None and result.report_after.passed
        print(f"[revisor] mock loop accepted {result.revised_path.name} "
              f"after {result.attempts} attempt(s)")


class _FakeLLM:
    """Deterministic stand-in for LLMClient.generate — returns the safe
    revision wrapped in prose+fences to exercise extraction."""

    def __init__(self):
        self.calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return ("Here is the revised protocol:\n```scribble\n"
                "module x;\n\n"
                'data <java> "java.lang.String" from "rt.jar" as String;\n\n'
                "global protocol ReportPublish(role Analyst, role Approver, "
                "role Writer) {\n"
                "    Draft(String) from Analyst to Writer;\n"
                "    ApprovalRequest(String) from Writer to Approver;\n"
                "    Approved(String) from Approver to Writer;\n"
                "    PublishNow(String) from Writer to Analyst;\n"
                "}\n```\n")


def test_revisor_with_injected_llm():
    with tempfile.TemporaryDirectory() as d:
        scr = Path(d) / "fixture.scr"
        scr.write_text(UNSAFE, encoding="utf-8")
        ps = parse_policy_text(POLICY)
        report = run_static_critic(UNSAFE, ps)
        fake = _FakeLLM()
        result = revise_protocol(scr, report, ps, llm_client=fake)
        assert result.success, f"revision failed: {result.error}"
        assert fake.calls == 1
        print(f"[revisor] injected-LLM revision accepted "
              f"({result.history[-1]})")


def test_critic_passes_clean_protocol():
    clean = UNSAFE.replace(
        """    choice at Writer {
        PublishNow(String) from Writer to Analyst;
    } or {
        ApprovalRequest(String) from Writer to Approver;
        Approved(String) from Approver to Writer;
        PublishNow(String) from Writer to Analyst;
    }""",
        """    ApprovalRequest(String) from Writer to Approver;
    Approved(String) from Approver to Writer;
    PublishNow(String) from Writer to Analyst;""")
    report = run_static_critic(clean, parse_policy_text(POLICY))
    assert report.passed, report.format_report()
    print("[critic-static] clean protocol passes all 4 policies")


if __name__ == "__main__":
    test_policy_parsing()
    test_static_critic_catches_branch_violation()
    test_static_critic_flow_without_declassify()
    test_runtime_critic()
    test_revisor_mock_loop()
    test_revisor_with_injected_llm()
    test_critic_passes_clean_protocol()
    print("ALL PASS")
