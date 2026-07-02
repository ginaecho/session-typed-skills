"""
LLM-Powered Architect Agent

Uses Azure OpenAI to translate natural language requirements into Scribble protocols.
"""

import re
from stjp_core.foundry.llm_client import LLMClient
from stjp_core.authoring.prompts import (
    SCRIBBLE_SYSTEM_PROMPT,
    SCRIBBLE_FIX_PROMPT,
    SCRIBBLE_EVOLVE_PROMPT,
    MERGE_SYSTEM_PROMPT,
    get_protocol_generation_prompt,
    get_protocol_evolve_prompt,
    get_protocol_fix_prompt,
    get_merge_prompt,
    SCRIBBLE_SYSTEM_PROMPT_V2,
    SCRIBBLE_FIX_PROMPT_V2,
    get_protocol_generation_prompt_v2,
    get_protocol_fix_prompt_v2,
)
from stjp_core.authoring.rules import ScribbleRules


class ArchitectAgent:
    """
    The Architect Agent - Uses LLM to translate natural language requirements 
    into valid Scribble protocols.
    
    Flow:
    1. User provides natural language requirement
    2. LLM generates Scribble protocol
    3. Scribble compiler validates
    4. If error: LLM fixes based on error message
    5. Repeat until valid or max attempts reached
    """
    
    def __init__(self, use_v2_prompt: bool = True, auto_fanout: bool = True):
        # use_v2_prompt=True (default): reason-then-code prompts with the
        # choice-notification template (2026-06-17, fewer re-draft loops).
        # auto_fanout=True (default): deterministically repair the #1 structural
        # error (incomplete choice fan-out / "Unfinished roles") BEFORE Scribble,
        # via fanout_normalizer. Insert-only + minimal-target, so it never alters
        # an already-valid protocol. Both flags False = original v1 behaviour.
        self.llm = LLMClient()
        self.use_v2_prompt = use_v2_prompt
        self.auto_fanout = auto_fanout
        self.attempt_count = 0
        self.last_error = None
        self.learned_lessons = []
        self.rules = ScribbleRules()
        self.conversation_history = []
        
    def draft_protocol(self, requirement: str, module_name: str,
                       previous_protocol: str = None, 
                       previous_error: str = None,
                       base_protocol: str = None,
                       accumulated_requirement: str = None) -> str:
        """
        Draft a Scribble protocol using LLM.
        
        Three modes:
        1. Fresh generation: No base_protocol, no error → create from scratch
        2. Evolve mode: base_protocol provided, no error → extend existing protocol
        3. Fix mode: previous_error provided → fix compiler error
        
        Args:
            requirement: Natural language description (new requirement)
            module_name: The module name (must match filename)
            previous_protocol: If retrying, the previous failed protocol
            previous_error: If retrying, the Scribble compiler error
            base_protocol: The current valid protocol to evolve from (v0 code)
            accumulated_requirement: Full accumulated description from all versions
            
        Returns:
            Generated Scribble protocol code
        """
        self.attempt_count += 1
        
        # Learn from previous error
        if previous_error:
          self._learn_from_error(previous_error)
        
        print(f"    [ARCHITECT-LLM] Generating protocol with LLM...")
        
        if previous_protocol and previous_error:
            # Fix mode - provide error context to LLM
            print(f"    [ARCHITECT-LLM] Fixing previous error: {previous_error[:80]}...")

            fix_requirement = accumulated_requirement or requirement
            if self.use_v2_prompt:
                system_prompt = SCRIBBLE_FIX_PROMPT_V2.replace("{module_name}", module_name)
                user_prompt = get_protocol_fix_prompt_v2(
                    fix_requirement, previous_protocol, previous_error, module_name
                )
            else:
                system_prompt = SCRIBBLE_FIX_PROMPT.replace("{module_name}", module_name)
                user_prompt = get_protocol_fix_prompt(
                    fix_requirement, previous_protocol, previous_error, module_name
                )
        elif base_protocol and not previous_error:
            # Evolve mode - extend existing protocol with new requirement
            print(f"    [ARCHITECT-LLM] Evolving existing protocol with new requirement...")
            
            system_prompt = SCRIBBLE_EVOLVE_PROMPT.replace("{module_name}", module_name)
            user_prompt = get_protocol_evolve_prompt(
                requirement, accumulated_requirement or requirement,
                base_protocol, module_name
            )
        else:
            # Fresh generation
            print(f"    [ARCHITECT-LLM] Creating new protocol from requirement...")

            if self.use_v2_prompt:
                system_prompt = SCRIBBLE_SYSTEM_PROMPT_V2.replace("{module_name}", module_name)
                user_prompt = get_protocol_generation_prompt_v2(requirement, module_name)
            else:
                system_prompt = SCRIBBLE_SYSTEM_PROMPT.replace("{module_name}", module_name)
                user_prompt = get_protocol_generation_prompt(requirement, module_name)
        
        # Call LLM
        response = self.llm.generate(system_prompt, user_prompt)
        
        # Extract protocol code (in case LLM adds explanations)
        protocol = self._extract_protocol_code(response, module_name)

        # Deterministic structural repair of the incomplete-fan-out error
        # (the dominant cause of re-draft loops). Insert-only + minimal-target.
        if self.auto_fanout:
            try:
                from stjp_core.authoring.fanout_normalizer import normalize_fanout
                fixed, inserted = normalize_fanout(protocol)
                if inserted:
                    protocol = fixed
                    print(f"    [ARCHITECT-LLM] [fan-out] inserted "
                          f"{len(inserted)} branch-notification(s): "
                          f"{', '.join(inserted)}")
            except Exception as e:
                print(f"    [ARCHITECT-LLM] [fan-out] skipped ({type(e).__name__}: {e})")

        print(f"    [ARCHITECT-LLM] Protocol generated ({len(protocol)} chars)")

        return protocol
    
    def _extract_protocol_code(self, response: str, module_name: str) -> str:
        """
        Extract just the Scribble code from LLM response.
        Handles cases where LLM might add explanations or markdown.
        Supports multi-block responses (e.g., aux + main protocols).
        """
        # If response has code blocks, extract ALL of them and join
        code_blocks = re.findall(r'```(?:scribble)?\s*\n(.*?)```', response, re.DOTALL)
        if code_blocks:
            code = "\n\n".join(block.strip() for block in code_blocks)
        else:
            code = response.strip()
        
        # Ensure module name is correct
        if not code.startswith("module"):
            # Try to find module declaration
            module_match = re.search(r'(module\s+\w+;.*)', code, re.DOTALL)
            if module_match:
                code = module_match.group(1)
        
        # Replace any wrong module name with correct one (first occurrence only)
        code = re.sub(r'module\s+\w+;', f'module {module_name};', code, count=1)
        
        # Remove duplicate module declarations (can happen when joining multiple code blocks)
        lines = code.split('\n')
        seen_module = False
        cleaned_lines = []
        for line in lines:
            if re.match(r'^module\s+\w+;', line.strip()):
                if seen_module:
                    continue  # Skip duplicate module declarations
                seen_module = True
            cleaned_lines.append(line)
        code = '\n'.join(cleaned_lines)
        
        return code
    
    def _learn_from_error(self, error: str):
        """Learn from Scribble error and remember the lesson"""
        rule = self.rules.get_rule_for_error(error)
        if rule:
            lesson = f"[{rule['name']}] {rule['fix']}"
            if lesson not in self.learned_lessons:
                self.learned_lessons.append(lesson)
                # ASCII-only marker: Windows console default codec (cp1252)
                # can't encode book-emoji and the resulting UnicodeEncodeError
                # blows up the whole architect call. Keep it portable.
                print(f"    [ARCHITECT-LLM] [learn] {rule['name']}")
    
    def reset(self):
        """Reset attempt counter for new requirement"""
        self.attempt_count = 0
        self.last_error = None
        self.conversation_history = []
    
    def show_rules(self):
        """Display all Scribble rules known to the agent"""
        print("\n" + "="*70)
        print("SCRIBBLE PROTOCOL RULES")
        print("="*70)
        for name, rule in self.rules.RULES.items():
            print(f"\n[{name}]")
            print(f"  Description: {rule['description']}")
            print(f"  Fix: {rule['fix']}")
        print("\n" + "-"*70)
        print("CHOICE/BRANCHING RULES:")
        print(self.rules.CHOICE_RULES)
        print("="*70)
    
    def show_learned_lessons(self):
        """Display lessons learned from errors"""
        if not self.learned_lessons:
            print("\n[ARCHITECT-LLM] No lessons learned yet - no errors encountered!")
            return
        print("\n" + "="*70)
        print("LESSONS LEARNED FROM ERRORS")
        print("="*70)
        for i, lesson in enumerate(self.learned_lessons, 1):
            print(f"  {i}. {lesson}")
        print("="*70)
    
    def extract_roles(self, content: str) -> list:
        """Extract role names from protocol.
        Prefers the main (non-aux) global protocol for complete role list.
        Falls back to collecting all roles from all protocol declarations.
        """
        # First try to find a non-aux global protocol (the main one has all roles)
        main_match = re.search(
            r'(?<!aux\s)global protocol \w+\((.*?)\)', content, re.DOTALL
        )
        if main_match:
            return re.findall(r'role (\w+)', main_match.group(1))
        
        # Fallback: collect all unique roles from all protocol declarations
        all_roles = []
        for match in re.finditer(r'global protocol \w+\((.*?)\)', content, re.DOTALL):
            roles = re.findall(r'role (\w+)', match.group(1))
            for r in roles:
                if r not in all_roles:
                    all_roles.append(r)
        return all_roles
    
    def extract_messages(self, content: str) -> list:
        """Extract messages from protocol"""
        messages = []
        pattern = r'(\w+)\(\)\s+from\s+(\w+)\s+to\s+(\w+);'
        for match in re.finditer(pattern, content):
            messages.append({
                'label': match.group(1),
                'from': match.group(2),
                'to': match.group(3)
            })
        return messages

    def merge_protocols(self, versions_info: list, module_name: str) -> str:
        """
        Use LLM to merge multiple protocol versions into one optimized protocol.
        
        Args:
            versions_info: List of dicts with version, requirement, protocol_content
            module_name: The module name for the merged protocol
            
        Returns:
            Merged Scribble protocol code
        """
        print(f"    [ARCHITECT-LLM] Merging {len(versions_info)} protocol versions...")
        
        # Show what we're merging
        for v in versions_info:
            print(f"      - v{v['version']}: {v['requirement'][:50]}...")
        
        system_prompt = MERGE_SYSTEM_PROMPT
        user_prompt = get_merge_prompt(versions_info, module_name)
        
        # Call LLM
        response = self.llm.generate(system_prompt, user_prompt)
        
        # Extract protocol code
        protocol = self._extract_protocol_code(response, module_name)
        
        print(f"    [ARCHITECT-LLM] Merged protocol generated ({len(protocol)} chars)")
        
        return protocol
