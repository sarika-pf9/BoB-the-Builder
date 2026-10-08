import json
import sys
from pathlib import Path

from common import config
from common.shell import fail, run


def require_quay_namespace():
    """Every build needs it: the controller bakes quay.io/<namespace>/... into
    its binary (see components/controller/build.py) even without --push."""
    if not config.QUAY_NAMESPACE:
        fail(
            "BOB_QUAY_NAMESPACE is not set. Put your quay.io namespace in bob.env "
            "(see config/bob.env.example), or export BOB_QUAY_NAMESPACE=<name>."
        )


def push_to_personal_quay(tag, images):
    """
    Pushes the given images' freshly-built LOCAL copies to your own quay.io
    namespace, into the per-image repos you already created there.

    For a single component build this only runs via --push (opt-in). For
    `qcow2` this is Step 7 and always runs - see cli.py for why it's no
    longer optional there.
    """
    for image in images:
        personal_ref = image.personal_ref(tag)
        if not image.built_as_personal_ref:
            run(["docker", "tag", image.local_ref(tag), personal_ref])
        # else: v2v-helper is NOT built under local/platform9/<repo>:<tag> like
        # the other 4 images - the controller's V2V_IMG override IS this exact
        # personal_ref, and the top-level Makefile uses V2V_IMG directly as
        # the v2v-helper image's own `docker build -t`, so it's already tagged
        # this way. Re-tagging from the local/platform9/v2v-helper name would
        # fail with "No such image" - that name was never created.
        run(["docker", "push", personal_ref])
        print(f"[OK] pushed {personal_ref}")


def ensure_quay_login():
    """Fail fast, before spending 20+ minutes on a qcow2 build, if this host
    clearly hasn't done `docker login quay.io` yet. Step 7 (push to your
    personal quay repos) is mandatory for `qcow2` - the manifests baked into
    the qcow2, and the v2v-helper reference compiled into the controller
    binary, both point directly at quay.io/<namespace>/proj_* (see cli.py),
    so a failed push there means the qcow2 you just built ships with
    dangling image references.

    This is a best-effort local check, not a real login test - docker may
    keep credentials in a helper (macOS keychain, secretservice, etc.)
    instead of this file, which we can't inspect from here. It only
    hard-fails on the unambiguous case (no docker config at all, and no
    credential helper configured either), so it won't false-block a host
    that's actually logged in via a helper.
    """
    config_path = Path.home() / ".docker" / "config.json"
    if not config_path.exists():
        _fail_no_quay_login()
        return
    try:
        docker_config = json.loads(config_path.read_text())
    except Exception:
        print(f"[*] couldn't parse {config_path} - skipping the docker-login pre-check.")
        return
    auths = docker_config.get("auths", {})
    if any("quay.io" in host for host in auths):
        return
    if docker_config.get("credsStore") or docker_config.get("credHelpers"):
        # Credentials may be in a helper we can't inspect from here - don't
        # false-block, just proceed and let the real push fail if it must.
        return
    _fail_no_quay_login()


def _fail_no_quay_login():
    print(
        f"[FAIL] this qcow2 build pushes to quay.io/{config.QUAY_NAMESPACE}/proj_* "
        f"as a mandatory step (Step 7) - the manifests baked into the qcow2, and the "
        f"v2v-helper reference compiled into the controller binary, both reference "
        f"those repos directly (see cli.py). Run `docker login quay.io` on "
        f"this host first, then retry.",
        file=sys.stderr,
    )
    sys.exit(1)
