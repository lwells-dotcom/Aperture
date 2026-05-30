#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="${ROOT_DIR}/scripts/setup.sh"
DEFAULT_HOST_PORT=5050
PORT_ATTEMPTS=4

check_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "error: required command not found: ${command_name}" >&2
    exit 1
  fi
}

port_is_available() {
  local port="$1"
  ! nc -z 127.0.0.1 "$port" >/dev/null 2>&1
}

find_available_port() {
  local base_port="$1"
  local attempts="$2"
  local port

  for ((offset=0; offset<attempts; offset++)); do
    port=$((base_port + offset))
    if port_is_available "$port"; then
      echo "$port"
      return 0
    fi
  done

  return 1
}

ensure_setup() {
  if [[ ! -d "${ROOT_DIR}/.venv" ]]; then
    echo "warning: .venv is missing. Running scripts/setup.sh first."
    "${SETUP_SCRIPT}"
  fi

  if [[ ! -f "${ROOT_DIR}/.env" ]]; then
    echo "warning: .env is missing. Running scripts/setup.sh first."
    "${SETUP_SCRIPT}"
  fi
}

MODE="dev"
for arg in "$@"; do
  case "$arg" in
    --stop)
      MODE="stop"
      ;;
    --help|-h)
      echo "Usage: bash scripts/run.sh [--stop]"
      echo ""
      echo "  (default)  Build and run Atlas (Flask + Postgres) in Docker Compose"
      echo "  --stop     Stop and remove running containers"
      exit 0
      ;;
    *)
      echo "error: unknown argument: ${arg}" >&2
      exit 1
      ;;
  esac
done

check_command python3
check_command docker
check_command nc

if ! docker compose version >/dev/null 2>&1; then
  echo "error: docker compose is required but not available" >&2
  exit 1
fi

if [[ "${MODE}" == "stop" ]]; then
  echo "Stopping Atlas containers"
  docker compose -f "${ROOT_DIR}/docker-compose.yml" down --remove-orphans || true
  exit 0
fi

echo "Checking local setup"
ensure_setup

HOST_PORT="$(find_available_port "$DEFAULT_HOST_PORT" "$PORT_ATTEMPTS")" || {
  echo "error: no open host ports found in the range ${DEFAULT_HOST_PORT}-$((DEFAULT_HOST_PORT + PORT_ATTEMPTS - 1))" >&2
  exit 1
}

if [[ "$HOST_PORT" != "$DEFAULT_HOST_PORT" ]]; then
  echo "warning: port ${DEFAULT_HOST_PORT} is busy. Using http://localhost:${HOST_PORT} instead."
else
  echo "Using http://localhost:${HOST_PORT}"
fi

echo "Building and starting Atlas"
WEB_PORT="$HOST_PORT" docker compose -f "${ROOT_DIR}/docker-compose.yml" up --build
