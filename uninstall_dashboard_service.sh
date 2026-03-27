#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="kari-dashboard.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./uninstall_dashboard_service.sh"
  exit 1
fi

if systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
  systemctl disable --now "${SERVICE_NAME}" || true
fi

rm -f "${SERVICE_DST}"
systemctl daemon-reload

echo "Removed ${SERVICE_NAME}"
