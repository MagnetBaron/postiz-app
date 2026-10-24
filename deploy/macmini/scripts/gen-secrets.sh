#!/usr/bin/env bash
# Fill JWT and database passwords in deploy/macmini/.env.
# Idempotent: a value that is already a long non-placeholder is left as it is.
# Never prints secret values. Do not run with bash -x.
# macOS bash 3.2. No sudo.

set -euo pipefail
set +o xtrace

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

ROOT=$(kit_root)
EXAMPLE="${ROOT}/.env.example"
ENV_FILE="${ROOT}/.env"
umask 077

if [ ! -f "$EXAMPLE" ]; then
  echo "Missing ${EXAMPLE}" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$EXAMPLE" "$ENV_FILE"
  echo "Created .env from .env.example" >&2
else
  echo "Using existing .env" >&2
fi

new_secret() {
  openssl rand -hex 32
}

ensure_secret() {
  local key="$1" current
  current=$(env_get "$ENV_FILE" "$key" || true)
  if is_placeholder "$current" || [ "${#current}" -lt 32 ]; then
    set_env_key "$ENV_FILE" "$key" "$(new_secret)"
    echo "Generated ${key}" >&2
  else
    echo "Left ${key} unchanged" >&2
  fi
}

ensure_secret JWT_SECRET
ensure_secret POSTGRES_PASSWORD
ensure_secret TEMPORAL_POSTGRES_PASSWORD

pg_pass=$(env_get "$ENV_FILE" POSTGRES_PASSWORD)
set_env_key "$ENV_FILE" DATABASE_URL \
  "postgresql://postiz-user:${pg_pass}@postiz-postgres:5432/postiz-db-local"
unset pg_pass

chmod 600 "$ENV_FILE"
echo "DATABASE_URL now uses host postiz-postgres. .env mode is 600." >&2
echo "Next: edit the public hostname and R2 values in .env, then run preflight.sh." >&2
echo "Do not cat .env into a chat, a ticket, or a shell with xtrace on." >&2
