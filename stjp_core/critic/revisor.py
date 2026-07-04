"""The Revisor — repairs the rules when the Critic flags something.

Closes the quartet: Checker (Scribble) proves the shape, Monitor enforces
each message, Critic finds cross-message policy breaches, REVISOR fixes the
protocol so the breach becomes impossible — then Scribble AND the Critic
re-judge the fix. Same posture as everywhere in STJP: the LLM only DRAFTS;
deterministic checkers decide.

    CriticReport (findings)
          │
          ▼  LLM drafts a revised global protocol (findings as constraints)
    revised .scr
          │
          ▼  Scribble validate  (shape: deadlock-free, projectable)
          ▼  static Critic      (policy: no path violates)
    both pass → accepted revision      either fails → feedback, retry

`critic_revise_loop()` runs the whole closed loop:
validate → critic → revise → re-validate → re-critique → … until clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from stjp_core.compiler.validator import ScribbleValidator
from stjp_core.critic.critic import CriticReport, run_static_critic
from stjp_core.critic.policies import PolicySet, parse_policy_file, find_policy_file


REVISOR_SYSTEM_PROMPT = """You are the STJP Revisor. You repair a Scribble multiparty
protocol so that it no longer violates cross-message policies found by the Critic,
while preserving every interaction that does not conflict with them.

You will receive: the current protocol, the Critic findings (each with a witness
chain), and possibly a previous revision plus the error it failed with.

REPAIR STRATEGIES (pick the least invasive that removes the violation on EVERY path):
- sequence violation (B must precede A): insert the missing `before` interaction on
  the path(s) that lack it — usually by adding it to the branch that skips it, or by
  moving the shared suffix after the required interaction.
- flow violation (data must not reach a role): remove or re-route the offending
  message; or route it through the declassify step named by the policy.
- separation violation (same role does both duties): reassign one of the two
  messages to a different existing role.
- aggregate violation (too many repetitions): remove the excess interactions or
  restructure the loop.

SCRIBBLE RULES (the revision must still compile):
- `module <Name>;` first; declare payload types with
  `data <java> "java.lang.String" from "rt.jar" as String;` (String/Double/Int/Bool).
- Messages: `Label(Type) from A to B;`  Choice: `choice at R { ... } or { ... }`.
- Every role used must be declared in the protocol header.
- In a choice, each receiving role must get its FIRST message from the same role in
  every branch, and a role appearing in one branch must appear in all (or none).
- Factor identical trailing messages out of the choice to after the block.

Return ONLY the revised Scribble code. Start with `module`, end with `}`."""


@dataclass
class RevisionResult:
    success: bool
    revised_path: Path | None = None
    protocol_text: str = ""
    attempts: int = 0
    report_before: CriticReport | None = None
    report_after: CriticReport | None = None
    error: str = ""
    history: list[str] = field(default_factory=list)   # one line per attempt


def _rename_module(text: str, new_module: str) -> str:
    return re.sub(r"module\s+[\w.]+\s*;", f"module {new_module};", text, count=1)


def _extract_code(reply: str) -> str:
    blocks = re.findall(r"```(?:scribble)?\s*\n(.*?)```", reply or "", re.DOTALL)
    code = "\n\n".join(b.strip() for b in blocks) if blocks else (reply or "").strip()
    if not code.startswith("module"):
        m = re.search(r"(module\s+[\w.]+;.*)", code, re.DOTALL)
        if m:
            code = m.group(1)
    return code


def revise_protocol(protocol_path: Path, report: CriticReport,
                    policies: PolicySet, llm_client=None,
                    output_dir: Path | None = None,
                    max_attempts: int = 4, mock: bool = False,
                    protocol_name: str | None = None) -> RevisionResult:
    """Draft-and-check loop: LLM revises, Scribble + Critic judge.

    `mock=True` uses the built-in fixture (the publish-without-approval case)
    for offline tests/demos. Otherwise `llm_client.generate(system, user)` is
    called (any object with that method works — inject a fake in tests).
    """
    protocol_path = Path(protocol_path).resolve()
    current_text = protocol_path.read_text(encoding="utf-8")
    out_dir = Path(output_dir).resolve() if output_dir else protocol_path.parent
    validator = ScribbleValidator()

    result = RevisionResult(success=False, report_before=report)
    prev_draft, prev_error = "", ""

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        stem = f"{protocol_path.stem}_rev{attempt}"
        draft = _draft_revision(current_text, report, prev_draft, prev_error,
                                llm_client, mock)
        draft = _rename_module(_extract_code(draft), stem)
        revised_path = out_dir / f"{stem}.scr"
        revised_path.write_text(draft, encoding="utf-8")

        ok, scribble_err = validator.validate_protocol(revised_path)
        if not ok:
            prev_draft, prev_error = draft, f"SCRIBBLE REJECTED:\n{scribble_err}"
            result.history.append(f"attempt {attempt}: Scribble rejected")
            if mock:
                break
            continue

        after = run_static_critic(revised_path, policies, protocol_name)
        if not after.passed:
            prev_draft = draft
            prev_error = "SCRIBBLE ACCEPTED but the Critic still finds:\n" + \
                after.as_llm_feedback()
            result.history.append(
                f"attempt {attempt}: Scribble OK, Critic still fails "
                f"({sum(1 for f in after.findings)} finding(s))")
            if mock:
                break
            continue

        result.success = True
        result.revised_path = revised_path
        result.protocol_text = draft
        result.report_after = after
        result.history.append(f"attempt {attempt}: Scribble OK + Critic clean")
        return result

    result.error = prev_error or "no revision accepted"
    return result


def _draft_revision(current_text: str, report: CriticReport, prev_draft: str,
                    prev_error: str, llm_client, mock: bool) -> str:
    if mock:
        return _mock_revision()
    if prev_error:
        user = (f"Your previous revision failed:\n{prev_error}\n\n"
                f"PREVIOUS REVISION:\n```scribble\n{prev_draft}\n```\n\n"
                f"ORIGINAL PROTOCOL:\n```scribble\n{current_text}\n```\n\n"
                f"ORIGINAL CRITIC FINDINGS:\n{report.as_llm_feedback()}\n\n"
                f"Produce a corrected revision.")
    else:
        user = (f"CURRENT PROTOCOL:\n```scribble\n{current_text}\n```\n\n"
                f"CRITIC FINDINGS:\n{report.as_llm_feedback()}\n\n"
                f"Produce the revised protocol.")
    return llm_client.generate(REVISOR_SYSTEM_PROMPT, user)


def _mock_revision() -> str:
    """Fixture revision for the publish-without-approval worked example
    (see tests/test_critic_revisor.py): the approval round-trip is made
    unconditional, so every path satisfies the sequence policy."""
    return (
        "module fixture;\n\n"
        'data <java> "java.lang.String" from "rt.jar" as String;\n\n'
        "global protocol ReportPublish(role Analyst, role Approver, role Writer) {\n"
        "    Draft(String) from Analyst to Writer;\n"
        "    ApprovalRequest(String) from Writer to Approver;\n"
        "    Approved(String) from Approver to Writer;\n"
        "    PublishNow(String) from Writer to Analyst;\n"
        "}\n"
    )


def critic_revise_loop(protocol_path: Path, policy_path: Path | None = None,
                       llm_client=None, mock: bool = False,
                       max_rounds: int = 3,
                       protocol_name: str | None = None) -> RevisionResult:
    """The full closed loop on one protocol file:

    critic(static) → clean? done : revise → Scribble+critic → … (≤ max_rounds)

    Returns the last RevisionResult; when the protocol was already clean, a
    success result pointing at the ORIGINAL file with attempts=0.
    """
    protocol_path = Path(protocol_path).resolve()
    ppath = Path(policy_path) if policy_path else find_policy_file(protocol_path)
    if ppath is None:
        r = RevisionResult(success=True, revised_path=protocol_path, attempts=0)
        r.history.append("no .policy sidecar — nothing for the Critic to check")
        return r
    policies = parse_policy_file(ppath)

    current = protocol_path
    last: RevisionResult | None = None
    for _round in range(max_rounds):
        report = run_static_critic(current, policies, protocol_name)
        if report.passed:
            r = RevisionResult(success=True, revised_path=current,
                               protocol_text=current.read_text(encoding="utf-8"),
                               attempts=(last.attempts if last else 0),
                               report_before=(last.report_before if last else report),
                               report_after=report)
            r.history = (last.history if last else []) + \
                [f"round {_round}: Critic clean on {current.name}"]
            return r
        last = revise_protocol(current, report, policies, llm_client,
                               max_attempts=4, mock=mock,
                               protocol_name=protocol_name)
        if not last.success:
            return last
        current = last.revised_path
    # loop exhausted — re-check once more
    report = run_static_critic(current, policies, protocol_name)
    last.success = report.passed
    last.report_after = report
    return last


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="STJP Revisor — repair a protocol until Scribble + Critic pass")
    ap.add_argument("protocol", help="path to the .scr global protocol")
    ap.add_argument("--policies", default=None)
    ap.add_argument("--mock", action="store_true",
                    help="use the built-in fixture instead of an LLM")
    ap.add_argument("--protocol-name", default=None)
    args = ap.parse_args(argv)

    llm = None
    if not args.mock:
        from stjp_core.foundry.llm_client import LLMClient
        llm = LLMClient()

    result = critic_revise_loop(
        Path(args.protocol),
        Path(args.policies) if args.policies else None,
        llm_client=llm, mock=args.mock,
        protocol_name=args.protocol_name)

    for h in result.history:
        print(f"[revisor] {h}")
    if result.success:
        print(f"[revisor] ACCEPTED: {result.revised_path}")
        return 0
    print(f"[revisor] FAILED: {result.error[:400]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
