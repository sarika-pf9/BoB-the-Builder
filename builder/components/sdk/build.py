"""sdk: the vpwned API server image."""
from common.images import Image

NAME = "sdk"
DESCRIPTION = "the vpwned/sdk docker image (make build-vpwned)"
MAKE_TARGETS = ["build-vpwned"]

VPWNED = Image(
    key="vpwned",
    local_repo="vjailbreak-vpwned",
    upstream_repo="vjailbreak-vpwned",
    default_quay_repo="proj_sdk",
)
IMAGES = [VPWNED]


def extra_env(tag):
    return {}
