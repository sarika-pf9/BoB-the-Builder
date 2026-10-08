# shellcheck shell=bash
# Sourced (not run) by install.sh and host-setup/*.sh.
# Loads $BOB_DIR/bob.env and exports every BOB_* setting with its default.

BOB_DIR="${BOB_THE_BUILDER_DIR:-$HOME/bob_the_builder}"
BOB_CONFIG="${BOB_CONFIG:-$BOB_DIR/bob.env}"

bob_load_config() {
  if [ ! -f "$BOB_CONFIG" ]; then
    echo "[FAIL] no config at $BOB_CONFIG" >&2
    echo "       Create it from the example, then fill in BOB_QUAY_NAMESPACE:" >&2
    echo "         mkdir -p $(dirname "$BOB_CONFIG")" >&2
    echo "         cp config/bob.env.example $BOB_CONFIG" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$BOB_CONFIG"
  set +a

  export BOB_DIR BOB_CONFIG
  export BOB_VJB_REPO_DIR="${BOB_VJB_REPO_DIR:-/opt/build/vjailbreak}"
  export BOB_VJB_REPO_URL="${BOB_VJB_REPO_URL:-git@github.com:platform9/vjailbreak-code.git}"
  export BOB_MIRROR_PORT="${BOB_MIRROR_PORT:-5051}"
  export BOB_DASHBOARD_PORT="${BOB_DASHBOARD_PORT:-80}"
}

bob_require_namespace() {
  if [ -z "${BOB_QUAY_NAMESPACE:-}" ]; then
    echo "[FAIL] BOB_QUAY_NAMESPACE is empty in $BOB_CONFIG - set it to your quay.io namespace." >&2
    return 1
  fi
}
