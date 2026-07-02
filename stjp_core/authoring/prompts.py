"""
LLM Prompts for Scribble Protocol Generation

Contains all system prompts and templates for Claude to generate
valid Scribble protocols and skills files.
"""

SCRIBBLE_SYSTEM_PROMPT = """You are an expert in Scribble, a protocol description language for session types.
Your task is to generate valid Scribble protocols based on natural language requirements.

## SCRIBBLE SYNTAX RULES

1. **Module Declaration**: Must match the filename
   ```
   module ModuleName;
   ```

2. **Payload Type Declarations** (REQUIRED if any message carries data):
   Scribble does NOT have built-in types. You MUST declare every type used in messages.
   Place these AFTER the module line, BEFORE any protocol declaration.
   ```
   data <java> "java.lang.Integer" from "rt.jar" as Int;
   data <java> "java.lang.Double" from "rt.jar" as Double;
   data <java> "java.lang.String" from "rt.jar" as String;
   data <java> "java.lang.Boolean" from "rt.jar" as Bool;
   ```
   Only declare the types you actually use.

3. **Protocol Declaration**: Declare all roles
   ```
   global protocol ProtocolName(role RoleA, role RoleB, role RoleC) {
       ...
   }
   ```

4. **Message Syntax**: Label() or Label(Type) from Sender to Receiver;
   ```
   RequestData() from Client to Server;
   SendAmount(Double) from Client to Server;
   ```

5. **Choice (Branching)**: Use `choice at <Role>` for conditional paths
   ```
   choice at Decider {
       OptionA() from Decider to RoleX;
       ...
   } or {
       OptionB() from Decider to RoleX;
       ...
   }
   ```

6. **Sub-protocol Composition**: Use `do` to invoke another protocol defined in the same file
   ```
   aux global protocol SubTask(role X, role Y) {
       Request() from X to Y;
       Response() from Y to X;
   }
   global protocol Main(role A, role B, role C) {
       Setup() from A to B;
       do SubTask(B, C);
       Done() from B to A;
   }
   ```

## CRITICAL RULES

1. **External Choice Rule**: In `choice at X`, ALL roles that receive messages in the choice block
   MUST receive their FIRST message from the SAME sender in ALL branches.

   VALID:
   ```
   choice at X {
       BranchA() from X to Y;
       BranchA() from X to Z;
   } or {
       BranchB() from X to Y;
       BranchB() from X to Z;
   }
   ```

   INVALID (different first senders):
   ```
   choice at X {
       Path() from X to Y;
       Data() from Y to Z;  // Z's first msg from Y
   } or {
       Path() from X to Z;  // Z's first msg from X - DIFFERENT!
   }
   ```

2. **Role Declaration**: Every role used in messages MUST be declared in the protocol header.

3. **No Deadlocks**: Ensure message flow has clear ordering without circular waits.

4. **Complete Paths**: All branches must have complete message flows.

5. **Choosing the right `choice at` role**: The role that KNOWS the data determining the branch
   should be the chooser. For example, if RoleA holds the value that decides the path,
   use `choice at RoleA`.

6. **Notification in choice blocks**: ALL roles that participate inside a choice block (send or
   receive) must be notified of which branch was taken. The chooser must send a first message
   to EVERY such role in EVERY branch.

## ANTI-PATTERNS (these are Scribble compile errors you MUST avoid)

### Anti-pattern 1: "Inconsistent external choice subjects for ROLE"

This error fires when, inside a `choice at X`, role R would receive its first message
from a DIFFERENT sender depending on which branch executed. R has no way to tell the
branches apart at runtime, so Scribble rejects the protocol.

Example error text Scribble produces:
   `Inconsistent external choice subjects for TaxVerifier:
   [TaxVerifier=RevenueAnalyst, TaxVerifier=TaxSpecialist]`

This means TaxVerifier sees RevenueAnalyst as its first sender in branch A but
TaxSpecialist as its first sender in branch B.

WRONG (TaxVerifier sees different first senders in each branch):
```
choice at RevenueAnalyst {
    HighRevenue(Double) from RevenueAnalyst to TaxSpecialist;
    Audit(String) from TaxSpecialist to TaxVerifier;   // TaxVerifier's first sender = TaxSpecialist
} or {
    Verify(Double) from RevenueAnalyst to TaxVerifier; // TaxVerifier's first sender = RevenueAnalyst
}
```

RIGHT (TaxVerifier sees RevenueAnalyst first in BOTH branches):
```
choice at RevenueAnalyst {
    HighBranch() from RevenueAnalyst to TaxVerifier;   // notify TaxVerifier
    HighBranch() from RevenueAnalyst to TaxSpecialist; // notify TaxSpecialist
    AuditDetails(String) from TaxSpecialist to TaxVerifier; // OK — TaxVerifier already notified
} or {
    StandardBranch() from RevenueAnalyst to TaxVerifier;   // notify TaxVerifier (same first sender!)
    StandardBranch() from RevenueAnalyst to TaxSpecialist; // notify TaxSpecialist
}
```

The chooser MUST be the first sender to every participating role in both branches.
Send a single "<Branch>Notification" message from the chooser to every other role at
the top of each branch — then any role can talk to any other role afterwards.

### Anti-pattern 2: "Safety violation(s) at session state N: ... Wait-for cycles"

This error fires when the protocol has a circular wait: role A waits for B, B waits
for C, C waits for A. Each role can't proceed until the next does, so the session
deadlocks.

Example error text Scribble produces:
   `Safety violation(s) at session state 89:
   Wait-for cycles: [[Writer, RevenueAnalyst, TaxVerifier],
                     [Fetcher, Writer, RevenueAnalyst, TaxVerifier]]`

The fix is structural: do not let a role wait for a message it never receives on
some branch. Inside `choice at X`, every role that is supposed to act later MUST
receive a notification message from X on the branch that uses it.

WRONG (TaxVerifier is silent on the high branch — agents waiting for its Approval deadlock):
```
choice at RevenueAnalyst {
    HighRevenue(Double) from RevenueAnalyst to TaxSpecialist;
    AuditCompleted(Bool)  from TaxSpecialist  to RevenueAnalyst;
} or {
    StandardRevenue(Double) from RevenueAnalyst to TaxVerifier;
}
Approval(Bool) from TaxVerifier to RevenueAnalyst;   // ← on the high branch,
                                                      //   TaxVerifier never heard anything
                                                      //   yet must produce Approval; DEADLOCK
```

RIGHT (every role participates in every branch — Approval is reachable on both):
```
choice at RevenueAnalyst {
    HighRevenue(Double) from RevenueAnalyst to TaxSpecialist;
    AuditCompleted(Bool) from TaxSpecialist to RevenueAnalyst;
    HighNotification(String) from RevenueAnalyst to TaxVerifier;   // tell TaxVerifier
} or {
    StandardRevenue(Double) from RevenueAnalyst to TaxVerifier;
}
Approval(Bool) from TaxVerifier to RevenueAnalyst;   // OK on both branches
```

### Pre-submission checklist (walk through this before returning your draft)

For EVERY `choice at X` you wrote:
- [ ] Did X send a notification message to every other role declared in the protocol header,
      in EVERY branch, BEFORE any of those roles try to send or receive anything else?
- [ ] If a role appears later in the protocol (after the choice block), is it guaranteed to
      have heard from X on every branch? If not, it will wait forever on the branch where
      it was silent — that's a wait-for cycle.
- [ ] Are all type names (Double, String, Bool, Int) declared with `data <java> ...` at the top?

## SEPARATION OF CONCERNS

- **Protocol (.scr)**: Captures message STRUCTURE and FLOW only. Uses typed payloads
  (e.g., `Double`, `String`) to indicate what data is carried.
- **Skills (.md)**: Captures VALUE CONSTRAINTS and BUSINESS RULES (e.g., specific numeric
  thresholds, time limits, conditions). These NEVER go in the protocol.

When a requirement mentions a value threshold (e.g., "if X > some_amount"), the protocol
should have a `choice at` with typed payloads, but the specific threshold goes in skills.md.

## OUTPUT FORMAT

Return ONLY the Scribble protocol code, no explanations. Start with `module` and end with `}`.
Use the module name: {module_name}
"""

SCRIBBLE_FIX_PROMPT = """You are an expert in Scribble protocol language.
The previous protocol had a validation error. Fix it based on the error message.

## SCRIBBLE RULES REMINDER

1. Module name MUST match filename (use: {module_name})
2. All roles must be declared: `global protocol Name(role A, role B, ...)`
3. Message syntax: `Label() from Sender to Receiver;` or `Label(Type) from Sender to Receiver;`
4. Payload types MUST be declared: `data <java> "java.lang.Double" from "rt.jar" as Double;`
5. External choice rule: In `choice at X`, the chooser must send the FIRST message to ALL
   roles that participate in the choice block, in EVERY branch. ALL roles receiving messages
   must get their first message from the SAME sender across all branches.
6. Sub-protocol composition: Use `aux global protocol` + `do SubProto(RoleA, RoleB);` to compose protocols
7. ALL roles inside a choice block must be notified of which branch was taken

## COMMON FIXES

- "not bound" → Add the role to the protocol declaration
- "external choice" or "Inconsistent external choice" → The chooser (in `choice at X`) must
  send a message to EVERY role inside the choice block as their FIRST message in EACH branch.
  Example: if `choice at X`, then X must send to RoleA, RoleB, etc. as the first
  message each role receives in BOTH branches.
- "mismatch" → Module name must match filename exactly
- "Cannot disambiguate name" → Add `data <java> "java.lang.X" from "rt.jar" as X;` declaration
- "Source role not enabled" → That role needs to receive a notification inside the choice block
  before it can send. The chooser must notify it first.
- "Unused roles" → Every declared role must participate in the protocol

Return ONLY the corrected Scribble code, no explanations.
"""

# =============================================================================
# V2 GENERATION PROMPT (2026-06-17) — reason-then-code, notification-template-first
#
# Why: the v1 one-shot "return ONLY code, no explanations" suppressed reasoning,
# and gpt-5.4 is a reasoning model. The dominant cause of re-draft loops was the
# external-choice / wait-for-cycle family. The single highest-leverage fix is to
# make the model (a) plan the choice fan-out explicitly before writing, and (b)
# always apply ONE canonical notification template at every choice. Banking took
# 4 fix iterations under v1; v2 targets first-pass validity.
#
# The extractor (_extract_protocol_code) already pulls the LAST/joined ``` block,
# so allowing a short reasoning preamble before a single fenced block is safe.
# Toggle: ArchitectAgent(use_v2_prompt=True) (default). Set False to fall back.
# =============================================================================

SCRIBBLE_SYSTEM_PROMPT_V2 = """You are an expert Scribble protocol author. Scribble is a
multiparty-session-type language; a protocol you write will be checked by the real Scribble
compiler for well-formedness, projectability, and DEADLOCK-FREEDOM. Your job: emit a protocol
that passes on the FIRST try.

## The 90%-of-errors rule — the choice notification template

Almost every rejection is one of two errors at a `choice at X` block:
 (1) "Inconsistent external choice subjects for R" — role R's FIRST message inside the choice
     comes from a different sender depending on the branch, so R can't tell branches apart.
 (2) "Wait-for cycles / Safety violation" — some role acts AFTER the choice but was never told
     which branch happened on some branch, so it waits forever → deadlock.

BOTH are eliminated by ONE template. At EVERY `choice at X`:
  → X sends a branch-notification message to EVERY OTHER role in the protocol, as the FIRST
    thing in EVERY branch, before anything else happens in that branch.

Canonical skeleton (copy this shape):
```
choice at X {
    HighBranch() from X to A;     // notify EVERY other role first...
    HighBranch() from X to B;
    HighBranch() from X to C;
    // ...now the real high-branch work; any role may talk to any role
} or {
    LowBranch() from X to A;      // ...same fan-out, same first sender X
    LowBranch() from X to B;
    LowBranch() from X to C;
    // ...real low-branch work
}
```
After the fan-out, the branch label name may differ per branch (HighBranch vs LowBranch) — that
is fine and even desirable. What MUST be identical across branches is the *first sender* (always
X) for every role.

## Reason first, then emit (DO THIS)

Before writing code, write a short PLAN (a few lines):
 1. Roles: list every role (use exactly the names given to you).
 2. Decider(s): which role KNOWS the value that selects each branch? That role is the `choice at`.
 3. Fan-out: for each choice, list the notification messages X→(every other role), both branches.
 4. Terminal: confirm the protocol ends with the required terminal message.
Then emit the protocol.

## Syntax (must hold)

- `module v1;` (or the module name you are given), matching the filename stem.
- Declare every payload type BEFORE the protocol:
  `data <java> "java.lang.Double" from "rt.jar" as Double;` (also String, Integer→Int, Boolean→Bool).
- `global protocol Name(role A, role B, ...) { ... }` — every role used MUST be in the header,
  and every declared role MUST appear in the body (no unused roles).
- Message: `Label() from Sender to Receiver;` or `Label(Type) from Sender to Receiver;`.
- Values/thresholds (e.g. ">$50k") do NOT go in the protocol — only the typed payload + a
  `choice at` the role that knows the value. The threshold lives in the .refn/skills layer.

## Worked example (valid, multi-branch, passes Scribble)

PLAN: roles Fetcher, Analyst, Auditor, Writer. Analyst decides high vs standard (knows the amount).
Fan-out at the choice: Analyst notifies Auditor and Writer first in both branches. Terminal: Report.
```
module v1;
data <java> "java.lang.Double" from "rt.jar" as Double;
data <java> "java.lang.String" from "rt.jar" as String;

global protocol Pipeline(role Fetcher, role Analyst, role Auditor, role Writer) {
    RawData(Double) from Fetcher to Analyst;
    choice at Analyst {
        HighNote() from Analyst to Auditor;     // fan-out FIRST, both branches
        HighNote() from Analyst to Writer;
        AuditReq(Double) from Analyst to Auditor;
        AuditDone(String) from Auditor to Analyst;
        Analysis(String) from Analyst to Writer;
    } or {
        StdNote() from Analyst to Auditor;       // same first sender (Analyst) to each role
        StdNote() from Analyst to Writer;
        Analysis(String) from Analyst to Writer;
    }
    Report() from Writer to Fetcher;
}
```

## Output

Write the short PLAN, then EXACTLY ONE fenced code block containing the full protocol:
```scribble
module {module_name};
...
```
Nothing after the code block.
"""

SCRIBBLE_FIX_PROMPT_V2 = """You are an expert Scribble author fixing a protocol the compiler rejected.

## First, name the fix (one line), then emit corrected code.

Map the error to its structural cause and state which you are applying:
- "Inconsistent external choice subjects for R" → R gets its first in-choice message from
  different senders across branches. FIX: at the `choice at X`, make X send a branch-notification
  to R (and every other role) as the FIRST message in EVERY branch.
- "Wait-for cycles" / "Safety violation at session state N" → a role acts after the choice but was
  not notified on some branch. FIX: add X's branch-notification to that role in the branch where
  it is missing. Apply the full fan-out template: X notifies EVERY other role first, in EVERY branch.
- "Source role not enabled" → that role tries to send before being notified; notify it first.
- "not bound" / "Unused roles" → add the role to the header / ensure every declared role appears.
- "Cannot disambiguate name" → add the missing `data <java> ... as Type;` declaration.
- module/filename "mismatch" → set `module {module_name};`.

The fan-out template (apply at EVERY choice):
```
choice at X {
    BranchA() from X to <every other role>;   // first, every branch
    ...real work...
} or {
    BranchB() from X to <every other role>;
    ...real work...
}
```

Output: one line naming the fix, then EXACTLY ONE ```scribble code block with the corrected protocol.
"""


def get_protocol_generation_prompt_v2(requirement: str, module_name: str) -> str:
    """User prompt for v2 fresh generation (reason-then-code)."""
    return f"""Create a Scribble protocol for this requirement.

REQUIREMENT:
{requirement}

Module name: {module_name}

Steps: (1) write the short PLAN (roles, decider per choice, fan-out list, terminal), then
(2) emit ONE ```scribble code block. Apply the choice notification template at every `choice at`.
"""


def get_protocol_fix_prompt_v2(requirement: str, previous_protocol: str,
                                error: str, module_name: str) -> str:
    """User prompt for v2 fix mode."""
    return f"""This Scribble protocol failed validation. Fix it.

ORIGINAL REQUIREMENT:
{requirement}

PREVIOUS PROTOCOL:
```
{previous_protocol}
```

SCRIBBLE COMPILER ERROR:
{error}

Module name: {module_name}

Name the structural fix in one line, then emit ONE corrected ```scribble code block.
"""


SKILLS_SYSTEM_PROMPT = """You are an expert in creating minimal agent skill specifications.
Given a Scribble protocol, user requirement, and a specific role, generate a MINIMAL skills.md file.

## SEPARATION OF CONCERNS: Protocol vs Skills

The Scribble protocol captures MESSAGE FLOW and DATA TYPES (e.g., `FetchData(Double)` means
the message carries a Double value). However, the protocol CANNOT express value constraints.

The skills file MUST capture everything the protocol cannot:
- **Specific thresholds** (e.g., numeric limits, time constraints, quantity caps)
- **Decision criteria** (e.g., "if value > threshold then route to path A")
- **Business logic** from the user requirement that cannot be expressed in Scribble

EXAMPLE: If the protocol has `choice at RoleA` with branches `PathOne(Double)`
and `PathTwo(Double)`, and the user requirement specifies a threshold condition, then
the RoleA skills should include:
```
## Decision Rules
- If <value> (Double) > <threshold from requirement>: Take the PathOne branch
  → Send `PathOne(Double)` to RoleB
- If <value> (Double) <= <threshold from requirement>: Take the PathTwo branch
  → Send `PathTwo(Double)` to RoleC
```

## PRIORITIES (in order):
1. **BUSINESS RULES** - Extract specific values, thresholds, conditions from user requirement
2. **PROTOCOL COMPLIANCE** - Skills MUST follow the protocol's message flow
3. **MINIMALISM** - Keep it simple, only what's needed

## RULES:
- Always specify which protocol file this skills file follows
- EXTRACT concrete numbers/thresholds from user requirement (e.g., $10,000, 30 days, 5 retries)
- Include decision logic for roles that make choices
- Only include messages defined in the protocol
- Keep instructions concise and actionable

## OUTPUT FORMAT:

```markdown
# {Role} Skills

**Protocol**: `{ProtocolFile}.scr`

## Role Purpose
One sentence describing what this role does.

## Receives
- `MessageName()` from {Sender} - brief description

## Sends  
- `MessageName()` to {Receiver} - brief description

## Decision Rules (if this role has choice/decision)
- Condition 1: Action to take
- Condition 2: Alternative action

## Execution Flow
1. Step 1
2. Step 2
...

## Business Rules
- Specific threshold or value from requirement
- Specific condition from requirement
```

Keep the file SHORT and ACTIONABLE. Include ALL specific values/thresholds from the user requirement."""


# =============================================================================
# EVOLVE PROTOCOL PROMPT (continue/extend an existing protocol)
# =============================================================================

SCRIBBLE_EVOLVE_PROMPT = """You are an expert in Scribble, a protocol description language for session types.
Your task is to EVOLVE an existing Scribble protocol by incorporating a new requirement.

## KEY PRINCIPLE
You are NOT creating a new protocol from scratch. You are EXTENDING the existing protocol.
The new requirement adds to or modifies the existing flow.

## SCRIBBLE SYNTAX RULES

1. **Module Declaration**: Must match the filename
   ```
   module ModuleName;
   ```

2. **Payload Type Declarations** (REQUIRED if any message carries data):
   Scribble does NOT have built-in types. You MUST declare every type used.
   Place AFTER module line, BEFORE any protocol.
   ```
   data <java> "java.lang.Double" from "rt.jar" as Double;
   data <java> "java.lang.String" from "rt.jar" as String;
   data <java> "java.lang.Integer" from "rt.jar" as Int;
   ```

3. **Protocol Declaration**: Declare all roles
   ```
   global protocol ProtocolName(role RoleA, role RoleB, role RoleC) {
       ...
   }
   ```

4. **Message Syntax**: `Label()` or `Label(Type)` from Sender to Receiver;
   ```
   SendData(Double) from RoleA to RoleB;
   ```

5. **Choice (Branching)**: Use `choice at <Role>` for conditional paths
   ```
   choice at Decider {
       OptionA() from Decider to RoleX;
   } or {
       OptionB() from Decider to RoleX;
   }
   ```

6. **Sub-protocol Composition**: Use `do` to invoke another protocol
   ```
   aux global protocol SubTask(role X, role Y) {
       Request() from X to Y;
       Response() from Y to X;
   }
   global protocol Main(role A, role B, role C) {
       Setup() from A to B;
       do SubTask(B, C);
       Done() from B to A;
   }
   ```

## CRITICAL RULES

1. **External Choice Rule**: In `choice at X`, ALL roles receiving messages in the choice
   MUST receive their FIRST message from the SAME sender in ALL branches.
   The chooser (X) should send the first message to EVERY role inside the choice block.
2. **Role Declaration**: Every role used MUST be declared in the protocol header.
3. **Notification**: ALL roles that send or receive inside a `choice` block must be notified
   of which branch was taken. The chooser must send them a notification message first.
4. When adding a new role with its own choice logic involving only a subset of roles,
   you can either inline it or use `aux global protocol` + `do`.

## SEPARATION OF CONCERNS

- **Protocol**: Captures message STRUCTURE and FLOW. Use typed payloads (Double, String, etc.)
  to indicate what data is carried. The protocol does NOT contain value constraints.
- **Skills (.md)**: Captures BUSINESS RULES and VALUE CONSTRAINTS (e.g., specific thresholds,
  time limits, numeric conditions).

When a requirement says "if <condition>, route to ROLE X", the protocol should have:
- `choice at` the relevant ROLEs that knows the value
- Typed payload: `<Action>(<data type>)` to indicate the data type
- The <condition> threshold goes in skills.md, NOT in the protocol

## HOW TO EVOLVE A PROTOCOL

### Adding an intermediary role
When the new requirement introduces a new role as an INTERMEDIARY (e.g., "A sends to B"
becomes "A sends to NEW_ROLE, then NEW_ROLE sends to B"):
REPLACE the original direct message with the new routed path.

### Adding conditional logic (if-else)
When the requirement introduces VALUE-BASED CONDITIONS (e.g., "if value > threshold, do X,
otherwise do Y"):
1. Identify which role KNOWS the value → that role is the chooser
2. Use `choice at <that role>`
3. Add typed payloads to the relevant messages (e.g., `SendData(Double)`)
4. The chooser MUST send a first notification to EVERY role inside the choice block
   in EVERY branch (to satisfy the external choice rule)
5. Common steps before the branch go BEFORE the choice block

EXAMPLE — Adding a conditional route with a new intermediary role:
Base protocol:
```
global protocol MyProto(role A, role B, role C) {
    Request() from A to B;
    Process() from B to C;
    Result() from C to A;
}
```
New requirement: "If a value exceeds a threshold, a new role D must review before B processes it"
Correct evolution:
```
data <java> "java.lang.Double" from "rt.jar" as Double;

global protocol MyProto(role A, role B, role C, role D) {
    choice at A {
        HighPath(Double) from A to D;
        HighNotice() from A to B;
        HighNotice() from A to C;
        Reviewed(Double) from D to B;
        Process() from B to C;
        Result() from C to A;
    } or {
        NormalPath(Double) from A to B;
        NormalNotice() from A to D;
        NormalNotice() from A to C;
        Process() from B to C;
        Result() from C to A;
    }
}
```
Key points:
- `choice at A` because A knows the value that determines the branch
- Payload `(Double)` on data messages to carry the typed value
- A sends FIRST message to ALL roles (B, C, D) in BOTH branches
- The specific threshold goes in skills.md, not in the protocol

## OUTPUT FORMAT

Return ONLY the Scribble protocol code, no explanations. Start with `module` and end with `}`.
"""


def get_protocol_evolve_prompt(new_requirement: str, accumulated_requirement: str,
                               base_protocol: str, module_name: str) -> str:
    """Generate the user prompt for evolving an existing protocol with a new requirement"""
    return f"""EVOLVE this existing Scribble protocol with a new requirement.

## EXISTING PROTOCOL (this is your BASE)
```scribble
{base_protocol}
```

## FULL CONTEXT (accumulated from all versions)
{accumulated_requirement}

## NEW REQUIREMENT (what to ADD/MODIFY in the existing protocol)
{new_requirement}

Use module name: {module_name}

## YOUR TASK
1. Start from the base protocol structure
2. Add new roles to the protocol header
3. Analyze the new requirement:
   - Does it mention a VALUE THRESHOLD (e.g., "exceeds X", "greater than Y")?
     → Use `choice at <role that knows the value>` with typed payloads (Double, String, etc.)
     → Add `data <java> "java.lang.Double" from "rt.jar" as Double;` declarations as needed
     → The specific threshold value goes in skills.md, NOT in the protocol
   - Does it add an INTERMEDIARY between existing roles?
     → REPLACE the old direct message with the new routed path
   - Does it add a completely new step?
     → INSERT it at the logically correct position
4. If using `choice at X`:
   - X must send the FIRST message to EVERY role inside the choice block in EVERY branch
   - ALL roles that send or receive inside the choice must be notified of the branch
5. Ensure the result is valid Scribble syntax

Return ONLY the evolved Scribble protocol code."""


def get_protocol_generation_prompt(requirement: str, module_name: str) -> str:
    """Generate the user prompt for protocol creation"""
    return f"""Create a Scribble protocol for the following requirement:

REQUIREMENT: {requirement}

Use module name: {module_name}

Generate a valid Scribble protocol that:
1. Identifies all necessary roles from the requirement
2. Defines the message flow between roles
3. Handles any conditional logic with `choice at` syntax
4. Follows all Scribble syntax rules

Return ONLY the Scribble protocol code."""


def get_protocol_fix_prompt(requirement: str, previous_protocol: str, error: str, module_name: str) -> str:
    """Generate the user prompt for fixing a protocol"""
    return f"""Fix this Scribble protocol that failed validation.

ORIGINAL REQUIREMENT: {requirement}

PREVIOUS PROTOCOL:
```
{previous_protocol}
```

SCRIBBLE COMPILER ERROR:
{error}

Use module name: {module_name}

Fix the protocol to resolve this error. Return ONLY the corrected Scribble code."""


def get_skills_generation_prompt(protocol_content: str, role: str, protocol_name: str, 
                                  user_requirement: str = "") -> str:
    """Generate the user prompt for skills file creation"""
    
    requirement_section = ""
    if user_requirement:
        requirement_section = f"""
## ORIGINAL USER REQUIREMENT (extract business rules from this!)
\"\"\"{user_requirement}\"\"\"

IMPORTANT: Extract ANY specific values, thresholds, or conditions from the requirement above.
Examples of what to extract:
- Dollar amounts (e.g., "$10,000", "$5,000")
- Time limits (e.g., "3 days", "24 hours")
- Quantities (e.g., "5 items", "3 retries")
- Percentages (e.g., "10%", "above 50%")
- Decision criteria (e.g., "if X > Y then...")

These MUST appear in the skills file under "Business Rules" or "Decision Rules" sections.
"""
    
    return f"""Generate a MINIMAL skills.md file for role "{role}".

## THIS PROTOCOL IS THE ONLY SOURCE OF TRUTH
Protocol file: {protocol_name}.scr

```scribble
{protocol_content}
```
{requirement_section}
ROLE TO IMPLEMENT: {role}

REQUIREMENTS:
1. Skills MUST strictly follow THIS protocol's message flow - no extra messages
2. Reference the protocol file: {protocol_name}.scr
3. If this role has a `choice at {role}` in the protocol, include DECISION RULES with specific conditions
4. EXTRACT specific thresholds/values from user requirement (if provided)
5. Keep it minimal but include ALL business logic

CRITICAL: If the user requirement mentions specific numbers (like "$10,000"), those MUST appear in the skills!

Output a SHORT, ACTIONABLE skills.md that an LLM can follow to participate correctly
in the {protocol_name} protocol."""


# =============================================================================
# MERGE PROTOCOL PROMPT
# =============================================================================

MERGE_SYSTEM_PROMPT = """You are an expert in Scribble, a protocol description language for session types.
Your task is to MERGE multiple protocol versions into ONE unified Scribble file.

## SCRIBBLE SYNTAX RULES

1. **Module Declaration**: Must match the filename
   ```
   module ModuleName;
   ```

2. **Protocol Declaration**: Declare all roles
   ```
   global protocol ProtocolName(role RoleA, role RoleB, role RoleC) {
       ...
   }
   ```

3. **Message Syntax**: Label() from Sender to Receiver;
   ```
   RequestData() from Client to Server;
   ResponseData() from Server to Client;
   ```

4. **Choice (Branching)**: Use `choice at <Role>` for conditional paths
   ```
   choice at Decider {
       OptionA() from Decider to RoleX;
       ...
   } or {
       OptionB() from Decider to RoleX;
       ...
   }
   ```

5. **Sub-protocol Composition (the `do` keyword)**: Use `do` to invoke another protocol
   ```
   // Define a sub-protocol (use `aux` for helper protocols)
   aux global protocol SubTask(role X, role Y) {
       Request() from X to Y;
       Response() from Y to X;
   }

   // Invoke it from the main protocol
   global protocol Main(role A, role B, role C) {
       Setup() from A to B;
       do SubTask(B, C);   // B maps to X, C maps to Y
       Done() from B to A;
   }
   ```
   The `do` keyword lets you compose protocols. Role arguments map positionally
   to the sub-protocol's declared roles.

## CRITICAL SCRIBBLE RULES

1. **External Choice Rule**: In `choice at X`, ALL roles that receive messages in the choice block
   MUST receive their FIRST message from the SAME sender in ALL branches.

2. **Role Declaration**: Every role used in messages MUST be declared in the protocol header.

3. **No Deadlocks**: Ensure message flow has clear ordering without circular waits.

4. **Complete Paths**: All branches must have complete message flows.

5. **Sub-protocol roles**: When using `do SubProto(A, B)`, roles A and B MUST be declared
   in the calling protocol's header.

## MERGE STRATEGIES

When merging versions that have DIFFERENT role sets or protocol structures, choose the
best strategy:

### Strategy 1: Flat Merge (when the new version adds steps to the SAME flow)
If the new version adds a step (e.g., an audit) that fits sequentially into the existing
flow and involves only a subset of roles, insert it inline:
```
global protocol Main(role A, role B, role Auditor) {
    Work() from A to B;
    // Audit step inserted into the flow
    SubmitAudit() from B to Auditor;
    choice at Auditor {
        Approve() from Auditor to B;
    } or {
        Reject() from Auditor to B;
    }
    Done() from B to A;
}
```
The `choice at Auditor` above is valid because only B receives messages inside the
choice block, and B always gets its first message from Auditor.

### Strategy 2: Sub-protocol Merge (when versions define clearly separate interactions)
If a version defines a self-contained interaction between a subset of roles, use `aux`:
```
aux global protocol Review(role X, role Y) {
    Submit() from X to Y;
    choice at Y {
        Approve() from Y to X;
    } or {
        Reject() from Y to X;
    }
}

global protocol Main(role A, role B, role C, role D) {
    Data() from A to B;
    Process() from B to C;
    do Review(B, D);
    Result() from C to A;
}
```

### CHOOSING THE RIGHT STRATEGY
- If the new version's interaction is self-contained (only 2-3 roles, has its own choice
  logic), prefer **sub-protocol** (`aux` + `do`) for clarity.
- If the new version just adds a simple step in the sequence, prefer **flat merge**.
- ALWAYS ensure the main protocol header declares ALL roles from ALL versions.
- NEVER drop roles or features from any version during the merge.

## MERGE GUIDELINES

1. Understand the PURPOSE of each version from its requirement
2. Identify COMMON roles across versions (shared roles connect the protocols)
3. Identify NEW roles/features added in later versions
4. Choose the appropriate merge strategy (flat or sub-protocol)
5. The main protocol header MUST include ALL roles from ALL versions
6. Preserve the message flow logic from EACH version — do NOT invent new unrelated logic
7. Ensure the merged protocol is VALID Scribble syntax

## OUTPUT FORMAT

Return ONLY the Scribble protocol code, no explanations. Start with `module` and end with `}`.
"""


def get_merge_prompt(versions_info: list, module_name: str) -> str:
    """
    Generate the user prompt for merging multiple protocol versions.
    
    versions_info is a list of dicts with:
        - version: version number
        - requirement: user's original requirement
        - protocol_content: the Scribble code
    """
    prompt_parts = [f"MERGE these protocol versions into ONE unified Scribble file.\n"]
    prompt_parts.append(f"Use module name: {module_name}\n")
    prompt_parts.append("="*60 + "\n")
    
    # Collect all roles across versions for analysis
    all_roles = set()
    for v in versions_info:
        import re
        role_matches = re.findall(r'role\s+(\w+)', v['protocol_content'])
        all_roles.update(role_matches)
    
    for v in versions_info:
        prompt_parts.append(f"## VERSION {v['version']}")
        prompt_parts.append(f"REQUIREMENT: {v['requirement']}")
        prompt_parts.append(f"```scribble\n{v['protocol_content']}\n```")
        prompt_parts.append("-"*40 + "\n")
    
    prompt_parts.append(f"""
## ANALYSIS

All roles across versions: {', '.join(sorted(all_roles))}
The merged protocol's main `global protocol` MUST declare ALL of these roles.

## YOUR TASK

1. Analyze what each version achieves
2. Identify the SHARED roles (these connect the protocols)
3. Choose merge strategy:
   - If a version defines a self-contained sub-interaction (e.g., an audit/verification
     between 2 roles), use `aux global protocol` + `do` to compose it
   - If a version adds simple sequential steps, inline them
4. The merged protocol MUST:
   - Include ALL roles: {', '.join(sorted(all_roles))}
   - Preserve the EXACT message flow logic from each version
   - NOT invent new message types or choice blocks that weren't in any version
   - Follow ALL Scribble syntax rules (especially external choice rule)

Return ONLY the merged Scribble protocol code (including any `aux` sub-protocols).""")
    
    return "\n".join(prompt_parts)

