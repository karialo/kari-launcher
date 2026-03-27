from __future__ import annotations

import os
import shutil
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


def _short_name(name: str, max_len: int = 14) -> str:
    safe = _clean(name, 64)
    if not safe:
        return ""
    short = safe.split(".", 1)[0]
    return short[:max_len]


@dataclass
class HostEntry:
    ip: str
    mac: str
    state: str
    hostname: str
    vendor: str
    source: str
    last_seen_ts: float


@dataclass
class LanternView:
    state: str
    menu_open: bool
    menu_title: str
    menu_items: list[str] = field(default_factory=list)
    menu_index: int = 0
    lines: list[str] = field(default_factory=list)
    list_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    list_selected: int = 0
    list_hint: str = ""


class LanternController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.iface = _clean(cfg.get("interface", "wlan0"), 24) or "wlan0"
        self.refresh_interval = max(8.0, float(cfg.get("refresh_interval_seconds", 20.0) or 20.0))
        self.visible_rows = 5
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.lock = threading.RLock()

        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.selected_index = 0
        self.entries: list[HostEntry] = []
        self.last_error = ""
        self.last_refresh_started = 0.0
        self.last_refresh_completed = 0.0
        self.last_refresh_duration_s = 0.0
        self.local_ip = ""
        self.gateway = ""
        self.last_source = "none"
        self.name_cache: dict[str, tuple[str, float]] = {}
        self.name_cache_ttl = 600.0
        self.active_scan_interval = max(45.0, float(cfg.get("active_scan_interval_seconds", 90.0) or 90.0))
        self.last_active_scan_completed = 0.0
        self.tool_ip = self._resolve_tool("ip", ["/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip"])
        self.tool_avahi = self._resolve_tool("avahi-resolve-address", ["/usr/bin/avahi-resolve-address"])
        self.tool_getent = self._resolve_tool("getent", ["/usr/bin/getent"])
        self.tool_nmap = self._resolve_tool("nmap", ["/usr/bin/nmap"])

    def _resolve_tool(self, name: str, fallbacks: list[str]) -> str:
        found = shutil.which(name)
        if found:
            return found
        for path in fallbacks:
            if Path(path).exists():
                return path
        return name

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.entries:
                return "U/D host  OK menu  Y refresh"
            return "OK menu  Y refresh  L/R page"

    def _menu_items(self) -> list[str]:
        return ["Refresh Data", "Clear Cache", "Back"]

    def _run_cmd(self, args: list[str], timeout: float = 6.0) -> subprocess.CompletedProcess[str]:
        cmd = list(args)
        if cmd and cmd[0] == "ip":
            cmd[0] = self.tool_ip
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

    def _iface_ip(self) -> str:
        try:
            result = self._run_cmd(["ip", "-4", "-brief", "addr", "show", "dev", self.iface], timeout=3.0)
        except Exception:
            return ""
        for raw in (result.stdout or "").splitlines():
            parts = raw.split()
            for token in parts[2:]:
                if "/" in token and token[0].isdigit():
                    return _clean(token.split("/", 1)[0], 32)
        return ""

    def _lookup_hostname_once(self, ip: str) -> str:
        if ip == self.gateway and self.gateway:
            return "gateway"
        if ip == self.local_ip and self.local_ip:
            return "kari"
        if self.tool_avahi and Path(self.tool_avahi).exists():
            try:
                result = subprocess.run(
                    [self.tool_avahi, ip],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if result.returncode == 0:
                    out = _clean(result.stdout, 96)
                    parts = out.split()
                    if len(parts) >= 2:
                        return _short_name(parts[-1], 18)
            except Exception:
                pass
        if self.tool_getent and Path(self.tool_getent).exists():
            try:
                result = subprocess.run(
                    [self.tool_getent, "hosts", ip],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if result.returncode == 0:
                    for raw in (result.stdout or "").splitlines():
                        parts = raw.split()
                        if len(parts) >= 2:
                            return _short_name(parts[1], 18)
            except Exception:
                pass
        return ""

    def _resolve_hostname(self, ip: str) -> str:
        now = time.monotonic()
        cached = self.name_cache.get(ip)
        if cached and (now - cached[1]) < self.name_cache_ttl:
            return cached[0]
        name = self._lookup_hostname_once(ip)
        self.name_cache[ip] = (name, now)
        return name

    def _display_name(self, item: HostEntry) -> str:
        if item.hostname:
            return _short_name(item.hostname, 16)
        if item.ip == self.gateway and self.gateway:
            return "gateway"
        if item.ip == self.local_ip and self.local_ip:
            return "kari"
        if item.vendor:
            vendor = item.vendor
            for suffix in (" Technologies", " Limited", " BV", " A.S.", " Inc.", " LLC"):
                if vendor.endswith(suffix):
                    vendor = vendor[: -len(suffix)]
                    break
            short = _short_name(vendor, 16)
            if short:
                return short
        return _clean(item.ip, 32)

    def _state_label(self, state: str) -> str:
        value = _clean(state, 16).lower()
        mapping = {
            "reachable": "LIVE",
            "stale": "SEEN",
            "delay": "SEEN",
            "probe": "PROBE",
            "arp": "ARP",
            "incomplete": "WAIT",
            "failed": "MISS",
            "permanent": "SAVE",
            "noarp": "NOARP",
        }
        return mapping.get(value, value[:4].upper() or "UNK")

    def _gateway_ip(self) -> str:
        try:
            result = self._run_cmd(["ip", "route", "show", "default", "dev", self.iface], timeout=3.0)
        except Exception:
            return ""
        for raw in (result.stdout or "").splitlines():
            parts = raw.split()
            for idx, token in enumerate(parts):
                if token == "via" and idx + 1 < len(parts):
                    return _clean(parts[idx + 1], 32)
        return ""

    def _parse_ip_neigh(self, text: str) -> list[HostEntry]:
        now = time.time()
        entries: list[HostEntry] = []
        seen: set[str] = set()
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split()
            ip = _clean(parts[0], 32)
            if not ip or ip in seen or not ip[0].isdigit():
                continue
            mac = ""
            state = ""
            source = "ip-neigh"
            if "lladdr" in parts:
                idx = parts.index("lladdr")
                if idx + 1 < len(parts):
                    mac = _clean(parts[idx + 1], 32).lower()
            if parts:
                state = _clean(parts[-1], 16).lower()
            entries.append(
                HostEntry(
                    ip=ip,
                    mac=mac or "--",
                    state=state or "unknown",
                    hostname=self._resolve_hostname(ip),
                    vendor="",
                    source=source,
                    last_seen_ts=now,
                )
            )
            seen.add(ip)
        return entries

    def _parse_proc_arp(self) -> list[HostEntry]:
        arp_path = Path("/proc/net/arp")
        if not arp_path.exists():
            return []
        now = time.time()
        entries: list[HostEntry] = []
        seen: set[str] = set()
        try:
            lines = arp_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return entries
        for raw in lines[1:]:
            parts = raw.split()
            if len(parts) < 6:
                continue
            ip, _, _, mac, _, iface = parts[:6]
            if iface != self.iface:
                continue
            ip = _clean(ip, 32)
            if not ip or ip in seen:
                continue
            entries.append(
                HostEntry(
                    ip=ip,
                    mac=_clean(mac, 32).lower(),
                    state="arp",
                    hostname=self._resolve_hostname(ip),
                    vendor="",
                    source="proc-arp",
                    last_seen_ts=now,
                )
            )
            seen.add(ip)
        return entries

    def _sort_entries(self, entries: list[HostEntry]) -> list[HostEntry]:
        def key(item: HostEntry) -> tuple[int, list[int] | str]:
            try:
                ip_key: list[int] | str = [int(part) for part in item.ip.split(".")]
            except Exception:
                ip_key = item.ip
            score = 0 if item.state in ("reachable", "stale", "delay", "probe", "arp") else 1
            return (score, ip_key)

        return sorted(entries, key=key)

    def _nmap_targets(self) -> str:
        source_ip = self.local_ip or self.gateway
        if source_ip and source_ip.count(".") == 3:
            parts = source_ip.split(".")
            return ".".join(parts[:3]) + ".0/24"
        return ""

    def _parse_nmap_ping_scan(self, text: str) -> list[HostEntry]:
        now = time.time()
        entries: list[HostEntry] = []
        current_ip = ""
        current_mac = ""
        current_vendor = ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("Nmap scan report for "):
                if current_ip:
                    entries.append(
                        HostEntry(
                            ip=current_ip,
                            mac=current_mac or "--",
                            state="live",
                            hostname=self._resolve_hostname(current_ip),
                            vendor=_clean(current_vendor, 48),
                            source="nmap",
                            last_seen_ts=now,
                        )
                    )
                current_ip = _clean(line.split()[-1], 32)
                current_mac = ""
                current_vendor = ""
                continue
            if line.startswith("MAC Address:"):
                after = line.split("MAC Address:", 1)[1].strip()
                mac, _, vendor = after.partition("(")
                current_mac = _clean(mac, 32).lower()
                current_vendor = _clean(vendor.rstrip(")"), 48)
        if current_ip:
            entries.append(
                HostEntry(
                    ip=current_ip,
                    mac=current_mac or "--",
                    state="live",
                    hostname=self._resolve_hostname(current_ip),
                    vendor=_clean(current_vendor, 48),
                    source="nmap",
                    last_seen_ts=now,
                )
            )
        return entries

    def _run_active_scan(self) -> list[HostEntry]:
        target = self._nmap_targets()
        if not target or not self.tool_nmap:
            return []
        try:
            result = subprocess.run(
                [self.tool_nmap, "-sn", target],
                capture_output=True,
                text=True,
                timeout=12.0,
                check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return self._parse_nmap_ping_scan(result.stdout or "")

    def _merge_entries(self, base: list[HostEntry], extra: list[HostEntry]) -> list[HostEntry]:
        merged: dict[str, HostEntry] = {item.ip: item for item in base}
        for item in extra:
            existing = merged.get(item.ip)
            if existing is None:
                merged[item.ip] = item
                continue
            if (not existing.mac or existing.mac == "--") and item.mac:
                existing.mac = item.mac
            if not existing.hostname and item.hostname:
                existing.hostname = item.hostname
            if not existing.vendor and item.vendor:
                existing.vendor = item.vendor
            if existing.state in ("unknown", "failed", "incomplete") and item.state:
                existing.state = item.state
            existing.last_seen_ts = max(existing.last_seen_ts, item.last_seen_ts)
        return list(merged.values())

    def refresh(self, force_active: bool = False) -> None:
        started = time.monotonic()
        local_ip = self._iface_ip()
        gateway = self._gateway_ip()
        self.local_ip = local_ip
        self.gateway = gateway
        try:
            neigh = self._run_cmd(["ip", "neigh", "show", "dev", self.iface], timeout=5.0)
        except Exception as exc:
            with self.lock:
                self.last_error = _clean(exc, 80)
                self.last_refresh_duration_s = max(0.0, time.monotonic() - started)
            self.redraw_cb()
            return

        entries = self._parse_ip_neigh(neigh.stdout or "")
        source = "ip-neigh"
        if not entries:
            entries = self._parse_proc_arp()
            source = "proc-arp" if entries else "none"
        active_due = (time.monotonic() - self.last_active_scan_completed) >= self.active_scan_interval
        if force_active or active_due or not entries:
            active_entries = self._run_active_scan()
            if active_entries:
                entries = self._merge_entries(entries, active_entries)
                source = "nmap" if source == "none" else f"{source}+nmap"
                self.last_active_scan_completed = time.monotonic()

        with self.lock:
            self.entries = self._sort_entries(entries)
            self.selected_index = max(0, min(self.selected_index, len(self.entries) - 1))
            self.last_error = "" if entries else "No local hosts discovered"
            self.local_ip = local_ip
            self.gateway = gateway
            self.last_source = source
            self.last_refresh_started = started
            self.last_refresh_completed = time.monotonic()
            self.last_refresh_duration_s = max(0.0, self.last_refresh_completed - started)
        self.redraw_cb()

    def tick(self, force: bool = False) -> None:
        with self.lock:
            if self.menu_open:
                return
            stale = (time.monotonic() - self.last_refresh_completed) if self.last_refresh_completed else None
        if force or stale is None or stale >= self.refresh_interval:
            self.refresh(force_active=force)

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
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False

        choice = _clean(item, 32).lower()
        if choice == "refresh data":
            self.refresh(force_active=True)
            self._set_status("Lantern refreshed", 4.0)
            return
        if choice == "clear cache":
            with self.lock:
                self.entries = []
                self.selected_index = 0
                self.last_error = ""
            self.redraw_cb()
            self._set_status("Lantern cache cleared", 4.0)
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self.refresh(force_active=True)
        self._set_status("Lantern refreshed", 4.0)

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
        if cmd == "lantern_refresh":
            self.secondary()
            return True
        if cmd == "lantern_clear":
            with self.lock:
                self.entries = []
                self.selected_index = 0
                self.last_error = ""
                self.menu_open = False
            self.redraw_cb()
            self._set_status("Lantern cache cleared", 4.0)
            return True
        return False

    def _age_text(self) -> str:
        if not self.last_refresh_completed:
            return "n/a"
        return _fmt_age(max(0.0, time.monotonic() - self.last_refresh_completed))

    def _selected_entry(self) -> HostEntry | None:
        if not self.entries:
            return None
        idx = max(0, min(self.selected_index, len(self.entries) - 1))
        return self.entries[idx]

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            selected = self._selected_entry()
            selected_payload = None
            if selected is not None:
                selected_payload = {
                    "label": self._display_name(selected),
                    "ip": selected.ip,
                    "mac": selected.mac,
                    "state": selected.state,
                    "hostname": selected.hostname,
                    "vendor": selected.vendor,
                    "source": selected.source,
                }
            return {
                "state": self.state,
                "iface": self.iface,
                "menu_open": self.menu_open,
                "menu_index": self.menu_index,
                "selected_index": self.selected_index,
                "host_count": len(self.entries),
                "entries": [
                    {
                        "label": self._display_name(item),
                        "ip": item.ip,
                        "mac": item.mac,
                        "state": item.state,
                        "hostname": item.hostname,
                        "vendor": item.vendor,
                        "source": item.source,
                    }
                    for item in self.entries[:24]
                ],
                "selected_host": selected_payload,
                "local_ip": self.local_ip,
                "gateway": self.gateway,
                "last_error": self.last_error,
                "last_source": self.last_source,
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

    def render_view(self) -> LanternView:
        with self.lock:
            rows: list[tuple[str, str, str, str]] = []
            selected = 0
            total = len(self.entries)
            if total:
                start = max(0, min(self.selected_index - 1, max(0, total - 3)))
                visible = self.entries[start : start + 3]
                for item in visible:
                    rows.append(
                        (
                            self._display_name(item),
                            _clean(item.ip, 15),
                            _clean(item.mac, 17),
                            self._state_label(item.state),
                        )
                    )
                selected = self.selected_index - start
            live_count = sum(1 for item in self.entries if item.state in ("reachable", "stale", "delay", "probe", "arp"))
            lines = [
                f"IFACE {self.iface.upper()}  IP {self.local_ip or 'n/a'}",
                f"HOSTS {len(self.entries)}  LIVE {live_count}",
                f"GW {self.gateway or 'n/a'}",
                f"SRC {self.last_source.upper()}  LAST {self._age_text()}",
            ]
            selected_entry = self._selected_entry()
            if selected_entry is not None:
                lines.append(
                    f"SEL {self._display_name(selected_entry)}"
                )
                lines.append(
                    f"STATE {self._state_label(selected_entry.state)}"
                )
                lines.append(
                    f"MAC {selected_entry.mac or '--'}"
                )
            elif self.last_error:
                lines.append(self.last_error[:28])
            return LanternView(
                state=self.state,
                menu_open=self.menu_open,
                menu_title="LANTERN MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=lines,
                list_rows=rows,
                list_selected=selected,
                list_hint=f"{len(self.entries)} hosts",
            )
