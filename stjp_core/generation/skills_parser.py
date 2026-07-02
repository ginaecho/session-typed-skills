"""
Skills Parser

Parses skills.md files into a structured representation for type-checking.
Extracts sections: Role Purpose, Receives, Sends, Decision Rules,
Execution Flow, Business Rules, Preconditions, Postconditions.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class MessageRef:
    """A message reference extracted from a skills file."""
    message_name: str       # e.g. "HighRevenue"
    payload_type: str       # e.g. "Double", "String", or ""
    counterparty: str       # the other role
    direction: str          # "send" or "receive"
    raw_line: str           # original line for error reporting


@dataclass
class ParsedSkills:
    """Structured representation of a skills.md file."""
    role_name: str
    protocol_ref: str = ""                       # e.g. "P2_v2.scr"
    role_purpose: str = ""
    receives: list[MessageRef] = field(default_factory=list)
    sends: list[MessageRef] = field(default_factory=list)
    decision_rules: str = ""
    execution_flow: str = ""
    business_rules: str = ""
    preconditions: str = ""
    postconditions: str = ""
    raw_sections: dict = field(default_factory=dict)  # section_name -> text


# Regex for lines like: `HighRevenue(Double)` from/to Fetcher - description
_MSG_PATTERN = re.compile(
    r'`?(\w+)\(([^)]*)\)`?\s+(from|to)\s+(\w+)', re.IGNORECASE
)


def parse_skills_file(path: Path) -> ParsedSkills:
    """
    Parse a single skills.md file into a ParsedSkills structure.
    
    Expected sections (case-insensitive heading match):
      ## Role Purpose
      ## Receives
      ## Sends
      ## Decision Rules
      ## Execution Flow
      ## Business Rules
      ## Preconditions
      ## Postconditions
    """
    text = path.read_text(encoding='utf-8')
    role_name = path.stem.replace('_skills', '')
    
    result = ParsedSkills(role_name=role_name)
    
    # Extract protocol reference (line like **Protocol**: `P2_v2.scr`)
    proto_match = re.search(r'\*\*Protocol\*\*:\s*`([^`]+)`', text)
    if proto_match:
        result.protocol_ref = proto_match.group(1)
    
    # Split into sections by ## headings
    sections = _split_sections(text)
    result.raw_sections = sections
    
    # Map sections to fields
    for section_name, section_text in sections.items():
        key = section_name.lower().strip()
        
        if 'role purpose' in key:
            result.role_purpose = section_text.strip()
        elif key == 'receives':
            result.receives = _parse_messages(section_text, direction='receive')
        elif key == 'sends':
            result.sends = _parse_messages(section_text, direction='send')
        elif 'decision rule' in key:
            result.decision_rules = section_text.strip()
        elif 'execution flow' in key:
            result.execution_flow = section_text.strip()
        elif 'business rule' in key:
            result.business_rules = section_text.strip()
        elif 'precondition' in key:
            result.preconditions = section_text.strip()
        elif 'postcondition' in key:
            result.postconditions = section_text.strip()
    
    return result


def parse_all_skills(skills_dir: Path) -> dict[str, ParsedSkills]:
    """
    Parse all *_skills.md files in a directory.
    Returns a dict mapping role_name -> ParsedSkills.
    """
    results = {}
    for path in sorted(skills_dir.glob('*_skills.md')):
        parsed = parse_skills_file(path)
        results[parsed.role_name] = parsed
    return results


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown text by ## headings. Returns heading -> body."""
    sections = {}
    current_heading = None
    current_lines = []
    
    for line in text.splitlines():
        heading_match = re.match(r'^##\s+(.+)', line)
        if heading_match:
            # Save previous section
            if current_heading is not None:
                sections[current_heading] = '\n'.join(current_lines)
            current_heading = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    
    # Save last section
    if current_heading is not None:
        sections[current_heading] = '\n'.join(current_lines)
    
    return sections


def _parse_messages(text: str, direction: str) -> list[MessageRef]:
    """
    Parse message lines from Receives or Sends sections.
    
    Handles formats like:
      - `HighRevenue(Double)` from Fetcher - description
      - `AuditRevenue(Double)` to TaxVerifier
      - None
    """
    messages = []
    
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower() in ('- none', 'none', '- n/a'):
            continue
        
        match = _MSG_PATTERN.search(line)
        if match:
            msg_name = match.group(1)
            payload = match.group(2).strip()
            # direction_word = match.group(3)  # "from" or "to"
            counterparty = match.group(4)
            
            messages.append(MessageRef(
                message_name=msg_name,
                payload_type=payload,
                counterparty=counterparty,
                direction=direction,
                raw_line=line
            ))
    
    return messages
