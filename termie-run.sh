#!/usr/bin/env bash
set -Eeuo pipefail

LOG_PATH="${TERMIE_LOG_PATH:-/tmp/termie.log}"

if [[ "$#" -eq 0 ]]; then
  echo "Usage: $(basename "$0") '<shell command>'"
  echo "   or: $(basename "$0") <command> [args...]"
  exit 1
fi

mkdir -p "$(dirname "$LOG_PATH")"
: > "$LOG_PATH"

{
  echo "Termie"
  echo "cmd: $*"
  echo "---"
} >> "$LOG_PATH"

if [[ "$#" -eq 1 ]]; then
  if command -v script >/dev/null 2>&1; then
    script -q -f -c "$1" /dev/null | tee -a "$LOG_PATH"
  elif command -v stdbuf >/dev/null 2>&1; then
    stdbuf -oL -eL bash -lc "$1" 2>&1 | stdbuf -oL -eL tee -a "$LOG_PATH"
  else
    bash -lc "$1" 2>&1 | tee -a "$LOG_PATH"
  fi
elif command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "$@" 2>&1 | stdbuf -oL -eL tee -a "$LOG_PATH"
else
  "$@" 2>&1 | tee -a "$LOG_PATH"
fi
