"""conftest.py — make `experiments.seam_bench.eval.*` importable regardless
of how pytest was invoked (repo root has no top-level __init__.py; every
package under `experiments/` is a PEP 420 namespace package, same pattern as
the rest of this repo's `experiments/scripts/*.py`)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
