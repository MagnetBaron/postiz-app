#!/usr/bin/env bash
# Check the Mac mini before the first boot.
# Reports every failure it can, then exits non-zero if any failed.
# Does not print secret values. No sudo. macOS bash 3.2.

set -euo pipefail
set +o xtrace

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

ROOT=$(kit_root)
ENV_FILE="${ROOT}/.env"
status=0

note() { echo "preflight: $*" >&2; }
bad() { echo "preflight: FAIL: $*" >&2; status=1; }
ok() { echo "preflight: ok: $*" >&2; }

if [ ! -f "$ENV_FILE" ]; then
  bad ".env is missing. Run scripts/gen-secrets.sh first."
  exit 1
fi

arch=$(uname -m)
case "$arch" in
  arm64 | aarch64)
    ok "architecture ${arch}"
    ;;
  *)
    if [ "${POSTIZ_PREFLIGHT_ALLOW_ARCH:-}" = "1" ]; then
      note "architecture is ${arch}; this kit is written for arm64"
    else
      bad "this kit is for Apple Silicon (arm64); uname -m is ${arch}"
    fi
    ;;
esac

if [ "$(uname -s)" = "Darwin" ]; then
  ok "kernel Darwin $(sw_vers -productVersion 2>/dev/null || true)"
else
  note "kernel is $(uname -s), not Darwin. Continue only if you are testing the scripts."
fi

mode=$(file_mode "$ENV_FILE")
case "$mode" in
  600 | 400) ok ".env mode ${mode}" ;;
  *) bad ".env mode is ${mode}, expected 600. Run: chmod 600 .env" ;;
esac

require_set() {
  local key="$1" value
  value=$(env_get "$ENV_FILE" "$key" || true)
  if is_placeholder "$value"; then
    bad "${key} is missing or still a placeholder"
    return 1
  fi
  return 0
}

for key in \
  MAIN_URL FRONTEND_URL NEXT_PUBLIC_BACKEND_URL JWT_SECRET DATABASE_URL \
  REDIS_URL BACKEND_INTERNAL_URL TEMPORAL_ADDRESS STORAGE_PROVIDER \
  API_LIMIT DISABLE_REGISTRATION IS_GENERAL POSTGRES_PASSWORD \
  TEMPORAL_POSTGRES_PASSWORD CLOUDFLARE_BUCKET_URL CLOUDFLARE_REGION
do
  require_set "$key" || true
done

jwt=$(env_get "$ENV_FILE" JWT_SECRET || true)
if ! is_placeholder "$jwt" && [ "${#jwt}" -lt 32 ]; then
  bad "JWT_SECRET is shorter than 32 characters"
fi
unset jwt

pg=$(env_get "$ENV_FILE" POSTGRES_PASSWORD || true)
db=$(env_get "$ENV_FILE" DATABASE_URL || true)
case "$db" in
  postgresql://postiz-user:*@postiz-postgres:5432/postiz-db-local) ;;
  *) bad "DATABASE_URL must use user postiz-user, host postiz-postgres, database postiz-db-local" ;;
esac
db_pass=${db#postgresql://postiz-user:}
db_pass=${db_pass%%@postiz-postgres:5432/postiz-db-local}
if [ -n "$pg" ] && [ "$db_pass" != "$pg" ]; then
  bad "DATABASE_URL password does not match POSTGRES_PASSWORD. Re-run scripts/gen-secrets.sh"
fi
unset pg db db_pass

if [ "$(env_get "$ENV_FILE" REDIS_URL || true)" != "redis://postiz-redis:6379" ]; then
  bad "REDIS_URL must be redis://postiz-redis:6379"
fi
if [ "$(env_get "$ENV_FILE" BACKEND_INTERNAL_URL || true)" != "http://localhost:3000" ]; then
  bad "BACKEND_INTERNAL_URL must be http://localhost:3000"
fi
if [ "$(env_get "$ENV_FILE" TEMPORAL_ADDRESS || true)" != "temporal:7233" ]; then
  bad "TEMPORAL_ADDRESS must be temporal:7233"
fi
if [ "$(env_get "$ENV_FILE" STORAGE_PROVIDER || true)" != "cloudflare" ]; then
  bad "STORAGE_PROVIDER must be cloudflare for this install"
fi
if [ "$(env_get "$ENV_FILE" IS_GENERAL || true)" != "true" ]; then
  bad "IS_GENERAL must be true or Settings > Agents / Dev stays hidden"
fi

frontend=$(env_get "$ENV_FILE" FRONTEND_URL || true)
main=$(env_get "$ENV_FILE" MAIN_URL || true)
backend=$(env_get "$ENV_FILE" NEXT_PUBLIC_BACKEND_URL || true)
if is_public_https_origin "$frontend"; then
  ok "FRONTEND_URL is a public https origin"
else
  bad "FRONTEND_URL must be https://<your-tunnel-hostname> with no path and no trailing slash. localhost and social.localhost are rejected."
fi
case "$(origin_host "$frontend")" in
  example.com | *.example.com | *.example.org | *.example.net)
    bad "FRONTEND_URL is still the documentation hostname. Use the Cloudflare Tunnel hostname."
    ;;
esac
if [ "$main" != "$frontend" ]; then
  bad "MAIN_URL must equal FRONTEND_URL"
fi
if [ "$backend" != "${frontend}/api" ]; then
  bad "NEXT_PUBLIC_BACKEND_URL must be FRONTEND_URL plus /api"
fi

bucket=$(env_get "$ENV_FILE" CLOUDFLARE_BUCKET_URL || true)
case "$bucket" in
  https://*/) bad "CLOUDFLARE_BUCKET_URL must not end with a slash" ;;
  https://cdn.magnetbaron.com) ok "CLOUDFLARE_BUCKET_URL is the media hostname" ;;
  https://*)
    note "CLOUDFLARE_BUCKET_URL is not https://cdn.magnetbaron.com. That is fine if the bucket's public hostname changed."
    ;;
  *) bad "CLOUDFLARE_BUCKET_URL must be an https URL with no trailing slash" ;;
esac
unset bucket frontend main backend

case "$(env_get "$ENV_FILE" CLOUDFLARE_REGION || true)" in
  auto) ok "CLOUDFLARE_REGION=auto" ;;
  *) bad "CLOUDFLARE_REGION must be auto for R2" ;;
esac

limit=$(env_get "$ENV_FILE" API_LIMIT || true)
case "$limit" in
  '' | *[!0-9]*) bad "API_LIMIT must be a positive integer" ;;
  *)
    if [ "$limit" -lt 1 ]; then
      bad "API_LIMIT must be a positive integer"
    else
      ok "API_LIMIT=${limit} create-post requests per organization per hour"
    fi
    ;;
esac
unset limit

case "$(env_get "$ENV_FILE" DISABLE_REGISTRATION || true)" in
  true) ok "DISABLE_REGISTRATION=true" ;;
  false)
    note "DISABLE_REGISTRATION is false. After the first account, set it to true and recreate the postiz container."
    ;;
  *) bad "DISABLE_REGISTRATION must be true or false" ;;
esac

if [ "${POSTIZ_PREFLIGHT_ALLOW_INCOMPLETE_R2:-}" != "1" ]; then
  for key in CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_ACCESS_KEY CLOUDFLARE_SECRET_ACCESS_KEY CLOUDFLARE_BUCKETNAME; do
    value=$(env_get "$ENV_FILE" "$key" || true)
    if is_placeholder "$value"; then
      bad "${key} is still a placeholder. Fill the R2 values, or set POSTIZ_PREFLIGHT_ALLOW_INCOMPLETE_R2=1 to boot the UI first."
    fi
  done
  unset value
fi

# Provider keys are filled as each app is registered. Empty is expected on first boot.
empty_providers=""
for key in \
  X_API_KEY X_API_SECRET LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET \
  FACEBOOK_APP_ID FACEBOOK_APP_SECRET INSTAGRAM_APP_ID INSTAGRAM_APP_SECRET \
  THREADS_APP_ID THREADS_APP_SECRET YOUTUBE_CLIENT_ID YOUTUBE_CLIENT_SECRET \
  TIKTOK_CLIENT_ID TIKTOK_CLIENT_SECRET PINTEREST_CLIENT_ID PINTEREST_CLIENT_SECRET \
  REDDIT_CLIENT_ID REDDIT_CLIENT_SECRET MASTODON_CLIENT_ID MASTODON_CLIENT_SECRET
do
  value=$(env_get "$ENV_FILE" "$key" || true)
  if is_placeholder "$value"; then
    empty_providers="${empty_providers} ${key}"
  fi
done
unset value
if [ -n "$empty_providers" ]; then
  note "provider keys still empty (normal until you register those apps):${empty_providers}"
fi

if [ "${POSTIZ_PREFLIGHT_SKIP_DNS:-}" = "1" ]; then
  note "DNS check skipped"
else
  host=$(origin_host "$(env_get "$ENV_FILE" FRONTEND_URL || true)")
  resolved=0
  if [ -n "$host" ]; then
    if command -v dig >/dev/null 2>&1; then
      if [ -n "$(dig +time=2 +tries=1 +short "$host" A 2>/dev/null || true)" ] \
        || [ -n "$(dig +time=2 +tries=1 +short "$host" AAAA 2>/dev/null || true)" ]; then
        resolved=1
      fi
    fi
    if [ "$resolved" -eq 0 ] && command -v dscacheutil >/dev/null 2>&1; then
      if dscacheutil -q host -a name "$host" 2>/dev/null | grep -q 'ip_address:'; then
        resolved=1
      fi
    fi
    if [ "$resolved" -eq 0 ] && command -v python3 >/dev/null 2>&1; then
      if python3 -c 'import socket,sys; socket.getaddrinfo(sys.argv[1], 443)' "$host" >/dev/null 2>&1; then
        resolved=1
      fi
    fi
  fi
  if [ "$resolved" -eq 1 ]; then
    ok "DNS resolves ${host}"
  else
    bad "DNS does not resolve ${host}. Create the Cloudflare Tunnel hostname first, or set POSTIZ_PREFLIGHT_SKIP_DNS=1 while you are still choosing the name."
  fi
fi

if [ "${POSTIZ_PREFLIGHT_SKIP_TUNNEL:-}" = "1" ]; then
  note "cloudflared check skipped"
elif command -v cloudflared >/dev/null 2>&1; then
  ok "cloudflared is installed"
else
  bad "cloudflared is not on PATH. Install it with: brew install cloudflared"
fi

if docker info >/dev/null 2>&1; then
  ok "docker is running"
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose is installed"
  else
    bad "docker compose v2 is missing. OrbStack and Docker Desktop both include it."
  fi
else
  bad "docker is not running. Open OrbStack or Docker Desktop and wait until docker info succeeds."
fi

check_port() {
  local port="$1" names
  if ! command -v lsof >/dev/null 2>&1; then
    note "lsof is missing, so port ${port} was not checked"
    return 0
  fi
  names=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1}' | sort -u || true)
  if [ -z "$names" ]; then
    ok "port ${port} is free"
    return 0
  fi
  case "$names" in
    *docker* | *com.docke* | *OrbStack* | *orbstack* | *vpnkit* | *qemu*)
      note "port ${port} is already held by Docker (${names}). Fine if this stack is already up."
      ;;
    *python* | *Python*)
      note "port ${port} is already held by Python (${names}). Fine if the draft proxy is already up."
      ;;
    *)
      bad "port ${port} is in use by: ${names}"
      ;;
  esac
}

check_port 4007
check_port 4017
check_port 8080
check_port 8088

if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if (cd "$ROOT" && docker compose config >/dev/null); then
    ok "docker compose config"
  else
    bad "docker compose config failed"
  fi
fi

if [ "$status" -ne 0 ]; then
  echo "preflight: failed" >&2
  exit 1
fi
echo "preflight: passed" >&2
