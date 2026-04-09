#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6 || true)"
if [[ -z "${RUN_HOME}" ]]; then
  RUN_HOME="/home/${RUN_USER}"
fi

PROJECTS_DIR="${PROJECTS_DIR:-${RUN_HOME}/Projects}"
REPO_DIR="${REPO_DIR:-${HERE}}"
CFG_DIR="${RUN_HOME}/.config/launcher"
CFG_PATH="${CFG_DIR}/dashboard.json"
RESULTS_DIR="${RUN_HOME}/Results"
RASPYJACK_DIR="${PROJECTS_DIR}/Raspyjack"
ANGRYOXIDE_DIR="${PROJECTS_DIR}/AngryOxide"
KISMET_DIR="${PROJECTS_DIR}/kismet"
VENV_DIR="${REPO_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"

APT_PACKAGES=(
  git
  curl
  util-linux
  build-essential
  python3
  python3-dev
  python3-pip
  python3-setuptools
  python3-venv
  python3-wheel
  network-manager
)

say() {
  printf '\n[%s] %s\n' "DIY" "$*"
}

warn() {
  printf '\n[%s] %s\n' "warn" "$*" >&2
}

die() {
  warn "$*"
  exit 1
}

ask() {
  local prompt="$1"
  local default="${2:-}"
  local answer=""
  if [[ -n "${default}" ]]; then
    read -r -p "${prompt} [${default}]: " answer
    printf '%s' "${answer:-$default}"
  else
    read -r -p "${prompt}: " answer
    printf '%s' "${answer}"
  fi
}

ask_secret() {
  local prompt="$1"
  local answer=""
  read -r -s -p "${prompt}: " answer
  printf '\n' >&2
  printf '%s' "${answer}"
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local suffix="[Y/n]"
  local answer=""
  if [[ "${default}" == "n" ]]; then
    suffix="[y/N]"
  fi
  read -r -p "${prompt} ${suffix}: " answer
  answer="${answer:-$default}"
  [[ "${answer}" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]
}

ask_choice() {
  local prompt="$1"
  shift
  local default="$1"
  shift
  local answer=""
  printf '%s\n' "${prompt}"
  while (($#)); do
    printf '  - %s\n' "$1"
    shift
  done
  read -r -p "Choice [${default}]: " answer
  printf '%s' "${answer:-$default}"
}

ensure_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required"
  fi
  sudo -v
}

install_apt_packages() {
  say "Installing launcher prerequisites"
  sudo apt update
  sudo apt install -y "${APT_PACKAGES[@]}"
}

create_directories() {
  say "Creating user directories"
  mkdir -p "${PROJECTS_DIR}" "${RESULTS_DIR}" "${CFG_DIR}"
}

setup_venv() {
  say "Creating launcher virtual environment"
  python3 -m venv "${VENV_DIR}"
  "${PIP_BIN}" install --upgrade pip wheel setuptools
  "${PIP_BIN}" install -r "${REPO_DIR}/requirements.txt"
}

ensure_base_config() {
  say "Writing launcher config scaffold"
  CFG_PATH="${CFG_PATH}" PYTHONPATH="${REPO_DIR}/src" "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
from launcher.dashboard import ensure_config

ensure_config(Path(__import__("os").environ["CFG_PATH"]))
PY
}

configure_nmcli_wifi() {
  local iface="$1"
  local profile="$2"
  local ssid="$3"
  local password="$4"

  [[ -n "${ssid}" ]] || return 0
  command -v nmcli >/dev/null 2>&1 || die "nmcli is required to configure ${iface}; install NetworkManager or skip Wi-Fi setup"
  say "Configuring NetworkManager maintenance link on ${iface}"

  if sudo nmcli -t -f NAME connection show | grep -Fxq "${profile}"; then
    sudo nmcli connection modify "${profile}" connection.interface-name "${iface}" 802-11-wireless.ssid "${ssid}" connection.autoconnect yes
    if [[ -n "${password}" ]]; then
      sudo nmcli connection modify "${profile}" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${password}"
    fi
  else
    if [[ -n "${password}" ]]; then
      sudo nmcli connection add type wifi ifname "${iface}" con-name "${profile}" ssid "${ssid}" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${password}" connection.autoconnect yes
    else
      sudo nmcli connection add type wifi ifname "${iface}" con-name "${profile}" ssid "${ssid}" connection.autoconnect yes
    fi
  fi

  sudo nmcli connection up "${profile}" ifname "${iface}" || true
}

clone_repo_if_needed() {
  local url="$1"
  local dest="$2"
  if [[ -d "${dest}/.git" ]]; then
    say "Keeping existing clone at ${dest}"
    return 0
  fi
  if [[ -e "${dest}" ]]; then
    warn "Skipping ${url}; destination already exists and is not a git repo: ${dest}"
    return 0
  fi
  git clone --depth 1 "${url}" "${dest}"
}

backup_file_if_present() {
  local path="$1"
  [[ -f "${path}" ]] || return 0
  cp -n "${path}" "${path}.kari-backup" || true
}

apply_raspyjack_patch_bundle() {
  [[ -d "${RASPYJACK_DIR}" ]] || die "RaspyJack directory not found: ${RASPYJACK_DIR}"
  say "Applying RaspyJack 1.3in compatibility bundle"
  for f in LCD_1in44.py LCD_ST7789.py raspyjack.py; do
    backup_file_if_present "${RASPYJACK_DIR}/${f}"
    install -m 0644 "${REPO_DIR}/third_party/raspyjack_patch/files/${f}" "${RASPYJACK_DIR}/${f}"
  done
}

disable_service_units_if_present() {
  local svc=""
  for svc in "$@"; do
    [[ -n "${svc}" ]] || continue
    if systemctl list-unit-files | grep -Fq "${svc}"; then
      say "Stopping and disabling ${svc}"
      sudo systemctl stop "${svc}" || true
      sudo systemctl disable "${svc}" || true
    fi
  done
}

configure_raspyjack_service_override() {
  local core_service="$1"
  [[ -n "${core_service}" ]] || return 0
  if ! systemctl list-unit-files | grep -Fq "${core_service}"; then
    warn "RaspyJack core service not found for override: ${core_service}"
    return 0
  fi

  say "Installing RaspyJack service override for launcher handoff"
  sudo install -d "/etc/systemd/system/${core_service}.d"
  sudo tee "/etc/systemd/system/${core_service}.d/kari-panel.conf" >/dev/null <<EOF
[Service]
Environment=RJ_LCD=${RASPYJACK_LCD_BACKEND}
Environment=RJ_ROTATE=${RASPYJACK_ROTATE}
Environment=RJ_PANEL_WIDTH=${RASPYJACK_PANEL_WIDTH}
Environment=RJ_PANEL_HEIGHT=${RASPYJACK_PANEL_HEIGHT}
Environment="RJ_RETURN_TO_LAUNCHER_CMD=${REPO_DIR}/stop_raspyjack.sh >/dev/null 2>&1"
EOF
  sudo systemctl daemon-reload
}

verify_command_or_path() {
  local label="$1"
  local raw="$2"
  local head
  head="${raw%% *}"
  [[ -n "${head}" ]] || {
    warn "${label}: empty"
    return 1
  }
  if [[ "${head}" == */* ]]; then
    [[ -e "${head}" ]] || {
      warn "${label}: path not found: ${head}"
      return 1
    }
    return 0
  fi
  command -v "${head}" >/dev/null 2>&1 || {
    warn "${label}: command not found in PATH: ${head}"
    return 1
  }
}

install_kismet_integration() {
  say "Installing Kismet package and launcher helper"
  sudo apt install -y kismet
  sudo install -m 0755 "${REPO_DIR}/scripts/kismet-source-autoconfig.sh" /usr/local/bin/kismet-source-autoconfig.sh
  sudo install -d /etc/systemd/system/kismet.service.d
  sudo install -m 0644 "${REPO_DIR}/scripts/kismet.service.override.conf" /etc/systemd/system/kismet.service.d/override.conf
  sudo systemctl daemon-reload
  sudo systemctl enable kismet.service || true
  sudo systemctl restart kismet.service || true
}

write_config_values() {
  say "Applying launcher config"
  CFG_PATH="${CFG_PATH}" \
  RUN_HOME="${RUN_HOME}" \
  PRIMARY_IFACE="${PRIMARY_IFACE}" \
  MONITOR_IFACE="${MONITOR_IFACE}" \
  DISPLAY_BACKEND="${DISPLAY_BACKEND}" \
  DISPLAY_PANEL="${DISPLAY_PANEL}" \
  UI_PROFILE="${UI_PROFILE}" \
  DISPLAY_ROTATION="${DISPLAY_ROTATION}" \
  DISPLAY_INVERT="${DISPLAY_INVERT}" \
  DISPLAY_SPI_SPEED="${DISPLAY_SPI_SPEED}" \
  WIFI_PROFILE="${WIFI_PROFILE}" \
  LOCAL_BUTTONS="${LOCAL_BUTTONS}" \
  REMOTE_PORT="${REMOTE_PORT}" \
  REMOTE_TOKEN="${REMOTE_TOKEN}" \
  RASPYJACK_DIR="${RASPYJACK_DIR}" \
  RASPYJACK_CORE_SERVICE="${RASPYJACK_CORE_SERVICE}" \
  RASPYJACK_DEVICE_SERVICE="${RASPYJACK_DEVICE_SERVICE}" \
  RASPYJACK_WEB_SERVICE="${RASPYJACK_WEB_SERVICE}" \
  ANGRYOXIDE_CMD="${ANGRYOXIDE_CMD}" \
  RESULTS_DIR="${RESULTS_DIR}" \
  INSTALL_KISMET="${INSTALL_KISMET}" \
  KISMET_SERVICE_NAME="${KISMET_SERVICE_NAME}" \
  REPO_DIR="${REPO_DIR}" \
  "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

cfg_path = Path(os.environ["CFG_PATH"])
data = json.loads(cfg_path.read_text(encoding="utf-8"))
run_home = Path(os.environ["RUN_HOME"])
primary_iface = os.environ["PRIMARY_IFACE"]
monitor_iface = os.environ["MONITOR_IFACE"]
display_backend = os.environ["DISPLAY_BACKEND"]
display_panel = os.environ["DISPLAY_PANEL"]
ui_profile = os.environ["UI_PROFILE"]
display_rotation = int(os.environ["DISPLAY_ROTATION"])
display_invert = os.environ["DISPLAY_INVERT"].lower() in {"1", "true", "yes", "on"}
display_spi_speed = int(os.environ["DISPLAY_SPI_SPEED"])
wifi_profile = os.environ["WIFI_PROFILE"]
results_dir = Path(os.environ["RESULTS_DIR"])
raspyjack_dir = Path(os.environ["RASPYJACK_DIR"])
raspyjack_core_service = os.environ["RASPYJACK_CORE_SERVICE"]
raspyjack_device_service = os.environ["RASPYJACK_DEVICE_SERVICE"]
raspyjack_web_service = os.environ["RASPYJACK_WEB_SERVICE"]
angryoxide_cmd = os.environ["ANGRYOXIDE_CMD"]
local_buttons = os.environ["LOCAL_BUTTONS"].lower() in {"1", "true", "yes", "on"}
remote_port = int(os.environ["REMOTE_PORT"])
remote_token = os.environ["REMOTE_TOKEN"]
install_kismet = os.environ["INSTALL_KISMET"].lower() in {"1", "true", "yes", "on"}
kismet_service_name = os.environ["KISMET_SERVICE_NAME"]

managed_apps = data.setdefault("managed_apps", {})
managed_apps.setdefault("termie", {})
managed_apps["termie"].update({
    "label": "Termie",
    "start_cmd": str(Path(os.environ.get("REPO_DIR", "")) / "start_termie.sh") if os.environ.get("REPO_DIR") else str(run_home / "Projects" / "kari-launcher" / "start_termie.sh"),
    "stop_cmd": str(Path(os.environ.get("REPO_DIR", "")) / "stop_termie.sh") if os.environ.get("REPO_DIR") else str(run_home / "Projects" / "kari-launcher" / "stop_termie.sh"),
    "status_cmd": "systemctl is-active termie.service",
    "takes_over_display": True,
})
managed_apps.setdefault("raspyjack", {})
managed_apps["raspyjack"].update({
    "label": "RaspyJack",
    "start_cmd": str(Path(os.environ.get("REPO_DIR", "")) / "start_raspyjack.sh") if os.environ.get("REPO_DIR") else str(run_home / "Projects" / "kari-launcher" / "start_raspyjack.sh"),
    "stop_cmd": str(Path(os.environ.get("REPO_DIR", "")) / "stop_raspyjack.sh") if os.environ.get("REPO_DIR") else str(run_home / "Projects" / "kari-launcher" / "stop_raspyjack.sh"),
    "status_cmd": f"systemctl is-active {raspyjack_core_service} {raspyjack_device_service} {raspyjack_web_service}".strip(),
    "takes_over_display": True,
})

data["local_buttons_enabled"] = local_buttons
data["nodes"] = []

hardware = data.setdefault("hardware", {})
hardware.update({
    "panel": display_panel,
    "ui_profile": ui_profile,
    "backend": display_backend,
    "rotation": display_rotation,
    "invert": display_invert,
    "spi_speed_hz": display_spi_speed,
})

network_ops = data.setdefault("network_ops", {})
network_ops.update({
    "primary_iface": primary_iface,
    "monitor_iface": monitor_iface,
    "wifi_profile": wifi_profile,
    "networkmanager_service": "NetworkManager.service",
    "tailscale_service": "tailscaled.service",
    "reboot_cmd": "systemctl reboot",
})

raspyjack = data.setdefault("raspyjack", {})
raspyjack.update({
    "service_names": [raspyjack_core_service, raspyjack_device_service, raspyjack_web_service],
    "webui_service_names": [raspyjack_web_service],
    "loot_path": str(raspyjack_dir / "loot"),
    "primary_interface": primary_iface,
    "monitor_interface": monitor_iface,
})

foxhunt = data.setdefault("foxhunt", {})
foxhunt["interface"] = monitor_iface

wifite = data.setdefault("wifite", {})
wifite["interface"] = monitor_iface

nmap = data.setdefault("nmap", {})
nmap["interface"] = primary_iface

angryoxide = data.setdefault("angryoxide", {})
angryoxide.update({
    "interface": monitor_iface,
    "command": angryoxide_cmd,
    "log_path": str(results_dir / "angryoxide-live.log"),
    "results_dir": str(results_dir),
})

kismet = data.setdefault("kismet", {})
kismet.update({
    "primary_interface": primary_iface,
    "networkmanager_service": "NetworkManager.service",
    "service_names": [kismet_service_name],
})

remote = data.setdefault("remote", {})
remote.update({
    "enabled": True,
    "host": "0.0.0.0",
    "port": remote_port,
    "token": remote_token,
})

cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

install_launcher_services() {
  say "Installing launcher services"
  sudo "${REPO_DIR}/install_dashboard_service.sh"
}

install_watchdog_if_requested() {
  say "Installing watchdog"
  sudo "${REPO_DIR}/install_watchdog_service.sh"
}

verify_external_plumbing() {
  say "Verifying external tool plumbing"
  verify_command_or_path "AngryOxide command" "${ANGRYOXIDE_CMD}" || true

  if [[ -d "${RASPYJACK_DIR}" ]]; then
    [[ -f "${RASPYJACK_DIR}/raspyjack.py" ]] || warn "RaspyJack root does not contain raspyjack.py: ${RASPYJACK_DIR}"
    if [[ "${DISPLAY_BACKEND}" == "waveshare_1in3" ]]; then
      [[ -f "${RASPYJACK_DIR}/LCD_ST7789.py" ]] || warn "RaspyJack 1.3in patch files are not present in ${RASPYJACK_DIR}"
    fi
  else
    warn "RaspyJack directory not present: ${RASPYJACK_DIR}"
  fi

  if [[ "${INSTALL_KISMET}" != "true" && -n "${KISMET_SERVICE_NAME}" ]]; then
    if ! systemctl list-unit-files | grep -Fq "${KISMET_SERVICE_NAME}"; then
      warn "Kismet service unit not found: ${KISMET_SERVICE_NAME}"
    fi
  fi
}

show_summary() {
  say "DIY install summary"
  printf 'User: %s\n' "${RUN_USER}"
  printf 'Repo: %s\n' "${REPO_DIR}"
  printf 'Config: %s\n' "${CFG_PATH}"
  printf 'Primary iface: %s\n' "${PRIMARY_IFACE}"
  printf 'Monitor iface: %s\n' "${MONITOR_IFACE}"
  printf 'Display backend: %s\n' "${DISPLAY_BACKEND}"
  printf 'Wi-Fi profile: %s\n' "${WIFI_PROFILE:-<blank>}"
  printf 'Results dir: %s\n' "${RESULTS_DIR}"
  printf 'RaspyJack dir: %s\n' "${RASPYJACK_DIR}"
  printf 'RaspyJack services: %s %s %s\n' "${RASPYJACK_CORE_SERVICE}" "${RASPYJACK_DEVICE_SERVICE}" "${RASPYJACK_WEB_SERVICE}"
  printf 'AngryOxide command: %s\n' "${ANGRYOXIDE_CMD}"
  printf 'Kismet integration: %s\n' "${INSTALL_KISMET}"
  printf 'Launcher service install: %s\n' "${INSTALL_SERVICES}"
  printf 'Watchdog install: %s\n' "${INSTALL_WATCHDOG}"
}

main() {
  [[ -d "${REPO_DIR}/src/launcher" ]] || die "Run this from the kari-launcher repo"
  ensure_sudo

  say "This wizard bootstraps K.A.R.I Launcher on Raspberry Pi OS."
  say "It prepares directories, Python, config, Wi-Fi, and optional launcher integrations."

  INSTALL_APT="false"
  if ask_yes_no "Install or refresh apt prerequisites" "y"; then
    INSTALL_APT="true"
    install_apt_packages
  fi

  create_directories
  setup_venv

  DISPLAY_CHOICE="$(ask_choice "Select the launcher display target" "1.3" "1.3 = Waveshare 1.3in 240x240 (recommended)" "1.44 = Waveshare 1.44in 128x128 (scaled launcher UI, less readable)")"
  case "${DISPLAY_CHOICE}" in
    1.44|1_44|144)
      DISPLAY_PANEL="waveshare_1in44"
      UI_PROFILE="compact128_scaled"
      DISPLAY_BACKEND="waveshare_1in44"
      DISPLAY_ROTATION="0"
      DISPLAY_INVERT="false"
      DISPLAY_SPI_SPEED="9000000"
      RASPYJACK_LCD_BACKEND="st7735"
      RASPYJACK_ROTATE="0"
      RASPYJACK_PANEL_WIDTH="128"
      RASPYJACK_PANEL_HEIGHT="128"
      ;;
    *)
      DISPLAY_PANEL="waveshare_1in3"
      UI_PROFILE="standard240"
      DISPLAY_BACKEND="waveshare_1in3"
      DISPLAY_ROTATION="90"
      DISPLAY_INVERT="true"
      DISPLAY_SPI_SPEED="24000000"
      RASPYJACK_LCD_BACKEND="st7789"
      RASPYJACK_ROTATE="0"
      RASPYJACK_PANEL_WIDTH="240"
      RASPYJACK_PANEL_HEIGHT="240"
      ;;
  esac

  PRIMARY_IFACE="$(ask "Primary maintenance interface" "wlan0")"
  MONITOR_IFACE="$(ask "Monitor/capture interface" "wlan1")"
  LOCAL_BUTTONS="false"
  if ask_yes_no "Enable local joystick/button input in launcher config" "y"; then
    LOCAL_BUTTONS="true"
  fi

  WIFI_SSID="$(ask "Maintenance Wi-Fi SSID for ${PRIMARY_IFACE} (blank to skip)")"
  WIFI_PASSWORD=""
  WIFI_PROFILE=""
  if [[ -n "${WIFI_SSID}" ]]; then
    WIFI_PROFILE="$(ask "NetworkManager profile name" "${WIFI_SSID}")"
    WIFI_PASSWORD="$(ask_secret "Wi-Fi password for ${WIFI_SSID} (leave blank for open network)")"
    configure_nmcli_wifi "${PRIMARY_IFACE}" "${WIFI_PROFILE}" "${WIFI_SSID}" "${WIFI_PASSWORD}"
  fi

  REMOTE_PORT="$(ask "Launcher web port" "8787")"
  REMOTE_TOKEN=""
  if ask_yes_no "Set a remote-control token now" "n"; then
    REMOTE_TOKEN="$(ask_secret "Remote token")"
  fi

  if ask_yes_no "Clone RaspyJack upstream into ${RASPYJACK_DIR}" "n"; then
    clone_repo_if_needed "https://github.com/7h30th3r0n3/Raspyjack.git" "${RASPYJACK_DIR}"
  fi

  RASPYJACK_CORE_SERVICE="$(ask "RaspyJack core service name" "raspyjack.service")"
  RASPYJACK_DEVICE_SERVICE="$(ask "RaspyJack device service name" "raspyjack-device.service")"
  RASPYJACK_WEB_SERVICE="$(ask "RaspyJack web service name" "raspyjack-webui.service")"

  if [[ "${DISPLAY_BACKEND}" == "waveshare_1in3" ]] && [[ -d "${RASPYJACK_DIR}" ]]; then
    if ask_yes_no "Apply the bundled RaspyJack 1.3in ST7789 compatibility patch" "y"; then
      apply_raspyjack_patch_bundle
    fi
  fi

  if ask_yes_no "Stop and disable RaspyJack services so the launcher owns startup by default" "y"; then
    disable_service_units_if_present "${RASPYJACK_CORE_SERVICE}" "${RASPYJACK_DEVICE_SERVICE}" "${RASPYJACK_WEB_SERVICE}"
  fi

  if ask_yes_no "Install a RaspyJack systemd override for launcher return/display env vars" "y"; then
    configure_raspyjack_service_override "${RASPYJACK_CORE_SERVICE}"
  fi

  if ask_yes_no "Clone AngryOxide upstream into ${ANGRYOXIDE_DIR}" "n"; then
    clone_repo_if_needed "https://github.com/Ragnt/AngryOxide.git" "${ANGRYOXIDE_DIR}"
  fi

  if ask_yes_no "Clone Kismet upstream source into ${KISMET_DIR}" "n"; then
    clone_repo_if_needed "https://github.com/kismetwireless/kismet.git" "${KISMET_DIR}"
  fi

  ANGRYOXIDE_CMD="$(ask "AngryOxide command path" "${RUN_HOME}/bin/angryoxide -i ${MONITOR_IFACE}")"

  ensure_base_config

  KISMET_SERVICE_NAME="kismet.service"
  INSTALL_KISMET="false"
  if ask_yes_no "Install Kismet package and launcher source-policy override" "n"; then
    INSTALL_KISMET="true"
    install_kismet_integration
  else
    KISMET_SERVICE_NAME="$(ask "Existing Kismet service name" "kismet.service")"
  fi

  REPO_DIR="${REPO_DIR}" write_config_values
  verify_external_plumbing

  INSTALL_SERVICES="false"
  if ask_yes_no "Install and enable launcher systemd services now" "y"; then
    INSTALL_SERVICES="true"
    install_launcher_services
  fi

  INSTALL_WATCHDOG="false"
  if ask_yes_no "Install the optional watchdog timer/service" "n"; then
    INSTALL_WATCHDOG="true"
    install_watchdog_if_requested
  fi

  show_summary
  say "Next steps: validate the panel, verify ${PRIMARY_IFACE} stays managed, and test each third-party tool outside the launcher before trusting its page."
}

main "$@"
