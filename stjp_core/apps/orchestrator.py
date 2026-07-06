"""
LLM-Powered Closed-Loop Orchestrator for Session-Typed Multi-Agent Systems
===========================================================================

This system uses Azure OpenAI to:
1. Generate Scribble protocols from natural language requirements
2. Fix protocols when Scribble compiler returns errors
3. Generate skills.md files for each role

Outputs go under ``experiments/cases/<case_id>/`` so protocols authored here
are immediately drivable by the 8-arm benchmark in
``experiments/scripts/case_runner.py``. The legacy ``stjp_core/protocols/``
and ``stjp_core/skills/`` directories were retired on 2026-05-29.

Usage:
    # Login to Azure first:
    az login

    # Interactive mode (authors into experiments/cases/<case_id>/):
    python -m stjp_core.apps.orchestrator --case <case_id>

    # Automated demo:
    python -m stjp_core.apps.orchestrator --case <case_id> --auto

If ``<case_id>`` does not yet have a case dir under ``experiments/cases/``,
one is created on first run (just the protocols/ and skills/ subdirs; the
matching case.yaml still has to be authored by hand).

Modules:
    config.py           - Configuration settings (paths to Scribble / Java)
    llm_client.py       - Azure OpenAI API client
    prompts.py          - System prompts for LLM
    rules.py            - Scribble protocol rules knowledge base
    validator.py        - Scribble compiler interface
    version_control.py  - Protocol version tracking
    skills_generator.py - LLM-powered skills.md generator
    architect.py        - LLM-powered Architect Agent
    evolution_loop.py   - Main closed-loop orchestration
"""

import sys
import os
import logging
from pathlib import Path

# --- bootstrap: make 'stjp_core' importable when run directly ---
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
# --- end bootstrap ---

# REPO_ROOT is two parents up: stjp_core/apps/orchestrator.py -> testing_ideas/
REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_ROOT = REPO_ROOT / "experiments" / "cases"


def check_azure_config():
    """Check if Azure OpenAI is configured in .env"""
    from dotenv import load_dotenv
    # Canonical .env lives at stjp_core/.env (this file is stjp_core/apps/).
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
    
    if not os.environ.get("AZURE_OPENAI_ENDPOINT"):
        print("""
╔══════════════════════════════════════════════════════════════════════╗
║  ERROR: Azure OpenAI not configured!                                 ║
╠══════════════════════════════════════════════════════════════════════╣
║  1. Create a .env file in the stjp_core folder with:                 ║
║     AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/    ║
║     AZURE_OPENAI_DEPLOYMENT=gpt-4o                                   ║
║                                                                      ║
║  2. Login to Azure:                                                  ║
║     az login                                                         ║
╚══════════════════════════════════════════════════════════════════════╝
""")
        return False
    return True


def select_protocol_at_startup(loop):
    """
    Ask user which protocol to work on at startup.
    Returns True if ready to proceed, False if user wants to quit.
    """
    protocols = loop.version_control.data["protocols"]
    active_id = loop.version_control.get_active_protocol_id()
    
    # No protocols exist yet
    if not protocols:
        print("\n[INFO] No existing protocols found. Let's create your first one!")
        loop.start_new_protocol()
        return True
    
    # Show current protocol info
    if active_id:
        active_proto = loop.version_control.get_protocol(active_id)
        current_ver = loop.version_control.get_current_version()
        print("\n" + "="*70)
        print("CURRENT PROTOCOL")
        print("="*70)
        print(f"  #{active_id}: {active_proto['name']}")
        print(f"  Description: {active_proto['description'][:60]}...")
        if current_ver:
            print(f"  Current version: v{current_ver['version']}")
        print("="*70)
    
    # Ask user what to do
    print("\nWhat would you like to do?")
    print("  [c] Continue editing the CURRENT protocol")
    print("  [s] Switch to ANOTHER existing protocol")
    print("  [n] Start a completely NEW protocol")
    print("  [q] Quit")
    
    choice = input("\nYour choice (c/s/n/q): ").strip().lower()
    
    if choice == 'q':
        return False
    
    elif choice == 'c':
        if active_id:
            proto = loop.version_control.get_protocol(active_id)
            print(f"\n[OK] Continuing with Protocol #{active_id}: {proto['name']}")
            return True
        else:
            print("\n[INFO] No active protocol. Creating new one...")
            loop.start_new_protocol()
            return True
    
    elif choice == 's':
        # Show list of all protocols
        print("\n" + "="*70)
        print("AVAILABLE PROTOCOLS")
        print("="*70)
        print(f"{'#':<4} {'Name':<25} {'Vers':<6} {'Description'}")
        print("-"*70)
        
        for pid, proto in sorted(protocols.items(), key=lambda x: int(x[0])):
            active_marker = " *" if pid == active_id else ""
            name = proto['name'][:22] + "..." if len(proto['name']) > 25 else proto['name']
            vers = f"v0-v{len(proto['versions'])-1}" if proto['versions'] else "none"
            desc = proto['description'][:35] + "..." if len(proto['description']) > 38 else proto['description']
            print(f"{pid:<4}{active_marker} {name:<25} {vers:<6} {desc}")
        
        print("-"*70)
        print("* = currently active")
        print("="*70)
        
        pid_choice = input("\nEnter protocol # to switch to: ").strip()
        if pid_choice in protocols:
            loop.version_control.set_active_protocol(pid_choice)
            proto = loop.version_control.get_protocol(pid_choice)
            print(f"\n[OK] Switched to Protocol #{pid_choice}: {proto['name']}")
        else:
            print(f"\n[ERROR] Protocol #{pid_choice} not found. Staying with current.")
        return True
    
    elif choice == 'n':
        loop.start_new_protocol()
        return True
    
    else:
        print("\n[INFO] Invalid choice. Continuing with current protocol.")
        return True


def _resolve_case_dir(case_id: str) -> Path:
    """Resolve --case <case_id> to an absolute path under experiments/cases/.

    Creates the dir on demand so a brand-new authoring session can start
    without a pre-existing case.yaml. The benchmark runner still needs that
    file before it can drive the case, but the protocol/skills authoring
    loop can run first and produce the artefacts that case.yaml references.
    """
    case_dir = CASES_ROOT / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def run_interactive_demo(case_dir: Path):
    """Run the interactive LLM-powered demonstration scoped to ``case_dir``."""

    if not check_azure_config():
        return

    # Import after config check
    from stjp_core.authoring.evolution_loop import ProtocolEvolutionLoop
    
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║     LLM-POWERED PROTOCOL EVOLUTION SYSTEM                            ║
║     Session-Typed Multi-Agent Orchestration with Azure OpenAI        ║
╠══════════════════════════════════════════════════════════════════════╣
║  This system uses Azure OpenAI to:                                   ║
║  1. Generate Scribble protocols from your natural language input     ║
║  2. Fix protocols when Scribble compiler finds errors                ║
║  3. Generate skills.md files for each role                           ║
║  4. Track versions with rollback support                             ║
╚══════════════════════════════════════════════════════════════════════╝
""")
    
    # Initialize — scoped to experiments/cases/<case_id>/
    loop = ProtocolEvolutionLoop(case_dir=case_dir)
    
    # Ask user which protocol to work on
    if not select_protocol_at_startup(loop):
        print("\nGoodbye!")
        return
    
    # Interactive loop
    print("\n" + "="*70)
    print("INTERACTIVE MODE - Powered by Azure OpenAI")
    print("="*70)
    print("Commands:")
    print("  <requirement>       - Create/evolve protocol (any natural language!)")
    print("  --- Protocol Management ---")
    print("  new                 - Start a NEW protocol track")
    print("  switch              - Switch to another protocol")
    print("  protocols           - List all protocols")
    print("  --- Version Management ---")
    print("  show                - Display current protocol")
    print("  versions            - List all versions of active protocol")
    print("  reqs                - Show all versions with their requirements")
    print("  version <n>         - Show details of version n")
    print("  rollback <n>        - Rollback to version n")
    print("  diff <a> <b>        - Compare version a and b")
    print("  merge <a> <b> ...   - MERGE versions into one optimized protocol")
    print("  --- Other ---")
    print("  history             - Show evolution history")
    print("  rules               - Show Scribble rules")
    print("  lessons             - Show lessons learned from errors")
    print("  clear               - Delete ALL protocols, skills & history")
    print("  quit                - Exit")
    print("="*70)
    print("\nExamples:")
    print('  "Create a chat protocol between client and server"')
    print('  merge 6 7 8         - Merge versions 6, 7, 8 into one protocol')
    print("="*70)
    
    while True:
        try:
            user_input = input("\n>> ").strip()
            
            if not user_input:
                continue
            
            # Parse commands
            # Single-word commands must be EXACT matches (to avoid
            # confusing "New compliance rule..." with the "new" command)
            parts = user_input.split()
            cmd = parts[0].lower()
            exact = user_input.strip().lower()  # full input, lowered
            
            # Commands that take arguments use cmd (first word)
            # Commands that are single words use exact (full input)
            
            if exact in ('quit', 'exit'):
                print("\nGoodbye!")
                break
            
            elif exact == 'new':
                loop.start_new_protocol()
            
            elif exact == 'switch':
                # Show nice listing and let user pick
                protocols = loop.version_control.data["protocols"]
                active_id = loop.version_control.get_active_protocol_id()
                
                if not protocols:
                    print("\n[INFO] No protocols exist. Use 'new' to create one.")
                    continue
                
                print("\n" + "="*70)
                print("AVAILABLE PROTOCOLS")
                print("="*70)
                print(f"{'#':<4} {'Name':<25} {'Vers':<6} {'Description'}")
                print("-"*70)
                
                for pid, proto in sorted(protocols.items(), key=lambda x: int(x[0])):
                    active_marker = " *" if pid == active_id else ""
                    name = proto['name'][:22] + "..." if len(proto['name']) > 25 else proto['name']
                    vers = f"v0-v{len(proto['versions'])-1}" if proto['versions'] else "none"
                    desc = proto['description'][:35] + "..." if len(proto['description']) > 38 else proto['description']
                    print(f"{pid:<4}{active_marker} {name:<25} {vers:<6} {desc}")
                
                print("-"*70)
                print("* = currently active")
                print("="*70)
                
                pid_choice = input("\nEnter protocol # to switch to: ").strip()
                if pid_choice in protocols:
                    loop.version_control.set_active_protocol(pid_choice)
                    switched_proto = loop.version_control.get_protocol(pid_choice)
                    if switched_proto:
                        print(f"\n[OK] Switched to Protocol #{pid_choice}: {switched_proto['name']}")
                else:
                    print(f"\n[ERROR] Protocol #{pid_choice} not found.")
            
            elif exact == 'protocols':
                loop.show_protocols()
            
            elif exact == 'show':
                loop.show_current_protocol()
            
            elif exact == 'versions':
                loop.show_versions()
            
            elif cmd == 'version':
                if len(parts) > 1:
                    try:
                        version_num = int(parts[1])
                        loop.show_version_details(version_num)
                    except ValueError:
                        print("Usage: version <number>")
                else:
                    loop.show_versions()
            
            elif cmd == 'rollback':
                if len(parts) > 1:
                    try:
                        version_num = int(parts[1])
                        loop.rollback(version_num)
                        loop.show_current_protocol()
                    except ValueError:
                        print("Usage: rollback <number>")
                else:
                    print("Usage: rollback <number>")
            
            elif cmd == 'diff':
                if len(parts) > 2:
                    try:
                        v1 = int(parts[1])
                        v2 = int(parts[2])
                        loop.diff_versions(v1, v2)
                    except ValueError:
                        print("Usage: diff <version1> <version2>")
                else:
                    print("Usage: diff <version1> <version2>")
            
            elif cmd == 'merge':
                if len(parts) > 2:
                    try:
                        version_nums = [int(p) for p in parts[1:]]
                        loop.merge_versions(version_nums)
                    except ValueError:
                        print("Usage: merge <v1> <v2> [v3 ...]  (e.g., merge 6 7 8)")
                else:
                    print("Usage: merge <v1> <v2> [v3 ...]  (e.g., merge 6 7 8)")
                    print("       Use 'reqs' to see all versions with their requirements")
            
            elif exact == 'reqs':
                loop.show_version_requirements()
            
            elif exact == 'history':
                loop.show_history()
            
            elif exact == 'rules':
                loop.architect.show_rules()
            
            elif exact == 'lessons':
                loop.architect.show_learned_lessons()
            
            elif exact == 'clear':
                loop.clear_all()
            
            else:
                # Treat as a natural language requirement
                loop.process_requirement(user_input)
            
        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n[ERROR]: {e}")
            import traceback
            traceback.print_exc()


def run_automated_demo(case_dir: Path):
    """Run an automated demonstration scoped to ``case_dir``."""

    if not check_azure_config():
        return

    from stjp_core.authoring.evolution_loop import ProtocolEvolutionLoop
    
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║     AUTOMATED DEMO: LLM-Powered Protocol Evolution                   ║
╚══════════════════════════════════════════════════════════════════════╝
""")
    
    loop = ProtocolEvolutionLoop(case_dir=case_dir)

    # Demo requirements to process
    requirements = [
        "Create a simple chat protocol between a Client and Server where client sends messages and server responds",
        "Add a Logger role that receives copies of all messages for audit purposes",
    ]

    for i, req in enumerate(requirements, 1):
        print(f"\n{'='*70}")
        print(f"DEMO {i}/{len(requirements)}")
        print(f"{'='*70}")
        loop.process_requirement(req)

        print("\n[DEMO] Current Protocol:")
        loop.show_current_protocol()

        print("\n[DEMO] Generated Skills Files:")
        for f in loop.skills_dir.iterdir():
            if f.suffix == '.md':
                print(f"  - {f.name}")
    
    print("\n" + "="*70)
    print("DEMO COMPLETE!")
    print("="*70)
    loop.show_versions()


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """Parse ``--case <case_id> [--auto]`` from argv. Returns (case_id, auto)."""
    auto = "--auto" in argv
    argv = [a for a in argv if a != "--auto"]
    case_id = None
    if "--case" in argv:
        i = argv.index("--case")
        if i + 1 < len(argv):
            case_id = argv[i + 1]
    if not case_id:
        print("usage: python -m stjp_core.apps.orchestrator --case <case_id> [--auto]")
        print(f"  cases dir: {CASES_ROOT}")
        sys.exit(2)
    return case_id, auto


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
    )
    case_id, auto = _parse_args(sys.argv[1:])
    case_dir = _resolve_case_dir(case_id)
    print(f"[orchestrator] case_dir = {case_dir}")
    if auto:
        run_automated_demo(case_dir)
    else:
        run_interactive_demo(case_dir)
