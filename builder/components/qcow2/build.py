"""qcow2: the full vJailbreak appliance disk image.

Builds all 5 first-party images, patches the local download_images.sh copy
(discarded on the next build - see common/git.py), then hands it + the local
mirror + your personal quay repos to vjailbreak's standard packer/qemu
pipeline.
"""
import os
from pathlib import Path

from common import config
from common.git import sanitize
from common.quay import ensure_quay_login, push_to_personal_quay
from common.shell import fail, run
from components import ALL_IMAGES, build_component
from components.qcow2.download_images import patch_download_images_sh
from components.qcow2.manifests import render_controller_manifests, render_early_manifests
from components.qcow2.mirror import publish_local_images_to_mirror

NAME = "qcow2"
DESCRIPTION = (
    "full local qcow2 image: builds all 5 images above, patches the local "
    "download_images.sh copy, publishes images to the local mirror + your "
    "personal quay repos, then runs the (locally patched, otherwise standard) "
    "download_images.sh + packer/qemu pipeline"
)

# Order the image components are built in. The last one's sha becomes the tag.
BUILD_ORDER = ("ui", "sdk", "ai", "controller")

# Distinctive, greppable marker printed once the qcow2 exists on disk. The
# dashboard (dashboard/app.py) watches for it - don't change it without
# changing that too.
QCOW2_READY_MARKER = "[QCOW2_READY]"


def build_qcow2(branch):
    repo_dir = config.REPO_DIR
    if not repo_dir.is_dir():
        fail(f"{repo_dir} doesn't exist - clone the repo there first (host-setup/30-vjailbreak-checkout.sh).")

    ensure_quay_login()

    print("=== Step 1/7: building all 5 first-party images ===")
    sha = None
    for component in BUILD_ORDER:
        sha = build_component(component, branch)  # push=False here - pushed once, in Step 7, for all 5 at once
    tag = f"{sanitize(branch)}-{sha}"

    print("\n=== Step 2/7: patching local download_images.sh copy (discarded on next build) ===")
    patch_download_images_sh(ALL_IMAGES)

    env = os.environ.copy()
    env["REGISTRY"] = config.LOCAL_REGISTRY
    env["TAG"] = tag
    # Packer's own ISO cache defaults to <cwd>/packer_cache when unset - and
    # packer below runs with cwd=REPO_DIR, i.e. INSIDE this checkout, which
    # checkout_branch()'s `git clean -ffdx` wipes on every run. Without this,
    # the multi-hundred-MB Ubuntu cloud image (ubuntu_cloud_url in
    # vjailbreak-image.pkr.hcl) re-downloads on every single qcow2 build.
    # Same idea as download_images.sh's own VIRTIO_CACHE_DIR, just for packer.
    packer_cache_dir = Path.home() / ".cache" / "packer"
    packer_cache_dir.mkdir(parents=True, exist_ok=True)
    env["PACKER_CACHE_DIR"] = str(packer_cache_dir)

    print("\n=== Step 3/7: rendering manifests (pre-image-stage) ===")
    render_early_manifests(tag, env)

    print("\n=== Step 4/7: staging images for the guest (local mirror) ===")
    publish_local_images_to_mirror(tag, ALL_IMAGES)
    run(["bash", "image_builder/scripts/download_images.sh", tag], env=env)

    print("\n=== Step 5/7: generating controller manifests ===")
    render_controller_manifests(env)

    print("\n=== Step 6/7: packer build ===")
    run(["packer", "init", "image_builder/vjailbreak-image.pkr.hcl"], env=env)
    run(["packer", "validate", "image_builder/vjailbreak-image.pkr.hcl"], env=env)
    run(["packer", "build", "image_builder/vjailbreak-image.pkr.hcl"], env=env)

    qcow2_path = repo_dir / "vjailbreak_qcow2" / "vjailbreak-image.qcow2"
    print(f"\n[OK] qcow2 built from '{branch}' @ {sha}  (tag: {tag})")
    print(f"--- output ---\n{qcow2_path}")
    # The qcow2 file exists and is complete as of this line. A caller tailing
    # this output (the dashboard) can use it to make the artifact downloadable
    # immediately, without waiting on Step 7 below - the quay.io push is a
    # separate, comparatively slow network operation that has nothing to do
    # with whether the qcow2 disk image itself is ready, and there's no reason
    # the person who just wants the disk image should be stuck waiting on 5
    # image uploads.
    print(f"{QCOW2_READY_MARKER} {qcow2_path}")

    # Step 7 is deliberately LAST and only reachable after the qcow2 already
    # exists - this used to run right after Step 1 (before packer even
    # started) when it was --push-gated, which meant a --push qcow2 build
    # always waited for all 5 quay.io uploads to finish before the packer
    # build could even begin. Doing it here instead means the qcow2 build's
    # own wall-clock time is never affected by it, and the artifact is ready
    # to serve/download the moment Step 6 finishes. It's mandatory (see
    # cli.py) - not gated behind any flag.
    print("\n=== Step 7/7: pushing component images to quay.io (mandatory - see cli.py) ===")
    push_to_personal_quay(tag, ALL_IMAGES)
