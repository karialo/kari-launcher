#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAMES=("kari-bootscreen.service" "kari-dashboard.service")

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./uninstall_dashboard_service.sh"
  exit 1
fi

for SERVICE_NAME in "${SERVICE_NAMES[@]}"; do
  SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
  if systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
    systemctl disable --now "${SERVICE_NAME}" || true
  fi
  rm -f "${SERVICE_DST}"
done
systemctl daemon-reload

echo "Removed kari-bootscreen.service and kari-dashboard.service"
