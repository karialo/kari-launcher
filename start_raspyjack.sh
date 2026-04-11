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
RJ_EXTRA_SERVICES="${RJ_EXTRA_SERVICES-raspyjack-caddy-autoconfig.service raspyjack-pin-wifi.service}"
if [[ -n "${RJ_EXTRA_SERVICES}" ]]; then
  read -r -a RJ_EXTRA_SERVICE_ARRAY <<< "${RJ_EXTRA_SERVICES}"
  RJ_SERVICES+=("${RJ_EXTRA_SERVICE_ARRAY[@]}")
fi

RJ_AVAILABLE_SERVICES=()
for svc in "${RJ_SERVICES[@]}"; do
  if systemctl list-unit-files "$svc" --no-legend 2>/dev/null | awk '{print $1}' | grep -Fxq "$svc"; then
    RJ_AVAILABLE_SERVICES+=("$svc")
  else
    echo "Skipping missing RaspyJack unit: $svc"
  fi
done

if [[ "${#RJ_AVAILABLE_SERVICES[@]}" -eq 0 ]]; then
  echo "No RaspyJack systemd units were found." >&2
  exit 1
fi

echo "Stopping launcher..."
sudo systemctl stop "${LAUNCHER_SERVICE}"

echo "Starting RaspyJack stack..."
sudo systemctl start "${RJ_AVAILABLE_SERVICES[@]}"

echo
echo "Service status:"
systemctl --no-pager --full status "${LAUNCHER_SERVICE}" "${RJ_AVAILABLE_SERVICES[@]}" | sed -n '1,60p'

echo
echo "If your RaspyJack install is not service-based, replace the systemctl calls in this wrapper with your own launch command."
