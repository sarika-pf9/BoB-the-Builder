#!/bin/bash
# install.sh - deploy (or update) BoB-the-Builder on this build host.
#
# Copies this checkout's builder/ and dashboard/ into $BOB_DIR (default
# ~/bob_the_builder), puts build_system on PATH, (re)creates the dashboard's
# Python venv, renders the systemd unit and restarts the dashboard.
#
# Run from a checkout of this repo, after host-setup/setup-host.sh:
#     ./install.sh            # refuses if a dashboard build is queued/running
#     ./install.sh --force    # restart anyway (kills that build)
#
# Updating = `git pull && ./install.sh`. Never edit files under $BOB_DIR by
# hand - the next install overwrites them.
#
# Never touched: $BOB_DIR/bob.env (your settings), $BOB_DIR/dashboard/data
# (build history, logs, qcow2 artifacts), $BOB_DIR/dashboard/venv (reused).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
. "$REPO_ROOT/lib/config.sh"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

SERVICE_NAME="vjb-build-dashboard"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DASH_DIR="$BOB_DIR/dashboard"
VERSION="$(git -C "$REPO_ROOT" describe --always --dirty 2>/dev/null || echo unknown)"

echo "=== BoB-the-Builder install ($VERSION) ==="
echo "Target directory: $BOB_DIR"
echo

echo "--- [1/7] config ---"
bob_load_config
bob_require_namespace
echo "[OK] $BOB_CONFIG (quay.io/$BOB_QUAY_NAMESPACE, checkout $BOB_VJB_REPO_DIR, dashboard :$BOB_DASHBOARD_PORT)"
echo

echo "--- [2/7] in-flight build check ---"
# Restarting the service kills any build started THROUGH THE DASHBOARD (the
# build_system subprocess dies with the service's cgroup). Builds run by hand
# in a terminal are unaffected.
if systemctl is-active --quiet "$SERVICE_NAME"; then
  busy="$(curl -fsS --max-time 5 "http://127.0.0.1:${BOB_DASHBOARD_PORT}/api/builds" 2>/dev/null \
    | python3 -c 'import json,sys; print(sum(b.get("status") in ("queued","running") for b in json.load(sys.stdin)))' 2>/dev/null \
    || echo "?")"
  if [ "$busy" = "0" ]; then
    echo "[OK] no queued/running dashboard builds"
  elif [ "$FORCE" = "1" ]; then
    echo "[WARN] $busy queued/running build(s) will be killed (--force)"
  else
    echo "[FAIL] dashboard reports $busy queued/running build(s) ('?' = couldn't ask it)." >&2
    echo "       Wait for them to finish, or re-run with --force to kill them." >&2
    exit 1
  fi
else
  echo "[*] $SERVICE_NAME not running - nothing to interrupt"
fi
echo

echo "--- [3/7] copy builder + dashboard ---"
mkdir -p "$BOB_DIR/builder" "$DASH_DIR/data"
rsync -a --delete --exclude '__pycache__/' "$REPO_ROOT/builder/" "$BOB_DIR/builder/"
rsync -a --delete --exclude '__pycache__/' --exclude 'venv/' --exclude 'data/' \
  "$REPO_ROOT/dashboard/" "$DASH_DIR/"
chmod +x "$BOB_DIR/builder/build_system"
echo "$VERSION" > "$BOB_DIR/VERSION"
echo "[OK] $BOB_DIR/builder, $DASH_DIR"
# The old single-file installer (install_bob_the_builder.sh) put build_system
# in bin/ and the registry script in ops/. Both now come from this repo.
for old in "$BOB_DIR/bin" "$BOB_DIR/ops"; do
  if [ -d "$old" ]; then
    rm -rf "$old"
    echo "[*] removed old-layout $old (replaced by builder/ and host-setup/)"
  fi
done
echo

echo "--- [4/7] build_system on PATH ---"
sudo ln -sfn "$BOB_DIR/builder/build_system" /usr/local/bin/build_system
echo "[OK] /usr/local/bin/build_system -> $BOB_DIR/builder/build_system"
echo

echo "--- [5/7] host prerequisites ---"
if curl -fsS --max-time 5 "http://127.0.0.1:${BOB_MIRROR_PORT}/v2/" >/dev/null 2>&1; then
  echo "[OK] local mirror registry up on 127.0.0.1:${BOB_MIRROR_PORT}"
else
  echo "[WARN] local mirror registry not reachable on 127.0.0.1:${BOB_MIRROR_PORT}." >&2
  echo "       qcow2 builds will fail until you run: ./host-setup/setup-host.sh" >&2
fi
echo

echo "--- [6/7] dashboard Python venv + dependencies ---"
# Check for bin/pip specifically, not just the directory: `python3 -m venv`
# on Ubuntu silently creates the venv dir WITHOUT pip if the python3-venv
# apt package isn't installed (ensurepip fails, venv creation still exits 0
# in some Python builds) - a bare directory-existence check would wrongly
# treat that broken venv as good on every future re-run.
if [ ! -x "$DASH_DIR/venv/bin/pip" ]; then
  if [ -d "$DASH_DIR/venv" ]; then
    echo "[*] $DASH_DIR/venv exists but has no pip (likely created without python3-venv installed) - recreating it"
    rm -rf "$DASH_DIR/venv"
  fi
  if ! python3 -m venv "$DASH_DIR/venv" || [ ! -x "$DASH_DIR/venv/bin/pip" ]; then
    PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "[*] venv creation didn't produce a working pip - installing python${PYVER}-venv and retrying"
    rm -rf "$DASH_DIR/venv"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "python${PYVER}-venv" python3-venv
    python3 -m venv "$DASH_DIR/venv"
  fi
  echo "[OK] created a working venv at $DASH_DIR/venv"
else
  echo "[*] venv already exists and has a working pip at $DASH_DIR/venv - reusing it"
fi
"$DASH_DIR/venv/bin/pip" install -q --upgrade pip
"$DASH_DIR/venv/bin/pip" install -q -r "$DASH_DIR/requirements.txt"
echo "[OK] dependencies installed/updated"
echo

echo "--- [7/7] systemd service ---"
BOB_USER="$(whoami)" BOB_DIR="$BOB_DIR" BOB_DASHBOARD_PORT="$BOB_DASHBOARD_PORT" \
  envsubst '${BOB_USER} ${BOB_DIR} ${BOB_DASHBOARD_PORT}' \
  < "$REPO_ROOT/systemd/${SERVICE_NAME}.service.tmpl" \
  | sudo tee "$SERVICE_FILE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"
echo "[OK] $SERVICE_NAME (re)started"
echo

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT_SUFFIX=""
[ "$BOB_DASHBOARD_PORT" = "80" ] || PORT_SUFFIX=":$BOB_DASHBOARD_PORT"
echo "=== done ==="
echo "build_system : build_system <controller|ui|sdk|ai|qcow2> <branch> [--push]"
echo "dashboard    : http://${HOST_IP:-<this-host>}${PORT_SUFFIX}/"
echo "data/logs    : $DASH_DIR/data"
echo "service      : sudo systemctl status $SERVICE_NAME"
echo "version      : $VERSION"
