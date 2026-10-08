"""
build_system - local build tool for platform9/vjailbreak-code components.

Builds a single component (or the full qcow2) from a given branch. For a
single component, nothing is pushed to quay.io unless you pass --push (see
that flag's help below). For `qcow2`, pushing to your own quay.io repos is
NOT optional: the manifests baked into the qcow2, and the v2v-helper image
reference compiled into the controller binary, both point directly at
quay.io/<namespace>/proj_* - so Step 7 always runs for `qcow2`, regardless
of --push. See "Design" below for why.

Design (qcow2 flow):
    image_builder/scripts/download_images.sh, as merged into main, always
    does `ctr i pull quay.io/platform9/vjailbreak-<x>:$TAG` for our five
    first-party images. For a local dev build, that tag was never pushed
    to the real quay.io/platform9 (we don't have write access there, and
    wouldn't want dev tags cluttering it if we did) - so that upstream ref
    is permanently dangling for any tag this tool produces.

    Instead, this tool:
      1. Patches the checked-out copy of download_images.sh (REPO_DIR only
         - see components/qcow2/download_images.py) so those five lines ask
         for quay.io/<namespace>/proj_* instead of quay.io/platform9/vjailbreak-*.
         This is NOT a permanent change: checkout_branch()'s `git reset
         --hard origin/<branch>`, which runs at the start of every build,
         discards it before the next build even starts. Nothing here is
         ever committed or pushed to the hosted repo - the patch exists only
         in this one build's working tree, for this one build.
      2. Mandatorily pushes the five freshly-built images to those same
         quay.io/<namespace>/proj_* repos (Step 7) so the ref the patched
         script (and the compiled-in v2v-helper default - see
         components/controller/build.py) asks for actually resolves - on
         the real internet, independent of this build host. That's what
         makes a deployed appliance's fallback pull (if a locally-cached
         image is ever deleted) land somewhere real instead of a dangling
         reference.
      3. Still pre-seeds a local mirror registry (host-setup/40-local-registry.sh,
         a one-time host setup script, NOT in the vjailbreak repo) with the
         same images, so the qcow2 build itself never has to wait on/depend
         on the real quay.io push finishing - the patched download_images.sh
         resolves against the local mirror during the build (fast, no network
         dependency for the build itself), while Step 7 populates the real
         quay.io/<namespace>/proj_* repos those same refs point at, for
         later/production use.

Usage:
    build_system <component> <branch> [--push]
"""
import argparse

from common import config
from common.quay import require_quay_namespace
from components import IMAGE_COMPONENTS, build_component
from components.qcow2 import build as qcow2


def build_parser():
    component_help = "\n".join(
        f"  {m.NAME:<12} {m.DESCRIPTION}" for m in (*IMAGE_COMPONENTS.values(), qcow2)
    )
    parser = argparse.ArgumentParser(
        prog="build_system",
        description="Build vjailbreak components from a git branch.",
        epilog=f"components:\n{component_help}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("component", choices=list(IMAGE_COMPONENTS.keys()) + [qcow2.NAME])
    parser.add_argument("branch")
    parser.add_argument(
        "--push", action="store_true",
        help=f"For a single COMPONENT build only: also push the built image(s) to "
             f"your own quay.io/{config.QUAY_NAMESPACE or '<namespace>'}/proj_* repos. "
             f"Ignored for `qcow2`, which always pushes there regardless of this "
             f"flag. Assumes you've already run `docker login quay.io` on this host.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    require_quay_namespace()

    if args.component == qcow2.NAME:
        if args.push:
            print("[*] --push has no extra effect on `qcow2` - it always pushes to your personal quay repos.")
        qcow2.build_qcow2(args.branch)
    else:
        build_component(args.component, args.branch, push=args.push)
