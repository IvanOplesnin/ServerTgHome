#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="server-tg-home"
DEFAULT_REPO_URL="git@github-servertghome:IvanOplesnin/ServerTgHome.git"
DEFAULT_BRANCH="main"
DEFAULT_APP_DIR="/opt/server-tg-home"
DEFAULT_TIMER_INTERVAL="10min"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${STH_APP_DIR:-}" ] && [ -f "$SCRIPT_REPO_ROOT/docker-compose.yml" ]; then
  STH_APP_DIR="$SCRIPT_REPO_ROOT"
fi

APP_DIR="${STH_APP_DIR:-$DEFAULT_APP_DIR}"
REPO_URL="${STH_REPO_URL:-$DEFAULT_REPO_URL}"
BRANCH="${STH_BRANCH:-$DEFAULT_BRANCH}"
TIMER_INTERVAL="${STH_UPDATE_INTERVAL:-$DEFAULT_TIMER_INTERVAL}"
FORCE="${STH_FORCE:-0}"
PULL_IMAGES="${STH_PULL_IMAGES:-0}"
START_WITH_NEW_CONFIG="${STH_START_WITH_NEW_CONFIG:-0}"
INSTALL_DOCKER="${STH_INSTALL_DOCKER:-1}"

REPO_CHANGED=0
RUNTIME_FILES_CREATED=0

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '[%s] %s\n' "$APP_NAME" "$*"
}

die() {
  printf '[%s] ERROR: %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $0 [command]

Commands:
  init             Install dependencies, clone/update repo, create .env and config if missing.
  deploy|update   Pull git changes, rebuild images when needed, and run docker compose up -d.
  restart         Recreate the docker compose stack without pulling git changes.
  status          Show docker compose status and systemd timer status.
  logs            Follow docker compose logs.
  ssh-key         Create an SSH deploy key for GitHub and print the public key.
  install-timer   Install a systemd timer that runs deploy periodically.
  uninstall-timer Remove the systemd timer.

Environment:
  STH_APP_DIR=$DEFAULT_APP_DIR
  STH_REPO_URL=$DEFAULT_REPO_URL
  STH_BRANCH=$DEFAULT_BRANCH
  STH_UPDATE_INTERVAL=$DEFAULT_TIMER_INTERVAL
  STH_FORCE=1                    Force rebuild/recreate on deploy.
  STH_PULL_IMAGES=1              Pull postgres/redis images even when git has no updates.
  STH_START_WITH_NEW_CONFIG=1    Start even if .env/config.yaml were just created.
EOF
}

require_sudo_if_needed() {
  if [ -n "$SUDO" ] && ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required when the script is not run as root"
  fi
}

apt_install() {
  require_sudo_if_needed
  $SUDO apt-get update
  $SUDO apt-get install -y "$@"
}

install_base_packages() {
  local packages=()
  command -v git >/dev/null 2>&1 || packages+=(git)
  command -v curl >/dev/null 2>&1 || packages+=(curl)
  dpkg -s ca-certificates >/dev/null 2>&1 || packages+=(ca-certificates)

  if [ "${#packages[@]}" -gt 0 ]; then
    log "Installing base packages: ${packages[*]}"
    apt_install "${packages[@]}"
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [ "$INSTALL_DOCKER" != "1" ]; then
    die "Docker Compose plugin is not available. Set STH_INSTALL_DOCKER=1 or install Docker manually."
  fi

  require_sudo_if_needed
  log "Installing Docker Engine and Compose plugin"
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl gnupg git
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO rm -f /etc/apt/keyrings/docker.gpg
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg

  # shellcheck disable=SC1091
  . /etc/os-release
  local codename="${VERSION_CODENAME:-}"
  [ -n "$codename" ] || die "Cannot detect Ubuntu codename from /etc/os-release"

  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$codename" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  if [ "$(id -u)" -ne 0 ]; then
    $SUDO usermod -aG docker "$(id -un)" || true
    log "Added $(id -un) to docker group for future sessions"
  fi
}

ensure_dependencies() {
  install_base_packages
  install_docker
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    require_sudo_if_needed
    $SUDO docker "$@"
  fi
}

compose() {
  (cd "$APP_DIR" && docker_cmd compose "$@")
}

prepare_app_parent() {
  local parent
  parent="$(dirname "$APP_DIR")"
  if [ ! -d "$parent" ]; then
    require_sudo_if_needed
    $SUDO mkdir -p "$parent"
    if [ -n "$SUDO" ]; then
      $SUDO chown "$(id -u):$(id -g)" "$parent"
    fi
  fi
}

ensure_git_safe_directory() {
  git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true
}

clone_or_update_repo() {
  prepare_app_parent
  REPO_CHANGED=0

  if [ ! -d "$APP_DIR/.git" ]; then
    if [ -e "$APP_DIR" ] && [ -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 2>/dev/null || true)" ]; then
      die "$APP_DIR exists but is not a git repository"
    fi
    log "Cloning $REPO_URL into $APP_DIR"
    rm -rf "$APP_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    REPO_CHANGED=1
    return
  fi

  ensure_git_safe_directory
  if ! git -C "$APP_DIR" diff --quiet || ! git -C "$APP_DIR" diff --cached --quiet; then
    die "Tracked files in $APP_DIR have local changes. Commit/stash them before deploy."
  fi

  local old_rev new_rev
  old_rev="$(git -C "$APP_DIR" rev-parse HEAD)"
  log "Fetching $BRANCH from origin"
  git -C "$APP_DIR" fetch --prune origin "$BRANCH"
  if git -C "$APP_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$APP_DIR" checkout "$BRANCH"
  else
    git -C "$APP_DIR" checkout -b "$BRANCH" "origin/$BRANCH"
  fi
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  new_rev="$(git -C "$APP_DIR" rev-parse HEAD)"

  if [ "$old_rev" != "$new_rev" ]; then
    REPO_CHANGED=1
    log "Updated $old_rev -> $new_rev"
  else
    log "No git updates"
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48
  fi
}

ensure_runtime_files() {
  mkdir -p "$APP_DIR/config" "$APP_DIR/data"

  if [ ! -f "$APP_DIR/config/config.yaml" ]; then
    cp "$APP_DIR/config/config.example.yaml" "$APP_DIR/config/config.yaml"
    RUNTIME_FILES_CREATED=1
    log "Created config/config.yaml from config.example.yaml"
  fi

  if [ ! -f "$APP_DIR/.env" ]; then
    local postgres_password webhook_token
    postgres_password="$(random_secret)"
    webhook_token="$(random_secret)"
    cat >"$APP_DIR/.env" <<EOF
TELEGRAM_BOT_TOKEN=
TELEGRAM_PROXY_URL=
STH_WEBHOOK_TOKEN=$webhook_token
HOME_ASSISTANT_TOKEN=
POSTGRES_DB=server_tg_home
POSTGRES_USER=server_tg_home
POSTGRES_PASSWORD=$postgres_password
EOF
    chmod 600 "$APP_DIR/.env"
    RUNTIME_FILES_CREATED=1
    log "Created .env with generated POSTGRES_PASSWORD and STH_WEBHOOK_TOKEN"
  fi
}

stack_running() {
  local services
  services="$(compose ps --status running --services 2>/dev/null || true)"
  for service in postgres redis api worker graph-worker buffer retention; do
    if ! printf '%s\n' "$services" | grep -qx "$service"; then
      return 1
    fi
  done
  return 0
}

deploy_stack() {
  if [ "$RUNTIME_FILES_CREATED" = "1" ] && [ "$START_WITH_NEW_CONFIG" != "1" ]; then
    cat <<EOF

Created initial runtime files. Edit them before starting the stack:
  $APP_DIR/.env
  $APP_DIR/config/config.yaml

Then run:
  $APP_DIR/scripts/deploy.sh deploy

EOF
    return
  fi

  if [ "$FORCE" != "1" ] && [ "$REPO_CHANGED" = "0" ] && stack_running; then
    log "Stack is already running and git has no updates"
    if [ "$PULL_IMAGES" = "1" ]; then
      compose pull postgres redis
    fi
    compose up -d --remove-orphans
    check_health
    return
  fi

  log "Pulling service images"
  compose pull postgres redis
  log "Building application images"
  compose build --pull
  log "Starting stack"
  compose up -d --remove-orphans
  check_health
}

check_health() {
  local url="${STH_HEALTH_URL:-http://127.0.0.1:8080/health}"
  log "Waiting for API health: $url"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "API is healthy"
      return
    fi
    sleep 2
  done
  log "API health check did not pass yet. Check logs with: $APP_DIR/scripts/deploy.sh logs"
}

create_ssh_key() {
  local ssh_dir key_path config_path
  ssh_dir="${STH_SSH_DIR:-$HOME/.ssh}"
  key_path="$ssh_dir/server_tg_home_github"
  config_path="$ssh_dir/config"
  mkdir -p "$ssh_dir"
  chmod 700 "$ssh_dir"

  if [ ! -f "$key_path" ]; then
    ssh-keygen -t ed25519 -C "server-tg-home-deploy-$(hostname)" -f "$key_path" -N ""
    chmod 600 "$key_path"
  fi

  touch "$config_path"
  chmod 600 "$config_path"
  if ! grep -q '^Host github-servertghome$' "$config_path"; then
    cat >>"$config_path" <<EOF

Host github-servertghome
  HostName github.com
  User git
  IdentityFile $key_path
  IdentitiesOnly yes
EOF
  fi

  cat <<EOF

Add this public key to GitHub as a read-only deploy key for IvanOplesnin/ServerTgHome:

$(cat "$key_path.pub")

Then test:
  ssh -T git@github-servertghome

EOF
}

install_timer() {
  require_sudo_if_needed
  clone_or_update_repo
  ensure_runtime_files

  local service_user service_user_lines
  service_user="${STH_SYSTEMD_USER:-}"
  if [ -z "$service_user" ] && [ "$(id -u)" -ne 0 ]; then
    service_user="$(id -un)"
  elif [ -z "$service_user" ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    service_user="$SUDO_USER"
  fi

  service_user_lines=""
  if [ -n "$service_user" ]; then
    $SUDO usermod -aG docker "$service_user" || true
    service_user_lines="User=$service_user
SupplementaryGroups=docker"
    log "Systemd update service will run as $service_user"
  else
    log "Systemd update service will run as root"
  fi

  log "Installing systemd timer"
  cat <<EOF | $SUDO tee /etc/systemd/system/server-tg-home-update.service >/dev/null
[Unit]
Description=Update Server Tg Home from git
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
$service_user_lines
Environment=STH_APP_DIR=$APP_DIR
Environment=STH_REPO_URL=$REPO_URL
Environment=STH_BRANCH=$BRANCH
ExecStart=/usr/bin/env bash $APP_DIR/scripts/deploy.sh deploy
EOF

  cat <<EOF | $SUDO tee /etc/systemd/system/server-tg-home-update.timer >/dev/null
[Unit]
Description=Periodically update Server Tg Home

[Timer]
OnBootSec=3min
OnUnitActiveSec=$TIMER_INTERVAL
RandomizedDelaySec=2min
Persistent=true

[Install]
WantedBy=timers.target
EOF

  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now server-tg-home-update.timer
  $SUDO systemctl list-timers server-tg-home-update.timer --no-pager
}

uninstall_timer() {
  require_sudo_if_needed
  $SUDO systemctl disable --now server-tg-home-update.timer >/dev/null 2>&1 || true
  $SUDO rm -f /etc/systemd/system/server-tg-home-update.timer /etc/systemd/system/server-tg-home-update.service
  $SUDO systemctl daemon-reload
  log "Systemd timer removed"
}

show_status() {
  compose ps
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files server-tg-home-update.timer --no-pager 2>/dev/null | grep -q '^server-tg-home-update.timer'; then
      systemctl status server-tg-home-update.timer --no-pager || true
    else
      log "Systemd timer is not installed"
    fi
  fi
}

command="${1:-deploy}"

case "$command" in
  init)
    ensure_dependencies
    clone_or_update_repo
    ensure_runtime_files
    log "Initial setup completed. Edit .env and config/config.yaml, then run deploy."
    ;;
  deploy|update)
    ensure_dependencies
    clone_or_update_repo
    ensure_runtime_files
    deploy_stack
    ;;
  restart)
    ensure_dependencies
    ensure_runtime_files
    log "Recreating stack"
    compose up -d --force-recreate --remove-orphans
    check_health
    ;;
  status)
    show_status
    ;;
  logs)
    compose logs -f --tail=200
    ;;
  ssh-key)
    create_ssh_key
    ;;
  install-timer)
    ensure_dependencies
    install_timer
    ;;
  uninstall-timer)
    uninstall_timer
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage
    die "Unknown command: $command"
    ;;
esac
