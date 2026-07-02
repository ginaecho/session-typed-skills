"""Configuration constants for the STJP library.

All experiment artefacts (protocols, skills, runs) live under
``experiments/cases/<case>/`` — see ``experiments/CLAUDE.md``. This module
only carries paths needed to invoke the Scribble compiler. The previously
exported ``PROTOCOLS_DIR``, ``SKILLS_DIR`` and ``VERSION_HISTORY_FILE``
constants were removed on 2026-05-29 along with the legacy data directories
they pointed at.
"""

import os
from pathlib import Path

# Directory Configuration
BASE_DIR = Path(__file__).parent          # stjp_core/
REPO_ROOT = BASE_DIR.parent               # testing_ideas/

# Scribble Configuration
# Resolved RELATIVE to the repo so it survives the project being moved/renamed.
# Layout: scribble-java/scribble-dist/target/{lib/*.jar, scribblec.sh}
SCRIBBLE_PATH = REPO_ROOT / "scribble-java" / "scribble-dist" / "target"

# Java for running the Scribble compiler. Honour a real JAVA_HOME from the
# environment if set; otherwise fall back to a JDK present on this machine.
JAVA_HOME = os.environ.get("JAVA_HOME") or r"C:\Program Files\Java\jdk-17.0.18"
