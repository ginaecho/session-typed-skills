"""
Protocol Evolution Loop (LLM-Powered, Multi-Protocol)

The main closed-loop system that orchestrates:
1. User chooses: NEW protocol or CONTINUE existing protocol
2. LLM generates Scribble protocol from natural language
3. Scribble compiler validates
4. If error → LLM fixes based on error message
5. If valid → LLM generates skills.md files
6. Version control tracks all changes per protocol
"""

import re
from pathlib import Path
from datetime import datetime

from stjp_core.compiler.validator import ScribbleValidator
from stjp_core.authoring.architect import ArchitectAgent
from stjp_core.generation.skills_generator import SkillsGenerator
from stjp_core.generation.skills_compiler import compile_skills, print_detailed_report
from stjp_core.authoring.version_control import VersionControl

# All authoring artefacts (protocols, skills, version history) live under a
# caller-supplied ``case_dir`` — typically ``experiments/cases/<case_id>/``.
# The legacy module-level self.protocols_dir / self.skills_dir constants from
# stjp_core.config were removed on 2026-05-29.


class ProtocolEvolutionLoop:
    """
    The main closed-loop system using LLM for protocol generation.
    Supports multiple protocols, each with independent version histories.
    
    Flow:
    1. User selects: new protocol or continue existing
    2. User provides natural language requirement
    3. ArchitectAgent (LLM) generates Scribble protocol
    4. ScribbleValidator checks with compiler
    5. If error: ArchitectAgent (LLM) fixes it
    6. If valid: SkillsGenerator (LLM) creates role skills
    7. VersionControl tracks the evolution per protocol
    """
    
    def __init__(self, case_dir: Path):
        """Initialize the evolution loop scoped to a single case directory.

        ``case_dir`` is typically ``experiments/cases/<case_id>/``. Three
        subpaths are derived from it (and created on demand):

          - ``<case_dir>/protocols/``  for generated ``.scr`` files
          - ``<case_dir>/skills/``     for generated per-role ``_skills.md``
          - ``<case_dir>/.version_history.json``  for the multi-protocol
            version-control log

        These names match the layout used by the benchmark in
        ``experiments/scripts/case_runner.py`` so a protocol authored here
        is immediately drivable by the 8-arm matrix.
        """
        print(f"[INIT] Initializing LLM-powered orchestrator (case_dir={case_dir})")
        self.case_dir = Path(case_dir)
        self.protocols_dir = self.case_dir / "protocols"
        self.skills_dir = self.case_dir / "skills"
        self.history_file = self.case_dir / ".version_history.json"
        self.protocols_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.validator = ScribbleValidator()
        self.architect = ArchitectAgent()
        self.skills_generator = SkillsGenerator(self.skills_dir)
        self.version_control = VersionControl(history_file=self.history_file)
        self.history = []
        self.skills_compiler_max_retries = 2  # LLM retries for skills fixes
        print("[INIT] All components initialized")

    def _get_current_protocol_path(self) -> Path:
        """Get the path to the current protocol file"""
        current = self.version_control.get_current_version()
        if current:
            return Path(current['protocol_file'])
        return self.protocols_dir / "Protocol.scr"
    
    def _get_current_protocol_content(self) -> str:
        """Get the current protocol content"""
        current = self.version_control.get_current_version()
        if current:
            return current.get('protocol_content', '')
        return ""
    
    def start_new_protocol(self, name: str = None, description: str = None,
                           auto_generate: bool = True) -> str:
        """
        Start a completely new protocol track.
        If auto_generate is True, immediately uses the description as the first
        requirement to generate a protocol + skills.
        Returns the protocol ID.
        """
        if not name:
            name = input("Protocol name (e.g., 'ChatProtocol'): ").strip()
        if not description:
            description = input("Brief description (this will be used to generate the protocol): ").strip()
        
        protocol_id = self.version_control.create_new_protocol(name, description)
        
        # Automatically generate the first protocol version from the description
        if auto_generate and description:
            print(f"\n[INFO] Generating initial protocol from your description...")
            self.process_requirement(description)
        
        return protocol_id
    
    def continue_protocol(self, protocol_id: str = None) -> bool:
        """
        Continue working on an existing protocol.
        If protocol_id not provided, will prompt user to select.
        """
        if not protocol_id:
            print(self.version_control.list_protocols())
            protocol_id = input("Enter protocol # to continue: ").strip()
        
        return self.version_control.set_active_protocol(protocol_id)
    
    def select_protocol_mode(self) -> str:
        """
        Interactive selection: new protocol or continue existing.
        Returns the active protocol ID.
        """
        existing = self.version_control.data["protocols"]
        
        if not existing:
            print("\n[INFO] No existing protocols. Creating new one...")
            return self.start_new_protocol()
        
        print("\n" + "="*70)
        print("PROTOCOL SELECTION")
        print("="*70)
        print("  [n] Start a NEW protocol")
        print("  [c] CONTINUE an existing protocol")
        print("="*70)
        
        choice = input("Your choice (n/c): ").strip().lower()
        
        if choice == 'n':
            return self.start_new_protocol()
        elif choice == 'c':
            self.continue_protocol()
            return self.version_control.get_active_protocol_id()
        else:
            print("Invalid choice. Starting new protocol...")
            return self.start_new_protocol()
    
    def process_requirement(self, user_requirement: str, max_attempts: int = 5) -> bool:
        """
        Process a user requirement through the LLM-powered closed loop.
        
        Args:
            user_requirement: Natural language description of what the user wants
            max_attempts: Maximum LLM attempts before giving up
            
        Returns:
            True if successful, False if max attempts exceeded
        """
        # Ensure we have an active protocol
        protocol_id = self.version_control.get_active_protocol_id()
        if not protocol_id:
            print("\n[INFO] No active protocol. Please select or create one first.")
            protocol_id = self.select_protocol_mode()
        
        proto = self.version_control.get_protocol(protocol_id)
        current_version = len(proto["versions"])  # Next version number (0-indexed)
        
        print("\n" + "="*70)
        print(f"[PROTOCOL #{protocol_id}] {proto['name']}")
        print(f"[NEW REQUIREMENT]: {user_requirement}")
        print(f"[WILL CREATE]: v{current_version}")
        print("="*70)
        
        self.architect.reset()
        
        # If continuing, load the previous protocol as base + build accumulated description
        base_protocol = None
        accumulated_requirement = user_requirement
        if proto["versions"]:
            previous_version = proto["versions"][-1]
            base_protocol = previous_version.get('protocol_content', '')
            
            # Build accumulated requirement from all previous versions + new requirement
            prev_requirements = []
            for v in proto["versions"]:
                prev_requirements.append(f"[v{v['version']}]: {v['requirement']}")
            prev_requirements.append(f"[v{current_version} NEW]: {user_requirement}")
            accumulated_requirement = "\n".join(prev_requirements)
            
            print(f"[INFO] Evolving from v{previous_version['version']} (carrying forward context)")
        
        error = None
        previous_protocol = None  # Used for fix-loop retries
        
        for attempt in range(1, max_attempts + 1):
            print(f"\n{'-'*50}")
            print(f"[ATTEMPT {attempt}/{max_attempts}]")
            print(f"{'-'*50}")
            
            # Step 1: LLM generates/fixes protocol
            print("\n[1] LLM ARCHITECT AGENT...")
            
            # Module name format: P{protocol_id}_v{version}
            module_name = f"P{protocol_id}_v{current_version}"
            
            draft = self.architect.draft_protocol(
                requirement=user_requirement,
                module_name=module_name,
                previous_protocol=previous_protocol,
                previous_error=error,
                base_protocol=base_protocol,
                accumulated_requirement=accumulated_requirement
            )
            
            # Save draft to file
            draft_path = self.protocols_dir / f"{module_name}.scr"
            draft_path.write_text(draft, encoding='utf-8')
            print(f"    -> Saved to: {draft_path.name}")
            
            # Step 2: Validate with Scribble compiler
            print("\n[2] SCRIBBLE COMPILER VALIDATION...")
            is_valid, error = self.validator.validate_protocol(draft_path)
            
            if is_valid:
                print("    SCRIBBLE SAYS: (silence) = VALID!")

                # Step 2.5: Critic — cross-message policy check (only when a
                # .policy sidecar exists for this case). A Critic failure is
                # treated exactly like a Scribble error: the findings go back
                # to the Architect LLM as the error to fix.
                critic_error = self._run_critic_gate(draft_path)
                if critic_error:
                    print(f"    CRITIC SAYS: POLICY VIOLATION\n{critic_error}")
                    print("    -> LLM will analyze the Critic findings and fix...")
                    error = critic_error
                    previous_protocol = draft
                    if draft_path.exists():
                        draft_path.unlink()
                    continue

                # Step 3: LLM generates skills (pass user_requirement for business rules!)
                print("\n[3] LLM GENERATING SKILLS FILES...")
                self.skills_generator.generate_all_skills(draft, module_name, user_requirement)
                
                # Step 4: Skills Compiler verification
                print("\n[4] SKILLS COMPILER VERIFICATION...")
                compilation = compile_skills(draft, self.skills_dir)
                
                if not compilation.passed:
                    print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                    print_detailed_report(compilation)
                    
                    # Retry: send errors back to LLM to regenerate skills
                    skills_fixed = False
                    for retry in range(1, self.skills_compiler_max_retries + 1):
                        print(f"\n    [SKILLS FIX {retry}/{self.skills_compiler_max_retries}] "
                              f"Sending errors to LLM for skills remediation...")
                        error_report = compilation.error_report_for_llm()
                        self.skills_generator.generate_all_skills(
                            draft, module_name, 
                            user_requirement + "\n\n" + error_report
                        )
                        print(f"    [SKILLS RECHECK {retry}/{self.skills_compiler_max_retries}]")
                        compilation = compile_skills(draft, self.skills_dir)
                        if compilation.passed:
                            skills_fixed = True
                            break
                        else:
                            print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                            print_detailed_report(compilation)
                    
                    if not skills_fixed:
                        print(f"\n    ⚠️  Skills compiler found issues but proceeding with commit.")
                        print(f"    Review the skills files manually.")
                
                print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                
                # Step 5: Commit to version control
                print("\n[5] COMMITTING TO VERSION CONTROL...")
                roles = self.architect.extract_roles(draft)
                messages = self.architect.extract_messages(draft)
                pid, version = self.version_control.commit(
                    draft_path, user_requirement, roles, messages, attempt, protocol_id
                )
                
                # Log success
                self.history.append({
                    'timestamp': datetime.now().isoformat(),
                    'protocol_id': protocol_id,
                    'version': version,
                    'requirement': user_requirement,
                    'attempts': attempt,
                    'protocol_file': str(draft_path),
                    'status': 'SUCCESS'
                })
                
                print("\n" + "="*70)
                print("PROTOCOL EVOLUTION COMPLETE!")
                print(f"   Protocol: #{protocol_id} '{proto['name']}'")
                print(f"   Version:  v{version}")
                print(f"   File:     {draft_path}")
                print(f"   Attempts: {attempt}")
                print("="*70)
                
                return True
            else:
                print(f"    SCRIBBLE ERROR: {error}")
                print("    -> LLM will analyze error and fix...")
                
                # Keep the failed protocol for LLM to fix
                previous_protocol = draft
                
                # Clean up invalid file
                if draft_path.exists():
                    draft_path.unlink()
                
        # Max attempts exceeded
        print("\n" + "="*70)
        print(f"FAILED after {max_attempts} attempts")
        print(f"   Last error: {error}")
        print("="*70)
        
        self.history.append({
            'timestamp': datetime.now().isoformat(),
            'protocol_id': protocol_id,
            'requirement': user_requirement,
            'attempts': max_attempts,
            'status': 'FAILED',
            'last_error': error
        })
        
        return False
    
    def _run_critic_gate(self, draft_path: Path) -> str:
        """Run the static Critic on a Scribble-valid draft.

        Looks for a `.policy` sidecar (``<stem>.policy`` next to the draft, or
        ``policies.policy`` in the protocols dir). Returns "" when there is
        nothing to check or all policies hold; otherwise the findings formatted
        as LLM feedback (so the fix loop treats them like a compiler error).
        """
        from stjp_core.critic.policies import find_policy_file, parse_policy_file
        from stjp_core.critic.critic import run_static_critic

        policy_path = find_policy_file(draft_path)
        if policy_path is None:
            return ""
        try:
            policies = parse_policy_file(policy_path)
            print(f"\n[2.5] CRITIC — {len(policies)} cross-message "
                  f"policy(ies) from {policy_path.name}...")
            report = run_static_critic(draft_path, policies)
            print(f"    {report.summary_line()}")
            if report.passed:
                return ""
            return report.as_llm_feedback()
        except Exception as e:
            # The Critic must never brick the authoring loop on a malformed
            # sidecar — surface the problem and continue without the gate.
            print(f"    CRITIC SKIPPED (error: {e})")
            return ""

    def rollback(self, version_num: int) -> bool:
        """Rollback to a specific version within the active protocol"""
        protocol_id = self.version_control.get_active_protocol_id()
        if not protocol_id:
            print("No active protocol!")
            return False
        
        print(f"\n[ROLLBACK] Protocol #{protocol_id} rolling back to v{version_num}...")
        return self.version_control.rollback(
            version_num, self.protocols_dir, self.skills_generator
        )
    
    def show_current_protocol(self):
        """Display the current protocol"""
        protocol_id = self.version_control.get_active_protocol_id()
        if not protocol_id:
            print("\n(No active protocol - use 'new' or 'switch' command)")
            return
        
        proto = self.version_control.get_protocol(protocol_id)
        current = self.version_control.get_current_version()
        
        print(f"\n[CURRENT PROTOCOL #{protocol_id}]: {proto['name']}")
        print("-"*50)
        
        if current:
            print(f"Version: v{current['version']}")
            print(f"File:    {current['protocol_file']}")
            print("-"*50)
            print(current.get('protocol_content', '(No content)'))
        else:
            print("(No versions yet - enter a requirement to create v0)")
        print("-"*50)
    
    def show_history(self):
        """Display evolution history"""
        print("\n[EVOLUTION HISTORY]:")
        print("-"*50)
        if not self.history:
            print("(No history yet)")
        for entry in self.history:
            status = "OK" if entry['status'] == 'SUCCESS' else "FAILED"
            pid = entry.get('protocol_id', '?')
            ver = entry.get('version', '?')
            print(f"{status} [P#{pid} v{ver}] {entry['timestamp'][:19]} {entry['requirement'][:30]}...")
            print(f"   Attempts: {entry['attempts']}")
        print("-"*50)
    
    def show_protocols(self):
        """Display all protocols"""
        print(self.version_control.list_protocols())
    
    def show_versions(self, protocol_id: str = None):
        """Display version control history for a protocol"""
        print(self.version_control.list_versions(protocol_id))
    
    def show_version_details(self, version_num: int):
        """Display details of a specific version"""
        print(self.version_control.show_version_details(version_num))
    
    def diff_versions(self, v1: int, v2: int):
        """Show diff between two versions"""
        print(self.version_control.diff_versions(v1, v2))

    def merge_versions(self, version_numbers: list, max_attempts: int = 5) -> bool:
        """
        Merge multiple protocol versions into one optimized protocol using LLM.
        
        Args:
            version_numbers: List of version numbers to merge (e.g., [6, 7, 8])
            max_attempts: Maximum LLM attempts for validation
            
        Returns:
            True if successful, False otherwise
        """
        protocol_id = self.version_control.get_active_protocol_id()
        if not protocol_id:
            print("\n[ERROR] No active protocol. Select one first.")
            return False
        
        proto = self.version_control.get_protocol(protocol_id)
        
        print("\n" + "="*70)
        print(f"MERGE PROTOCOL VERSIONS - Protocol #{protocol_id}: {proto['name']}")
        print("="*70)
        
        # Gather version info
        versions_info = []
        print("\n[VERSIONS TO MERGE]:")
        print("-"*70)
        print(f"{'Ver':<6} {'Requirement'}")
        print("-"*70)
        
        for v_num in version_numbers:
            v = self.version_control.get_version(v_num, protocol_id)
            if not v:
                print(f"[ERROR] Version {v_num} not found!")
                return False
            
            versions_info.append({
                'version': v_num,
                'requirement': v['requirement'],
                'protocol_content': v['protocol_content']
            })
            req_short = v['requirement'][:55] + "..." if len(v['requirement']) > 58 else v['requirement']
            print(f"v{v_num:<5} {req_short}")
        
        print("-"*70)
        
        # Determine new version number
        new_version = len(proto["versions"])
        module_name = f"P{protocol_id}_v{new_version}"
        
        print(f"\n[TARGET]: Will create v{new_version} as merged result")
        
        self.architect.reset()
        error = None
        previous_protocol = None
        
        for attempt in range(1, max_attempts + 1):
            print(f"\n{'-'*50}")
            print(f"[MERGE ATTEMPT {attempt}/{max_attempts}]")
            print(f"{'-'*50}")
            
            # Step 1: LLM merges (or fixes) protocol
            print("\n[1] LLM MERGING PROTOCOLS...")
            
            if previous_protocol and error:
                # Fix mode
                from stjp_core.authoring.prompts import SCRIBBLE_FIX_PROMPT, get_protocol_fix_prompt
                
                combined_req = " + ".join([f"v{v['version']}: {v['requirement'][:30]}" for v in versions_info])
                draft = self.architect.draft_protocol(
                    requirement=f"MERGED: {combined_req}",
                    module_name=module_name,
                    previous_protocol=previous_protocol,
                    previous_error=error
                )
            else:
                # Fresh merge
                draft = self.architect.merge_protocols(versions_info, module_name)
            
            # Save draft
            draft_path = self.protocols_dir / f"{module_name}.scr"
            draft_path.write_text(draft, encoding='utf-8')
            print(f"    -> Saved to: {draft_path.name}")
            
            # Step 2: Validate
            print("\n[2] SCRIBBLE COMPILER VALIDATION...")
            is_valid, error = self.validator.validate_protocol(draft_path)
            
            if is_valid:
                print("    SCRIBBLE SAYS: VALID!")
                
                # Combine all requirements for skills generation (to capture all business rules)
                combined_requirement = "\n\n".join([
                    f"[v{v['version']}]: {v['requirement']}" for v in versions_info
                ])
                
                # Step 3: Generate skills (pass combined requirements!)
                print("\n[3] LLM GENERATING SKILLS FILES...")
                self.skills_generator.generate_all_skills(draft, module_name, combined_requirement)
                
                # Step 4: Skills Compiler verification
                print("\n[4] SKILLS COMPILER VERIFICATION...")
                compilation = compile_skills(draft, self.skills_dir)
                
                if not compilation.passed:
                    print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                    print_detailed_report(compilation)
                    
                    skills_fixed = False
                    for retry in range(1, self.skills_compiler_max_retries + 1):
                        print(f"\n    [SKILLS FIX {retry}/{self.skills_compiler_max_retries}] "
                              f"Sending errors to LLM for skills remediation...")
                        error_report = compilation.error_report_for_llm()
                        self.skills_generator.generate_all_skills(
                            draft, module_name,
                            combined_requirement + "\n\n" + error_report
                        )
                        print(f"    [SKILLS RECHECK {retry}/{self.skills_compiler_max_retries}]")
                        compilation = compile_skills(draft, self.skills_dir)
                        if compilation.passed:
                            skills_fixed = True
                            break
                        else:
                            print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                            print_detailed_report(compilation)
                    
                    if not skills_fixed:
                        print(f"\n    ⚠️  Skills compiler found issues but proceeding with commit.")
                        print(f"    Review the skills files manually.")
                
                print(f"    SKILLS COMPILER SAYS: {compilation.summary_line()}")
                
                # Step 5: Commit
                print("\n[5] COMMITTING MERGED VERSION...")
                roles = self.architect.extract_roles(draft)
                messages = self.architect.extract_messages(draft)
                
                merged_requirement = f"[MERGED v{', v'.join(map(str, version_numbers))}] " + \
                                    " | ".join([v['requirement'][:40] for v in versions_info])
                
                pid, version = self.version_control.commit(
                    draft_path, merged_requirement, roles, messages, attempt, protocol_id
                )
                
                print("\n" + "="*70)
                print("MERGE COMPLETE!")
                print(f"   Protocol: #{protocol_id} '{proto['name']}'")
                print(f"   Merged:   v{', v'.join(map(str, version_numbers))} → v{version}")
                print(f"   File:     {draft_path}")
                print(f"   Attempts: {attempt}")
                print("="*70)
                
                return True
            else:
                print(f"    SCRIBBLE ERROR: {error}")
                print("    -> LLM will fix and retry...")
                previous_protocol = draft
                
                if draft_path.exists():
                    draft_path.unlink()
        
        print("\n" + "="*70)
        print(f"MERGE FAILED after {max_attempts} attempts")
        print(f"   Last error: {error}")
        print("="*70)
        return False

    def show_version_requirements(self):
        """Show all versions with their requirements for the active protocol"""
        protocol_id = self.version_control.get_active_protocol_id()
        if not protocol_id:
            print("\n[ERROR] No active protocol.")
            return
        
        proto = self.version_control.get_protocol(protocol_id)
        
        print("\n" + "="*70)
        print(f"VERSION REQUIREMENTS - Protocol #{protocol_id}: {proto['name']}")
        print("="*70)
        print(f"{'Ver':<6} {'Timestamp':<20} {'Requirement'}")
        print("-"*70)
        
        for v in proto["versions"]:
            timestamp = v['timestamp'][:19]
            req = v['requirement']
            print(f"v{v['version']:<5} {timestamp:<20} {req[:45]}...")
            if len(req) > 45:
                # Print continuation
                remaining = req[45:]
                while remaining:
                    print(f"{'':6} {'':20} {remaining[:45]}...")
                    remaining = remaining[45:]
        
        print("-"*70)
        print(f"Total: {len(proto['versions'])} versions")
        print("="*70)
    
    def clear_all(self):
        """
        Clear ALL protocols, skills, and version history.
        Deletes all .scr files in protocols/, all .md files in skills/,
        and resets version_history.json.
        """
        confirm = input("\n⚠️  This will DELETE all protocols, skills, and version history. Type 'yes' to confirm: ").strip().lower()
        if confirm != 'yes':
            print("[CANCELLED] Nothing was deleted.")
            return
        
        # Delete all .scr files in protocols/
        deleted_protocols = 0
        for f in self.protocols_dir.iterdir():
            if f.suffix == '.scr':
                f.unlink()
                deleted_protocols += 1
        
        # Delete all .md files in skills/
        deleted_skills = 0
        for f in self.skills_dir.iterdir():
            if f.suffix == '.md':
                f.unlink()
                deleted_skills += 1
        
        # Reset version history
        self.version_control.data = {"protocols": {}, "active_protocol_id": None}
        self.version_control._save_history()
        
        # Reset in-memory state
        self.history = []
        self.architect.reset()
        self.architect.learned_lessons = []
        
        print(f"\n[CLEARED] Deleted {deleted_protocols} protocol files, {deleted_skills} skills files.")
        print("[CLEARED] Version history reset. Ready for a fresh start!")
