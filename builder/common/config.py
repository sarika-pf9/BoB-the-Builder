"""
Settings for build_system, read once at import.

Precedence (highest first):
    1. a real environment variable (e.g. `BOB_QUAY_NAMESPACE=x build_system ...`)
    2. bob.env - one directory above builder/, i.e. $BOB_DIR/bob.env on an
       installed host, or <repo root>/bob.env when run from a checkout.
       Override the path with BOB_CONFIG=/path/to/bob.env.
    3. the defaults below

bob.env is plain KEY=VALUE lines (the same file the dashboard's systemd unit
loads via EnvironmentFile=, and the shell scripts `source`), so one file
configures every piece. See config/bob.env.example for every key.
"""
import os
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BUILDER_DIR.parent / "bob.env"


def parse_env_file(text):
    """Parses KEY=VALUE lines. Ignores blanks, `#` comments and a leading
    `export `; strips one layer of matching single/double quotes."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(config_path=None, environ=None):
    environ = os.environ if environ is None else environ
    path = Path(config_path or environ.get("BOB_CONFIG") or DEFAULT_CONFIG_PATH)
    settings = parse_env_file(path.read_text()) if path.is_file() else {}
    settings.update({k: v for k, v in environ.items() if k.startswith("BOB_")})
    return settings


_settings = load_settings()


def get(key, default):
    value = _settings.get(key, "")
    return value if value != "" else default


# Where the vjailbreak-code checkout lives. Every build `git reset --hard`s
# and `git clean -ffdx`es it - never point this at a checkout you work in.
REPO_DIR = Path(get("BOB_VJB_REPO_DIR", "/opt/build/vjailbreak"))

# Tag namespace used only for the `make` build step itself - purely a local
# build-time label, never referenced by download_images.sh or anything that
# ships inside the qcow2.
LOCAL_REGISTRY = "local"

# What image_builder/scripts/download_images.sh hardcodes on main, BEFORE
# patch_download_images_sh() rewrites the local checkout's copy of it. Kept
# here (a) as the search target for that patch, and (b) as documentation of
# what upstream actually expects, in case this tool's assumptions ever drift
# from main. Must stay byte-for-byte in sync with the
# v2v_helper/controller/ui/vpwned/ai variables there.
UPSTREAM_REGISTRY = "quay.io"
REPO_PREFIX = "platform9"

# The local pull-through mirror set up by host-setup/40-local-registry.sh.
# Must match the address in that script and in
# /etc/containerd/certs.d/quay.io/hosts.toml on this host. Mirrors the whole
# quay.io HOST regardless of repo path, so retargeting the five images from
# platform9/... to <namespace>/proj_* needs no change to that containerd
# config - only to what path is asked for.
MIRROR_HOST = f"127.0.0.1:{get('BOB_MIRROR_PORT', '5051')}"

# Your own quay.io namespace (the per-image repos live in each component's
# build.py). Assumes `docker login quay.io` has already been done on this
# host - this tool never handles credentials itself. For `qcow2` these are
# the MANDATORY final destination (see cli.py); for a single component build
# they're only used when --push is passed.
QUAY_NAMESPACE = get("BOB_QUAY_NAMESPACE", "")

# Bootstrap version for the `go` toolchain (see components/qcow2/go_toolchain.py)
# - just needs to be new enough to run `go env`/module resolution;
# GOTOOLCHAIN defaults to "auto" so `go` itself will fetch a newer toolchain
# on the fly if k8s/migration's go.mod ever asks for one, as long as this
# host can reach proxy.golang.org. Bump this if that self-upgrade path isn't
# available (e.g. fully airgapped) and go.mod moves past it.
GO_VERSION = get("BOB_GO_VERSION", "1.23.4")
GO_INSTALL_DIR = Path.home() / ".local" / "go"
GO_DL_CACHE_DIR = Path.home() / ".cache" / "go-dl"
