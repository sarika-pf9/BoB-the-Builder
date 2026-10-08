import subprocess
import sys

from common import config
from common.shell import run


def ensure_mirror_ready():
    """Fail fast with a clear message if the one-time host setup hasn't run yet,
    instead of letting `docker push` fail deep inside the loop below."""
    check = subprocess.run(
        ["curl", "-fsS", f"http://{config.MIRROR_HOST}/v2/"],
        capture_output=True,
    )
    if check.returncode != 0:
        print(
            f"[FAIL] local mirror registry at {config.MIRROR_HOST} isn't reachable.\n"
            f"       Run host-setup/40-local-registry.sh once on this host (see that "
            f"script's header), then retry.",
            file=sys.stderr,
        )
        sys.exit(1)


def publish_local_images_to_mirror(tag, images):
    """
    Pushes each freshly-built image into the local mirror registry under
    the exact quay.io/<namespace>/proj_* ref the LOCALLY PATCHED
    download_images.sh (see patch_download_images_sh(), which must run
    before this) will actually ask for. That script's own `ctr i pull`
    then resolves it from the mirror (see host-setup/40-local-registry.sh,
    which mirrors the whole quay.io host regardless of repo path - no change
    needed there) instead of needing the real quay.io/<namespace>/proj_*
    push (Step 7) to have finished first. Nothing in the repo is read or
    written here.
    """
    ensure_mirror_ready()
    for image in images:
        personal_ref = image.personal_ref(tag)
        if image.built_as_personal_ref:
            # See push_to_personal_quay(): v2v-helper is built directly under
            # personal_ref (via the controller's V2V_IMG override, used
            # verbatim as this image's `docker build -t`), never under
            # local/platform9/v2v-helper:<tag> like the other 4 images - so
            # it's already at personal_ref, nothing to tag from a local name
            # that doesn't exist.
            local_ref = personal_ref
        else:
            local_ref = image.local_ref(tag)
            # Tagged locally too (not pushed) purely so `docker images` shows
            # the same name download_images.sh will end up resolving via the
            # mirror.
            run(["docker", "tag", local_ref, personal_ref])

        mirror_ref = image.mirror_ref(tag)
        run(["docker", "tag", local_ref, mirror_ref])
        run(["docker", "push", mirror_ref])
