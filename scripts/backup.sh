#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="server-tg-home"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${STH_APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DIR="${STH_BACKUP_DIR:-$APP_DIR/backups}"
INCLUDE_DATA="${STH_BACKUP_INCLUDE_DATA:-0}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '[%s-backup] %s\n' "$APP_NAME" "$*"
}

die() {
  printf '[%s-backup] ERROR: %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $0

Creates a backup archive with:
  - Postgres dump
  - .env
  - config/config.yaml

Environment:
  STH_BACKUP_DIR=$BACKUP_DIR
  STH_BACKUP_INCLUDE_DATA=1    Also include the data/ directory.
EOF
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    $SUDO docker "$@"
  fi
}

compose() {
  (cd "$APP_DIR" && docker_cmd compose "$@")
}

require_file() {
  [ -f "$1" ] || die "Required file does not exist: $1"
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

require_file "$APP_DIR/docker-compose.yml"
require_file "$APP_DIR/.env"
require_file "$APP_DIR/config/config.yaml"

mkdir -p "$BACKUP_DIR"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

timestamp="$(date -u +%Y%m%d-%H%M%S)"
archive="$BACKUP_DIR/server-tg-home-$timestamp.tar.gz"

log "Preparing backup in $tmp_dir"
mkdir -p "$tmp_dir/config"
cp "$APP_DIR/.env" "$tmp_dir/.env"
cp "$APP_DIR/config/config.yaml" "$tmp_dir/config/config.yaml"

log "Dumping Postgres"
compose up -d postgres >/dev/null
compose exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$tmp_dir/postgres.dump"

cat >"$tmp_dir/manifest.txt" <<EOF
created_at_utc=$timestamp
include_data=$INCLUDE_DATA
app_dir=$APP_DIR
EOF

if [ "$INCLUDE_DATA" = "1" ]; then
  log "Including data directory"
  tar -czf "$archive" -C "$tmp_dir" . -C "$APP_DIR" data
else
  tar -czf "$archive" -C "$tmp_dir" .
fi

chmod 600 "$archive"
log "Backup created: $archive"
