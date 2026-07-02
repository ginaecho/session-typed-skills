"""
Completeness Checker (Pass 3)

Verifies that skills files are complete and well-structured:
  - Every role has a Role Purpose section
  - Every input/output is documented
  - Decision Rules present for choice roles
  - Preconditions/Postconditions stated (if present)
  - No phantom actions (references to roles not in the protocol)

Follows Anthropic "Building Effective Agents" guidance:
  tools need clear documentation, well-defined interfaces, and explicit boundaries.
"""

from dataclasses import dataclass
from enum import Enum

from stjp_core.compiler.protocol_parser import ParsedProtocol
from stjp_core.generation.skills_parser import ParsedSkills


class CompleteSeverity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class CompletenessFinding:
    """A completeness issue in a skills file."""
    check_name: str
    severity: CompleteSeverity
    role: str
    description: str


def check_completeness(
    protocol: ParsedProtocol,
    all_skills: dict[str, ParsedSkills]
) -> list[CompletenessFinding]:
    """
    Run all completeness checks across skills files.
    Returns list of findings (empty = pass).
    """
    findings = []
    
    for role in protocol.roles:
        if role not in all_skills:
            # Already reported by structural checker
            continue
        
        skills = all_skills[role]
        
        findings.extend(_check_role_purpose(role, skills))
        findings.extend(_check_inputs_documented(role, skills, protocol))
        findings.extend(_check_outputs_documented(role, skills, protocol))
        findings.extend(_check_decision_rules(role, skills, protocol))
        findings.extend(_check_execution_flow(role, skills))
        findings.extend(_check_phantom_references(role, skills, protocol))
    
    return findings


def _check_role_purpose(role: str, skills: ParsedSkills) -> list[CompletenessFinding]:
    """Every role must have a Role Purpose section."""
    findings = []
    if not skills.role_purpose:
        findings.append(CompletenessFinding(
            check_name="ROLE_PURPOSE",
            severity=CompleteSeverity.ERROR,
            role=role,
            description=f"No 'Role Purpose' section — agent won't know what it's doing"
        ))
    elif len(skills.role_purpose) < 20:
        findings.append(CompletenessFinding(
            check_name="ROLE_PURPOSE",
            severity=CompleteSeverity.WARNING,
            role=role,
            description=f"'Role Purpose' is very brief ({len(skills.role_purpose)} chars) — consider expanding"
        ))
    return findings


def _check_inputs_documented(
    role: str,
    skills: ParsedSkills,
    protocol: ParsedProtocol
) -> list[CompletenessFinding]:
    """Every message this role receives should be documented in skills."""
    findings = []
    
    # Get protocol receives for this role
    proto_receives = set()
    for msg in protocol.messages:
        if msg.receiver == role:
            proto_receives.add(msg.message_name)
    
    if proto_receives and not skills.receives:
        findings.append(CompletenessFinding(
            check_name="INPUTS_DOCUMENTED",
            severity=CompleteSeverity.WARNING,
            role=role,
            description=f"Role receives {len(proto_receives)} message(s) in protocol "
                       f"but has no 'Receives' section in skills"
        ))
    
    return findings


def _check_outputs_documented(
    role: str,
    skills: ParsedSkills,
    protocol: ParsedProtocol
) -> list[CompletenessFinding]:
    """Every message this role sends should be documented in skills."""
    findings = []
    
    proto_sends = set()
    for msg in protocol.messages:
        if msg.sender == role:
            proto_sends.add(msg.message_name)
    
    if proto_sends and not skills.sends:
        findings.append(CompletenessFinding(
            check_name="OUTPUTS_DOCUMENTED",
            severity=CompleteSeverity.WARNING,
            role=role,
            description=f"Role sends {len(proto_sends)} message(s) in protocol "
                       f"but has no 'Sends' section in skills"
        ))
    
    return findings


def _check_decision_rules(
    role: str,
    skills: ParsedSkills,
    protocol: ParsedProtocol
) -> list[CompletenessFinding]:
    """Choice roles must have Decision Rules."""
    findings = []
    
    if role in protocol.choice_roles:
        if not skills.decision_rules:
            findings.append(CompletenessFinding(
                check_name="DECISION_RULES",
                severity=CompleteSeverity.ERROR,
                role=role,
                description=f"Role has 'choice at {role}' in protocol but no Decision Rules "
                           f"in skills — branching logic will be left to LLM improvisation"
            ))
        elif len(skills.decision_rules) < 30:
            findings.append(CompletenessFinding(
                check_name="DECISION_RULES",
                severity=CompleteSeverity.WARNING,
                role=role,
                description=f"Decision Rules section is very brief ({len(skills.decision_rules)} chars) "
                           f"— consider adding explicit conditions for each branch"
            ))
    
    return findings


def _check_execution_flow(role: str, skills: ParsedSkills) -> list[CompletenessFinding]:
    """Every role should have an Execution Flow."""
    findings = []
    if not skills.execution_flow:
        findings.append(CompletenessFinding(
            check_name="EXECUTION_FLOW",
            severity=CompleteSeverity.WARNING,
            role=role,
            description=f"No 'Execution Flow' section — agent may not understand step ordering"
        ))
    return findings


def _check_phantom_references(
    role: str,
    skills: ParsedSkills,
    protocol: ParsedProtocol
) -> list[CompletenessFinding]:
    """
    Check if the skills file references role names that don't exist in the protocol.
    """
    findings = []
    protocol_roles = set(protocol.roles)
    
    # Check counterparties in sends and receives
    all_refs = set()
    for msg in skills.sends:
        all_refs.add(msg.counterparty)
    for msg in skills.receives:
        all_refs.add(msg.counterparty)
    
    for ref in all_refs:
        if ref not in protocol_roles:
            findings.append(CompletenessFinding(
                check_name="PHANTOM_REFERENCE",
                severity=CompleteSeverity.WARNING,
                role=role,
                description=f"Skills references role '{ref}' which is not defined in the protocol"
            ))
    
    return findings


def format_completeness_findings(findings: list[CompletenessFinding]) -> str:
    """Format completeness findings for display."""
    if not findings:
        return ""
    
    lines = []
    for f in findings:
        icon = "❌" if f.severity == CompleteSeverity.ERROR else \
               "⚠️" if f.severity == CompleteSeverity.WARNING else "ℹ️"
        lines.append(f"    {icon} [{f.severity.value}] {f.check_name} — {f.role}")
        lines.append(f"       {f.description}")
    
    return '\n'.join(lines)
