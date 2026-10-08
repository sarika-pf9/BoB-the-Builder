#!/bin/bash
# 50-check-host.sh - read-only health check of a build host. Changes nothing.
# Run any time ("why is my build failing?"); setup-host.sh runs it last.
set -uo pipefail

: "${BOB_VJB_REPO_DIR:=/opt/build/vjailbreak}"
: "${BOB_MIRROR_PORT:=5051}"

failures=0
ok()   { echo "  [OK]   $*"; }
bad()  { echo "  [FAIL] $*"; failures=$((failures + 1)); }
warn() { echo "  [WARN] $*"; }

echo "=== tools ==="
for tool in docker packer qemu-img envsubst git make curl wget jq rsync python3; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; else bad "$tool missing (host-setup/10-packages.sh)"; fi
done

echo "=== permissions ==="
if docker info >/dev/null 2>&1; then ok "docker usable without sudo"; else bad "docker not usable as $(whoami) - in docker group? logged in again since?"; fi
if [ -w /dev/kvm ]; then ok "/dev/kvm writable"; else bad "/dev/kvm not writable - kvm group, or KVM not enabled for this VM"; fi

echo "=== vjailbreak checkout ==="
if [ -d "$BOB_VJB_REPO_DIR/.git" ]; then ok "$BOB_VJB_REPO_DIR"; else bad "$BOB_VJB_REPO_DIR missing (host-setup/30-vjailbreak-checkout.sh)"; fi

echo "=== local mirror + ctr wrapper ==="
if curl -fsS "http://127.0.0.1:${BOB_MIRROR_PORT}/v2/" >/dev/null 2>&1; then
  ok "mirror registry on 127.0.0.1:${BOB_MIRROR_PORT}"
else
  bad "mirror registry not reachable on 127.0.0.1:${BOB_MIRROR_PORT} (host-setup/40-local-registry.sh)"
fi
if [ -f /etc/containerd/certs.d/quay.io/hosts.toml ]; then ok "containerd hosts.toml for quay.io"; else bad "no /etc/containerd/certs.d/quay.io/hosts.toml"; fi
if [ "$(sudo -n which ctr 2>/dev/null)" = "/usr/local/bin/ctr" ]; then ok "sudo ctr -> wrapper"; else warn "couldn't confirm 'sudo ctr' resolves to /usr/local/bin/ctr (needs passwordless sudo to check)"; fi

echo "=== quay.io login ==="
if [ -f "$HOME/.docker/config.json" ] && grep -q "quay.io" "$HOME/.docker/config.json"; then
  ok "docker config has quay.io credentials"
else
  warn "no quay.io entry in ~/.docker/config.json - run: docker login quay.io (qcow2 builds need it)"
fi

echo
if [ "$failures" -eq 0 ]; then echo "host looks ready."; else echo "$failures problem(s) found."; exit 1; fi
