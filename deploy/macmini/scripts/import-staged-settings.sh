#!/usr/bin/env bash
# Optional. Copy a short allowlist of non-secret settings from an older
# staged .env into this kit's .env. Secrets are never copied and never printed.
# Generate secrets on this Mac with gen-secrets.sh. No sudo.

set -euo pipefail
set +o xtrace

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/import-staged-settings.sh /path/to/old.env" >&2
  exit 1
fi

OLD=$1
ROOT=$(kit_root)
ENV_FILE="${ROOT}/.env"

if [ ! -f "$OLD" ]; then
  echo "No file at ${OLD}" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "Run scripts/gen-secrets.sh before importing." >&2
  exit 1
fi

# Domains, bucket name, public CDN URL, R2 account id, region.
# Access keys, client secrets, JWT, and passwords are not on this list.
for key in \
  MAIN_URL FRONTEND_URL NEXT_PUBLIC_BACKEND_URL STORAGE_PROVIDER \
  CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_BUCKETNAME CLOUDFLARE_BUCKET_URL \
  CLOUDFLARE_REGION
do
  if is_secret_name "$key"; then
    echo "Refusing ${key}: the name looks like a secret" >&2
    exit 1
  fi
  value=$(env_get "$OLD" "$key" || true)
  if is_placeholder "$value"; then
    echo "Skipped ${key}: empty or placeholder in the old file" >&2
    continue
  fi
  case "$key" in
    MAIN_URL | FRONTEND_URL)
      if ! is_public_https_origin "$value"; then
        echo "Skipped ${key}: not a public https origin" >&2
        continue
      fi
      ;;
    NEXT_PUBLIC_BACKEND_URL)
      case "$value" in
        https://*/api) ;;
        *)
          echo "Skipped ${key}: expected https://<host>/api" >&2
          continue
          ;;
      esac
      if ! is_public_https_origin "${value%/api}"; then
        echo "Skipped ${key}: host is not a public https origin" >&2
        continue
      fi
      ;;
    STORAGE_PROVIDER)
      if [ "$value" != "cloudflare" ]; then
        echo "Skipped STORAGE_PROVIDER: this install uses cloudflare" >&2
        continue
      fi
      ;;
    CLOUDFLARE_BUCKET_URL)
      case "$value" in
        https://*/)
          echo "Skipped CLOUDFLARE_BUCKET_URL: trailing slash" >&2
          continue
          ;;
        https://*) ;;
        *)
          echo "Skipped CLOUDFLARE_BUCKET_URL: not https" >&2
          continue
          ;;
      esac
      ;;
    CLOUDFLARE_REGION)
      if [ "$value" != "auto" ]; then
        echo "Skipped CLOUDFLARE_REGION: R2 uses auto" >&2
        continue
      fi
      ;;
  esac
  set_env_key "$ENV_FILE" "$key" "$value"
  echo "Updated ${key}" >&2
done

chmod 600 "$ENV_FILE"
echo "Import finished. Secrets in the old file were not read into .env." >&2
echo "If the old file had a localhost or social.localhost URL, it was skipped on purpose." >&2
