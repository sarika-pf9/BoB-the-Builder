import subprocess
from dataclasses import dataclass

from common import config


@dataclass(frozen=True)
class Image:
    """One first-party vjailbreak container image.

    local_repo is what the Makefile target actually tags locally (REGISTRY/TAG
    overridden, REPO left at its "platform9" default) - verified against real
    build output, not assumed. upstream_repo is what
    image_builder/scripts/download_images.sh asks for on main, BEFORE
    patch_download_images_sh() rewrites the local copy. These are the SAME
    string for every image except v2v-helper: the top-level Makefile's own
    default for it is "v2v-helper" (no "vjailbreak-" prefix), while
    download_images.sh (on main) pulls "vjailbreak-v2v-helper". Keep both
    names explicit - conflating them silently 404s later in `ctr`, with no
    hint it's a naming mismatch.
    """
    key: str
    local_repo: str
    upstream_repo: str
    default_quay_repo: str
    # True when the Makefile builds this image directly under personal_ref()
    # (v2v-helper, via the controller's V2V_IMG override) instead of under
    # local_ref() - there is then no local name to re-tag from.
    built_as_personal_ref: bool = False

    @property
    def quay_repo(self):
        """Your own quay.io repo for this image. Override per image in bob.env
        with BOB_QUAY_REPO_<KEY>, e.g. BOB_QUAY_REPO_V2V_HELPER."""
        return config.get(f"BOB_QUAY_REPO_{self.key.upper()}", self.default_quay_repo)

    def local_ref(self, tag):
        return f"{config.LOCAL_REGISTRY}/platform9/{self.local_repo}:{tag}"

    def personal_ref(self, tag):
        return f"{config.UPSTREAM_REGISTRY}/{config.QUAY_NAMESPACE}/{self.quay_repo}:{tag}"

    def mirror_ref(self, tag):
        return f"{config.MIRROR_HOST}/{config.QUAY_NAMESPACE}/{self.quay_repo}:{tag}"

    def upstream_ref_in_download_script(self):
        return f"{config.UPSTREAM_REGISTRY}/{config.REPO_PREFIX}/{self.upstream_repo}:$TAG"

    def personal_ref_in_download_script(self):
        return f"{config.UPSTREAM_REGISTRY}/{config.QUAY_NAMESPACE}/{self.quay_repo}:$TAG"


def show_images_for_tag(tag):
    print("--- resulting local images ---")
    subprocess.run(
        ["bash", "-c", f"docker images | head -1; docker images | grep -- '{tag}' || true"],
        cwd=config.REPO_DIR,
    )
