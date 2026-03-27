from __future__ import annotations

import csv
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .foxhunt import ScanEntry, TargetInfo, _clean, _freq_to_channel, _trim_ssid


@dataclass
class AOView:
    state: str
    menu_open: bool
    menu_title: str
    menu_items: list[str] = field(default_factory=list)
    menu_index: int = 0
    lines: list[str] = field(default_factory=list)
    list_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    list_selected: int = 0
    list_hint: str = ""


class AngryOxideMenuController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
        launch_cb: Callable[[str, str | None, str | None], None],
        stop_cb: Callable[[], None],
        gpsd_cb: Callable[[], str | None],
        toggle_log_cb: Callable[[], None],
        iface_choices_cb: Callable[[], list[str]] | None = None,
        set_iface_cb: Callable[[str], bool] | None = None,
        reset_iface_cb: Callable[[str, str], bool] | None = None,
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.iface = _clean(cfg.get("interface", "wlan1"), 24) or "wlan1"
        self.scan_max_results = max(8, min(64, int(cfg.get("scan_max_results", 32) or 32)))
        self.scan_interval = max(2.0, float(cfg.get("scan_interval_active_seconds", 4.0) or 4.0))
        self.visible_rows = 5
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.launch_cb = launch_cb
        self.stop_cb = stop_cb
        self.gpsd_cb = gpsd_cb
        self.toggle_log_cb = toggle_log_cb
        self.iface_choices_cb = iface_choices_cb
        self.set_iface_cb = set_iface_cb
        self.reset_iface_cb = reset_iface_cb
        self.lock = threading.RLock()

        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.scan_results: list[ScanEntry] = []
        self.selected_index = 0
        self.pending_target: TargetInfo | None = None
        self.pending_scope = "all"
        self.last_error = ""
        self.last_scan_started = 0.0
        self.last_scan_completed = 0.0
        self.scan_reset_attempted = False
        self.tool_ip = self._resolve_tool("ip", ["/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip"])
        self.tool_iw = self._resolve_tool("iw", ["/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw"])
        self.tool_sudo = self._resolve_tool("sudo", ["/usr/bin/sudo", "/bin/sudo"])
        self.tool_timeout = self._resolve_tool("timeout", ["/usr/bin/timeout", "/bin/timeout"])
        self.tool_airodump = self._resolve_tool("airodump-ng", ["/usr/sbin/airodump-ng", "/usr/bin/airodump-ng", "/bin/airodump-ng"])

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
            if self.state == "scan":
                return "U/D sel  OK lock  L back"
            if self.state == "iface":
                return "U/D iface  OK pick  L back"
            if self.state == "profile":
                return "OK start  L back"
            return "OK menu  K2 log  L/R page"

    def _iface_menu(self) -> list[str]:
        if self.iface_choices_cb is None:
            return ["Back"]
        items = [item for item in self.iface_choices_cb() if _clean(item, 32)]
        return items + ["Back"] if items else ["Back"]

    def _run_cmd(self, args: list[str], timeout: float = 8.0, privileged: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = list(args)
        if cmd:
            if cmd[0] == "ip":
                cmd[0] = self.tool_ip
            elif cmd[0] == "iw":
                cmd[0] = self.tool_iw
            elif cmd[0] == "sudo":
                cmd[0] = self.tool_sudo
        if privileged and os.geteuid() != 0 and self.tool_sudo:
            cmd = [self.tool_sudo, "-n"] + cmd
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

    def _iface_mode(self) -> str:
        try:
            result = self._run_cmd(["iw", "dev", self.iface, "info"], timeout=2.5, privileged=False)
        except Exception:
            return ""
        for raw in (result.stdout or "").splitlines():
            line = raw.strip()
            if line.startswith("type "):
                return _clean(line.split(None, 1)[1], 16).lower()
        return ""

    def _prepare_scan_iface(self) -> str:
        original_mode = self._iface_mode()
        if original_mode == "managed":
            try:
                self._run_cmd(["ip", "link", "set", self.iface, "up"], timeout=4.0, privileged=True)
            except Exception:
                pass
            return original_mode
        for cmd in (
            ["ip", "link", "set", self.iface, "down"],
            ["iw", "dev", self.iface, "set", "type", "managed"],
            ["ip", "link", "set", self.iface, "up"],
        ):
            try:
                result = self._run_cmd(cmd, timeout=5.0, privileged=True)
            except Exception as exc:
                self.last_error = _clean(exc, 80)
                return ""
            if result.returncode != 0:
                self.last_error = _clean(result.stderr or result.stdout or "failed", 80)
                return ""
        time.sleep(0.35)
        return original_mode

    def _restore_scan_iface(self, original_mode: str) -> None:
        mode = _clean(original_mode, 16).lower()
        if not mode or mode == "managed":
            return
        for cmd in (
            ["ip", "link", "set", self.iface, "down"],
            ["iw", "dev", self.iface, "set", "type", mode],
            ["ip", "link", "set", self.iface, "up"],
        ):
            try:
                self._run_cmd(cmd, timeout=5.0, privileged=True)
            except Exception:
                return
        time.sleep(0.25)

    def _parse_scan_output(self, text: str) -> list[ScanEntry]:
        results: list[ScanEntry] = []
        current: list[str] = []
        blocks: list[list[str]] = []
        now = time.time()
        for line in text.splitlines():
            if line.startswith("BSS "):
                if current:
                    blocks.append(current)
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append(current)

        for block in blocks:
            try:
                bssid = _clean(block[0].split()[1].split("(")[0], 32).lower()
            except Exception:
                continue
            ssid = ""
            rssi = None
            freq = None
            channel = None
            last_seen = None
            security = "open"
            for raw in block[1:]:
                line = raw.strip()
                if line.startswith("SSID:"):
                    ssid = _clean(line.split(":", 1)[1], 64)
                elif line.startswith("signal:"):
                    try:
                        rssi = int(round(float(line.split(":", 1)[1].split()[0])))
                    except Exception:
                        pass
                elif line.startswith("freq:"):
                    try:
                        freq = int(float(line.split(":", 1)[1].strip()))
                    except Exception:
                        pass
                elif "DS Parameter set: channel" in line:
                    try:
                        channel = int(line.rsplit(" ", 1)[-1])
                    except Exception:
                        pass
                elif "primary channel:" in line:
                    try:
                        channel = int(line.rsplit(":", 1)[-1].strip())
                    except Exception:
                        pass
                elif line.startswith("last seen:"):
                    try:
                        last_seen = float(line.split(":", 1)[1].split()[0]) / 1000.0
                    except Exception:
                        pass
                elif line.startswith("RSN:") or line.startswith("WPA:"):
                    security = "enc"
                elif line.startswith("capability:") and "Privacy" in line and security == "open":
                    security = "enc"
            if channel is None:
                channel = _freq_to_channel(freq)
            results.append(
                ScanEntry(
                    ssid=_trim_ssid(ssid or "<hidden>", 20),
                    bssid=bssid,
                    channel=channel,
                    rssi=rssi,
                    last_seen_s=last_seen,
                    security=security,
                    seen_ts=now,
                )
            )
        results.sort(key=lambda item: (-999 if item.rssi is None else item.rssi), reverse=True)
        return results[: self.scan_max_results]

    def _prepare_monitor_scan_iface(self) -> bool:
        for cmd in (
            ["ip", "link", "set", self.iface, "down"],
            ["iw", "dev", self.iface, "set", "type", "monitor"],
            ["ip", "link", "set", self.iface, "up"],
        ):
            try:
                result = self._run_cmd(cmd, timeout=5.0, privileged=True)
            except Exception as exc:
                self.last_error = _clean(exc, 80)
                return False
            if result.returncode != 0:
                self.last_error = _clean(result.stderr or result.stdout or "failed", 80)
                return False
        time.sleep(0.5)
        return True

    def _parse_airodump_csv(self, text: str) -> list[ScanEntry]:
        results: list[ScanEntry] = []
        if not text:
            return results
        now = time.time()
        seen: set[str] = set()
        for row in csv.reader(text.splitlines()):
            if not row:
                if results:
                    break
                continue
            if row[0].strip() in ("BSSID", "Station MAC"):
                continue
            if len(row) < 14:
                continue
            bssid = _clean(row[0], 32).lower()
            if not bssid or bssid == "bssid" or bssid in seen:
                continue
            seen.add(bssid)
            try:
                channel = int(row[3].strip())
            except Exception:
                channel = None
            try:
                rssi = int(row[8].strip())
            except Exception:
                rssi = None
            security = "open" if _clean(row[5], 16).upper() == "OPN" else "enc"
            results.append(
                ScanEntry(
                    ssid=_trim_ssid(_clean(row[13], 64) or "<hidden>", 20),
                    bssid=bssid,
                    channel=channel,
                    rssi=rssi,
                    last_seen_s=0.0,
                    security=security,
                    seen_ts=now,
                )
            )
        results.sort(key=lambda item: (-999 if item.rssi is None else item.rssi), reverse=True)
        return results[: self.scan_max_results]

    def _scan_airodump(self) -> list[ScanEntry]:
        if not self.tool_airodump:
            return []
        if not self._prepare_monitor_scan_iface():
            return []
        prefix = f"/tmp/launcher-ao-scan-{os.getpid()}-{int(time.time() * 1000)}"
        csv_path = Path(f"{prefix}-01.csv")
        cmd = [
            self.tool_timeout,
            "8",
            self.tool_airodump,
            "--band",
            "abg",
            "--write-interval",
            "1",
            "--output-format",
            "csv",
            "-w",
            prefix,
            self.iface,
        ]
        if os.geteuid() != 0 and self.tool_sudo:
            cmd = [self.tool_sudo, "-n"] + cmd
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10.0,
                check=False,
            )
        except Exception as exc:
            self.last_error = _clean(exc, 80)
            return []
        try:
            csv_text = csv_path.read_text(encoding="utf-8", errors="replace") if csv_path.exists() else ""
        except Exception:
            csv_text = ""
        finally:
            for suffix in ("-01.csv", "-01.kismet.csv", "-01.kismet.netxml", "-01.log.csv", "-01.cap"):
                try:
                    Path(f"{prefix}{suffix}").unlink(missing_ok=True)
                except Exception:
                    pass
        if not csv_text and result.returncode not in (0, 124):
            self.last_error = "airodump scan failed"
            return []
        return self._parse_airodump_csv(csv_text)

    def _scan_once(self) -> list[ScanEntry]:
        original_mode = self._prepare_scan_iface()
        if not original_mode and self._iface_mode() != "managed":
            return []
        try:
            result = self._run_cmd(["iw", "dev", self.iface, "scan", "ap-force"], timeout=8.0, privileged=True)
        except Exception as exc:
            self.last_error = _clean(exc, 80)
            return []
        if result.returncode == 0:
            parsed = self._parse_scan_output(result.stdout)
            if parsed:
                self._restore_scan_iface(original_mode)
                self.last_error = ""
                self.last_scan_completed = time.monotonic()
                return parsed
        else:
            self.last_error = _clean(result.stderr or result.stdout or "scan failed", 80)
        fallback = self._scan_airodump()
        self.last_scan_completed = time.monotonic()
        if fallback:
            self.last_error = ""
            return fallback
        if not self.last_error:
            self.last_error = "No APs via iw or airodump"
        return []

    def _maybe_reset_scan_iface(self, reason: str) -> bool:
        if self.scan_reset_attempted or self.reset_iface_cb is None:
            return False
        self.scan_reset_attempted = True
        return bool(self.reset_iface_cb(self.iface, reason))

    def _scan(self) -> list[ScanEntry]:
        self.last_scan_started = time.monotonic()
        results = self._scan_once()
        if results:
            self.scan_reset_attempted = False
            return results
        if self._maybe_reset_scan_iface("angryoxide_scan_empty"):
            results = self._scan_once()
            if results:
                self.last_error = ""
                self.scan_reset_attempted = False
                return results
        return []

    def _idle_menu(self, running: bool) -> list[str]:
        items = ["Scan All", "Select Network", "Select Interface"]
        if running:
            items.append("Stop AngryOxide")
        items.extend(["Toggle Log", "Back"])
        return items

    def _profile_menu(self) -> list[str]:
        prefix = "Target" if self.pending_scope == "target" else "All"
        return [f"{prefix} Standard", f"{prefix} Passive", f"{prefix} AutoExit", "Back"]

    def tick(self, force: bool = False) -> None:
        with self.lock:
            if self.state != "scan" or self.menu_open:
                return
            if (not force) and (time.monotonic() - self.last_scan_started) < self.scan_interval:
                return
        results = self._scan()
        with self.lock:
            if results:
                self.scan_results = results
                self.selected_index = max(0, min(self.selected_index, len(self.scan_results) - 1))
            self.redraw_cb()

    def move(self, delta: int, running: bool = False) -> None:
        with self.lock:
            if self.menu_open:
                if self.state == "profile":
                    items = self._profile_menu()
                elif self.state == "iface":
                    items = self._iface_menu()
                else:
                    items = self._idle_menu(running)
                if items:
                    self.menu_index = (self.menu_index + delta) % len(items)
                    self.redraw_cb()
                return
            if self.state == "scan" and self.scan_results:
                self.selected_index = max(0, min(self.selected_index + delta, len(self.scan_results) - 1))
                self.redraw_cb()

    def back(self) -> bool:
        with self.lock:
            if self.menu_open:
                self.menu_open = False
                if self.state == "profile":
                    self.state = "idle"
                elif self.state == "iface":
                    self.state = "idle"
                self.redraw_cb()
                return True
            if self.state == "scan":
                self.state = "idle"
                self.redraw_cb()
                return True
            if self.state == "profile":
                if self.pending_scope == "target":
                    self.state = "scan"
                else:
                    self.state = "idle"
                self.redraw_cb()
                return True
            return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open or self.state in ("scan", "profile", "iface")

    def secondary(self, running: bool = False) -> None:
        if self.state == "scan":
            self.scan_reset_attempted = False
            self.tick(force=True)
            self._set_status("AO target scan refreshed", 4.0)
            return
        self.toggle_log_cb()
        self.redraw_cb()

    def ok(self, running: bool = False) -> None:
        with self.lock:
            if self.menu_open:
                if self.state == "profile":
                    item = self._profile_menu()[self.menu_index]
                elif self.state == "iface":
                    item = self._iface_menu()[self.menu_index]
                else:
                    item = self._idle_menu(running)[self.menu_index]
            elif self.state == "scan":
                if not self.scan_results:
                    self._set_status("AO: no targets visible", 4.0)
                    return
                selected = self.scan_results[self.selected_index]
                self.pending_target = TargetInfo(
                    ssid=selected.ssid,
                    bssid=selected.bssid,
                    channel=selected.channel,
                    security=selected.security,
                )
                self.pending_scope = "target"
                self.state = "profile"
                self.menu_open = True
                self.menu_index = 0
                self._set_status(f"AO target {selected.ssid}", 4.0)
                self.redraw_cb()
                return
            else:
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return

        self._execute_menu_item(item, running=running)

    def _execute_menu_item(self, item: str, running: bool = False) -> None:
        choice = _clean(item, 32).lower()
        with self.lock:
            self.menu_open = False
        if self.state == "iface":
            if choice == "back":
                with self.lock:
                    self.state = "idle"
                self.redraw_cb()
                return
            iface = _clean(item.split()[0], 24)
            if iface and self.set_iface_cb and self.set_iface_cb(iface):
                with self.lock:
                    self.iface = iface
                    self.state = "idle"
                self._set_status(f"AO iface {iface}", 4.0)
                self.redraw_cb()
                return
            with self.lock:
                self.state = "idle"
            self.redraw_cb()
            return
        if choice == "scan all":
            with self.lock:
                self.pending_scope = "all"
                self.pending_target = None
                self.state = "profile"
                self.menu_open = True
                self.menu_index = 0
            self.redraw_cb()
            return
        if choice == "select network":
            with self.lock:
                self.scan_reset_attempted = False
                self.state = "scan"
            self.tick(force=True)
            self._set_status("AO target scan started", 4.0)
            return
        if choice == "select interface":
            with self.lock:
                self.state = "iface"
                self.menu_open = True
                self.menu_index = 0
            self.redraw_cb()
            return
        if choice == "stop angryoxide":
            self.stop_cb()
            return
        if choice == "toggle log":
            self.toggle_log_cb()
            self.redraw_cb()
            return
        if choice == "back":
            with self.lock:
                self.state = "idle"
                self.pending_scope = "all"
            self.redraw_cb()
            return

        profile = ""
        if choice.endswith("standard"):
            profile = "standard"
        elif choice.endswith("passive"):
            profile = "passive"
        elif choice.endswith("autoexit"):
            profile = "autoexit"
        if not profile:
            return

        with self.lock:
            target = self.pending_target.bssid if self.pending_scope == "target" and self.pending_target else None
            target_name = self.pending_target.ssid if self.pending_scope == "target" and self.pending_target else "all"
            self.state = "idle"
        gpsd = self.gpsd_cb()
        self.launch_cb(profile, target, gpsd)
        self._set_status(f"AO {profile} -> {target_name}", 5.0)
        self.redraw_cb()

    def remote_action(self, action: str, running: bool = False) -> bool:
        cmd = _clean(action, 32).lower()
        if cmd == "ao_select_network":
            with self.lock:
                self.scan_reset_attempted = False
                self.state = "scan"
                self.menu_open = False
            self.tick(force=True)
            return True
        if cmd == "ao_scan_all":
            with self.lock:
                self.pending_scope = "all"
                self.pending_target = None
                self.state = "profile"
                self.menu_open = True
                self.menu_index = 0
            self.redraw_cb()
            return True
        if cmd == "ao_lock_target":
            with self.lock:
                if self.state != "scan" or not self.scan_results:
                    return False
            self.ok(running=running)
            return True
        return False

    def status_payload(self, running: bool) -> dict[str, Any]:
        view = self.render_view(running)
        with self.lock:
            target = None
            if self.pending_target is not None:
                target = {
                    "ssid": self.pending_target.ssid,
                    "bssid": self.pending_target.bssid,
                    "channel": self.pending_target.channel,
                    "security": self.pending_target.security,
                }
            return {
                "state": self.state,
                "iface": self.iface,
                "menu_open": self.menu_open,
                "menu_index": self.menu_index,
                "menu_items": list(view.menu_items),
                "selected_index": self.selected_index,
                "scan_count": len(self.scan_results),
                "scan_results": [
                    {
                        "ssid": item.ssid,
                        "bssid": item.bssid,
                        "channel": item.channel,
                        "rssi": item.rssi,
                        "last_seen_s": item.last_seen_s,
                        "security": item.security,
                    }
                    for item in self.scan_results[:16]
                ],
                "pending_scope": self.pending_scope,
                "pending_target": target,
                "last_error": self.last_error,
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

    def render_view(self, running: bool) -> AOView:
        with self.lock:
            if self.state == "scan":
                rows: list[tuple[str, str, str, str]] = []
                total = len(self.scan_results)
                selected = 0
                if total:
                    start = max(0, min(self.selected_index - 2, max(0, total - self.visible_rows)))
                    visible = self.scan_results[start : start + self.visible_rows]
                    for item in visible:
                        rows.append(
                            (
                                _trim_ssid(item.ssid, 10),
                                "n/a" if item.rssi is None else str(item.rssi),
                                "--" if item.channel is None else str(item.channel),
                                item.security[:1].upper(),
                            )
                        )
                    selected = self.selected_index - start
                return AOView(
                    state="scan",
                    menu_open=False,
                    menu_title="AO TARGETS",
                    list_rows=rows,
                    list_selected=selected,
                    list_hint=f"{len(self.scan_results)} aps",
                )

            if self.state == "profile":
                target = self.pending_target
                gpsd = self.gpsd_cb()
                lines = [
                    f"SCOPE {self.pending_scope.upper()}",
                    f"TARGET {target.ssid if target else 'ALL NETWORKS'}",
                    f"BSSID ..{(target.bssid[-8:] if target else '--')}",
                    f"CH {target.channel if target and target.channel is not None else 'n/a'}",
                    f"GPSD {'ON' if gpsd else 'OFF'}",
                    f"IFACE {self.iface.upper()}",
                ]
                return AOView(
                    state="profile",
                    menu_open=True,
                    menu_title="AO PROFILE",
                    menu_items=self._profile_menu(),
                    menu_index=self.menu_index,
                    lines=lines,
                )

            if self.state == "iface":
                return AOView(
                    state="iface",
                    menu_open=True,
                    menu_title="AO IFACE",
                    menu_items=self._iface_menu(),
                    menu_index=self.menu_index,
                    lines=[
                        f"CURRENT {self.iface}",
                        "Pick AO adapter",
                    ],
                )

            target = self.pending_target
            lines = []
            if target is not None:
                lines.extend(
                    [
                        f"PENDING {target.ssid}",
                        f"BSSID ..{target.bssid[-8:]}",
                    ]
                )
            else:
                lines.append("PENDING all networks")
            if self.last_error:
                lines.append(self.last_error[:28])
            return AOView(
                state="idle",
                menu_open=self.menu_open,
                menu_title="ANGRYOXIDE MENU",
                menu_items=self._idle_menu(running),
                menu_index=self.menu_index,
                lines=lines,
            )
