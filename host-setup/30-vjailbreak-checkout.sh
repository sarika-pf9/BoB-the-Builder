#!/bin/bash
# 30-vjailbreak-checkout.sh - clone vjailbreak-code to $BOB_VJB_REPO_DIR, the
# dedicated checkout every build resets and builds from.
#
# Needs a GitHub SSH key on this host with read access to $BOB_VJB_REPO_URL
# (check with: ssh -T git@github.com). Leaves an existing checkout alone.
set -euo pipefail

: "${BOB_VJB_REPO_DIR:?run via setup-host.sh, or export BOB_VJB_REPO_DIR}"
: "${BOB_VJB_REPO_URL:?run via setup-host.sh, or export BOB_VJB_REPO_URL}"

if [ -d "$BOB_VJB_REPO_DIR/.git" ]; then
  echo "[*] $BOB_VJB_REPO_DIR already exists:"
  git -C "$BOB_VJB_REPO_DIR" remote -v | head -1
  exit 0
fi

PARENT="$(dirname "$BOB_VJB_REPO_DIR")"
sudo mkdir -p "$PARENT"
sudo chown "$(id -u):$(id -g)" "$PARENT"
git clone "$BOB_VJB_REPO_URL" "$BOB_VJB_REPO_DIR"
echo "[OK] cloned $BOB_VJB_REPO_URL -> $BOB_VJB_REPO_DIR"
