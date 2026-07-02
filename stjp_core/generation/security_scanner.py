"""
Security Scanner (Pass 2)

Static analysis of skills.md files for injection, dangerous patterns,
and prompt manipulation — grounded in OWASP Top 10 for LLM Applications (2025).

Categories:
  A — Markdown injection / hidden instructions  (LLM01 + LLM05)
  B — Dangerous action patterns                  (LLM06)
  C — Prompt injection patterns                  (LLM01)

Each finding is a SecurityFinding with severity (CRITICAL / HIGH / MEDIUM / LOW).
"""

import re
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class SecurityFinding:
    """A single security finding."""
    category: str          # "A", "B", or "C"
    pattern_name: str      # short identifier
    severity: Severity
    description: str       # human-readable explanation
    line_number: int       # 1-based
    line_text: str         # the offending line
    file_path: str = ""    # which file


# ──────────────────────────────────────────────
# Category A: Markdown Injection / Hidden Instructions
# ──────────────────────────────────────────────

_CATEGORY_A_PATTERNS = [
    {
        'name': 'HTML_COMMENT',
        'pattern': re.compile(r'<!--.*?-->', re.DOTALL),
        'severity': Severity.CRITICAL,
        'description': 'HTML comment detected — can hide instructions invisible in rendered Markdown',
        'multiline': True,
    },
    {
        'name': 'JS_LINK',
        'pattern': re.compile(r'\[.*?\]\(javascript:', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'JavaScript link in Markdown — potential code execution',
    },
    {
        'name': 'DATA_LINK',
        'pattern': re.compile(r'\[.*?\]\(data:', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Data URI link — potential data exfiltration via encoded payload',
    },
    {
        'name': 'TRACKING_IMAGE',
        'pattern': re.compile(r'!\[.*?\]\(https?://.*?\?.*?=', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'Image with query parameters — potential tracking/exfiltration',
    },
    {
        'name': 'ZERO_WIDTH_CHAR',
        'pattern': re.compile(r'[\u200b\u200c\u200d\u2060\ufeff\u00ad]'),
        'severity': Severity.HIGH,
        'description': 'Zero-width/invisible character — can hide text from humans but LLMs parse it',
    },
    {
        'name': 'HTML_TAG',
        'pattern': re.compile(r'<(script|iframe|object|embed|form|input|style|link|meta)\b', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'HTML tag that could execute code or load external resources',
    },
]

# ──────────────────────────────────────────────
# Category B: Dangerous Action Patterns
# ──────────────────────────────────────────────

_CATEGORY_B_PATTERNS = [
    {
        'name': 'SHELL_EXECUTION',
        'pattern': re.compile(r'\b(exec|eval|subprocess|os\.system|os\.popen|bash|sh\s+-c|powershell|cmd\s+/c)\b', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Shell/code execution command',
    },
    {
        'name': 'NETWORK_CALL',
        'pattern': re.compile(r'\b(curl|wget|fetch|requests\.get|requests\.post|urllib|httpx)\b', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Network call command — potential arbitrary HTTP request',
    },
    {
        'name': 'EXTERNAL_URL',
        'pattern': re.compile(r'https?://(?!example\.com|www\.example\.com|your-resource)', re.IGNORECASE),
        'severity': Severity.MEDIUM,
        'description': 'External URL reference — review for legitimacy',
    },
    {
        'name': 'FILE_OPERATION',
        'pattern': re.compile(r'\b(open\(|write\(|unlink|os\.remove|shutil\.rmtree|rm\s+-rf|del\s+/)\b', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'File system operation — potential destructive action',
    },
    {
        'name': 'CREDENTIAL_EXPOSURE',
        'pattern': re.compile(r'\b(api[_-]?key|secret[_-]?key|password|Bearer\s+|Authorization:\s*|token\s*=\s*["\'])', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Credential or secret pattern — potential sensitive data exposure (OWASP LLM02)',
    },
    {
        'name': 'CODE_IMPORT',
        'pattern': re.compile(r'\b(import\s+\w|from\s+\w+\s+import|require\()', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'Code import/require statement — potential code execution',
    },
    {
        'name': 'SQL_INJECTION',
        'pattern': re.compile(r'\b(DROP\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|INSERT\s+INTO|TRUNCATE)\b', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'SQL injection payload',
    },
    {
        'name': 'BASE64_BLOB',
        'pattern': re.compile(r'[A-Za-z0-9+/]{80,}={0,2}'),
        'severity': Severity.MEDIUM,
        'description': 'Large Base64-encoded block — potential obfuscated payload',
    },
    {
        'name': 'PIPE_TO_SHELL',
        'pattern': re.compile(r'(curl|wget)\s+.*\|\s*(bash|sh|python|perl)', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Pipe download to shell execution — classic attack vector',
    },
]

# ──────────────────────────────────────────────
# Category C: Prompt Injection Patterns
# ──────────────────────────────────────────────

_CATEGORY_C_PATTERNS = [
    {
        'name': 'INSTRUCTION_OVERRIDE',
        'pattern': re.compile(r'(ignore\s+(all\s+)?previous\s+instructions|forget\s+(your\s+)?rules|disregard\s+(the\s+)?(above|prior))', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Direct prompt override attempt',
    },
    {
        'name': 'ROLE_HIJACK',
        'pattern': re.compile(r'(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you\s+are)\b', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'Role hijacking attempt',
    },
    {
        'name': 'CONCEALMENT',
        'pattern': re.compile(r'(do\s+not\s+tell\s+the\s+user|keep\s+this\s+secret|don\'t\s+mention|hide\s+this)', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'Concealment instruction — attempts to hide behavior from user',
    },
    {
        'name': 'FAKE_DELIMITER',
        'pattern': re.compile(r'(SYSTEM:|<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|<\|system\|>|<\|user\|>|<\|assistant\|>)', re.IGNORECASE),
        'severity': Severity.CRITICAL,
        'description': 'Fake system prompt delimiter — attempts to inject system-level instructions',
    },
    {
        'name': 'SECRET_INSTRUCTION',
        'pattern': re.compile(r'(secret\s+instructions?|hidden\s+instructions?|do\s+not\s+reveal|internal\s+prompt)', re.IGNORECASE),
        'severity': Severity.HIGH,
        'description': 'Reference to secret/hidden instructions',
    },
]


def scan_file(path: Path) -> list[SecurityFinding]:
    """
    Scan a single skills.md file for security issues.
    Returns list of SecurityFinding objects.
    """
    text = path.read_text(encoding='utf-8')
    findings = []
    
    # First, check multiline patterns (HTML comments that span lines)
    for spec in _CATEGORY_A_PATTERNS:
        if spec.get('multiline'):
            for match in spec['pattern'].finditer(text):
                # Find line number of the match start
                line_num = text[:match.start()].count('\n') + 1
                matched_text = match.group()
                # Truncate display if very long
                display = matched_text[:120] + '...' if len(matched_text) > 120 else matched_text
                findings.append(SecurityFinding(
                    category='A',
                    pattern_name=spec['name'],
                    severity=spec['severity'],
                    description=spec['description'],
                    line_number=line_num,
                    line_text=display,
                    file_path=str(path),
                ))
    
    # Line-by-line scanning for remaining patterns
    lines = text.splitlines()
    for line_idx, line in enumerate(lines, start=1):
        # Category A (non-multiline)
        for spec in _CATEGORY_A_PATTERNS:
            if spec.get('multiline'):
                continue
            if spec['pattern'].search(line):
                findings.append(SecurityFinding(
                    category='A',
                    pattern_name=spec['name'],
                    severity=spec['severity'],
                    description=spec['description'],
                    line_number=line_idx,
                    line_text=line.strip(),
                    file_path=str(path),
                ))
        
        # Category B
        for spec in _CATEGORY_B_PATTERNS:
            if spec['pattern'].search(line):
                findings.append(SecurityFinding(
                    category='B',
                    pattern_name=spec['name'],
                    severity=spec['severity'],
                    description=spec['description'],
                    line_number=line_idx,
                    line_text=line.strip(),
                    file_path=str(path),
                ))
        
        # Category C
        for spec in _CATEGORY_C_PATTERNS:
            if spec['pattern'].search(line):
                findings.append(SecurityFinding(
                    category='C',
                    pattern_name=spec['name'],
                    severity=spec['severity'],
                    description=spec['description'],
                    line_number=line_idx,
                    line_text=line.strip(),
                    file_path=str(path),
                ))
    
    return findings


def scan_all_skills(skills_dir: Path) -> dict[str, list[SecurityFinding]]:
    """
    Scan all *_skills.md files in a directory.
    Returns dict mapping filename -> list of findings.
    """
    results = {}
    for path in sorted(skills_dir.glob('*_skills.md')):
        findings = scan_file(path)
        if findings:
            results[path.name] = findings
    return results


def format_findings(findings: list[SecurityFinding], file_name: str = "") -> str:
    """Format findings for human-readable output."""
    if not findings:
        return ""
    
    lines = []
    if file_name:
        lines.append(f"\n  {file_name}:")
    
    for f in findings:
        icon = "🔴" if f.severity == Severity.CRITICAL else \
               "🟠" if f.severity == Severity.HIGH else \
               "🟡" if f.severity == Severity.MEDIUM else "⚪"
        lines.append(f"    {icon} [{f.severity.value}] {f.pattern_name} (Cat {f.category}) line {f.line_number}")
        lines.append(f"       {f.description}")
        lines.append(f"       > {f.line_text[:100]}")
    
    return '\n'.join(lines)
