"""
Skills Compiler — Main Entry Point

Runs all 3 verification passes on skills.md files against a Scribble protocol:
  Pass 1: Structural  (skills match protocol?)
  Pass 2: Security    (injection / dangerous patterns?)
  Pass 3: Completeness (all branches covered, sections present?)

Returns a CompilationResult with pass/fail status and all findings.
"""

import logging
from pathlib import Path
from dataclasses import dataclass, field

from stjp_core.compiler.protocol_parser import parse_protocol, ParsedProtocol
from stjp_core.generation.skills_parser import parse_all_skills, ParsedSkills
from stjp_core.generation.structural_checker import (
    check_structural, StructuralFinding, CheckSeverity,
    format_structural_findings
)
from stjp_core.generation.security_scanner import (
    scan_all_skills, SecurityFinding, Severity,
    format_findings as format_security_findings
)
from stjp_core.generation.completeness_checker import (
    check_completeness, CompletenessFinding, CompleteSeverity,
    format_completeness_findings
)

logger = logging.getLogger(__name__)


@dataclass
class CompilationResult:
    """Result of running the skills compiler."""
    passed: bool
    structural_findings: list[StructuralFinding] = field(default_factory=list)
    security_findings: dict[str, list[SecurityFinding]] = field(default_factory=dict)
    completeness_findings: list[CompletenessFinding] = field(default_factory=list)
    
    # Counts
    structural_errors: int = 0
    structural_warnings: int = 0
    security_critical: int = 0
    security_high: int = 0
    security_medium: int = 0
    completeness_errors: int = 0
    completeness_warnings: int = 0
    
    def summary_line(self) -> str:
        """One-line summary."""
        if self.passed:
            parts = []
            if self.structural_warnings:
                parts.append(f"{self.structural_warnings} structural warning(s)")
            if self.security_medium:
                parts.append(f"{self.security_medium} security note(s)")
            if self.completeness_warnings:
                parts.append(f"{self.completeness_warnings} completeness warning(s)")
            if parts:
                return f"PASS (with {', '.join(parts)})"
            return "PASS"
        else:
            parts = []
            if self.structural_errors:
                parts.append(f"{self.structural_errors} structural error(s)")
            if self.security_critical or self.security_high:
                parts.append(f"{self.security_critical + self.security_high} security issue(s)")
            if self.completeness_errors:
                parts.append(f"{self.completeness_errors} completeness error(s)")
            return f"FAIL ({', '.join(parts)})"
    
    def error_report_for_llm(self) -> str:
        """
        Generate an error report suitable for sending back to the LLM
        for automatic remediation.
        """
        lines = ["SKILLS COMPILER ERRORS:\n"]
        
        if self.structural_findings:
            errors = [f for f in self.structural_findings if f.severity == CheckSeverity.ERROR]
            if errors:
                lines.append("=== STRUCTURAL ERRORS ===")
                for f in errors:
                    lines.append(f"  [{f.check_name}] {f.role}: {f.description}")
                    if f.expected:
                        lines.append(f"    Expected: {f.expected}")
                    if f.actual:
                        lines.append(f"    Actual:   {f.actual}")
                lines.append("")
        
        # Only report CRITICAL and HIGH security findings
        critical_security = {}
        for fname, findings in self.security_findings.items():
            serious = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
            if serious:
                critical_security[fname] = serious
        
        if critical_security:
            lines.append("=== SECURITY ISSUES ===")
            for fname, findings in critical_security.items():
                lines.append(f"  {fname}:")
                for f in findings:
                    lines.append(f"    [{f.severity.value}] {f.pattern_name}: {f.description}")
                    lines.append(f"      Line {f.line_number}: {f.line_text[:100]}")
                    lines.append(f"      FIX: Remove or rewrite this content.")
            lines.append("")
        
        if self.completeness_findings:
            errors = [f for f in self.completeness_findings if f.severity == CompleteSeverity.ERROR]
            if errors:
                lines.append("=== COMPLETENESS ERRORS ===")
                for f in errors:
                    lines.append(f"  [{f.check_name}] {f.role}: {f.description}")
                lines.append("")
        
        lines.append("Please regenerate the skills files fixing the above issues.")
        return '\n'.join(lines)


def compile_skills(
    protocol_content: str,
    skills_dir: Path,
    fail_on_security_medium: bool = False,
    fail_on_warnings: bool = False,
) -> CompilationResult:
    """
    Run all 3 passes of the Skills Compiler.
    
    Args:
        protocol_content: The Scribble protocol text
        skills_dir: Directory containing *_skills.md files
        fail_on_security_medium: Treat MEDIUM security findings as errors
        fail_on_warnings: Treat all warnings as errors
    
    Returns:
        CompilationResult with all findings and pass/fail status
    """
    result = CompilationResult(passed=True)
    
    # Parse protocol
    protocol = parse_protocol(protocol_content)
    logger.debug(f"[SkillsCompiler] Protocol: {protocol.protocol_name}, "
                 f"Roles: {protocol.roles}, Choices: {protocol.choice_roles}")
    
    # Parse all skills files
    all_skills = parse_all_skills(skills_dir)
    logger.debug(f"[SkillsCompiler] Found {len(all_skills)} skills files: "
                 f"{list(all_skills.keys())}")
    
    # ── Pass 1: Structural ──
    print("    Pass 1 — Structural:   ", end="", flush=True)
    structural = check_structural(protocol, all_skills)
    result.structural_findings = structural
    result.structural_errors = sum(1 for f in structural if f.severity == CheckSeverity.ERROR)
    result.structural_warnings = sum(1 for f in structural if f.severity == CheckSeverity.WARNING)
    
    if result.structural_errors > 0:
        result.passed = False
        print(f"✗ {result.structural_errors} error(s), {result.structural_warnings} warning(s)")
    elif result.structural_warnings > 0:
        if fail_on_warnings:
            result.passed = False
        print(f"~ {result.structural_warnings} warning(s)")
    else:
        print("✓ all roles match, messages aligned")
    
    # ── Pass 2: Security ──
    print("    Pass 2 — Security:     ", end="", flush=True)
    security = scan_all_skills(skills_dir)
    result.security_findings = security
    
    all_sec_findings = [f for flist in security.values() for f in flist]
    result.security_critical = sum(1 for f in all_sec_findings if f.severity == Severity.CRITICAL)
    result.security_high = sum(1 for f in all_sec_findings if f.severity == Severity.HIGH)
    result.security_medium = sum(1 for f in all_sec_findings if f.severity == Severity.MEDIUM)
    
    if result.security_critical > 0 or result.security_high > 0:
        result.passed = False
        print(f"✗ {result.security_critical} critical, {result.security_high} high, "
              f"{result.security_medium} medium")
    elif result.security_medium > 0:
        if fail_on_security_medium:
            result.passed = False
        print(f"~ {result.security_medium} medium finding(s)")
    else:
        print("✓ no injections or dangerous patterns")
    
    # ── Pass 3: Completeness ──
    print("    Pass 3 — Completeness: ", end="", flush=True)
    completeness = check_completeness(protocol, all_skills)
    result.completeness_findings = completeness
    result.completeness_errors = sum(1 for f in completeness if f.severity == CompleteSeverity.ERROR)
    result.completeness_warnings = sum(1 for f in completeness if f.severity == CompleteSeverity.WARNING)
    
    if result.completeness_errors > 0:
        result.passed = False
        print(f"✗ {result.completeness_errors} error(s), {result.completeness_warnings} warning(s)")
    elif result.completeness_warnings > 0:
        if fail_on_warnings:
            result.passed = False
        print(f"~ {result.completeness_warnings} warning(s)")
    else:
        print("✓ all branches covered, sections present")
    
    return result


def print_detailed_report(result: CompilationResult):
    """Print a detailed report of all findings."""
    if result.structural_findings:
        print("\n  — Structural Details —")
        print(format_structural_findings(result.structural_findings))
    
    if result.security_findings:
        print("\n  — Security Details —")
        for fname, findings in result.security_findings.items():
            print(format_security_findings(findings, fname))
    
    if result.completeness_findings:
        print("\n  — Completeness Details —")
        print(format_completeness_findings(result.completeness_findings))
