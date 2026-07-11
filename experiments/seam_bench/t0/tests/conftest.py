"""conftest.py — make `experiments.seam_bench.t0.*` importable regardless of
how pytest was invoked (same PEP 420 namespace-package pattern as
experiments/seam_bench/eval/tests/conftest.py)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
