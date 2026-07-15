#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="server-tg-home"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${STH_APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '[%s-restore] %s\n' "$APP_NAME" "$*"
}

die() {
  printf '[%s-restore] ERROR: %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $0 <backup.tar.gz>

Restores:
  - Postgres dump
  - .env
  - config/config.yaml
  - data/ only if it exists in the archive
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

backup_path="${1:-}"
if [ "$backup_path" = "-h" ] || [ "$backup_path" = "--help" ]; then
  usage
  exit 0
fi
if [ -z "$backup_path" ]; then
  usage
  exit 1
fi

[ -f "$backup_path" ] || die "Backup archive does not exist: $backup_path"
[ -f "$APP_DIR/docker-compose.yml" ] || die "docker-compose.yml does not exist in $APP_DIR"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

log "Extracting $backup_path"
tar -xzf "$backup_path" -C "$tmp_dir"

[ -f "$tmp_dir/postgres.dump" ] || die "postgres.dump is missing from backup"
[ -f "$tmp_dir/.env" ] || die ".env is missing from backup"
[ -f "$tmp_dir/config/config.yaml" ] || die "config/config.yaml is missing from backup"

timestamp="$(date -u +%Y%m%d-%H%M%S)"

log "Stopping application services"
compose stop api worker graph-worker buffer retention >/dev/null 2>&1 || true

log "Saving current runtime files before restore"
[ ! -f "$APP_DIR/.env" ] || cp "$APP_DIR/.env" "$APP_DIR/.env.restore-before-$timestamp"
mkdir -p "$APP_DIR/config"
[ ! -f "$APP_DIR/config/config.yaml" ] || cp "$APP_DIR/config/config.yaml" "$APP_DIR/config/config.yaml.restore-before-$timestamp"

cp "$tmp_dir/.env" "$APP_DIR/.env"
cp "$tmp_dir/config/config.yaml" "$APP_DIR/config/config.yaml"
chmod 600 "$APP_DIR/.env"

if [ -d "$tmp_dir/data" ]; then
  log "Restoring data directory"
  if [ -d "$APP_DIR/data" ]; then
    mv "$APP_DIR/data" "$APP_DIR/data.restore-before-$timestamp"
  fi
  cp -a "$tmp_dir/data" "$APP_DIR/data"
fi

compose up -d postgres redis >/dev/null
log "Waiting for Postgres"
postgres_ready=0
for _ in $(seq 1 60); do
  if compose exec -T postgres sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    postgres_ready=1
    break
  fi
  sleep 2
done
[ "$postgres_ready" = "1" ] || die "Postgres did not become ready in time"

log "Restoring Postgres"
compose exec -T postgres sh -lc 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <"$tmp_dir/postgres.dump"

log "Starting stack"
compose up -d --remove-orphans
log "Restore completed"
