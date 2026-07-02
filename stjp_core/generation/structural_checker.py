"""
Structural Checker (Pass 1)

Compares skills.md files against the protocol (.scr) to verify:
  - Every protocol role has a matching skills file (ACTION_EXISTS)
  - Messages in skills match protocol messages (INPUT_MATCH / OUTPUT_MATCH)
  - Counterparties are correct (COUNTERPARTY)
  - All branches of choice blocks are covered (BRANCH_COVERAGE)

This is like checking that function bodies match their header declarations.
"""

from dataclasses import dataclass
from enum import Enum

from stjp_core.compiler.protocol_parser import ParsedProtocol, get_role_type_signature
from stjp_core.generation.skills_parser import ParsedSkills


class CheckSeverity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass
class StructuralFinding:
    """A structural mismatch between skills and protocol."""
    check_name: str         # e.g. "ACTION_EXISTS", "INPUT_MATCH"
    severity: CheckSeverity
    role: str
    description: str
    expected: str = ""
    actual: str = ""


def check_structural(
    protocol: ParsedProtocol,
    all_skills: dict[str, ParsedSkills]
) -> list[StructuralFinding]:
    """
    Run all structural checks. Returns list of findings (empty = pass).
    
    Args:
        protocol: Parsed protocol from protocol_parser
        all_skills: Dict of role_name -> ParsedSkills from skills_parser
    """
    findings = []
    
    findings.extend(_check_action_exists(protocol, all_skills))
    
    for role in protocol.roles:
        if role not in all_skills:
            continue  # already reported by ACTION_EXISTS
        
        skills = all_skills[role]
        type_sig = get_role_type_signature(protocol, role)
        
        findings.extend(_check_sends(role, skills, type_sig))
        findings.extend(_check_receives(role, skills, type_sig))
        findings.extend(_check_counterparties(role, skills, type_sig))
        findings.extend(_check_branch_coverage(role, skills, protocol))
    
    # Check for phantom roles in skills that don't exist in protocol
    findings.extend(_check_phantom_roles(protocol, all_skills))
    
    return findings


def _check_action_exists(
    protocol: ParsedProtocol,
    all_skills: dict[str, ParsedSkills]
) -> list[StructuralFinding]:
    """Every role in the protocol must have a skills file."""
    findings = []
    for role in protocol.roles:
        if role not in all_skills:
            findings.append(StructuralFinding(
                check_name="ACTION_EXISTS",
                severity=CheckSeverity.ERROR,
                role=role,
                description=f"Role '{role}' is defined in the protocol but has no skills file",
                expected=f"{role}_skills.md",
                actual="(missing)"
            ))
    return findings


def _check_sends(
    role: str,
    skills: ParsedSkills,
    type_sig: dict
) -> list[StructuralFinding]:
    """Messages listed in skills Sends should match protocol sends for this role."""
    findings = []
    
    # Build set of protocol sends: (message_name, receiver)
    proto_sends = set()
    for msg_name, payload, receiver in type_sig['sends']:
        proto_sends.add((msg_name, receiver))
    
    # Check each skills send
    skills_sends = set()
    for msg in skills.sends:
        key = (msg.message_name, msg.counterparty)
        skills_sends.add(key)
        
        if key not in proto_sends:
            findings.append(StructuralFinding(
                check_name="OUTPUT_MATCH",
                severity=CheckSeverity.WARNING,
                role=role,
                description=f"Skills claims to send '{msg.message_name}' to {msg.counterparty}, "
                           f"but protocol does not define this message",
                expected="(not in protocol)",
                actual=msg.raw_line
            ))
    
    # Check protocol sends not in skills
    for msg_name, receiver in proto_sends:
        if (msg_name, receiver) not in skills_sends:
            findings.append(StructuralFinding(
                check_name="OUTPUT_MATCH",
                severity=CheckSeverity.WARNING,
                role=role,
                description=f"Protocol defines send '{msg_name}' to {receiver}, "
                           f"but skills file does not mention it",
                expected=f"{msg_name}(...) to {receiver}",
                actual="(not in skills)"
            ))
    
    return findings


def _check_receives(
    role: str,
    skills: ParsedSkills,
    type_sig: dict
) -> list[StructuralFinding]:
    """Messages in skills Receives should match protocol receives."""
    findings = []
    
    proto_receives = set()
    for msg_name, payload, sender in type_sig['receives']:
        proto_receives.add((msg_name, sender))
    
    skills_receives = set()
    for msg in skills.receives:
        key = (msg.message_name, msg.counterparty)
        skills_receives.add(key)
        
        if key not in proto_receives:
            findings.append(StructuralFinding(
                check_name="INPUT_MATCH",
                severity=CheckSeverity.WARNING,
                role=role,
                description=f"Skills claims to receive '{msg.message_name}' from {msg.counterparty}, "
                           f"but protocol does not define this message",
                expected="(not in protocol)",
                actual=msg.raw_line
            ))
    
    for msg_name, sender in proto_receives:
        if (msg_name, sender) not in skills_receives:
            findings.append(StructuralFinding(
                check_name="INPUT_MATCH",
                severity=CheckSeverity.WARNING,
                role=role,
                description=f"Protocol defines receive '{msg_name}' from {sender}, "
                           f"but skills file does not mention it",
                expected=f"{msg_name}(...) from {sender}",
                actual="(not in skills)"
            ))
    
    return findings


def _check_counterparties(
    role: str,
    skills: ParsedSkills,
    type_sig: dict
) -> list[StructuralFinding]:
    """
    Check that counterparties mentioned in skills are consistent.
    E.g., if skills says "send X to RoleA" but protocol says "send X to RoleB".
    """
    findings = []
    
    # Build protocol lookup: message_name -> expected counterparty for sends
    proto_send_targets = {}
    for msg_name, payload, receiver in type_sig['sends']:
        proto_send_targets.setdefault(msg_name, set()).add(receiver)
    
    proto_recv_sources = {}
    for msg_name, payload, sender in type_sig['receives']:
        proto_recv_sources.setdefault(msg_name, set()).add(sender)
    
    for msg in skills.sends:
        expected_targets = proto_send_targets.get(msg.message_name, set())
        if expected_targets and msg.counterparty not in expected_targets:
            findings.append(StructuralFinding(
                check_name="COUNTERPARTY",
                severity=CheckSeverity.ERROR,
                role=role,
                description=f"Skills sends '{msg.message_name}' to {msg.counterparty}, "
                           f"but protocol says it should go to {expected_targets}",
                expected=str(expected_targets),
                actual=msg.counterparty
            ))
    
    for msg in skills.receives:
        expected_sources = proto_recv_sources.get(msg.message_name, set())
        if expected_sources and msg.counterparty not in expected_sources:
            findings.append(StructuralFinding(
                check_name="COUNTERPARTY",
                severity=CheckSeverity.ERROR,
                role=role,
                description=f"Skills receives '{msg.message_name}' from {msg.counterparty}, "
                           f"but protocol says it comes from {expected_sources}",
                expected=str(expected_sources),
                actual=msg.counterparty
            ))
    
    return findings


def _check_branch_coverage(
    role: str,
    skills: ParsedSkills,
    protocol: ParsedProtocol
) -> list[StructuralFinding]:
    """
    If the protocol has 'choice at Role', the skills should describe all branches.
    """
    findings = []
    
    if role not in protocol.choice_roles:
        return findings
    
    # Count branches for this role
    role_branches = [b for b in protocol.branches if b.choice_role == role]
    branch_count = len(role_branches)
    
    if branch_count == 0:
        return findings
    
    # Check if skills has decision rules
    if not skills.decision_rules:
        findings.append(StructuralFinding(
            check_name="BRANCH_COVERAGE",
            severity=CheckSeverity.ERROR,
            role=role,
            description=f"Protocol has 'choice at {role}' with {branch_count} branches, "
                       f"but skills file has no Decision Rules section",
            expected=f"{branch_count} branch descriptions",
            actual="(no Decision Rules)"
        ))
    else:
        # Heuristic: count distinct conditions/paths in decision rules
        # Look for branch-distinguishing messages in the decision rules text
        described_branches = 0
        for branch in role_branches:
            if branch.first_message and branch.first_message.lower() in skills.decision_rules.lower():
                described_branches += 1
        
        if described_branches < branch_count:
            findings.append(StructuralFinding(
                check_name="BRANCH_COVERAGE",
                severity=CheckSeverity.WARNING,
                role=role,
                description=f"Protocol has {branch_count} branches for 'choice at {role}', "
                           f"but Decision Rules only references {described_branches} branch label(s)",
                expected=f"All branch labels mentioned: {[b.first_message for b in role_branches]}",
                actual=f"{described_branches} referenced"
            ))
    
    return findings


def _check_phantom_roles(
    protocol: ParsedProtocol,
    all_skills: dict[str, ParsedSkills]
) -> list[StructuralFinding]:
    """Skills files that don't correspond to any protocol role."""
    findings = []
    protocol_roles = set(protocol.roles)
    
    for role_name in all_skills:
        if role_name not in protocol_roles:
            findings.append(StructuralFinding(
                check_name="PHANTOM_ROLE",
                severity=CheckSeverity.WARNING,
                role=role_name,
                description=f"Skills file '{role_name}_skills.md' exists but role '{role_name}' "
                           f"is not in the protocol",
                expected="(not in protocol)",
                actual=f"{role_name}_skills.md"
            ))
    
    return findings


def format_structural_findings(findings: list[StructuralFinding]) -> str:
    """Format structural findings for display."""
    if not findings:
        return ""
    
    lines = []
    for f in findings:
        icon = "❌" if f.severity == CheckSeverity.ERROR else "⚠️"
        lines.append(f"    {icon} [{f.severity.value}] {f.check_name} — {f.role}")
        lines.append(f"       {f.description}")
        if f.expected:
            lines.append(f"       Expected: {f.expected}")
        if f.actual:
            lines.append(f"       Actual:   {f.actual}")
    
    return '\n'.join(lines)
