import re
import subprocess

from common import config
from common.shell import run


def sanitize(branch):
    """Branch name -> something usable inside a docker tag."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", branch)


def checkout_branch(branch):
    """Hard-resets the checkout to origin/<branch>; returns the short sha."""
    print(f"=== Checking out '{branch}' (clean) ===")
    run(["git", "fetch", "origin", branch])
    # --force guards against any stray local modification blocking the
    # checkout - including, deliberately, patch_download_images_sh()'s own
    # edit from a previous qcow2 build. `git reset --hard` (not `git clean`)
    # is what actually discards that edit: clean only removes untracked
    # files, reset is what reverts a tracked file like download_images.sh
    # back to the real, unmodified upstream content, every single run.
    run(["git", "checkout", "--force", "-B", branch, f"origin/{branch}"])
    run(["git", "reset", "--hard", f"origin/{branch}"])
    run(["git", "clean", "-ffdx"])
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=config.REPO_DIR, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return sha
