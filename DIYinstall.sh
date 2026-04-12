#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
if [[ "${RUN_USER}" == "root" ]]; then
  REPO_OWNER="$(stat -c '%U' "${HERE}" 2>/dev/null || true)"
  if [[ -n "${REPO_OWNER}" && "${REPO_OWNER}" != "root" ]]; then
    RUN_USER="${REPO_OWNER}"
  fi
fi
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
WIFITE_DIR="${PROJECTS_DIR}/wifite2"
VENV_DIR="${REPO_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"

APT_PACKAGES=(
  git
  curl
  ca-certificates
  gnupg
  util-linux
  iw
  wireless-tools
  aircrack-ng
  build-essential
  python3
  python3-dev
  python3-pip
  python3-numpy
  python3-setuptools
  python3-venv
  python3-wheel
  network-manager
)

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'
  C_CYAN=$'\033[36m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_GREEN=""
  C_CYAN=""
  C_YELLOW=""
  C_RED=""
fi

banner() {
  printf '\n%s' "${C_BOLD}${C_CYAN}"
  cat <<'EOF'
K.A.R.I DIY Installer
Fresh Pi -> pocket network testing tool
EOF
  printf '%s\n' "${C_RESET}"
}

section() {
  printf '\n%s== %s ==%s\n' "${C_BOLD}${C_CYAN}" "$*" "${C_RESET}"
}

say() {
  printf '%s->%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"
}

note() {
  printf '%s::%s %s\n' "${C_DIM}" "${C_RESET}" "$*"
}

warn() {
  printf '%s!!%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2
}

die() {
  warn "$*"
  exit 1
}

run_as_user() {
  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" ]]; then
    sudo -H -u "${RUN_USER}" "$@"
  else
    "$@"
  fi
}

ask() {
  local prompt="$1"
  local default="${2:-}"
  local answer=""
  if [[ -n "${default}" ]]; then
    read -r -p "${C_BOLD}${prompt}${C_RESET} [${default}]: " answer
    printf '%s' "${answer:-$default}"
  else
    read -r -p "${C_BOLD}${prompt}${C_RESET}: " answer
    printf '%s' "${answer}"
  fi
}

ask_secret() {
  local prompt="$1"
  local answer=""
  read -r -s -p "${C_BOLD}${prompt}${C_RESET}: " answer
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
  read -r -p "${C_BOLD}${prompt}${C_RESET} ${suffix}: " answer
  answer="${answer:-$default}"
  [[ "${answer}" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]
}

ask_choice() {
  local prompt="$1"
  shift
  local default="$1"
  shift
  local answer=""
  printf '%s%s%s\n' "${C_BOLD}" "${prompt}" "${C_RESET}" >&2
  while (($#)); do
    printf '  %s-%s %s\n' "${C_CYAN}" "${C_RESET}" "$1" >&2
    shift
  done
  read -r -p "Choice [${default}]: " answer >&2
  printf '%s' "${answer:-$default}"
}

ensure_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required"
  fi
  sudo -v
}

install_apt_packages() {
  section "System Packages"
  say "Installing launcher prerequisites"
  sudo apt update
  sudo apt install -y "${APT_PACKAGES[@]}"
}

create_directories() {
  section "Directories"
  say "Creating user directories"
  mkdir -p "${PROJECTS_DIR}" "${RESULTS_DIR}" "${CFG_DIR}"
  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" ]]; then
    chown "${RUN_USER}:${RUN_USER}" "${PROJECTS_DIR}" "${RESULTS_DIR}" "${CFG_DIR}" || true
  fi
}

setup_venv() {
  section "Python"
  say "Creating launcher virtual environment"
  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" && -d "${VENV_DIR}" ]]; then
    chown -R "${RUN_USER}:${RUN_USER}" "${VENV_DIR}" || true
  fi
  run_as_user python3 -m venv --system-site-packages "${VENV_DIR}"
  run_as_user "${PIP_BIN}" install --upgrade pip wheel setuptools
  run_as_user "${PIP_BIN}" install -r "${REPO_DIR}/requirements.txt"
}

ensure_base_config() {
  section "Launcher Config"
  say "Writing launcher config scaffold"
  run_as_user env CFG_PATH="${CFG_PATH}" PYTHONPATH="${REPO_DIR}/src" "${PYTHON_BIN}" - <<'PY'
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

detect_iface_driver() {
  local iface="$1"
  local driver=""
  driver="$(readlink -f "/sys/class/net/${iface}/device/driver" 2>/dev/null || true)"
  basename "${driver}" 2>/dev/null || true
}

detect_onboard_wifi_iface() {
  local iface=""
  for path in /sys/class/net/*; do
    iface="$(basename "${path}")"
    [[ "${iface}" == wl* ]] || continue
    if [[ "$(detect_iface_driver "${iface}")" == "brcmfmac" ]]; then
      printf '%s' "${iface}"
      return 0
    fi
  done
  return 1
}

detect_iface_mac() {
  local iface="$1"
  [[ -r "/sys/class/net/${iface}/address" ]] || return 1
  tr '[:upper:]' '[:lower:]' <"/sys/class/net/${iface}/address"
}

detect_iface_type() {
  local iface="$1"
  command -v iw >/dev/null 2>&1 || return 1
  iw dev "${iface}" info 2>/dev/null | awk '/^[[:space:]]*type / {print $2; exit}'
}

install_text_root() {
  local mode="$1"
  local path="$2"
  local tmp=""
  tmp="$(mktemp)"
  cat >"${tmp}"
  sudo install -m "${mode}" "${tmp}" "${path}"
  rm -f "${tmp}"
}

configure_wifi_identity() {
  section "Radio Identity"
  say "Pinning the onboard Broadcom radio to ${PRIMARY_IFACE}"

  local onboard_iface=""
  local onboard_mac=""
  local monitor_mac=""
  onboard_iface="$(detect_onboard_wifi_iface || true)"
  if [[ -n "${onboard_iface}" ]]; then
    onboard_mac="$(detect_iface_mac "${onboard_iface}" || true)"
  fi

  sudo install -d -m 0755 /etc/systemd/network /etc/udev/rules.d

  if [[ -n "${onboard_mac}" ]]; then
    install_text_root 0644 /etc/systemd/network/10-kari-onboard-wifi.link <<EOF
[Match]
MACAddress=${onboard_mac}

[Link]
Name=${PRIMARY_IFACE}
EOF
  else
    warn "Could not read onboard Broadcom MAC; falling back to driver match"
    install_text_root 0644 /etc/systemd/network/10-kari-onboard-wifi.link <<EOF
[Match]
Driver=brcmfmac

[Link]
Name=${PRIMARY_IFACE}
EOF
  fi

  if [[ -e "/sys/class/net/${MONITOR_IFACE}" && "${MONITOR_IFACE}" != "${PRIMARY_IFACE}" ]]; then
    if [[ "$(detect_iface_type "${MONITOR_IFACE}" || true)" == "monitor" ]]; then
      warn "${MONITOR_IFACE} is already in monitor mode; not writing a permanent MAC pin from a possibly modified address"
      monitor_mac=""
    else
      monitor_mac="$(detect_iface_mac "${MONITOR_IFACE}" || true)"
    fi
    if [[ -n "${monitor_mac}" ]]; then
      say "Pinning currently attached capture radio to ${MONITOR_IFACE}"
      install_text_root 0644 /etc/systemd/network/11-kari-monitor-wifi.link <<EOF
[Match]
MACAddress=${monitor_mac}

[Link]
Name=${MONITOR_IFACE}
EOF
    fi
  else
    note "No live ${MONITOR_IFACE} found to pin by MAC; USB radios will still be managed by the launcher inventory."
  fi

  install_text_root 0644 /etc/udev/rules.d/70-kari-wifi-names.rules <<EOF
# K.A.R.I: keep the internal Broadcom maintenance radio stable.
ACTION=="add", SUBSYSTEM=="net", DRIVERS=="brcmfmac", NAME="${PRIMARY_IFACE}"
EOF

  install_text_root 0644 /etc/udev/rules.d/99-kari-nm-managed.rules <<'EOF'
# Keep Wi-Fi interfaces managed by NetworkManager.
ACTION=="add|change", SUBSYSTEM=="net", KERNEL=="wlan*", ENV{NM_UNMANAGED}="0"
ACTION=="add|change", SUBSYSTEM=="net", KERNEL=="wl*", ENV{NM_UNMANAGED}="0"

# Keep USB gadget Ethernet links unmanaged by NM; gadget setup usually owns IPs directly.
ACTION=="add|change", SUBSYSTEM=="net", ENV{ID_NET_DRIVER}=="g_ether",    ENV{NM_UNMANAGED}="1"
ACTION=="add|change", SUBSYSTEM=="net", ENV{ID_NET_DRIVER}=="cdc_ether",  ENV{NM_UNMANAGED}="1"
ACTION=="add|change", SUBSYSTEM=="net", ENV{ID_NET_DRIVER}=="rndis_host", ENV{NM_UNMANAGED}="1"
ACTION=="add|change", SUBSYSTEM=="net", ENV{ID_NET_DRIVER}=="cdc_ncm",    ENV{NM_UNMANAGED}="1"
ACTION=="add|change", SUBSYSTEM=="net", KERNEL=="usb0", ENV{NM_UNMANAGED}="1"
EOF

  sudo udevadm control --reload-rules || true
  sudo systemctl restart systemd-udevd.service 2>/dev/null || true
  warn "Interface naming changes are most reliable after reboot. Keep SSH open until ${PRIMARY_IFACE} is verified."
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
  run_as_user git clone --depth 1 "${url}" "${dest}"
}

clone_repo_with_submodules_if_needed() {
  local url="$1"
  local dest="$2"
  if [[ -d "${dest}/.git" ]]; then
    say "Keeping existing clone at ${dest}"
    (
      cd "${dest}"
      run_as_user git submodule update --init --recursive
    )
    return 0
  fi
  if [[ -e "${dest}" ]]; then
    warn "Skipping ${url}; destination already exists and is not a git repo: ${dest}"
    return 0
  fi
  run_as_user git clone --recurse-submodules --depth 1 "${url}" "${dest}"
}

update_repo_if_needed() {
  local dest="$1"
  if [[ -d "${dest}/.git" ]]; then
    (
      cd "${dest}"
      run_as_user git pull --ff-only || warn "Could not update ${dest}; keeping existing checkout"
    )
  fi
}

prepare_driver_build_packages() {
  section "Monitor Drivers"
  say "Installing driver build and firmware packages"
  apt_install_available \
    dkms \
    bc \
    build-essential \
    libelf-dev \
    raspberrypi-kernel-headers \
    "linux-headers-$(uname -r)" \
    linux-headers-rpi-v8 \
    linux-headers-rpi-v7 \
    linux-headers-rpi-v6 \
    firmware-realtek \
    firmware-atheros \
    firmware-misc-nonfree \
    usb-modeswitch
}

prepare_aircrack_rpi_platform() {
  local dest="$1"
  [[ -f "${dest}/Makefile" ]] || return 0
  if [[ "$(uname -m)" == "aarch64" ]]; then
    sudo sed -i \
      -e 's/^CONFIG_PLATFORM_I386_PC = y/CONFIG_PLATFORM_I386_PC = n/' \
      -e 's/^CONFIG_PLATFORM_ARM64_RPI = n/CONFIG_PLATFORM_ARM64_RPI = y/' \
      "${dest}/Makefile"
  elif [[ "$(uname -m)" == arm* ]]; then
    sudo sed -i \
      -e 's/^CONFIG_PLATFORM_I386_PC = y/CONFIG_PLATFORM_I386_PC = n/' \
      -e 's/^CONFIG_PLATFORM_ARM_RPI = n/CONFIG_PLATFORM_ARM_RPI = y/' \
      "${dest}/Makefile"
  fi
}

install_dkms_driver_repo() {
  local label="$1"
  local url="$2"
  local dest="$3"
  local installer="${4:-auto}"

  say "Installing ${label}"
  clone_repo_if_needed "${url}" "${dest}"
  update_repo_if_needed "${dest}"

  (
    cd "${dest}"
    case "${installer}" in
      aircrack)
        prepare_aircrack_rpi_platform "${dest}"
        sudo make dkms_install
        ;;
      morrownr)
        if [[ -x ./install-driver.sh ]]; then
          sudo ./install-driver.sh NoPrompt
        else
          sudo make dkms_install
        fi
        ;;
      *)
        if [[ -x ./install-driver.sh ]]; then
          sudo ./install-driver.sh NoPrompt
        elif make -n dkms_install >/dev/null 2>&1; then
          sudo make dkms_install
        else
          sudo make
          sudo make install
        fi
        ;;
    esac
  )
}

detect_monitor_driver_choices() {
  local choices=("rtl8812au" "rtl8821au" "ath9k_htc" "mt76")
  if command -v lsusb >/dev/null 2>&1; then
    if lsusb | grep -Eiq '2357:011e|RTL8811AU|RTL8821AU|Archer T2U'; then
      choices+=("rtl8821au")
    fi
    if lsusb | grep -Eiq 'RTL8812AU|RTL8814AU|AWUS036ACH|0bda:8812|0bda:8814'; then
      choices+=("rtl8812au")
    fi
    if lsusb | grep -Eiq 'AR9271|0cf3:9271|TL-WN722N'; then
      choices+=("ath9k_htc")
    fi
    if lsusb | grep -Eiq '0e8d:7610|MediaTek'; then
      choices+=("mt76")
    fi
  fi
  merge_unique_words "${choices[@]}" | xargs
}

install_monitor_driver_set() {
  local selected="$1"
  local driver=""
  local driver_root="${PROJECTS_DIR}/drivers"
  mkdir -p "${driver_root}"
  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" ]]; then
    chown "${RUN_USER}:${RUN_USER}" "${driver_root}" || true
  fi

  prepare_driver_build_packages

  for driver in ${selected}; do
    case "${driver}" in
      rtl8812au|8812au|88xxau)
        install_dkms_driver_repo \
          "rtl8812au / rtl8821au / rtl8814au monitor driver" \
          "https://github.com/aircrack-ng/rtl8812au.git" \
          "${driver_root}/rtl8812au" \
          "aircrack"
        ;;
      rtl8821au|8821au|8811au)
        install_dkms_driver_repo \
          "rtl8811au / rtl8821au monitor driver" \
          "https://github.com/morrownr/8821au-20210708.git" \
          "${driver_root}/8821au-20210708" \
          "morrownr"
        ;;
      rtl88x2bu|88x2bu|8812bu|8822bu)
        install_dkms_driver_repo \
          "rtl8812bu / rtl8822bu monitor driver" \
          "https://github.com/morrownr/88x2bu-20210702.git" \
          "${driver_root}/88x2bu-20210702" \
          "morrownr"
        ;;
      rtl8821cu|8821cu|8811cu)
        install_dkms_driver_repo \
          "rtl8811cu / rtl8821cu monitor driver" \
          "https://github.com/morrownr/8821cu-20210916.git" \
          "${driver_root}/8821cu-20210916" \
          "morrownr"
        ;;
      ath9k_htc|ar9271|atheros)
        say "Atheros AR9271 uses the in-kernel ath9k_htc driver; firmware package was requested through apt."
        ;;
      mt76|mediatek)
        say "MediaTek USB adapters such as MT7610U use the in-kernel mt76 stack on current Raspberry Pi OS."
        ;;
      "")
        ;;
      *)
        warn "Unknown driver token '${driver}', skipping"
        ;;
    esac
  done

  sudo depmod -a || true
}

install_tailscale_if_requested() {
  section "Tailscale"
  local auth_key=""
  if ask_yes_no "Install Tailscale for remote maintenance access" "y"; then
    auth_key="$(ask_secret "Tailscale auth key (blank to install only)")"
    if [[ -n "${auth_key}" ]]; then
      sudo TAILSCALE_AUTH_KEY="${auth_key}" TAILSCALE_HOSTNAME="$(hostname 2>/dev/null || echo kari)" "${REPO_DIR}/scripts/tailscalesetup.sh"
    else
      sudo "${REPO_DIR}/scripts/tailscalesetup.sh"
    fi
    INSTALL_TAILSCALE="true"
  else
    INSTALL_TAILSCALE="false"
  fi
}

prepare_raspyjack_root_alias() {
  [[ -d "${RASPYJACK_DIR}" ]] || return 0

  # Current RaspyJack upstream still has a few absolute /root/Raspyjack paths
  # in its installer. Keep the user's clone location, but provide the path the
  # installer expects so a fresh DIY run can continue.
  if [[ -e /root/Raspyjack || -L /root/Raspyjack ]]; then
    local resolved=""
    resolved="$(readlink -f /root/Raspyjack 2>/dev/null || true)"
    if [[ "${resolved}" != "$(readlink -f "${RASPYJACK_DIR}")" ]]; then
      warn "/root/Raspyjack already exists and does not point at ${RASPYJACK_DIR}; RaspyJack installer may use the wrong tree"
    fi
    return 0
  fi

  say "Creating /root/Raspyjack compatibility link for RaspyJack installer"
  sudo ln -s "${RASPYJACK_DIR}" /root/Raspyjack
}

apt_install_available() {
  local packages=("$@")
  local installable=()
  local pkg=""

  for pkg in "${packages[@]}"; do
    if apt-cache show "${pkg}" >/dev/null 2>&1; then
      installable+=("${pkg}")
    else
      warn "APT package not available on this OS/release, skipping: ${pkg}"
    fi
  done

  if ((${#installable[@]})); then
    sudo apt install -y "${installable[@]}"
  fi
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

apply_raspyjack_return_hook() {
  [[ -d "${RASPYJACK_DIR}" ]] || die "RaspyJack directory not found: ${RASPYJACK_DIR}"
  [[ -f "${RASPYJACK_DIR}/raspyjack.py" ]] || die "RaspyJack raspyjack.py not found: ${RASPYJACK_DIR}/raspyjack.py"
  say "Adding RaspyJack Return to Launcher menu hook"
  run_as_user python3 "${REPO_DIR}/scripts/patch-raspyjack-return-hook.py" "${RASPYJACK_DIR}/raspyjack.py"
}

install_raspyjack_upstream() {
  [[ -d "${RASPYJACK_DIR}" ]] || die "RaspyJack directory not found: ${RASPYJACK_DIR}"
  prepare_raspyjack_root_alias

  local installer=""
  for candidate in install_raspyjack.sh install.sh setup.sh; do
    if [[ -f "${RASPYJACK_DIR}/${candidate}" ]]; then
      installer="${RASPYJACK_DIR}/${candidate}"
      break
    fi
  done

  if [[ -z "${installer}" ]]; then
    warn "No RaspyJack installer found in ${RASPYJACK_DIR}; expected install_raspyjack.sh, install.sh, or setup.sh"
    return 0
  fi

  say "Running RaspyJack upstream installer: ${installer}"
  (
    cd "${RASPYJACK_DIR}"
    sudo bash "${installer}"
  )
  sudo systemctl daemon-reload || true
  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" && "${RASPYJACK_DIR}" == "${RUN_HOME}/"* ]]; then
    chown -R "${RUN_USER}:${RUN_USER}" "${RASPYJACK_DIR}" || true
  fi
}

disable_service_units_if_present() {
  local svc=""
  for svc in "$@"; do
    [[ -n "${svc}" ]] || continue
    if service_unit_exists "${svc}"; then
      say "Stopping and disabling ${svc}"
      sudo systemctl stop "${svc}" || true
      sudo systemctl disable "${svc}" || true
    else
      warn "Service unit not found, skipping disable: ${svc}"
    fi
  done
}

service_unit_exists() {
  local svc="$1"
  [[ -n "${svc}" ]] || return 1
  systemctl list-unit-files "${svc}" --no-legend --no-pager 2>/dev/null | awk '{print $1}' | grep -Fxq "${svc}"
}

discover_raspyjack_services() {
  systemctl list-unit-files "raspyjack*.service" --no-legend --no-pager 2>/dev/null | awk '{print $1}' | sort -u
}

merge_unique_words() {
  local seen=""
  local item=""
  for item in "$@"; do
    [[ -n "${item}" ]] || continue
    if [[ " ${seen} " != *" ${item} "* ]]; then
      printf '%s\n' "${item}"
      seen="${seen} ${item}"
    fi
  done
}

configure_raspyjack_service_override() {
  local core_service="$1"
  [[ -n "${core_service}" ]] || return 0
  if ! service_unit_exists "${core_service}"; then
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
Environment="RJ_RETURN_TO_LAUNCHER_CMD=systemd-run --unit kari-rj-return --collect --same-dir /usr/bin/env bash -lc '${REPO_DIR}/stop_raspyjack.sh >/dev/null 2>&1'"
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
  local ok="true"

  if ! apt_package_has_candidate kismet; then
    warn "No kismet package candidate found in the current apt sources"
    configure_kismet_apt_repo || {
      warn "Could not configure the official Kismet apt repository"
      return 1
    }
  fi

  if ! sudo env DEBIAN_FRONTEND=noninteractive apt install -y kismet; then
    warn "Kismet package install failed; skipping Kismet service integration"
    return 1
  fi

  if getent group kismet >/dev/null 2>&1; then
    sudo usermod -aG kismet "${RUN_USER}" || warn "Could not add ${RUN_USER} to kismet group"
  fi

  sudo install -m 0755 "${REPO_DIR}/scripts/kismet-source-autoconfig.sh" /usr/local/bin/kismet-source-autoconfig.sh || ok="false"
  sudo install -d /etc/systemd/system/kismet.service.d || ok="false"
  sudo install -m 0644 "${REPO_DIR}/scripts/kismet.service.override.conf" /etc/systemd/system/kismet.service.d/override.conf || ok="false"
  sudo systemctl daemon-reload || ok="false"

  if systemctl list-unit-files | grep -Fq "kismet.service"; then
    sudo systemctl enable kismet.service || warn "Could not enable kismet.service"
    sudo systemctl restart kismet.service || warn "Could not restart kismet.service; check journalctl -u kismet.service"
  else
    warn "kismet.service was not found after package install"
    ok="false"
  fi

  [[ "${ok}" == "true" ]]
}

apt_package_has_candidate() {
  local package="$1"
  apt-cache policy "${package}" 2>/dev/null | awk '/Candidate:/ { found=1; if ($2 != "(none)") ok=1 } END { exit !(found && ok) }'
}

configure_kismet_apt_repo() {
  local os_id=""
  local os_like=""
  local codename=""
  local repo_codename=""
  local repo_channel="${KISMET_REPO_CHANNEL:-release}"
  local apt_arch=""

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-}"
    os_like="${ID_LIKE:-}"
    codename="${VERSION_CODENAME:-}"
  fi

  if [[ "${os_id}" == "kali" || " ${os_like} " == *" kali "* ]]; then
    repo_codename="kali"
  elif [[ "${codename}" == "trixie" || "${codename}" == "bookworm" ]]; then
    repo_codename="${codename}"
  else
    warn "Unsupported Kismet apt repository target: ID=${os_id:-unknown} VERSION_CODENAME=${codename:-unknown}"
    return 1
  fi

  if [[ "${repo_channel}" != "release" && "${repo_channel}" != "git" ]]; then
    warn "Unsupported Kismet repo channel '${repo_channel}', using release"
    repo_channel="release"
  fi

  say "Configuring official Kismet ${repo_channel} apt repository for ${repo_codename}"
  apt_arch="$(dpkg --print-architecture 2>/dev/null || true)"
  [[ -n "${apt_arch}" ]] || apt_arch="arm64"
  sudo install -d -m 0755 /usr/share/keyrings /etc/apt/sources.list.d
  if ! curl -fsSL https://www.kismetwireless.net/repos/kismet-release.gpg.key | gpg --dearmor | sudo tee /usr/share/keyrings/kismet-archive-keyring.gpg >/dev/null; then
    warn "Could not download or install Kismet apt signing key"
    return 1
  fi
  printf 'deb [arch=%s signed-by=/usr/share/keyrings/kismet-archive-keyring.gpg] https://www.kismetwireless.net/repos/apt/%s/%s %s main\n' \
    "${apt_arch}" "${repo_channel}" "${repo_codename}" "${repo_codename}" | sudo tee /etc/apt/sources.list.d/kismet.list >/dev/null
  sudo apt update
  apt_package_has_candidate kismet
}

install_wifite_from_source() {
  [[ -d "${WIFITE_DIR}" ]] || die "Wifite2 directory not found: ${WIFITE_DIR}"

  say "Installing Wifite2 helper tools when available"
  apt_install_available \
    aircrack-ng \
    wireless-tools \
    net-tools \
    reaver \
    bully \
    tshark \
    cowpatty \
    hashcat \
    hcxtools \
    hcxdumptool \
    macchanger

  if [[ -f "${WIFITE_DIR}/setup.py" ]]; then
    say "Installing Wifite2 from source"
    (
      cd "${WIFITE_DIR}"
      sudo python3 setup.py install
    )
    if [[ -f "${WIFITE_DIR}/Wifite.py" ]]; then
      say "Installing Wifite2 launcher wrapper"
      sudo tee /usr/local/sbin/wifite >/dev/null <<EOF
#!/usr/bin/env sh
exec python3 "${WIFITE_DIR}/Wifite.py" "\$@"
EOF
      sudo chmod 0755 /usr/local/sbin/wifite
    fi
    if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" && "${WIFITE_DIR}" == "${RUN_HOME}/"* ]]; then
      chown -R "${RUN_USER}:${RUN_USER}" "${WIFITE_DIR}" || true
    fi
  elif [[ -f "${WIFITE_DIR}/Wifite.py" ]]; then
    say "Installing Wifite2 script wrapper"
    sudo install -m 0755 "${WIFITE_DIR}/Wifite.py" /usr/local/sbin/wifite
  else
    warn "Wifite2 clone does not contain setup.py or Wifite.py: ${WIFITE_DIR}"
  fi
}

install_angryoxide_from_source() {
  [[ -d "${ANGRYOXIDE_DIR}" ]] || die "AngryOxide directory not found: ${ANGRYOXIDE_DIR}"

  say "Installing AngryOxide build prerequisites"
  apt_install_available cargo rustc make pkg-config libssl-dev

  say "Building AngryOxide from source"
  (
    cd "${ANGRYOXIDE_DIR}"
    run_as_user git submodule update --init --recursive
    run_as_user make build
    sudo make install
  )

  if [[ "$(id -u)" -eq 0 && "${RUN_USER}" != "root" && "${ANGRYOXIDE_DIR}" == "${RUN_HOME}/"* ]]; then
    chown -R "${RUN_USER}:${RUN_USER}" "${ANGRYOXIDE_DIR}" || true
  fi
}

install_angryoxide_release() {
  say "Installing AngryOxide from latest compatible GitHub release"
  apt_install_available curl tar ca-certificates

  local asset_url=""
  asset_url="$(python3 - <<'PY'
import json
import sys
import urllib.request

url = "https://api.github.com/repos/Ragnt/AngryOxide/releases/latest"
with urllib.request.urlopen(url, timeout=20) as resp:
    data = json.load(resp)

assets = data.get("assets", [])
preferred = []
fallback = []
for asset in assets:
    name = str(asset.get("name", "")).lower()
    download = asset.get("browser_download_url")
    if not download:
        continue
    if "linux" not in name:
        continue
    if "aarch64" in name or "arm64" in name:
        preferred.append(download)
    elif "arm" in name:
        fallback.append(download)

choices = preferred + fallback
if not choices:
    names = ", ".join(str(a.get("name", "")) for a in assets)
    print(f"No linux arm64/aarch64 AngryOxide release asset found. Assets: {names}", file=sys.stderr)
    sys.exit(1)

print(choices[0])
PY
)"

  [[ -n "${asset_url}" ]] || return 1
  say "Downloading ${asset_url}"

  local tmpdir=""
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  curl -fL "${asset_url}" -o "${tmpdir}/angryoxide-release"
  if file "${tmpdir}/angryoxide-release" | grep -qi 'gzip compressed'; then
    tar -xzf "${tmpdir}/angryoxide-release" -C "${tmpdir}"
  elif file "${tmpdir}/angryoxide-release" | grep -qi 'tar archive'; then
    tar -xf "${tmpdir}/angryoxide-release" -C "${tmpdir}"
  else
    install -m 0755 "${tmpdir}/angryoxide-release" "${tmpdir}/angryoxide"
  fi

  local install_script=""
  install_script="$(find "${tmpdir}" -maxdepth 3 -type f -name install.sh -print -quit)"
  if [[ -n "${install_script}" ]]; then
    chmod +x "${install_script}"
    (
      cd "$(dirname "${install_script}")"
      sudo ./install.sh
    )
    return 0
  fi

  local binary=""
  binary="$(find "${tmpdir}" -maxdepth 4 -type f -name angryoxide -perm /111 -print -quit)"
  [[ -n "${binary}" ]] || die "Downloaded AngryOxide release did not contain an executable angryoxide binary"
  sudo install -m 0755 "${binary}" /usr/bin/angryoxide
}

write_config_values() {
  say "Applying launcher config"
  run_as_user env \
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
  RASPYJACK_EXTRA_SERVICES="${RASPYJACK_EXTRA_SERVICES}" \
  ANGRYOXIDE_CMD="${ANGRYOXIDE_CMD}" \
  WIFITE_RUN_COMMAND="${WIFITE_RUN_COMMAND}" \
  RESULTS_DIR="${RESULTS_DIR}" \
  INSTALL_KISMET="${INSTALL_KISMET}" \
  KISMET_SERVICE_NAME="${KISMET_SERVICE_NAME}" \
  REPO_DIR="${REPO_DIR}" \
  "${PYTHON_BIN}" - <<'PY'
import json
import os
import shlex
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
raspyjack_extra_services = [s for s in os.environ["RASPYJACK_EXTRA_SERVICES"].split() if s]
angryoxide_cmd = os.environ["ANGRYOXIDE_CMD"]
wifite_run_command = os.environ["WIFITE_RUN_COMMAND"]
local_buttons = os.environ["LOCAL_BUTTONS"].lower() in {"1", "true", "yes", "on"}
remote_port = int(os.environ["REMOTE_PORT"])
remote_token = os.environ["REMOTE_TOKEN"]
install_kismet = os.environ["INSTALL_KISMET"].lower() in {"1", "true", "yes", "on"}
kismet_service_name = os.environ["KISMET_SERVICE_NAME"]

managed_apps = data.setdefault("managed_apps", {})
repo_dir = Path(os.environ.get("REPO_DIR", "")) if os.environ.get("REPO_DIR") else run_home / "Projects" / "kari-launcher"
rj_env = {
    "RJ_CORE_SERVICE": raspyjack_core_service,
    "RJ_DEVICE_SERVICE": raspyjack_device_service,
    "RJ_WEB_SERVICE": raspyjack_web_service,
    "RJ_EXTRA_SERVICES": " ".join(raspyjack_extra_services),
}
rj_env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in rj_env.items())
rj_start_cmd = f"{rj_env_prefix} {shlex.quote(str(repo_dir / 'start_raspyjack.sh'))}".strip()
rj_stop_cmd = f"{rj_env_prefix} {shlex.quote(str(repo_dir / 'stop_raspyjack.sh'))}".strip()
managed_apps.setdefault("termie", {})
managed_apps["termie"].update({
    "label": "Termie",
    "start_cmd": str(repo_dir / "start_termie.sh"),
    "stop_cmd": str(repo_dir / "stop_termie.sh"),
    "status_cmd": "systemctl is-active termie.service",
    "takes_over_display": True,
})
managed_apps.setdefault("raspyjack", {})
managed_apps["raspyjack"].update({
    "label": "RaspyJack",
    "start_cmd": rj_start_cmd,
    "stop_cmd": rj_stop_cmd,
    "status_cmd": f"systemctl is-active {raspyjack_core_service} {raspyjack_device_service} {raspyjack_web_service}".strip(),
    "start_grace_seconds": 20,
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
raspyjack_services = [raspyjack_core_service, raspyjack_device_service, raspyjack_web_service] + raspyjack_extra_services
raspyjack.update({
    "service_names": raspyjack_services,
    "webui_service_names": [raspyjack_web_service],
    "loot_path": str(raspyjack_dir / "loot"),
    "primary_interface": primary_iface,
    "monitor_interface": monitor_iface,
})

foxhunt = data.setdefault("foxhunt", {})
foxhunt["interface"] = monitor_iface

wifite = data.setdefault("wifite", {})
wifite["interface"] = monitor_iface
if wifite_run_command:
    wifite["run_command"] = wifite_run_command

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
  section "Services"
  say "Installing launcher services"
  sudo KARI_RUN_USER="${RUN_USER}" KARI_DASH_CONFIG="${CFG_PATH}" "${REPO_DIR}/install_dashboard_service.sh"
}

install_watchdog_if_requested() {
  say "Installing watchdog"
  sudo "${REPO_DIR}/install_watchdog_service.sh"
}

verify_external_plumbing() {
  say "Verifying external tool plumbing"
  verify_command_or_path "AngryOxide command" "${ANGRYOXIDE_CMD}" || true
  if [[ -n "${WIFITE_RUN_COMMAND}" ]]; then
    verify_command_or_path "Wifite run command" "${WIFITE_RUN_COMMAND}" || true
  fi

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
  section "Summary"
  printf 'User: %s\n' "${RUN_USER}"
  printf 'Repo: %s\n' "${REPO_DIR}"
  printf 'Config: %s\n' "${CFG_PATH}"
  printf 'Primary iface: %s\n' "${PRIMARY_IFACE}"
  printf 'Monitor iface: %s\n' "${MONITOR_IFACE}"
  printf 'Display backend: %s\n' "${DISPLAY_BACKEND}"
  printf 'Wi-Fi profile: %s\n' "${WIFI_PROFILE:-<blank>}"
  printf 'Results dir: %s\n' "${RESULTS_DIR}"
  printf 'RaspyJack dir: %s\n' "${RASPYJACK_DIR}"
  printf 'RaspyJack services: %s %s %s %s\n' "${RASPYJACK_CORE_SERVICE}" "${RASPYJACK_DEVICE_SERVICE}" "${RASPYJACK_WEB_SERVICE}" "${RASPYJACK_EXTRA_SERVICES}"
  printf 'AngryOxide command: %s\n' "${ANGRYOXIDE_CMD}"
  printf 'Wifite dir: %s\n' "${WIFITE_DIR}"
  printf 'Wifite run command: %s\n' "${WIFITE_RUN_COMMAND:-<blank>}"
  printf 'Kismet integration: %s\n' "${INSTALL_KISMET}"
  printf 'Tailscale install: %s\n' "${INSTALL_TAILSCALE}"
  printf 'Monitor driver set: %s\n' "${MONITOR_DRIVER_SET:-<skipped>}"
  printf 'Launcher service install: %s\n' "${INSTALL_SERVICES}"
  printf 'Watchdog install: %s\n' "${INSTALL_WATCHDOG}"
}

main() {
  [[ -d "${REPO_DIR}/src/launcher" ]] || die "Run this from the kari-launcher repo"
  ensure_sudo

  banner
  note "Target: Raspberry Pi OS on Pi Zero 2 W."
  note "Goal: stable wlan0 maintenance Wi-Fi, external capture radios, launcher services, and optional tools."

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
  if ask_yes_no "Install K.A.R.I radio naming and NetworkManager rules" "y"; then
    configure_wifi_identity
  fi
  MONITOR_DRIVER_SET=""
  if ask_yes_no "Install monitor-mode adapter driver support" "y"; then
    MONITOR_DRIVER_DEFAULT="$(detect_monitor_driver_choices)"
    MONITOR_DRIVER_SET="$(ask "Driver set (space separated: rtl8812au rtl8821au rtl88x2bu rtl8821cu ath9k_htc mt76)" "${MONITOR_DRIVER_DEFAULT}")"
    install_monitor_driver_set "${MONITOR_DRIVER_SET}"
  fi
  install_tailscale_if_requested

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

  RASPYJACK_WAS_CLONED="false"
  if ask_yes_no "Clone RaspyJack upstream into ${RASPYJACK_DIR}" "n"; then
    RASPYJACK_WAS_CLONED="true"
    clone_repo_if_needed "https://github.com/7h30th3r0n3/Raspyjack.git" "${RASPYJACK_DIR}"
  fi

  if [[ -d "${RASPYJACK_DIR}" ]]; then
    RASPYJACK_INSTALL_DEFAULT="n"
    [[ "${RASPYJACK_WAS_CLONED}" == "true" ]] && RASPYJACK_INSTALL_DEFAULT="y"
    if ask_yes_no "Run the RaspyJack installer before applying launcher overrides" "${RASPYJACK_INSTALL_DEFAULT}"; then
      install_raspyjack_upstream
    fi
  fi

  RASPYJACK_CORE_SERVICE="$(ask "RaspyJack core service name" "raspyjack.service")"
  RASPYJACK_DEVICE_SERVICE="$(ask "RaspyJack device service name" "raspyjack-device.service")"
  RASPYJACK_WEB_SERVICE="$(ask "RaspyJack web service name" "raspyjack-webui.service")"
  RASPYJACK_EXTRA_SERVICES="$(ask "Additional RaspyJack service names to stop/disable" "raspyjack-caddy-autoconfig.service raspyjack-pin-wifi.service")"

  mapfile -t RASPYJACK_DISCOVERED_SERVICES < <(discover_raspyjack_services)
  if ((${#RASPYJACK_DISCOVERED_SERVICES[@]})); then
    say "Discovered RaspyJack service units: ${RASPYJACK_DISCOVERED_SERVICES[*]}"
    if ! service_unit_exists "${RASPYJACK_CORE_SERVICE}" && service_unit_exists "raspyjack.service"; then
      warn "RaspyJack core service '${RASPYJACK_CORE_SERVICE}' was not found; using discovered raspyjack.service"
      RASPYJACK_CORE_SERVICE="raspyjack.service"
    fi
    if ! service_unit_exists "${RASPYJACK_DEVICE_SERVICE}" && service_unit_exists "raspyjack-device.service"; then
      warn "RaspyJack device service '${RASPYJACK_DEVICE_SERVICE}' was not found; using discovered raspyjack-device.service"
      RASPYJACK_DEVICE_SERVICE="raspyjack-device.service"
    fi
    if ! service_unit_exists "${RASPYJACK_WEB_SERVICE}" && service_unit_exists "raspyjack-webui.service"; then
      warn "RaspyJack web service '${RASPYJACK_WEB_SERVICE}' was not found; using discovered raspyjack-webui.service"
      RASPYJACK_WEB_SERVICE="raspyjack-webui.service"
    fi
    mapfile -t RASPYJACK_EXTRA_SERVICE_LIST < <(merge_unique_words ${RASPYJACK_EXTRA_SERVICES} "${RASPYJACK_DISCOVERED_SERVICES[@]}" | grep -Fvx "${RASPYJACK_CORE_SERVICE}" | grep -Fvx "${RASPYJACK_DEVICE_SERVICE}" | grep -Fvx "${RASPYJACK_WEB_SERVICE}" || true)
    RASPYJACK_EXTRA_SERVICES="${RASPYJACK_EXTRA_SERVICE_LIST[*]:-}"
  else
    warn "No RaspyJack systemd units were discovered yet"
  fi

  if [[ "${DISPLAY_BACKEND}" == "waveshare_1in3" ]] && [[ -d "${RASPYJACK_DIR}" ]]; then
    if ask_yes_no "Apply the bundled RaspyJack 1.3in ST7789 compatibility patch" "y"; then
      apply_raspyjack_patch_bundle
    fi
  fi

  if [[ -d "${RASPYJACK_DIR}" ]]; then
    if ask_yes_no "Add Return to Launcher to RaspyJack menus" "y"; then
      apply_raspyjack_return_hook
    fi
  fi

  if ask_yes_no "Stop and disable RaspyJack services so the launcher owns startup by default" "y"; then
    disable_service_units_if_present "${RASPYJACK_CORE_SERVICE}" "${RASPYJACK_DEVICE_SERVICE}" "${RASPYJACK_WEB_SERVICE}" ${RASPYJACK_EXTRA_SERVICES}
  fi

  if ask_yes_no "Install a RaspyJack systemd override for launcher return/display env vars" "y"; then
    configure_raspyjack_service_override "${RASPYJACK_CORE_SERVICE}"
  fi

  if ask_yes_no "Install AngryOxide from the latest compatible release binary" "y"; then
    install_angryoxide_release || warn "AngryOxide release install failed; source build remains available as fallback"
  fi

  ANGRYOXIDE_WAS_CLONED="false"
  if ask_yes_no "Clone AngryOxide upstream source into ${ANGRYOXIDE_DIR}" "n"; then
    ANGRYOXIDE_WAS_CLONED="true"
    clone_repo_with_submodules_if_needed "https://github.com/Ragnt/AngryOxide.git" "${ANGRYOXIDE_DIR}"
  fi
  if [[ -d "${ANGRYOXIDE_DIR}" ]] && ! command -v angryoxide >/dev/null 2>&1; then
    ANGRYOXIDE_INSTALL_DEFAULT="n"
    [[ "${ANGRYOXIDE_WAS_CLONED}" == "true" ]] && ANGRYOXIDE_INSTALL_DEFAULT="y"
    if ask_yes_no "Build and install AngryOxide from ${ANGRYOXIDE_DIR}" "${ANGRYOXIDE_INSTALL_DEFAULT}"; then
      install_angryoxide_from_source
    fi
  fi

  WIFITE_WAS_CLONED="false"
  if ask_yes_no "Clone Wifite2 upstream into ${WIFITE_DIR}" "n"; then
    WIFITE_WAS_CLONED="true"
    clone_repo_if_needed "https://github.com/derv82/wifite2.git" "${WIFITE_DIR}"
  fi
  if [[ -d "${WIFITE_DIR}" ]]; then
    WIFITE_INSTALL_DEFAULT="n"
    [[ "${WIFITE_WAS_CLONED}" == "true" ]] && WIFITE_INSTALL_DEFAULT="y"
    if ask_yes_no "Install Wifite2 from ${WIFITE_DIR}" "${WIFITE_INSTALL_DEFAULT}"; then
      install_wifite_from_source
    fi
  fi

  ANGRYOXIDE_DEFAULT_CMD="/usr/bin/angryoxide -i ${MONITOR_IFACE}"
  if ! [[ -x /usr/bin/angryoxide ]]; then
    ANGRYOXIDE_DEFAULT_CMD="${RUN_HOME}/bin/angryoxide -i ${MONITOR_IFACE}"
  fi
  ANGRYOXIDE_CMD="$(ask "AngryOxide command path" "${ANGRYOXIDE_DEFAULT_CMD}")"
  DEFAULT_WIFITE_RUN_COMMAND="sudo /usr/local/sbin/wifite -i ${MONITOR_IFACE} -b "'$WIFITE_TARGET_BSSID'" -c "'$WIFITE_TARGET_CHANNEL'" --kill"
  WIFITE_RUN_COMMAND="$(ask "Wifite run command template (blank to leave disabled)" "${DEFAULT_WIFITE_RUN_COMMAND}")"

  ensure_base_config

  KISMET_SERVICE_NAME="kismet.service"
  INSTALL_KISMET="false"
  if ask_yes_no "Install Kismet package and launcher source-policy override" "y"; then
    if install_kismet_integration; then
      INSTALL_KISMET="true"
    else
      INSTALL_KISMET="failed"
      KISMET_SERVICE_NAME="$(ask "Kismet install failed. Service name to keep in launcher config" "kismet.service")"
    fi
  else
    KISMET_SERVICE_NAME="$(ask "Existing Kismet service name" "kismet.service")"
  fi

  if ask_yes_no "Clone Kismet upstream source into ${KISMET_DIR} for reference/build work" "n"; then
    clone_repo_if_needed "https://github.com/kismetwireless/kismet.git" "${KISMET_DIR}"
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
