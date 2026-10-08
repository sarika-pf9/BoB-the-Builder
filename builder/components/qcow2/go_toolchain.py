import platform
import shutil
import sys
from pathlib import Path

from common import config
from common.shell import run


def ensure_go_available(env):
    """`make -C k8s/migration build-installer` shells out to controller-gen,
    which needs a working `go` toolchain to load/introspect the Go packages
    under k8s/migration - nothing else in this pipeline touches Go at all, so
    a fresh build host typically won't have one installed. Rather than making
    that a manual host prerequisite (breaks the "just take a branch and run
    it" goal), install it once into a user-local, outside-the-checkout
    directory, the same way PACKER_CACHE_DIR and download_images.sh's own
    virtio-win cache avoid re-fetching things `git clean -ffdx` would
    otherwise wipe on every run. No sudo, no system packages touched, no repo
    file read or written. No-op if `go` is already on PATH (you installed your
    own, or a previous run already did this).
    """
    go_install_dir = config.GO_INSTALL_DIR
    go_bin = go_install_dir / "bin"
    if shutil.which("go", path=env.get("PATH", "")):
        return
    if (go_bin / "go").exists():
        env["PATH"] = f"{go_bin}:{env.get('PATH', '')}"
        return

    machine = platform.machine()
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not arch:
        print(
            f"[FAIL] no `go` on PATH and don't know which Go release to fetch for "
            f"architecture '{machine}' - install Go yourself and make sure it's on "
            f"PATH, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    config.GO_DL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tarball = config.GO_DL_CACHE_DIR / f"go{config.GO_VERSION}.linux-{arch}.tar.gz"
    url = f"https://go.dev/dl/go{config.GO_VERSION}.linux-{arch}.tar.gz"

    if not tarball.exists():
        print(
            f"[*] no `go` on PATH - fetching Go {config.GO_VERSION} ({arch}) to "
            f"{go_install_dir} (one-time; cached at {tarball} after this)"
        )
        run(["curl", "-fsSL", "-o", str(tarball), url], cwd=Path.home())
    else:
        print(f"[*] no `go` on PATH - installing from cached {tarball}")

    go_install_dir.parent.mkdir(parents=True, exist_ok=True)
    if go_install_dir.exists():
        shutil.rmtree(go_install_dir)
    # Every official go*.tar.gz extracts to a top-level "go/" dir, so
    # extracting straight into the install dir's parent (~/.local) lands it at
    # exactly ~/.local/go - no separate rename step needed.
    run(["tar", "-C", str(go_install_dir.parent), "-xzf", str(tarball)], cwd=Path.home())

    if not (go_bin / "go").exists():
        print(
            f"[FAIL] extracting {tarball} didn't produce {go_bin}/go - it may be "
            f"corrupt; delete it and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    env["PATH"] = f"{go_bin}:{env.get('PATH', '')}"
    print(f"[OK] installed go {config.GO_VERSION} to {go_install_dir}")
