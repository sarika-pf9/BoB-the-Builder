import subprocess
import sys

from common import config


def run(cmd, cwd=None, env=None, check=True):
    """Echoes and runs cmd (in the vjailbreak checkout unless cwd is given);
    exits the whole build with the command's own exit code on failure."""
    cwd = config.REPO_DIR if cwd is None else cwd
    print(f"+ {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if check and result.returncode != 0:
        print(f"\n[FAIL] exited {result.returncode}: {' '.join(str(c) for c in cmd)}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode


def fail(message):
    print(f"[FAIL] {message}", file=sys.stderr)
    sys.exit(1)
