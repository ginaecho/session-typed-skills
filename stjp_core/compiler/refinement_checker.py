"""
Refinement Checker

Parses .refn sidecar files and evaluates payload predicates at runtime.
Predicates are sandboxed Python expressions over a bound variable `x`.

Message-payload format:
  [Sender -> Receiver : Label]
  type: int
  require: x > 0
  require: x <= 1000000

Choice-point format (value-dependent internal choice — the rule that decides
WHICH branch a role must take, given values it has already seen). Predicates
range over previously observed message payloads, bound by their label name:
  [choice at RevenueAnalyst]
  when: float(RawRevenueData) > 50000
  require: HighRevenueNotification
  over: StandardRevenueNotification

Semantics (enforced by stjp_core/monitor/monitor.py with value tracking):
  - when TRUE  and the role sends a label in `over`    -> choice_guard_violation
  - when TRUE  and the role sends `require`            -> OK
  - when FALSE and the role sends `require`            -> choice_guard_violation
    (only if `over` is non-empty — i.e. an alternative existed)
  - predicate not yet evaluable (value not seen)       -> guard is skipped

Research basis:
  - Bocchi et al. CONCUR'10 (asserted MPST — assertions at choice points)
  - Bocchi, Chen, Demangeon, Honda, Yoshida FORTE'13 (monitored session types)
  - "Specifying Stateful Asynchronous Properties for Distributed Programs"
    (stateful assertions over previously received values)
  - Zhou et al. OOPSLA'20 (refinement-typed payloads)
  - Das & Pfenning CONCUR'20 (undecidability of full arithmetic refinements)
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


class RefinementViolation(Exception):
    """Raised when a payload fails the refinement predicate at the call site."""


SAFE_BUILTINS = {
    'len': len, 'abs': abs, 'min': min, 'max': max,
    'int': int, 'float': float, 'str': str, 'bool': bool,
    'isinstance': isinstance, 'True': True, 'False': False, 'None': None,
}


def _matches(pattern: str, s: str) -> bool:
    return re.fullmatch(pattern, s) is not None

def _startswith(s: str, prefix: str) -> bool:
    return isinstance(s, str) and s.startswith(prefix)

def _endswith(s: str, suffix: str) -> bool:
    return isinstance(s, str) and s.endswith(suffix)

def _contains(s: str, sub: str) -> bool:
    return isinstance(s, str) and sub in s


SAFE_HELPERS = {
    'matches': _matches,
    'startswith': _startswith,
    'endswith': _endswith,
    'contains': _contains,
}

# AST nodes allowed in predicates
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Constant, ast.Name, ast.Load, ast.IfExp,
    ast.Tuple, ast.List, ast.Attribute,
    # Operators
    ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot,
)

# Safe string methods allowed on `x`
_SAFE_ATTRS = {'lower', 'upper', 'strip', 'lstrip', 'rstrip', 'startswith', 'endswith', 'replace'}


def _validate_ast(node: ast.AST) -> bool:
    """Reject anything outside the safe subset."""
    if isinstance(node, ast.Attribute):
        # Allow only safe string method calls on the bound variable
        if node.attr not in _SAFE_ATTRS:
            return False
    elif not isinstance(node, _ALLOWED_NODES):
        return False
    for child in ast.iter_child_nodes(node):
        if not _validate_ast(child):
            return False
    return True


@dataclass
class Refinement:
    """Refinement contract for one message."""
    sender: str
    receiver: str
    label: str
    declared_type: str = ""
    predicates: list[str] = field(default_factory=list)

    def check(self, payload_str: str) -> tuple[bool, str]:
        """Evaluate all predicates against a payload value. Returns (ok, error)."""
        # Type coercion
        try:
            if self.declared_type == 'int':
                x = int(payload_str)
            elif self.declared_type == 'float':
                x = float(payload_str)
            elif self.declared_type == 'bool':
                x = payload_str.lower() in ('true', '1', 'yes')
            else:
                x = payload_str
        except (ValueError, TypeError) as e:
            return False, f"type error: expected {self.declared_type}, got {payload_str!r}: {e}"

        env = {'x': x, **SAFE_BUILTINS, **SAFE_HELPERS, '__builtins__': {}}
        for pred in self.predicates:
            try:
                tree = ast.parse(pred, mode='eval')
                if not _validate_ast(tree):
                    return False, f"unsafe predicate: {pred}"
                result = eval(compile(tree, '<refn>', 'eval'), env)
                if not result:
                    return False, f"predicate failed: {pred} (x={x!r})"
            except Exception as e:
                return False, f"predicate error: {pred}: {e}"
        return True, ""

    def __str__(self):
        parts = [f"[{self.sender} -> {self.receiver} : {self.label}]"]
        if self.declared_type:
            parts.append(f"type: {self.declared_type}")
        for p in self.predicates:
            parts.append(f"require: {p}")
        return "\n".join(parts)


@dataclass
class ChoiceGuard:
    """Value-dependent choice rule for one role's internal choice.

    `when` is a sandboxed predicate over previously observed payloads,
    referenced by message label (e.g. ``float(RawRevenueData) > 50000``).
    If it evaluates True the role MUST take `require`; sending any label in
    `over` instead is a choice_guard_violation (and vice versa when False).
    """
    role: str
    when: str = ""
    require: str = ""
    over: list[str] = field(default_factory=list)

    def evaluate(self, values: dict[str, str]) -> bool | None:
        """Evaluate `when` against observed payloads. None = not evaluable yet
        (a referenced label has not been observed) or unsafe/failed predicate."""
        if not self.when:
            return None
        try:
            tree = ast.parse(self.when, mode='eval')
        except SyntaxError:
            return None
        if not _validate_ast(tree):
            return None
        # Which bare names does the predicate need (beyond builtins/helpers)?
        known = set(SAFE_BUILTINS) | set(SAFE_HELPERS)
        needed = {n.id for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id not in known}
        if not needed.issubset(values.keys()):
            return None  # value(s) not observed yet — guard not active
        env = {**SAFE_BUILTINS, **SAFE_HELPERS,
               **{k: values[k] for k in needed}, '__builtins__': {}}
        try:
            return bool(eval(compile(tree, '<refn-choice>', 'eval'), env))
        except Exception:
            return None

    def __str__(self):
        alt = f" (instead of {', '.join(self.over)})" if self.over else ""
        return (f"[choice at {self.role}] when {self.when} "
                f"require {self.require}{alt}")


def choice_guards_for(refinements: dict, role: str) -> list["ChoiceGuard"]:
    """Extract the ChoiceGuards for one role from a parsed .refn dict."""
    return [g for k, g in refinements.items()
            if isinstance(g, ChoiceGuard) and g.role == role]


# Parser for .refn files
_HEADER_RE = re.compile(r'\[\s*(\w+)\s*->\s*(\w+)\s*:\s*(\w+)\s*\]')
_CHOICE_HEADER_RE = re.compile(r'\[\s*choice\s+at\s+(\w+)\s*\]', re.IGNORECASE)


def parse_refn_file(path: Path) -> dict[tuple[str, str, str], Refinement]:
    """Parse a .refn file. Returns dict keyed by (sender, receiver, label)."""
    text = path.read_text(encoding='utf-8')
    return parse_refn_text(text)


def parse_refn_text(text: str) -> dict:
    """Parse refinement text content.

    Returns a dict containing both kinds of entries:
      (sender, receiver, label) -> Refinement          (payload guards)
      ('__choice__', role, idx) -> ChoiceGuard          (choice-point guards)
    The special first element '__choice__' can never collide with a real
    sender role name, so existing ``refinements.get((s, r, label))`` callers
    are unaffected. Use ``choice_guards_for(refinements, role)`` to extract
    the guards for one role.
    """
    refinements: dict = {}
    current: Refinement | None = None
    current_choice: ChoiceGuard | None = None
    n_choice = 0

    def _flush():
        nonlocal current, current_choice, n_choice
        if current:
            refinements[(current.sender, current.receiver, current.label)] = current
            current = None
        if current_choice:
            refinements[('__choice__', current_choice.role, n_choice)] = current_choice
            n_choice += 1
            current_choice = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        choice_header = _CHOICE_HEADER_RE.match(line)
        if choice_header:
            _flush()
            current_choice = ChoiceGuard(role=choice_header.group(1))
            continue

        header = _HEADER_RE.match(line)
        if header:
            _flush()
            current = Refinement(
                sender=header.group(1),
                receiver=header.group(2),
                label=header.group(3),
            )
            continue

        if current_choice is not None:
            if line.startswith('when:'):
                current_choice.when = line.split(':', 1)[1].strip()
            elif line.startswith('require:'):
                current_choice.require = line.split(':', 1)[1].strip()
            elif line.startswith('over:'):
                current_choice.over = [s.strip() for s in
                                       line.split(':', 1)[1].split(',') if s.strip()]
            continue

        if current is None:
            continue

        if line.startswith('type:'):
            current.declared_type = line.split(':', 1)[1].strip()
        elif line.startswith('require:'):
            current.predicates.append(line.split(':', 1)[1].strip())

    _flush()
    return refinements


def load_refinements_for_protocol(protocol_path: Path) -> dict[tuple[str, str, str], Refinement]:
    """Auto-discover sibling .refn file for a .scr file."""
    refn_path = protocol_path.with_suffix('.refn')
    if refn_path.exists():
        return parse_refn_file(refn_path)
    return {}
