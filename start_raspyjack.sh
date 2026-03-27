#!/usr/bin/env bash
set -Eeuo pipefail

# Example launcher-managed RaspyJack start wrapper.
# Edit these values for your own install. This script is intentionally explicit:
# the launcher does not auto-detect your RaspyJack layout or service model.

LAUNCHER_SERVICE="${LAUNCHER_SERVICE:-kari-dashboard.service}"
RJ_SERVICES=(
  "${RJ_CORE_SERVICE:-raspyjack.service}"
  "${RJ_DEVICE_SERVICE:-raspyjack-device.service}"
  "${RJ_WEB_SERVICE:-raspyjack-webui.service}"
)

echo "Stopping launcher..."
sudo systemctl stop "${LAUNCHER_SERVICE}"

echo "Starting RaspyJack stack..."
sudo systemctl start "${RJ_SERVICES[@]}"

echo
echo "Service status:"
systemctl --no-pager --full status "${LAUNCHER_SERVICE}" "${RJ_SERVICES[@]}" | sed -n '1,60p'

echo
echo "If your RaspyJack install is not service-based, replace the systemctl calls in this wrapper with your own launch command."
