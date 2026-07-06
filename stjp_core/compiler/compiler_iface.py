"""
ProtocolCompiler — a common interface over the two protocol compilers.

STJP can drive either the vendored scribble-java compiler (default) or the
coinductive nuscr fork (via Docker). Both implement the two operations
downstream code needs:

  - ``validate(path) -> (ok, message)``
  - ``project_efsm(path, protocol_name, role) -> EFSM``

so callers can obtain the configured backend via :func:`get_compiler` and stay
agnostic to which compiler is in use. The backend is selected by
``config.COMPILER_BACKEND`` (env ``STJP_COMPILER_BACKEND``: ``scribble`` |
``nuscr``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from stjp_core import config
from stjp_core.compiler.efsm_parser import EFSM, get_efsm_from_scribble
from stjp_core.compiler.validator import ScribbleValidator


@runtime_checkable
class ProtocolCompiler(Protocol):
    """Common interface implemented by both compiler backends."""

    name: str

    def validate(self, protocol_path: Path) -> tuple[bool, str]:
        """Return (is_valid, error_message). Empty message on success."""
        ...

    def project_efsm(self, protocol_path: Path, protocol_name: str, role: str) -> EFSM:
        """Project the per-role EFSM for ``role`` in ``protocol_name``."""
        ...


class ScribbleCompiler:
    """ProtocolCompiler adapter over the vendored scribble-java backend."""

    name = "scribble"

    def __init__(self) -> None:
        self._validator = ScribbleValidator()

    def validate(self, protocol_path: Path) -> tuple[bool, str]:
        return self._validator.validate_protocol(Path(protocol_path))

    def project_efsm(self, protocol_path: Path, protocol_name: str, role: str) -> EFSM:
        return get_efsm_from_scribble(Path(protocol_path), protocol_name, role)


def get_compiler(backend: str | None = None) -> ProtocolCompiler:
    """Return the configured protocol compiler.

    ``backend`` overrides ``config.COMPILER_BACKEND`` when given.
    """
    backend = (backend or config.COMPILER_BACKEND or "scribble").lower()
    if backend == "scribble":
        return ScribbleCompiler()
    if backend == "nuscr":
        # Imported lazily so the scribble path has no hard dependency on the
        # nuscr backend (which needs Docker).
        from stjp_core.compiler.nuscr_compiler import NuscrCompiler

        return NuscrCompiler()
    raise ValueError(
        f"Unknown STJP_COMPILER_BACKEND {backend!r} (expected 'scribble' or 'nuscr')"
    )
