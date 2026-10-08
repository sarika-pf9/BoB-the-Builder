"""
One folder per buildable component. Each image component's build.py declares:

    NAME          the CLI name (`build_system <NAME> <branch>`)
    DESCRIPTION   one line for --help
    MAKE_TARGETS  vjailbreak Makefile target(s) that build it
    IMAGES        the common.images.Image(s) those targets produce
    extra_env()   any extra env the build needs, given the tag

The Dockerfiles themselves live in vjailbreak-code, not here - this only says
how to drive them. qcow2/ is different: it builds every component above,
then packages them into the appliance disk image.

To add an image: create components/<name>/build.py with the fields above and
add it to IMAGE_COMPONENTS below.
"""
import os

from common import config
from common.git import checkout_branch, sanitize
from common.images import show_images_for_tag
from common.quay import push_to_personal_quay
from common.shell import run
from components.ai import build as ai
from components.controller import build as controller
from components.sdk import build as sdk
from components.ui import build as ui

IMAGE_COMPONENTS = {m.NAME: m for m in (controller, ui, sdk, ai)}

# Every first-party image, in the order download_images.sh is patched and
# images are pushed: v2v_helper, controller, ui, vpwned, ai.
ALL_IMAGES = [image for m in IMAGE_COMPONENTS.values() for image in m.IMAGES]


def build_component(name, branch, push=False):
    """Checks out branch, builds one component's image(s), returns the short sha."""
    component = IMAGE_COMPONENTS[name]
    sha = checkout_branch(branch)
    tag = f"{sanitize(branch)}-{sha}"

    env = os.environ.copy()
    env["REGISTRY"] = config.LOCAL_REGISTRY
    env["TAG"] = tag
    env.update(component.extra_env(tag))

    for target in component.MAKE_TARGETS:
        print(f"\n=== make {target}  (REGISTRY={config.LOCAL_REGISTRY} TAG={tag}) ===")
        run(["make", target], env=env)

    print(f"\n[OK] {name} built from '{branch}' @ {sha}  (tag: {tag})")
    show_images_for_tag(tag)

    if push:
        push_to_personal_quay(tag, component.IMAGES)

    return sha
