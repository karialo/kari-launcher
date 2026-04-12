#!/usr/bin/env bash
set -Eeuo pipefail

AUTH_KEY="${TAILSCALE_AUTH_KEY:-${TS_AUTHKEY:-}}"
HOSTNAME="${TAILSCALE_HOSTNAME:-$(hostname 2>/dev/null || echo kari)}"

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

systemctl enable --now tailscaled.service

if [[ -n "${AUTH_KEY}" ]]; then
  tailscale up --auth-key "${AUTH_KEY}" --hostname "${HOSTNAME}" --ssh
else
  cat <<EOF
Tailscale is installed and tailscaled is running.

Finish enrollment with:

  sudo tailscale up --hostname ${HOSTNAME} --ssh

Or rerun this script with:

  sudo TAILSCALE_AUTH_KEY=tskey-auth-... TAILSCALE_HOSTNAME=${HOSTNAME} $0
EOF
fi
