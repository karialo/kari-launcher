#!/usr/bin/env python3
"""
Waveshare launcher dashboard for local + remote node telemetry.

Controls:
- A: previous page
- B: next page
- X/Y: context actions (per-page)
"""

from __future__ import annotations

import copy
import io
import json
import math
import os
import pwd
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pygame
from PIL import Image

try:
    from displayhatmini import DisplayHATMini
except Exception:
    DisplayHATMini = None  # type: ignore[assignment]

try:
    import st7789
except Exception:
    st7789 = None  # type: ignore[assignment]

try:
    import numpy
except Exception:
    numpy = None  # type: ignore[assignment]

try:
    import spidev
except Exception:
    spidev = None  # type: ignore[assignment]

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None  # type: ignore[assignment]

from .animation import PageTransition
from .angryoxide_menu import AngryOxideMenuController
from .effects import HudEffects
from .foxhunt import FoxhuntController
from .theme import THEMES, Theme, load_theme_name_from_env, next_theme_name
from .ui_primitives import GlowCache, PanelStyle, TextRenderer, draw_panel, draw_status_dot
from .wifite_prep import WifitePrepController


def _resolve_config_path() -> Path:
    override = os.environ.get("DHM_DASH_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()

    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if os.geteuid() == 0 and sudo_user:
        try:
            sudo_home = Path(pwd.getpwnam(sudo_user).pw_dir)
            return sudo_home / ".config" / "launcher" / "dashboard.json"
        except Exception:
            pass

    return (Path.home() / ".config" / "launcher" / "dashboard.json").expanduser()


CONFIG_PATH = _resolve_config_path()
ANGRYOXIDE_PID_PATH = Path("/tmp/portableops-angryoxide.pid")
REMOTE_ACTION_LOG_PATH = Path("/tmp/portableops-remote-actions.log")
SPLASH_FILENAME = "KARI.png"
HIGH_IMPACT_ACTIONS = {
    "ao_toggle",
    "ao_monitor_on",
    "ao_monitor_off",
    "net_reboot",
    "rj_core_start",
    "rj_core_stop",
    "rj_core_restart",
    "rj_device_start",
    "rj_device_stop",
    "rj_device_restart",
    "rj_web_start",
    "rj_web_stop",
    "rj_web_restart",
    "rj_all_start",
    "rj_all_stop",
    "rj_all_restart",
}
SAFE_REMOTE_ACTIONS = {
    "up",
    "down",
    "left",
    "right",
    "page_prev",
    "page_next",
    "refresh",
    "goto_gps",
    "goto_foxhunt",
    "goto_wifite",
    "goto_networkops",
    "goto_overview",
    "goto_raspyjack",
    "goto_angryoxide",
    "ao_toggle",
    "ao_view",
    "ao_monitor_on",
    "ao_monitor_off",
    "ao_scan_all",
    "ao_select_network",
    "ao_lock_target",
    "ao_profile_standard",
    "ao_profile_passive",
    "ao_profile_autoexit",
    "wf_select_network",
    "wf_lock_target",
    "wf_clear_target",
    "net_refresh",
    "net_reconnect_wlan0",
    "net_restart_networkmanager",
    "net_restart_tailscale",
    "net_iface_menu",
    "net_reboot",
    "rj_runbook_up",
    "rj_runbook_recover",
    "rj_runbook_web_bounce",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "refresh_seconds": 30,
    "idle_redraw_seconds": 2.0,
    "history_points": 180,
    "local_buttons_enabled": False,
    "backlight_level": 1.0,
    "backlight_pwm": False,
    "request_timeout_seconds": 1.8,
    "smb_deep_stats_enabled": False,
    "smb_detail_refresh_seconds": 60,
    "hardware": {
        "backend": "auto",
        "rotation": 90,
        "invert": True,
        "spi_port": 0,
        "spi_cs": 0,
        "spi_speed_hz": 24000000,
        "dc_pin": 25,
        "rst_pin": 27,
        "backlight_pin": 24,
    },
    "input": {
        "pins": {
            "UP": 6,
            "DOWN": 19,
            "LEFT": 5,
            "RIGHT": 26,
            "OK": 13,
            "KEY1": 21,
            "KEY2": 20,
            "KEY3": 16,
        },
        "debounce_seconds": 0.10,
    },
    "managed_apps": {
        "raspyjack": {
            "label": "RaspyJack",
            "start_cmd": "/home/kari/Projects/start_raspyjack.sh",
            "stop_cmd": "/home/kari/Projects/stop_raspyjack.sh",
            "status_cmd": "systemctl is-active raspyjack.service raspyjack-device.service raspyjack-webui.service",
            "takes_over_display": True,
        }
    },
    "nodes": [
        {
            "name": "PiTemplar",
            "host": "pitemplar.tailnet.ts.net",
            "ports": [22, 8080],
            "health_url": "",
            "health_json_path": "",
            "health_expect": "",
            "smb": {
                "host": "pitemplar.tailnet.ts.net",
                "share": "private",
                "username": "",
                "password": "",
            },
        },
        {
            "name": "Bjorn",
            "host": "bjorn.tailnet.ts.net",
            "ports": [22, 8000],
            "health_url": "",
            "health_json_path": "",
            "health_expect": "",
        },
    ],
    "raspyjack": {
        "service_names": [
            "raspyjack.service",
            "raspyjack-device.service",
            "raspyjack-webui.service",
        ],
        "webui_service_names": [
            "raspyjack-webui.service",
            "caddy.service",
        ],
        "webui_host": "127.0.0.1",
        "webui_port": 8080,
        "webui_url": "",
        "loot_path": "/home/kali/Raspyjack/loot",
        "primary_interface": "wlan0",
        "monitor_interface": "wlan1",
    },
    "angryoxide": {
        "interface": "wlan1",
        "start_monitor_cmd": "startmonitormode",
        "command": "/home/kali/angryoxide -i wlan1",
        "whitelist_flag": "--whitelist",
        "whitelist_networks": [],
        "log_path": "/home/kali/Results/angryoxide-live.log",
        "results_dir": "/home/kali/Results",
        "results_prefix": "oxide",
        "run_profiles": {
            "standard": [],
            "passive": [
                "--notransmit",
                "--disable-deauth",
                "--disable-pmkid",
                "--disable-anon",
                "--disable-csa",
                "--disable-disassoc",
                "--disable-roguem2",
                "--notar",
            ],
            "autoexit": [
                "--autoexit",
                "--notransmit",
                "--disable-deauth",
                "--disable-pmkid",
                "--disable-anon",
                "--disable-csa",
                "--disable-disassoc",
                "--disable-roguem2",
                "--notar",
            ],
        },
    },
    "foxhunt": {
        "interface": "wlan1",
        "scan_max_results": 32,
        "scan_interval_idle_seconds": 10.0,
        "scan_interval_active_seconds": 3.0,
        "signal_window_short": 5,
        "signal_window_long": 12,
        "sort": "rssi",
        "save_dir": "~/.local/share/launcher/foxhunt",
    },
    "wifite": {
        "interface": "wlan1",
        "scan_max_results": 32,
        "scan_interval_active_seconds": 4.0,
        "run_command": "/home/kari/.local/bin/sd-list",
    },
    "network_ops": {
        "primary_iface": "wlan0",
        "monitor_iface": "wlan1",
        "wifi_profile": "VM8248525",
        "networkmanager_service": "NetworkManager.service",
        "tailscale_service": "tailscaled.service",
        "reboot_cmd": "systemctl reboot",
    },
    "remote": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8787,
        "token": "",
        "safe_mode": False,
    },
}

PING_RE = re.compile(r"time=([0-9.]+)\s*ms")
SMB_BLOCKS_RE = re.compile(r"(\d+)\s+blocks of size\s+(\d+)\.\s+(\d+)\s+blocks available", re.IGNORECASE)
SMB_ENTRY_RE = re.compile(r"^\s*(.+?)\s+([A-Z]+)\s+(\d+)\s+")
AO_SOCKETS_RE = re.compile(r"Sockets Opened\s*\[Rx:\s*(\d+)\s*\|\s*Tx:\s*(\d+)\]")
AO_OUI_RE = re.compile(r"OUI Records Imported:\s*(\d+)")
AO_SSID_RE = re.compile(r"SSID:\s*([^\r\n]+)")
AO_WL_ARG_RE = re.compile(r"--whitelist-entry\s+([^\s]+)")
AO_ROGUE_M2_RE = re.compile(r"Rogue M2 Collected", re.IGNORECASE)
AO_M1_SENT_RE = re.compile(r"M1 Retrieval - Sent", re.IGNORECASE)
IW_SIGNAL_RE = re.compile(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", re.IGNORECASE)
PISUGAR_BATTERY_RE = re.compile(r"battery[^0-9-]*([0-9]{1,3}(?:\.[0-9]+)?)", re.IGNORECASE)
PCT_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%")

SMB_DETAIL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
DIR_STATS_CACHE: dict[str, tuple[float, tuple[int, int]]] = {}
WLAN_SIGNAL_CACHE: dict[str, tuple[float, tuple[int | None, int | None]]] = {}
PISUGAR_BATTERY_CACHE: tuple[float, float | None] | None = None
GPS_CACHE: tuple[float, "GPSStatus"] | None = None
AO_LOG_METRICS_CACHE: dict[str, tuple[float, float | None, int | None, dict[str, Any], list[str]]] = {}
AO_HC_METRICS_CACHE: dict[str, tuple[float, tuple[int, int]]] = {}
AO_RESULTS_SUMMARY_CACHE: dict[str, tuple[float, tuple[int, int, int, int]]] = {}


@dataclass
class NodeStatus:
    name: str
    host: str
    status: str
    latency_ms: float | None
    ports_open: list[int]
    ports_closed: list[int]
    health_text: str
    smb_text: str
    smb_ok: bool | None
    smb_file_count: int | None
    smb_total_bytes: int | None
    smb_used_bytes: int | None
    smb_free_bytes: int | None
    error: str
    checked_at: float


@dataclass
class RaspyJackStatus:
    core_state: str
    device_state: str
    webui_state: str
    nmap_running: bool
    responder_running: bool
    ettercap_running: bool
    primary_iface: str
    primary_ip: str
    monitor_iface: str
    monitor_mode: str
    loot_files: int
    loot_size_bytes: int
    latest_nmap_name: str
    latest_nmap_path: str
    latest_nmap_size_bytes: int | None
    latest_nmap_age_seconds: float | None
    latest_nmap_mtime: float | None
    latest_nmap_stable: bool
    latest_nmap_preview_lines: list[str]


@dataclass
class AngryOxideStatus:
    running: bool
    pid: int | None
    iface: str
    iface_mode: str
    command: str
    log_path: str
    log_size_bytes: int
    log_age_seconds: float | None
    log_lines: list[str]
    result_files: int
    result_size_bytes: int
    hc22000_files: int
    pcap_files: int
    kismet_files: int
    tar_files: int
    discovered_ssids: int
    whitelist_count: int
    sockets_rx: int | None
    sockets_tx: int | None
    oui_records: int | None
    panic_count: int
    runtime_seconds: int | None
    fourway_hashes: int
    pmkid_hashes: int
    rogue_m2_events: int
    m1_sent_events: int


@dataclass
class GPSStatus:
    available: bool
    device: str
    mode: int
    fix_label: str
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    speed_kph: float | None
    track_deg: float | None
    satellites_used: int | None
    satellites_visible: int | None
    time_utc: str
    hdop: float | None
    pdop: float | None
    vdop: float | None
    epx_m: float | None
    epy_m: float | None
    epv_m: float | None
    climb_kph: float | None
    satellites: list[dict[str, Any]]


@dataclass
class WirelessAdapterStatus:
    iface: str
    role: str
    label: str
    driver: str
    mode: str
    operstate: str
    ip: str
    signal_dbm: int | None
    signal_pct: int | None
    active_profile: str
    is_onboard: bool


@dataclass
class NetworkStatus:
    primary_iface: str
    primary_ip: str
    primary_mode: str
    primary_operstate: str
    primary_profile: str
    monitor_iface: str
    monitor_ip: str
    monitor_mode: str
    monitor_operstate: str
    default_route_iface: str
    default_route_gw: str
    networkmanager_state: str
    tailscale_state: str
    wireless_adapters: list[WirelessAdapterStatus]


@dataclass
class Snapshot:
    ts: float
    hostname: str
    cpu_temp: float | None
    cpu_usage_pct: float | None
    mem_used_pct: float | None
    tailscale_ip: str
    battery_pct: float | None
    wlan0_signal_dbm: int | None
    wlan0_signal_pct: int | None
    gps: GPSStatus
    network: NetworkStatus
    nodes: list[NodeStatus]
    raspyjack: RaspyJackStatus
    angryoxide: AngryOxideStatus


class WaveshareInput:
    KEY_MAP = {
        "LEFT": pygame.K_LEFT,
        "RIGHT": pygame.K_RIGHT,
        "UP": pygame.K_UP,
        "DOWN": pygame.K_DOWN,
        "OK": pygame.K_RETURN,
        "KEY1": pygame.K_F1,
        "KEY2": pygame.K_F2,
        "KEY3": pygame.K_F3,
    }

    def __init__(self, pins: dict[str, int], debounce_seconds: float = 0.10):
        self.pins = {name: int(pin) for name, pin in pins.items() if name in self.KEY_MAP}
        self.debounce_seconds = max(0.03, float(debounce_seconds))
        self.state = {name: False for name in self.pins}
        self.last_ts = {name: 0.0 for name in self.pins}
        self.enabled = False

    def init(self) -> None:
        if GPIO is None:
            raise RuntimeError("RPi.GPIO not available")
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in self.pins.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.enabled = True

    def suspend(self) -> None:
        self.enabled = False

    def resume(self) -> None:
        if not self.enabled:
            self.init()

    def cleanup(self) -> None:
        if GPIO is None:
            return
        try:
            GPIO.cleanup(list(self.pins.values()))
        except Exception:
            pass
        self.enabled = False

    def poll(self) -> None:
        if (not self.enabled) or GPIO is None:
            return
        now = time.monotonic()
        for name, pin in self.pins.items():
            try:
                pressed = GPIO.input(pin) == 0
            except Exception:
                continue
            was_pressed = self.state[name]
            if pressed != was_pressed:
                self.state[name] = pressed
                if pressed and (now - self.last_ts[name]) >= self.debounce_seconds:
                    self.last_ts[name] = now
                    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=self.KEY_MAP[name]))


class WaveshareDisplay:
    def __init__(
        self,
        spi_port: int,
        spi_cs: int,
        dc_pin: int,
        rst_pin: int,
        backlight_pin: int,
        rotation: int = 90,
        invert: bool = True,
        spi_speed_hz: int = 24000000,
    ):
        if spidev is None or GPIO is None or numpy is None:
            raise RuntimeError("waveshare display dependencies not available")
        self.rotation = int(rotation)
        self.width = 240
        self.height = 240
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(int(dc_pin), GPIO.OUT)
        GPIO.setup(int(rst_pin), GPIO.OUT)
        GPIO.setup(int(backlight_pin), GPIO.OUT)
        self._spi = spidev.SpiDev(int(spi_port), int(spi_cs))
        self._spi.mode = 0b00
        self._spi.max_speed_hz = int(spi_speed_hz)
        self._dc = int(dc_pin)
        self._rst = int(rst_pin)
        self._bl = int(backlight_pin)
        self._write_pin(self._bl, False)
        time.sleep(0.05)
        self._write_pin(self._bl, True)
        self._init_panel()
        self.set_backlight(1.0)

    def _write_pin(self, pin: int, value: bool) -> None:
        GPIO.output(pin, GPIO.HIGH if value else GPIO.LOW)

    def _command(self, value: int) -> None:
        self._write_pin(self._dc, False)
        self._spi.writebytes([value & 0xFF])

    def _data(self, *values: int) -> None:
        if not values:
            return
        self._write_pin(self._dc, True)
        self._spi.writebytes([value & 0xFF for value in values])

    def _reset(self) -> None:
        self._write_pin(self._rst, True)
        time.sleep(0.01)
        self._write_pin(self._rst, False)
        time.sleep(0.01)
        self._write_pin(self._rst, True)
        time.sleep(0.01)

    def _init_panel(self) -> None:
        # Waveshare's 1.3in LCD HAT uses a different init sequence than the
        # generic ST7789 package. Without this, the panel stays black.
        self._reset()
        self._command(0x36)
        self._data(0x70)
        self._command(0x11)
        time.sleep(0.12)
        self._command(0x36)
        self._data(0x00)
        self._command(0x3A)
        self._data(0x05)
        self._command(0xB2)
        self._data(0x0C, 0x0C, 0x00, 0x33, 0x33)
        self._command(0xB7)
        self._data(0x00)
        self._command(0xBB)
        self._data(0x3F)
        self._command(0xC0)
        self._data(0x2C)
        self._command(0xC2)
        self._data(0x01)
        self._command(0xC3)
        self._data(0x0D)
        self._command(0xC6)
        self._data(0x0F)
        self._command(0xD0)
        self._data(0xA7)
        self._command(0xD0)
        self._data(0xA4, 0xA1)
        self._command(0xD6)
        self._data(0xA1)
        self._command(0xE0)
        self._data(0xF0, 0x00, 0x02, 0x01, 0x00, 0x00, 0x27, 0x43, 0x3F, 0x33, 0x0E, 0x0E, 0x26, 0x2E)
        self._command(0xE1)
        self._data(0xF0, 0x07, 0x0D, 0x0D, 0x0B, 0x16, 0x26, 0x43, 0x3E, 0x3F, 0x19, 0x19, 0x31, 0x3A)
        self._command(0x21)
        self._command(0x29)
        time.sleep(0.02)

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._command(0x2A)
        self._data(0x00, x0 & 0xFF, 0x00, (x1 - 1) & 0xFF)
        self._command(0x2B)
        self._data(0x00, y0 & 0xFF, 0x00, (y1 - 1) & 0xFF)
        self._command(0x2C)

    def set_backlight(self, value: float) -> None:
        self._write_pin(self._bl, float(value) > 0.0)

    def set_led(self, *_args: float) -> None:
        return

    def display_surface(self, surface: pygame.Surface) -> None:
        raw = pygame.image.tostring(surface, "RGB")
        image = Image.frombytes("RGB", surface.get_size(), raw)
        if self.rotation:
            image = image.rotate(-self.rotation, expand=False)
        image = image.convert("RGB")
        img = numpy.asarray(image)
        pix = numpy.zeros((self.width, self.height, 2), dtype=numpy.uint8)
        pix[..., [0]] = numpy.add(numpy.bitwise_and(img[..., [0]], 0xF8), numpy.right_shift(img[..., [1]], 5))
        pix[..., [1]] = numpy.add(
            numpy.bitwise_and(numpy.left_shift(img[..., [1]], 3), 0xE0),
            numpy.right_shift(img[..., [2]], 3),
        )
        buf = pix.flatten().tolist()
        self._set_window(0, 0, self.width, self.height)
        self._write_pin(self._dc, True)
        for i in range(0, len(buf), 4096):
            self._spi.writebytes(buf[i : i + 4096])


def clean_text(value: Any, max_len: int = 64) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\x00", " ")
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len] if max_len > 0 else s


def format_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    n = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for u in units:
        if n < 1024.0 or u == units[-1]:
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024.0
    return "n/a"


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except Exception:
        value = default
    return max(lo, min(hi, value))


def env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(str(raw).strip())
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_dict(base[k], v)
        else:
            base[k] = v
    return base


def ensure_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)

    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = copy.deepcopy(DEFAULT_CONFIG)
    _merge_dict(merged, raw)
    return merged


def write_config(path: Path, config: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def cmd_output(args: list[str], timeout: float = 1.5) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def run_shell(command: str, timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["/usr/bin/env", "bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, (result.stdout or ""), (result.stderr or "")
    except Exception as e:
        return 1, "", str(e)


def resolve_command(command: str) -> str:
    cmd = clean_text(command, 512)
    if not cmd:
        return ""
    try:
        parts = shlex.split(cmd)
    except Exception:
        return cmd
    if not parts:
        return ""

    head = parts[0]
    if "/" not in head and not head.startswith("."):
        found = shutil.which(head)
        if not found:
            for candidate in (f"/home/kali/{head}", f"/home/kali/{head}.sh"):
                p = Path(candidate)
                if p.exists() and os.access(str(p), os.X_OK):
                    found = str(p)
                    break
        if found:
            parts[0] = found
    return " ".join(shlex.quote(p) for p in parts)


def read_cpu_temp_c() -> float | None:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return round(float(raw) / 1000.0, 1)
    except Exception:
        return None


def read_mem_used_pct() -> float | None:
    mem_total = None
    mem_avail = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = float(line.split()[1])
        if mem_total and mem_avail is not None:
            return round((1.0 - (mem_avail / mem_total)) * 100.0, 1)
    except Exception:
        return None
    return None


_CPU_USAGE_PREV: tuple[int, int] | None = None


def _read_cpu_counters() -> tuple[int, int] | None:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            parts = f.readline().split()
        if not parts or parts[0] != "cpu":
            return None
        values = [int(v) for v in parts[1:]]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total, idle
    except Exception:
        return None


def read_cpu_usage_pct() -> float | None:
    global _CPU_USAGE_PREV

    current = _read_cpu_counters()
    if current is None:
        return None

    if _CPU_USAGE_PREV is None:
        _CPU_USAGE_PREV = current
        time.sleep(0.12)
        current = _read_cpu_counters()
        if current is None:
            return None

    prev_total, prev_idle = _CPU_USAGE_PREV
    total, idle = current
    _CPU_USAGE_PREV = current

    total_delta = total - prev_total
    idle_delta = idle - prev_idle
    if total_delta <= 0:
        return None

    usage = (1.0 - (idle_delta / total_delta)) * 100.0
    return round(clamp(usage, 0.0, 100.0), 1)


def read_tailscale_ip() -> str:
    out = cmd_output(["tailscale", "ip", "-4"], timeout=1.2)
    if not out:
        return "n/a"
    first = out.splitlines()[0].strip()
    return first if first else "n/a"


def dbm_to_pct(dbm: int | None) -> int | None:
    if dbm is None:
        return None
    return clamp(int(round(((dbm + 90.0) / 40.0) * 100.0)), 0, 100)


def read_wlan_signal(iface: str = "wlan0", interval_seconds: float = 4.0) -> tuple[int | None, int | None]:
    key = clean_text(iface, 24) or "wlan0"
    now = time.time()
    cached = WLAN_SIGNAL_CACHE.get(key)
    if cached and (now - cached[0]) < max(1.0, interval_seconds):
        return cached[1]

    dbm: int | None = None
    out = cmd_output(["iw", "dev", key, "link"], timeout=1.0)
    if out:
        m = IW_SIGNAL_RE.search(out)
        if m:
            try:
                dbm = int(round(float(m.group(1))))
            except Exception:
                dbm = None

    if dbm is None:
        try:
            with open("/proc/net/wireless", "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s.startswith(f"{key}:"):
                        continue
                    parts = s.replace(":", " ").split()
                    # /proc/net/wireless columns: iface status link level noise ...
                    if len(parts) >= 4:
                        try:
                            level = float(parts[3].rstrip("."))
                            if level <= 0:
                                dbm = int(round(level))
                        except Exception:
                            pass
                    break
        except Exception:
            pass

    pct = dbm_to_pct(dbm)
    WLAN_SIGNAL_CACHE[key] = (now, (dbm, pct))
    return (dbm, pct)


def _extract_battery_pct(text: str) -> float | None:
    s = clean_text(text, 256)
    if not s:
        return None

    for pattern in (PISUGAR_BATTERY_RE, PCT_RE):
        m = pattern.search(s)
        if not m:
            continue
        try:
            v = float(m.group(1))
            if 0.0 <= v <= 100.0:
                return round(v, 1)
        except Exception:
            continue

    m = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except Exception:
        return None
    if 0.0 <= v <= 100.0:
        return round(v, 1)
    return None


def _pisugar_query(command: str, timeout: float = 0.7) -> str:
    payload = (clean_text(command, 64) + "\n").encode("utf-8", errors="ignore")
    if not payload.strip():
        return ""

    uds = Path("/tmp/pisugar-server.sock")
    if uds.exists():
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(str(uds))
            s.sendall(payload)
            data = s.recv(256)
            s.close()
            return data.decode("utf-8", errors="replace").strip()
        except Exception:
            pass

    try:
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.settimeout(timeout)
        s2.connect(("127.0.0.1", 8423))
        s2.sendall(payload)
        data2 = s2.recv(256)
        s2.close()
        return data2.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def read_pisugar_battery_pct(interval_seconds: float = 15.0) -> float | None:
    global PISUGAR_BATTERY_CACHE
    now = time.time()
    if PISUGAR_BATTERY_CACHE and (now - PISUGAR_BATTERY_CACHE[0]) < max(3.0, interval_seconds):
        return PISUGAR_BATTERY_CACHE[1]

    cmd = os.environ.get("PISUGAR_BATTERY_CMD", "").strip()
    if cmd:
        _, out, err = run_shell(cmd, timeout=1.5)
        pct = _extract_battery_pct(f"{out}\n{err}")
        if pct is not None:
            PISUGAR_BATTERY_CACHE = (now, pct)
            return pct

    try:
        ps = Path("/sys/class/power_supply")
        if ps.exists():
            for p in sorted(ps.iterdir()):
                n = p.name.lower()
                if ("bat" not in n) and ("battery" not in n) and ("pisugar" not in n):
                    continue
                cap = p / "capacity"
                if cap.exists():
                    raw = cap.read_text(encoding="utf-8").strip()
                    pct = _extract_battery_pct(raw)
                    if pct is not None:
                        PISUGAR_BATTERY_CACHE = (now, pct)
                        return pct
    except Exception:
        pass

    pct = _extract_battery_pct(_pisugar_query("get battery"))
    PISUGAR_BATTERY_CACHE = (now, pct)
    return pct


def gps_mode_label(mode: int | None) -> str:
    if mode == 3:
        return "3D fix"
    if mode == 2:
        return "2D fix"
    if mode == 1:
        return "No fix"
    return "GPS offline"


def _gps_devices() -> list[str]:
    candidates: list[str] = []
    by_id = Path("/dev/serial/by-id")
    try:
        if by_id.exists():
            for path in sorted(by_id.iterdir()):
                name = path.name.lower()
                if "u-blox" in name or "ublox" in name or "gnss" in name or "gps" in name:
                    candidates.append(str(path))
    except Exception:
        pass

    for pattern in ("ttyACM*", "ttyUSB*"):
        for path in sorted(Path("/dev").glob(pattern)):
            candidates.append(str(path))

    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def ensure_gpsd_running() -> None:
    if shutil.which("gpsd") is None:
        return

    devices = _gps_devices()
    if not devices:
        return

    try:
        with socket.create_connection(("127.0.0.1", 2947), timeout=0.25):
            return
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["gpsd", "-n", devices[0], "-F", "/run/gpsd.sock"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return

    time.sleep(0.25)


def read_gps_status(interval_seconds: float = 5.0, timeout_seconds: float = 1.2) -> GPSStatus:
    global GPS_CACHE
    now = time.time()
    cached = GPS_CACHE
    if cached and (now - cached[0]) < max(1.0, interval_seconds):
        return cached[1]

    ensure_gpsd_running()

    device = ""
    last_tpv: dict[str, Any] = {}
    last_sky: dict[str, Any] = {}
    available = False

    try:
        with socket.create_connection(("127.0.0.1", 2947), timeout=0.35) as sock:
            available = True
            sock.settimeout(timeout_seconds)
            try:
                sock.recv(4096)
            except Exception:
                pass
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')

            deadline = time.monotonic() + max(0.5, timeout_seconds)
            buffer = ""
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    payload_class = payload.get("class")
                    if payload_class == "DEVICE":
                        device = clean_text(payload.get("path", ""), 128)
                    elif payload_class == "DEVICES":
                        devices = payload.get("devices", [])
                        if devices and isinstance(devices[0], dict):
                            device = clean_text(devices[0].get("path", ""), 128)
                    elif payload_class == "TPV":
                        last_tpv = payload
                    elif payload_class == "SKY":
                        last_sky = payload
                if last_tpv and last_sky:
                    break
    except Exception:
        pass

    mode = 0
    try:
        mode = int(last_tpv.get("mode", 0))
    except Exception:
        mode = 0

    satellites_visible = last_sky.get("nSat")
    satellites_used = last_sky.get("uSat")
    if satellites_used is None:
        sats = last_sky.get("satellites", [])
        if isinstance(sats, list):
            satellites_used = sum(1 for sat in sats if isinstance(sat, dict) and sat.get("used"))
    if satellites_visible is None:
        sats = last_sky.get("satellites", [])
        if isinstance(sats, list):
            satellites_visible = len(sats)

    def _maybe_float(value: Any, digits: int = 6) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value), digits)
        except Exception:
            return None

    speed_mps = _maybe_float(last_tpv.get("speed"), 3)
    climb_mps = _maybe_float(last_tpv.get("climb"), 3)
    device_path = device
    if not device_path:
        devices = _gps_devices()
        device_path = devices[0] if devices else ""

    result = GPSStatus(
        available=available or bool(device) or bool(last_tpv) or bool(last_sky),
        device=device_path,
        mode=mode,
        fix_label=gps_mode_label(mode),
        latitude=_maybe_float(last_tpv.get("lat")),
        longitude=_maybe_float(last_tpv.get("lon")),
        altitude_m=_maybe_float(last_tpv.get("altHAE"), 1) or _maybe_float(last_tpv.get("alt"), 1),
        speed_kph=(None if speed_mps is None else round(speed_mps * 3.6, 1)),
        track_deg=_maybe_float(last_tpv.get("track"), 1),
        satellites_used=satellites_used if isinstance(satellites_used, int) else None,
        satellites_visible=satellites_visible if isinstance(satellites_visible, int) else None,
        time_utc=clean_text(last_tpv.get("time") or last_sky.get("time") or "", 48),
        hdop=_maybe_float(last_sky.get("hdop"), 1),
        pdop=_maybe_float(last_sky.get("pdop"), 1),
        vdop=_maybe_float(last_sky.get("vdop"), 1),
        epx_m=_maybe_float(last_tpv.get("epx"), 1),
        epy_m=_maybe_float(last_tpv.get("epy"), 1),
        epv_m=_maybe_float(last_tpv.get("epv"), 1),
        climb_kph=(None if climb_mps is None else round(climb_mps * 3.6, 1)),
        satellites=[
            {
                "prn": clean_text(sat.get("PRN") or sat.get("svid") or sat.get("gnssid") or "?", 12),
                "ss": _maybe_float(sat.get("ss"), 0),
                "used": bool(sat.get("used")),
                "el": _maybe_float(sat.get("el"), 0),
                "az": _maybe_float(sat.get("az"), 0),
            }
            for sat in (last_sky.get("satellites", []) if isinstance(last_sky.get("satellites", []), list) else [])
            if isinstance(sat, dict)
        ],
    )
    GPS_CACHE = (now, result)
    return result


def ping_latency_ms(host: str, timeout: float) -> float | None:
    wait_seconds = max(1, int(math.ceil(timeout)))
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(wait_seconds), host],
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout + 0.8),
            check=False,
        )
        if result.returncode != 0:
            return None
        match = PING_RE.search(result.stdout)
        if not match:
            return None
        return round(float(match.group(1)), 1)
    except Exception:
        return None


def check_port(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, int(port))) == 0
    except Exception:
        return False
    finally:
        sock.close()


def json_path_get(value: Any, path: str) -> Any:
    current = value
    for part in [p for p in path.split(".") if p]:
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current


def fetch_health(url: str, timeout: float, json_path: str = "", expect: str = "") -> tuple[str, bool, str]:
    if not url:
        return ("(no endpoint)", True, "")

    req = Request(url, headers={"User-Agent": "displayhatmini-dashboard/1.0"})
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace").strip()
            ctype = response.headers.get("Content-Type", "")
    except HTTPError as e:
        if e.code in (401, 403):
            return (f"http {e.code} auth", True, "")
        return (f"http {e.code}", False, f"HTTP {e.code}")
    except URLError as e:
        return ("endpoint down", False, str(e.reason))
    except Exception as e:
        return ("endpoint down", False, str(e))

    text = body
    if "json" in ctype.lower() or body.startswith("{"):
        try:
            parsed = json.loads(body)
            if json_path:
                parsed = json_path_get(parsed, json_path)
            text = str(parsed)
        except Exception:
            text = body[:64]

    text = clean_text(text, 64)
    if not expect:
        return (text or "ok", True, "")

    good = expect.lower() in text.lower()
    return (text or "n/a", good, "" if good else f"expected '{expect}'")


def parse_smb_ls_output(output: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "file_count": 0,
        "dir_count": 0,
        "file_bytes": 0,
        "total_bytes": None,
        "free_bytes": None,
        "used_bytes": None,
    }

    for line in output.splitlines():
        bm = SMB_BLOCKS_RE.search(line)
        if bm:
            total_blocks = int(bm.group(1))
            block_size = int(bm.group(2))
            free_blocks = int(bm.group(3))
            total = total_blocks * block_size
            free = free_blocks * block_size
            details["total_bytes"] = total
            details["free_bytes"] = free
            details["used_bytes"] = max(0, total - free)
            continue

        em = SMB_ENTRY_RE.match(line)
        if not em:
            continue
        name = clean_text(em.group(1), 160)
        attrs = em.group(2)
        size = int(em.group(3))
        if name in (".", ".."):
            continue
        if "D" in attrs:
            details["dir_count"] += 1
        else:
            details["file_count"] += 1
            details["file_bytes"] += size

    return details


def _smb_deep_stats(
    host: str,
    share: str,
    user: str,
    password: str,
    timeout: float,
    refresh_seconds: float,
) -> dict[str, Any]:
    key = f"{host}/{share}/{user}"
    now = time.time()
    cached = SMB_DETAIL_CACHE.get(key)
    if cached and (now - cached[0]) < refresh_seconds:
        return cached[1]

    smbclient = shutil.which("smbclient")
    if not smbclient:
        details: dict[str, Any] = {}
        SMB_DETAIL_CACHE[key] = (now, details)
        return details

    args = [smbclient, f"//{host}/{share}", "-m", "SMB3", "-c", "recurse ON;ls"]
    if user:
        args.extend(["-U", f"{user}%{password}"])
    else:
        args.append("-N")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(2.5, min(10.0, timeout * 4.0)),
            check=False,
        )
        details = parse_smb_ls_output(result.stdout if result.returncode == 0 else "")
    except Exception:
        details = {}

    SMB_DETAIL_CACHE[key] = (now, details)
    return details


def probe_smb(cfg: dict[str, Any], timeout: float, refresh_seconds: float) -> tuple[str, bool | None, dict[str, Any]]:
    smb_cfg = cfg.get("smb")
    if not isinstance(smb_cfg, dict):
        return ("", None, {})

    host = str(smb_cfg.get("host", cfg.get("host", ""))).strip()
    share = str(smb_cfg.get("share", "")).strip()
    user = str(smb_cfg.get("username", "")).strip()
    password = str(smb_cfg.get("password", "")).strip()
    if not host or not share:
        return ("SMB config missing", False, {})

    if not check_port(host, 445, timeout=min(timeout, 1.0)):
        return ("SMB 445 closed", False, {})

    smbclient = shutil.which("smbclient")
    if not smbclient:
        return (f"SMB {share} reachable", True, {})

    args = [smbclient, f"//{host}/{share}", "-m", "SMB3", "-c", "ls"]
    if user:
        args.extend(["-U", f"{user}%{password}"])
    else:
        args.append("-N")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(1.5, timeout + 1.0),
            check=False,
        )
    except Exception:
        return (f"SMB {share} probe failed", False, {})

    if result.returncode != 0:
        err_blob = clean_text((result.stderr or "") + " " + (result.stdout or ""), 80).lower()
        if "access_denied" in err_blob or "logon_failure" in err_blob or "unauthorized" in err_blob:
            return (f"SMB {share} unauthorized", False, {})
        return (f"SMB {share} auth/share fail", False, {})

    deep_stats_enabled = bool(cfg.get("smb_deep_stats_enabled", False))
    if not deep_stats_enabled:
        return (f"SMB {share} auth ok", True, {})

    details = _smb_deep_stats(host, share, user, password, timeout, refresh_seconds)
    return (f"SMB {share} auth ok", True, details)


def probe_node(cfg: dict[str, Any], timeout: float, smb_detail_refresh: float) -> NodeStatus:
    name = str(cfg.get("name", "node"))
    host = str(cfg.get("host", "")).strip()
    ports = cfg.get("ports", [])
    ports = [int(p) for p in ports if isinstance(p, int) or str(p).isdigit()]
    health_url = str(cfg.get("health_url", "")).strip()
    health_path = str(cfg.get("health_json_path", "")).strip()
    health_expect = str(cfg.get("health_expect", "")).strip()

    now = time.time()
    if not host:
        return NodeStatus(
            name=name,
            host="(missing host)",
            status="offline",
            latency_ms=None,
            ports_open=[],
            ports_closed=[],
            health_text="invalid config",
            smb_text="",
            smb_ok=None,
            smb_file_count=None,
            smb_total_bytes=None,
            smb_used_bytes=None,
            smb_free_bytes=None,
            error="host missing",
            checked_at=now,
        )

    latency = ping_latency_ms(host, timeout)
    ports_open: list[int] = []
    ports_closed: list[int] = []
    for p in ports:
        if check_port(host, p, timeout=min(timeout, 0.9)):
            ports_open.append(p)
        else:
            ports_closed.append(p)

    health_text, health_ok, health_error = fetch_health(health_url, timeout, health_path, health_expect)
    smb_text, smb_ok, smb_details = probe_smb(cfg, timeout, smb_detail_refresh)

    reachable = (latency is not None) or bool(ports_open) or (bool(health_url) and health_ok) or (smb_ok is True)

    if not reachable:
        status = "offline"
        if health_error:
            error = health_error
        elif ports:
            error = "host unreachable"
        else:
            error = "ping failed"
    elif health_ok and (smb_ok is not False):
        status = "online"
        error = ""
    else:
        status = "degraded"
        error = health_error or (smb_text if smb_ok is False else "health check failed")

    return NodeStatus(
        name=name,
        host=host,
        status=status,
        latency_ms=latency,
        ports_open=ports_open,
        ports_closed=ports_closed,
        health_text=health_text,
        smb_text=smb_text,
        smb_ok=smb_ok,
        smb_file_count=smb_details.get("file_count"),
        smb_total_bytes=smb_details.get("total_bytes"),
        smb_used_bytes=smb_details.get("used_bytes"),
        smb_free_bytes=smb_details.get("free_bytes"),
        error=error,
        checked_at=now,
    )


def service_state(service_name: str) -> str:
    if not service_name:
        return "n/a"
    out = cmd_output(["systemctl", "is-active", service_name], timeout=1.2)
    return clean_text(out or "unknown", 16)


def iface_mode(iface: str) -> str:
    if not iface:
        return "n/a"
    out = cmd_output(["iw", "dev", iface, "info"], timeout=1.2)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("type "):
            return clean_text(line.split(None, 1)[1], 16)
    legacy = cmd_output(["iwconfig", iface], timeout=1.2)
    match = re.search(r"Mode:([A-Za-z-]+)", legacy)
    if match:
        return clean_text(match.group(1), 16)
    return "n/a"


def wait_for_iface_mode(iface: str, expected_mode: str, timeout_seconds: float = 4.0) -> bool:
    deadline = time.monotonic() + max(0.5, timeout_seconds)
    want = expected_mode.strip().lower()
    while time.monotonic() < deadline:
        mode = iface_mode(iface).strip().lower()
        if mode == want:
            return True
        time.sleep(0.2)
    return False


def force_iface_mode(iface: str, mode: str) -> bool:
    if not iface:
        return False
    safe_iface = shlex.quote(iface)
    safe_mode = shlex.quote(mode)
    steps = [
        ["/usr/bin/env", "bash", "-lc", f"ip link set {safe_iface} down"],
    ]
    if mode.strip().lower() == "monitor":
        steps.append(["/usr/bin/env", "bash", "-lc", f"nmcli device set {safe_iface} managed no || true"])
    steps.append(["/usr/bin/env", "bash", "-lc", f"iw dev {safe_iface} set type {safe_mode}"])
    steps.append(["/usr/bin/env", "bash", "-lc", f"ip link set {safe_iface} up"])
    if mode.strip().lower() == "managed":
        steps.append(["/usr/bin/env", "bash", "-lc", f"nmcli device set {safe_iface} managed yes || true"])
    for cmd in steps:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0, check=False)
            if result.returncode != 0:
                return False
        except Exception:
            return False
    return True


def iface_ip(iface: str) -> str:
    if not iface:
        return "n/a"
    out = cmd_output(["ip", "-4", "addr", "show", iface], timeout=1.2)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            token = line.split()[1]
            return token.split("/")[0]
    return "n/a"


def iface_operstate(iface: str) -> str:
    if not iface:
        return "n/a"
    out = cmd_output(["ip", "link", "show", iface], timeout=1.2)
    for line in out.splitlines():
        if f"{iface}:" not in line:
            continue
        if "state " in line:
            return clean_text(line.split("state ", 1)[1].split()[0].lower(), 16)
    return "n/a"


def default_route() -> tuple[str, str]:
    out = cmd_output(["ip", "-4", "route", "show", "default"], timeout=1.2)
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        gw = "n/a"
        dev = "n/a"
        if "via" in parts:
            try:
                gw = parts[parts.index("via") + 1]
            except Exception:
                pass
        if "dev" in parts:
            try:
                dev = parts[parts.index("dev") + 1]
            except Exception:
                pass
        return (clean_text(dev, 16), clean_text(gw, 32))
    return ("n/a", "n/a")


def active_wifi_profile(iface: str) -> str:
    if not iface:
        return "n/a"
    out = cmd_output(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], timeout=1.5)
    for line in out.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            continue
        name, device = line.split(":", 1)
        if device.strip() == iface:
            return clean_text(name.strip(), 32) or "n/a"
    return "n/a"


def _sysfs_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _is_onboard_wifi(iface: str) -> bool:
    base = Path("/sys/class/net") / iface / "device"
    try:
        real = base.resolve()
        if "mmc" in str(real):
            return True
    except Exception:
        pass
    driver = _sysfs_text(base / "driver" / "module" / "drivers")
    if "brcmfmac" in driver:
        return True
    try:
        driver_real = (base / "driver").resolve()
        if driver_real.name == "brcmfmac":
            return True
    except Exception:
        pass
    return False


def _wireless_label(iface: str, driver: str, vendor_id: str, product_id: str, onboard: bool) -> str:
    if onboard:
        return "Internal"
    vendor_map = {
        "0x0e8d": "MediaTek",
        "0x148f": "MediaTek",
        "0x2357": "TP-Link",
        "0x2001": "D-Link",
        "0x0846": "Netgear",
        "0x0bda": "Realtek",
        "0x050d": "Belkin",
        "0x0411": "Buffalo",
        "0x0cf3": "Atheros",
        "0x168c": "Atheros",
        "0x7392": "Edimax",
        "0x2019": "Planex",
        "0x04bb": "I-O DATA",
        "0x056e": "ELECOM",
        "0x07b8": "Abocom",
        "0x0586": "ZyXEL",
        "0x1d6b": "Linux USB",
    }
    driver_map = {
        "mt76x2u": "MediaTek",
        "mt76x0u": "MediaTek",
        "mt7921u": "MediaTek",
        "ath9k_htc": "Alfa/Atheros",
        "ath10k_usb": "Atheros",
        "rtl88xxau": "Realtek",
        "rtl8812au": "Realtek",
        "rtl8814au": "Realtek",
        "rtl8xxxu": "Realtek",
        "brcmfmac": "Internal",
    }
    label = vendor_map.get(vendor_id.lower()) if vendor_id else ""
    if not label and driver:
        label = driver_map.get(driver.lower(), "")
    if not label and product_id.lower() in {"0x7612", "0x7662"}:
        label = "MediaTek"
    return label or "WiFi"


def collect_wireless_adapters(config: dict[str, Any]) -> list[WirelessAdapterStatus]:
    cfg = config.get("network_ops", {}) if isinstance(config.get("network_ops"), dict) else {}
    primary_iface = clean_text(cfg.get("primary_iface", "wlan0"), 24) or "wlan0"
    monitor_iface = clean_text(cfg.get("monitor_iface", "wlan1"), 24) or "wlan1"
    base = Path("/sys/class/net")
    items: list[WirelessAdapterStatus] = []
    try:
        names = sorted(
            p.name for p in base.iterdir()
            if p.name.startswith("wlan") and (p / "wireless").exists()
        )
    except Exception:
        names = []
    for iface in names:
        device_dir = base / iface / "device"
        vendor_id = _sysfs_text(device_dir / "vendor")
        product_id = _sysfs_text(device_dir / "device")
        driver = "n/a"
        try:
            driver = clean_text((device_dir / "driver").resolve().name, 24) or "n/a"
        except Exception:
            pass
        onboard = _is_onboard_wifi(iface)
        role = "primary" if iface == primary_iface else ("monitor" if iface == monitor_iface else "aux")
        label = _wireless_label(iface, driver, vendor_id, product_id, onboard)
        signal_dbm, signal_pct = read_wlan_signal(iface)
        items.append(
            WirelessAdapterStatus(
                iface=iface,
                role=role,
                label=clean_text(label, 18) or "WiFi",
                driver=driver,
                mode=iface_mode(iface),
                operstate=iface_operstate(iface),
                ip=iface_ip(iface),
                signal_dbm=signal_dbm,
                signal_pct=signal_pct,
                active_profile=active_wifi_profile(iface),
                is_onboard=onboard,
            )
        )
    return items


def collect_network_status(config: dict[str, Any]) -> NetworkStatus:
    cfg = config.get("network_ops", {}) if isinstance(config.get("network_ops"), dict) else {}
    primary_iface = clean_text(cfg.get("primary_iface", "wlan0"), 24) or "wlan0"
    monitor_iface = clean_text(cfg.get("monitor_iface", "wlan1"), 24) or "wlan1"
    route_iface, route_gw = default_route()
    return NetworkStatus(
        primary_iface=primary_iface,
        primary_ip=iface_ip(primary_iface),
        primary_mode=iface_mode(primary_iface),
        primary_operstate=iface_operstate(primary_iface),
        primary_profile=active_wifi_profile(primary_iface),
        monitor_iface=monitor_iface,
        monitor_ip=iface_ip(monitor_iface),
        monitor_mode=iface_mode(monitor_iface),
        monitor_operstate=iface_operstate(monitor_iface),
        default_route_iface=route_iface,
        default_route_gw=route_gw,
        networkmanager_state=service_state(clean_text(cfg.get("networkmanager_service", "NetworkManager.service"), 64)),
        tailscale_state=service_state(clean_text(cfg.get("tailscale_service", "tailscaled.service"), 64)),
        wireless_adapters=collect_wireless_adapters(config),
    )


def _dir_stats(path: Path, prefix: str = "", interval: float = 20.0) -> tuple[int, int]:
    key = f"{path}|{prefix}"
    now = time.time()
    cached = DIR_STATS_CACHE.get(key)
    if cached and (now - cached[0]) < interval:
        return cached[1]

    file_count = 0
    total_size = 0
    if path.exists() and path.is_dir():
        try:
            for root, _, files in os.walk(path):
                for name in files:
                    if prefix and (prefix not in name):
                        continue
                    file_count += 1
                    fp = Path(root) / name
                    try:
                        total_size += fp.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass

    result = (file_count, total_size)
    DIR_STATS_CACHE[key] = (now, result)
    return result


def _candidate_nmap_dirs(rj_cfg: dict[str, Any], loot_path: Path) -> list[Path]:
    dirs: list[Path] = []
    configured = rj_cfg.get("nmap_results_dirs", [])
    if isinstance(configured, list):
        for item in configured:
            p = Path(str(item)).expanduser()
            if p not in dirs:
                dirs.append(p)
    if loot_path not in dirs:
        dirs.append(loot_path / "Nmap")
    for p in (Path("/root/Raspyjack/loot/Nmap"), Path("/home/kari/Projects/Raspyjack/loot/Nmap")):
        if p not in dirs:
            dirs.append(p)
    return dirs


def _latest_nmap_result(rj_cfg: dict[str, Any], loot_path: Path, nmap_running: bool) -> dict[str, Any]:
    now = time.time()
    entries: list[tuple[float, int, Path]] = []
    for d in _candidate_nmap_dirs(rj_cfg, loot_path):
        if not d.exists() or (not d.is_dir()):
            continue
        try:
            for fp in d.iterdir():
                if (not fp.is_file()) or (not fp.name.lower().endswith(".txt")):
                    continue
                if fp.name.lower().startswith("readme"):
                    continue
                try:
                    st = fp.stat()
                except Exception:
                    continue
                entries.append((float(st.st_mtime), int(st.st_size), fp))
        except Exception:
            continue

    if not entries:
        return {
            "name": "",
            "path": "",
            "size_bytes": None,
            "age_seconds": None,
            "mtime": None,
            "stable": True,
            "preview_lines": [],
        }

    entries.sort(key=lambda x: x[0], reverse=True)
    selected_idx = 0
    selected_stable = True
    newest_age = max(0.0, now - entries[0][0])
    if nmap_running and newest_age < 8.0:
        if len(entries) > 1:
            selected_idx = 1
        else:
            selected_stable = False

    mtime, size_bytes, path = entries[selected_idx]
    age_seconds = max(0.0, now - mtime)
    preview_lines = tail_lines(path, 12)

    return {
        "name": clean_text(path.name, 120),
        "path": str(path),
        "size_bytes": size_bytes,
        "age_seconds": age_seconds,
        "mtime": mtime,
        "stable": selected_stable,
        "preview_lines": preview_lines,
    }


def read_pid_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pid_file(path: Path, pid: int) -> None:
    try:
        path.write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def pid_cmdline(pid: int | None) -> str:
    if not pid or pid <= 0:
        return ""
    try:
        return cmd_output(["ps", "-o", "args=", "-p", str(pid)], timeout=0.9).strip()
    except Exception:
        return ""


def pid_is_angryoxide(pid: int | None) -> bool:
    cmd = pid_cmdline(pid).lower()
    if not cmd:
        return False
    if "angryoxide" not in cmd:
        return False
    if "pgrep -f angryoxide" in cmd:
        return False
    return True


def pid_runtime_seconds(pid: int | None) -> int | None:
    if not pid or pid <= 0:
        return None
    try:
        out = cmd_output(["ps", "-o", "etimes=", "-p", str(pid)], timeout=1.2)
        value = clean_text(out, 16)
        return int(value) if value.isdigit() else None
    except Exception:
        return None


def find_angryoxide_pid() -> int | None:
    pids = find_angryoxide_pids()
    return pids[-1] if pids else None


def find_angryoxide_pids() -> list[int]:
    pids: list[int] = []
    seen: set[int] = set()

    # Exact process-name match only; broad pattern matching causes false positives
    # (for example, pgrep helper commands that include "angryoxide" in their args).
    out = cmd_output(["pgrep", "-x", "angryoxide"], timeout=1.3)
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pid = int(line)
            if pid > 0 and pid not in seen:
                seen.add(pid)
                pids.append(pid)
    return pids


def tail_lines(path: Path, count: int = 5) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = 4096
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= (count + 2):
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
        lines = data.decode("utf-8", errors="replace").splitlines()
        return [clean_text(x, 60) for x in lines[-count:] if clean_text(x, 1)]
    except Exception:
        return []


AO_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
AO_TIMESTAMP_PREFIX_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*)?(?:\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\s*UTC)?\s*[-|:]?\s*)+",
    re.IGNORECASE,
)


def sanitize_ao_log_line(text: str) -> str:
    line = AO_ANSI_RE.sub("", text or "")
    line = line.replace("\x00", " ")
    line = AO_TIMESTAMP_PREFIX_RE.sub("", line).strip()
    return clean_text(line or text, 60)


def read_tail_text(path: Path, max_bytes: int = 262144) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            take = min(max_bytes, size)
            f.seek(size - take)
            data = f.read(take)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def parse_ao_log_metrics(log_text: str) -> dict[str, Any]:
    sockets_rx = None
    sockets_tx = None
    oui_records = None
    ssids: set[str] = set()

    for m in AO_SOCKETS_RE.finditer(log_text):
        sockets_rx = int(m.group(1))
        sockets_tx = int(m.group(2))
    for m in AO_OUI_RE.finditer(log_text):
        oui_records = int(m.group(1))
    for m in AO_SSID_RE.finditer(log_text):
        ssid = clean_text(m.group(1), 80)
        if ssid:
            ssids.add(ssid)

    panic_count = log_text.lower().count("panicked at")
    rogue_m2_events = len(AO_ROGUE_M2_RE.findall(log_text))
    m1_sent_events = len(AO_M1_SENT_RE.findall(log_text))
    return {
        "sockets_rx": sockets_rx,
        "sockets_tx": sockets_tx,
        "oui_records": oui_records,
        "panic_count": panic_count,
        "discovered_ssids": len(ssids),
        "rogue_m2_events": rogue_m2_events,
        "m1_sent_events": m1_sent_events,
    }


def parse_hc22000_metrics(results_dir: Path) -> tuple[int, int]:
    key = str(results_dir)
    now = time.time()
    cached = AO_HC_METRICS_CACHE.get(key)
    if cached and (now - cached[0]) < 20.0:
        return cached[1]
    fourway = 0
    pmkid = 0
    try:
        if not results_dir.exists() or not results_dir.is_dir():
            return (0, 0)
        for fp in results_dir.iterdir():
            if not fp.is_file() or not fp.name.lower().endswith(".hc22000"):
                continue
            try:
                for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("WPA*02*"):
                        fourway += 1
                    elif line.startswith("WPA*01*"):
                        pmkid += 1
            except Exception:
                continue
    except Exception:
        pass
    result = (fourway, pmkid)
    AO_HC_METRICS_CACHE[key] = (now, result)
    return result


def collect_ao_results_summary(results_dir: Path) -> tuple[int, int, int, int]:
    key = str(results_dir)
    now = time.time()
    cached = AO_RESULTS_SUMMARY_CACHE.get(key)
    if cached and (now - cached[0]) < 20.0:
        return cached[1]

    hc22000_files = 0
    pcap_files = 0
    kismet_files = 0
    tar_files = 0
    try:
        if results_dir.exists() and results_dir.is_dir():
            for fp in results_dir.iterdir():
                if not fp.is_file():
                    continue
                n = fp.name.lower()
                if n.endswith(".hc22000"):
                    hc22000_files += 1
                elif n.endswith(".pcapng") or n.endswith(".pcap"):
                    pcap_files += 1
                elif n.endswith(".kismet"):
                    kismet_files += 1
                elif n.endswith(".tar.gz"):
                    tar_files += 1
    except Exception:
        pass

    result = (hc22000_files, pcap_files, kismet_files, tar_files)
    AO_RESULTS_SUMMARY_CACHE[key] = (now, result)
    return result


def collect_ao_log_summary(log_path: Path) -> tuple[dict[str, Any], list[str]]:
    key = str(log_path)
    try:
        st = log_path.stat()
        log_mtime = float(st.st_mtime)
        log_size = int(st.st_size)
    except Exception:
        log_mtime = None
        log_size = None

    cached = AO_LOG_METRICS_CACHE.get(key)
    if cached and cached[1] == log_mtime and cached[2] == log_size:
        return cached[3], cached[4]

    log_text = read_tail_text(log_path, max_bytes=262144)
    parsed = parse_ao_log_metrics(log_text)
    lines = [sanitize_ao_log_line(x) for x in tail_lines(log_path, 6) if sanitize_ao_log_line(x)]
    AO_LOG_METRICS_CACHE[key] = (time.time(), log_mtime, log_size, parsed, lines)
    return parsed, lines


def collect_raspyjack(config: dict[str, Any]) -> RaspyJackStatus:
    rj_cfg = config.get("raspyjack", {}) if isinstance(config.get("raspyjack"), dict) else {}
    services = rj_cfg.get("service_names", [])
    if not isinstance(services, list):
        services = []
    names = [str(x) for x in services if str(x).strip()]

    core = service_state(names[0]) if len(names) > 0 else "unknown"
    device = service_state(names[1]) if len(names) > 1 else "unknown"

    web_service_candidates: list[str] = []
    if len(names) > 2 and names[2]:
        web_service_candidates.append(names[2])
    extra_web_services = rj_cfg.get("webui_service_names", [])
    if isinstance(extra_web_services, list):
        for svc in extra_web_services:
            s = clean_text(svc, 96)
            if s and s not in web_service_candidates:
                web_service_candidates.append(s)
    if "caddy.service" not in web_service_candidates:
        web_service_candidates.append("caddy.service")

    web_states = [service_state(svc) for svc in web_service_candidates]
    if "active" in web_states:
        webui = "active"
    else:
        web_proc_running = bool(
            cmd_output(["pgrep", "-f", "web_server.py|raspyjack-webui|raspyjack.*web"], timeout=0.9)
        )
        web_host = clean_text(rj_cfg.get("webui_host", "127.0.0.1"), 64) or "127.0.0.1"
        try:
            web_port = int(rj_cfg.get("webui_port", 8080))
        except Exception:
            web_port = 8080
        web_port_open = check_port(web_host, web_port, timeout=0.45)

        web_url = clean_text(rj_cfg.get("webui_url", ""), 200)
        web_http_ok = False
        if web_url:
            _, web_http_ok, _ = fetch_health(web_url, timeout=0.8)

        if web_http_ok:
            webui = "active(http)"
        elif web_port_open and web_proc_running:
            webui = "active(port+proc)"
        elif web_port_open:
            webui = "active(port)"
        elif web_proc_running:
            webui = "active(proc)"
        elif web_states:
            webui = web_states[0]
        else:
            webui = "unknown"

    nmap_running = bool(cmd_output(["pgrep", "-f", "nmap"], timeout=0.9))
    responder_running = bool(cmd_output(["pgrep", "-f", "Responder.py|Responder"], timeout=0.9))
    ettercap_running = bool(cmd_output(["pgrep", "-f", "ettercap"], timeout=0.9))

    primary_iface = str(rj_cfg.get("primary_interface", "wlan0"))
    monitor_iface = str(rj_cfg.get("monitor_interface", "wlan1"))

    loot_path = Path(str(rj_cfg.get("loot_path", "/home/kali/Raspyjack/loot"))).expanduser()
    if not loot_path.exists():
        for alt in (Path("/root/Raspyjack/loot"), Path("/home/kari/Projects/Raspyjack/loot")):
            if alt.exists():
                loot_path = alt
                break
    loot_files, loot_size = _dir_stats(loot_path, prefix="", interval=25.0)
    latest_nmap = _latest_nmap_result(rj_cfg, loot_path, nmap_running)

    return RaspyJackStatus(
        core_state=core,
        device_state=device,
        webui_state=webui,
        nmap_running=nmap_running,
        responder_running=responder_running,
        ettercap_running=ettercap_running,
        primary_iface=primary_iface,
        primary_ip=iface_ip(primary_iface),
        monitor_iface=monitor_iface,
        monitor_mode=iface_mode(monitor_iface),
        loot_files=loot_files,
        loot_size_bytes=loot_size,
        latest_nmap_name=clean_text(latest_nmap.get("name", ""), 120),
        latest_nmap_path=str(latest_nmap.get("path", "")),
        latest_nmap_size_bytes=latest_nmap.get("size_bytes"),
        latest_nmap_age_seconds=latest_nmap.get("age_seconds"),
        latest_nmap_mtime=latest_nmap.get("mtime"),
        latest_nmap_stable=bool(latest_nmap.get("stable", True)),
        latest_nmap_preview_lines=list(latest_nmap.get("preview_lines", [])),
    )


def build_angryoxide_command(cfg: dict[str, Any]) -> str:
    base = clean_text(cfg.get("command", "/home/kali/angryoxide -i wlan1"), 512)
    networks = cfg.get("whitelist_networks", [])
    flag = clean_text(cfg.get("whitelist_flag", "--whitelist"), 32)

    if not isinstance(networks, list) or not networks or not flag:
        return base

    suffix = ""
    for n in networks:
        net = clean_text(n, 64)
        if net:
            suffix += f" {flag} {net}"
    return (base + suffix).strip()


def collect_angryoxide(config: dict[str, Any]) -> AngryOxideStatus:
    ao_cfg = config.get("angryoxide", {}) if isinstance(config.get("angryoxide"), dict) else {}
    iface = clean_text(ao_cfg.get("interface", "wlan1"), 24)
    command = build_angryoxide_command(ao_cfg)
    log_path = Path(str(ao_cfg.get("log_path", "/home/kali/Results/angryoxide-live.log"))).expanduser()
    results_dir = Path(str(ao_cfg.get("results_dir", "/home/kali/Results"))).expanduser()
    results_prefix = clean_text(ao_cfg.get("results_prefix", "oxide"), 32)

    pid = read_pid_file(ANGRYOXIDE_PID_PATH)
    live_pids = find_angryoxide_pids()
    if (not pid) or (pid not in live_pids):
        pid = live_pids[-1] if live_pids else None

    running = pid is not None
    runtime_seconds = pid_runtime_seconds(pid if running else None)

    log_size = None
    log_age = None
    if log_path.exists() and log_path.is_file():
        try:
            st = log_path.stat()
            log_size = st.st_size
            log_age = max(0.0, time.time() - st.st_mtime)
        except Exception:
            pass

    result_files, result_bytes = _dir_stats(results_dir, prefix=results_prefix, interval=20.0)
    hc22000_files, pcap_files, kismet_files, tar_files = collect_ao_results_summary(results_dir)
    parsed, log_lines = collect_ao_log_summary(log_path)
    fourway_hashes, pmkid_hashes = parse_hc22000_metrics(results_dir)

    whitelist_count = 0
    wl_cfg = ao_cfg.get("whitelist_networks", [])
    if isinstance(wl_cfg, list):
        whitelist_count = len([x for x in wl_cfg if clean_text(x, 80)])
    if whitelist_count == 0:
        whitelist_count = len(AO_WL_ARG_RE.findall(command))

    return AngryOxideStatus(
        running=running,
        pid=pid if running else None,
        iface=iface,
        iface_mode=iface_mode(iface),
        command=command,
        log_path=str(log_path),
        log_size_bytes=0 if log_size is None else int(log_size),
        log_age_seconds=log_age,
        log_lines=log_lines,
        result_files=result_files,
        result_size_bytes=result_bytes,
        hc22000_files=hc22000_files,
        pcap_files=pcap_files,
        kismet_files=kismet_files,
        tar_files=tar_files,
        discovered_ssids=int(parsed["discovered_ssids"]),
        whitelist_count=whitelist_count,
        sockets_rx=parsed["sockets_rx"],
        sockets_tx=parsed["sockets_tx"],
        oui_records=parsed["oui_records"],
        panic_count=int(parsed["panic_count"]),
        runtime_seconds=runtime_seconds,
        fourway_hashes=fourway_hashes,
        pmkid_hashes=pmkid_hashes,
        rogue_m2_events=int(parsed["rogue_m2_events"]),
        m1_sent_events=int(parsed["m1_sent_events"]),
    )


def collect_snapshot(config: dict[str, Any]) -> Snapshot:
    timeout = float(config.get("request_timeout_seconds", 1.8))
    smb_detail_refresh = float(config.get("smb_detail_refresh_seconds", 60))
    nodes_cfg = config.get("nodes", [])

    nodes: list[NodeStatus] = []
    if isinstance(nodes_cfg, list):
        for entry in nodes_cfg:
            if isinstance(entry, dict):
                nodes.append(probe_node(entry, timeout, smb_detail_refresh))

    host = socket.gethostname()
    temp = read_cpu_temp_c()
    mem = read_mem_used_pct()
    cpu_usage_pct = read_cpu_usage_pct()
    tailscale_ip = read_tailscale_ip()
    battery_pct = read_pisugar_battery_pct()
    wlan0_signal_dbm, wlan0_signal_pct = read_wlan_signal("wlan0")
    gps = read_gps_status()

    return Snapshot(
        ts=time.time(),
        hostname=host,
        cpu_temp=temp,
        cpu_usage_pct=cpu_usage_pct,
        mem_used_pct=mem,
        tailscale_ip=tailscale_ip,
        battery_pct=battery_pct,
        wlan0_signal_dbm=wlan0_signal_dbm,
        wlan0_signal_pct=wlan0_signal_pct,
        gps=gps,
        network=collect_network_status(config),
        nodes=nodes,
        raspyjack=collect_raspyjack(config),
        angryoxide=collect_angryoxide(config),
    )


def draw_vertical_gradient(surface: pygame.Surface, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    width, height = surface.get_size()
    if height <= 1:
        surface.fill(top)
        return
    for y in range(height):
        t = y / float(height - 1)
        color = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        pygame.draw.line(surface, color, (0, y), (width, y))


def draw_glow(surface: pygame.Surface, x: int, y: int, radius: int, color: tuple[int, int, int], alpha: int) -> None:
    glow = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    pygame.draw.circle(glow, (*color, alpha), (radius, radius), radius)
    surface.blit(glow, (x - radius, y - radius))


def status_color(theme: Theme, status: str) -> tuple[int, int, int]:
    if status == "online":
        return theme.ok
    if status == "degraded":
        return theme.warn
    if status == "offline":
        return theme.bad
    return theme.dim_text


def fmt_latency(latency: float | None) -> str:
    return "n/a" if latency is None else f"{latency:.1f}ms"


def fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


class DashboardApp:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = ensure_config(config_path)

        if pygame.vernum < (2, 0, 0):
            raise RuntimeError("PyGame >= 2.0.0 is required")

        self.preview_mode = env_flag("LAUNCHER_PREVIEW", False)
        self.preview_out = Path(os.environ.get("LAUNCHER_PREVIEW_OUT", "./preview_frames")).expanduser()
        self.preview_seconds = env_float("LAUNCHER_PREVIEW_SECONDS", 5.0, 1.0, 60.0)
        self.preview_frame_idx = 0
        self.preview_started_at = time.monotonic()
        self.preview_next_page_at = self.preview_started_at + 1.3
        self.preview_out.mkdir(parents=True, exist_ok=True)

        # Stability-first default for Pi nodes: keep the steady-state UI static.
        self.anim_enabled = env_flag("LAUNCHER_ANIM_ENABLE", False)
        self.target_fps = env_int("LAUNCHER_FPS", 24, 8, 60)
        self.effect_scanlines = env_flag("LAUNCHER_EFFECT_SCANLINES", False)
        self.effect_vignette = env_flag("LAUNCHER_EFFECT_VIGNETTE", False)
        self.effect_noise = env_flag("LAUNCHER_EFFECT_NOISE", False)
        self.theme_combo_enabled = env_flag("LAUNCHER_THEME_COMBO", True)
        self.idle_redraw_seconds = env_float(
            "LAUNCHER_IDLE_REDRAW_SECONDS",
            float(self.config.get("idle_redraw_seconds", 2.0)),
            0.5,
            30.0,
        )
        self._last_nav_key = None
        self._last_nav_ts = 0.0

        os.putenv("SDL_VIDEODRIVER", "dummy")
        pygame.display.init()
        pygame.font.init()

        self.display = None
        self.input_backend: WaveshareInput | None = None
        self.hardware_backend = "preview"
        if not self.preview_mode:
            hw_cfg = self.config.get("hardware", {}) if isinstance(self.config.get("hardware"), dict) else {}
            input_cfg = self.config.get("input", {}) if isinstance(self.config.get("input"), dict) else {}
            requested_backend = clean_text(hw_cfg.get("backend", "auto"), 24).lower() or "auto"

            if requested_backend in ("auto", "waveshare", "waveshare_1in3", "st7789"):
                if st7789 is not None:
                    self.display = WaveshareDisplay(
                        spi_port=int(hw_cfg.get("spi_port", 0)),
                        spi_cs=int(hw_cfg.get("spi_cs", 0)),
                        dc_pin=int(hw_cfg.get("dc_pin", 25)),
                        rst_pin=int(hw_cfg.get("rst_pin", 27)),
                        backlight_pin=int(hw_cfg.get("backlight_pin", 24)),
                        rotation=int(hw_cfg.get("rotation", 90)),
                        invert=bool(hw_cfg.get("invert", True)),
                        spi_speed_hz=int(hw_cfg.get("spi_speed_hz", 24000000)),
                    )
                    self.input_backend = WaveshareInput(
                        pins=input_cfg.get("pins", {}),
                        debounce_seconds=float(input_cfg.get("debounce_seconds", 0.10)),
                    )
                    self.input_backend.init()
                    self.hardware_backend = "waveshare"

            if self.display is None:
                if DisplayHATMini is None:
                    raise RuntimeError("No supported display backend available")
                use_backlight_pwm = bool(self.config.get("backlight_pwm", False))
                self.display = DisplayHATMini(None, backlight_pwm=use_backlight_pwm)
                try:
                    bl = float(self.config.get("backlight_level", 1.0))
                except Exception:
                    bl = 1.0
                bl = max(0.0, min(1.0, bl))
                if use_backlight_pwm:
                    self.display.set_backlight(bl)
                else:
                    self.display.set_backlight(1.0 if bl >= 0.5 else 0.0)
                self.hardware_backend = "displayhatmini"
            width = int(getattr(self.display, "width", getattr(self.display, "WIDTH", 240)))
            height = int(getattr(self.display, "height", getattr(self.display, "HEIGHT", 240)))
        else:
            width = env_int("LAUNCHER_PREVIEW_WIDTH", 240, 200, 800)
            height = env_int("LAUNCHER_PREVIEW_HEIGHT", 240, 160, 600)

        self.screen = pygame.Surface((width, height))

        self.text = TextRenderer()
        self.font_small = self._font(14)
        self.font_body = self._font(16)
        self.font_title = self._font(22)
        self.font_big = self._font(30)
        self.font_huge = self._font(34)

        self.theme_name = load_theme_name_from_env()
        self.theme: Theme = THEMES[self.theme_name]
        self.glow_cache = GlowCache()
        self.panel_style = PanelStyle()
        self.foxhunt = FoxhuntController(
            self.config.get("foxhunt", {}) if isinstance(self.config.get("foxhunt"), dict) else {},
            status_cb=self._set_status,
            redraw_cb=self._request_redraw,
            iface_choices_cb=self._wireless_attack_interface_labels,
            set_iface_cb=self._set_foxhunt_iface,
            reset_iface_cb=self._reset_external_wifi,
        )
        self.wifite = WifitePrepController(
            self.config.get("wifite", {}) if isinstance(self.config.get("wifite"), dict) else {},
            status_cb=self._set_status,
            redraw_cb=self._request_redraw,
            iface_choices_cb=self._wireless_attack_interface_labels,
            set_iface_cb=self._set_wifite_iface,
            reset_iface_cb=self._reset_external_wifi,
        )
        self.ao_menu = AngryOxideMenuController(
            self.config.get("angryoxide", {}) if isinstance(self.config.get("angryoxide"), dict) else {},
            status_cb=self._set_status,
            redraw_cb=self._request_redraw,
            launch_cb=self._angryoxide_start_menu_launch,
            stop_cb=self._angryoxide_stop,
            gpsd_cb=self._angryoxide_gpsd_endpoint,
            toggle_log_cb=self._toggle_angryoxide_log_view,
            iface_choices_cb=self._wireless_attack_interface_labels,
            set_iface_cb=self._set_angryoxide_iface,
            reset_iface_cb=self._reset_external_wifi,
        )
        self.effects = HudEffects(
            self.screen.get_size(),
            self.theme,
            self.glow_cache,
            scanlines_enabled=self.effect_scanlines,
            vignette_enabled=self.effect_vignette,
            noise_enabled=self.effect_noise,
        )
        self.transition = PageTransition(duration_s=0.24, enabled=self.anim_enabled)

        self.running = True
        self.refresh_event = threading.Event()
        self.redraw_event = threading.Event()
        self.stop_event = threading.Event()
        self.data_lock = threading.Lock()
        self.frame_lock = threading.Lock()

        self.page_idx = 0
        self.cursor_idx: dict[str, int] = {}
        self.status_note = "Starting..."
        self.status_note_expires = 0.0
        self.snapshot = collect_snapshot(self.config)
        try:
            hp = int(self.config.get("history_points", 180))
        except Exception:
            hp = 180
        self.history_points = max(30, min(600, hp))
        self.telemetry_history: deque[dict[str, Any]] = deque(maxlen=self.history_points)
        self.node_latency_history: dict[str, deque[dict[str, Any]]] = {}
        self._append_history_snapshot(self.snapshot)
        self.pages = self._build_pages(self.snapshot)
        self.startup_fade_start: float | None = None
        self.startup_fade_duration = 0.75

        self.page_scroll: dict[str, int] = {}
        self.page_line_counts: dict[str, int] = {}
        self.page_line_visible: dict[str, int] = {}
        self.angryoxide_log_view = False
        self.network_ops_menu_open = False
        self.network_ops_menu_state = "actions"
        self.network_ops_iface_target = ""
        self.last_external_monitor_enforce_at = 0.0
        self.suspended_app_id: str | None = None
        self.suspended_started_at: float = 0.0
        self.remote_action_lock = threading.Lock()
        self.remote_action_queue: deque[tuple[str, str]] = deque()
        self.remote_httpd: ThreadingHTTPServer | None = None
        self.remote_thread: threading.Thread | None = None
        self._pending_confirm_action: str | None = None
        self._pending_confirm_until: float = 0.0
        self.last_draw_at = 0.0

        if self.display is not None and self.hardware_backend == "displayhatmini":
            self.button_map = {
                self.display.BUTTON_A: pygame.K_a,
                self.display.BUTTON_B: pygame.K_b,
                self.display.BUTTON_X: pygame.K_x,
                self.display.BUTTON_Y: pygame.K_y,
            }
        else:
            self.button_map = {}
        self.button_state = {pin: False for pin in self.button_map}
        self.button_last_ts = {pin: 0.0 for pin in self.button_map}
        self.button_debounce_s = 0.09
        local_buttons_enabled = self.config.get("local_buttons_enabled", False)
        if isinstance(local_buttons_enabled, str):
            local_buttons_enabled = local_buttons_enabled.strip().lower() in ("1", "true", "yes", "on")
        self.local_buttons_enabled = (self.display is not None) and bool(local_buttons_enabled)
        self.interrupt_buttons_enabled = (
            self.local_buttons_enabled
            and self.hardware_backend == "displayhatmini"
            and (os.environ.get("DHM_DASH_BUTTON_IRQ", "0") == "1")
        )
        if not self.local_buttons_enabled:
            self.status_note = "Local GPIO buttons disabled (conflict-safe mode)"
        if self.interrupt_buttons_enabled:
            try:
                if self.display is not None:
                    self.display.on_button_pressed(self._button_callback)
            except Exception:
                self.interrupt_buttons_enabled = False
                self.status_note = "Button IRQ unavailable; using polling"

        self.worker = threading.Thread(target=self._refresh_loop, daemon=True)
        self.worker.start()
        self.foxhunt_worker = threading.Thread(target=self._foxhunt_loop, daemon=True)
        self.foxhunt_worker.start()
        self.wifite_worker = threading.Thread(target=self._wifite_loop, daemon=True)
        self.wifite_worker.start()
        self.ao_menu_worker = threading.Thread(target=self._angryoxide_menu_loop, daemon=True)
        self.ao_menu_worker.start()
        if not self.preview_mode:
            self._start_remote_server()

        signal.signal(signal.SIGINT, self._on_exit)
        signal.signal(signal.SIGTERM, self._on_exit)

        if not self.preview_mode:
            self._play_startup_splash()
            self.startup_fade_start = None
        self._request_redraw()

    def _font(self, size: int) -> pygame.font.Font:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        for path in candidates:
            if os.path.exists(path):
                return pygame.font.Font(path, size)
        return pygame.font.SysFont(None, size)

    def _blit_text(
        self,
        text: str,
        size: int,
        color: tuple[int, int, int],
        pos: tuple[int, int],
        target: pygame.Surface | None = None,
    ) -> pygame.Rect:
        surf = target if target is not None else self.screen
        return self.text.blit(surf, text, size, color, pos)

    def _cycle_theme(self) -> None:
        self.theme_name = next_theme_name(self.theme_name)
        self.theme = THEMES[self.theme_name]
        self.effects.set_theme(self.theme)
        self._set_status(f"Theme: {self.theme.name}", 3.0)

    def _request_redraw(self) -> None:
        self.redraw_event.set()

    def _maybe_theme_combo(self, key: int) -> bool:
        if not self.theme_combo_enabled:
            return False
        if key not in (pygame.K_a, pygame.K_b):
            return False
        now = time.monotonic()
        if self._last_nav_key is None:
            self._last_nav_key = key
            self._last_nav_ts = now
            return False
        if (now - self._last_nav_ts) <= 0.23 and self._last_nav_key != key:
            self._cycle_theme()
            self._last_nav_key = None
            self._last_nav_ts = 0.0
            return True
        self._last_nav_key = key
        self._last_nav_ts = now
        return False

    def _set_page_index(self, new_idx: int, direction: int = 1) -> None:
        if not self.pages:
            self.page_idx = 0
            return
        size = len(self.pages)
        old_idx = self.page_idx
        target = new_idx % size
        if target == old_idx:
            return
        if self.pages[old_idx] != "networkops":
            self.network_ops_menu_open = False
        elif self.pages[target] != "networkops":
            self.network_ops_menu_open = False
        old_frame = self.screen.copy()
        self.page_idx = target
        if not self.anim_enabled:
            self._request_redraw()
            return
        with self.data_lock:
            snap = self.snapshot
            page = self._current_page()
        next_frame = pygame.Surface(self.screen.get_size())
        self._render_page_to(next_frame, page, snap, include_overlays=True, include_fade=False)
        self.transition.start(old_frame, next_frame, direction=direction)
        self._request_redraw()

    def _build_pages(self, snapshot: Snapshot) -> list[str]:
        pages = ["overview", "gps", "networkops"]
        pages.append("foxhunt")
        pages.append("wifite")
        pages.append("raspyjack")
        pages.append("angryoxide")
        return pages

    def _append_history_snapshot(self, snap: Snapshot) -> None:
        online = 0
        degraded = 0
        offline = 0
        for n in snap.nodes:
            if n.status == "online":
                online += 1
            elif n.status == "degraded":
                degraded += 1
            else:
                offline += 1

        self.telemetry_history.append(
            {
                "ts": int(snap.ts),
                "cpu_temp_c": snap.cpu_temp,
                "cpu_usage_pct": snap.cpu_usage_pct,
                "mem_used_pct": snap.mem_used_pct,
                "battery_pct": snap.battery_pct,
                "wlan0_signal_pct": snap.wlan0_signal_pct,
                "gps_mode": snap.gps.mode,
                "gps_satellites_used": snap.gps.satellites_used,
                "gps_satellites_visible": snap.gps.satellites_visible,
                "nodes_online": online,
                "nodes_degraded": degraded,
                "nodes_offline": offline,
                "ao_running": bool(snap.angryoxide.running),
            }
        )

        alive_names: set[str] = set()
        for node in snap.nodes:
            name = clean_text(node.name, 64)
            if not name:
                continue
            alive_names.add(name)
            hist = self.node_latency_history.get(name)
            if hist is None:
                hist = deque(maxlen=self.history_points)
                self.node_latency_history[name] = hist
            hist.append(
                {
                    "ts": int(snap.ts),
                    "latency_ms": node.latency_ms,
                    "status": node.status,
                }
            )

        stale = [name for name in self.node_latency_history if name not in alive_names]
        for name in stale:
            self.node_latency_history.pop(name, None)

    def _history_payload_locked(self) -> dict[str, Any]:
        local = [
            {
                "t": int(item.get("ts", 0)),
                "cpu": item.get("cpu_temp_c"),
                "cpu_usage": item.get("cpu_usage_pct"),
                "ram": item.get("mem_used_pct"),
                "battery": item.get("battery_pct"),
                "wifi": item.get("wlan0_signal_pct"),
                "gps_mode": item.get("gps_mode"),
                "gps_satellites_used": item.get("gps_satellites_used"),
                "gps_satellites_visible": item.get("gps_satellites_visible"),
                "offline": item.get("nodes_offline"),
                "degraded": item.get("nodes_degraded"),
                "ao_running": bool(item.get("ao_running", False)),
            }
            for item in self.telemetry_history
        ]
        nodes = {
            name: [
                {
                    "t": int(entry.get("ts", 0)),
                    "latency_ms": entry.get("latency_ms"),
                    "status": entry.get("status"),
                }
                for entry in hist
            ]
            for name, hist in self.node_latency_history.items()
        }
        return {"points": self.history_points, "local": local, "nodes": nodes}

    def _on_exit(self, *_: Any) -> None:
        self.running = False
        self.stop_event.set()

    def _foxhunt_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self.data_lock:
                    gps = self.snapshot.gps
                self.foxhunt.tick(gps)
            except Exception as e:
                self._set_status(f"FoxHunt loop: {clean_text(e, 48)}", 5.0)
            if self.stop_event.wait(timeout=1.0):
                break

    def _wifite_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.wifite.tick()
            except Exception as e:
                self._set_status(f"Wifite loop: {clean_text(e, 48)}", 5.0)
            if self.stop_event.wait(timeout=1.0):
                break

    def _angryoxide_menu_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self.data_lock:
                    running = self.snapshot.angryoxide.running
                self.ao_menu.tick()
                if running and self.ao_menu.state == "scan":
                    self._request_redraw()
            except Exception as e:
                self._set_status(f"AO menu loop: {clean_text(e, 48)}", 5.0)
            if self.stop_event.wait(timeout=1.0):
                break

    def _button_callback(self, pin: int) -> None:
        if pin not in self.button_map:
            return
        if self.display.read_button(pin):
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=self.button_map[pin]))

    def _poll_buttons(self) -> None:
        if self.input_backend is not None:
            self.input_backend.poll()
            return
        now = time.monotonic()
        for pin, key in self.button_map.items():
            try:
                pressed = bool(self.display.read_button(pin))
            except Exception:
                continue

            was_pressed = self.button_state[pin]
            if pressed != was_pressed:
                self.button_state[pin] = pressed
                if pressed and (now - self.button_last_ts[pin]) >= self.button_debounce_s:
                    self.button_last_ts[pin] = now
                    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))

    def _refresh_loop(self) -> None:
        refresh_seconds = max(2, int(self.config.get("refresh_seconds", 30)))
        while not self.stop_event.is_set():
            try:
                fresh = collect_snapshot(self.config)
                with self.data_lock:
                    self.snapshot = fresh
                    self._append_history_snapshot(fresh)
                    self.pages = self._build_pages(fresh)
                    self.page_idx = min(self.page_idx, len(self.pages) - 1)
                if time.monotonic() >= self.status_note_expires:
                    self.status_note = "Data refreshed"
                self._sync_led()
                self._request_redraw()
            except Exception as e:
                self._set_status(f"Refresh error: {e}", 6.0)
                self.display.set_led(1.0, 0.1, 0.1)

            if self.refresh_event.wait(timeout=refresh_seconds):
                self.refresh_event.clear()
                self._set_status("Manual refresh", 3.0)

    def _sync_led(self) -> None:
        if self.display is None:
            return
        with self.data_lock:
            nodes = self.snapshot.nodes

        if not nodes:
            self.display.set_led(0.15, 0.35, 0.8)
            return

        statuses = {n.status for n in nodes}
        if statuses == {"online"}:
            self.display.set_led(0.0, 0.85, 0.2)
        elif "offline" in statuses:
            self.display.set_led(0.9, 0.1, 0.1)
        else:
            self.display.set_led(0.95, 0.55, 0.0)

    def _update_display(self) -> None:
        if self.preview_mode or self.display is None:
            out_path = self.preview_out / f"frame_{self.preview_frame_idx:04d}.png"
            pygame.image.save(self.screen, str(out_path))
            self.preview_frame_idx += 1
            return
        if self.hardware_backend == "waveshare":
            self.display.display_surface(self.screen)
            return
        self.display.st7789.set_window()
        pixelbytes = pygame.transform.rotate(self.screen, 180).convert(16, 0).get_buffer()
        swapped = bytearray(pixelbytes)
        swapped[0::2], swapped[1::2] = swapped[1::2], swapped[0::2]
        for i in range(0, len(swapped), 4096):
            self.display.st7789.data(swapped[i : i + 4096])

    def _load_splash(self) -> pygame.Surface | None:
        splash_path = Path(__file__).resolve().with_name(SPLASH_FILENAME)
        if not splash_path.exists():
            return None
        try:
            raw = pygame.image.load(str(splash_path))
            if pygame.display.get_surface() is not None:
                if raw.get_flags() & pygame.SRCALPHA:
                    raw = raw.convert_alpha()
                else:
                    raw = raw.convert()
            else:
                raw = raw.copy()
        except Exception:
            return None

        sw, sh = self.screen.get_size()
        iw, ih = raw.get_size()
        if iw <= 0 or ih <= 0:
            return None

        scale = min(sw / float(iw), sh / float(ih))
        nw = max(1, int(iw * scale))
        nh = max(1, int(ih * scale))
        scaled = pygame.transform.smoothscale(raw, (nw, nh))

        canvas = pygame.Surface((sw, sh), pygame.SRCALPHA)
        canvas.fill((0, 0, 0, 255))
        canvas.blit(scaled, ((sw - nw) // 2, (sh - nh) // 2))
        return canvas

    def _draw_splash_frame(self, splash: pygame.Surface, alpha: int) -> None:
        self.screen.fill((0, 0, 0))
        frame = splash.copy()
        frame.set_alpha(clamp(alpha, 0, 255))
        self.screen.blit(frame, (0, 0))
        self._update_display()

    def _handle_startup_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.running = False

    def _play_startup_splash(self) -> None:
        splash = self._load_splash()
        self.screen.fill((0, 0, 0))
        self._update_display()
        if splash is None:
            return

        clock = pygame.time.Clock()

        def phase(duration: float, alpha_from: int, alpha_to: int) -> bool:
            start = time.monotonic()
            while self.running:
                t = (time.monotonic() - start) / max(duration, 0.001)
                if t >= 1.0:
                    break
                alpha = int(alpha_from + ((alpha_to - alpha_from) * t))
                self._draw_splash_frame(splash, alpha)
                self._handle_startup_events()
                clock.tick(30)
            self._draw_splash_frame(splash, alpha_to)
            self._handle_startup_events()
            return self.running

        if not phase(0.9, 0, 255):
            return

        hold_start = time.monotonic()
        while self.running and (time.monotonic() - hold_start) < 2.0:
            self._draw_splash_frame(splash, 255)
            self._handle_startup_events()
            clock.tick(30)

        if not self.running:
            return

        phase(0.9, 255, 0)
        self.screen.fill((0, 0, 0))
        self._update_display()

    def _queue_remote_action(self, action: str, source: str = "remote") -> tuple[bool, str]:
        raw = clean_text(action, 32).lower()
        aliases = {
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "a": "page_prev",
            "prev": "page_prev",
            "page_prev": "page_prev",
            "b": "page_next",
            "next": "page_next",
            "page_next": "page_next",
            "x": "context_x",
            "context_x": "context_x",
            "y": "context_y",
            "context_y": "context_y",
            "refresh": "refresh",
            "fh_menu": "fh_menu",
            "fh_scan": "fh_scan",
            "fh_lock": "fh_lock",
            "fh_mark": "fh_mark",
            "fh_save": "fh_save",
            "fh_resume": "fh_resume",
            "fh_end": "fh_end",
            "fh_clear": "fh_clear",
            "fh_last": "fh_last",
            "fh_target": "fh_target",
            "wf_select_network": "wf_select_network",
            "wf_lock_target": "wf_lock_target",
            "wf_clear_target": "wf_clear_target",
            "ao_toggle": "ao_toggle",
            "ao_scan_all": "ao_scan_all",
            "ao_select_network": "ao_select_network",
            "ao_lock_target": "ao_lock_target",
            "ao_view": "ao_view",
            "ao_monitor_on": "ao_monitor_on",
            "ao_monitor_off": "ao_monitor_off",
            "ao_profile_standard": "ao_profile_standard",
            "ao_profile_passive": "ao_profile_passive",
            "ao_profile_autoexit": "ao_profile_autoexit",
            "overview": "goto_overview",
            "gps": "goto_gps",
            "networkops": "goto_networkops",
            "foxhunt": "goto_foxhunt",
            "wifite": "goto_wifite",
            "raspyjack": "goto_raspyjack",
            "angryoxide": "goto_angryoxide",
            "rj_core_start": "rj_core_start",
            "rj_core_stop": "rj_core_stop",
            "rj_core_restart": "rj_core_restart",
            "rj_device_start": "rj_device_start",
            "rj_device_stop": "rj_device_stop",
            "rj_device_restart": "rj_device_restart",
            "rj_web_start": "rj_web_start",
            "rj_web_stop": "rj_web_stop",
            "rj_web_restart": "rj_web_restart",
            "rj_all_start": "rj_all_start",
            "rj_all_stop": "rj_all_stop",
            "rj_all_restart": "rj_all_restart",
            "rj_runbook_up": "rj_runbook_up",
            "rj_runbook_recover": "rj_runbook_recover",
            "rj_runbook_web_bounce": "rj_runbook_web_bounce",
            "net_refresh": "net_refresh",
            "net_reconnect_wlan0": "net_reconnect_wlan0",
            "net_restart_networkmanager": "net_restart_networkmanager",
            "net_restart_tailscale": "net_restart_tailscale",
            "net_iface_menu": "net_iface_menu",
            "net_reboot": "net_reboot",
        }
        if raw.startswith("net_monitor_") or raw.startswith("net_managed_"):
            mapped = raw
        else:
            mapped = aliases.get(raw)
        if not mapped:
            return (False, f"Unknown action: {raw}")

        with self.remote_action_lock:
            self.remote_action_queue.append((mapped, clean_text(source, 64)))
        return (True, mapped)

    def _drain_remote_actions(self) -> None:
        actions: list[tuple[str, str]] = []
        with self.remote_action_lock:
            while self.remote_action_queue:
                actions.append(self.remote_action_queue.popleft())
        for action, source in actions[:16]:
            self._apply_remote_action(action, source=source)

    def _log_remote_action(self, action: str, source: str, result: str) -> None:
        try:
            REMOTE_ACTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REMOTE_ACTION_LOG_PATH, "a", encoding="utf-8") as f:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{ts}] source={clean_text(source,64)} action={clean_text(action,32)} result={clean_text(result,64)}\n")
        except Exception:
            pass

    def _confirm_high_impact_action(self, action: str) -> bool:
        if action not in HIGH_IMPACT_ACTIONS:
            return True
        now = time.monotonic()
        if self._pending_confirm_action == action and now <= self._pending_confirm_until:
            self._pending_confirm_action = None
            self._pending_confirm_until = 0.0
            return True
        self._pending_confirm_action = action
        self._pending_confirm_until = now + 2.5
        self._set_status(f"Confirm {action}: repeat within 2.5s", 3.2)
        return False

    def _apply_remote_action(self, action: str, source: str = "remote") -> None:
        if not self._confirm_high_impact_action(action):
            self._log_remote_action(action, source, "confirm_required")
            return
        if action == "up":
            self._move_selection(self._current_page(), -1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "down":
            self._move_selection(self._current_page(), +1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "left":
            page = self._current_page()
            if page == "foxhunt":
                if self.foxhunt.block_page_cycle() and self.foxhunt.back():
                    self._log_remote_action(action, source, "ok")
                    return
            if page == "wifite":
                if self.wifite.block_page_cycle() and self.wifite.back():
                    self._log_remote_action(action, source, "ok")
                    return
            if page == "networkops":
                if self.network_ops_menu_open:
                    if self.network_ops_menu_state == "mode":
                        self.network_ops_menu_state = "iface"
                        self.network_ops_iface_target = ""
                        self.cursor_idx["networkops"] = 0
                        self._request_redraw()
                        self._log_remote_action(action, source, "ok")
                        return
                    if self.network_ops_menu_state == "iface":
                        self.network_ops_menu_state = "actions"
                        self.cursor_idx["networkops"] = 0
                        self._request_redraw()
                        self._log_remote_action(action, source, "ok")
                        return
                    if self._network_ops_close_menu():
                        self._log_remote_action(action, source, "ok")
                        return
            if page == "angryoxide" and self.ao_menu.back():
                self._log_remote_action(action, source, "ok")
                return
            self._set_page_index(self.page_idx - 1, direction=-1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "right":
            page = self._current_page()
            if page == "foxhunt" and self.foxhunt.block_page_cycle():
                self._log_remote_action(action, source, "blocked")
                return
            if page == "wifite" and self.wifite.block_page_cycle():
                self._log_remote_action(action, source, "blocked")
                return
            if page == "networkops" and self.network_ops_menu_open:
                self._log_remote_action(action, source, "blocked")
                return
            if page == "angryoxide" and self.ao_menu.block_page_cycle():
                self._log_remote_action(action, source, "blocked")
                return
            self._set_page_index(self.page_idx + 1, direction=1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "page_prev":
            self._set_page_index(self.page_idx - 1, direction=-1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "page_next":
            self._set_page_index(self.page_idx + 1, direction=1)
            self._log_remote_action(action, source, "ok")
            return
        if action == "context_x":
            self._handle_context_action(self._current_page(), pygame.K_x)
            self._log_remote_action(action, source, "ok")
            return
        if action == "context_y":
            self._handle_context_action(self._current_page(), pygame.K_y)
            self._log_remote_action(action, source, "ok")
            return
        if action == "refresh":
            self._set_status("Refreshing...", 3.0)
            self.refresh_event.set()
            self._log_remote_action(action, source, "ok")
            return
        if action.startswith("fh_"):
            ok = self.foxhunt.remote_action(action)
            self._log_remote_action(action, source, "ok" if ok else "invalid_state")
            return
        if action.startswith("wf_"):
            ok = self.wifite.remote_action(action)
            self._log_remote_action(action, source, "ok" if ok else "invalid_state")
            return
        if action.startswith("net_"):
            self._network_ops_remote_action(action)
            self._log_remote_action(action, source, "ok")
            return
        if action in ("ao_scan_all", "ao_select_network", "ao_lock_target"):
            with self.data_lock:
                running = self.snapshot.angryoxide.running
            ok = self.ao_menu.remote_action(action, running=running)
            self._log_remote_action(action, source, "ok" if ok else "invalid_state")
            return
        if action == "ao_toggle":
            with self.data_lock:
                running = self.snapshot.angryoxide.running
            if running:
                self._angryoxide_stop()
            else:
                self._angryoxide_start()
            self._log_remote_action(action, source, "ok")
            return
        if action == "ao_view":
            self.angryoxide_log_view = not self.angryoxide_log_view
            self.page_scroll["angryoxide"] = 0
            self._set_status(
                "AngryOxide log view" if self.angryoxide_log_view else "AngryOxide summary",
                4.0,
            )
            self._log_remote_action(action, source, "ok")
            return
        if action == "ao_monitor_on":
            self._angryoxide_monitor_control(enable=True)
            self._log_remote_action(action, source, "ok")
            return
        if action == "ao_monitor_off":
            self._angryoxide_monitor_control(enable=False)
            self._log_remote_action(action, source, "ok")
            return
        if action.startswith("ao_profile_"):
            self._angryoxide_runbook(action)
            self._log_remote_action(action, source, "ok")
            return
        if action.startswith("rj_"):
            if action.startswith("rj_runbook_"):
                self._raspyjack_runbook(action)
            else:
                self._raspyjack_remote_control(action)
            self._log_remote_action(action, source, "ok")
            return

        target = {
            "goto_overview": "overview",
            "goto_gps": "gps",
            "goto_networkops": "networkops",
            "goto_foxhunt": "foxhunt",
            "goto_wifite": "wifite",
            "goto_raspyjack": "raspyjack",
            "goto_angryoxide": "angryoxide",
        }.get(action)
        if target:
            try:
                idx = self.pages.index(target)
                direction = 1 if idx >= self.page_idx else -1
                self._set_page_index(idx, direction=direction)
                self._log_remote_action(action, source, "ok")
            except ValueError:
                self._log_remote_action(action, source, "invalid_target")

    def _network_ops_cfg(self) -> dict[str, Any]:
        cfg = self.config.get("network_ops", {})
        return cfg if isinstance(cfg, dict) else {}

    def _network_ops_actions(self) -> list[str]:
        return [
            "Refresh",
            "Reconnect wlan0",
            "Reset wlan1",
            "Restart NetworkMgr",
            "Restart Tailscale",
            "Interface Modes",
            "Shutdown Device",
            "Restart Device",
        ]

    def _external_wifi_reset_command(self, iface: str) -> str:
        target = clean_text(iface, 24)
        if not target:
            return ""
        module = ""
        for item in self._wireless_attack_adapters():
            if item.iface != target:
                continue
            driver = clean_text(item.driver, 24).lower()
            if driver in {"88xxau", "rtl88xxau"}:
                module = "88XXau"
            break
        quoted = shlex.quote(target)
        parts: list[str] = [
            f"pkill -f 'airodump-ng.*{quoted}' || true",
            f"pkill -f 'iw dev {quoted} scan ap-force' || true",
        ]
        if module:
            parts.extend(
                [
                    f"modprobe -r {module} || true",
                    "sleep 2",
                    f"modprobe {module}",
                    "sleep 3",
                ]
            )
        parts.extend(
            [
                f"ip link set {quoted} down || true",
                f"nmcli device set {quoted} managed no || true",
                f"iw dev {quoted} set type monitor || true",
                f"ip link set {quoted} up",
            ]
        )
        return "; ".join(parts)

    def _reset_external_wifi(self, iface: str, reason: str = "manual") -> bool:
        target = clean_text(iface, 24)
        if not target:
            return False
        self._set_status(f"Resetting {target}...", 5.0)
        cmd = self._external_wifi_reset_command(target)
        if not cmd:
            self._set_status(f"{target} reset unsupported", 6.0)
            return False
        rc, _, err = run_shell(cmd, timeout=30.0)
        if rc != 0:
            self._set_status(f"{target} reset failed: {clean_text(err, 32)}", 8.0)
            return False
        if reason == "manual":
            self._set_status(f"{target} reset ok", 5.0)
        else:
            self._set_status(f"{target} radio reset, retrying scan", 5.0)
        self.refresh_event.set()
        self.redraw_event.set()
        return True

    def _network_ops_external_iface_items(self) -> list[str]:
        items: list[str] = []
        for item in self._wireless_attack_adapters():
            label = item.label if item.label and item.label != "WiFi" else item.driver
            items.append(clean_text(f"{item.iface} - {label}", 28))
        return items

    def _network_ops_mode_items(self) -> list[str]:
        iface = clean_text(self.network_ops_iface_target, 24)
        if not iface:
            return ["Back"]
        return ["Monitor", "Managed", "Back"]

    def _network_ops_menu_title(self) -> str:
        if self.network_ops_menu_state == "iface":
            return "Interfaces"
        if self.network_ops_menu_state == "mode":
            iface = clean_text(self.network_ops_iface_target, 12)
            return iface or "Mode"
        return "Actions"

    def _network_ops_menu_items(self) -> list[str]:
        if self.network_ops_menu_state == "iface":
            items = self._network_ops_external_iface_items()
            return items + ["Back"] if items else ["Back"]
        if self.network_ops_menu_state == "mode":
            return self._network_ops_mode_items()
        return self._network_ops_actions()

    def _network_ops_selected_action(self) -> str:
        actions = self._network_ops_menu_items()
        idx = self.cursor_idx.get("networkops", 0) % len(actions)
        return actions[idx]

    def _network_ops_open_menu(self) -> None:
        self.network_ops_menu_state = "actions"
        self.network_ops_iface_target = ""
        self.network_ops_menu_open = True
        self._request_redraw()

    def _network_ops_close_menu(self) -> bool:
        if not self.network_ops_menu_open:
            return False
        self.network_ops_menu_open = False
        self.network_ops_menu_state = "actions"
        self.network_ops_iface_target = ""
        self._request_redraw()
        return True

    def _network_ops_command(self, action: str) -> str:
        cfg = self._network_ops_cfg()
        primary_iface = clean_text(cfg.get("primary_iface", "wlan0"), 24) or "wlan0"
        wifi_profile = clean_text(cfg.get("wifi_profile", ""), 64)
        nm_service = clean_text(cfg.get("networkmanager_service", "NetworkManager.service"), 64)
        tailscale_service = clean_text(cfg.get("tailscale_service", "tailscaled.service"), 64)
        if action == "Reconnect wlan0":
            if wifi_profile:
                return (
                    f"nmcli device disconnect {shlex.quote(primary_iface)}; "
                    f"sleep 1; "
                    f"nmcli connection up {shlex.quote(wifi_profile)} ifname {shlex.quote(primary_iface)}"
                )
            return (
                f"nmcli device disconnect {shlex.quote(primary_iface)}; "
                f"sleep 1; "
                f"nmcli device connect {shlex.quote(primary_iface)}"
            )
        if action == "Restart NetworkMgr":
            return f"systemctl restart {shlex.quote(nm_service)}"
        if action == "Restart Tailscale":
            return f"systemctl restart {shlex.quote(tailscale_service)}"
        if action == "Shutdown Device":
            return clean_text(cfg.get("shutdown_cmd", "/usr/bin/systemctl poweroff -i"), 160) or "/usr/bin/systemctl poweroff -i"
        if action == "Restart Device":
            return clean_text(cfg.get("reboot_cmd", "/usr/bin/systemctl reboot -i"), 160) or "/usr/bin/systemctl reboot -i"
        return ""

    def _network_ops_iface_mode_command(self, iface: str, mode: str) -> str:
        safe_iface = clean_text(iface, 24)
        if not safe_iface:
            return ""
        quoted = shlex.quote(safe_iface)
        if mode == "Monitor":
            return (
                f"ip link set {quoted} down; "
                f"nmcli device set {quoted} managed no || true; "
                f"iw dev {quoted} set type monitor; "
                f"ip link set {quoted} up"
            )
        if mode == "Managed":
            return (
                f"ip link set {quoted} down; "
                f"iw dev {quoted} set type managed; "
                f"ip link set {quoted} up; "
                f"nmcli device set {quoted} managed yes || true"
            )
        return ""

    def _run_network_ops_action(self, action: str) -> None:
        if self.network_ops_menu_state == "actions" and action == "Interface Modes":
            items = self._network_ops_external_iface_items()
            if not items:
                self._set_status("No external adapters", 5.0)
                return
            self.network_ops_menu_state = "iface"
            self.network_ops_iface_target = ""
            self.cursor_idx["networkops"] = 0
            self._request_redraw()
            return
        if self.network_ops_menu_state == "iface":
            if action == "Back":
                self.network_ops_menu_state = "actions"
                self.cursor_idx["networkops"] = 0
                self._request_redraw()
                return
            iface = clean_text(action.split()[0], 24)
            if not iface:
                self._set_status("Invalid adapter", 5.0)
                return
            self.network_ops_iface_target = iface
            self.network_ops_menu_state = "mode"
            self.cursor_idx["networkops"] = 0
            self._request_redraw()
            return
        if self.network_ops_menu_state == "mode":
            if action == "Back":
                self.network_ops_menu_state = "iface"
                self.network_ops_iface_target = ""
                self.cursor_idx["networkops"] = 0
                self._request_redraw()
                return
            iface = clean_text(self.network_ops_iface_target, 24)
            cmd = self._network_ops_iface_mode_command(iface, action)
            if not cmd:
                self._set_status(f"network: unsupported {action}", 6.0)
                return
            rc, _, err = run_shell(cmd, timeout=18.0)
            if rc != 0:
                self._set_status(f"{iface} {action} failed: {clean_text(err, 28)}", 8.0)
                return
            self._set_status(f"{iface} {action.lower()} ok", 5.0)
            self.network_ops_menu_state = "actions"
            self.network_ops_iface_target = ""
            self.network_ops_menu_open = False
            self.refresh_event.set()
            self._request_redraw()
            return
        if action == "Refresh":
            self._set_status("Refreshing...", 3.0)
            self.refresh_event.set()
            return
        if action == "Reset wlan1":
            cfg = self._network_ops_cfg()
            monitor_iface = clean_text(cfg.get("monitor_iface", "wlan1"), 24) or "wlan1"
            self._reset_external_wifi(monitor_iface, reason="manual")
            self.network_ops_menu_state = "actions"
            self.network_ops_iface_target = ""
            self.network_ops_menu_open = False
            self._request_redraw()
            return
        cmd = self._network_ops_command(action)
        if not cmd:
            self._set_status(f"network: unsupported {action}", 6.0)
            return
        if action in {"Shutdown Device", "Restart Device"}:
            self._set_status("Shutdown requested" if action == "Shutdown Device" else "Reboot requested", 4.0)
            try:
                subprocess.Popen(
                    ["/usr/bin/env", "bash", "-lc", cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as exc:
                self._set_status(f"Reboot failed: {clean_text(exc, 32)}", 8.0)
            return
        rc, _, err = run_shell(cmd, timeout=18.0)
        if rc != 0:
            self._set_status(f"{action} failed: {clean_text(err, 36)}", 8.0)
            return
        self._set_status(f"{action} ok", 5.0)
        self.refresh_event.set()

    def _network_ops_remote_action(self, action: str) -> None:
        if action.startswith("net_monitor_"):
            iface = clean_text(action.removeprefix("net_monitor_"), 24)
            if iface:
                self.network_ops_menu_state = "mode"
                self.network_ops_iface_target = iface
                self._run_network_ops_action("Monitor")
            return
        if action.startswith("net_managed_"):
            iface = clean_text(action.removeprefix("net_managed_"), 24)
            if iface:
                self.network_ops_menu_state = "mode"
                self.network_ops_iface_target = iface
                self._run_network_ops_action("Managed")
            return
        mapping = {
            "net_refresh": "Refresh",
            "net_reconnect_wlan0": "Reconnect wlan0",
            "net_reset_wlan1": "Reset wlan1",
            "net_restart_networkmanager": "Restart NetworkMgr",
            "net_restart_tailscale": "Restart Tailscale",
            "net_iface_menu": "Interface Modes",
            "net_shutdown": "Shutdown Device",
            "net_reboot": "Restart Device",
        }
        target = mapping.get(action)
        if target:
            self._run_network_ops_action(target)

    def _raspyjack_service_names(self) -> tuple[str, str, str]:
        rj_cfg = self.config.get("raspyjack", {}) if isinstance(self.config.get("raspyjack"), dict) else {}
        names = rj_cfg.get("service_names", [])
        if not isinstance(names, list):
            names = []
        cleaned = [clean_text(x, 96) for x in names if clean_text(x, 96)]
        defaults = ["raspyjack.service", "raspyjack-device.service", "raspyjack-webui.service"]
        merged = (cleaned + defaults)[:3]
        while len(merged) < 3:
            merged.append(defaults[len(merged)])
        return (merged[0], merged[1], merged[2])

    def _raspyjack_script_control(self, field: str, label: str) -> bool:
        cfg = self._managed_app_cfg("raspyjack")
        command = clean_text(cfg.get(field, ""), 256)
        if not command:
            self._set_status(f"{label}: no {field}", 6.0)
            return False
        if field == "start_cmd" and bool(cfg.get("takes_over_display", False)):
            unit = f"launcher-rj-handoff-{int(time.time())}"
            if self._run_detached_managed_app_cmd(command, unit):
                self._set_status(f"{label} scheduled", 6.0)
                return True
            self._set_status(f"{label} failed: detached start failed", 8.0)
            return False
        rc, _, err = run_shell(command, timeout=30.0)
        if rc != 0:
            info = clean_text(err, 44) or "failed"
            self._set_status(f"{label} failed: {info}", 8.0)
            return False
        self._set_status(f"{label} ok", 6.0)
        return True

    def _service_control(self, service: str, action: str, label: str) -> bool:
        svc = clean_text(service, 96)
        act = clean_text(action, 16).lower()
        if not svc or act not in ("start", "stop", "restart"):
            self._set_status("invalid service action", 5.0)
            return False

        cmd = f"systemctl {act} {shlex.quote(svc)}"
        rc, _, err = run_shell(cmd, timeout=15.0)
        if rc != 0:
            info = clean_text(err, 44) or "failed"
            self._set_status(f"{label} {act} failed: {info}", 8.0)
            return False
        self._set_status(f"{label} {act} ok", 6.0)
        return True

    def _raspyjack_remote_control(self, action: str) -> None:
        core, device, webui = self._raspyjack_service_names()
        if action == "rj_all_start":
            if self._raspyjack_script_control("start_cmd", "RJ start script"):
                self.refresh_event.set()
            return
        if action == "rj_all_stop":
            if self._raspyjack_script_control("stop_cmd", "RJ stop script"):
                self.refresh_event.set()
            return
        if action == "rj_all_restart":
            if self._raspyjack_script_control("stop_cmd", "RJ stop script"):
                time.sleep(0.8)
                self._raspyjack_script_control("start_cmd", "RJ start script")
                self.refresh_event.set()
            return
        mapping: dict[str, tuple[tuple[str, str, str], ...]] = {
            "rj_core_start": ((core, "start", "RJ core"),),
            "rj_core_stop": ((core, "stop", "RJ core"),),
            "rj_core_restart": ((core, "restart", "RJ core"),),
            "rj_device_start": ((device, "start", "RJ device"),),
            "rj_device_stop": ((device, "stop", "RJ device"),),
            "rj_device_restart": ((device, "restart", "RJ device"),),
            "rj_web_start": ((webui, "start", "RJ web"),),
            "rj_web_stop": ((webui, "stop", "RJ web"),),
            "rj_web_restart": ((webui, "restart", "RJ web"),),
        }

        ops = mapping.get(action)
        if not ops:
            self._set_status("Unknown RaspyJack action", 5.0)
            return

        ok_count = 0
        for service, verb, label in ops:
            if self._service_control(service, verb, label):
                ok_count += 1
            else:
                break

        if len(ops) > 1 and ok_count == len(ops):
            self._set_status("RaspyJack action complete", 6.0)
        self.refresh_event.set()

    def _raspyjack_runbook(self, action: str) -> None:
        mapping = {
            "rj_runbook_up": "rj_all_start",
            "rj_runbook_recover": "rj_all_restart",
            "rj_runbook_web_bounce": "rj_web_restart",
        }
        mapped = mapping.get(action, "")
        if not mapped:
            self._set_status("Unknown RaspyJack runbook", 5.0)
            return
        self._raspyjack_remote_control(mapped)

    def _append_command_flags(self, base_cmd: str, flags: list[str]) -> str:
        cmd = clean_text(base_cmd, 1024)
        if not cmd:
            return ""
        try:
            parts = shlex.split(cmd)
        except Exception:
            return cmd
        if not parts:
            return ""
        existing = set(parts)
        for item in flags:
            raw = clean_text(item, 128)
            if not raw:
                continue
            try:
                tokens = shlex.split(raw)
            except Exception:
                tokens = [raw]
            for token in tokens:
                if token in existing:
                    continue
                parts.append(token)
                existing.add(token)
        return " ".join(shlex.quote(p) for p in parts)

    def _append_command_pairs(self, base_cmd: str, pairs: list[tuple[str, str]]) -> str:
        cmd = clean_text(base_cmd, 1024)
        if not cmd:
            return ""
        try:
            parts = shlex.split(cmd)
        except Exception:
            return cmd
        if not parts:
            return ""
        existing = set(parts)
        for flag, value in pairs:
            clean_flag = clean_text(flag, 64)
            clean_value = clean_text(value, 256)
            if not clean_flag or not clean_value:
                continue
            if clean_flag in existing:
                continue
            parts.extend([clean_flag, clean_value])
            existing.add(clean_flag)
        return " ".join(shlex.quote(p) for p in parts)

    def _angryoxide_profile_flags(self, ao_cfg: dict[str, Any], profile: str) -> list[str]:
        key = clean_text(profile, 24).lower()
        if not key or key in ("default", "standard"):
            default_flags: list[str] = []
        elif key == "passive":
            default_flags = [
                "--notransmit",
                "--disable-deauth",
                "--disable-pmkid",
                "--disable-anon",
                "--disable-csa",
                "--disable-disassoc",
                "--disable-roguem2",
                "--notar",
            ]
        elif key == "autoexit":
            default_flags = [
                "--autoexit",
                "--notransmit",
                "--disable-deauth",
                "--disable-pmkid",
                "--disable-anon",
                "--disable-csa",
                "--disable-disassoc",
                "--disable-roguem2",
                "--notar",
            ]
        else:
            default_flags = []

        run_profiles = ao_cfg.get("run_profiles", {})
        if not isinstance(run_profiles, dict):
            return default_flags
        custom = run_profiles.get(key)
        if not isinstance(custom, list):
            return default_flags
        cleaned = [clean_text(x, 128) for x in custom if clean_text(x, 128)]
        return cleaned if cleaned else default_flags

    def _angryoxide_command_for_profile(
        self,
        ao_cfg: dict[str, Any],
        profile: str,
        target_bssid: str | None = None,
        gpsd_endpoint: str | None = None,
    ) -> str:
        run_cmd = resolve_command(build_angryoxide_command(ao_cfg))
        if not run_cmd:
            return ""
        key = clean_text(profile, 24).lower()
        flags: list[str] = []
        if key and key != "default":
            flags = self._angryoxide_profile_flags(ao_cfg, key)
        if "--headless" not in run_cmd:
            flags = ["--headless"] + flags
        run_cmd = self._append_command_flags(run_cmd, flags)
        pairs: list[tuple[str, str]] = []
        if target_bssid:
            pairs.append(("--target-entry", target_bssid))
        if gpsd_endpoint:
            pairs.append(("--gpsd", gpsd_endpoint))
        return self._append_command_pairs(run_cmd, pairs)

    def _angryoxide_runbook(self, action: str) -> None:
        profile_map = {
            "ao_profile_standard": "standard",
            "ao_profile_passive": "passive",
            "ao_profile_autoexit": "autoexit",
        }
        profile = profile_map.get(action, "")
        if not profile:
            self._set_status("Unknown AO runbook", 5.0)
            return
        with self.data_lock:
            running = self.snapshot.angryoxide.running
        if running:
            self._angryoxide_stop()
            time.sleep(0.3)
        self._angryoxide_start(profile=profile)

    def _angryoxide_gpsd_endpoint(self) -> str | None:
        host = "127.0.0.1"
        port = 2947
        try:
            with socket.create_connection((host, port), timeout=0.7):
                return f"{host}:{port}"
        except Exception:
            return None

    def _toggle_angryoxide_log_view(self) -> None:
        self.angryoxide_log_view = not self.angryoxide_log_view
        self.page_scroll["angryoxide"] = 0
        self._set_status(
            "AngryOxide log view" if self.angryoxide_log_view else "AngryOxide summary",
            4.0,
        )

    def _angryoxide_start_menu_launch(self, profile: str, target_bssid: str | None, gpsd_endpoint: str | None) -> None:
        with self.data_lock:
            running = self.snapshot.angryoxide.running
        if running:
            self._angryoxide_stop()
            time.sleep(0.3)
        self._angryoxide_start(profile=profile, target_bssid=target_bssid, gpsd_endpoint=gpsd_endpoint)

    def _angryoxide_monitor_control(self, enable: bool) -> None:
        ao_cfg = self.config.get("angryoxide", {}) if isinstance(self.config.get("angryoxide"), dict) else {}
        iface = clean_text(ao_cfg.get("interface", "wlan1"), 24)
        if not enable:
            self._set_status("monitor disable skipped; leaving monitor mode active", 6.0)
            self.refresh_event.set()
            return
        key = "start_monitor_cmd" if enable else "stop_monitor_cmd"
        script = resolve_command(str(ao_cfg.get(key, "")))
        if not script:
            self._set_status(f"missing {key}", 8.0)
            return
        rc, _, err = run_shell(script, timeout=20.0)
        if rc != 0:
            self._set_status(f"monitor cmd failed: {clean_text(err, 42)}", 8.0)
            return

        if iface:
            if enable:
                if not wait_for_iface_mode(iface, "monitor", timeout_seconds=4.0):
                    if not force_iface_mode(iface, "monitor"):
                        self._set_status(f"{iface} monitor mode failed", 8.0)
                        return
            else:
                wait_for_iface_mode(iface, "managed", timeout_seconds=2.0)

        self._set_status("monitor enabled" if enable else "monitor disabled", 6.0)
        self.refresh_event.set()

    def _remote_status_payload(self) -> dict[str, Any]:
        with self.data_lock:
            snap = self.snapshot
            pages = list(self.pages)
            page_idx = self.page_idx
            page = pages[page_idx] if pages else "overview"
            history = self._history_payload_locked()
        foxhunt = self.foxhunt.status_payload()
        wifite = self.wifite.status_payload()
        with self.data_lock:
            ao_running = bool(self.snapshot.angryoxide.running)
        ao_menu = self.ao_menu.status_payload(ao_running)

        return {
            "ok": True,
            "ts": int(time.time()),
            "theme": self.theme.name,
            "hostname": snap.hostname,
            "status_note": clean_text(self.status_note, 120),
            "page": page,
            "page_index": page_idx,
            "pages": pages,
            "angryoxide_log_view": self.angryoxide_log_view,
            "history": history,
            "runbooks": {
                "ao_profiles": ["standard", "passive", "autoexit"],
                "raspyjack": ["up", "recover", "web_bounce"],
            },
            "foxhunt": {
                **foxhunt,
            },
            "wifite": {
                **wifite,
            },
            "local": {
                "tailscale_ip": snap.tailscale_ip,
                "cpu_temp_c": snap.cpu_temp,
                "cpu_usage_pct": snap.cpu_usage_pct,
                "mem_used_pct": snap.mem_used_pct,
                "ram_used_pct": snap.mem_used_pct,
                "battery_pct": snap.battery_pct,
                "wlan0_signal_dbm": snap.wlan0_signal_dbm,
                "wlan0_signal_pct": snap.wlan0_signal_pct,
                "gps": {
                    "available": snap.gps.available,
                    "device": snap.gps.device,
                    "mode": snap.gps.mode,
                    "fix_label": snap.gps.fix_label,
                    "latitude": snap.gps.latitude,
                    "longitude": snap.gps.longitude,
                    "altitude_m": snap.gps.altitude_m,
                    "speed_kph": snap.gps.speed_kph,
                    "track_deg": snap.gps.track_deg,
                    "satellites_used": snap.gps.satellites_used,
                    "satellites_visible": snap.gps.satellites_visible,
                    "time_utc": snap.gps.time_utc,
                    "hdop": snap.gps.hdop,
                    "pdop": snap.gps.pdop,
                    "vdop": snap.gps.vdop,
                    "epx_m": snap.gps.epx_m,
                    "epy_m": snap.gps.epy_m,
                    "epv_m": snap.gps.epv_m,
                    "climb_kph": snap.gps.climb_kph,
                    "satellites": snap.gps.satellites,
                },
            },
            "network": {
                "primary_iface": snap.network.primary_iface,
                "primary_ip": snap.network.primary_ip,
                "primary_mode": snap.network.primary_mode,
                "primary_operstate": snap.network.primary_operstate,
                "primary_profile": snap.network.primary_profile,
                "monitor_iface": snap.network.monitor_iface,
                "monitor_ip": snap.network.monitor_ip,
                "monitor_mode": snap.network.monitor_mode,
                "monitor_operstate": snap.network.monitor_operstate,
                "default_route_iface": snap.network.default_route_iface,
                "default_route_gw": snap.network.default_route_gw,
                "networkmanager_state": snap.network.networkmanager_state,
                "tailscale_state": snap.network.tailscale_state,
                "wireless_adapters": [
                    {
                        "iface": item.iface,
                        "role": item.role,
                        "label": item.label,
                        "driver": item.driver,
                        "mode": item.mode,
                        "operstate": item.operstate,
                        "ip": item.ip,
                        "signal_dbm": item.signal_dbm,
                        "signal_pct": item.signal_pct,
                        "active_profile": item.active_profile,
                        "is_onboard": item.is_onboard,
                    }
                    for item in snap.network.wireless_adapters
                ],
            },
            "nodes": [
                {
                    "name": n.name,
                    "host": n.host,
                    "status": n.status,
                    "latency_ms": n.latency_ms,
                    "ports_open": n.ports_open,
                    "health_text": n.health_text,
                    "smb_text": n.smb_text,
                    "smb_files": n.smb_file_count,
                    "smb_used_bytes": n.smb_used_bytes,
                    "smb_total_bytes": n.smb_total_bytes,
                }
                for n in snap.nodes
            ],
            "raspyjack": {
                "core": snap.raspyjack.core_state,
                "device": snap.raspyjack.device_state,
                "webui": snap.raspyjack.webui_state,
                "nmap_running": snap.raspyjack.nmap_running,
                "responder_running": snap.raspyjack.responder_running,
                "ettercap_running": snap.raspyjack.ettercap_running,
                "primary_iface": snap.raspyjack.primary_iface,
                "primary_ip": snap.raspyjack.primary_ip,
                "monitor_iface": snap.raspyjack.monitor_iface,
                "monitor_mode": snap.raspyjack.monitor_mode,
                "loot_files": snap.raspyjack.loot_files,
                "loot_size_bytes": snap.raspyjack.loot_size_bytes,
                "latest_nmap": {
                    "name": snap.raspyjack.latest_nmap_name,
                    "path": snap.raspyjack.latest_nmap_path,
                    "size_bytes": snap.raspyjack.latest_nmap_size_bytes,
                    "age_seconds": snap.raspyjack.latest_nmap_age_seconds,
                    "mtime": snap.raspyjack.latest_nmap_mtime,
                    "stable": snap.raspyjack.latest_nmap_stable,
                    "preview_lines": list(snap.raspyjack.latest_nmap_preview_lines[-10:]),
                },
            },
            "angryoxide": {
                "running": snap.angryoxide.running,
                "pid": snap.angryoxide.pid,
                "iface": snap.angryoxide.iface,
                "iface_mode": snap.angryoxide.iface_mode,
                "runtime_seconds": snap.angryoxide.runtime_seconds,
                "hc22000_files": snap.angryoxide.hc22000_files,
                "pcap_files": snap.angryoxide.pcap_files,
                "kismet_files": snap.angryoxide.kismet_files,
                "tar_files": snap.angryoxide.tar_files,
                "fourway_hashes": snap.angryoxide.fourway_hashes,
                "pmkid_hashes": snap.angryoxide.pmkid_hashes,
                "m1_sent_events": snap.angryoxide.m1_sent_events,
                "rogue_m2_events": snap.angryoxide.rogue_m2_events,
                "discovered_ssids": snap.angryoxide.discovered_ssids,
                "whitelist_count": snap.angryoxide.whitelist_count,
                "sockets_rx": snap.angryoxide.sockets_rx,
                "sockets_tx": snap.angryoxide.sockets_tx,
                "oui_records": snap.angryoxide.oui_records,
                "panic_count": snap.angryoxide.panic_count,
                "log_size_bytes": snap.angryoxide.log_size_bytes,
                "log_age_seconds": snap.angryoxide.log_age_seconds,
                "log_lines": list(snap.angryoxide.log_lines[-5:]),
                "result_files": snap.angryoxide.result_files,
                "result_size_bytes": snap.angryoxide.result_size_bytes,
                "menu": ao_menu,
                "gpsd_endpoint": self._angryoxide_gpsd_endpoint(),
            },
        }

    def _remote_frame_png(self) -> bytes:
        with self.frame_lock:
            surface = self.screen.copy()
        raw = pygame.image.tostring(surface, "RGB")
        image = Image.frombytes("RGB", surface.get_size(), raw)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def _remote_html(self) -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portable Ops Remote</title>
  <style>
    :root{
      --bg:#071017;
      --panel:#0b1722;
      --panel-hi:#102233;
      --line:#2cf5aa;
      --line-soft:#1d8d66;
      --text:#dcfff0;
      --muted:#8bc7b0;
      --ok:#85ffd0;
      --warn:#ffd57c;
      --bad:#ff8da0;
      --shadow:0 18px 44px rgba(0,0,0,.42);
      --radius:16px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      min-height:100vh;
      color:var(--text);
      font-family:"Exo 2","Rajdhani","Segoe UI",system-ui,sans-serif;
      background:
        radial-gradient(900px 500px at 5% -10%, rgba(44,245,170,.12), transparent 60%),
        radial-gradient(880px 520px at 100% 0%, rgba(77,160,255,.08), transparent 60%),
        linear-gradient(180deg, #08111a 0%, #04090f 100%);
      letter-spacing:.02em;
    }
    .app{
      max-width:1400px;
      margin:0 auto;
      padding:18px;
    }
    .shell{
      display:grid;
      grid-template-columns:260px minmax(0,1fr) 320px;
      gap:16px;
      align-items:start;
    }
    .panel{
      background:linear-gradient(180deg, rgba(16,31,44,.94), rgba(9,19,29,.94));
      border:1px solid rgba(44,245,170,.14);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      overflow:hidden;
    }
    .sidebar{
      padding:18px 14px;
      position:sticky;
      top:18px;
    }
    .brand{
      padding:6px 8px 14px;
      border-bottom:1px solid rgba(44,245,170,.12);
      margin-bottom:14px;
    }
    .brand h1{
      margin:0;
      font-size:1.6rem;
      line-height:1.05;
    }
    .brand p{
      margin:6px 0 0;
      color:var(--muted);
      font-size:.9rem;
    }
    .nav{
      display:grid;
      gap:8px;
      margin-bottom:16px;
    }
    .nav-btn,.action-btn,.softkey,.dpad{
      appearance:none;
      border:1px solid rgba(44,245,170,.16);
      border-radius:12px;
      background:linear-gradient(180deg, rgba(19,40,54,.96), rgba(10,22,31,.96));
      color:var(--text);
      font:inherit;
      cursor:pointer;
      transition:border-color .16s ease, transform .16s ease, background .16s ease;
    }
    .nav-btn{
      text-align:left;
      padding:12px 14px;
      font-weight:700;
      color:#d5fff0;
    }
    .nav-btn small{
      display:block;
      margin-top:4px;
      color:var(--muted);
      font-weight:500;
      font-size:.8rem;
    }
    .nav-btn.active{
      background:linear-gradient(180deg, rgba(29,71,66,.98), rgba(13,38,39,.98));
      border-color:rgba(88,255,196,.44);
    }
    .nav-btn:hover,.action-btn:hover,.softkey:hover,.dpad:hover{
      transform:translateY(-1px);
      border-color:rgba(88,255,196,.42);
    }
    .side-group{
      margin-top:14px;
      padding-top:14px;
      border-top:1px solid rgba(44,245,170,.10);
    }
    .side-title{
      margin:0 0 10px;
      color:var(--muted);
      text-transform:uppercase;
      letter-spacing:.08em;
      font-size:.75rem;
    }
    .side-actions{
      display:grid;
      gap:8px;
    }
    .action-btn{
      padding:11px 12px;
      text-align:left;
      font-size:.92rem;
    }
    .token{
      width:100%;
      margin-top:10px;
      border:1px solid rgba(44,245,170,.14);
      border-radius:12px;
      background:rgba(6,14,21,.92);
      color:var(--text);
      padding:11px 12px;
      outline:none;
      font:inherit;
    }
    .content{
      display:grid;
      gap:16px;
    }
    .hero{
      padding:18px 20px;
      display:flex;
      justify-content:space-between;
      align-items:flex-start;
      gap:16px;
    }
    .hero h2{
      margin:0;
      font-size:1.8rem;
    }
    .hero p{
      margin:6px 0 0;
      color:var(--muted);
    }
    .hero-meta{
      text-align:right;
      font-family:"JetBrains Mono","Consolas",monospace;
      font-size:.88rem;
      color:var(--muted);
    }
    .clock{
      display:inline-block;
      margin-top:8px;
      padding:8px 10px;
      border-radius:10px;
      background:rgba(8,18,26,.9);
      color:#9fffd9;
    }
    .page{
      display:none;
      padding:18px;
    }
    .page.active{display:block}
    .section-title{
      margin:0 0 12px;
      font-size:1rem;
      text-transform:uppercase;
      letter-spacing:.08em;
      color:#c4ffe7;
    }
    .summary-grid,.telemetry-grid,.stat-grid{
      display:grid;
      grid-template-columns:repeat(2,minmax(0,1fr));
      gap:12px;
    }
    .telemetry-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
    .stat{
      border:1px solid rgba(44,245,170,.12);
      border-radius:14px;
      background:rgba(9,21,30,.82);
      padding:12px 13px;
    }
    .stat .label{
      display:block;
      color:var(--muted);
      font-size:.75rem;
      text-transform:uppercase;
      letter-spacing:.08em;
      margin-bottom:5px;
    }
    .stat .value{
      font-size:1rem;
      font-weight:700;
      word-break:break-word;
    }
    .mono{
      font-family:"JetBrains Mono","Consolas",monospace;
      font-size:.84rem;
      line-height:1.45;
    }
    .blurb{
      margin-top:12px;
      padding:12px 13px;
      border-radius:14px;
      background:rgba(9,21,30,.82);
      border:1px solid rgba(44,245,170,.10);
      color:#c7f5e0;
    }
    .split{
      display:grid;
      grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);
      gap:16px;
      align-items:start;
    }
    .nodes{
      display:grid;
      gap:10px;
    }
    .node{
      border:1px solid rgba(44,245,170,.12);
      border-radius:14px;
      background:rgba(9,21,30,.82);
      padding:12px 13px;
    }
    .node-top{
      display:flex;
      justify-content:space-between;
      gap:8px;
      margin-bottom:4px;
    }
    .node-host{color:var(--muted);font-size:.84rem}
    .signal{
      height:6px;
      margin-top:8px;
      border-radius:999px;
      background:rgba(255,255,255,.08);
      overflow:hidden;
    }
    .signal > span{
      display:block;
      height:100%;
      background:linear-gradient(90deg, #53f0b4, #67d7ff);
    }
    .controls{
      display:grid;
      grid-template-columns:repeat(2,minmax(0,1fr));
      gap:10px;
      margin-top:14px;
    }
    pre.log{
      margin:12px 0 0;
      padding:12px;
      border-radius:14px;
      background:#071018;
      border:1px solid rgba(44,245,170,.10);
      color:#c8f8e1;
      max-height:220px;
      overflow:auto;
      font-family:"JetBrains Mono","Consolas",monospace;
      font-size:.8rem;
      line-height:1.42;
      white-space:pre-wrap;
    }
    .online{color:var(--ok)}
    .degraded{color:var(--warn)}
    .offline{color:var(--bad)}
    .rail{
      display:grid;
      gap:16px;
      position:sticky;
      top:18px;
    }
    .status-card,.device-shell{
      padding:18px;
    }
    .status-list{
      display:grid;
      gap:10px;
      margin-top:12px;
    }
    .status-row{
      display:flex;
      justify-content:space-between;
      gap:12px;
      color:#d6ffee;
      font-size:.92rem;
    }
    .status-row span:last-child{
      color:var(--muted);
      text-align:right;
    }
    .virtual-device{
      width:100%;
      background:linear-gradient(180deg, #d9ddd9, #bfc5c0);
      border-radius:18px;
      padding:16px 16px 20px;
      box-shadow:0 24px 46px rgba(0,0,0,.35), inset 0 0 0 1px rgba(255,255,255,.36);
      color:#26312b;
    }
    .device-top{
      display:flex;
      justify-content:space-between;
      align-items:center;
      margin-bottom:10px;
      font-family:"JetBrains Mono","Consolas",monospace;
      font-size:.72rem;
      letter-spacing:.08em;
      text-transform:uppercase;
    }
    .device-brand{font-weight:800}
    .device-status{opacity:.72}
    .device-screen{
      aspect-ratio:1/1;
      width:100%;
      border-radius:10px;
      border:5px solid #27ef45;
      background:linear-gradient(180deg, #071008 0%, #020503 100%);
      box-shadow:inset 0 0 30px rgba(34,255,84,.15);
      padding:10px;
      overflow:hidden;
      display:flex;
      align-items:center;
      justify-content:center;
    }
    .device-frame{
      display:block;
      width:100%;
      height:100%;
      object-fit:contain;
      image-rendering:pixelated;
      image-rendering:crisp-edges;
      border-radius:4px;
      background:#000;
    }
    .device-controls{
      display:grid;
      grid-template-columns:56px 56px 56px 1fr;
      gap:10px 12px;
      align-items:center;
      margin-top:16px;
    }
    .dpad{
      width:56px;
      height:36px;
      border-radius:7px;
      background:linear-gradient(180deg, #353e39, #191e1b);
      color:#8cff93;
    }
    .dpad.up{grid-column:2}
    .dpad.left{grid-column:1;grid-row:2}
    .dpad.ok{grid-column:2;grid-row:2}
    .dpad.right{grid-column:3;grid-row:2}
    .dpad.down{grid-column:2;grid-row:3}
    .softkeys{
      grid-column:4;
      grid-row:1 / span 3;
      display:grid;
      gap:10px;
    }
    .softkey{
      padding:8px 10px;
      border-radius:7px;
      background:linear-gradient(180deg, #35413a, #1a211d);
      color:#9bffa1;
      text-transform:uppercase;
      font-family:"JetBrains Mono","Consolas",monospace;
      font-size:.76rem;
    }
    @media (max-width: 1180px){
      .shell{grid-template-columns:240px minmax(0,1fr)}
      .rail{grid-column:1 / -1;position:static;grid-template-columns:repeat(2,minmax(0,1fr))}
    }
    @media (max-width: 860px){
      .shell{grid-template-columns:1fr}
      .sidebar,.rail{position:static}
      .telemetry-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
      .split{grid-template-columns:1fr}
    }
    @media (max-width: 620px){
      .app{padding:10px}
      .telemetry-grid,.summary-grid,.stat-grid,.controls,.rail{grid-template-columns:1fr}
      .hero{flex-direction:column}
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="shell">
      <aside class="panel sidebar">
        <div class="brand">
          <h1>Portable Ops Remote</h1>
          <p id="meta">loading telemetry...</p>
        </div>
        <div class="nav">
          <button class="nav-btn" data-nav="overview" onclick="act('overview')">Overview<small>mission summary</small></button>
          <button class="nav-btn" data-nav="gps" onclick="act('gps')">GPS<small>satellite lock</small></button>
          <button class="nav-btn" data-nav="networkops" onclick="act('networkops')">Network Ops<small>interfaces and recovery</small></button>
          <button class="nav-btn" data-nav="foxhunt" onclick="act('foxhunt')">FoxHunt<small>recon and tracking</small></button>
          <button class="nav-btn" data-nav="wifite" onclick="act('wifite')">Wifite<small>passive target prep</small></button>
          <button class="nav-btn" data-nav="raspyjack" onclick="act('raspyjack')">RaspyJack<small>stack and loot</small></button>
          <button class="nav-btn" data-nav="angryoxide" onclick="act('angryoxide')">AngryOxide<small>packet ops</small></button>
        </div>
      </aside>

      <main class="content">
        <section class="panel hero">
          <div>
            <h2 id="heroTitle">Overview</h2>
            <p id="heroDesc">Live mission summary and device health.</p>
          </div>
          <div class="hero-meta">
            <div id="heroBadges">theme -- | page -- | status --</div>
            <div class="clock" id="clock">--:--:--</div>
          </div>
        </section>

        <section class="panel page active" data-view="overview">
          <h3 class="section-title">Overview</h3>
          <div class="telemetry-grid">
            <div class="stat"><span class="label">PiSugar Battery</span><div class="value" id="kpiBattery">--</div></div>
            <div class="stat"><span class="label">CPU Temp</span><div class="value" id="kpiCpu">--</div></div>
            <div class="stat"><span class="label">CPU Usage</span><div class="value" id="kpiLoad">--</div></div>
            <div class="stat"><span class="label">RAM Used</span><div class="value" id="kpiRam">--</div></div>
            <div class="stat"><span class="label">WiFi wlan0</span><div class="value" id="kpiWifi">--</div></div>
          </div>
          <div class="split" style="margin-top:16px;">
            <div>
              <div class="summary-grid">
                <div class="stat"><span class="label">Hostname</span><div class="value mono" id="ovHost">--</div></div>
                <div class="stat"><span class="label">Tailscale IP</span><div class="value mono" id="ovTs">--</div></div>
                <div class="stat"><span class="label">Battery</span><div class="value mono" id="ovBattery">--</div></div>
                <div class="stat"><span class="label">Status</span><div class="value mono" id="ovStatus">--</div></div>
              </div>
              <div class="blurb mono" id="local">loading...</div>
            </div>
            <div>
              <h3 class="section-title">Node Summary</h3>
              <div class="nodes" id="nodes"></div>
            </div>
          </div>
        </section>

        <section class="panel page" data-view="gps">
          <h3 class="section-title">GPS</h3>
          <div class="stat-grid">
            <div class="stat"><span class="label">Fix</span><div class="value mono" id="gpsFix">--</div></div>
            <div class="stat"><span class="label">Coordinates</span><div class="value mono" id="gpsCoords">--</div></div>
            <div class="stat"><span class="label">Satellites</span><div class="value mono" id="gpsSats">--</div></div>
            <div class="stat"><span class="label">Altitude</span><div class="value mono" id="gpsAlt">--</div></div>
            <div class="stat"><span class="label">Speed</span><div class="value mono" id="gpsSpeed">--</div></div>
            <div class="stat"><span class="label">Track</span><div class="value mono" id="gpsTrack">--</div></div>
            <div class="stat"><span class="label">HDOP / PDOP</span><div class="value mono" id="gpsDop">--</div></div>
            <div class="stat"><span class="label">Error Est.</span><div class="value mono" id="gpsErr">--</div></div>
          </div>
          <div class="blurb mono" id="gpsInfo">loading...</div>
          <pre class="log" id="gpsSatLog">(loading satellites)</pre>
        </section>

        <section class="panel page" data-view="networkops">
          <h3 class="section-title">Network Ops</h3>
          <div class="stat-grid">
            <div class="stat"><span class="label">Primary Interface</span><div class="value mono" id="netPrimary">--</div></div>
            <div class="stat"><span class="label">Monitor Interface</span><div class="value mono" id="netMonitor">--</div></div>
            <div class="stat"><span class="label">Route</span><div class="value mono" id="netRoute">--</div></div>
            <div class="stat"><span class="label">Services</span><div class="value mono" id="netServices">--</div></div>
          </div>
          <div class="blurb mono" id="networkops">loading...</div>
          <div class="controls">
            <button class="action-btn" onclick="act('net_refresh')">Refresh Data</button>
            <button class="action-btn" onclick="act('net_reconnect_wlan0')">Reconnect wlan0</button>
            <button class="action-btn" onclick="act('net_restart_networkmanager')">Restart NetworkManager</button>
            <button class="action-btn" onclick="act('net_restart_tailscale')">Restart Tailscale</button>
            <button class="action-btn" onclick="act('net_iface_menu')">Interface Modes</button>
            <button class="action-btn" onclick="act('net_shutdown')">Shutdown Device</button>
            <button class="action-btn" onclick="act('net_reboot')">Restart Device</button>
          </div>
          <div class="controls" id="netIfaceControls"></div>
        </section>

        <section class="panel page" data-view="foxhunt">
          <h3 class="section-title">FoxHunt</h3>
          <div class="blurb mono" id="foxhunt">loading...</div>
          <div class="controls">
            <button class="action-btn" onclick="act('fh_scan')">Start Scan</button>
            <button class="action-btn" onclick="act('fh_lock')">Lock Target</button>
            <button class="action-btn" onclick="act('fh_resume')">Start Hunt</button>
            <button class="action-btn" onclick="act('fh_mark')">Mark Point</button>
            <button class="action-btn" onclick="act('fh_save')">Save Session</button>
            <button class="action-btn" onclick="act('fh_end')">End Hunt</button>
            <button class="action-btn" onclick="act('fh_clear')">Clear Target</button>
            <button class="action-btn" onclick="act('fh_last')">Last Session</button>
          </div>
          <pre class="log" id="foxhuntlog">(loading)</pre>
        </section>

        <section class="panel page" data-view="wifite">
          <h3 class="section-title">Wifite</h3>
          <div class="blurb mono" id="wifite">loading...</div>
          <div class="controls">
            <button class="action-btn" onclick="act('wf_select_network')">Select Network</button>
            <button class="action-btn" onclick="act('wf_lock_target')">Set Target</button>
            <button class="action-btn" onclick="act('wf_clear_target')">Clear Target</button>
            <button class="action-btn" onclick="act('refresh')">Refresh Data</button>
          </div>
          <pre class="log" id="wifitelog">(loading)</pre>
        </section>

        <section class="panel page" data-view="raspyjack">
          <h3 class="section-title">RaspyJack</h3>
          <div class="blurb mono" id="rj">loading...</div>
          <div class="controls">
            <button class="action-btn" onclick="act('rj_runbook_up')">Stack Up</button>
            <button class="action-btn" onclick="act('rj_runbook_recover')">Recover Stack</button>
            <button class="action-btn" onclick="act('rj_runbook_web_bounce')">Bounce RJ Web</button>
            <button class="action-btn" onclick="act('refresh')">Refresh Data</button>
          </div>
          <pre class="log" id="rjnmap">(loading latest nmap)</pre>
        </section>

        <section class="panel page" data-view="angryoxide">
          <h3 class="section-title">AngryOxide</h3>
          <div class="blurb mono" id="ao">loading...</div>
          <div class="controls">
            <button class="action-btn" onclick="act('ao_scan_all')">Scan All</button>
            <button class="action-btn" onclick="act('ao_select_network')">Select Network</button>
            <button class="action-btn" onclick="act('ao_lock_target')">Lock Target</button>
            <button class="action-btn" onclick="act('ao_toggle')">Start/Stop AO</button>
            <button class="action-btn" onclick="act('ao_view')">Summary/Log</button>
            <button class="action-btn" onclick="act('ao_monitor_on')">Monitor On</button>
            <button class="action-btn" onclick="act('ao_monitor_off')">Monitor Off</button>
            <button class="action-btn" onclick="act('refresh')">Refresh Data</button>
          </div>
          <pre class="log" id="aolog">(loading)</pre>
        </section>
      </main>

      <aside class="rail">
        <section class="panel status-card">
          <h3 class="section-title">Launcher Status</h3>
          <div class="status-list">
            <div class="status-row"><span>Theme</span><span id="theme">--</span></div>
            <div class="status-row"><span>Page</span><span id="page">--</span></div>
            <div class="status-row"><span>Status</span><span id="note">--</span></div>
            <div class="status-row"><span>Battery</span><span id="statusBattery">--</span></div>
            <div class="status-row"><span>Hostname</span><span id="statusHost">--</span></div>
            <div class="status-row"><span>Tailscale</span><span id="statusTs">--</span></div>
          </div>
        </section>

        <section class="panel device-shell">
          <div class="virtual-device">
            <div class="device-top">
              <span class="device-brand">K.A.R.I Launcher</span>
              <span class="device-status" id="vdStatus">linked</span>
            </div>
            <div class="device-screen">
              <img class="device-frame" id="vdFrame" alt="K.A.R.I launcher display">
            </div>
            <div class="device-controls">
              <button class="dpad up" onclick="act('up')">▲</button>
              <button class="dpad left" onclick="act('left')">◀</button>
              <button class="dpad ok" onclick="act('context_x')">OK</button>
              <button class="dpad right" onclick="act('right')">▶</button>
              <button class="dpad down" onclick="act('down')">▼</button>
              <div class="softkeys">
                <button class="softkey" onclick="act('context_x')">Key1 / Act</button>
                <button class="softkey" onclick="act('context_y')">Key2 / Alt</button>
                <button class="softkey" onclick="act('overview')">Key3 / Home</button>
              </div>
            </div>
          </div>
        </section>
      </aside>
    </div>
  </div>
  <script>
    const views = Array.from(document.querySelectorAll(".page"));
    const navButtons = Array.from(document.querySelectorAll(".nav-btn"));
    const viewMeta = {
      overview: { title: "Overview", desc: "Live mission summary and device health." },
      gps: { title: "GPS", desc: "Satellite lock, fix quality, and receiver detail." },
      networkops: { title: "Network Ops", desc: "Interface state, services, and recovery actions." },
      foxhunt: { title: "FoxHunt", desc: "Wireless recon, target lock, and hunt state." },
      wifite: { title: "Wifite", desc: "Passive network selection and target prep." },
      raspyjack: { title: "RaspyJack", desc: "Stack state, loot, and latest nmap output." },
      angryoxide: { title: "AngryOxide", desc: "Packet capture workflow and live run status." }
    };

    function headers(){
      return {};
    }
    function esc(v){
      return String(v ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }
    function fmtBytes(v){
      if (v === null || v === undefined) return "n/a";
      let n = Number(v); const units = ["B","KB","MB","GB","TB"]; let i = 0;
      while (n >= 1024 && i < units.length - 1){ n /= 1024; i += 1; }
      return (i === 0 ? Math.round(n) : n.toFixed(1)) + units[i];
    }
    function spark(values){
      const chars = "▁▂▃▄▅▆▇█";
      const nums = (values || []).map((v) => Number(v));
      const clean = nums.filter((n) => Number.isFinite(n));
      if (!clean.length) return "n/a";
      let min = Math.min(...clean);
      let max = Math.max(...clean);
      if (max <= min) max = min + 1;
      return nums.map((n) => {
        if (!Number.isFinite(n)) return "·";
        const ratio = (n - min) / (max - min);
        const idx = Math.max(0, Math.min(chars.length - 1, Math.round(ratio * (chars.length - 1))));
        return chars[idx];
      }).join("");
    }
    function yn(v){ return v ? "yes" : "no"; }
    function toNum(v){
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    }
    function gpsFixText(gps){
      if (!gps || !gps.available) return "offline";
      return String(gps.fix_label || "n/a");
    }
    function gpsSatText(gps){
      if (!gps || (!gps.available && gps.satellites_visible === null && gps.satellites_used === null)) return "n/a";
      const used = gps.satellites_used ?? 0;
      const visible = gps.satellites_visible ?? "?";
      return `${used} / ${visible}`;
    }
    function gpsCoordText(gps){
      if (!gps || gps.latitude === null || gps.latitude === undefined || gps.longitude === null || gps.longitude === undefined){
        return "no fix";
      }
      return `${Number(gps.latitude).toFixed(5)}, ${Number(gps.longitude).toFixed(5)}`;
    }
    function gpsValue(v, suffix=""){
      if (v === null || v === undefined || v === "") return "n/a";
      return `${v}${suffix}`;
    }
    function renderVirtualDevice(d){
      document.getElementById("vdStatus").textContent = d.status_note || "linked";
      const frame = document.getElementById("vdFrame");
      frame.src = `/api/frame.png?ts=${Date.now()}`;
    }
    function latencyFill(lat){
      const n = toNum(lat);
      if (n === null) return 18;
      return Math.max(8, Math.min(100, 100 - (n * 1.4)));
    }
    function viewKey(page){
      const p = String(page || "");
      if (viewMeta[p]) return p;
      return "overview";
    }
    function setView(page){
      const key = viewKey(page);
      for (const el of views){
        el.classList.toggle("active", el.dataset.view === key);
      }
      for (const btn of navButtons){
        btn.classList.toggle("active", btn.dataset.nav === key);
      }
      const meta = viewMeta[key] || viewMeta.overview;
      document.getElementById("heroTitle").textContent = meta.title;
      document.getElementById("heroDesc").textContent = meta.desc;
    }
    async function act(action){
      try {
        const res = await fetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...headers() },
          body: JSON.stringify({ action })
        });
        const data = await res.json();
        if (!data.ok) alert(data.message || "action failed");
      } catch (e) {
        alert(String(e));
      }
      setTimeout(load, 220);
    }
    async function load(){
      document.getElementById("clock").textContent = new Date().toLocaleTimeString();
      try {
        const res = await fetch("/api/status", { headers: headers() });
        const d = await res.json();
        if (!d.ok) throw new Error(d.message || "status error");

        document.getElementById("meta").textContent = `${d.hostname} | tailscale ${d.local.tailscale_ip}`;
        document.getElementById("heroBadges").textContent = `theme ${d.theme} | page ${d.page} | status ${d.status_note || "ready"}`;
        document.getElementById("theme").textContent = d.theme;
        document.getElementById("page").textContent = d.page;
        document.getElementById("note").textContent = d.status_note || "ready";
        document.getElementById("statusBattery").textContent =
          (d.local.battery_pct === null || d.local.battery_pct === undefined) ? "n/a" : `${d.local.battery_pct}%`;
        document.getElementById("statusHost").textContent = d.hostname || "n/a";
        document.getElementById("statusTs").textContent = d.local.tailscale_ip || "n/a";

        document.getElementById("kpiBattery").textContent =
          (d.local.battery_pct === null || d.local.battery_pct === undefined) ? "n/a" : `${d.local.battery_pct} %`;
        document.getElementById("kpiCpu").textContent = `${d.local.cpu_temp_c ?? "n/a"} C`;
        document.getElementById("kpiLoad").textContent =
          (d.local.cpu_usage_pct === null || d.local.cpu_usage_pct === undefined) ? "n/a" : `${d.local.cpu_usage_pct} %`;
        document.getElementById("kpiRam").textContent = `${d.local.mem_used_pct ?? "n/a"} %`;
        if (d.local.wlan0_signal_pct !== null && d.local.wlan0_signal_pct !== undefined){
          document.getElementById("kpiWifi").textContent = `${d.local.wlan0_signal_pct}% (${d.local.wlan0_signal_dbm ?? "n/a"} dBm)`;
        } else if (d.local.wlan0_signal_dbm !== null && d.local.wlan0_signal_dbm !== undefined){
          document.getElementById("kpiWifi").textContent = `${d.local.wlan0_signal_dbm} dBm`;
        } else {
          document.getElementById("kpiWifi").textContent = "n/a";
        }
        document.getElementById("ovHost").textContent = String(d.hostname || "n/a");
        document.getElementById("ovTs").textContent = String(d.local.tailscale_ip || "n/a");
        document.getElementById("ovBattery").textContent =
          (d.local.battery_pct === null || d.local.battery_pct === undefined) ? "n/a" : `${d.local.battery_pct}%`;
        document.getElementById("ovStatus").textContent = String(d.status_note || "ready");
        document.getElementById("local").innerHTML =
          `host ${esc(d.hostname)} | tailscale ${esc(d.local.tailscale_ip)}<br>` +
          `battery ${esc(d.local.battery_pct ?? "n/a")}% | cpu ${esc(d.local.cpu_temp_c ?? "n/a")}C | use ${esc(d.local.cpu_usage_pct ?? "n/a")}%<br>` +
          `ram ${esc(d.local.mem_used_pct ?? "n/a")}% | wlan0 ${esc(d.local.wlan0_signal_dbm ?? "n/a")} dBm<br>` +
          `status ${esc(d.status_note || "ready")}`;

        const gps = d.local.gps || {};
        document.getElementById("gpsFix").textContent = gpsFixText(gps);
        document.getElementById("gpsCoords").textContent = gpsCoordText(gps);
        document.getElementById("gpsSats").textContent = gpsSatText(gps);
        document.getElementById("gpsAlt").textContent = gpsValue(gps.altitude_m, " m");
        document.getElementById("gpsSpeed").textContent = gpsValue(gps.speed_kph, " kph");
        document.getElementById("gpsTrack").textContent = gpsValue(gps.track_deg, " deg");
        document.getElementById("gpsDop").textContent = `${gpsValue(gps.hdop)} / ${gpsValue(gps.pdop)}`;
        document.getElementById("gpsErr").textContent = `x ${gpsValue(gps.epx_m)}m y ${gpsValue(gps.epy_m)}m v ${gpsValue(gps.epv_m)}m`;
        document.getElementById("gpsInfo").innerHTML =
          `device ${esc(gps.device || "n/a")} | mode ${esc(gps.mode ?? "n/a")} | time ${esc(gps.time_utc || "n/a")}<br>` +
          `fix ${esc(gpsFixText(gps))} | coords ${esc(gpsCoordText(gps))}<br>` +
          `alt ${esc(gpsValue(gps.altitude_m, "m"))} | speed ${esc(gpsValue(gps.speed_kph, "kph"))} | climb ${esc(gpsValue(gps.climb_kph, "kph"))}<br>` +
          `track ${esc(gpsValue(gps.track_deg, "deg"))} | hdop ${esc(gpsValue(gps.hdop))} | pdop ${esc(gpsValue(gps.pdop))} | vdop ${esc(gpsValue(gps.vdop))}`;
        const satRows = Array.isArray(gps.satellites) ? gps.satellites : [];
        document.getElementById("gpsSatLog").textContent = satRows.length
          ? satRows.map((sat) => {
              const used = sat.used ? "*" : " ";
              return `${used} PRN ${String(sat.prn ?? "?").padEnd(4)} SS ${String(sat.ss ?? "n/a").padStart(3)} EL ${String(sat.el ?? "n/a").padStart(3)} AZ ${String(sat.az ?? "n/a").padStart(3)}`;
            }).join("\\n")
          : "(no per-satellite data)";

        const nodes = document.getElementById("nodes");
        nodes.innerHTML = "";
        for (const n of d.nodes){
          const cls = n.status === "online" ? "online" : (n.status === "degraded" ? "degraded" : "offline");
          const fill = latencyFill(n.latency_ms);
          const nodeHist = ((((d.history || {}).nodes || {})[String(n.name)]) || []).slice(-24);
          const latTrend = spark(nodeHist.map((h) => h.latency_ms));
          const div = document.createElement("div");
          div.className = "node";
          div.innerHTML =
            `<div class="node-top"><b>${esc(n.name)}</b><span class="${cls}">${esc(n.status)}</span></div>` +
            `<div class="node-host">${esc(n.host)}</div>` +
            `<div class="node-host">lat ${esc(n.latency_ms ?? "n/a")}ms | smb ${esc(n.smb_text || "n/a")}</div>` +
            `<div class="node-host">lat trend ${esc(latTrend)}</div>` +
            `<div class="signal"><span style="width:${fill}%;"></span></div>`;
          nodes.appendChild(div);
        }

        const net = d.network || {};
        const adapters = Array.isArray(net.wireless_adapters) ? net.wireless_adapters : [];
        const mon = adapters.find((item) => item.role === "monitor") || null;
        document.getElementById("netPrimary").textContent = `${net.primary_iface || "wlan0"} ${net.primary_operstate || "n/a"} ${net.primary_mode || "n/a"} ${net.primary_ip || "n/a"}`;
        document.getElementById("netMonitor").textContent = mon
          ? `${mon.iface || "n/a"} ${mon.label || "WiFi"} ${mon.mode || "n/a"} ${mon.operstate || "n/a"}`
          : `${net.monitor_iface || "n/a"} ${net.monitor_operstate || "n/a"} ${net.monitor_mode || "n/a"} ${net.monitor_ip || "n/a"}`;
        document.getElementById("netRoute").textContent = `${net.default_route_iface || "n/a"} via ${net.default_route_gw || "n/a"} | ${net.primary_profile || "n/a"}`;
        document.getElementById("netServices").textContent = `NM ${net.networkmanager_state || "n/a"} | TS ${net.tailscale_state || "n/a"}`;
        const adapterRows = adapters.length
          ? adapters.map((item) => {
              const role = item.role === "primary" ? "primary" : (item.role === "monitor" ? "monitor" : "aux");
              const signal = item.signal_dbm !== null && item.signal_dbm !== undefined ? ` | ${esc(item.signal_dbm)} dBm` : "";
              const profile = item.active_profile && item.active_profile !== "n/a" ? ` | ${esc(item.active_profile)}` : "";
              const onboard = item.is_onboard ? " | onboard" : "";
              return `${esc(role)} ${esc(item.iface)} ${esc(item.label || "WiFi")} | ${esc(item.mode)} ${esc(item.operstate)}${signal}${profile}${onboard}`;
            }).join("<br>")
          : "no wireless adapters detected";
        document.getElementById("networkops").innerHTML =
          adapterRows + `<br>route ${esc(net.default_route_iface || "n/a")} via ${esc(net.default_route_gw || "n/a")}<br>` +
          `NetworkManager ${esc(net.networkmanager_state || "n/a")} | Tailscale ${esc(net.tailscale_state || "n/a")}`;
        const ifaceControls = document.getElementById("netIfaceControls");
        const externalAdapters = adapters.filter((item) => !item.is_onboard);
        ifaceControls.innerHTML = externalAdapters.length
          ? externalAdapters.map((item) => {
              const title = `${esc(item.iface)} ${esc(item.label || "WiFi")}`;
              const state = `${esc(item.mode || "n/a")} | ${esc(item.operstate || "n/a")}`;
              return `<div class="blurb mono" style="margin:0 0 6px; grid-column:1 / -1;">${title} | ${state}</div>` +
                `<button class="action-btn" onclick="act('net_monitor_${esc(item.iface)}')">${title} Monitor</button>` +
                `<button class="action-btn" onclick="act('net_managed_${esc(item.iface)}')">${title} Managed</button>`;
            }).join("")
          : `<div class="blurb mono" style="margin:0; grid-column:1 / -1;">no external adapters detected</div>`;

        const fh = d.foxhunt || {};
        const fhView = fh.view || {};
        const fhTarget = fh.target || {};
        const fhScanRows = (fh.scan_results || []).slice(0, 5).map((row, idx) => {
          const pointer = idx === Number(fh.selected_index || 0) ? ">" : " ";
          const sec = esc((row.security || "").slice(0, 1).toUpperCase() || "-");
          return `${pointer} ${esc((row.ssid || "<hidden>").slice(0, 12))}  ${esc(row.rssi ?? "n/a")}  ${esc(row.channel ?? "--")} ${sec}`;
        });
        const fhLastSeen = (fh.last_seen_age_s === null || fh.last_seen_age_s === undefined) ? "n/a" : `${Math.round(Number(fh.last_seen_age_s))}s`;
        document.getElementById("foxhunt").innerHTML =
          `mode ${esc(fh.state || "idle")} | iface ${esc(fh.iface || "wlan1")}<br>` +
          `target ${esc(fhTarget.ssid || "none")} | bssid ${esc(fhTarget.bssid ? String(fhTarget.bssid).slice(-8) : "--")}<br>` +
          `rssi ${esc(fh.current_rssi ?? "n/a")} | avg ${esc(fh.avg_short === null || fh.avg_short === undefined ? "n/a" : Math.round(Number(fh.avg_short)))} | trend ${esc(fh.trend || "stable")}<br>` +
          `best ${esc(fh.best_rssi ?? "n/a")} | last ${esc(fhLastSeen)} | visible ${esc(fh.target_visible ? "yes" : "no")}<br>` +
          `samples ${esc(fh.sample_count ?? 0)} | gps samples ${esc(fh.gps_valid_samples ?? 0)} | marks ${esc(fh.mark_count ?? 0)}`;
        document.getElementById("foxhuntlog").textContent =
          fh.state === "scan"
            ? (fhScanRows.length ? fhScanRows.join("\\n") : "(no APs visible)")
            : ((fhView.lines && fhView.lines.length) ? fhView.lines.map((row) => Array.isArray(row) ? row[0] : String(row)).join("\\n") : (fh.saved_path || "(no foxhunt session data)"));

        const wf = d.wifite || {};
        const wfTarget = wf.pending_target || {};
        const wfSelected = wf.selected_scan || {};
        const wfLastAge = (wf.last_scan_age_s === null || wf.last_scan_age_s === undefined) ? "n/a" : `${Math.round(Number(wf.last_scan_age_s))}s`;
        const wfLastDur = (wf.last_scan_duration_s === null || wf.last_scan_duration_s === undefined) ? "n/a" : `${Math.round(Number(wf.last_scan_duration_s))}s`;
        const wfSource = wf.last_scan_source || "none";
        document.getElementById("wifite").innerHTML =
          `mode ${esc(wf.state || "idle")} | iface ${esc(wf.iface || "wlan1")}<br>` +
          `target ${esc(wfTarget.ssid || "none")} | bssid ${esc(wfTarget.bssid ? String(wfTarget.bssid).slice(-8) : "--")}<br>` +
          `channel ${esc(wfTarget.channel ?? "n/a")} | security ${esc(wfTarget.security || "n/a")} | rssi ${esc(wfTarget.rssi ?? "n/a")}<br>` +
          `scan results ${esc(wf.scan_count ?? 0)} | source ${esc(wfSource)} | last ${esc(wfLastAge)} | dur ${esc(wfLastDur)}<br>` +
          `selected ${esc(wfSelected.ssid || "none")} | ch ${esc(wfSelected.channel ?? "n/a")} | rssi ${esc(wfSelected.rssi ?? "n/a")}<br>` +
          `status ${esc(wf.last_error || "ready")}`;
        const wfScanRows = (wf.scan_results || []).slice(0, 5).map((row, idx) => {
          const pointer = idx === Number(wf.selected_index || 0) ? ">" : " ";
          return `${pointer} ${(row.ssid || "<hidden>").slice(0, 12)}  ${row.rssi ?? "n/a"}  ${row.channel ?? "--"} ${String((row.security || "").slice(0, 1) || "-").toUpperCase()}`;
        });
        document.getElementById("wifitelog").textContent =
          wf.state === "scan"
            ? (wfScanRows.length ? wfScanRows.join("\\n") : "(no APs visible)")
            : ((wf.view && wf.view.lines && wf.view.lines.length) ? wf.view.lines.join("\\n") : "(no target selected)");

        const rj = d.raspyjack || {};
        const latestNmap = rj.latest_nmap || {};
        const latestAge = (latestNmap.age_seconds === null || latestNmap.age_seconds === undefined) ? "n/a" : `${Math.round(Number(latestNmap.age_seconds))}s ago`;
        const latestState = latestNmap.stable ? "ready" : "writing";
        document.getElementById("rj").innerHTML =
          `core ${esc(rj.core)} | device ${esc(rj.device)} | web ${esc(rj.webui)}<br>` +
          `iface ${esc(rj.primary_iface)}:${esc(rj.primary_ip)} | mon ${esc(rj.monitor_iface)}:${esc(rj.monitor_mode)}<br>` +
          `nmap ${yn(rj.nmap_running)} responder ${yn(rj.responder_running)} ettercap ${yn(rj.ettercap_running)}<br>` +
          `loot ${esc(rj.loot_files)} files / ${esc(fmtBytes(rj.loot_size_bytes))}<br>` +
          `latest ${esc(latestNmap.name || "none")} | ${esc(latestAge)} | ${esc(latestState)}`;
        document.getElementById("rjnmap").textContent =
          (latestNmap.preview_lines && latestNmap.preview_lines.length) ? latestNmap.preview_lines.join("\\n") : "(no nmap results yet)";

        const ao = d.angryoxide || {};
        const aoMenu = ao.menu || {};
        const aoTarget = aoMenu.pending_target || {};
        const sock = (ao.sockets_rx !== null && ao.sockets_tx !== null) ? `${ao.sockets_rx}/${ao.sockets_tx}` : "n/a";
        document.getElementById("ao").innerHTML =
          `${ao.running ? "RUNNING" : "STOPPED"} | iface ${esc(ao.iface)} (${esc(ao.iface_mode)})<br>` +
          `menu ${esc(aoMenu.state || "idle")} | scope ${esc(aoMenu.pending_scope || "all")} | gpsd ${esc(ao.gpsd_endpoint || "off")}<br>` +
          `target ${esc(aoTarget.ssid || "all networks")} | bssid ${esc(aoTarget.bssid ? String(aoTarget.bssid).slice(-8) : "--")}<br>` +
          `uptime ${esc(ao.runtime_seconds ?? "n/a")}s | panics ${esc(ao.panic_count)} | sockets ${esc(sock)}<br>` +
          `pcap ${esc(ao.pcap_files)} | kismet ${esc(ao.kismet_files)} | tar ${esc(ao.tar_files)} | hashfiles ${esc(ao.hc22000_files)}<br>` +
          `4-way ${esc(ao.fourway_hashes)} | pmkid ${esc(ao.pmkid_hashes)} | m1 ${esc(ao.m1_sent_events)} | rogue m2 ${esc(ao.rogue_m2_events)}<br>` +
          `ssids ${esc(ao.discovered_ssids)} | whitelist ${esc(ao.whitelist_count)} | results ${esc(ao.result_files)}/${esc(fmtBytes(ao.result_size_bytes))} | log ${esc(fmtBytes(ao.log_size_bytes))}`;
        const aoScanRows = (aoMenu.scan_results || []).slice(0, 5).map((row, idx) => {
          const pointer = idx === Number(aoMenu.selected_index || 0) ? ">" : " ";
          return `${pointer} ${(row.ssid || "<hidden>").slice(0, 12)}  ${row.rssi ?? "n/a"}  ${row.channel ?? "--"} ${String((row.security || "").slice(0, 1) || "-").toUpperCase()}`;
        });
        const aoPendingLines = (aoMenu.view && aoMenu.view.lines && aoMenu.view.lines.length) ? aoMenu.view.lines : [];
        const aoRecentLogs = (ao.log_lines && ao.log_lines.length) ? ao.log_lines.slice(-3) : [];
        const aoDebugLines = [...aoPendingLines, ...aoRecentLogs];
        document.getElementById("aolog").textContent =
          aoMenu.state === "scan"
            ? (aoScanRows.length ? aoScanRows.join("\\n") : "(no APs visible)")
            : (aoDebugLines.length ? aoDebugLines.join("\\n") : "(no log lines)");

        renderVirtualDevice(d);
        setView(d.page);
      } catch (e) {
        document.getElementById("meta").textContent = `error: ${String(e)}`;
      }
    }
    load();
    setInterval(load, 1600);
  </script>
</body>
</html>
"""

    def _start_remote_server(self) -> None:
        remote_cfg = self.config.get("remote", {}) if isinstance(self.config.get("remote"), dict) else {}
        enabled = remote_cfg.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("0", "false", "no", "off")
        if not enabled:
            return

        host = clean_text(remote_cfg.get("host", "0.0.0.0"), 64) or "0.0.0.0"
        try:
            port = int(remote_cfg.get("port", 8787))
        except Exception:
            port = 8787
        token = str(remote_cfg.get("token", "")).strip()
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_png(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, code: int, html: str) -> None:
                body = html.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if not token:
                    return True
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                candidate = self.headers.get("X-Token") or self.headers.get("X-PortableOps-Token")
                if not candidate:
                    candidate = qs.get("token", [""])[0]
                return candidate == token

            def _action_from_body(self, body: bytes) -> str:
                if not body:
                    return ""
                ctype = (self.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
                if ctype == "application/json":
                    try:
                        parsed = json.loads(body.decode("utf-8", errors="replace"))
                        if isinstance(parsed, dict):
                            return clean_text(parsed.get("action", ""), 32)
                    except Exception:
                        return ""
                try:
                    params = parse_qs(body.decode("utf-8", errors="replace"))
                    return clean_text(params.get("action", [""])[0], 32)
                except Exception:
                    return ""

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._send_html(200, app._remote_html())
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                if parsed.path == "/api/status":
                    if not self._authorized():
                        self._send_json(403, {"ok": False, "message": "forbidden"})
                        return
                    self._send_json(200, app._remote_status_payload())
                    return
                if parsed.path == "/api/frame.png":
                    if not self._authorized():
                        self._send_json(403, {"ok": False, "message": "forbidden"})
                        return
                    self._send_png(200, app._remote_frame_png())
                    return
                if parsed.path == "/api/action":
                    self._send_json(405, {"ok": False, "message": "use POST /api/action"})
                    return
                self._send_json(404, {"ok": False, "message": "not found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/api/action":
                    self._send_json(404, {"ok": False, "message": "not found"})
                    return
                if not self._authorized():
                    self._send_json(403, {"ok": False, "message": "forbidden"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except Exception:
                    length = 0
                body = self.rfile.read(max(0, length))
                action = self._action_from_body(body)
                src = self.client_address[0] if self.client_address else "remote"
                ok, message = app._queue_remote_action(action, source=src)
                self._send_json(200 if ok else 400, {"ok": ok, "message": message})

        try:
            httpd = ThreadingHTTPServer((host, port), Handler)
            httpd.daemon_threads = True
            self.remote_httpd = httpd
            self.remote_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self.remote_thread.start()
            self._set_status(f"Remote control: {host}:{port}", 8.0)
        except Exception as e:
            self._set_status(f"Remote server disabled: {clean_text(e, 56)}", 10.0)

    def _stop_remote_server(self) -> None:
        if self.remote_httpd is not None:
            try:
                self.remote_httpd.shutdown()
            except Exception:
                pass
            try:
                self.remote_httpd.server_close()
            except Exception:
                pass
            self.remote_httpd = None
        if self.remote_thread is not None:
            try:
                self.remote_thread.join(timeout=1.0)
            except Exception:
                pass
            self.remote_thread = None

    def _draw_card(self, rect: pygame.Rect, theme: Theme, alt: bool = False, title: str | None = None) -> None:
        draw_panel(
            self.screen,
            rect,
            theme,
            self.glow_cache,
            title=title,
            text_renderer=self.text,
            style=self.panel_style,
            alt=alt,
        )

    def _draw_layered_heading(
        self,
        theme: Theme,
        label: str,
        title: str,
        pos: tuple[int, int],
        title_size: int = 22,
        label_size: int = 13,
        title_color: tuple[int, int, int] | None = None,
        label_color: tuple[int, int, int] | None = None,
    ) -> None:
        x, y = pos
        lc = label_color if label_color is not None else theme.neon_secondary
        tc = title_color if title_color is not None else theme.text
        self._blit_text(clean_text(label.upper(), 24), label_size, lc, (x, y))
        self._blit_text(clean_text(title, 24), title_size, tc, (x, y + 14))

    def _draw_header(self, theme: Theme, snap: Snapshot) -> None:
        now_s = time.monotonic()
        pulse = 0.0 if not self.anim_enabled else ((math.sin(now_s * 1.9) + 1.0) * 0.5)

        self._blit_text("PORTABLE OPS", 18, theme.text, (10, 8))
        self._blit_text(clean_text(snap.hostname, 18), 12, theme.dim_text, (10, 26))

        header_bar = self.glow_cache.line(self.screen.get_width() - 20, 1, theme.neon_primary)
        header_bar.set_alpha(75 + int(45 * pulse))
        self.screen.blit(header_bar, (10, 40))

        clock_text = time.strftime("%H:%M:%S")
        clock_surf = self.text.render(clock_text, 13, theme.neon_primary)
        right_x = self.screen.get_width() - clock_surf.get_width() - 10
        self.screen.blit(clock_surf, (right_x, 10))

        if snap.wlan0_signal_pct is None:
            wifi_text = "WLAN0 n/a"
            if snap.wlan0_signal_dbm is not None:
                wifi_text = f"WLAN0 {snap.wlan0_signal_dbm}dBm"
        else:
            bars = clamp(int(round(snap.wlan0_signal_pct / 25.0)), 0, 4)
            wifi_text = f"WLAN0 {snap.wlan0_signal_pct}% [{'#' * bars}{'-' * (4 - bars)}]"

        wifi_surf = self.text.render(wifi_text, 11, theme.dim_text)
        metrics_x = self.screen.get_width() - wifi_surf.get_width() - 10
        self.screen.blit(wifi_surf, (metrics_x, 30))

    def _current_page(self) -> str:
        if not self.pages:
            return "overview"
        return self.pages[self.page_idx]

    def _footer_text(self, page: str) -> str:
        if page == "foxhunt":
            return self.foxhunt.footer_text()
        if page == "wifite":
            return self.wifite.footer_text()
        if page == "networkops":
            if self.network_ops_menu_open:
                return "U/D select  OK run  Y close  K3 home"
            return "U/D scroll  OK menu  Y refresh"
        if page == "raspyjack":
            return "U/D move  OK act  K1 run  K2 stop"
        if page.startswith("node:") or page == "gps":
            return "L/R page  U/D scroll  OK refresh"
        if page == "angryoxide":
            return self.ao_menu.footer_text()
        return "L/R page  OK refresh  K3 home"

    def _set_status(self, text: str, hold_seconds: float = 4.0) -> None:
        self.status_note = clean_text(text, 72)
        self.status_note_expires = time.monotonic() + max(0.5, hold_seconds)
        self._request_redraw()

    def _draw_footer(self, theme: Theme, page: str) -> None:
        text = self._footer_text(page)
        self._blit_text(clean_text(text, 38), 11, theme.dim_text, (8, 222))

        x0 = self.screen.get_width() - 10 - (len(self.pages) * 8)
        now_s = time.monotonic()
        for i in range(len(self.pages)):
            center = (x0 + (i * 8), 213)
            if i == self.page_idx:
                draw_status_dot(
                    self.screen,
                    center,
                    "online",
                    theme,
                    self.glow_cache,
                    now_s,
                    anim_enabled=self.anim_enabled,
                )
                continue
            pygame.draw.circle(self.screen, (0, 0, 0), center, 3)
            pygame.draw.circle(self.screen, theme.neon_primary, center, 3, 1)

    def _draw_background(self, theme: Theme) -> None:
        self.screen.fill(theme.bg)

    def _draw_main_panel(
        self,
        theme: Theme,
        label: str,
        title: str,
        lines: list[tuple[str, tuple[int, int, int]]],
        page_key: str,
        selected_index: int | None = None,
        action_rows: list[str] | None = None,
    ) -> None:
        card = pygame.Rect(10, 52, 220, 156)
        self._draw_card(card, theme, alt=True)
        self._draw_layered_heading(
            theme,
            label=label,
            title=title,
            pos=(18, 58),
            title_size=18,
            label_size=11,
            title_color=theme.neon_primary,
        )
        y = 94
        max_rows = 5 if action_rows else 6
        offset = self._set_scroll_meta(page_key, len(lines), max_rows)
        visible_lines = lines[offset : offset + max_rows]
        for idx, (line, color) in enumerate(visible_lines):
            if selected_index is not None and (not action_rows) and idx == selected_index:
                hi = pygame.Rect(16, y - 2, 208, 18)
                pygame.draw.rect(self.screen, (*theme.neon_primary, 30), hi, border_radius=2)
                pygame.draw.rect(self.screen, theme.neon_primary, hi, width=1, border_radius=2)
            self._blit_text(clean_text(line, 34), 13, color, (20, y))
            y += 18
        if action_rows:
            y += 2
            for idx, action in enumerate(action_rows[:2]):
                c = theme.neon_primary if idx == (selected_index or 0) else theme.dim_text
                self._blit_text(clean_text(action, 30), 12, c, (20 + (idx * 102), y))
        else:
            self._draw_scroll_hint(theme, 198, page_key)

    def _draw_overview(self, theme: Theme, snap: Snapshot) -> None:
        online = len([n for n in snap.nodes if n.status == "online"])
        degraded = len([n for n in snap.nodes if n.status == "degraded"])
        offline = len([n for n in snap.nodes if n.status == "offline"])
        sats = (
            f"{snap.gps.satellites_used if snap.gps.satellites_used is not None else 0}/"
            f"{snap.gps.satellites_visible if snap.gps.satellites_visible is not None else '?'}"
        )
        lines = [
            (f"TS {snap.tailscale_ip}", theme.text),
            (f"BAT {fmt_pct(snap.battery_pct)}", theme.text),
            (f"TEMP {snap.cpu_temp if snap.cpu_temp is not None else 'n/a'}C", theme.text),
            (f"CPU {fmt_pct(snap.cpu_usage_pct)}  RAM {fmt_pct(snap.mem_used_pct)}", theme.text),
            (f"GPS {snap.gps.fix_label}  SAT {sats}", theme.text),
            (f"NODES up:{online} dg:{degraded} dn:{offline}", theme.dim_text),
        ]
        self._draw_main_panel(theme, "LOCAL STATUS", "Overview", lines, "overview")

    def _set_scroll_meta(self, key: str, total: int, visible: int) -> int:
        self.page_line_counts[key] = total
        self.page_line_visible[key] = visible
        max_off = max(0, total - visible)
        current = clamp(self.page_scroll.get(key, 0), 0, max_off)
        self.page_scroll[key] = current
        return current

    def _scroll_page(self, key: str, delta: int) -> None:
        total = self.page_line_counts.get(key, 0)
        visible = self.page_line_visible.get(key, 6)
        max_off = max(0, total - visible)
        current = self.page_scroll.get(key, 0)
        new_value = clamp(current + delta, 0, max_off)
        if new_value != current:
            self.page_scroll[key] = new_value
            self._request_redraw()

    def _draw_scroll_hint(self, theme: Theme, y: int, key: str) -> None:
        total = self.page_line_counts.get(key, 0)
        visible = self.page_line_visible.get(key, 1)
        if total <= visible:
            return
        off = self.page_scroll.get(key, 0)
        text = self.text.render(f"{off + 1}/{total - visible + 1}", 13, theme.dim_text)
        self.screen.blit(text, (self.screen.get_width() - text.get_width() - 14, y))

    def _draw_node_page(self, theme: Theme, snap: Snapshot, node_name: str) -> None:
        node = None
        for n in snap.nodes:
            if n.name == node_name:
                node = n
                break

        if node is None:
            self._blit_text("Node not found", 16, theme.bad, (18, 100))
            return

        color = status_color(theme, node.status)
        lines: list[tuple[str, tuple[int, int, int]]] = []
        lines.append((f"STATUS {node.status.upper()}", color))
        lines.append((f"HOST {node.host}", theme.text))
        lines.append((f"LAT {fmt_latency(node.latency_ms)}", theme.text))
        open_ports = ",".join(str(p) for p in node.ports_open) if node.ports_open else "none"
        lines.append((f"PORTS {open_ports}", theme.text))
        lines.append((f"HTTP {node.health_text or 'n/a'}", theme.text))
        lines.append((clean_text(node.smb_text or "SMB n/a", 34), theme.dim_text))
        if node.smb_file_count is not None:
            lines.append((f"FILES {node.smb_file_count}", theme.dim_text))
        self._draw_main_panel(theme, "NODE DETAIL", node.name, lines[:6], f"node:{node_name}")

    def _draw_gps_page(self, theme: Theme, snap: Snapshot) -> None:
        gps = snap.gps
        coord_text = "no fix"
        if gps.latitude is not None and gps.longitude is not None:
            coord_text = f"{gps.latitude:.5f}, {gps.longitude:.5f}"
        lines = [
            (f"FIX {gps.fix_label}", theme.ok if gps.mode >= 2 else theme.warn),
            (f"COORD {coord_text}", theme.text),
            (f"SATS {gps.satellites_used if gps.satellites_used is not None else 0}/{gps.satellites_visible if gps.satellites_visible is not None else '?'}", theme.text),
            (f"ALT {gps.altitude_m if gps.altitude_m is not None else 'n/a'}m", theme.text),
            (f"SPD {gps.speed_kph if gps.speed_kph is not None else 'n/a'}kph", theme.text),
            (f"DEV {gps.device or 'n/a'}", theme.dim_text),
        ]
        self._draw_main_panel(theme, "SATELLITE LOCK", "GPS", lines, "gps")

    def _draw_network_ops_page(self, theme: Theme, snap: Snapshot) -> None:
        net = snap.network
        actions = self._network_ops_menu_items()
        selected = self.cursor_idx.get("networkops", 0) % len(actions)
        route = f"{net.default_route_iface} via {net.default_route_gw}"
        adapter_lines: list[tuple[str, tuple[int, int, int]]] = []
        for item in net.wireless_adapters[:4]:
            role = "PRI" if item.role == "primary" else ("MON" if item.role == "monitor" else "AUX")
            signal = f"{item.signal_dbm}dBm" if item.signal_dbm is not None else "n/a"
            color = theme.ok if item.operstate == "up" else theme.text
            adapter_lines.extend(
                [
                    (clean_text(f"{role} {item.iface} {item.label}", 34), color),
                    (clean_text(f"{item.mode} {item.operstate} {signal}", 34), theme.dim_text),
                    ("", theme.dim_text),
                ]
            )
        if not adapter_lines:
            adapter_lines.append(("No wireless adapters", theme.warn))
        visible_adapter_lines = adapter_lines[:-1] if len(adapter_lines) > 1 else adapter_lines
        lines = [
            (clean_text(f"PRI {net.primary_iface} {net.primary_operstate}/{net.primary_mode}", 34), theme.ok if net.primary_operstate == "up" else theme.warn),
            (clean_text(f"ip {net.primary_ip}  nm {net.primary_profile}", 34), theme.text),
            (clean_text(f"route {route}", 34), theme.text),
            (clean_text(f"NM {net.networkmanager_state} TS {net.tailscale_state}", 34), theme.dim_text),
            *visible_adapter_lines,
            ("OK actions  Y refresh", theme.dim_text),
        ]
        self._draw_main_panel(theme, "NETWORK OPS", "Interfaces", lines, "networkops")
        if self.network_ops_menu_open:
            self._draw_foxhunt_menu(theme, self._network_ops_menu_title(), actions, selected)

    def _save_runtime_config(self) -> bool:
        ok = write_config(self.config_path, self.config)
        if not ok:
            self._set_status("Config save failed", 5.0)
        return ok

    def _wireless_attack_adapters(self) -> list[WirelessAdapterStatus]:
        with self.data_lock:
            adapters = list(self.snapshot.network.wireless_adapters)
        items = [item for item in adapters if not item.is_onboard]
        items.sort(key=lambda item: (0 if item.role == "monitor" else 1, item.iface))
        return items

    def _wireless_attack_interface_labels(self) -> list[str]:
        labels: list[str] = []
        for item in self._wireless_attack_adapters():
            tags = []
            if item.role == "monitor":
                tags.append("monitor")
            if item.label and item.label != "WiFi":
                tags.append(item.label)
            labels.append(clean_text(" ".join([item.iface] + tags), 28))
        return labels

    def _set_foxhunt_iface(self, iface: str) -> bool:
        target = clean_text(iface, 24)
        if not target:
            return False
        valid = {item.iface for item in self._wireless_attack_adapters()}
        if target not in valid:
            self._set_status(f"FoxHunt iface invalid: {target}", 5.0)
            return False
        fox_cfg = self.config.get("foxhunt", {})
        if not isinstance(fox_cfg, dict):
            fox_cfg = {}
            self.config["foxhunt"] = fox_cfg
        fox_cfg["interface"] = target
        self.foxhunt.iface = target
        self.refresh_event.set()
        self.redraw_event.set()
        return self._save_runtime_config()

    def _set_wifite_iface(self, iface: str) -> bool:
        target = clean_text(iface, 24)
        if not target:
            return False
        valid = {item.iface for item in self._wireless_attack_adapters()}
        if target not in valid:
            self._set_status(f"Wifite iface invalid: {target}", 5.0)
            return False
        wf_cfg = self.config.get("wifite", {})
        if not isinstance(wf_cfg, dict):
            wf_cfg = {}
            self.config["wifite"] = wf_cfg
        wf_cfg["interface"] = target
        self.wifite.iface = target
        self.refresh_event.set()
        self.redraw_event.set()
        return self._save_runtime_config()

    def _set_angryoxide_iface(self, iface: str) -> bool:
        target = clean_text(iface, 24)
        if not target:
            return False
        valid = {item.iface for item in self._wireless_attack_adapters()}
        if target not in valid:
            self._set_status(f"AO iface invalid: {target}", 5.0)
            return False
        ao_cfg = self.config.get("angryoxide", {})
        if not isinstance(ao_cfg, dict):
            ao_cfg = {}
            self.config["angryoxide"] = ao_cfg
        command = clean_text(ao_cfg.get("command", ""), 512)
        current = clean_text(ao_cfg.get("interface", ""), 24)
        ao_cfg["interface"] = target
        if command and current:
            ao_cfg["command"] = clean_text(command.replace(current, target), 512)
        self.ao_menu.iface = target
        self.refresh_event.set()
        self.redraw_event.set()
        return self._save_runtime_config()

    def _foxhunt_owns_iface(self, iface: str) -> bool:
        state = clean_text(getattr(self.foxhunt, "state", ""), 16)
        active = {"scan", "target", "hunt"}
        return clean_text(getattr(self.foxhunt, "iface", ""), 24) == iface and state in active

    def _wifite_owns_iface(self, iface: str) -> bool:
        state = clean_text(getattr(self.wifite, "state", ""), 16)
        active = {"scan"}
        return clean_text(getattr(self.wifite, "iface", ""), 24) == iface and state in active

    def _ao_menu_owns_iface(self, iface: str) -> bool:
        state = clean_text(getattr(self.ao_menu, "state", ""), 16)
        active = {"scan", "profile", "iface"}
        return clean_text(getattr(self.ao_menu, "iface", ""), 24) == iface and state in active

    def _external_monitor_policy_targets(self) -> list[str]:
        targets: list[str] = []
        with self.data_lock:
            adapters = list(self.snapshot.network.wireless_adapters)
            ao_running = bool(self.snapshot.angryoxide.running)
        ao_iface = clean_text(getattr(self.ao_menu, "iface", ""), 24)
        for item in adapters:
            if item.is_onboard:
                continue
            iface = clean_text(item.iface, 24)
            if not iface:
                continue
            if self._foxhunt_owns_iface(iface):
                continue
            if self._wifite_owns_iface(iface):
                continue
            if self._ao_menu_owns_iface(iface):
                continue
            if ao_running and iface == ao_iface:
                continue
            if clean_text(item.active_profile, 32) not in ("", "n/a"):
                continue
            if clean_text(item.ip, 32) not in ("", "n/a"):
                continue
            targets.append(iface)
        return targets

    def _enforce_external_monitor_policy(self, interval_seconds: float = 4.0) -> None:
        now = time.monotonic()
        if (now - self.last_external_monitor_enforce_at) < max(1.0, interval_seconds):
            return
        self.last_external_monitor_enforce_at = now
        changed = False
        for iface in self._external_monitor_policy_targets():
            mode = clean_text(iface_mode(iface), 16).lower()
            if mode == "monitor":
                continue
            if not force_iface_mode(iface, "monitor"):
                continue
            wait_for_iface_mode(iface, "monitor", timeout_seconds=2.5)
            changed = True
        if changed:
            self.refresh_event.set()
            self.redraw_event.set()

    def _draw_raspyjack_page(self, theme: Theme, snap: Snapshot) -> None:
        rj = snap.raspyjack
        state_text = f"core:{rj.core_state} web:{rj.webui_state}"
        state_color = theme.ok if rj.core_state == "active" else theme.warn
        actions = ["Launch", "Stop"]
        selected = self.cursor_idx.get("raspyjack", 0) % len(actions)
        lines = [
            (clean_text(state_text.upper(), 32), state_color),
            (f"device {rj.device_state}", theme.text),
            (f"{rj.primary_iface} {rj.primary_ip}", theme.text),
            (f"{rj.monitor_iface} {rj.monitor_mode}", theme.text),
            (f"loot {rj.loot_files} {format_bytes(rj.loot_size_bytes)}", theme.text),
            (f"latest {rj.latest_nmap_name or 'none'}", theme.dim_text),
        ]
        self._draw_main_panel(theme, "MANAGED APP", "RaspyJack", lines, "raspyjack", selected_index=selected, action_rows=actions)

    def _draw_angryoxide_page(self, theme: Theme, snap: Snapshot) -> None:
        ao = snap.angryoxide
        view = self.ao_menu.render_view(bool(ao.running))
        if view.state == "scan":
            card = pygame.Rect(10, 52, 220, 156)
            self._draw_card(card, theme, alt=True)
            self._draw_layered_heading(
                theme,
                label="PACKET OPS",
                title="AngryOxide",
                pos=(18, 58),
                title_size=18,
                label_size=11,
                title_color=theme.neon_primary,
            )
            self._blit_text(clean_text(view.list_hint, 22), 11, theme.dim_text, (18, 80))
            y = 98
            for idx, row in enumerate(view.list_rows[:5]):
                row_color = theme.bg if idx == view.list_selected else theme.text
                sec_color = theme.bg if idx == view.list_selected else theme.dim_text
                if idx == view.list_selected:
                    hi = pygame.Rect(14, y - 2, 212, 18)
                    pygame.draw.rect(self.screen, (*theme.neon_primary, 28), hi, border_radius=2)
                    pygame.draw.rect(self.screen, theme.neon_primary, hi, width=1, border_radius=2)
                    self._blit_text(">", 13, theme.neon_primary, (18, y))
                self._blit_text(clean_text(row[0], 10), 13, row_color, (28, y))
                self._blit_text(clean_text(row[1], 4), 13, row_color, (110, y))
                self._blit_text(clean_text(row[2], 3), 13, row_color, (158, y))
                self._blit_text(clean_text(row[3], 2), 13, sec_color, (198, y))
                y += 18
            self._blit_text("SSID", 11, theme.dim_text, (28, 86))
            self._blit_text("RSSI", 11, theme.dim_text, (108, 86))
            self._blit_text("CH", 11, theme.dim_text, (156, 86))
            self._blit_text("S", 11, theme.dim_text, (198, 86))
            return
        if view.state == "profile" or view.menu_open:
            lines = [(line, theme.text) for line in view.lines[:6]]
            self._draw_main_panel(theme, "PACKET OPS", "AngryOxide", lines, "angryoxide")
            self._draw_foxhunt_menu(theme, view.menu_title, view.menu_items, view.menu_index)
            return
        run_state = "RUNNING" if ao.running else "STOPPED"
        run_color = theme.ok if ao.running else theme.warn
        lines: list[tuple[str, tuple[int, int, int]]] = []
        if not self.angryoxide_log_view:
            lines.extend([
                (run_state, run_color),
                (f"iface {ao.iface} {ao.iface_mode}", theme.text),
                (f"pcap {ao.pcap_files}  kismet {ao.kismet_files}", theme.text),
                (f"tar {ao.tar_files}  hashfiles {ao.hc22000_files}", theme.text),
                (f"4-way {ao.fourway_hashes}  pmkid {ao.pmkid_hashes}", theme.text),
                (f"ssid {ao.discovered_ssids}  whitelist {ao.whitelist_count}", theme.dim_text),
            ])
        else:
            lines.append(("LOG VIEW", run_color))
            if ao.log_lines:
                lines.extend((clean_text(x, 34), theme.text) for x in ao.log_lines[-5:])
            else:
                lines.append(("(no log lines yet)", theme.dim_text))

        self._draw_main_panel(theme, "PACKET OPS", "AngryOxide", lines[:6], "angryoxide")

    def _foxhunt_color(self, theme: Theme, key: str) -> tuple[int, int, int]:
        mapping = {
            "text": theme.text,
            "dim": theme.dim_text,
            "ok": theme.ok,
            "warn": theme.warn,
            "bad": theme.bad,
            "accent": theme.neon_primary,
        }
        return mapping.get(key, theme.text)

    def _draw_foxhunt_menu(self, theme: Theme, menu_title: str, items: list[str], selected: int) -> None:
        menu_rect = pygame.Rect(22, 72, 196, 118)
        self._draw_card(menu_rect, theme, alt=False)
        self._blit_text(clean_text(menu_title, 20), 13, theme.neon_primary, (menu_rect.x + 12, menu_rect.y + 8))
        total = len(items)
        max_visible = 4
        if total <= max_visible:
            start = 0
        else:
            start = clamp(selected - 2, 0, total - max_visible)
        visible = items[start : start + max_visible]
        y = menu_rect.y + 30
        for idx, item in enumerate(visible):
            real_idx = start + idx
            if real_idx == selected:
                hi = pygame.Rect(menu_rect.x + 8, y - 3, menu_rect.width - 16, 19)
                pygame.draw.rect(self.screen, (*theme.neon_primary, 58), hi, border_radius=3)
                pygame.draw.rect(self.screen, theme.neon_primary, hi, width=2, border_radius=3)
                self._blit_text(">", 13, theme.neon_primary, (menu_rect.x + 12, y))
            self._blit_text(
                clean_text(item, 24),
                14,
                theme.bg if real_idx == selected else theme.text,
                (menu_rect.x + (24 if real_idx == selected else 14), y),
            )
            y += 19
        if total > max_visible:
            footer = f"{selected + 1}/{total}"
            self._blit_text(footer, 11, theme.neon_secondary, (menu_rect.right - 38, menu_rect.bottom - 15))

    def _draw_wifite_page(self, theme: Theme, snap: Snapshot) -> None:
        view = self.wifite.render_view()
        if view.state == "scan":
            card = pygame.Rect(10, 52, 220, 156)
            self._draw_card(card, theme, alt=True)
            self._draw_layered_heading(
                theme,
                label="PASSIVE PREP",
                title="Wifite",
                pos=(18, 58),
                title_size=18,
                label_size=11,
                title_color=theme.neon_primary,
            )
            self._blit_text(clean_text(view.list_hint, 22), 11, theme.dim_text, (18, 80))
            y = 98
            for idx, row in enumerate(view.list_rows[:5]):
                row_color = theme.bg if idx == view.list_selected else theme.text
                sec_color = theme.bg if idx == view.list_selected else theme.dim_text
                if idx == view.list_selected:
                    hi = pygame.Rect(14, y - 2, 212, 18)
                    pygame.draw.rect(self.screen, (*theme.neon_primary, 28), hi, border_radius=2)
                    pygame.draw.rect(self.screen, theme.neon_primary, hi, width=1, border_radius=2)
                    self._blit_text(">", 13, theme.neon_primary, (18, y))
                self._blit_text(clean_text(row[0], 10), 13, row_color, (28, y))
                self._blit_text(clean_text(row[1], 4), 13, row_color, (110, y))
                self._blit_text(clean_text(row[2], 3), 13, row_color, (158, y))
                self._blit_text(clean_text(row[3], 2), 13, sec_color, (198, y))
                y += 18
            self._blit_text("SSID", 11, theme.dim_text, (28, 86))
            self._blit_text("RSSI", 11, theme.dim_text, (108, 86))
            self._blit_text("CH", 11, theme.dim_text, (156, 86))
            self._blit_text("S", 11, theme.dim_text, (198, 86))
            return

        lines = [(line, theme.text) for line in view.lines[:6]]
        self._draw_main_panel(theme, "PASSIVE PREP", "Wifite", lines, "wifite")
        if view.menu_open:
            self._draw_foxhunt_menu(theme, view.menu_title, view.menu_items, view.menu_index)

    def _draw_foxhunt_page(self, theme: Theme, snap: Snapshot) -> None:
        view = self.foxhunt.render_view()
        if view.state == "scan":
            card = pygame.Rect(10, 52, 220, 156)
            self._draw_card(card, theme, alt=True)
            self._draw_layered_heading(
                theme,
                label="DEFENSIVE SURVEY",
                title="FoxHunt",
                pos=(18, 58),
                title_size=18,
                label_size=11,
                title_color=theme.neon_primary,
            )
            self._blit_text(clean_text(view.list_hint, 22), 11, theme.dim_text, (18, 80))
            y = 98
            for idx, row in enumerate(view.list_rows[:5]):
                row_color = theme.bg if idx == view.list_selected else theme.text
                sec_color = theme.bg if idx == view.list_selected else theme.dim_text
                if idx == view.list_selected:
                    hi = pygame.Rect(14, y - 2, 212, 18)
                    pygame.draw.rect(self.screen, (*theme.neon_primary, 28), hi, border_radius=2)
                    pygame.draw.rect(self.screen, theme.neon_primary, hi, width=1, border_radius=2)
                    self._blit_text(">", 13, theme.neon_primary, (18, y))
                self._blit_text(clean_text(row[0], 10), 13, row_color, (28, y))
                self._blit_text(clean_text(row[1], 4), 13, row_color, (110, y))
                self._blit_text(clean_text(row[2], 3), 13, row_color, (158, y))
                self._blit_text(clean_text(row[3], 2), 13, sec_color, (198, y))
                y += 18
            self._blit_text("SSID", 11, theme.dim_text, (28, 86))
            self._blit_text("RSSI", 11, theme.dim_text, (108, 86))
            self._blit_text("CH", 11, theme.dim_text, (156, 86))
            self._blit_text("S", 11, theme.dim_text, (198, 86))
        elif view.state == "hunt":
            card = pygame.Rect(10, 52, 220, 156)
            self._draw_card(card, theme, alt=True)
            self._draw_layered_heading(
                theme,
                label="TARGET TRACK",
                title="FoxHunt",
                pos=(18, 58),
                title_size=18,
                label_size=11,
                title_color=theme.neon_primary,
            )
            self._blit_text(clean_text(view.target_name, 18), 18, theme.text, (18, 86))
            self._blit_text(clean_text(view.big_value, 14), 28, theme.neon_primary, (18, 112))
            trend_color = theme.ok if view.trend == "hotter" else theme.bad if view.trend == "colder" else theme.warn
            trend_text = "HOTTER ^" if view.trend == "hotter" else "COLDER v" if view.trend == "colder" else "STABLE -"
            self._blit_text(trend_text, 18, trend_color, (18, 148))
            y = 176
            for line, color_key in view.lines[:4]:
                self._blit_text(clean_text(line, 26), 12, self._foxhunt_color(theme, color_key), (18, y))
                y += 14
        else:
            self._draw_main_panel(
                theme,
                "DEFENSIVE SURVEY",
                "FoxHunt",
                [(line, self._foxhunt_color(theme, color_key)) for line, color_key in view.lines[:6]],
                "foxhunt",
            )

        if view.menu_open:
            self._draw_foxhunt_menu(theme, view.menu_title, view.menu_items, view.menu_index)

    def _render_page_to(
        self,
        target: pygame.Surface,
        page: str,
        snap: Snapshot,
        include_overlays: bool = True,
        include_fade: bool = True,
    ) -> None:
        old = self.screen
        self.screen = target
        try:
            theme = self.theme
            self._draw_background(theme)
            self._draw_header(theme, snap)

            if page == "overview":
                self._draw_overview(theme, snap)
            elif page == "gps":
                self._draw_gps_page(theme, snap)
            elif page == "networkops":
                self._draw_network_ops_page(theme, snap)
            elif page.startswith("node:"):
                self._draw_node_page(theme, snap, page.split(":", 1)[1])
            elif page == "foxhunt":
                self._draw_foxhunt_page(theme, snap)
            elif page == "wifite":
                self._draw_wifite_page(theme, snap)
            elif page == "raspyjack":
                self._draw_raspyjack_page(theme, snap)
            elif page == "angryoxide":
                self._draw_angryoxide_page(theme, snap)

            self._draw_footer(theme, page)
            self._blit_text(clean_text(self.status_note, 38), 11, theme.dim_text, (10, 209))

            if include_overlays and (self.effect_scanlines or self.effect_vignette or self.effect_noise):
                self.effects.draw_overlays(self.screen, time.monotonic())

            if include_fade and self.startup_fade_start is not None:
                elapsed = time.monotonic() - self.startup_fade_start
                if elapsed < self.startup_fade_duration:
                    alpha = int(255 * (1.0 - (elapsed / self.startup_fade_duration)))
                    overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
                    overlay.fill((0, 0, 0, clamp(alpha, 0, 255)))
                    self.screen.blit(overlay, (0, 0))
                else:
                    self.startup_fade_start = None
        finally:
            self.screen = old

    def _angryoxide_start(
        self,
        profile: str = "default",
        target_bssid: str | None = None,
        gpsd_endpoint: str | None = None,
    ) -> None:
        ao_cfg = self.config.get("angryoxide", {}) if isinstance(self.config.get("angryoxide"), dict) else {}
        iface = clean_text(ao_cfg.get("interface", "wlan1"), 24)

        pid = read_pid_file(ANGRYOXIDE_PID_PATH)
        live_pids = set(find_angryoxide_pids())
        if (pid and pid in live_pids) or live_pids:
            self._set_status("AngryOxide already running", 6.0)
            return

        start_cmd = resolve_command(str(ao_cfg.get("start_monitor_cmd", "")))
        if start_cmd:
            rc, _, err = run_shell(start_cmd, timeout=20.0)
            if rc != 0:
                self._set_status(f"monitor start failed: {clean_text(err, 40)}", 8.0)
                return

        if iface and not wait_for_iface_mode(iface, "monitor", timeout_seconds=4.0):
            forced = force_iface_mode(iface, "monitor")
            if (not forced) or (not wait_for_iface_mode(iface, "monitor", timeout_seconds=3.0)):
                self._set_status(f"{iface} not in monitor mode", 8.0)
                return

        run_cmd = self._angryoxide_command_for_profile(
            ao_cfg,
            profile,
            target_bssid=target_bssid,
            gpsd_endpoint=gpsd_endpoint,
        )
        if not run_cmd:
            self._set_status("AngryOxide command empty", 8.0)
            return
        log_path = Path(str(ao_cfg.get("log_path", "/home/kali/Results/angryoxide-live.log"))).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        run_cwd = Path("/home/kali")
        try:
            parts = shlex.split(run_cmd)
            if parts and parts[0].startswith("/"):
                p = Path(parts[0])
                if p.exists():
                    run_cwd = p.parent
        except Exception:
            pass

        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] dashboard start\n")
                lf.write(f"profile: {clean_text(profile, 24)}\n")
                lf.write(f"target: {clean_text(target_bssid or 'all', 32)}\n")
                lf.write(f"gpsd: {clean_text(gpsd_endpoint or 'off', 48)}\n")
                lf.write(f"cmd: {run_cmd}\n")
                lf.write(f"cwd: {run_cwd}\n")

            # AngryOxide expects a TTY for its input reader. Launch via `script`
            # to provide a pseudo-terminal even when started from this dashboard/service.
            script_bin = shutil.which("script")
            if script_bin:
                pty_cmd = (
                    f"COLUMNS=120 LINES=40 TERM=xterm-256color {shlex.quote(script_bin)} -q -f -a "
                    f"{shlex.quote(str(log_path))} -c {shlex.quote(run_cmd)}"
                )
                proc = subprocess.Popen(
                    ["/usr/bin/env", "bash", "-lc", pty_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=str(run_cwd),
                )
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write("launcher: started with PTY wrapper (script)\n")
            else:
                lf_bin = open(log_path, "ab")
                proc = subprocess.Popen(
                    ["/usr/bin/env", "bash", "-lc", run_cmd],
                    stdout=lf_bin,
                    stderr=lf_bin,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=str(run_cwd),
                )
                lf_bin.close()
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write("launcher: script(1) missing, started without PTY\n")

            write_pid_file(ANGRYOXIDE_PID_PATH, proc.pid)
            time.sleep(1.0)
            if not pid_alive(proc.pid):
                self._set_status("AngryOxide crashed quickly; check log", 8.0)
            else:
                profile_note = clean_text(profile, 24)
                if profile_note and profile_note != "default":
                    self._set_status(f"AO {profile_note} started (pid {proc.pid})", 8.0)
                else:
                    self._set_status(f"AngryOxide started (pid {proc.pid})", 8.0)
            self.refresh_event.set()
        except Exception as e:
            self._set_status(f"start failed: {e}", 8.0)

    def _angryoxide_stop(self) -> None:
        ao_cfg = self.config.get("angryoxide", {}) if isinstance(self.config.get("angryoxide"), dict) else {}
        pid_file_pid = read_pid_file(ANGRYOXIDE_PID_PATH)
        pids = set(find_angryoxide_pids())
        if pid_file_pid and pid_is_angryoxide(pid_file_pid):
            pids.add(pid_file_pid)

        if not pids:
            self._set_status("AngryOxide not running", 5.0)
        for pid in sorted(pids):
            if not pid_alive(pid):
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        for _ in range(20):
            if not any(pid_alive(p) for p in pids):
                break
            time.sleep(0.1)
        for pid in sorted(pids):
            if pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass

        try:
            if ANGRYOXIDE_PID_PATH.exists():
                ANGRYOXIDE_PID_PATH.unlink()
        except Exception:
            pass

        self._set_status("AngryOxide stopped", 8.0)
        self.refresh_event.set()

    def _managed_app_cfg(self, app_id: str) -> dict[str, Any]:
        cfg = self.config.get("managed_apps", {})
        if not isinstance(cfg, dict):
            return {}
        entry = cfg.get(app_id, {})
        return entry if isinstance(entry, dict) else {}

    def _managed_app_running(self, app_id: str) -> bool:
        cfg = self._managed_app_cfg(app_id)
        status_cmd = clean_text(cfg.get("status_cmd", ""), 256)
        if not status_cmd:
            return False
        rc, out, _ = run_shell(status_cmd, timeout=3.0)
        if rc == 0 and "active" in out:
            return True
        return False

    def _run_detached_managed_app_cmd(self, command: str, unit_name: str) -> bool:
        cmd = clean_text(command, 256)
        if not cmd:
            return False
        runner = shutil.which("systemd-run")
        if runner:
            unit = clean_text(unit_name, 48) or f"launcher-app-{int(time.time())}"
            quoted = shlex.quote(cmd)
            launch_cmd = (
                f"{shlex.quote(runner)} --unit {shlex.quote(unit)} --collect --same-dir "
                f"/usr/bin/env bash -lc {quoted}"
            )
            rc, _, _ = run_shell(launch_cmd, timeout=8.0)
            return rc == 0
        try:
            subprocess.Popen(
                ["/usr/bin/env", "bash", "-lc", cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            return False

    def _run_managed_app_cmd(self, app_id: str, field: str) -> bool:
        cfg = self._managed_app_cfg(app_id)
        command = clean_text(cfg.get(field, ""), 256)
        if not command:
            self._set_status(f"{app_id}: no {field}", 6.0)
            return False
        if field == "start_cmd" and bool(cfg.get("takes_over_display", False)):
            unit = f"launcher-{clean_text(app_id, 24).lower()}-handoff-{int(time.time())}"
            if self._run_detached_managed_app_cmd(command, unit):
                return True
            self._set_status(f"{app_id}: detached start failed", 8.0)
            return False
        rc, _, err = run_shell(command, timeout=15.0)
        if rc != 0:
            self._set_status(f"{app_id}: {field} failed {clean_text(err, 40)}", 8.0)
            return False
        return True

    def _suspend_for_app(self, app_id: str) -> None:
        self.suspended_app_id = app_id
        self.suspended_started_at = time.monotonic()
        self.screen.fill((0, 0, 0))
        self._update_display()
        if self.input_backend is not None:
            self.input_backend.suspend()
        self._set_status(f"{app_id} active", 4.0)

    def _resume_from_app(self) -> None:
        self.suspended_app_id = None
        self.suspended_started_at = 0.0
        if self.input_backend is not None:
            self.input_backend.resume()
        self.refresh_event.set()
        self.redraw_event.set()
        self._set_status("Launcher resumed", 5.0)

    def _launch_managed_app(self, app_id: str) -> None:
        if self._managed_app_running(app_id):
            self._suspend_for_app(app_id)
            return
        if not self._run_managed_app_cmd(app_id, "start_cmd"):
            return
        time.sleep(1.2)
        if self._managed_app_running(app_id):
            self._suspend_for_app(app_id)
        else:
            self._set_status(f"{app_id} did not start", 6.0)

    def _stop_managed_app(self, app_id: str) -> None:
        if not self._managed_app_running(app_id):
            self._set_status(f"{app_id} already stopped", 5.0)
            if self.suspended_app_id == app_id:
                self._resume_from_app()
            return
        if self._run_managed_app_cmd(app_id, "stop_cmd"):
            self._set_status(f"{app_id} stopping", 5.0)

    def _check_suspended_app(self) -> None:
        app_id = self.suspended_app_id
        if not app_id:
            return
        if self._managed_app_running(app_id):
            time.sleep(0.12)
            return
        self._resume_from_app()

    def _home_page_index(self) -> int:
        try:
            return self.pages.index("overview")
        except ValueError:
            return 0

    def _move_selection(self, page: str, delta: int) -> None:
        if page == "foxhunt":
            self.foxhunt.move(delta)
            return
        if page == "wifite":
            self.wifite.move(delta)
            return
        if page == "networkops":
            if self.network_ops_menu_open:
                count = len(self._network_ops_menu_items())
                self.cursor_idx[page] = (self.cursor_idx.get(page, 0) + delta) % count
                self._request_redraw()
            else:
                self._scroll_page(page, delta)
            return
        if page == "angryoxide":
            with self.data_lock:
                running = self.snapshot.angryoxide.running
            self.ao_menu.move(delta, running=running)
            return
        if page == "raspyjack":
            count = 2
            self.cursor_idx[page] = (self.cursor_idx.get(page, 0) + delta) % count
            self._request_redraw()
            return
        if page.startswith("node:") or page == "gps":
            self._scroll_page(page, delta)

    def _activate_page_action(self, page: str, key: int = pygame.K_RETURN) -> None:
        if page == "foxhunt":
            self.foxhunt.ok()
            return
        if page == "wifite":
            self.wifite.ok()
            return
        if page == "overview":
            self._set_status("Refreshing...", 3.0)
            self.refresh_event.set()
            return
        if page == "gps":
            self.refresh_event.set()
            self._set_status("GPS refreshed", 3.0)
            return
        if page == "networkops":
            if not self.network_ops_menu_open:
                self._network_ops_open_menu()
            else:
                self._run_network_ops_action(self._network_ops_selected_action())
            return
        if page == "raspyjack":
            selected = self.cursor_idx.get(page, 0) % 2
            if selected == 0:
                self._launch_managed_app("raspyjack")
            else:
                self._stop_managed_app("raspyjack")
            return
        if page == "angryoxide":
            with self.data_lock:
                running = self.snapshot.angryoxide.running
            self.ao_menu.ok(running=running)
            return

    def _handle_context_action(self, page: str, key: int) -> None:
        if page == "foxhunt":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F1):
                self.foxhunt.ok()
            elif key in (pygame.K_y, pygame.K_F2):
                self.foxhunt.secondary()
            elif key == pygame.K_F3:
                if not self.foxhunt.back():
                    self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page == "wifite":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F1):
                self.wifite.ok()
            elif key in (pygame.K_y, pygame.K_F2):
                self.wifite.secondary()
            elif key == pygame.K_F3:
                if not self.wifite.back():
                    self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page == "overview":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F2):
                self._activate_page_action(page, key)
            elif key == pygame.K_F3:
                self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page == "networkops":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F1):
                if not self.network_ops_menu_open:
                    self._network_ops_open_menu()
                else:
                    self._run_network_ops_action(self._network_ops_selected_action())
            elif key in (pygame.K_y, pygame.K_F2):
                if self.network_ops_menu_open:
                    if self.network_ops_menu_state == "mode":
                        self.network_ops_menu_state = "iface"
                        self.network_ops_iface_target = ""
                        self.cursor_idx["networkops"] = 0
                        self._request_redraw()
                    elif self.network_ops_menu_state == "iface":
                        self.network_ops_menu_state = "actions"
                        self.cursor_idx["networkops"] = 0
                        self._request_redraw()
                    else:
                        self._network_ops_close_menu()
                else:
                    self._run_network_ops_action("Refresh")
            elif key == pygame.K_F3:
                if self._network_ops_close_menu():
                    return
                self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page.startswith("node:") or page == "gps":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F2):
                self._activate_page_action(page, key)
            elif key == pygame.K_y:
                self._scroll_page(page, +1)
            elif key == pygame.K_F3:
                self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page == "raspyjack":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F1):
                self._launch_managed_app("raspyjack")
            elif key == pygame.K_F2:
                self._stop_managed_app("raspyjack")
            elif key == pygame.K_F3:
                self._set_page_index(self._home_page_index(), direction=-1)
            return

        if page == "angryoxide":
            if key in (pygame.K_x, pygame.K_RETURN, pygame.K_F1):
                with self.data_lock:
                    running = self.snapshot.angryoxide.running
                self.ao_menu.ok(running=running)
            elif key in (pygame.K_y, pygame.K_F2):
                with self.data_lock:
                    running = self.snapshot.angryoxide.running
                self.ao_menu.secondary(running=running)
            elif key == pygame.K_F3:
                if not self.ao_menu.back():
                    self._set_page_index(self._home_page_index(), direction=-1)

    def render(self) -> None:
        if self.transition.active:
            self.transition.compose_into(self.screen, now_ts=time.monotonic())
            return
        with self.data_lock:
            snap = self.snapshot
            page = self._current_page()
        self._render_page_to(self.screen, page, snap, include_overlays=True, include_fade=True)

    def run(self) -> None:
        try:
            while self.running:
                self._drain_remote_actions()
                if self.suspended_app_id:
                    self._check_suspended_app()
                    time.sleep(0.08)
                    continue
                if self.local_buttons_enabled and (not self.interrupt_buttons_enabled):
                    self._poll_buttons()

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                        break
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self.running = False
                            break
                        if event.key in (pygame.K_a, pygame.K_LEFT):
                            if self._current_page() == "foxhunt":
                                if self.foxhunt.block_page_cycle() and self.foxhunt.back():
                                    continue
                            if self._current_page() == "wifite":
                                if self.wifite.block_page_cycle() and self.wifite.back():
                                    continue
                            if self._current_page() == "networkops":
                                if self.network_ops_menu_open:
                                    if self.network_ops_menu_state == "mode":
                                        self.network_ops_menu_state = "iface"
                                        self.network_ops_iface_target = ""
                                        self.cursor_idx["networkops"] = 0
                                        self._request_redraw()
                                        continue
                                    if self.network_ops_menu_state == "iface":
                                        self.network_ops_menu_state = "actions"
                                        self.cursor_idx["networkops"] = 0
                                        self._request_redraw()
                                        continue
                                    if self._network_ops_close_menu():
                                        continue
                            if self._current_page() == "angryoxide" and self.ao_menu.back():
                                continue
                            if self._maybe_theme_combo(event.key):
                                continue
                            self._set_page_index(self.page_idx - 1, direction=-1)
                        elif event.key in (pygame.K_b, pygame.K_RIGHT):
                            if self._current_page() == "foxhunt" and self.foxhunt.block_page_cycle():
                                continue
                            if self._current_page() == "wifite" and self.wifite.block_page_cycle():
                                continue
                            if self._current_page() == "networkops" and self.network_ops_menu_open:
                                continue
                            if self._current_page() == "angryoxide" and self.ao_menu.block_page_cycle():
                                continue
                            if self._maybe_theme_combo(event.key):
                                continue
                            self._set_page_index(self.page_idx + 1, direction=1)
                        elif event.key == pygame.K_UP:
                            self._move_selection(self._current_page(), -1)
                        elif event.key == pygame.K_DOWN:
                            self._move_selection(self._current_page(), +1)
                        elif event.key in (pygame.K_x, pygame.K_y, pygame.K_RETURN, pygame.K_F1, pygame.K_F2, pygame.K_F3):
                            self._handle_context_action(self._current_page(), event.key)

                self._enforce_external_monitor_policy()
                now = time.monotonic()
                needs_redraw = self.preview_mode or self.transition.active or self.redraw_event.is_set()
                if (now - self.last_draw_at) >= self.idle_redraw_seconds:
                    needs_redraw = True

                if not needs_redraw:
                    time.sleep(0.05)
                    continue

                try:
                    with self.frame_lock:
                        self.render()
                        self._update_display()
                    self.last_draw_at = now
                    self.redraw_event.clear()
                except Exception as e:
                    self._set_status(f"Render error: {e}", 6.0)
                    with self.frame_lock:
                        self.screen.fill((0, 0, 0))
                        msg = self.font_small.render(self.status_note, True, (255, 140, 140))
                        self.screen.blit(msg, (10, 108))
                        self._update_display()
                    self.last_draw_at = now
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        self._stop_remote_server()
        try:
            self.worker.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.foxhunt_worker.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.wifite_worker.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ao_menu_worker.join(timeout=1.0)
        except Exception:
            pass
        with self.frame_lock:
            self.screen.fill((0, 0, 0))
            self._update_display()
        if self.input_backend is not None:
            self.input_backend.cleanup()
        if self.display is not None:
            self.display.set_led(0.0, 0.0, 0.0)
            self.display.set_backlight(0.0)
        pygame.quit()


def main() -> int:
    app = DashboardApp(CONFIG_PATH)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
