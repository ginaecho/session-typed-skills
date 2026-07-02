"""
Skills Synthesizer — Reverse Pipeline

Reconstructs a Scribble global protocol from existing *_skills.md files.
This is the inverse of the forward pipeline (protocol → skills).

Flow:
  *_skills.md files
      → skills_parser.parse_all_skills()   (extract per-role local views)
      → LLM synthesizes global protocol     (local → global reconstruction)
      → Scribble compiler validates
      → If invalid: LLM fixes (retry loop, same pattern as architect.py)
      → Returns (success, protocol_content, saved_path)

Theoretical note:
  Each *_skills.md is essentially a LOCAL TYPE (the projection of the global
  protocol onto one role).  This module solves the PROJECTION INVERSE problem:
  given n local types, find the global type they collectively implement.
  The Scribble compiler acts as the oracle that confirms well-formedness.
"""

import re
import logging
from pathlib import Path

from stjp_core.foundry.llm_client import LLMClient
from stjp_core.generation.skills_parser import parse_all_skills, ParsedSkills
from stjp_core.compiler.validator import ScribbleValidator
# Callers must supply an explicit ``output_path`` when invoking
# synthesize_from_skills_dir(...). The module-level PROTOCOLS_DIR default
# was removed when stjp_core/protocols/ was retired in favour of
# experiments/cases/<case>/protocols/.

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = """You are an expert in Scribble, a protocol description language for session types.
Your task is to RECONSTRUCT a global Scribble protocol from a set of per-role local views (skills files).

Each skills file describes what ONE role does: what messages it sends, what it receives,
and its decision logic.  Your job is to synthesize these local views into ONE coherent
global protocol that all roles collectively implement.

## SCRIBBLE SYNTAX RULES

1. **Module Declaration**: Must match the filename
   ```
   module ModuleName;
   ```

2. **Payload Type Declarations** (REQUIRED if any message carries data):
   Scribble does NOT have built-in types. You MUST declare every type used in messages.
   Place these AFTER the module line, BEFORE any protocol declaration.
   ```
   data <java> "java.lang.String" from "rt.jar" as String;
   data <java> "java.lang.Double" from "rt.jar" as Double;
   data <java> "java.lang.Integer" from "rt.jar" as Int;
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

## CRITICAL RULES

1. **External Choice Rule**: In `choice at X`, ALL roles that receive messages inside the
   choice block MUST receive their FIRST message from the SAME sender (X) in ALL branches.
   The chooser (X) must send the very first message to EVERY role that appears inside the
   choice block, in EVERY branch.

2. **Role Declaration**: Every role used in messages MUST be declared in the protocol header.

3. **No Deadlocks**: Ensure message flow has clear ordering without circular waits.

4. **Complete Paths**: All branches must have complete message flows with no dead ends.

5. **Choosing the right `choice at` role**: Use the role whose Decision Rules section
   describes the branching logic — that is the role that KNOWS the value determining the path.

6. **Notification in all branches**: ALL roles that send or receive inside a choice block
   must be notified of which branch was taken in EVERY branch.

7. **CRITICAL — Factor out common suffixes**: If a message (or sequence of messages) appears
   identically at the end of EVERY branch, it MUST be moved OUTSIDE the choice block to the
   top level. Repeating identical messages inside each branch causes "Inconsistent external
   choice subjects" errors because Scribble sees a role receiving from different first-senders
   depending on which branch is ordered first.

   WRONG (common messages inside each branch):
   ```
   choice at X {
       BranchA() from X to Y;
       Done() from Y to Z;      // Done appears in both branches
   } or {
       BranchB() from X to Y;
       Done() from Y to Z;      // Scribble ERROR: inconsistent first sender for Z
   }
   ```
   CORRECT (common messages factored out after the choice):
   ```
   choice at X {
       BranchA() from X to Y;
   } or {
       BranchB() from X to Y;
   }
   Done() from Y to Z;          // Z's first sender is always Y, no ambiguity
   ```

8. **Causal connectivity for top-level ordering (CRITICAL)**: After a choice block, the
   global protocol has sequential top-level messages. For a role R to safely take a
   top-level action (send or receive), Scribble must be able to prove that R knows the
   choice has completed. R knows this IF AND ONLY IF R received at least one message inside
   the choice block (in EVERY branch it cares about), OR R receives a top-level message
   from a role that was already enabled.

   The key consequence: if the CHOOSER needs to send a "trigger" message to role R so
   that R can produce a result needed by another role at the top level, that trigger message
   MUST be placed as the LAST message inside EVERY branch of the choice — NOT factored
   out to the top level. If it is moved to the top level, Scribble loses the causal
   ordering guarantee and raises a "Safety violation".

   EXAMPLE — Fetcher must send RetrieveData() to ExpenseAnalyst, and ExpenseAnalyst's
   output (ExpenseAnalysis) is needed at the top level:
   WRONG (trigger outside → safety violation):
   ```
   choice at Fetcher {
       HighPath() from Fetcher to X;
   } or {
       LowPath() from Fetcher to X;
   }
   RetrieveData() from Fetcher to ExpenseAnalyst;   // ExpenseAnalyst sees this AFTER choice
   ExpenseAnalysis(String) from ExpenseAnalyst to Writer;  // Scribble: unsafe ordering!
   ```
   CORRECT (trigger inside every branch → safe):
   ```
   choice at Fetcher {
       HighPath() from Fetcher to X;
       RetrieveData() from Fetcher to ExpenseAnalyst;  // last message in branch 0
   } or {
       LowPath() from Fetcher to X;
       RetrieveData() from Fetcher to ExpenseAnalyst;  // last message in branch 1
   }
   ExpenseAnalysis(String) from ExpenseAnalyst to Writer;  // safe: ExpenseAnalyst already ready
   ```

9. **ALL-OR-NOTHING rule for roles in a choice block (DEADLOCK PREVENTION)**:
   A role R must appear in ALL branches of a choice OR in NO branches. If R appears in
   only SOME branches, then in branches where R has no messages, R is stuck waiting for
   a message that never arrives → DEADLOCK.

   WRONG (TaxVerifier only in high branch → deadlock in standard branch):
   ```
   choice at Fetcher {
       HighPath() from Fetcher to X;
       AuditResult() from TaxVerifier to X;   // TaxVerifier only here!
   } or {
       StandardPath() from Fetcher to X;
       // TaxVerifier absent → TaxVerifier waits forever in standard branch → DEADLOCK
   }
   ```
   To fix: either
   (a) Move TaxVerifier's interactions OUTSIDE the choice (to the top level, always
       happening): `AuditResult() from TaxVerifier to X;` at top level after the choice, OR
   (b) Add TaxVerifier a notification in EVERY branch so it knows which path was taken.

10. **Rule for factoring-out vs. keeping inside**:
    - FACTOR OUT to top level: messages that are conceptually "always required" regardless
      of branch, AND whose sender/receiver would otherwise appear in only some branches.
      These are typically: audit/verification round-trips, analysis messages, report generation.
    - KEEP INSIDE each branch: messages that are BRANCH-SPECIFIC — those where the label
      or payload is different per branch (HighRevenue vs StandardRevenue, HighNotice vs
      StandardNotice). Also keep inside: any TRIGGER message the chooser must send to a
      role so that role is causally ready for top-level actions (duplicate this in every branch).
    - KEY HEURISTIC: if a message appears in the skills with no conditional logic
      (no "if branch A / if branch B"), it almost certainly belongs at the TOP LEVEL.

## RECONSTRUCTION APPROACH

When given local views from multiple roles:

1. **Collect all roles** — one `role X` per role in the skills.
2. **Identify common vs. branch-specific messages**:
   - Messages that appear in the Execution Flow of the choice role for BOTH branches
     AND are identical → place them AFTER the choice block.
   - Messages unique to one branch → place them INSIDE that branch.
3. **Match each message** — cross-reference SENDS and RECEIVES sections:
   - If role A's SENDS says `Msg(T) to B` AND role B's RECEIVES says `Msg(T) from A`,
     that is a confirmed match → `Msg(T) from A to B;`
   - If only one side mentions a message, use it but treat it as less certain.
   - Resolve conflicts (two roles both claiming to send the same message) by preferring
     the role that explicitly lists it in SENDS over one that mentions it narratively.
3. **Detect the choice role** — the role with a Decision Rules section that describes
   an if/else branch is the `choice at` role.
4. **Reconstruct branches** — group messages that belong to each branch using the
   Decision Rules as the guide.  Ensure the choice role sends first in every branch.
5. **Order messages globally** — use the Execution Flow sections to infer sequence.
6. **Declare payload types** — scan all messages for non-empty payload types.

## OUTPUT FORMAT

Return ONLY the Scribble protocol code, no explanations.
Start with `module` and end with `}`.
Use the module name: {module_name}
"""


def _format_skills_as_context(all_skills: dict[str, "ParsedSkills"]) -> str:
    """Format parsed skills into a structured string for the LLM."""
    lines = ["## LOCAL VIEWS (per-role skills)\n"]

    for role_name, skills in sorted(all_skills.items()):
        lines.append(f"### ROLE: {role_name}")

        if skills.role_purpose:
            lines.append(f"Purpose: {skills.role_purpose}")

        if skills.receives:
            lines.append("Receives:")
            for msg in skills.receives:
                payload = f"({msg.payload_type})" if msg.payload_type else "()"
                lines.append(f"  - {msg.message_name}{payload} from {msg.counterparty}")
        else:
            lines.append("Receives: None (this role initiates)")

        if skills.sends:
            lines.append("Sends:")
            for msg in skills.sends:
                payload = f"({msg.payload_type})" if msg.payload_type else "()"
                lines.append(f"  - {msg.message_name}{payload} to {msg.counterparty}")
        else:
            lines.append("Sends: None")

        if skills.decision_rules:
            lines.append("Decision Rules:")
            for dl in skills.decision_rules.splitlines():
                lines.append(f"  {dl}")

        if skills.execution_flow:
            lines.append("Execution Flow:")
            for fl in skills.execution_flow.splitlines():
                lines.append(f"  {fl}")

        lines.append("")  # blank separator between roles

    return "\n".join(lines)


def _synthesis_user_prompt(skills_context: str, module_name: str,
                           protocol_name: str) -> str:
    return f"""RECONSTRUCT a global Scribble protocol from these per-role local views.

{skills_context}

## YOUR TASK
1. Declare one `role X` per role listed above.
2. Match every (sender, message, receiver) triple by cross-referencing SENDS/RECEIVES sections.
3. Identify the role with Decision Rules → use `choice at <that role>` for branching.
4. Inside each branch, ensure the chooser sends the FIRST message to EVERY role
   that participates inside the choice block (External Choice Rule).
5. Order messages using Execution Flow sections as ordering hints.
6. Declare all payload types (Double, String, etc.) before the protocol block.
7. Keep thresholds and business logic OUT of the protocol (they belong in skills).

Module name: {module_name}
Protocol name: {protocol_name}

Return ONLY the Scribble protocol code.  Start with `module`, end with `}}`.
"""


def _fix_user_prompt(skills_context: str, previous_protocol: str,
                     error: str, module_name: str, protocol_name: str) -> str:
    return f"""The Scribble compiler rejected the reconstructed protocol. Fix it.

{skills_context}

## PREVIOUS PROTOCOL (failed)
```scribble
{previous_protocol}
```

## SCRIBBLE COMPILER ERROR
{error}

## COMMON FIXES
- "Inconsistent external choice subjects for RoleX" → RoleX receives its first message
  from DIFFERENT senders in different branches. The fix is to move that message (and any
  messages that appear identically in ALL branches) OUT of the choice block to the top
  level AFTER the choice. Only branch-specific messages belong inside a choice branch.
  Example fix:
    BEFORE (broken):     choice at X {{ BranchA() from X to Y; Result() from Y to Z; }} or {{ BranchB() from X to Y; Result() from Y to Z; }}
    AFTER  (correct):    choice at X {{ BranchA() from X to Y; }} or {{ BranchB() from X to Y; }} Result() from Y to Z;
- "Safety violation" trace → There are two common root causes. Diagnose which applies:

  CAUSE A — A role appears inside only SOME branches (deadlock in other branches):
  Look at your choice block. If role TaxVerifier (or any role with messages in a
  round-trip) appears inside the HIGH branch but NOT the standard branch, that role
  will be stuck waiting in the standard path. FIX: Move those messages (the full
  round-trip: Request + Approval) OUTSIDE the choice block, to the top level. They
  will then always execute after the choice completes, in all branches.
  Affected messages to move out: any REQUEST/RESPONSE pair between non-chooser roles
  (e.g., RevenueAuditRequest/RevenueAuditApproval, or similar audit/verify round-trips).

  CAUSE B — A trigger message from the chooser to a role is outside the choice when
  it should be inside (causal ordering):
  If Trace shows `RoleX?Chooser:MsgY` at the top level after the choice, move
  `MsgY from Chooser to RoleX` to the END of EVERY branch (duplicate it in all branches).

  COMBINED FIX PATTERN for this protocol:
  - INSIDE each branch: only the BRANCH-SPECIFIC messages (different label per branch)
    AND the trigger from Fetcher to ExpenseAnalyst (RetrieveData — inside BOTH branches).
  - OUTSIDE the choice (top level): all ROUND-TRIP messages between non-chooser roles
    (RevenueAuditRequest→TaxVerifier, RevenueAuditApproval→RevenueAnalyst),
    then the analysis messages (RevenueAnalysis→Writer, ExpenseAnalysis→Writer),
    then the final report (GenerateReport→Fetcher).
- "external choice" / "Inconsistent external choice" (general) → The chooser must send the
  FIRST message to EVERY role inside the choice block in EVERY branch.
- "not bound" → Add the missing role to the protocol declaration header.
- "Cannot disambiguate" → Add `data <java> "java.lang.X" from "rt.jar" as X;`
- "Source role not enabled" → A role tries to send before being notified of the branch.
  Add a notification from the chooser to that role first.
- "mismatch" → Module name must match exactly: {module_name}

Module name: {module_name}
Protocol name: {protocol_name}

Return ONLY the corrected Scribble code.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Synthesizer
# ─────────────────────────────────────────────────────────────────────────────

class SkillsSynthesizer:
    """
    Reconstructs a Scribble global protocol from existing *_skills.md files.

    Reverse of the forward pipeline:
      Forward: protocol (.scr) → project → skills (.md) per role
      Reverse: skills (.md) per role → synthesize → protocol (.scr)

    Uses the same LLM + Scribble compiler validation retry loop as architect.py.
    """

    MAX_RETRIES = 5

    def __init__(self):
        self.llm = LLMClient()
        self.validator = ScribbleValidator()

    def synthesize_from_skills_dir(
        self,
        skills_dir: Path,
        module_name: str = "SynthesizedProtocol",
        protocol_name: str = "SynthesizedProtocol",
        output_path: Path = None,
    ) -> tuple[bool, str, Path | None]:
        """
        Reconstruct a global Scribble protocol from a directory of *_skills.md files.

        Args:
            skills_dir:     Directory containing *_skills.md files.
            module_name:    Scribble module name (must match output filename stem).
            protocol_name:  Name used inside `global protocol <name>(...)`.
            output_path:    Where to save the .scr file. REQUIRED — pass an
                            absolute path under experiments/cases/<case>/protocols/
                            or wherever the caller wants the .scr written.

        Returns:
            (success, protocol_content, saved_path)
            success=False means the Scribble compiler rejected all attempts.
        """
        if output_path is None:
            raise ValueError(
                "synthesize_from_skills_dir() requires output_path. "
                "Pass an explicit Path under experiments/cases/<case>/protocols/ "
                "(the legacy PROTOCOLS_DIR default was removed on 2026-05-29)."
            )

        print(f"\n[SYNTHESIZER] Parsing skills from: {skills_dir}")
        all_skills = parse_all_skills(skills_dir)

        if not all_skills:
            print("[SYNTHESIZER] ERROR: no *_skills.md files found!")
            return False, "", None

        role_list = ", ".join(sorted(all_skills))
        print(f"[SYNTHESIZER] Found {len(all_skills)} role(s): {role_list}")

        skills_context = _format_skills_as_context(all_skills)
        system_prompt = SYNTHESIS_SYSTEM_PROMPT.replace("{module_name}", module_name)

        protocol_content = ""
        error = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            print(f"\n[SYNTHESIZER] Attempt {attempt}/{self.MAX_RETRIES}")

            if attempt == 1:
                print("[SYNTHESIZER-LLM] Synthesizing global protocol from local views...")
                user_prompt = _synthesis_user_prompt(skills_context, module_name, protocol_name)
            else:
                print(f"[SYNTHESIZER-LLM] Fixing error: {error[:80]}...")
                user_prompt = _fix_user_prompt(
                    skills_context, protocol_content, error, module_name, protocol_name
                )

            response = self.llm.generate(system_prompt, user_prompt)
            protocol_content = self._extract_protocol_code(response, module_name)
            print(f"[SYNTHESIZER-LLM] Protocol generated ({len(protocol_content)} chars)")

            # Save to file — Scribble compiler works on disk
            output_path.write_text(protocol_content, encoding="utf-8")

            print("[SYNTHESIZER] Validating with Scribble compiler...")
            is_valid, error = self.validator.validate_protocol(output_path)

            if is_valid:
                print(f"[SYNTHESIZER] VALID -- saved to: {output_path}")
                return True, protocol_content, output_path
            else:
                print(f"[SYNTHESIZER] INVALID: {error[:150]}")

        print(f"[SYNTHESIZER] FAILED after {self.MAX_RETRIES} attempts. Last error: {error[:200]}")
        return False, protocol_content, output_path

    # ── helpers ──────────────────────────────────────────────────────────────

    def _extract_protocol_code(self, response: str, module_name: str) -> str:
        """
        Extract Scribble code from LLM response.
        Mirrors architect.py._extract_protocol_code() exactly.
        """
        code_blocks = re.findall(r"```(?:scribble)?\s*\n(.*?)```", response, re.DOTALL)
        if code_blocks:
            code = "\n\n".join(block.strip() for block in code_blocks)
        else:
            code = response.strip()

        if not code.startswith("module"):
            module_match = re.search(r"(module\s+\w+;.*)", code, re.DOTALL)
            if module_match:
                code = module_match.group(1)

        # Enforce correct module name
        code = re.sub(r"module\s+\w+;", f"module {module_name};", code, count=1)

        # Drop duplicate module declarations (can happen when LLM joins blocks)
        lines = code.split("\n")
        seen_module = False
        cleaned = []
        for line in lines:
            if re.match(r"^module\s+\w+;", line.strip()):
                if seen_module:
                    continue
                seen_module = True
            cleaned.append(line)

        return "\n".join(cleaned)
