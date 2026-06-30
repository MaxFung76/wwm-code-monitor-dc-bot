#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
USE_REGISTRY_IMAGE="${USE_REGISTRY_IMAGE:-false}"

log() {
  printf '[deploy] %s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

main() {
  require_command git
  require_command docker

  cd "$APP_DIR"

  if [ ! -f ".env" ]; then
    printf 'Missing .env in %s\n' "$APP_DIR" >&2
    exit 1
  fi

  mkdir -p data

  log "Fetching latest code from branch ${BRANCH}"
  git fetch --all --prune
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"

  if [ "$USE_REGISTRY_IMAGE" = "true" ]; then
    log "Pulling latest registry image"
    docker compose -f "$COMPOSE_FILE" pull wwm-codebot
    log "Starting containers from pulled image"
    docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
  else
    log "Building and starting containers"
    docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans
  fi

  log "Current container status"
  docker compose -f "$COMPOSE_FILE" ps

  log "Deployment complete"
}

main "$@"
