import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MINING_DIR = HERE.parent
REPO_ROOT = MINING_DIR.parents[2]
for p in (str(REPO_ROOT), str(MINING_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
