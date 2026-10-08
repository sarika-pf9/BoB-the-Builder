"""ui: the vJailbreak web UI image."""
from common.images import Image

NAME = "ui"
DESCRIPTION = "the UI docker image (make ui)"
MAKE_TARGETS = ["ui"]

UI = Image(
    key="ui",
    local_repo="vjailbreak-ui",
    upstream_repo="vjailbreak-ui",
    default_quay_repo="proj_ui",
)
IMAGES = [UI]


def extra_env(tag):
    return {}
