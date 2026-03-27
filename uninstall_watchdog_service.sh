#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="kari-watchdog.service"
TIMER_NAME="kari-watchdog.timer"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
TIMER_DST="/etc/systemd/system/${TIMER_NAME}"
ENV_DST="/etc/default/kari-watchdog"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./uninstall_watchdog_service.sh"
  exit 1
fi

if systemctl list-unit-files | grep -q "^${TIMER_NAME}"; then
  systemctl disable --now "${TIMER_NAME}" || true
fi

if systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
  systemctl stop "${SERVICE_NAME}" || true
fi

rm -f "${SERVICE_DST}" "${TIMER_DST}"
systemctl daemon-reload

echo "Removed ${SERVICE_NAME} and ${TIMER_NAME}"
echo "Config kept at ${ENV_DST} (remove manually if not needed)"
