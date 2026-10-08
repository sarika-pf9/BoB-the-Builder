from common import config


def patch_download_images_sh(images):
    """
    Rewrites the first-party image lines in the LOCAL CHECKOUT's
    image_builder/scripts/download_images.sh (REPO_DIR only) from the
    upstream quay.io/platform9/vjailbreak-* refs (which don't exist for a
    dev tag - we don't have push access to the real platform9 org) to your
    own quay.io/<namespace>/proj_* refs instead.

    Deliberately NOT a permanent change, and the hosted repo is never
    touched: checkout_branch()'s `git reset --hard origin/<branch>`, which
    runs at the start of every build (including the very next one), reverts
    this edit before that next build does anything else. Nothing here is
    ever committed or pushed anywhere.
    """
    path = config.REPO_DIR / "image_builder" / "scripts" / "download_images.sh"
    text = path.read_text()
    for image in images:
        old_ref = image.upstream_ref_in_download_script()
        new_ref = image.personal_ref_in_download_script()
        count = text.count(old_ref)
        assert count == 1, (
            f"expected exactly 1 occurrence of {old_ref!r} in "
            f"{path} (local checkout), found {count} - upstream may have "
            f"changed download_images.sh; update the Image definitions in "
            f"builder/components/*/build.py (or UPSTREAM_REGISTRY/REPO_PREFIX "
            f"in common/config.py) to match before retrying."
        )
        text = text.replace(old_ref, new_ref)
    path.write_text(text)
    print(
        f"[OK] patched local download_images.sh -> quay.io/{config.QUAY_NAMESPACE}/proj_* "
        f"(local checkout only - see checkout_branch(), this is discarded on the next build)"
    )
