#!/usr/bin/env bash
set -euo pipefail

SOURCE_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "${SOURCE_PATH}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for reproducible verification. Install from https://docs.astral.sh/uv/." >&2
  exit 1
fi

cd "${REPO_ROOT}"

with_accel=()
if [[ "${SKIP_ACCEL_INSTALL:-0}" != "1" ]]; then
  with_accel=(--with-editable "${REPO_ROOT}/dhee-accel")
fi

if [[ "$#" -eq 0 ]]; then
  set -- -q
fi

uv run \
  --no-project \
  --with-editable "${REPO_ROOT}" \
  --with pytest \
  --with pytest-asyncio \
  "${with_accel[@]}" \
  pytest "$@"
