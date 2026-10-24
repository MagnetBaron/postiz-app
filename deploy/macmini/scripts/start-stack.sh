#!/usr/bin/env bash
# Wait for Docker, then start the stack. Used by launchd at login.
# Does not start the optional Caddy profile. No sudo.

set -euo pipefail
set +o xtrace

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.orbstack/bin:/Applications/Docker.app/Contents/Resources/bin:${PATH:-/usr/bin:/bin}"

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

ROOT=$(kit_root)
cd "$ROOT"

i=0
until docker info >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "docker did not become ready within 3 minutes. Open OrbStack or Docker Desktop." >&2
    exit 1
  fi
  sleep 3
done

docker compose up -d
