from __future__ import annotations

import os
import socket
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .foxhunt import _clean


def _local_host_label() -> str:
    try:
        host = socket.gethostname().strip()
    except Exception:
        host = ""
    return _short_name(host, 18) or "local"


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
    services: list[str] = field(default_factory=list)


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
        self.top_ports = max(5, min(50, int(cfg.get("top_ports", 20) or 20)))
        self.scan_progress_current = 0
        self.scan_progress_total = 0
        self.scan_progress_label = "idle"
        self.scan_thread: threading.Thread | None = None

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
            if self.state == "scanning":
                return "Scanning...  L back"
            if self.state == "detail" and self.entries:
                return "U/D device  OK menu  L back"
            return "OK menu  Y refresh  L/R page"

    def _menu_items(self) -> list[str]:
        if self.state == "detail":
            return ["Refresh", "Exit"]
        return ["Light the Way", "Back"]

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
            return _local_host_label()
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
            return _local_host_label()
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
                [self.tool_nmap, "-sn", "-n", "--max-retries", "0", "--host-timeout", "1500ms", target],
                capture_output=True,
                text=True,
                timeout=6.0,
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

    def _scan_host_services(self, ip: str) -> list[str]:
        safe_ip = _clean(ip, 32)
        if not safe_ip or safe_ip.count(".") != 3:
            return []
        try:
            result = self._run_cmd(
                [
                    self.tool_nmap,
                    "-Pn",
                    "-n",
                    "-T4",
                    "--max-retries",
                    "1",
                    "--host-timeout",
                    "4s",
                    "--top-ports",
                    str(self.top_ports),
                    "--open",
                    safe_ip,
                ],
                timeout=8.0,
            )
        except Exception:
            return []
        lines: list[str] = []
        for raw in (result.stdout or "").splitlines():
            line = raw.strip()
            if "/tcp" in line or "/udp" in line:
                lines.append(_clean(line, 80))
        return lines[:8]

    def _start_scan(self) -> bool:
        with self.lock:
            if self.scan_thread is not None and self.scan_thread.is_alive():
                self._set_status("Lantern already scanning", 4.0)
                return False
            self.state = "scanning"
            self.menu_open = False
            self.menu_index = 0
            self.scan_progress_current = 0
            self.scan_progress_total = 1
            self.scan_progress_label = "Discovering hosts"
            worker = threading.Thread(target=self._scan_worker, daemon=True)
            self.scan_thread = worker
        self.redraw_cb()
        worker.start()
        return True

    def _scan_worker(self) -> None:
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
                self.state = "idle"
                self.scan_progress_current = 0
                self.scan_progress_total = 0
                self.scan_progress_label = "scan failed"
            self.redraw_cb()
            return

        entries = self._parse_ip_neigh(neigh.stdout or "")
        source = "ip-neigh"
        if not entries:
            entries = self._parse_proc_arp()
            source = "proc-arp" if entries else "none"
        with self.lock:
            self.scan_progress_current = 1
            self.scan_progress_total = 2
            self.scan_progress_label = "Fast discovery complete"
        self.redraw_cb()

        if not entries:
            with self.lock:
                self.scan_progress_current = 1
                self.scan_progress_total = 2
                self.scan_progress_label = "Deep discovery"
            self.redraw_cb()
            active_entries = self._run_active_scan()
            if active_entries:
                entries = self._merge_entries(entries, active_entries)
                source = "nmap" if source == "none" else f"{source}+nmap"
                self.last_active_scan_completed = time.monotonic()

        entries = self._sort_entries(entries)
        total_hosts = len(entries)
        with self.lock:
            self.scan_progress_current = 0
            self.scan_progress_total = max(1, 1 + total_hosts)
            self.scan_progress_label = f"Scanning services 0/{total_hosts}"
        self.redraw_cb()

        for idx, entry in enumerate(entries, start=1):
            entry.services = self._scan_host_services(entry.ip)
            with self.lock:
                self.scan_progress_current = 1 + idx
                self.scan_progress_total = max(1, 1 + total_hosts)
                self.scan_progress_label = f"{entry.ip} {idx}/{total_hosts}"
            self.redraw_cb()

        with self.lock:
            previous_ip = ""
            if self.entries:
                current = self._selected_entry()
                if current is not None:
                    previous_ip = current.ip
            self.entries = entries
            self.selected_index = max(0, min(self.selected_index, len(self.entries) - 1))
            if previous_ip:
                for idx, item in enumerate(self.entries):
                    if item.ip == previous_ip:
                        self.selected_index = idx
                        break
            self.last_error = "" if entries else "No local hosts discovered"
            self.local_ip = local_ip
            self.gateway = gateway
            self.last_source = source
            self.last_refresh_started = started
            self.last_refresh_completed = time.monotonic()
            self.last_refresh_duration_s = max(0.0, self.last_refresh_completed - started)
            self.state = "detail" if self.entries else "idle"
            self.scan_progress_current = self.scan_progress_total
            self.scan_progress_label = "Complete"
        self.redraw_cb()
        self._set_status(f"Lantern lit {len(entries)} hosts", 4.0 if entries else 5.0)

    def refresh(self, force_active: bool = False) -> None:
        self._start_scan()

    def tick(self, force: bool = False) -> None:
        with self.lock:
            if self.menu_open or self.state == "scanning":
                return
            current_ip = self.local_ip
            current_gateway = self.gateway
        next_ip = self._iface_ip()
        next_gateway = self._gateway_ip()
        if force or next_ip != current_ip or next_gateway != current_gateway:
            with self.lock:
                self.local_ip = next_ip
                self.gateway = next_gateway
                if self.last_source == "none":
                    self.last_source = "passive"
            self.redraw_cb()
        return

    def move(self, delta: int) -> None:
        with self.lock:
            if self.menu_open:
                items = self._menu_items()
                self.menu_index = (self.menu_index + delta) % len(items)
            elif self.state == "detail" and self.entries:
                self.selected_index = max(0, min(self.selected_index + delta, len(self.entries) - 1))
            else:
                return
        self.redraw_cb()

    def ok(self) -> None:
        with self.lock:
            if self.state == "scanning":
                return
            if not self.menu_open:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return
            item = self._menu_items()[self.menu_index]
            self.menu_open = False

        choice = _clean(item, 32).lower()
        if choice in ("light the way", "refresh"):
            self._start_scan()
            return
        if choice == "exit":
            with self.lock:
                self.state = "idle"
            self.redraw_cb()
            return
        self.redraw_cb()

    def secondary(self) -> None:
        self._start_scan()

    def back(self) -> bool:
        with self.lock:
            if self.state == "detail" and not self.menu_open:
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
            return self.menu_open or self.state == "scanning"

    def remote_action(self, action: str) -> bool:
        cmd = _clean(action, 32).lower()
        if cmd == "lantern_refresh":
            self._start_scan()
            return True
        if cmd == "lantern_clear":
            with self.lock:
                self.menu_open = False
                self.state = "idle"
            self.redraw_cb()
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
                    "services": list(selected.services),
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
                        "services": list(item.services[:8]),
                    }
                    for item in self.entries[:24]
                ],
                "selected_host": selected_payload,
                "local_ip": self.local_ip,
                "gateway": self.gateway,
                "scan_progress_current": self.scan_progress_current,
                "scan_progress_total": self.scan_progress_total,
                "scan_progress_label": self.scan_progress_label,
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
            live_count = sum(1 for item in self.entries if item.state in ("reachable", "stale", "delay", "probe", "arp", "live"))

            if self.state == "scanning":
                total_steps = max(1, int(self.scan_progress_total or 1))
                current = max(0, min(int(self.scan_progress_current or 0), total_steps))
                fill = max(0, min(12, int(round((current / total_steps) * 12))))
                bar = "[" + ("#" * fill) + ("." * (12 - fill)) + "]"
                lines = [
                    "LIGHTING THE WAY",
                    f"PROGRESS {current}/{total_steps}",
                    bar,
                    _clean(self.scan_progress_label, 32),
                    f"IFACE {self.iface.upper()}",
                    f"SELF {self.local_ip or 'n/a'}",
                    f"GW {self.gateway or 'n/a'}",
                ]
            elif self.state == "detail" and total:
                selected_entry = self._selected_entry()
                lines = [
                    f"{self.selected_index + 1}/{total}  {selected_entry.ip}",
                    f"NAME {self._display_name(selected_entry)}",
                    f"MAC {selected_entry.mac or '--'}",
                    f"{self._state_label(selected_entry.state)} / {(_clean(selected_entry.vendor, 20) or 'n/a')}",
                ]
                if selected_entry.services:
                    port_tokens: list[str] = []
                    for raw in selected_entry.services:
                        parts = raw.split()
                        if not parts:
                            continue
                        service = parts[2] if len(parts) >= 3 else ""
                        token = parts[0] if not service else f"{parts[0]}/{service}"
                        port_tokens.append(_clean(token, 20))
                    current_line = "PORTS "
                    for token in port_tokens:
                        candidate = token if current_line == "PORTS " else f"{current_line}, {token}"
                        if len(candidate) <= 34:
                            current_line = candidate
                        else:
                            lines.append(current_line)
                            current_line = token
                    if current_line:
                        lines.append(current_line)
                else:
                    lines.append("PORTS none")
            else:
                lines = [
                    f"IFACE {self.iface.upper()}  IP {self.local_ip or 'n/a'}",
                    f"HOSTS {len(self.entries)}  LIVE {live_count}",
                    f"GW {self.gateway or 'n/a'}",
                    f"SRC {self.last_source.upper()}  LAST {self._age_text()}",
                    "",
                    "OK menu",
                    "Light the Way",
                ]
                if self.last_error:
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
