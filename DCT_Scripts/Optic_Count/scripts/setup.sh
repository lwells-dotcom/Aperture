#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

if [[ "${CANVAS_SKIP_DEV_BOOTSTRAP:-0}" != "1" ]] && command -v git >/dev/null 2>&1; then
  GIT_ROOT="$(git -C "${ROOT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "${GIT_ROOT}" && -x "${GIT_ROOT}/scripts/setup-canvas-dev.sh" ]]; then
    echo "Running Canvas developer bootstrap"
    bash "${GIT_ROOT}/scripts/setup-canvas-dev.sh" || true
  fi
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "error: ${PYTHON_BIN} is not installed or not on PATH" >&2
  exit 1
fi

echo "Creating virtual environment at ${VENV_DIR}"
if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "Virtual environment already exists at ${VENV_DIR}; reusing it"
fi

echo "Installing Python dependencies"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "Created .env from .env.example — fill in secrets before running"
else
  echo ".env already exists; leaving it in place"
fi

cat <<EOF

Setup complete.

Next steps:
  Fill in secrets in .env (DEMO_VERIFY_PIN, DEMO_TOKEN_SECRET, ANTHROPIC_API_KEY, NETBOX_API_TOKEN)
  bash scripts/run.sh
EOF
