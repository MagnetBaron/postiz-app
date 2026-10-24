#!/usr/bin/env bash
# Install the two LaunchAgents for this user. No sudo.
# Re-running replaces the plists and reloads them.
# The tunnel agent is loaded only after ~/.cloudflared/config.yml exists.

set -euo pipefail
set +o xtrace

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

ROOT=$(kit_root)
uid=$(id -u)
agents="${HOME}/Library/LaunchAgents"
logs="${HOME}/Library/Logs"
mkdir -p "$agents" "$logs"

case "${HOME}${ROOT}" in
  *'|'* | *'&'* | *'<'* | *'>'*)
    echo "Home or kit path contains a character this installer will not put in a plist." >&2
    exit 1
    ;;
esac

if command -v brew >/dev/null 2>&1; then
  brew_prefix=$(brew --prefix)
else
  brew_prefix="/opt/homebrew"
fi

render() {
  local src="$1" dest="$2"
  sed \
    -e "s|__ROOT__|${ROOT}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|__BREW_PREFIX__|${brew_prefix}|g" \
    "$src" >"$dest"
  chmod 644 "$dest"
}

load_agent() {
  local label="$1" plist="$2"
  launchctl bootout "gui/${uid}/${label}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${uid}" "$plist"
  launchctl enable "gui/${uid}/${label}"
  echo "Loaded ${label}" >&2
}

stack_plist="${agents}/com.magnetbaron.postiz.stack.plist"
render "${ROOT}/launchd/com.magnetbaron.postiz.stack.plist" "$stack_plist"
load_agent "com.magnetbaron.postiz.stack" "$stack_plist"

tunnel_plist="${agents}/com.magnetbaron.postiz.tunnel.plist"
render "${ROOT}/launchd/com.magnetbaron.postiz.tunnel.plist" "$tunnel_plist"
if [ -f "${HOME}/.cloudflared/config.yml" ]; then
  load_agent "com.magnetbaron.postiz.tunnel" "$tunnel_plist"
else
  echo "Wrote ${tunnel_plist} but did not load it. Copy the tunnel template to ${HOME}/.cloudflared/config.yml, then run this script again." >&2
fi
