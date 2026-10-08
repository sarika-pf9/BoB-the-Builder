#!/bin/bash
# 10-packages.sh - install everything a build host needs (Ubuntu 24.04).
#
#   docker + containerd   from Docker's official apt repo (skipped if present)
#   packer                from HashiCorp's apt repo (skipped if present);
#                         `packer init` fetches its qemu plugin itself
#   qemu / kvm            packer's qemu builder runs the image build in a VM
#   libnbd-dev, gettext-base (envsubst), jq, curl, wget, rsync, git, make
#   python3-venv          for the dashboard's venv
#
# Go is NOT installed here - build_system downloads its own copy to
# ~/.local/go on first qcow2 build. Node/yarn aren't needed either: the UI
# image builds inside docker. Safe to re-run.
set -euo pipefail

. /etc/os-release
CODENAME="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
ARCH="$(dpkg --print-architecture)"

echo "=== base packages ==="
sudo apt-get update -qq
sudo apt-get install -y -qq \
  ca-certificates curl wget gnupg git make jq rsync gettext-base \
  qemu-system-x86 qemu-utils cpu-checker libnbd-dev \
  python3 python3-venv

echo "=== docker ==="
if command -v docker >/dev/null 2>&1; then
  echo "[*] docker already installed: $(docker --version)"
else
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin
  echo "[OK] $(docker --version)"
fi
sudo systemctl enable --now docker containerd

echo "=== packer ==="
if command -v packer >/dev/null 2>&1; then
  echo "[*] packer already installed: $(packer version | head -1)"
else
  curl -fsSL https://apt.releases.hashicorp.com/gpg \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
  echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${CODENAME} main" \
    | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq packer
  echo "[OK] $(packer version | head -1)"
fi

echo "=== kvm ==="
if sudo kvm-ok; then
  echo "[OK] KVM acceleration available"
else
  echo "[WARN] KVM not available - packer's qemu build (accelerator = \"kvm\") will fail." >&2
  echo "       Enable nested virtualization / VT-x for this VM." >&2
fi
