#!/bin/bash
# setup-host.sh - turn a fresh Ubuntu 24.04 VM into a vjailbreak build host.
# Runs every numbered step in this folder, in order. Each step is safe to
# re-run, so this is too. Run it ONCE per machine, before install.sh.
#
#   ./host-setup/setup-host.sh            # all steps
#   ./host-setup/setup-host.sh 40         # just steps whose name starts with 40
#
# Manual steps it can't do for you are listed in host-setup/README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/config.sh
. "$HERE/../lib/config.sh"
bob_load_config

ONLY="${1:-}"
for step in "$HERE"/[0-9][0-9]-*.sh; do
  name="$(basename "$step")"
  if [ -n "$ONLY" ] && [[ "$name" != "$ONLY"* ]]; then
    continue
  fi
  echo
  echo "################ $name ################"
  if [ "$name" = "50-check-host.sh" ]; then
    bash "$step" || echo "[*] fix the above (often: just log out and back in), then re-run: $step"
  elif docker info >/dev/null 2>&1 || ! getent group docker | grep -qw "$(whoami)"; then
    bash "$step"
  else
    # 20-users-groups.sh just added us to the docker group, but this login
    # session doesn't have it yet - run the step inside a shell that does.
    sg docker -c "bash '$step'"
  fi
done
