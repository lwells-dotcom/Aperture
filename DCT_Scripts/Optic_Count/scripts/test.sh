#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="${ROOT_DIR}/scripts/setup.sh"
PYTEST_BIN="${ROOT_DIR}/.venv/bin/pytest"

if [[ ! -d "${ROOT_DIR}/.venv" ]]; then
  echo "warning: .venv is missing. Running scripts/setup.sh first."
  "${SETUP_SCRIPT}"
fi

if [[ ! -x "${PYTEST_BIN}" ]]; then
  echo "error: pytest is not available in .venv. Re-run scripts/setup.sh." >&2
  exit 1
fi

cd "${ROOT_DIR}"

echo "Running tests"
"${PYTEST_BIN}" test_*.py -v "$@"
