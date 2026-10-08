import base64
import subprocess
import sys
from pathlib import Path

from common import config
from common.shell import run
from components.ai.build import AI
from components.controller.build import CONTROLLER
from components.qcow2.go_toolchain import ensure_go_available
from components.sdk.build import VPWNED
from components.ui.build import UI


def render_early_manifests(tag, env):
    """envsubst steps that don't depend on download_images.sh having run yet."""
    repo = env.get("REPO", config.REPO_PREFIX)
    registry = env["REGISTRY"]
    # These four feed manifests baked into the qcow2 and pulled by the
    # appliance's own containerd against the tars download_images.sh exports
    # below - must be byte-for-byte the same refs as the LOCALLY PATCHED
    # copy of that script (patch_download_images_sh(), called just before
    # this in build_qcow2()), i.e. your own quay.io/<namespace>/proj_*
    # repos - never quay.io/platform9/..., which this dev tag was never
    # pushed to and never will be (see cli.py).
    env["UI_IMG"] = UI.personal_ref(tag)
    env["AI_IMG"] = AI.personal_ref(tag)
    env["CONTROLLER_IMG"] = CONTROLLER.personal_ref(tag)
    env["VPWNED_IMG"] = VPWNED.personal_ref(tag)
    env["QCOW2_IMG"] = f"{registry}/{repo}/vjailbreak:{tag}"

    # No CI secrets available locally - default to empty rather than failing.
    env["AMPLITUDE_API_KEY"] = base64.b64encode(env.get("AMPLITUDE_API_KEY", "").encode()).decode()
    env["BUGSNAG_API_KEY"] = base64.b64encode(env.get("BUGSNAG_API_KEY", "").encode()).decode()
    env["VJB_VERSION_TAG"] = base64.b64encode(tag.encode()).decode()

    repo_dir = config.REPO_DIR
    deploy_dir = repo_dir / "image_builder" / "deploy"
    deploy_dir.mkdir(parents=True, exist_ok=True)

    def envsubst(src, dst):
        print(f"+ envsubst < {src} > {dst}")
        with open(src) as f_in, open(dst, "w") as f_out:
            result = subprocess.run(["envsubst"], stdin=f_in, stdout=f_out, env=env)
        if result.returncode != 0:
            print(f"[FAIL] envsubst failed for {src}", file=sys.stderr)
            sys.exit(1)

    envsubst(repo_dir / "ui/deploy/ui.yaml", deploy_dir / "01ui.yaml")
    envsubst(repo_dir / "vjailbreak-ai/deploy/vjailbreak-ai.yaml", deploy_dir / "08vjailbreak-ai.yaml")
    envsubst(repo_dir / "image_builder/configs/version-config.yaml", deploy_dir / "version-config.yaml")
    envsubst(repo_dir / "image_builder/configs/analytics-keys.yaml", deploy_dir / "analytics-keys.yaml")

    run(["cp", "image_builder/cronjob/version-checker.yaml", "image_builder/deploy/version-checker.yaml"], env=env)
    run(["cp", "image_builder/configs/vjailbreak-settings.yaml", "image_builder/deploy/vjailbreak-settings.yaml"], env=env)
    run(["cp", "opensource.txt", "image_builder/opensource.txt"], env=env)


def render_controller_manifests(env):
    """The 'Generate Controller Manifests' step - must run AFTER download_images.sh,
    since it copies the cert-manager.yaml that step downloads."""
    ensure_go_available(env)
    localbin = str(Path.home() / ".cache" / "k8s-migration-bin")
    run(["make", "-C", "k8s/migration/", "build-installer", f"LOCALBIN={localbin}"], env=env)
    run(["cp", "k8s/migration/dist/install.yaml", "image_builder/deploy/00controller.yaml"], env=env)
    run(["cp", "-r", "k8s/kube-prometheus", "image_builder/deploy/"], env=env)
    (config.REPO_DIR / "image_builder" / "deploy" / "cert-manager").mkdir(parents=True, exist_ok=True)
    run(["cp", "image_builder/cert-manager-manifests/cert-manager.yaml", "image_builder/deploy/cert-manager/"], env=env)
    run(["cp", "k8s/cert-manager/00-selfsigned-issuer.yaml", "image_builder/deploy/cert-manager/"], env=env)
    run(["cp", "deploy/volumeimageprofile-defaults.yaml", "image_builder/configs/volumeimageprofile-defaults.yaml"], env=env)
    run(["cp", "opensource.txt", "image_builder/opensource.txt"], env=env)
    run(["cp", "LICENSE", "image_builder/LICENSE"], env=env)
