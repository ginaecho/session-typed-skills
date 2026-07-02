"""
Protocol Parser

Extracts structured type signatures from Scribble .scr protocol files:
- Roles
- Messages (name, payload, sender, receiver)
- Branches (choice at Role, with branch labels)

This gives the Skills Compiler the "type declarations" to check skills against.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ProtocolMessage:
    """A single message in the protocol."""
    message_name: str     # e.g. "HighRevenue"
    payload_type: str     # e.g. "Double", "String", or ""
    sender: str           # role name
    receiver: str         # role name
    branch_context: str   # which branch this belongs to ("" if top-level)


@dataclass
class ProtocolBranch:
    """A branch within a choice block."""
    choice_role: str                              # the role making the choice
    branch_index: int                             # 0, 1, ...
    first_message: str = ""                       # label distinguishing this branch
    messages: list[ProtocolMessage] = field(default_factory=list)


@dataclass
class ParsedProtocol:
    """Structured representation of a Scribble protocol."""
    module_name: str
    protocol_name: str
    roles: list[str]
    messages: list[ProtocolMessage]               # all messages (flat)
    branches: list[ProtocolBranch]                # choice blocks
    choice_roles: list[str]                       # roles with 'choice at'
    raw_content: str


def parse_protocol_file(path: Path) -> ParsedProtocol:
    """Parse a .scr file into a ParsedProtocol."""
    content = path.read_text(encoding='utf-8')
    return parse_protocol(content)


def parse_protocol(content: str) -> ParsedProtocol:
    """Parse protocol text into a ParsedProtocol."""
    module_name = _extract_module(content)
    protocol_name = _extract_protocol_name(content)
    roles = _extract_roles(content)
    messages = _extract_all_messages(content)
    branches = _extract_branches(content)
    choice_roles = _extract_choice_roles(content)
    
    return ParsedProtocol(
        module_name=module_name,
        protocol_name=protocol_name,
        roles=roles,
        messages=messages,
        branches=branches,
        choice_roles=choice_roles,
        raw_content=content
    )


def get_role_type_signature(protocol: ParsedProtocol, role: str) -> dict:
    """
    Get the "type signature" for a role: what it sends, what it receives,
    and whether it has decision logic (choice at).
    
    Returns dict with:
        sends: list of (message_name, payload_type, receiver)
        receives: list of (message_name, payload_type, sender)
        has_choice: bool
        branch_count: int (number of branches if choice role)
    """
    sends = []
    receives = []
    
    for msg in protocol.messages:
        if msg.sender == role:
            sends.append((msg.message_name, msg.payload_type, msg.receiver))
        if msg.receiver == role:
            receives.append((msg.message_name, msg.payload_type, msg.sender))
    
    has_choice = role in protocol.choice_roles
    branch_count = 0
    if has_choice:
        branch_count = sum(1 for b in protocol.branches if b.choice_role == role)
    
    return {
        'sends': sends,
        'receives': receives,
        'has_choice': has_choice,
        'branch_count': branch_count,
    }


# --- Internal parsing helpers ---

def _extract_module(content: str) -> str:
    match = re.search(r'module\s+(\w+)\s*;', content)
    return match.group(1) if match else ""


def _extract_protocol_name(content: str) -> str:
    match = re.search(r'global protocol\s+(\w+)\s*\(', content)
    return match.group(1) if match else ""


def _extract_roles(content: str) -> list[str]:
    match = re.search(r'global protocol\s+\w+\s*\((.*?)\)', content, re.DOTALL)
    if match:
        return re.findall(r'role\s+(\w+)', match.group(1))
    return []


def _extract_choice_roles(content: str) -> list[str]:
    return re.findall(r'choice\s+at\s+(\w+)', content)


def _extract_all_messages(content: str) -> list[ProtocolMessage]:
    """Extract all messages from the protocol, including those inside branches."""
    messages = []
    # Pattern: MessageName(Type) from Sender to Receiver;
    pattern = re.compile(
        r'(\w+)\(([^)]*)\)\s+from\s+(\w+)\s+to\s+(\w+)\s*;'
    )
    
    for match in pattern.finditer(content):
        messages.append(ProtocolMessage(
            message_name=match.group(1),
            payload_type=match.group(2).strip(),
            sender=match.group(3),
            receiver=match.group(4),
            branch_context=""  # simplified; full branch tracking below
        ))
    
    return messages


def _extract_branches(content: str) -> list[ProtocolBranch]:
    """
    Extract choice blocks and their branches.
    Handles: choice at Role { ... } or { ... }
    """
    branches = []
    
    # Find all 'choice at Role' blocks
    choice_pattern = re.compile(r'choice\s+at\s+(\w+)\s*\{', re.DOTALL)
    
    for choice_match in choice_pattern.finditer(content):
        choice_role = choice_match.group(1)
        start = choice_match.end()
        
        # Find the matching branches by tracking brace depth
        branch_texts = _split_or_branches(content, start)
        
        for i, branch_text in enumerate(branch_texts):
            # Extract messages in this branch
            msg_pattern = re.compile(
                r'(\w+)\(([^)]*)\)\s+from\s+(\w+)\s+to\s+(\w+)\s*;'
            )
            branch_msgs = []
            first_msg = ""
            
            for j, msg_match in enumerate(msg_pattern.finditer(branch_text)):
                msg = ProtocolMessage(
                    message_name=msg_match.group(1),
                    payload_type=msg_match.group(2).strip(),
                    sender=msg_match.group(3),
                    receiver=msg_match.group(4),
                    branch_context=f"branch_{i}"
                )
                branch_msgs.append(msg)
                if j == 0:
                    first_msg = msg.message_name
            
            branches.append(ProtocolBranch(
                choice_role=choice_role,
                branch_index=i,
                first_message=first_msg,
                messages=branch_msgs
            ))
    
    return branches


def _split_or_branches(content: str, start: int) -> list[str]:
    """
    Starting after the opening { of a choice block, split on top-level '} or {'
    Returns list of branch text bodies.
    """
    branches = []
    depth = 1
    current_start = start
    pos = start
    
    while pos < len(content) and depth > 0:
        ch = content[pos]
        
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                # End of choice block
                branches.append(content[current_start:pos])
                break
            # Check if this is '} or {'
            rest = content[pos:].lstrip()
            if depth == 1 and rest.startswith('} or {'):
                # This is a branch separator at our level — but we need to be
                # at depth 1 before the }, so actually:
                pass
        
        # Detect "} or {" at depth == 1
        if ch == '}' and depth == 0:
            # Already handled above
            pass
        
        pos += 1
    
    # Simpler approach: split on "} or {" within the choice body
    if not branches:
        # Re-extract the full choice body
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1
        choice_body = content[start:pos - 1]
        branches = re.split(r'\}\s*or\s*\{', choice_body)
    
    return branches
