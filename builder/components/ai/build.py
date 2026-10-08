"""ai: the vjailbreak-ai image."""
from common.images import Image

NAME = "ai"
DESCRIPTION = "the vjailbreak-ai docker image (make vjailbreak-ai)"
MAKE_TARGETS = ["vjailbreak-ai"]

AI = Image(
    key="ai",
    local_repo="vjailbreak-ai",
    upstream_repo="vjailbreak-ai",
    default_quay_repo="proj_ai",
)
IMAGES = [AI]


def extra_env(tag):
    return {}
