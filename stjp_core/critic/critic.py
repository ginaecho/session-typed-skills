"""The Critic — cross-message policy analysis, static AND runtime.

The Monitor judges one message at a time against one role's EFSM. The Critic
judges the WHOLE conversation against cross-message policies (.policy files):

  static mode   every enumerated execution path of the validated global type
                is checked BEFORE any agent runs. A static finding means the
                protocol itself permits a policy breach — even perfectly
                protocol-conformant agents could commit it.
  runtime mode  the actual trace (list of TraceEvent) is checked, exactly.

Both modes are deterministic code — no LLM in the judging path. The optional
LLM only DRAFTS policies from the user intent (`draft_policies_from_intent`),
mirroring evaluation/goal_elicitor.py; a human approves the sidecar.

Usage:
    python -m stjp_core.critic.critic <protocol.scr> [--policies <file>]
                                      [--trace <events.jsonl>]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from stjp_core.critic.policies import (
    PolicySet, FlowPolicy, SequencePolicy, SeparationPolicy, AggregatePolicy,
    parse_policy_file, find_policy_file)
from stjp_core.critic.protocol_paths import GMessage, paths_for_protocol


class CriticSeverity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass
class CriticFinding:
    policy_id: str
    policy_kind: str          # flow | sequence | separation | aggregate
    mode: str                 # "static" | "runtime"
    severity: CriticSeverity
    message: str
    witness: list[str] = field(default_factory=list)   # the offending chain


@dataclass
class CriticReport:
    findings: list[CriticFinding] = field(default_factory=list)
    paths_checked: int = 0
    events_checked: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == CriticSeverity.ERROR for f in self.findings)

    def summary_line(self) -> str:
        errs = sum(1 for f in self.findings if f.severity == CriticSeverity.ERROR)
        warns = len(self.findings) - errs
        scope = (f"{self.paths_checked} path(s)" if self.paths_checked
                 else f"{self.events_checked} event(s)")
        if not self.findings:
            return f"CRITIC PASS — {scope}, no cross-message policy issues"
        return f"CRITIC {'FAIL' if errs else 'PASS'} — {scope}, {errs} error(s), {warns} warning(s)"

    def format_report(self) -> str:
        lines = [self.summary_line()]
        for f in self.findings:
            lines.append(f"  [{f.severity.value}] {f.policy_id} ({f.policy_kind}, {f.mode}): {f.message}")
            for w in f.witness:
                lines.append(f"      {w}")
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)

    def as_llm_feedback(self) -> str:
        """Findings formatted as an error report the Revisor/Architect LLM can act on."""
        lines = ["CRITIC POLICY VIOLATIONS (cross-message analysis):", ""]
        for f in self.findings:
            if f.severity != CriticSeverity.ERROR:
                continue
            lines.append(f"- policy {f.policy_id} [{f.policy_kind}]: {f.message}")
            if f.witness:
                lines.append("  witness (the offending chain of interactions):")
                for w in f.witness:
                    lines.append(f"    {w}")
        lines.append("")
        lines.append("Revise the protocol so that NO execution path violates these "
                     "policies, while keeping all interactions that do not conflict "
                     "with them.")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Per-path / per-trace checks (shared by static and runtime modes)
# ─────────────────────────────────────────────────────────────────────────────

def _edge_str(e) -> str:
    return f"{e.sender} -> {e.receiver} : {e.label}"


def _check_flow(events: list, p: FlowPolicy, mode: str,
                path_desc: str) -> list[CriticFinding]:
    """Taint-propagation over one linear event sequence (may-flow, conservative:
    once a role holds tainted data, every later send by that role propagates)."""
    findings = []
    tainted: dict[str, list[str]] = {}   # role -> chain of edges that tainted it

    def _violate(chain):
        findings.append(CriticFinding(
            policy_id=p.id, policy_kind="flow", mode=mode,
            severity=CriticSeverity.ERROR,
            message=(f"{p.description or 'information-flow policy'} — data from "
                     f"[{p.source}] can reach the forbidden target"
                     + (f" on {path_desc}" if path_desc else "")),
            witness=chain))

    for e in events:
        edge = _edge_str(e)
        is_source = p.source.matches(e.sender, e.receiver, e.label)
        propagating = e.sender in tainted
        if not (is_source or propagating):
            continue
        chain = (tainted.get(e.sender, []) + [edge]) if propagating else [edge]
        if p.declassify and p.declassify.matches(e.sender, e.receiver, e.label):
            continue  # laundered: the receiver gets a declassified copy
        hit_role = p.forbidden_role and e.receiver == p.forbidden_role
        hit_edge = p.forbidden and p.forbidden.matches(e.sender, e.receiver, e.label)
        if hit_role or hit_edge:
            _violate(chain)
            return findings   # one witness per path is enough
        prev = tainted.get(e.receiver)
        if prev is None or len(chain) < len(prev):
            tainted[e.receiver] = chain
    return findings


def _check_sequence(events: list, p: SequencePolicy, mode: str,
                    path_desc: str) -> list[CriticFinding]:
    seen_before = False
    for i, e in enumerate(events):
        if p.before.matches(e.sender, e.receiver, e.label):
            seen_before = True
        if p.after.matches(e.sender, e.receiver, e.label) and not seen_before:
            prefix = [_edge_str(x) for x in events[:i + 1]]
            return [CriticFinding(
                policy_id=p.id, policy_kind="sequence", mode=mode,
                severity=CriticSeverity.ERROR,
                message=(f"{p.description or 'ordering policy'} — "
                         f"[{p.after}] occurs without a preceding [{p.before}]"
                         + (f" on {path_desc}" if path_desc else "")),
                witness=prefix[-6:])]
    return []


def _check_separation(events: list, p: SeparationPolicy, mode: str,
                      path_desc: str) -> list[CriticFinding]:
    first_senders = {e.sender for e in events
                     if p.first.matches(e.sender, e.receiver, e.label)}
    second_senders = {e.sender for e in events
                      if p.second.matches(e.sender, e.receiver, e.label)}
    both = first_senders & second_senders
    if both:
        return [CriticFinding(
            policy_id=p.id, policy_kind="separation", mode=mode,
            severity=CriticSeverity.ERROR,
            message=(f"{p.description or 'separation-of-duty policy'} — role(s) "
                     f"{sorted(both)} send both [{p.first}] and [{p.second}]"
                     + (f" on {path_desc}" if path_desc else "")),
            witness=[f"{r} performs both duties" for r in sorted(both)])]
    return []


def _check_aggregate(events: list, p: AggregatePolicy, mode: str,
                     path_desc: str, has_loops: bool) -> list[CriticFinding]:
    hits = [e for e in events if p.count.matches(e.sender, e.receiver, e.label)]
    findings = []
    if len(hits) > p.max_count:
        findings.append(CriticFinding(
            policy_id=p.id, policy_kind="aggregate", mode=mode,
            severity=CriticSeverity.ERROR,
            message=(f"{p.description or 'aggregate policy'} — [{p.count}] occurs "
                     f"{len(hits)} time(s), max {p.max_count}"
                     + (f" on {path_desc}" if path_desc else "")),
            witness=[_edge_str(e) for e in hits]))
    elif mode == "static" and has_loops and hits:
        findings.append(CriticFinding(
            policy_id=p.id, policy_kind="aggregate", mode=mode,
            severity=CriticSeverity.WARNING,
            message=(f"[{p.count}] occurs inside/alongside a rec loop — repetition "
                     f"is potentially unbounded; enforce max {p.max_count} at "
                     f"runtime (loops are unrolled once in static analysis)"),
            witness=[_edge_str(e) for e in hits]))
    return findings


def _check_one_sequence_of_events(events: list, policies: PolicySet, mode: str,
                                  path_desc: str = "",
                                  has_loops: bool = False) -> list[CriticFinding]:
    findings: list[CriticFinding] = []
    for p in policies:
        if isinstance(p, FlowPolicy):
            findings.extend(_check_flow(events, p, mode, path_desc))
        elif isinstance(p, SequencePolicy):
            findings.extend(_check_sequence(events, p, mode, path_desc))
        elif isinstance(p, SeparationPolicy):
            findings.extend(_check_separation(events, p, mode, path_desc))
        elif isinstance(p, AggregatePolicy):
            findings.extend(_check_aggregate(events, p, mode, path_desc, has_loops))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Static mode — every path of the global type
# ─────────────────────────────────────────────────────────────────────────────

def run_static_critic(protocol: str | Path, policies: PolicySet,
                      protocol_name: str | None = None) -> CriticReport:
    """Check every enumerated execution path of the global protocol against
    the policy set. `protocol` is a path to a .scr file or raw Scribble text."""
    if isinstance(protocol, Path) or (isinstance(protocol, str)
                                      and "\n" not in protocol
                                      and protocol.endswith(".scr")):
        text = Path(protocol).read_text(encoding="utf-8")
    else:
        text = protocol

    pathset = paths_for_protocol(text, protocol_name)
    report = CriticReport(paths_checked=len(pathset.paths), notes=list(pathset.notes))
    if pathset.truncated:
        report.notes.append("path enumeration truncated — findings may be incomplete")

    seen: set[tuple] = set()   # dedupe identical findings across paths
    for i, path in enumerate(pathset.paths):
        desc = f"path {i + 1}/{len(pathset.paths)}"
        for f in _check_one_sequence_of_events(path, policies, "static", desc,
                                               pathset.has_loops):
            key = (f.policy_id, f.severity.value, tuple(f.witness))
            if key not in seen:
                seen.add(key)
                report.findings.append(f)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Runtime mode — the actual trace
# ─────────────────────────────────────────────────────────────────────────────

def run_runtime_critic(events: list, policies: PolicySet) -> CriticReport:
    """Check an executed trace (objects with .sender/.receiver/.label — e.g.
    monitor.TraceEvent — or dicts) against the policy set."""
    class _E:
        __slots__ = ("sender", "receiver", "label")

        def __init__(self, sender, receiver, label):
            self.sender, self.receiver, self.label = sender, receiver, label

    norm = []
    for e in events:
        if isinstance(e, dict):
            norm.append(_E(e.get("sender", ""), e.get("receiver", ""),
                           _strip_label(e.get("label", ""))))
        else:
            norm.append(_E(e.sender, e.receiver, _strip_label(e.label)))

    report = CriticReport(events_checked=len(norm))
    report.findings = _check_one_sequence_of_events(norm, policies, "runtime")
    return report


def _strip_label(label: str) -> str:
    idx = label.find("(")
    return label[:idx] if idx > 0 else label


# ─────────────────────────────────────────────────────────────────────────────
# Optional LLM assist — draft policies from the user intent (human approves)
# ─────────────────────────────────────────────────────────────────────────────

POLICY_DRAFT_SYSTEM_PROMPT = """You extract CROSS-MESSAGE safety policies from a task
description and its multiparty protocol. Output a `.policy` sidecar file.

Supported sections (repeat as needed; edge patterns are `Sender -> Receiver : Label`
and each position may be `*`):

[flow]
id: F1
description: <one line>
source: <edge where sensitive data enters>
forbidden_role: <role that must never receive it>   # or: forbidden: <edge>
# optional: declassify: <edge that redacts/launders it>

[sequence]
id: S1
description: <one line>
before: <edge that must happen first>
after: <edge that must not happen without it>

[separation]
id: D1
description: <one line>
first: <edge>
second: <edge>    # the same role must not SEND both

[aggregate]
id: A1
description: <one line>
count: <edge>
max: <int>

Only emit policies clearly implied by the intent (confidentiality, approvals,
duty separation, retry caps). Use ONLY roles and labels from the protocol.
Return ONLY the policy file content, no fences, no prose."""


def draft_policies_from_intent(intent: str, protocol_text: str,
                               llm_client=None, mock: bool = False) -> str:
    """LLM drafts a .policy sidecar from the intent (mock returns a fixture).
    The caller must have a HUMAN review the draft before adopting it."""
    if mock:
        return ("[sequence]\n"
                "id: S1\n"
                "description: approval must precede the final report\n"
                "before: TaxVerifier -> RevenueAnalyst : RevenueAuditApproval\n"
                "after: Writer -> Fetcher : GenerateReport\n")
    user = (f"TASK INTENT:\n{intent}\n\nPROTOCOL:\n{protocol_text}\n\n"
            f"Emit the .policy file content.")
    reply = llm_client.generate(POLICY_DRAFT_SYSTEM_PROMPT, user)
    return re.sub(r"^```[a-z]*\n|\n```$", "", (reply or "").strip())


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="STJP Critic — cross-message policy check (static / runtime)")
    ap.add_argument("protocol", help="path to the .scr global protocol")
    ap.add_argument("--policies", help="path to the .policy sidecar "
                                       "(default: auto-discover next to the .scr)")
    ap.add_argument("--protocol-name", default=None)
    ap.add_argument("--trace", help="events .jsonl — also run the runtime critic")
    args = ap.parse_args(argv)

    scr = Path(args.protocol)
    ppath = Path(args.policies) if args.policies else find_policy_file(scr)
    if ppath is None:
        print(f"no .policy sidecar found for {scr} — nothing to check")
        return 0
    policies = parse_policy_file(ppath)
    print(f"[critic] {len(policies)} policy(ies) from {ppath.name}")

    report = run_static_critic(scr, policies, args.protocol_name)
    print(report.format_report())
    rc = 0 if report.passed else 1

    if args.trace:
        events = []
        for line in Path(args.trace).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        rt = run_runtime_critic(events, policies)
        print(rt.format_report())
        rc = rc or (0 if rt.passed else 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
