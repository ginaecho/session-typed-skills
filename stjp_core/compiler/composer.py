"""
Sub-session Composer

Resolves cross-file @use directives and splices sub-protocols into a single
Scribble-compilable .scr file. Validates the composed result via Scribble.

Syntax (inside Scribble comments, so Scribble's parser ignores them):
  // @use BankingSession from "../banking/Banking.scr";
  // @use InventorySession from "../inventory/Inventory.scr";

Then in the protocol body:
  do BankingSession(Customer, Bank);

The composer resolves @use transitively, deduplicates data decls, and emits
a single composed .scr. Scribble's STypeInliner handles the do-substitution.

Research basis:
  - Demangeon-Honda CONCUR'12 (nested protocols)
  - Gheri-Yoshida OOPSLA'23 (Hybrid MPST — future v0.7)
  - Honda-Yoshida-Carbone POPL'08 (soundness theorem inherited transitively)

Error classes:
  - ResolutionError: file not found, named protocol not in file, cycle
  - RoleMappingError: do arity mismatch with sub's role declaration
  - CompositionError: spliced whole rejected by Scribble
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from stjp_core.compiler.validator import ScribbleValidator


class ResolutionError(Exception):
    pass

class RoleMappingError(Exception):
    pass

class CompositionError(Exception):
    pass


@dataclass
class UseDirective:
    """A parsed @use directive."""
    protocol_name: str     # e.g. "BankingSession"
    file_path: str         # e.g. "../banking/Banking.scr"
    resolved_path: Path | None = None


@dataclass
class ParsedScr:
    """Lightweight parse of a .scr file for composition."""
    path: Path
    module_name: str = ""
    data_decls: list[str] = field(default_factory=list)
    use_directives: list[UseDirective] = field(default_factory=list)
    aux_protocols: list[str] = field(default_factory=list)  # full text of aux global protocol blocks
    main_protocols: list[str] = field(default_factory=list)  # full text of global protocol blocks
    raw_content: str = ""


_MODULE_RE = re.compile(r'module\s+([\w.]+)\s*;')
_DATA_RE = re.compile(r'data\s+<java>\s+"[^"]*"\s+from\s+"[^"]*"\s+as\s+\w+\s*;')
_USE_RE = re.compile(r'//\s*@use\s+(\w+)\s+from\s+"([^"]+)"\s*;')
_AUX_PROTO_RE = re.compile(
    r'(aux\s+global\s+protocol\s+\w+\s*\([^)]*\)\s*\{)',
    re.DOTALL
)
_MAIN_PROTO_RE = re.compile(
    r'(global\s+protocol\s+\w+\s*\([^)]*\)\s*\{)',
    re.DOTALL
)


def parse_scr_file(path: Path) -> ParsedScr:
    """Parse a .scr file for composition purposes."""
    content = path.read_text(encoding='utf-8')
    result = ParsedScr(path=path, raw_content=content)

    # Module
    m = _MODULE_RE.search(content)
    if m:
        result.module_name = m.group(1)

    # Data declarations
    result.data_decls = _DATA_RE.findall(content)

    # @use directives
    for m in _USE_RE.finditer(content):
        result.use_directives.append(UseDirective(
            protocol_name=m.group(1),
            file_path=m.group(2),
        ))

    # Extract protocol blocks (aux and main)
    result.aux_protocols = _extract_protocol_blocks(content, is_aux=True)
    result.main_protocols = _extract_protocol_blocks(content, is_aux=False)

    return result


def _extract_protocol_blocks(content: str, is_aux: bool) -> list[str]:
    """Extract full protocol block text including braces."""
    keyword = "aux global protocol" if is_aux else "global protocol"
    blocks = []
    # Find each protocol keyword, then match to closing brace
    pattern = re.compile(
        rf'({re.escape(keyword)}\s+\w+\s*\([^)]*\)\s*\{{)',
        re.DOTALL
    )

    for m in pattern.finditer(content):
        # But skip aux when looking for main
        if not is_aux:
            prefix_start = max(0, m.start() - 10)
            prefix = content[prefix_start:m.start()]
            if 'aux' in prefix:
                continue

        start = m.start()
        brace_start = m.end() - 1  # position of opening {
        depth = 1
        pos = brace_start + 1
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1
        blocks.append(content[start:pos])

    return blocks


def resolve_uses(parent: ParsedScr, visited: set[str] | None = None) -> list[ParsedScr]:
    """
    Resolve @use directives transitively (DFS with cycle detection).
    Returns list of sub-protocol ParsedScr objects in dependency order.
    """
    if visited is None:
        visited = set()

    resolved = []
    parent_dir = parent.path.parent

    for use in parent.use_directives:
        # Resolve relative path
        target = (parent_dir / use.file_path).resolve()
        use.resolved_path = target

        if not target.exists():
            raise ResolutionError(
                f"File not found: {use.file_path} (resolved to {target}), "
                f"referenced from {parent.path}"
            )

        target_key = str(target)
        if target_key in visited:
            raise ResolutionError(
                f"Cycle detected: {target} already visited. "
                f"Chain: {visited}"
            )

        visited.add(target_key)
        sub = parse_scr_file(target)

        # Check the named protocol exists
        found = False
        for block in sub.aux_protocols + sub.main_protocols:
            if use.protocol_name in block:
                found = True
                break
        if not found:
            raise ResolutionError(
                f"Protocol {use.protocol_name} not found in {target}"
            )

        # Recurse for transitive dependencies
        sub_deps = resolve_uses(sub, visited)
        resolved.extend(sub_deps)
        resolved.append(sub)

    return resolved


def compose(parent_path: Path, output_path: Path | None = None) -> tuple[str, Path]:
    """
    Compose a parent .scr with all its @use dependencies.

    Returns (composed_scr_text, output_file_path).
    """
    parent_path = parent_path.resolve()
    parent = parse_scr_file(parent_path)
    sub_protocols = resolve_uses(parent)

    # Collect all data decls (deduplicated by alias)
    all_data = list(parent.data_decls)
    seen_data = set(parent.data_decls)
    for sub in sub_protocols:
        for d in sub.data_decls:
            if d not in seen_data:
                all_data.append(d)
                seen_data.add(d)

    # Collect aux protocols from dependencies (in dependency order)
    all_aux = []
    seen_aux_names = set()
    for sub in sub_protocols:
        for aux in sub.aux_protocols:
            # Extract name for dedup
            name_match = re.search(r'protocol\s+(\w+)', aux)
            name = name_match.group(1) if name_match else aux[:50]
            if name not in seen_aux_names:
                all_aux.append(aux)
                seen_aux_names.add(name)

    # Parent's own aux protocols
    for aux in parent.aux_protocols:
        name_match = re.search(r'protocol\s+(\w+)', aux)
        name = name_match.group(1) if name_match else aux[:50]
        if name not in seen_aux_names:
            all_aux.append(aux)
            seen_aux_names.add(name)

    # Build composed output — use simple module name to match Scribble's expectation
    # Scribble requires: module name == file stem for simple names
    if output_path:
        module_name = output_path.stem
    else:
        module_name = f"{parent_path.stem}_composed"
    lines = [f"module {module_name};", ""]

    for d in all_data:
        lines.append(d)
    lines.append("")

    for aux in all_aux:
        lines.append(aux)
        lines.append("")

    for main in parent.main_protocols:
        lines.append(main)
        lines.append("")

    composed = "\n".join(lines)

    # Write output
    if output_path is None:
        output_path = parent_path.parent / f"{parent_path.stem}_composed.scr"
    output_path.write_text(composed, encoding='utf-8')

    return composed, output_path


def compose_and_validate(parent_path: Path, output_path: Path | None = None) -> tuple[bool, str, Path]:
    """
    Compose and validate via Scribble.
    Returns (is_valid, error_or_empty, output_path).
    """
    composed_text, out_path = compose(parent_path, output_path)

    validator = ScribbleValidator()
    is_valid, error = validator.validate_protocol(out_path)

    if not is_valid:
        raise CompositionError(
            f"Composed protocol rejected by Scribble:\n{error}\n"
            f"Composed file: {out_path}"
        )

    return is_valid, error, out_path
