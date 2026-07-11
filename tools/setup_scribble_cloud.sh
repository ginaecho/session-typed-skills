#!/usr/bin/env bash
# Wire the REAL protocol toolchain into this checkout (cloud sandbox recipe).
#
# Installs/wires two backends (see docs/reference/NUSCR_CLOUD_INSTALL.md):
#   1. Scribble-java (DEFAULT validator backend) — built once from the
#      ginaecho/scribble-java fork with Maven; jars land under
#      /workspace/scribble-java/scribble-dist/target/lib.
#   2. nuscr coinductive fork (opt-in backend) — prebuilt Linux binary from
#      the fork's ci-artifacts branch at /workspace/bin/nuscr.
#
# Idempotent; safe to run in every git worktree. It wires the CURRENT
# checkout (repo root = this script's ../) to the shared /workspace build,
# so parallel worktrees reuse one Maven build.
#
# SYMLINK TRAP (why lib/ is linked, not the whole tree): validator.py runs
# the Scribble CLI with cwd=<repo>/scribble-java/scribble-dist/target and a
# RELATIVE protocol path. If that target dir were a symlink into /workspace,
# the kernel would resolve the ".." components physically and the relative
# path would escape to /workspace instead of the repo — every validation
# fails with "File couldn't be opened". So: real directories in the repo,
# symlink ONLY lib/ (no ".." ever crosses it). Verified 2026-07-11:
# 30/30 _corpus protocols pass; corrupted negative control rejected with
# "missing PROTOCOL_KW".
#
# NOTE: the JVM prints a "Picked up JAVA_TOOL_OPTIONS: ..." banner on stderr
# in this environment. validator.py's pass rule is returncode==0 AND empty
# STDOUT, so the banner is harmless — do not "fix" adapters by matching
# stderr emptiness.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SJ_SRC=/workspace/scribble-java
NUSCR_SRC=/workspace/nuscr_coinduction
NUSCR_BIN=/workspace/bin/nuscr

# -- 1. scribble-java: clone + build once (shared across worktrees) ---------
if [ ! -d "$SJ_SRC/.git" ]; then
  git clone --depth 1 https://github.com/ginaecho/scribble-java "$SJ_SRC"
fi
if [ ! -d "$SJ_SRC/scribble-dist/target/lib" ]; then
  (cd "$SJ_SRC" && mvn -q -DskipTests package)
  unzip -qo "$SJ_SRC"/scribble-dist/target/scribble-dist-*.zip \
        -d "$SJ_SRC/scribble-dist/target/"
fi

# -- 2. wire THIS checkout to the shared build (real dirs, lib symlink) -----
mkdir -p "$REPO_ROOT/scribble-java/scribble-dist/target"
ln -sfn "$SJ_SRC/scribble-dist/target/lib" \
        "$REPO_ROOT/scribble-java/scribble-dist/target/lib"

# -- 3. nuscr: prebuilt binary from the fork's ci-artifacts branch ----------
if [ ! -x "$NUSCR_BIN" ]; then
  mkdir -p /workspace/bin
  if [ ! -d "$NUSCR_SRC/.git" ]; then
    git clone --depth 1 https://github.com/ginaecho/nuscr_coinduction "$NUSCR_SRC"
  fi
  (cd "$NUSCR_SRC" && git fetch --depth 1 origin ci-artifacts \
     && git show FETCH_HEAD:dist/nuscr-linux-x86_64 > "$NUSCR_BIN")
  chmod +x "$NUSCR_BIN"
fi

# -- 4. smoke: REAL validation must pass a gold and reject a corrupt one ----
cd "$REPO_ROOT"
python3 - <<'EOF'
import sys, tempfile, os
from pathlib import Path
from stjp_core.compiler.validator import ScribbleValidator
v = ScribbleValidator()
gold = Path("experiments/cases/_corpus/corpus_000.scr")
ok, err = v.validate_protocol(gold)
assert ok, f"gold corpus_000 must validate, got: {err[:200]}"
broken = gold.read_text().replace("protocol", "protooocol", 1)
with tempfile.NamedTemporaryFile("w", suffix=".scr", delete=False,
                                 dir=gold.parent) as f:
    f.write(broken); tmp = f.name
ok2, err2 = v.validate_protocol(Path(tmp)); os.unlink(tmp)
assert not ok2, "corrupted protocol must be rejected"
print("scribble-java smoke: gold PASS, corrupt REJECTED  [real toolchain OK]")
EOF
"$NUSCR_BIN" --help >/dev/null && echo "nuscr smoke: binary runs  [OK]"
echo "export STJP_NUSCR_BIN=$NUSCR_BIN   # for the opt-in nuscr backend"
