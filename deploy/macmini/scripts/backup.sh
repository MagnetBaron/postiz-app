#!/usr/bin/env bash
# Dump the Postiz database, the Temporal databases, and the local uploads volume.
# Media bytes live in R2. The uploads tarball is only the local volume.
# Each run writes new files. It does not delete older backups.
# No sudo. Does not print secrets.

set -euo pipefail
set +o xtrace

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
refuse_root

ROOT=$(kit_root)
cd "$ROOT"
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "${ROOT}/backups"
chmod 700 "${ROOT}/backups"

if ! docker info >/dev/null 2>&1; then
  echo "docker is not running" >&2
  exit 1
fi

postiz_dump="${ROOT}/backups/postiz-${stamp}.sql.gz"
echo "Writing Postiz database dump" >&2
docker compose exec -T postiz-postgres \
  pg_dump -U postiz-user -d postiz-db-local | gzip -c >"$postiz_dump"
chmod 600 "$postiz_dump"

temporal_dump="${ROOT}/backups/temporal-${stamp}.sql.gz"
echo "Writing Temporal database dump" >&2
docker compose exec -T temporal-postgresql \
  pg_dumpall -U temporal | gzip -c >"$temporal_dump"
chmod 600 "$temporal_dump"

uploads_dump="${ROOT}/backups/uploads-${stamp}.tar.gz"
if docker volume inspect postiz_postiz-uploads >/dev/null 2>&1; then
  echo "Writing local uploads volume" >&2
  docker run --rm \
    -v postiz_postiz-uploads:/uploads:ro \
    -v "${ROOT}/backups:/backup" \
    --entrypoint tar \
    postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94 \
    czf "/backup/uploads-${stamp}.tar.gz" -C /uploads .
  chmod 600 "$uploads_dump"
else
  echo "No postiz_postiz-uploads volume yet, skipped the uploads tarball" >&2
fi

note="${ROOT}/backups/README-${stamp}.txt"
cat >"$note" <<EOF
Postiz backup ${stamp}
- postiz-${stamp}.sql.gz is the Postiz Postgres database.
- temporal-${stamp}.sql.gz is pg_dumpall of the Temporal database.
- uploads-${stamp}.tar.gz is the local uploads volume only.
Object bytes for published media are in the R2 bucket (CLOUDFLARE_BUCKETNAME
in .env), served from CLOUDFLARE_BUCKET_URL. This note contains no secrets.
Keep the dumps on this Mac. Mode 600.
EOF
chmod 600 "$note"
echo "Backup files are in ${ROOT}/backups (mode 600). Contents were not printed." >&2
