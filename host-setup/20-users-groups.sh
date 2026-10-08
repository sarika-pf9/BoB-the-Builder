#!/bin/bash
# 20-users-groups.sh - let the build user run docker and use /dev/kvm without
# sudo. Takes effect on the NEXT login (or `newgrp docker`). Safe to re-run.
set -euo pipefail

BUILD_USER="${BUILD_USER:-$(whoami)}"

for group in docker kvm; do
  if id -nG "$BUILD_USER" | tr ' ' '\n' | grep -qx "$group"; then
    echo "[*] $BUILD_USER already in $group"
  else
    sudo usermod -aG "$group" "$BUILD_USER"
    echo "[OK] added $BUILD_USER to $group (log out and back in for it to apply)"
  fi
done
