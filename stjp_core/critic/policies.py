"""Cross-message policy model + parser (.policy sidecar files).

A `.policy` file sits next to a protocol version, exactly like the `.refn`
sidecar, and declares properties that span MULTIPLE messages — the Critic's
input. Four policy kinds are supported:

  [flow]        information flow / confidentiality. Data entering at `source`
                must never reach `forbidden_role` (or a `forbidden` edge),
                unless it passes through a `declassify` edge first.
  [sequence]    ordering obligation. Every occurrence of `after` must be
                preceded (on the same path/trace) by an occurrence of `before`.
  [separation]  separation of duty. The role that sends a `first` message must
                not also be the role that sends a `second` message.
  [aggregate]   bounded repetition. Events matching `count` may occur at most
                `max` times per path/trace.

Edge patterns are written `Sender -> Receiver : Label`; each position accepts
`*` as a wildcard. Example sidecar:

    # confidential audit figures must not reach the report writer directly
    [flow]
    id: F1
    description: raw audited figures reach the Writer only via the analyst
    source: TaxSpecialist -> * : AuditedRevenue
    forbidden: * -> Writer : AuditedRevenue

    [sequence]
    id: S1
    description: approval precedes the final report
    before: TaxVerifier -> RevenueAnalyst : RevenueAuditApproval
    after: Writer -> Fetcher : GenerateReport

Research basis:
  - Castellani, Dezani-Ciancaglini, Perez — Information Flow Safety in
    Multiparty Sessions (MSCS'16): security levels / leak-freedom on MPST.
  - Capecchi, Castellani, Dezani — Session Types with Access Control and
    Information Flow (secure sessions).
  - Bocchi, Chen, Demangeon, Honda, Yoshida (FORTE'13/TCS'17): assertions
    beyond single payloads need a cross-message observer — this module is
    that observer's specification language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class PolicyParseError(Exception):
    pass


_EDGE_RE = re.compile(
    r'^\s*(\*|\w+)\s*->\s*(\*|\w+)\s*(?::\s*(\*|\w+))?\s*$')


@dataclass(frozen=True)
class EdgePattern:
    """A (sender, receiver, label) pattern; '*' matches anything."""
    sender: str = "*"
    receiver: str = "*"
    label: str = "*"

    @staticmethod
    def parse(text: str) -> "EdgePattern":
        m = _EDGE_RE.match(text)
        if not m:
            raise PolicyParseError(
                f"bad edge pattern {text!r} — expected 'Sender -> Receiver : Label' "
                f"(each position may be '*')")
        return EdgePattern(m.group(1), m.group(2), m.group(3) or "*")

    def matches(self, sender: str, receiver: str, label: str) -> bool:
        return ((self.sender == "*" or self.sender == sender)
                and (self.receiver == "*" or self.receiver == receiver)
                and (self.label == "*" or self.label == label))

    def __str__(self):
        return f"{self.sender} -> {self.receiver} : {self.label}"


@dataclass
class FlowPolicy:
    """Taint introduced at `source` must never reach the forbidden target."""
    id: str
    description: str = ""
    source: EdgePattern = field(default_factory=EdgePattern)
    forbidden_role: str = ""                  # role that must never observe it
    forbidden: EdgePattern | None = None      # or a specific forbidden edge
    declassify: EdgePattern | None = None     # edge that launders the taint
    kind: str = "flow"


@dataclass
class SequencePolicy:
    """Every `after` event must be preceded by a `before` event."""
    id: str
    description: str = ""
    before: EdgePattern = field(default_factory=EdgePattern)
    after: EdgePattern = field(default_factory=EdgePattern)
    kind: str = "sequence"


@dataclass
class SeparationPolicy:
    """The sender of a `first` event must differ from the sender of `second`."""
    id: str
    description: str = ""
    first: EdgePattern = field(default_factory=EdgePattern)
    second: EdgePattern = field(default_factory=EdgePattern)
    kind: str = "separation"


@dataclass
class AggregatePolicy:
    """Events matching `count` may occur at most `max_count` times."""
    id: str
    description: str = ""
    count: EdgePattern = field(default_factory=EdgePattern)
    max_count: int = 1
    kind: str = "aggregate"


Policy = FlowPolicy | SequencePolicy | SeparationPolicy | AggregatePolicy


@dataclass
class PolicySet:
    policies: list[Policy] = field(default_factory=list)
    source_path: Path | None = None

    def __iter__(self):
        return iter(self.policies)

    def __len__(self):
        return len(self.policies)

    def __bool__(self):
        return bool(self.policies)


_SECTION_RE = re.compile(r'^\[\s*(flow|sequence|separation|aggregate)\s*\]\s*$',
                         re.IGNORECASE)


def parse_policy_text(text: str) -> PolicySet:
    """Parse `.policy` content into a PolicySet."""
    policies: list[Policy] = []
    kind: str | None = None
    fields: dict[str, str] = {}
    n_anon = 0

    def _flush():
        nonlocal kind, fields, n_anon
        if kind is None:
            return
        n_anon += 1
        pid = fields.get("id", f"{kind.upper()[0]}{n_anon}")
        desc = fields.get("description", "")
        try:
            if kind == "flow":
                p: Policy = FlowPolicy(
                    id=pid, description=desc,
                    source=EdgePattern.parse(_req(fields, "source", pid)),
                    forbidden_role=fields.get("forbidden_role", ""),
                    forbidden=(EdgePattern.parse(fields["forbidden"])
                               if "forbidden" in fields else None),
                    declassify=(EdgePattern.parse(fields["declassify"])
                                if "declassify" in fields else None))
                if not p.forbidden_role and p.forbidden is None:
                    raise PolicyParseError(
                        f"[flow] {pid}: needs 'forbidden_role:' or 'forbidden:'")
            elif kind == "sequence":
                p = SequencePolicy(
                    id=pid, description=desc,
                    before=EdgePattern.parse(_req(fields, "before", pid)),
                    after=EdgePattern.parse(_req(fields, "after", pid)))
            elif kind == "separation":
                p = SeparationPolicy(
                    id=pid, description=desc,
                    first=EdgePattern.parse(_req(fields, "first", pid)),
                    second=EdgePattern.parse(_req(fields, "second", pid)))
            else:  # aggregate
                p = AggregatePolicy(
                    id=pid, description=desc,
                    count=EdgePattern.parse(_req(fields, "count", pid)),
                    max_count=int(fields.get("max", "1")))
        finally:
            kind, fields = None, {}
        policies.append(p)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # allow trailing inline comments after values
        m = _SECTION_RE.match(line)
        if m:
            _flush()
            kind = m.group(1).lower()
            continue
        if kind is None:
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            val = val.split("#", 1)[0].strip() if " #" in val else val.strip()
            fields[key.strip().lower()] = val
    _flush()
    return PolicySet(policies=policies)


def _req(fields: dict, key: str, pid: str) -> str:
    if key not in fields:
        raise PolicyParseError(f"policy {pid}: missing required field '{key}:'")
    return fields[key]


def parse_policy_file(path: Path) -> PolicySet:
    ps = parse_policy_text(Path(path).read_text(encoding="utf-8"))
    ps.source_path = Path(path)
    return ps


def find_policy_file(protocol_path: Path) -> Path | None:
    """Discover the policy sidecar for a protocol: `<stem>.policy` next to the
    .scr, else a shared `policies.policy` in the same directory."""
    protocol_path = Path(protocol_path)
    sibling = protocol_path.with_suffix(".policy")
    if sibling.exists():
        return sibling
    shared = protocol_path.parent / "policies.policy"
    if shared.exists():
        return shared
    return None
