"""controller: the k8s migration controller AND v2v-helper.

One `make vjail-controller` call produces both images, which is why
v2v-helper has no folder of its own.
"""
from common.images import Image

NAME = "controller"
DESCRIPTION = "v2v-helper + the k8s controller (make vjail-controller)"
MAKE_TARGETS = ["vjail-controller"]

V2V_HELPER = Image(
    key="v2v_helper",
    local_repo="v2v-helper",
    upstream_repo="vjailbreak-v2v-helper",
    default_quay_repo="proj_v2v",
    built_as_personal_ref=True,
)
CONTROLLER = Image(
    key="controller",
    local_repo="vjailbreak-controller",
    upstream_repo="vjailbreak-controller",
    default_quay_repo="proj_controller",
)
IMAGES = [V2V_HELPER, CONTROLLER]


def extra_env(tag):
    # k8s/migration/internal/controller/migrationplan_controller.go
    # declares `var v2vimage = "platform9/v2v-helper:v0.1"` with the
    # comment "replaced by Go linker flags in the Dockerfile" - and
    # k8s/migration/Makefile's `build` target does exactly that:
    # `-ldflags "-X ...controller.v2vimage=${V2V_IMG}"`. There is no
    # runtime override anywhere (checked: no V2V_* env var appears in
    # the manager Deployment manifests) - whatever V2V_IMG resolves to
    # HERE, at compile time, is permanently frozen into the controller
    # binary, and is what a deployed appliance will try to pull for
    # every migration, forever.
    #
    # Left unset, the top-level Makefile's own `V2V_IMG ?=
    # ${REGISTRY}/${REPO}/v2v-helper:${TAG}` default kicks in using
    # REGISTRY=LOCAL_REGISTRY ("local" - only meaningful for local
    # docker tagging) - baking a reference that can never resolve
    # anywhere into the shipped binary. Pointing it at the real,
    # mandatory-pushed (for qcow2) quay.io/<namespace>/proj_v2v ref here
    # is the only way to fix that; there's no manifest field to patch
    # after the fact.
    return {"V2V_IMG": V2V_HELPER.personal_ref(tag)}
