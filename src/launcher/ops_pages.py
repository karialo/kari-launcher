from __future__ import annotations

import glob
import json
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .foxhunt import _clean


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 1.0:
        return "now"
    if seconds < 60.0:
        return f"{int(seconds)}s"
    if seconds < 3600.0:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def _resolve_tool(name: str, fallbacks: list[str]) -> str:
    found = shutil.which(name)
    if found:
        return found
    for path in fallbacks:
        if Path(path).exists():
            return path
    return name


def _check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, int(port))) == 0
    except Exception:
        return False
    finally:
        sock.close()


@dataclass
class OpsPageView:
    state: str
    menu_open: bool
    menu_title: str
    menu_items: list[str] = field(default_factory=list)
    menu_index: int = 0
    lines: list[str] = field(default_factory=list)
    list_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    list_selected: int = 0
    list_hint: str = ""


class SocketWatchController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.refresh_interval = max(4.0, float(cfg.get("refresh_interval_seconds", 8.0) or 8.0))
        self.visible_rows = 5
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.lock = threading.RLock()
        self.tool_ss = _resolve_tool("ss", ["/usr/bin/ss", "/bin/ss"])

        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.selected_index = 0
        self.listeners: list[dict[str, str]] = []
        self.established_count = 0
        self.last_error = ""
        self.last_refresh_completed = 0.0
        self.last_refresh_duration_s = 0.0

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.listeners:
                return "U/D socket  OK menu  Y refresh"
            return "OK menu  Y refresh  L/R page"

    def _menu_items(self) -> list[str]:
        return ["Refresh Data", "Back"]

    def _run_cmd(self, args: list[str], timeout: float = 4.0) -> str:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        except Exception:
            return ""
        return result.stdout or ""

    def _parse_listener_line(self, raw: str, proto: str) -> dict[str, str] | None:
        parts = raw.split()
        if len(parts) < 5:
            return None
        local = parts[3]
        if "[" in local and "]" in local:
            host, _, port = local.rpartition(":")
        else:
            host, _, port = local.rpartition(":")
        return {
            "proto": proto,
            "host": _clean(host or "*", 32),
            "port": _clean(port or "?", 12),
        }

    def refresh(self) -> None:
        started = time.monotonic()
        listeners: list[dict[str, str]] = []
        for proto, args in (
            ("tcp", [self.tool_ss, "-H", "-ltn"]),
            ("udp", [self.tool_ss, "-H", "-lun"]),
        ):
            out = self._run_cmd(args)
            for raw in out.splitlines():
                item = self._parse_listener_line(raw.strip(), proto)
                if item:
                    listeners.append(item)
        listeners.sort(key=lambda item: (item["proto"], item["port"], item["host"]))
        established = len(
            [
                line
                for line in self._run_cmd([self.tool_ss, "-H", "-tan", "state", "established"]).splitlines()
                if line.strip()
            ]
        )
        with self.lock:
            self.listeners = listeners[:32]
            self.established_count = established
            self.selected_index = max(0, min(self.selected_index, len(self.listeners) - 1))
            self.last_refresh_completed = time.monotonic()
            self.last_refresh_duration_s = max(0.0, self.last_refresh_completed - started)
            self.last_error = "" if self.listeners or established else "No socket data"
        self.redraw_cb()

    def tick(self, force: bool = False) -> None:
        with self.lock:
            stale = (time.monotonic() - self.last_refresh_completed) if self.last_refresh_completed else None
            menu_open = self.menu_open
        if menu_open:
            return
        if force or stale is None or stale >= self.refresh_interval:
            self.refresh()

    def move(self, delta: int) -> None:
        with self.lock:
            if self.menu_open:
                items = self._menu_items()
                self.menu_index = (self.menu_index + delta) % len(items)
            elif self.listeners:
                self.selected_index = max(0, min(self.selected_index + delta, len(self.listeners) - 1))
            else:
                return
        self.redraw_cb()

    def ok(self) -> None:
        with self.lock:
            if self.state == "browse":
                self.state = "idle"
                self.redraw_cb()
                self._set_status("Kismet device selected", 4.0)
                return
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False
        if item == "Refresh Data":
            self.refresh()
            self._set_status("SocketWatch refreshed", 4.0)
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self.refresh()
        self._set_status("SocketWatch refreshed", 4.0)

    def back(self) -> bool:
        with self.lock:
            if self.menu_open:
                self.menu_open = False
                self.redraw_cb()
                return True
        return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open

    def remote_action(self, action: str) -> bool:
        if _clean(action, 32).lower() == "socketwatch_refresh":
            self.secondary()
            return True
        return False

    def render_view(self) -> OpsPageView:
        with self.lock:
            rows: list[tuple[str, str, str, str]] = []
            total = len(self.listeners)
            selected = 0
            if total:
                start = max(0, min(self.selected_index - 1, max(0, total - 3)))
                visible = self.listeners[start : start + 3]
                for item in visible:
                    rows.append((item["proto"].upper(), item["port"], item["host"], "LISTEN"))
                selected = self.selected_index - start
            lines = [
                f"LISTEN {len(self.listeners)}  ESTAB {self.established_count}",
                f"LAST {_fmt_age(None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed))}",
                f"DUR {int(round(self.last_refresh_duration_s))}s",
            ]
            if total:
                item = self.listeners[self.selected_index]
                lines.append(f"SEL {item['proto'].upper()} {item['port']}")
                lines.append(f"HOST {item['host']}")
            elif self.last_error:
                lines.append(self.last_error[:28])
            return OpsPageView(
                state=self.state,
                menu_open=self.menu_open,
                menu_title="SOCKETWATCH MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=lines,
                list_rows=rows,
                list_selected=selected,
                list_hint=f"{len(self.listeners)} listeners",
            )

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            selected_item = self.listeners[self.selected_index] if self.listeners else None
            return {
                "state": self.state,
                "listener_count": len(self.listeners),
                "established_count": self.established_count,
                "selected_index": self.selected_index,
                "selected_socket": selected_item,
                "listeners": list(self.listeners[:24]),
                "last_error": self.last_error,
                "last_refresh_age_s": None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed),
                "last_refresh_duration_s": self.last_refresh_duration_s,
                "view": {
                    "state": view.state,
                    "menu_open": view.menu_open,
                    "menu_title": view.menu_title,
                    "menu_items": list(view.menu_items),
                    "menu_index": view.menu_index,
                    "lines": list(view.lines),
                    "list_rows": list(view.list_rows),
                    "list_selected": view.list_selected,
                    "list_hint": view.list_hint,
                },
            }


class TrafficViewController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.refresh_interval = max(1.0, float(cfg.get("refresh_interval_seconds", 2.0) or 2.0))
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.lock = threading.RLock()
        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.selected_index = 0
        self.entries: list[dict[str, Any]] = []
        self.last_error = ""
        self.last_refresh_completed = 0.0
        self.last_refresh_duration_s = 0.0
        self.prev_counters: dict[str, tuple[float, int, int]] = {}

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.entries:
                return "U/D iface  OK menu  Y refresh"
            return "OK menu  Y refresh  L/R page"

    def _menu_items(self) -> list[str]:
        return ["Refresh Data", "Reset Rates", "Back"]

    def _read_counters(self) -> dict[str, tuple[int, int]]:
        path = Path("/proc/net/dev")
        counters: dict[str, tuple[int, int]] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return counters
        for raw in lines[2:]:
            if ":" not in raw:
                continue
            iface, rest = raw.split(":", 1)
            parts = rest.split()
            if len(parts) < 16:
                continue
            try:
                rx = int(parts[0])
                tx = int(parts[8])
            except Exception:
                continue
            counters[_clean(iface.strip(), 24)] = (rx, tx)
        return counters

    def _fmt_rate(self, value: float) -> str:
        if value < 1024:
            return f"{int(value)}B/s"
        if value < (1024 * 1024):
            return f"{value / 1024.0:.1f}K/s"
        return f"{value / (1024.0 * 1024.0):.1f}M/s"

    def refresh(self) -> None:
        started = time.monotonic()
        now = time.monotonic()
        counters = self._read_counters()
        entries: list[dict[str, Any]] = []
        for iface, (rx, tx) in counters.items():
            prev = self.prev_counters.get(iface)
            rx_rate = 0.0
            tx_rate = 0.0
            if prev is not None:
                prev_ts, prev_rx, prev_tx = prev
                dt = max(0.001, now - prev_ts)
                rx_rate = max(0.0, (rx - prev_rx) / dt)
                tx_rate = max(0.0, (tx - prev_tx) / dt)
            entries.append(
                {
                    "iface": iface,
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_rate": rx_rate,
                    "tx_rate": tx_rate,
                }
            )
        entries.sort(key=lambda item: (-(item["rx_rate"] + item["tx_rate"]), item["iface"]))
        self.prev_counters = {iface: (now, rx, tx) for iface, (rx, tx) in counters.items()}
        with self.lock:
            self.entries = entries[:24]
            self.selected_index = max(0, min(self.selected_index, len(self.entries) - 1))
            self.last_refresh_completed = now
            self.last_refresh_duration_s = max(0.0, time.monotonic() - started)
            self.last_error = "" if entries else "No traffic counters"
        self.redraw_cb()

    def tick(self, force: bool = False) -> None:
        with self.lock:
            stale = (time.monotonic() - self.last_refresh_completed) if self.last_refresh_completed else None
        if force or stale is None or stale >= self.refresh_interval:
            self.refresh()

    def move(self, delta: int) -> None:
        with self.lock:
            if self.menu_open:
                items = self._menu_items()
                self.menu_index = (self.menu_index + delta) % len(items)
            elif self.entries:
                self.selected_index = max(0, min(self.selected_index + delta, len(self.entries) - 1))
            else:
                return
        self.redraw_cb()

    def ok(self) -> None:
        with self.lock:
            if self.state == "browse":
                self.state = "idle"
                self.redraw_cb()
                self._set_status("Kismet device selected", 4.0)
                return
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False
        if item == "Refresh Data":
            self.refresh()
            self._set_status("TrafficView refreshed", 4.0)
            return
        if item == "Reset Rates":
            with self.lock:
                self.prev_counters = {}
            self.refresh()
            self._set_status("TrafficView baseline reset", 4.0)
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self.refresh()
        self._set_status("TrafficView refreshed", 4.0)

    def back(self) -> bool:
        with self.lock:
            if self.menu_open:
                self.menu_open = False
                self.redraw_cb()
                return True
        return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open

    def remote_action(self, action: str) -> bool:
        cmd = _clean(action, 32).lower()
        if cmd == "trafficview_refresh":
            self.secondary()
            return True
        if cmd == "trafficview_reset":
            with self.lock:
                self.prev_counters = {}
            self.refresh()
            self._set_status("TrafficView baseline reset", 4.0)
            return True
        return False

    def render_view(self) -> OpsPageView:
        with self.lock:
            rows: list[tuple[str, str, str, str]] = []
            total = len(self.entries)
            selected = 0
            if total:
                start = max(0, min(self.selected_index - 1, max(0, total - 3)))
                visible = self.entries[start : start + 3]
                for item in visible:
                    rows.append(
                        (
                            item["iface"],
                            self._fmt_rate(item["rx_rate"]),
                            self._fmt_rate(item["tx_rate"]),
                            "LIVE",
                        )
                    )
                selected = self.selected_index - start
            lines = [
                f"IFACES {len(self.entries)}",
                f"LAST {_fmt_age(None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed))}",
                f"DUR {int(round(self.last_refresh_duration_s))}s",
            ]
            if total:
                item = self.entries[self.selected_index]
                lines.append(f"SEL {item['iface']}")
                lines.append(f"RX {self._fmt_rate(item['rx_rate'])} TX {self._fmt_rate(item['tx_rate'])}")
                lines.append(f"TOTAL {item['rx_bytes']} / {item['tx_bytes']}")
            elif self.last_error:
                lines.append(self.last_error[:28])
            return OpsPageView(
                state=self.state,
                menu_open=self.menu_open,
                menu_title="TRAFFICVIEW MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=lines,
                list_rows=rows,
                list_selected=selected,
                list_hint=f"{len(self.entries)} interfaces",
            )

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            selected_item = self.entries[self.selected_index] if self.entries else None
            return {
                "state": self.state,
                "entries": copy_entries(self.entries[:24]),
                "selected_index": self.selected_index,
                "selected_entry": dict(selected_item) if selected_item else None,
                "last_error": self.last_error,
                "last_refresh_age_s": None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed),
                "last_refresh_duration_s": self.last_refresh_duration_s,
                "view": {
                    "state": view.state,
                    "menu_open": view.menu_open,
                    "menu_title": view.menu_title,
                    "menu_items": list(view.menu_items),
                    "menu_index": view.menu_index,
                    "lines": list(view.lines),
                    "list_rows": list(view.list_rows),
                    "list_selected": view.list_selected,
                    "list_hint": view.list_hint,
                },
            }


def copy_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in items]


class KismetController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
        hunt_cb: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.refresh_interval = max(10.0, float(cfg.get("refresh_interval_seconds", 20.0) or 20.0))
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.hunt_cb = hunt_cb
        self.lock = threading.RLock()
        self.tool_systemctl = _resolve_tool("systemctl", ["/usr/bin/systemctl", "/bin/systemctl"])
        self.tool_pgrep = _resolve_tool("pgrep", ["/usr/bin/pgrep", "/bin/pgrep"])
        self.tool_journalctl = _resolve_tool("journalctl", ["/usr/bin/journalctl", "/bin/journalctl"])
        self.service_names = [str(x) for x in cfg.get("service_names", ["kismet.service"]) if str(x).strip()]
        self.networkmanager_service = _clean(cfg.get("networkmanager_service", "NetworkManager.service"), 48) or "NetworkManager.service"
        self.primary_interface = _clean(cfg.get("primary_interface", "wlan0"), 24) or "wlan0"
        self.host = _clean(cfg.get("webui_host", "127.0.0.1"), 64) or "127.0.0.1"
        self.port = int(cfg.get("webui_port", 2501) or 2501)
        self.source_config_path = Path(str(cfg.get("source_config_path", "/etc/kismet/kismet_site.conf") or "/etc/kismet/kismet_site.conf")).expanduser()
        self.capture_dirs = [Path(str(x)).expanduser() for x in cfg.get("capture_dirs", ["/var/log/kismet", str(Path.home() / "kismet")]) if str(x).strip()]
        self.db_globs = [str(x) for x in cfg.get("db_globs", ["/Kismet-*.kismet", "~/Kismet-*.kismet", "/root/Kismet-*.kismet"]) if str(x).strip()]
        self.state = "idle"
        self.browse_kind = "wireless"
        self.menu_open = False
        self.menu_index = 0
        self.selected_index = 0
        self.service_state = "unknown"
        self.proc_running = False
        self.port_open = False
        self.capture_files = 0
        self.capture_breakdown: dict[str, int] = {}
        self.latest_files: list[str] = []
        self.latest_file_age_s: float | None = None
        self.db_path = ""
        self.devices: list[dict[str, Any]] = []
        self.selected_device: dict[str, Any] | None = None
        self.wifi_ap_count = 0
        self.wifi_device_count = 0
        self.bt_device_count = 0
        self.source_lines: list[str] = []
        self.active_wifi_source = ""
        self.active_bt_source = ""
        self.warning_lines: list[str] = []
        self.log_lines: list[str] = []
        self.last_error = ""
        self.last_refresh_completed = 0.0
        self.last_refresh_duration_s = 0.0

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.state == "browse_type":
                return "U/D type  OK pick  L back"
            if self.state == "browse":
                return "U/D device  OK pick  L back"
            return "U/D scroll  OK menu  Y refresh"

    def _menu_items(self) -> list[str]:
        items = ["Browse Devices"]
        if self._selected_can_hunt():
            items.append("Hunt")
        items.extend(["Refresh Data", "Start Service", "Stop Service", "Restart Service", "Recover Link", "Back"])
        return items

    def _browse_type_items(self) -> list[str]:
        return ["Wireless", "Bluetooth", "Back"]

    def _run_cmd(self, args: list[str], timeout: float = 3.0) -> str:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        except Exception:
            return ""
        return result.stdout or ""

    def _display_label(self, item: dict[str, Any]) -> str:
        label = _clean(str(item.get("label", "device")), 48)
        mac = _clean(str(item.get("mac", "--")).lower(), 32)
        manufacturer = _clean(str(item.get("manufacturer", "")), 48)
        if label.lower() == mac.lower() and manufacturer:
            dtype = _clean(str(item.get("type", "")), 24)
            if dtype and dtype.lower() not in {"unknown", "device"}:
                return _clean(f"{manufacturer} {dtype}", 48)
            return manufacturer
        return label

    def _phy_tag(self, item: dict[str, Any]) -> str:
        phy = _clean(str(item.get("phy", "?")), 24).lower()
        dtype = _clean(str(item.get("type", "?")), 24)
        if "bluetooth" in phy:
            return "BT"
        if "802.11" in phy or "wi-fi" in phy or "wifi" in phy:
            if "ap" in dtype.lower():
                return "AP"
            return "WIFI"
        return _clean(dtype or phy or "DEV", 8).upper()

    def _is_bluetooth(self, item: dict[str, Any]) -> bool:
        phy = _clean(str(item.get("phy", "?")), 24).lower()
        dtype = _clean(str(item.get("type", "?")), 24).lower()
        return "bluetooth" in phy or dtype == "btle"

    def _filtered_devices(self) -> list[dict[str, Any]]:
        if self.browse_kind == "bluetooth":
            return [item for item in self.devices if self._is_bluetooth(item)]
        return [item for item in self.devices if not self._is_bluetooth(item)]

    def _selected_can_hunt(self) -> bool:
        if not self.selected_device:
            return False
        if self._is_bluetooth(self.selected_device):
            return False
        return bool(_clean(self.selected_device.get("mac", ""), 32))

    def _latest_db_path(self) -> str:
        newest: tuple[float, str] | None = None
        for pattern in self.db_globs:
            for match in glob.glob(str(Path(pattern).expanduser())):
                try:
                    mtime = Path(match).stat().st_mtime
                except Exception:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, match)
        return "" if newest is None else newest[1]

    def _read_config_sources(self) -> tuple[str, str]:
        wifi_source = ""
        bt_source = ""
        path = self.source_config_path
        if not path.exists():
            return wifi_source, bt_source
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return wifi_source, bt_source
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or not line.startswith("source="):
                continue
            source = _clean(line.split("=", 1)[1], 64)
            base = source.split(":", 1)[0]
            low = source.lower()
            if "linuxbluetooth" in low and not bt_source:
                bt_source = base
            elif "linuxwifi" in low and not wifi_source:
                wifi_source = base
        return wifi_source, bt_source

    def _load_devices(self) -> tuple[str, list[dict[str, Any]]]:
        db_path = self._latest_db_path()
        if not db_path:
            return "", []
        rows: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            cur.execute("select last_time, devmac, phyname, type, device from devices order by last_time desc limit 64")
            for last_time, devmac, phyname, dtype, blob in cur.fetchall():
                payload: dict[str, Any] = {}
                try:
                    if isinstance(blob, bytes):
                        payload = json.loads(blob.decode("utf-8", "ignore"))
                    elif isinstance(blob, str):
                        payload = json.loads(blob)
                except Exception:
                    payload = {}
                label = (
                    payload.get("kismet.device.base.commonname")
                    or payload.get("kismet.device.base.name")
                    or payload.get("dot11.device/dot11.device.last_beaconed_ssid")
                    or _clean(devmac or "unknown", 32)
                )
                channel = str(payload.get("kismet.device.base.channel") or payload.get("dot11.device/dot11.device.last_channel") or "n/a")
                signal = payload.get("kismet.device.base.signal/kismet.common.signal.last_signal")
                seenby = int(payload.get("kismet.device.base.seenbycount") or 0)
                manuf = payload.get("kismet.device.base.manuf") or ""
                crypt = payload.get("dot11.device/dot11.device.last_crypt_set") or ""
                rows.append(
                    {
                        "label": _clean(label, 48),
                        "mac": _clean((devmac or "--").lower(), 32),
                        "phy": _clean(phyname or "unknown", 24),
                        "type": _clean(dtype or "unknown", 24),
                        "channel": _clean(channel, 12),
                        "signal": None if signal is None else int(signal),
                        "seenby": seenby,
                        "manufacturer": _clean(manuf, 48),
                        "last_seen_age_s": max(0.0, time.time() - float(last_time or 0.0)),
                        "crypt": _clean(str(crypt), 24),
                    }
                )
            conn.close()
        except Exception:
            return db_path, []
        return db_path, rows

    def refresh(self) -> None:
        started = time.monotonic()
        service_state = "unknown"
        if self.service_names:
            for name in self.service_names:
                state = _clean(self._run_cmd([self.tool_systemctl, "is-active", name]).strip() or "unknown", 16)
                if state == "active":
                    service_state = state
                    break
                service_state = state
        proc_running = bool(self._run_cmd([self.tool_pgrep, "-f", "kismet"]).strip())
        port_open = _check_port(self.host, self.port, timeout=0.45)
        latest: list[tuple[float, str]] = []
        count = 0
        breakdown = {"kismet": 0, "pcapng": 0, "wiglecsv": 0, "netxml": 0}
        for directory in self.capture_dirs:
            if not directory.exists() or not directory.is_dir():
                continue
            try:
                for fp in directory.iterdir():
                    if not fp.is_file():
                        continue
                    name = fp.name.lower()
                    if not (name.endswith(".kismet") or name.endswith(".pcapng") or name.endswith(".wiglecsv") or name.endswith(".netxml")):
                        continue
                    count += 1
                    if name.endswith(".kismet"):
                        breakdown["kismet"] += 1
                    elif name.endswith(".pcapng"):
                        breakdown["pcapng"] += 1
                    elif name.endswith(".wiglecsv"):
                        breakdown["wiglecsv"] += 1
                    elif name.endswith(".netxml"):
                        breakdown["netxml"] += 1
                    try:
                        latest.append((fp.stat().st_mtime, fp.name))
                    except Exception:
                        latest.append((0.0, fp.name))
            except Exception:
                continue
        latest.sort(reverse=True)
        db_path, devices = self._load_devices()
        wifi_ap_count = 0
        wifi_device_count = 0
        bt_device_count = 0
        for item in devices:
            phy = item.get("phy", "").lower()
            dtype = item.get("type", "").lower()
            if "bluetooth" in phy or "bt" in dtype:
                bt_device_count += 1
            elif "ap" in dtype or "wifi access point" in dtype:
                wifi_ap_count += 1
            else:
                wifi_device_count += 1
        source_lines: list[str] = []
        active_wifi_source = ""
        active_bt_source = ""
        warning_lines: list[str] = []
        log_lines: list[str] = []
        if self.service_names:
            journal = self._run_cmd([self.tool_journalctl, "-u", self.service_names[0], "-n", "80", "--no-pager"], timeout=4.0)
            for raw in journal.splitlines():
                line = _clean(raw.strip(), 120)
                low = line.lower()
                if not line:
                    continue
                if "finished configuring" in low and "ready to capture" in low:
                    m = re.search(r"finished configuring\s+([a-z0-9_./-]+)", line, re.IGNORECASE)
                    if m:
                        iface = _clean(m.group(1).split("/", 1)[0], 24)
                        if iface.startswith("wlan"):
                            active_wifi_source = iface
                    source_lines.append(line)
                    continue
                if "data source '" in low and "launched successfully" in low:
                    m = re.search(r"data source '([^']+)' launched successfully", line, re.IGNORECASE)
                    if m:
                        source = _clean(m.group(1), 48)
                        if "linuxbluetooth" in source and not active_bt_source:
                            active_bt_source = source.split(":", 1)[0]
                        elif "linuxwifi" in source and not active_wifi_source:
                            active_wifi_source = source.split(":", 1)[0]
                    source_lines.append(line)
                    continue
                if self.primary_interface.lower() in low and (
                    "telling networkmanager not to control interface" in low
                    or "bringing down parent interface" in low
                    or "broadcom" in low
                    or "nexmon" in low
                ):
                    warning_lines.append(line)
                    continue
                if "no data sources defined" in low or "will not capture anything until a source is added" in low:
                    warning_lines.append(line)
                    continue
                if ("source" in low and "open" in low):
                    source_lines.append(line)
                    continue
                if "http server listening" in low or "gps tracker" in low or "saving packets" in low:
                    log_lines.append(line)
                    continue
                if "warning:" in low or "error:" in low:
                    warning_lines.append(line)
                    continue
            source_lines = source_lines[-3:]
            warning_lines = warning_lines[-4:]
            log_lines = log_lines[-4:]
        cfg_wifi_source, cfg_bt_source = self._read_config_sources()
        if not active_wifi_source:
            active_wifi_source = cfg_wifi_source
        if not active_bt_source:
            active_bt_source = cfg_bt_source
        with self.lock:
            previous_mac = ""
            if self.selected_device:
                previous_mac = str(self.selected_device.get("mac", "")).lower()
            self.service_state = service_state
            self.proc_running = proc_running
            self.port_open = port_open
            self.capture_files = count
            self.capture_breakdown = breakdown
            self.latest_files = [_clean(name, 48) for _, name in latest[:6]]
            self.latest_file_age_s = None if not latest else max(0.0, time.time() - latest[0][0])
            self.db_path = _clean(db_path, 72)
            self.devices = devices
            if previous_mac:
                for idx, item in enumerate(self.devices):
                    if str(item.get("mac", "")).lower() == previous_mac:
                        self.selected_index = idx
                        break
            self.selected_index = max(0, min(self.selected_index, len(self.devices) - 1))
            self.selected_device = self.devices[self.selected_index] if self.devices else None
            self.wifi_ap_count = wifi_ap_count
            self.wifi_device_count = wifi_device_count
            self.bt_device_count = bt_device_count
            self.source_lines = source_lines
            self.active_wifi_source = active_wifi_source
            self.active_bt_source = active_bt_source
            self.warning_lines = warning_lines
            self.log_lines = log_lines
            self.last_error = "" if (count or proc_running or port_open or service_state != "unknown") else "Kismet not detected"
            self.last_refresh_completed = time.monotonic()
            self.last_refresh_duration_s = max(0.0, self.last_refresh_completed - started)
        self.redraw_cb()

    def _service_action(self, action: str) -> bool:
        if not self.service_names:
            self.last_error = "No Kismet service configured"
            self.redraw_cb()
            return False
        target = self.service_names[0]
        try:
            result = subprocess.run(
                [self.tool_systemctl, action, target],
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
        except Exception as exc:
            self.last_error = _clean(exc, 80)
            self.redraw_cb()
            return False
        if result.returncode != 0:
            self.last_error = _clean((result.stderr or result.stdout or f"{action} failed"), 80)
            self.redraw_cb()
            return False
        self.refresh()
        self._set_status(f"Kismet {action} ok", 4.0)
        return True

    def _recover_link(self) -> bool:
        if self.service_names:
            try:
                subprocess.run(
                    [self.tool_systemctl, "stop", self.service_names[0]],
                    capture_output=True,
                    text=True,
                    timeout=8.0,
                    check=False,
                )
            except Exception:
                pass
        try:
            result = subprocess.run(
                [self.tool_systemctl, "restart", self.networkmanager_service],
                capture_output=True,
                text=True,
                timeout=12.0,
                check=False,
            )
        except Exception as exc:
            self.last_error = _clean(exc, 80)
            self.redraw_cb()
            return False
        if result.returncode != 0:
            self.last_error = _clean((result.stderr or result.stdout or "NetworkManager restart failed"), 80)
            self.redraw_cb()
            return False
        self.refresh()
        self._set_status("Link recovery complete", 5.0)
        return True

    def tick(self, force: bool = False) -> None:
        with self.lock:
            stale = (time.monotonic() - self.last_refresh_completed) if self.last_refresh_completed else None
            menu_open = self.menu_open
        if menu_open:
            return
        if force or stale is None or stale >= self.refresh_interval:
            self.refresh()

    def move(self, delta: int) -> None:
        with self.lock:
            if self.state == "browse_type":
                items = self._browse_type_items()
                self.menu_index = (self.menu_index + delta) % len(items)
                self.redraw_cb()
                return
            if self.state == "browse":
                visible = self._filtered_devices()
                if not visible:
                    return
                self.selected_index = max(0, min(self.selected_index + delta, len(visible) - 1))
                self.selected_device = visible[self.selected_index]
                self.redraw_cb()
                return
            if not self.menu_open:
                return
            items = self._menu_items()
            self.menu_index = (self.menu_index + delta) % len(items)
        self.redraw_cb()

    def ok(self) -> None:
        with self.lock:
            if self.state == "browse_type":
                choice = self._browse_type_items()[self.menu_index]
                if choice == "Back":
                    self.state = "idle"
                else:
                    self.browse_kind = "bluetooth" if choice.lower() == "bluetooth" else "wireless"
                    self.state = "browse"
                    visible = self._filtered_devices()
                    self.selected_index = 0
                    self.selected_device = visible[0] if visible else None
                self.redraw_cb()
                return
            if self.state == "browse":
                self.state = "idle"
                self.redraw_cb()
                self._set_status("Kismet device selected", 4.0)
                return
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False
        if item == "Browse Devices":
            with self.lock:
                self.state = "browse_type"
                self.menu_index = 0
            self.redraw_cb()
            self._set_status("Browse Kismet devices", 4.0)
            return
        if item == "Hunt":
            if self.hunt_cb and self.selected_device:
                if self.hunt_cb(dict(self.selected_device)):
                    self._set_status("Handed to FoxHunt", 4.0)
                    return
            self._set_status("Kismet target not huntable", 4.0)
            self.redraw_cb()
            return
        if item == "Refresh Data":
            self.refresh()
            self._set_status("Kismet refreshed", 4.0)
            return
        if item == "Start Service":
            self._service_action("start")
            return
        if item == "Stop Service":
            self._service_action("stop")
            return
        if item == "Restart Service":
            self._service_action("restart")
            return
        if item == "Recover Link":
            self._recover_link()
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self.refresh()
        self._set_status("Kismet refreshed", 4.0)

    def back(self) -> bool:
        with self.lock:
            if self.state == "browse":
                self.state = "browse_type"
                self.redraw_cb()
                return True
            if self.state == "browse_type":
                self.state = "idle"
                self.redraw_cb()
                return True
            if self.menu_open:
                self.menu_open = False
                self.redraw_cb()
                return True
        return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open or self.state in ("browse", "browse_type")

    def remote_action(self, action: str) -> bool:
        cmd = _clean(action, 32).lower()
        if cmd == "kismet_refresh":
            self.secondary()
            return True
        if cmd == "kismet_start":
            return self._service_action("start")
        if cmd == "kismet_stop":
            return self._service_action("stop")
        if cmd == "kismet_restart":
            return self._service_action("restart")
        if cmd == "kismet_recover":
            return self._recover_link()
        return False

    def render_view(self) -> OpsPageView:
        with self.lock:
            rows: list[tuple[str, str, str, str]] = []
            if self.state == "browse_type":
                return OpsPageView(
                    state=self.state,
                    menu_open=True,
                    menu_title="DEVICE TYPE",
                    menu_items=self._browse_type_items(),
                    menu_index=self.menu_index,
                    lines=[
                        "Choose device class",
                        f"WIFI {self.wifi_ap_count + self.wifi_device_count}",
                        f"BT {self.bt_device_count}",
                        f"DB {Path(self.db_path).name[:22] if self.db_path else 'n/a'}",
                    ],
                    list_rows=[],
                    list_selected=0,
                    list_hint="device types",
                )
            if self.state == "browse":
                visible_devices = self._filtered_devices()
                total = len(visible_devices)
                selected = 0
                if total:
                    start = max(0, min(self.selected_index - 1, max(0, total - 3)))
                    visible = visible_devices[start : start + 3]
                    for idx, item in enumerate(visible, start=start + 1):
                        sig = item.get("signal")
                        sig_text = "n/a" if sig is None else str(sig)
                        rows.append((
                            _clean(f"{idx}/{total} {self._display_label(item)}", 18),
                            _clean(str(item.get("manufacturer", "")) or str(item.get("mac", "--")), 22),
                            f"SIG {sig_text} CH {item.get('channel', 'n/a')}",
                            self._phy_tag(item),
                        ))
                    selected = self.selected_index - start
                return OpsPageView(
                    state=self.state,
                    menu_open=self.menu_open,
                    menu_title="KISMET MENU",
                    menu_items=self._menu_items(),
                    menu_index=self.menu_index,
                    lines=[
                        f"{self.browse_kind.upper()} {len(visible_devices)}",
                        f"SEL {min(self.selected_index + 1, total) if total else 0}/{total}",
                        f"WIFI AP {self.wifi_ap_count} DEV {self.wifi_device_count}",
                        f"BT {self.bt_device_count}",
                        f"DB {Path(self.db_path).name[:22] if self.db_path else 'n/a'}",
                    ],
                    list_rows=rows,
                    list_selected=selected,
                    list_hint=f"{len(self.devices)} found",
                )
            lines = [
                f"SVC {self.service_state.upper()}  PROC {'YES' if self.proc_running else 'NO'}",
                f"WEB {'OPEN' if self.port_open else 'CLOSED'}  {self.host}:{self.port}",
                f"WIFI SRC {(self.active_wifi_source or 'manual').upper()}  BT {(self.active_bt_source or 'off').upper()}",
                f"WIFI AP {self.wifi_ap_count}  DEV {self.wifi_device_count}",
                f"BT {self.bt_device_count}  FILES {self.capture_files}",
                f"LAST {_fmt_age(None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed))}",
            ]
            if self.selected_device:
                dev = self.selected_device
                lines.append("")
                lines.append(f"SEL {_clean(self._display_label(dev), 34)}")
                lines.append(f"PHY {self._phy_tag(dev)}  TYPE {_clean(str(dev.get('type', '?')), 18)}")
                sig = dev.get("signal")
                sig_text = "n/a" if sig is None else str(sig)
                lines.append(f"SIG {sig_text}  CH {_clean(str(dev.get('channel', 'n/a')), 10)}")
                lines.append(f"SEEN {_fmt_age(dev.get('last_seen_age_s'))}  MAC {_clean(str(dev.get('mac', '--'))[-8:], 8)}")
                manuf = _clean(str(dev.get("manufacturer", "")), 34)
                if manuf:
                    lines.append(f"MFG {manuf}")
            if self.latest_file_age_s is not None:
                lines.append(f"NEWEST {_fmt_age(self.latest_file_age_s)}")
            if self.source_lines:
                lines.append("")
                lines.append("SRC")
                for line in self.source_lines:
                    lines.append(_clean(line.replace("INFO:", "").replace("NOTICE:", "").strip(), 38))
            if self.warning_lines:
                lines.append("")
                lines.append("WARN")
                for line in self.warning_lines:
                    lines.append(_clean(line.replace("WARNING:", "").replace("ERROR:", "").strip(), 38))
            if self.latest_files:
                lines.append("")
                lines.append("FILES")
                lines.extend([f"> {name}" for name in self.latest_files[:4]])
            elif self.log_lines:
                lines.append("")
                lines.append("LOG")
                lines.extend([_clean(line, 38) for line in self.log_lines[:3]])
            elif self.last_error:
                lines.append(_clean(self.last_error, 38))
            return OpsPageView(
                state=self.state,
                menu_open=self.menu_open,
                menu_title="KISMET MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=lines,
                list_rows=[],
                list_selected=0,
                list_hint="",
            )

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            return {
                "state": self.state,
                "service_state": self.service_state,
                "proc_running": self.proc_running,
                "port_open": self.port_open,
                "host": self.host,
                "port": self.port,
                "capture_files": self.capture_files,
                "capture_breakdown": dict(self.capture_breakdown),
                "latest_files": list(self.latest_files),
                "latest_file_age_s": self.latest_file_age_s,
                "db_path": self.db_path,
                "device_count": len(self.devices),
                "devices": list(self.devices[:24]),
                "selected_index": self.selected_index,
                "selected_device": dict(self.selected_device) if self.selected_device else None,
                "wifi_ap_count": self.wifi_ap_count,
                "wifi_device_count": self.wifi_device_count,
                "bt_device_count": self.bt_device_count,
                "source_lines": list(self.source_lines),
                "active_wifi_source": self.active_wifi_source,
                "active_bt_source": self.active_bt_source,
                "warning_lines": list(self.warning_lines),
                "log_lines": list(self.log_lines),
                "primary_interface": self.primary_interface,
                "networkmanager_service": self.networkmanager_service,
                "last_error": self.last_error,
                "last_refresh_age_s": None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed),
                "last_refresh_duration_s": self.last_refresh_duration_s,
                "view": {
                    "state": view.state,
                    "menu_open": view.menu_open,
                    "menu_title": view.menu_title,
                    "menu_items": list(view.menu_items),
                    "menu_index": view.menu_index,
                    "lines": list(view.lines),
                    "list_rows": list(view.list_rows),
                    "list_selected": view.list_selected,
                    "list_hint": view.list_hint,
                },
            }


class NmapController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.iface = _clean(cfg.get("interface", "wlan0"), 24) or "wlan0"
        self.refresh_interval = max(20.0, float(cfg.get("refresh_interval_seconds", 60.0) or 60.0))
        self.top_ports = max(5, min(50, int(cfg.get("top_ports", 20) or 20)))
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.lock = threading.RLock()
        self.tool_ip = _resolve_tool("ip", ["/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip"])
        self.tool_nmap = _resolve_tool("nmap", ["/usr/bin/nmap"])
        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.selected_index = 0
        self.hosts: list[dict[str, Any]] = []
        self.service_lines: list[str] = []
        self.last_service_target = ""
        self.last_error = ""
        self.last_source = "none"
        self.last_refresh_completed = 0.0
        self.last_refresh_duration_s = 0.0

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.hosts:
                return "U/D host  OK menu  Y refresh"
            return "OK menu  Y refresh  L/R page"

    def _menu_items(self) -> list[str]:
        return ["Refresh Hosts", "Service Scan", "Clear Details", "Back"]

    def _run_cmd(self, args: list[str], timeout: float = 12.0) -> str:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        except Exception:
            return ""
        return result.stdout or ""

    def _scan_target(self) -> str:
        out = self._run_cmd([self.tool_ip, "-4", "-brief", "addr", "show", "dev", self.iface], timeout=3.0)
        for raw in out.splitlines():
            parts = raw.split()
            for token in parts[2:]:
                if "/" in token and token[0].isdigit():
                    ip = token.split("/", 1)[0]
                    if ip.count(".") == 3:
                        return ".".join(ip.split(".")[:3]) + ".0/24"
        return ""

    def _parse_ping_scan(self, text: str) -> list[dict[str, Any]]:
        hosts: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("Nmap scan report for "):
                if current:
                    hosts.append(current)
                target = line.split("for ", 1)[1]
                if "(" in target and target.endswith(")"):
                    host, _, rest = target.partition(" (")
                    ip = rest[:-1]
                    current = {"label": _clean(host, 32), "ip": _clean(ip, 32), "mac": "--"}
                else:
                    current = {"label": _clean(target, 32), "ip": _clean(target, 32), "mac": "--"}
                continue
            if line.startswith("MAC Address:") and current is not None:
                after = line.split("MAC Address:", 1)[1].strip()
                mac, _, vendor = after.partition("(")
                current["mac"] = _clean(mac, 32).lower()
                current["vendor"] = _clean(vendor.rstrip(")"), 48)
        if current:
            hosts.append(current)
        return hosts

    def refresh(self) -> None:
        started = time.monotonic()
        target = self._scan_target()
        if not target:
            with self.lock:
                self.last_error = "No scan target"
                self.last_source = "none"
                self.last_refresh_duration_s = max(0.0, time.monotonic() - started)
            self.redraw_cb()
            return
        out = self._run_cmd([self.tool_nmap, "-sn", target], timeout=14.0)
        hosts = self._parse_ping_scan(out)
        with self.lock:
            self.hosts = hosts[:32]
            self.selected_index = max(0, min(self.selected_index, len(self.hosts) - 1))
            self.last_error = "" if hosts else "No hosts found"
            self.last_source = target
            self.last_refresh_completed = time.monotonic()
            self.last_refresh_duration_s = max(0.0, self.last_refresh_completed - started)
        self.redraw_cb()

    def _service_scan(self) -> None:
        with self.lock:
            if not self.hosts:
                self.last_error = "No host selected"
                self.redraw_cb()
                return
            target = _clean(self.hosts[self.selected_index].get("ip", ""), 32)
        if not target:
            return
        out = self._run_cmd([self.tool_nmap, "-Pn", "--top-ports", str(self.top_ports), "--open", target], timeout=18.0)
        lines: list[str] = []
        for raw in out.splitlines():
            line = raw.strip()
            if "/tcp" in line or "/udp" in line:
                lines.append(_clean(line, 80))
        with self.lock:
            self.service_lines = lines[:10]
            self.last_service_target = target
            self.last_error = "" if lines else f"No open ports for {target}"
        self.redraw_cb()

    def tick(self, force: bool = False) -> None:
        with self.lock:
            stale = (time.monotonic() - self.last_refresh_completed) if self.last_refresh_completed else None
            menu_open = self.menu_open
        if menu_open:
            return
        if force or stale is None or stale >= self.refresh_interval:
            self.refresh()

    def move(self, delta: int) -> None:
        with self.lock:
            if self.menu_open:
                items = self._menu_items()
                self.menu_index = (self.menu_index + delta) % len(items)
            elif self.hosts:
                self.selected_index = max(0, min(self.selected_index + delta, len(self.hosts) - 1))
            else:
                return
        self.redraw_cb()

    def ok(self) -> None:
        with self.lock:
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False
        if item == "Refresh Hosts":
            self.refresh()
            self._set_status("Nmap refreshed", 4.0)
            return
        if item == "Service Scan":
            self._service_scan()
            self._set_status("Nmap service scan complete", 4.0)
            return
        if item == "Clear Details":
            with self.lock:
                self.service_lines = []
                self.last_service_target = ""
            self.redraw_cb()
            self._set_status("Nmap details cleared", 4.0)
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self.refresh()
        self._set_status("Nmap refreshed", 4.0)

    def back(self) -> bool:
        with self.lock:
            if self.menu_open:
                self.menu_open = False
                self.redraw_cb()
                return True
        return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open

    def remote_action(self, action: str) -> bool:
        cmd = _clean(action, 32).lower()
        if cmd == "nmap_refresh":
            self.secondary()
            return True
        if cmd == "nmap_services":
            self._service_scan()
            self._set_status("Nmap service scan complete", 4.0)
            return True
        if cmd == "nmap_clear":
            with self.lock:
                self.service_lines = []
                self.last_service_target = ""
            self.redraw_cb()
            self._set_status("Nmap details cleared", 4.0)
            return True
        return False

    def render_view(self) -> OpsPageView:
        with self.lock:
            rows: list[tuple[str, str, str, str]] = []
            total = len(self.hosts)
            selected = 0
            if total:
                start = max(0, min(self.selected_index - 1, max(0, total - 3)))
                visible = self.hosts[start : start + 3]
                for item in visible:
                    vendor = _clean(item.get("vendor", ""), 14) or "--"
                    rows.append(
                        (
                            _clean(item.get("label") or item.get("ip"), 16),
                            _clean(item.get("ip"), 15),
                            vendor,
                            "HOST",
                        )
                    )
                selected = self.selected_index - start
            lines = [
                f"IFACE {self.iface.upper()}",
                f"TARGET {self.last_source or 'n/a'}",
                f"HOSTS {len(self.hosts)}",
                f"LAST {_fmt_age(None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed))}",
            ]
            if total:
                item = self.hosts[self.selected_index]
                lines.append(f"SEL {_clean(item.get('label') or item.get('ip'), 20)}")
                lines.append(f"IP {_clean(item.get('ip'), 20)}")
                lines.append(f"MAC {_clean(item.get('mac', '--'), 20)}")
                lines.append(f"VND {_clean(item.get('vendor', 'n/a'), 20)}")
            if self.service_lines:
                lines.append("")
                if self.last_service_target:
                    lines.append(_clean(f"SRV {self.last_service_target}", 34))
                lines.extend([_clean(line, 34) for line in self.service_lines[:3]])
            elif self.last_error:
                lines.append(self.last_error[:28])
            return OpsPageView(
                state=self.state,
                menu_open=self.menu_open,
                menu_title="NMAP MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=lines,
                list_rows=rows,
                list_selected=selected,
                list_hint=f"{len(self.hosts)} hosts",
            )

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            selected_item = self.hosts[self.selected_index] if self.hosts else None
            return {
                "state": self.state,
                "iface": self.iface,
                "target": self.last_source,
                "hosts": copy_entries(self.hosts[:24]),
                "selected_index": self.selected_index,
                "selected_host": dict(selected_item) if selected_item else None,
                "service_lines": list(self.service_lines),
                "last_service_target": self.last_service_target,
                "last_error": self.last_error,
                "last_refresh_age_s": None if not self.last_refresh_completed else max(0.0, time.monotonic() - self.last_refresh_completed),
                "last_refresh_duration_s": self.last_refresh_duration_s,
                "view": {
                    "state": view.state,
                    "menu_open": view.menu_open,
                    "menu_title": view.menu_title,
                    "menu_items": list(view.menu_items),
                    "menu_index": view.menu_index,
                    "lines": list(view.lines),
                    "list_rows": list(view.list_rows),
                    "list_selected": view.list_selected,
                    "list_hint": view.list_hint,
                },
            }
