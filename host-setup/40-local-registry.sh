#!/bin/bash
# 40-local-registry.sh (formerly setup_local_registry.sh) - ONE-TIME host
# setup for a build host, not part of the vjailbreak repo and not run by
# build_system itself. Run via host-setup/setup-host.sh.
#
# Why this exists: image_builder/scripts/download_images.sh (on main, and not
# to be edited) always does `sudo ctr i pull quay.io/platform9/vjailbreak-<x>:$TAG`
# for our five first-party images. A local dev build's tag was never pushed to
# the real quay.io, so that pull would 404. This script:
#
#   1. Starts a small local "mirror" registry that build_system pushes each
#      freshly-built image into, under that exact quay.io/platform9/... name.
#   2. Writes a containerd hosts.toml for quay.io that tries the mirror first
#      and falls back to the real quay.io for everything else (verified: an
#      image NOT in the mirror, e.g. quay.io/prometheus/prometheus, still
#      pulls from the real quay.io through this same config).
#   3. Installs a thin `ctr` wrapper ahead of the real one in PATH, because
#      `ctr images pull` only reads hosts.toml files when called with
#      --hosts-dir - it does NOT do so by default, and download_images.sh's
#      hardcoded `ctr i pull` call has no such flag and can't be edited to
#      add one. The wrapper adds it transparently to any `pull` call and
#      passes every other ctr invocation straight through unchanged. This is
#      a host/tooling change, not a change to any repo file.
#
# (An earlier version of this script also touched containerd's config.toml
# for CRI's own registry config_path - that plugin is disabled on this Docker
# host and was never actually involved. No containerd restart is needed for
# any of this.)
#
# Safe to re-run (idempotent).
set -euo pipefail

MIRROR_HOST="127.0.0.1"
# Must match BOB_MIRROR_PORT in bob.env (build_system reads the same value).
MIRROR_PORT="${BOB_MIRROR_PORT:-5051}"
MIRROR_ADDR="${MIRROR_HOST}:${MIRROR_PORT}"
CONTAINER_NAME="vjb-local-mirror"
DATA_DIR="/opt/vjb-local-mirror-data"
HOSTS_DIR="/etc/containerd/certs.d/quay.io"
CTR_WRAPPER="/usr/local/bin/ctr"

echo "=== 1/3: local mirror registry container ==="
if docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "[*] ${CONTAINER_NAME} already exists - leaving it as-is."
  docker start "${CONTAINER_NAME}" >/dev/null 2>&1 || true
else
  sudo mkdir -p "${DATA_DIR}"
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${MIRROR_HOST}:${MIRROR_PORT}:5000" \
    -v "${DATA_DIR}:/var/lib/registry" \
    registry:2
  echo "[OK] started ${CONTAINER_NAME}, bound to ${MIRROR_ADDR} (loopback only)"
fi

echo
echo "=== 2/3: containerd mirror config for quay.io (mirror first, real quay.io as fallback) ==="
sudo mkdir -p "${HOSTS_DIR}"
sudo tee "${HOSTS_DIR}/hosts.toml" >/dev/null <<EOF
server = "https://quay.io"

[host."http://${MIRROR_ADDR}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
EOF
echo "[OK] wrote ${HOSTS_DIR}/hosts.toml"

echo
echo "=== 3/3: ctr wrapper (adds --hosts-dir to pull calls only) ==="
if [ -e "${CTR_WRAPPER}" ] && ! grep -q "vjb-local-mirror" "${CTR_WRAPPER}" 2>/dev/null; then
  echo "[FAIL] ${CTR_WRAPPER} already exists and wasn't put there by this script - not overwriting it." >&2
  echo "       Move it aside first if you want this script to install the wrapper here." >&2
  exit 1
fi

REAL_CTR="$(command -v ctr || true)"
if [ -z "${REAL_CTR}" ] || [ "${REAL_CTR}" = "${CTR_WRAPPER}" ]; then
  # Wrapper already installed from a previous run - command -v now resolves
  # to itself. Ask the wrapper what it thinks the real one is instead of
  # guessing, so re-runs stay idempotent.
  REAL_CTR="$(sudo grep -oP '(?<=^REAL_CTR=")[^"]+' "${CTR_WRAPPER}" 2>/dev/null || echo "/usr/bin/ctr")"
fi

sudo tee "${CTR_WRAPPER}" >/dev/null <<EOF
#!/bin/bash
# Installed by vjailbreak's setup_local_registry.sh (vjb-local-mirror).
# Transparently adds --hosts-dir to any "ctr ... pull ..." invocation, so
# download_images.sh's unmodified \`sudo ctr i pull\` resolves our five
# first-party images from the local mirror registry (see that script's
# comments) instead of always going straight to the real quay.io. Every
# other ctr call passes through completely unchanged.
REAL_CTR="${REAL_CTR}"
HOSTS_DIR="/etc/containerd/certs.d"

has_hosts_dir=0
for a in "\$@"; do
  case "\$a" in
    --hosts-dir|--hosts-dir=*) has_hosts_dir=1 ;;
  esac
done

new_args=()
inserted=0
for a in "\$@"; do
  new_args+=("\$a")
  if [ "\$a" = "pull" ] && [ "\$has_hosts_dir" = "0" ] && [ "\$inserted" = "0" ]; then
    new_args+=("--hosts-dir" "\$HOSTS_DIR")
    inserted=1
  fi
done

exec "\$REAL_CTR" "\${new_args[@]}"
EOF
sudo chmod +x "${CTR_WRAPPER}"
echo "[OK] wrote ${CTR_WRAPPER} (wraps ${REAL_CTR})"

RESOLVED="$(sudo which ctr)"
if [ "${RESOLVED}" != "${CTR_WRAPPER}" ]; then
  echo "[WARN] 'sudo which ctr' resolves to ${RESOLVED}, not ${CTR_WRAPPER}." >&2
  echo "       sudo's secure_path on this host doesn't put /usr/local/bin first - the" >&2
  echo "       wrapper won't actually be used by download_images.sh's 'sudo ctr' calls." >&2
  echo "       Check /etc/sudoers' secure_path, or the ordering of PATH for a login shell." >&2
else
  echo "[OK] sudo ctr -> ${CTR_WRAPPER} (confirmed)"
fi

echo
echo "=== done ==="
echo "Verify with:"
echo "    curl -fsS http://${MIRROR_ADDR}/v2/ && echo OK"
echo "    sudo ctr images pull --platform linux/amd64 quay.io/prometheus/prometheus:v3.9.1   # should still work (fallback)"
echo "then re-run build_system qcow2 <branch>."
