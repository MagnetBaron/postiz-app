#!/usr/bin/env bash
# Install the LaunchAgents for this user. No sudo.
# Re-running replaces the plists and reloads them.
# The tunnel agent is loaded only after ~/.cloudflared/config.yml exists.
# The draft proxy is loaded only after python3 is on the machine.

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

if [ -x "${brew_prefix}/bin/python3" ]; then
  python_bin="${brew_prefix}/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  python_bin=$(command -v python3)
else
  python_bin="${brew_prefix}/bin/python3"
fi

case "${python_bin}" in
  *'|'* | *'&'* | *'<'* | *'>'*)
    echo "Python path contains a character this installer will not put in a plist." >&2
    exit 1
    ;;
esac

render() {
  local src="$1" dest="$2"
  sed \
    -e "s|__ROOT__|${ROOT}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|__BREW_PREFIX__|${brew_prefix}|g" \
    -e "s|__PYTHON__|${python_bin}|g" \
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

mkdir -p "${logs}/postiz-draft-proxy"
proxy_plist="${agents}/com.magnetbaron.postiz.draft-proxy.plist"
render "${ROOT}/launchd/com.magnetbaron.postiz.draft-proxy.plist" "$proxy_plist"
if [ -x "$python_bin" ]; then
  load_agent "com.magnetbaron.postiz.draft-proxy" "$proxy_plist"
else
  echo "Wrote ${proxy_plist} but did not load it. Install Python 3 with: brew install python@3.12" >&2
  echo "Create the Keychain item and a bot token, then run this script again. See README, Draft proxy." >&2
fi
