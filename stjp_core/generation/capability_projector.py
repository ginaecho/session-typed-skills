"""
Capability Projector

From each role's projected EFSM, computes the minimum capability set
(outgoing message labels -> required tools). Compares against declared
allowed-tools frontmatter. Flags over/under-provisioned agents.

This implements SCRIBBLE.md §4.8: "Capability projection <-> frontmatter cross-check."

Research basis:
  - MPST_STATIC.md §2.5: "Tight capability projection — minimum permissions per role"
  - Each role's local type uses only the message labels it sends/receives.
    The set of outgoing labels is the role's minimum required capability set.
"""

from dataclasses import dataclass, field
from stjp_core.compiler.efsm_parser import EFSM


@dataclass
class RoleCapabilities:
    """Projected capabilities for one role."""
    role: str
    sends_to: dict[str, set[str]] = field(default_factory=dict)     # peer -> {labels}
    receives_from: dict[str, set[str]] = field(default_factory=dict) # peer -> {labels}
    all_send_labels: set[str] = field(default_factory=set)
    all_receive_labels: set[str] = field(default_factory=set)
    all_peers: set[str] = field(default_factory=set)

    @property
    def minimum_tools(self) -> set[str]:
        """Minimum tool set implied by outgoing (send) transitions."""
        return self.all_send_labels

    @property
    def required_inputs(self) -> set[str]:
        """Required input capabilities (what this role must be able to receive)."""
        return self.all_receive_labels


@dataclass
class CapabilityAudit:
    """Result of comparing projected capabilities against declared tools."""
    role: str
    projected: set[str]
    declared: set[str]
    missing: set[str]           # in projected but not declared (UNDER-provisioned)
    excess: set[str]            # in declared but not projected (OVER-provisioned)
    is_sound: bool              # declared >= projected


def project_capabilities(efsm: EFSM) -> RoleCapabilities:
    """Extract capability profile from a role's EFSM."""
    caps = RoleCapabilities(role=efsm.role)

    for t in efsm.transitions:
        caps.all_peers.add(t.peer)
        if t.direction == "send":
            caps.all_send_labels.add(t.label)
            if t.peer not in caps.sends_to:
                caps.sends_to[t.peer] = set()
            caps.sends_to[t.peer].add(t.label)
        else:
            caps.all_receive_labels.add(t.label)
            if t.peer not in caps.receives_from:
                caps.receives_from[t.peer] = set()
            caps.receives_from[t.peer].add(t.label)

    return caps


def project_all_capabilities(efsms: dict[str, EFSM]) -> dict[str, RoleCapabilities]:
    """Project capabilities for all roles."""
    return {role: project_capabilities(efsm) for role, efsm in efsms.items()}


def audit_capabilities(
    capabilities: RoleCapabilities,
    declared_tools: set[str]
) -> CapabilityAudit:
    """
    Compare projected minimum capabilities against declared tools.

    Sound if: declared_tools >= projected.minimum_tools
    (i.e., the agent has at least the capabilities the protocol requires)
    """
    projected = capabilities.minimum_tools
    missing = projected - declared_tools
    excess = declared_tools - projected

    return CapabilityAudit(
        role=capabilities.role,
        projected=projected,
        declared=declared_tools,
        missing=missing,
        excess=excess,
        is_sound=len(missing) == 0,
    )


def print_capability_report(efsms: dict[str, EFSM], declared: dict[str, set[str]] | None = None):
    """Print a capability projection report for all roles."""
    all_caps = project_all_capabilities(efsms)

    print(f"\n{'='*60}")
    print(f"  CAPABILITY PROJECTION REPORT")
    print(f"{'='*60}\n")

    for role, caps in sorted(all_caps.items()):
        print(f"  Role: {role}")
        print(f"    Sends ({len(caps.all_send_labels)}): {sorted(caps.all_send_labels)}")
        print(f"    Receives ({len(caps.all_receive_labels)}): {sorted(caps.all_receive_labels)}")
        print(f"    Peers: {sorted(caps.all_peers)}")
        if caps.sends_to:
            for peer, labels in sorted(caps.sends_to.items()):
                print(f"      -> {peer}: {sorted(labels)}")
        if caps.receives_from:
            for peer, labels in sorted(caps.receives_from.items()):
                print(f"      <- {peer}: {sorted(labels)}")

        if declared and role in declared:
            audit = audit_capabilities(caps, declared[role])
            status = "SOUND" if audit.is_sound else "UNSOUND"
            print(f"    Declared tools: {sorted(audit.declared)}")
            print(f"    Audit: [{status}]")
            if audit.missing:
                print(f"      MISSING (under-provisioned): {sorted(audit.missing)}")
            if audit.excess:
                print(f"      EXCESS (over-provisioned): {sorted(audit.excess)}")

        print()
