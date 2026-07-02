"""
Scribble Protocol Validator

Interfaces with the Scribble compiler for protocol validation.
"""

import subprocess
import os
import logging
from pathlib import Path

from stjp_core.config import SCRIBBLE_PATH, JAVA_HOME

logger = logging.getLogger(__name__)


class ScribbleValidator:
    """Interfaces with the Scribble compiler for protocol validation"""
    
    def __init__(self):
        self.scribble_lib = SCRIBBLE_PATH / "lib"
        
    def validate_protocol(self, protocol_path: Path) -> tuple[bool, str]:
        """
        Validates a Scribble protocol file.
        Returns (is_valid, error_message)
        Scribble convention: silence = success
        """
        env = os.environ.copy()
        env["JAVA_HOME"] = JAVA_HOME
        
        # Scribble's CLI rejects a module path containing spaces ("Bad
        # module arg"). Pass it relative to cwd (SCRIBBLE_PATH) so a space
        # in the repo's parent path (e.g. "OneDrive - Microsoft") never
        # reaches Scribble.
        cmd = [
            "java", "-cp", str(self.scribble_lib / "*"),
            "org.scribble.cli.CommandLine",
            os.path.relpath(protocol_path, SCRIBBLE_PATH)
        ]

        logger.info(f"[Scribble] Validating: {protocol_path.name}")
        logger.debug(f"[Scribble] Command: {' '.join(cmd)}")
        logger.debug(f"[Scribble] CWD: {SCRIBBLE_PATH}")
        
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, env=env, cwd=str(SCRIBBLE_PATH)
            )
            
            logger.debug(f"[Scribble] Return code: {result.returncode}")
            if result.stdout.strip():
                logger.debug(f"[Scribble] stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                logger.debug(f"[Scribble] stderr: {result.stderr.strip()}")
            
            # Scribble: silence = success
            if result.returncode == 0 and not result.stdout.strip():
                logger.info(f"[Scribble] ✓ Validation PASSED: {protocol_path.name}")
                return True, ""
            else:
                error = result.stdout.strip() or result.stderr.strip()
                logger.info(f"[Scribble] ✗ Validation FAILED: {protocol_path.name}")
                logger.info(f"[Scribble]   Error: {error}")
                return False, error
                
        except Exception as e:
            logger.error(f"[Scribble] ✗ Execution error: {e}")
            return False, f"Scribble execution error: {e}"
    
    def get_projection(self, protocol_path: Path, protocol_name: str, role: str) -> str:
        """Gets the local projection for a specific role"""
        env = os.environ.copy()
        env["JAVA_HOME"] = JAVA_HOME
        
        cmd = [
            "java", "-cp", str(self.scribble_lib / "*"),
            "org.scribble.cli.CommandLine",
            os.path.relpath(protocol_path, SCRIBBLE_PATH),
            "-project", protocol_name, role
        ]
        
        logger.info(f"[Scribble] Projecting: {protocol_name} @ {role}")
        logger.debug(f"[Scribble] Command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd=str(SCRIBBLE_PATH)
        )
        
        if result.stdout.strip():
            logger.debug(f"[Scribble] Projection result:\n{result.stdout.strip()}")
        if result.stderr.strip():
            logger.debug(f"[Scribble] stderr: {result.stderr.strip()}")
        
        return result.stdout
