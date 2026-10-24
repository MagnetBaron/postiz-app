# shellcheck shell=bash
# Shared helpers. Sourced by the other scripts. Not executed directly.

kit_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

refuse_root() {
  if [ "$(id -u)" -eq 0 ]; then
    echo "Run as the Mac user who owns Docker, not with sudo." >&2
    exit 1
  fi
}

# Print the raw value of KEY in an env file. Matching quotes are removed.
# The value is not evaluated.
env_get() {
  local file="$1" key="$2" line val
  [ -f "$file" ] || return 1
  line=$(grep -E "^${key}=" "$file" | tail -n 1 || true)
  [ -n "$line" ] || return 1
  val=${line#*=}
  if [ "${val#\"}" != "$val" ] && [ "${val%\"}" != "$val" ]; then
    val=${val#\"}
    val=${val%\"}
  elif [ "${val#\'}" != "$val" ] && [ "${val%\'}" != "$val" ]; then
    val=${val#\'}
    val=${val%\'}
  fi
  printf '%s' "$val"
}

# Replace KEY or append it. The value must not contain a newline.
set_env_key() {
  local file="$1" key="$2" value="$3" tmp found
  tmp=$(mktemp "${file}.tmp.XXXXXX")
  found=0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "${key}="*)
        printf '%s=%s\n' "$key" "$value"
        found=1
        ;;
      *)
        printf '%s\n' "$line"
        ;;
    esac
  done <"$file" >"$tmp"
  if [ "$found" -eq 0 ]; then
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$file"
}

is_placeholder() {
  local v="$1"
  case "$v" in
    "" | change-me | CHANGE_ME | changeme | postiz-password | temporal | \
      your-cloudflare-account-id | your-r2-access-key-id | \
      your-r2-secret-access-key | your-bucket-name | your-api-key | \
      replace-me | REPLACE_ME | REPLACE_WITH_TUNNEL_UUID)
      return 0
      ;;
  esac
  case "$v" in
    your-* | replace-* | REPLACE_* | *"random string"* | *"type random characters"*)
      return 0
      ;;
  esac
  return 1
}

is_secret_name() {
  case "$1" in
    *SECRET* | *PASSWORD* | *TOKEN* | *PRIVATE* | *API_KEY* | *ACCESS_KEY* | *SIGNING*)
      return 0
      ;;
  esac
  return 1
}

# https origin with a real hostname. Rejects localhost, .localhost, and .local.
is_public_https_origin() {
  local url="$1" rest host
  case "$url" in
    https://*) ;;
    *) return 1 ;;
  esac
  case "$url" in
    */) return 1 ;;
  esac
  rest=${url#https://}
  case "$rest" in
    */*) return 1 ;;
  esac
  host=${rest%%:*}
  case "$host" in
    "" | localhost | 127.0.0.1 | social.localhost | *.localhost | *.local | \
      example.com | *.example.com | example.org | *.example.org | \
      example.net | *.example.net)
      return 1
      ;;
  esac
  return 0
}

file_mode() {
  if [ "$(uname -s)" = "Darwin" ]; then
    stat -f %Lp "$1"
  else
    stat -c %a "$1"
  fi
}

origin_host() {
  local url="$1" rest
  rest=${url#*://}
  rest=${rest%%/*}
  printf '%s' "${rest%%:*}"
}
