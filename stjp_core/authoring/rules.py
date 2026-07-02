"""
Scribble Protocol Rules Knowledge Base

Contains all known Scribble rules for protocol validation and correction.
"""


class ScribbleRules:
    """
    Knowledge base of Scribble protocol rules.
    The agent uses this to generate correct protocols and learn from errors.
    """
    
    RULES = {
        # Syntax Rules
        "MODULE_NAME": {
            "description": "Module name MUST match the filename (without .scr extension)",
            "error_patterns": ["mismatch", "module name"],
            "fix": "Use 'module <Filename>;' where Filename matches the .scr file"
        },
        "ROLE_DECLARATION": {
            "description": "All roles must be declared in the protocol header",
            "error_patterns": ["not bound", "Role not bound"],
            "fix": "Add 'role <RoleName>' to the protocol declaration for each role used"
        },
        "MESSAGE_SYNTAX": {
            "description": "Messages use: Label() from Sender to Receiver;",
            "error_patterns": ["syntax error", "expecting"],
            "fix": "Format: MessageLabel() from RoleA to RoleB;"
        },
        
        # Semantic Rules
        "EXTERNAL_CHOICE": {
            "description": "In 'choice at X', all external roles must receive their FIRST message from the SAME sender in ALL branches",
            "error_patterns": ["external choice", "Inconsistent external choice"],
            "fix": "The chooser must send a first notification message to ALL roles inside the choice block in EVERY branch. Every role that sends or receives inside a choice must get its first message from the same sender across all branches."
        },
        "SOURCE_NOT_ENABLED": {
            "description": "A role inside a choice block cannot send until it has been notified of the branch",
            "error_patterns": ["Source role not enabled", "not enabled"],
            "fix": "The chooser must send a notification to this role inside the choice block before it can send."
        },
        "PAYLOAD_TYPE": {
            "description": "Payload types (Double, String, Int, etc.) must be declared with 'data' before use",
            "error_patterns": ["Cannot disambiguate name", "disambiguate"],
            "fix": "Add type declaration: data <java> \"java.lang.Double\" from \"rt.jar\" as Double; (after module line, before protocol)"
        },
        "ROLE_NOT_USED": {
            "description": "All declared roles should participate in the protocol",
            "error_patterns": ["not used", "unused role"],
            "fix": "Either remove the role from declaration or add messages for it"
        },
        "DEADLOCK": {
            "description": "Protocol must not have circular waits or deadlocks",
            "error_patterns": ["deadlock", "circular"],
            "fix": "Ensure message flow has clear ordering without circular dependencies"
        },
        
        # Best Practices
        "UNIQUE_MESSAGES": {
            "description": "Message labels should be unique and descriptive",
            "error_patterns": [],
            "fix": "Use descriptive labels like 'RevReport' instead of generic 'Data'"
        },
        "COMPLETE_PATHS": {
            "description": "All branches in a choice should have complete message flows",
            "error_patterns": ["incomplete", "missing"],
            "fix": "Ensure each branch handles all necessary communications"
        }
    }
    
    CHOICE_RULES = """
    SCRIBBLE CHOICE RULES:
    1. 'choice at X' means role X makes a decision
    2. X sends DIFFERENT message labels in each branch to indicate the choice
    3. ANY role that receives a message in the choice block MUST receive their
       FIRST message from the SAME sender in ALL branches (external choice rule)
    4. After the first message, subsequent messages can come from different senders
    
    VALID PATTERN:
    choice at Decider {
        PathA() from Decider to RoleY;  // RoleY's first msg from Decider
        PathA() from Decider to RoleZ;  // RoleZ's first msg from Decider
        ... // subsequent messages can be from anyone
    } or {
        PathB() from Decider to RoleY;  // RoleY's first msg from Decider (same sender!)
        PathB() from Decider to RoleZ;  // RoleZ's first msg from Decider (same sender!)
        ...
    }
    
    INVALID PATTERN:
    choice at Decider {
        Msg() from Decider to RoleY;
        Data() from RoleY to RoleZ;     // RoleZ's first msg from RoleY
    } or {
        Msg() from Decider to RoleZ;    // RoleZ's first msg from Decider - DIFFERENT SENDER!
    }
    """
    
    @classmethod
    def get_rule_for_error(cls, error_message: str) -> dict:
        """Find the matching rule for a Scribble error"""
        error_lower = error_message.lower()
        for rule_name, rule in cls.RULES.items():
            for pattern in rule["error_patterns"]:
                if pattern.lower() in error_lower:
                    return {"name": rule_name, **rule}
        return None
    
    @classmethod
    def get_all_rules_summary(cls) -> str:
        """Get a summary of all rules for the agent"""
        lines = ["SCRIBBLE PROTOCOL RULES:"]
        for name, rule in cls.RULES.items():
            lines.append(f"  [{name}]: {rule['description']}")
        return "\n".join(lines)
