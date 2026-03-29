from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


def _clean(text: Any, max_len: int = 96) -> str:
    s = "" if text is None else str(text)
    s = "".join(ch if ch.isprintable() else " " for ch in s.replace("\x00", " "))
    s = " ".join(s.split())
    return s[:max_len] if max_len > 0 else s


def _trim_ssid(ssid: str, max_len: int = 14) -> str:
    safe = _clean(ssid or "<hidden>", max_len=max_len)
    return safe if safe else "<hidden>"


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


def _freq_to_channel(freq: int | None) -> int | None:
    if freq is None:
        return None
    if 2412 <= freq <= 2472:
        return ((freq - 2412) // 5) + 1
    if freq == 2484:
        return 14
    if 5000 <= freq <= 5900:
        return int(round((freq - 5000) / 5.0))
    return None


def _gps_payload(gps: Any) -> dict[str, Any]:
    if gps is None:
        return {
            "available": False,
            "fix_label": "GPS offline",
            "latitude": None,
            "longitude": None,
            "satellites_used": None,
            "satellites_visible": None,
            "time_utc": "",
        }
    if isinstance(gps, dict):
        return {
            "available": bool(gps.get("available", False)),
            "fix_label": _clean(gps.get("fix_label", "GPS offline"), 24),
            "latitude": gps.get("latitude"),
            "longitude": gps.get("longitude"),
            "satellites_used": gps.get("satellites_used"),
            "satellites_visible": gps.get("satellites_visible"),
            "time_utc": _clean(gps.get("time_utc", ""), 48),
        }
    return {
        "available": bool(getattr(gps, "available", False)),
        "fix_label": _clean(getattr(gps, "fix_label", "GPS offline"), 24),
        "latitude": getattr(gps, "latitude", None),
        "longitude": getattr(gps, "longitude", None),
        "satellites_used": getattr(gps, "satellites_used", None),
        "satellites_visible": getattr(gps, "satellites_visible", None),
        "time_utc": _clean(getattr(gps, "time_utc", ""), 48),
    }


@dataclass
class ScanEntry:
    ssid: str
    bssid: str
    channel: int | None
    rssi: int | None
    last_seen_s: float | None
    security: str
    seen_ts: float


@dataclass
class TargetInfo:
    ssid: str
    bssid: str
    channel: int | None
    security: str


@dataclass
class SamplePoint:
    ts: float
    rssi: int
    avg_short: float
    avg_long: float
    trend: str
    gps: dict[str, Any]
    marked: bool = False


@dataclass
class FoxhuntView:
    state: str
    header: str
    footer: str
    menu_open: bool
    menu_title: str
    menu_items: list[str] = field(default_factory=list)
    menu_index: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)
    list_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    list_selected: int = 0
    list_hint: str = ""
    big_value: str = ""
    big_caption: str = ""
    trend: str = ""
    target_name: str = ""


class FoxhuntController:
    def __init__(
        self,
        config: dict[str, Any] | None,
        status_cb: Callable[[str, float], None],
        redraw_cb: Callable[[], None],
        iface_choices_cb: Callable[[], list[str]] | None = None,
        set_iface_cb: Callable[[str], bool] | None = None,
        reset_iface_cb: Callable[[str, str], bool] | None = None,
    ) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.iface = _clean(cfg.get("interface", "wlan1"), 24) or "wlan1"
        self.scan_max_results = max(8, min(64, int(cfg.get("scan_max_results", 32) or 32)))
        self.scan_interval_idle = max(6.0, float(cfg.get("scan_interval_idle_seconds", 10.0) or 10.0))
        self.scan_interval_active = max(2.0, float(cfg.get("scan_interval_active_seconds", 3.0) or 3.0))
        self.scan_interval_hunt = max(0.5, float(cfg.get("scan_interval_hunt_seconds", 1.0) or 1.0))
        self.short_window = max(3, min(8, int(cfg.get("signal_window_short", 5) or 5)))
        self.long_window = max(6, min(20, int(cfg.get("signal_window_long", 12) or 12)))
        self.visible_rows = 5
        self.save_dir = Path(str(cfg.get("save_dir", str(Path.home() / ".local" / "share" / "launcher" / "foxhunt")))).expanduser()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.active_path = self.save_dir / "active_session.json"
        self.last_path = self.save_dir / "last_session.json"
        self.primary_iface = _clean(cfg.get("primary_interface", "wlan0"), 24) or "wlan0"
        self.top_ports = max(5, min(50, int(cfg.get("top_ports", 20) or 20)))
        self.status_cb = status_cb
        self.redraw_cb = redraw_cb
        self.iface_choices_cb = iface_choices_cb
        self.set_iface_cb = set_iface_cb
        self.reset_iface_cb = reset_iface_cb
        self.lock = threading.RLock()

        self.state = "idle"
        self.menu_open = False
        self.menu_index = 0
        self.scan_results: list[ScanEntry] = []
        self.selected_index = 0
        self.sort_mode = _clean(cfg.get("sort", "rssi"), 16).lower() or "rssi"
        self.selected_target: TargetInfo | None = None
        self.current_rssi: int | None = None
        self.avg_short: float | None = None
        self.avg_long: float | None = None
        self.trend = "stable"
        self.last_seen_ts: float | None = None
        self.target_visible = False
        self.best_rssi: int | None = None
        self.worst_rssi: int | None = None
        self.best_sample: SamplePoint | None = None
        self.sample_history: deque[int] = deque(maxlen=self.long_window)
        self.session_samples: list[SamplePoint] = []
        self.mark_count = 0
        self.session_started_ts: float | None = None
        self.session_saved_path = ""
        self.status_label = "none"
        self.last_scan_started = 0.0
        self.last_scan_completed = 0.0
        self.last_error = ""
        self.scan_reset_attempted = False
        self.last_gps = _gps_payload(None)
        self.last_session_summary: dict[str, Any] | None = self._load_json(self.last_path)
        self.tool_ip = self._resolve_tool("ip", ["/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip"])
        self.tool_iw = self._resolve_tool("iw", ["/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw"])
        self.tool_sudo = self._resolve_tool("sudo", ["/usr/bin/sudo", "/bin/sudo"])
        self.tool_timeout = self._resolve_tool("timeout", ["/usr/bin/timeout", "/bin/timeout"])
        self.tool_airodump = self._resolve_tool("airodump-ng", ["/usr/sbin/airodump-ng", "/usr/bin/airodump-ng", "/bin/airodump-ng"])
        self.tool_nmap = self._resolve_tool("nmap", ["/usr/bin/nmap"])
        self._resume_active_if_present()
        self.service_target_ip = ""
        self.service_lines: list[str] = []

    def _resolve_tool(self, name: str, fallbacks: list[str]) -> str:
        found = shutil.which(name)
        if found:
            return found
        for path in fallbacks:
            if Path(path).exists():
                return path
        return name

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _resume_active_if_present(self) -> None:
        payload = self._load_json(self.active_path)
        if not payload:
            return
        target = payload.get("target", {})
        if not isinstance(target, dict) or not target.get("bssid"):
            return
        self.selected_target = TargetInfo(
            ssid=_clean(target.get("ssid", "<hidden>"), 64),
            bssid=_clean(target.get("bssid", ""), 32),
            channel=target.get("channel"),
            security=_clean(target.get("security", ""), 24),
        )
        self.state = "target"
        self.best_rssi = payload.get("best_rssi")
        self.worst_rssi = payload.get("worst_rssi")
        self.session_started_ts = payload.get("session_started_ts")
        self.session_saved_path = _clean(payload.get("session_saved_path", ""), 256)
        self.mark_count = int(payload.get("mark_count", 0) or 0)
        self.status_label = "previous"

    def _menu_items(self) -> list[str]:
        if self.state == "idle":
            items = ["Start Scan"]
            if self.selected_target is not None:
                items.append("Resume Session")
            if self.last_session_summary:
                items.append("Last Session")
            items.extend(["Select Interface", "Settings", "Back"])
            return items
        if self.state == "scan":
            return ["Lock Target", "Refresh Scan", f"Sort {self.sort_mode.upper()}", "Back"]
        if self.state == "target":
            return ["Start Hunt", "Mark Point", "Save Session", "Clear Target", "Back"]
        if self.state == "hunt":
            return ["Mark Point", "Pause Hunt", "End Hunt", "Save Session", "Back"]
        if self.state == "summary":
            items = ["Return Idle"]
            if self.selected_target is not None:
                items.append("Resume Target")
            items.extend(["Save Again", "Back"])
            return items
        return ["Back"]

    def footer_text(self) -> str:
        with self.lock:
            if self.menu_open:
                return "U/D menu  OK pick  L back"
            if self.state == "scan":
                return "U/D sel  OK lock  L back"
            if self.state == "hunt":
                return "U/D idle  OK menu  L target"
            if self.state == "target":
                return "OK menu  L scan  K2 mark"
            if self.state == "summary":
                return "OK menu  L idle"
            return "L/R page  OK menu"

    def _set_status(self, text: str, hold: float = 4.0) -> None:
        self.status_cb(_clean(text, 72), hold)

    def _iface_menu(self) -> list[str]:
        if self.iface_choices_cb is None:
            return ["Back"]
        items = [item for item in self.iface_choices_cb() if _clean(item, 32)]
        return items + ["Back"] if items else ["Back"]

    def _sort_results(self) -> None:
        if self.sort_mode == "last":
            self.scan_results.sort(key=lambda item: (9999.0 if item.last_seen_s is None else item.last_seen_s, -(item.rssi or -999)), reverse=False)
            return
        self.scan_results.sort(key=lambda item: (-999 if item.rssi is None else item.rssi, -(item.channel or 0)), reverse=True)

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
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _iface_mode(self) -> str:
        try:
            result = self._run_cmd(["iw", "dev", self.iface, "info"], timeout=2.5, privileged=False)
        except Exception:
            return ""
        blob = result.stdout or ""
        for raw in blob.splitlines():
            line = raw.strip()
            if line.startswith("type "):
                return _clean(line.split(None, 1)[1], 16).lower()
        return ""

    def _prepare_scan_iface(self) -> tuple[bool, str]:
        original_mode = self._iface_mode()
        if original_mode == "managed":
            try:
                self._run_cmd(["ip", "link", "set", self.iface, "up"], timeout=4.0, privileged=True)
            except Exception:
                pass
            return (True, original_mode)

        steps = [
            ["ip", "link", "set", self.iface, "down"],
            ["iw", "dev", self.iface, "set", "type", "managed"],
            ["ip", "link", "set", self.iface, "up"],
        ]
        for cmd in steps:
            try:
                result = self._run_cmd(cmd, timeout=5.0, privileged=True)
            except Exception as exc:
                self.last_error = _clean(exc, 80)
                return (False, original_mode)
            if result.returncode != 0:
                self.last_error = _clean(result.stderr or result.stdout or "failed", 80)
                return (False, original_mode)

        time.sleep(0.35)
        return (True, original_mode)

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
        if not text:
            return results
        now = time.time()
        blocks = []
        current: list[str] = []
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
            first = block[0].strip()
            try:
                bssid = _clean(first.split()[1].split("(")[0], 32).lower()
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
                        rssi = None
                elif line.startswith("freq:"):
                    try:
                        freq = int(line.split(":", 1)[1].strip())
                    except Exception:
                        freq = None
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
                        last_seen = None
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
        prefix = f"/tmp/launcher-foxhunt-scan-{os.getpid()}-{int(time.time() * 1000)}"
        csv_path = Path(f"{prefix}-01.csv")
        dwell_seconds = "8"
        run_timeout = 10.0
        target_channel = None
        if self.state in ("target", "hunt") and self.selected_target is not None:
            target_channel = self.selected_target.channel
            if target_channel is not None:
                dwell_seconds = "2"
                run_timeout = 4.0
        cmd = [
            self.tool_timeout,
            dwell_seconds,
            self.tool_airodump,
            "--write-interval",
            "1",
            "--output-format",
            "csv",
            "-w",
            prefix,
        ]
        if target_channel is not None:
            cmd.extend(["--channel", str(target_channel)])
        else:
            cmd.extend(["--band", "abg"])
        cmd.append(self.iface)
        if os.geteuid() != 0 and self.tool_sudo:
            cmd = [self.tool_sudo, "-n"] + cmd
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=run_timeout,
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
        if self.state in ("target", "hunt") and self.selected_target is not None and self.selected_target.channel is not None:
            fallback = self._scan_airodump()
            self.last_scan_completed = time.monotonic()
            if fallback:
                self.last_error = ""
                return fallback

        ready, original_mode = self._prepare_scan_iface()
        if not ready:
            return []
        try:
            result = self._run_cmd(
                ["iw", "dev", self.iface, "scan", "ap-force"],
                timeout=8.0,
                privileged=True,
            )
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
            err = _clean(result.stderr or result.stdout, 80)
            self.last_error = err or "scan failed"
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
        if self._maybe_reset_scan_iface("foxhunt_scan_empty"):
            results = self._scan_once()
            if results:
                self.last_error = ""
                self.scan_reset_attempted = False
                return results
        return []

    def _append_sample(self, rssi: int, gps: dict[str, Any], marked: bool = False) -> None:
        now = time.time()
        previous_rssi = self.current_rssi
        self.current_rssi = rssi
        self.sample_history.append(rssi)
        short_values = list(self.sample_history)[-self.short_window :]
        long_values = list(self.sample_history)[-self.long_window :]
        self.avg_short = sum(short_values) / float(len(short_values))
        self.avg_long = sum(long_values) / float(len(long_values))
        if previous_rssi is None:
            self.trend = "stable"
        elif rssi > previous_rssi:
            self.trend = "hotter"
        elif rssi < previous_rssi:
            self.trend = "colder"
        else:
            self.trend = "stable"
        self.last_seen_ts = now
        self.target_visible = True
        self.status_label = "active"
        if self.best_rssi is None or rssi > self.best_rssi:
            self.best_rssi = rssi
            self.best_sample = SamplePoint(
                ts=now,
                rssi=rssi,
                avg_short=self.avg_short,
                avg_long=self.avg_long,
                trend=self.trend,
                gps=gps,
                marked=marked,
            )
        if self.worst_rssi is None or rssi < self.worst_rssi:
            self.worst_rssi = rssi
        point = SamplePoint(
            ts=now,
            rssi=rssi,
            avg_short=self.avg_short,
            avg_long=self.avg_long,
            trend=self.trend,
            gps=gps,
            marked=marked,
        )
        self.session_samples.append(point)
        if marked:
            self.mark_count += 1

    def _persist_active(self) -> None:
        payload = {
            "state": self.state,
            "target": None if self.selected_target is None else asdict(self.selected_target),
            "current_rssi": self.current_rssi,
            "avg_short": self.avg_short,
            "avg_long": self.avg_long,
            "trend": self.trend,
            "last_seen_ts": self.last_seen_ts,
            "target_visible": self.target_visible,
            "best_rssi": self.best_rssi,
            "worst_rssi": self.worst_rssi,
            "mark_count": self.mark_count,
            "session_started_ts": self.session_started_ts,
            "session_saved_path": self.session_saved_path,
            "sample_count": len(self.session_samples),
        }
        self._write_json(self.active_path, payload)

    def _summary_payload(self) -> dict[str, Any]:
        gps_valid = sum(
            1
            for sample in self.session_samples
            if sample.gps.get("latitude") is not None and sample.gps.get("longitude") is not None
        )
        target = {} if self.selected_target is None else asdict(self.selected_target)
        duration = 0.0
        if self.session_started_ts is not None:
            duration = max(0.0, time.time() - self.session_started_ts)
        payload = {
            "target": target,
            "duration_seconds": int(duration),
            "sample_count": len(self.session_samples),
            "gps_valid_samples": gps_valid,
            "best_rssi": self.best_rssi,
            "worst_rssi": self.worst_rssi,
            "mark_count": self.mark_count,
            "saved_at": int(time.time()),
            "saved_path": self.session_saved_path,
        }
        if self.best_sample is not None:
            payload["best_sample"] = asdict(self.best_sample)
        return payload

    def _save_session(self, final: bool = False) -> None:
        if self.selected_target is None:
            self._set_status("FoxHunt: no target", 4.0)
            return
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = self.save_dir / f"session-{stamp}.json"
        payload = self._summary_payload()
        payload["samples"] = [asdict(sample) for sample in self.session_samples[-240:]]
        self._write_json(path, payload)
        self.session_saved_path = str(path)
        payload["saved_path"] = self.session_saved_path
        self._write_json(self.last_path, payload)
        self.last_session_summary = payload
        self._persist_active()
        self._set_status("FoxHunt session saved", 5.0)
        if final:
            self.state = "summary"
            self.menu_open = False
            try:
                self.active_path.unlink()
            except Exception:
                pass

    def _lock_selected_target(self) -> None:
        if not self.scan_results:
            self._set_status("FoxHunt: no scan results", 4.0)
            return
        idx = max(0, min(self.selected_index, len(self.scan_results) - 1))
        selected = self.scan_results[idx]
        self.selected_target = TargetInfo(
            ssid=selected.ssid,
            bssid=selected.bssid,
            channel=selected.channel,
            security=selected.security,
        )
        self.state = "target"
        self.menu_open = False
        self.current_rssi = selected.rssi
        self.avg_short = float(selected.rssi) if selected.rssi is not None else None
        self.avg_long = float(selected.rssi) if selected.rssi is not None else None
        self.trend = "stable"
        self.best_rssi = selected.rssi
        self.worst_rssi = selected.rssi
        self.best_sample = None
        self.sample_history.clear()
        self.session_samples.clear()
        self.mark_count = 0
        self.session_started_ts = time.time()
        self.last_seen_ts = time.time()
        self.target_visible = selected.rssi is not None
        self.status_label = "active"
        self._persist_active()
        self._set_status(f"FoxHunt locked {selected.ssid}", 4.0)

    def set_external_target(self, ssid: str, bssid: str, channel: int | None, security: str = "unknown", rssi: int | None = None) -> bool:
        safe_bssid = _clean(bssid, 32).lower()
        if not safe_bssid:
            return False
        safe_ssid = _clean(ssid or "<hidden>", 64) or "<hidden>"
        safe_security = _clean(security or "unknown", 24) or "unknown"
        with self.lock:
            self.selected_target = TargetInfo(
                ssid=safe_ssid,
                bssid=safe_bssid,
                channel=channel,
                security=safe_security,
            )
            self.state = "target"
            self.menu_open = False
            self.current_rssi = rssi
            self.avg_short = float(rssi) if rssi is not None else None
            self.avg_long = float(rssi) if rssi is not None else None
            self.trend = "stable"
            self.best_rssi = rssi
            self.worst_rssi = rssi
            self.best_sample = None
            self.sample_history.clear()
            self.session_samples.clear()
            self.mark_count = 0
            self.session_started_ts = time.time()
            self.last_seen_ts = time.time()
            self.target_visible = rssi is not None
            self.status_label = "active"
            self._persist_active()
        self._set_status(f"FoxHunt locked {safe_ssid}", 4.0)
        self.redraw_cb()
        return True

    def _clear_target(self) -> None:
        self.selected_target = None
        self.current_rssi = None
        self.avg_short = None
        self.avg_long = None
        self.trend = "stable"
        self.last_seen_ts = None
        self.target_visible = False
        self.best_rssi = None
        self.worst_rssi = None
        self.best_sample = None
        self.sample_history.clear()
        self.session_samples.clear()
        self.mark_count = 0
        self.session_started_ts = None
        self.session_saved_path = ""
        self.service_target_ip = ""
        self.service_lines = []
        self.status_label = "none"
        self.state = "idle"
        self.menu_open = False
        try:
            self.active_path.unlink()
        except Exception:
            pass
        self._set_status("FoxHunt target cleared", 4.0)

    def _resume_or_last(self, use_last: bool = False) -> None:
        payload = self.last_session_summary if use_last else self._load_json(self.active_path)
        if not payload:
            payload = self.last_session_summary
        if not payload:
            self._set_status("FoxHunt: no saved session", 4.0)
            return
        target = payload.get("target", {})
        if not isinstance(target, dict) or not target.get("bssid"):
            self._set_status("FoxHunt: invalid session", 4.0)
            return
        self.selected_target = TargetInfo(
            ssid=_clean(target.get("ssid", "<hidden>"), 64),
            bssid=_clean(target.get("bssid", ""), 32),
            channel=target.get("channel"),
            security=_clean(target.get("security", ""), 24),
        )
        self.state = "summary" if use_last else "target"
        self.best_rssi = payload.get("best_rssi")
        self.worst_rssi = payload.get("worst_rssi")
        self.session_saved_path = _clean(payload.get("saved_path", ""), 256)
        self.mark_count = int(payload.get("mark_count", 0) or 0)
        self.service_target_ip = ""
        self.service_lines = []
        self.status_label = "previous"
        self._set_status("FoxHunt session loaded", 4.0)

    def _resolve_target_ip(self) -> str:
        target = self.selected_target
        if target is None:
            return ""
        try:
            result = self._run_cmd([self.tool_ip, "-4", "neigh", "show", "dev", self.primary_iface], timeout=3.0, privileged=False)
        except Exception:
            return ""
        for raw in (result.stdout or "").splitlines():
            line = raw.strip()
            low = line.lower()
            if target.bssid.lower() not in low:
                continue
            parts = line.split()
            if parts and parts[0].count(".") == 3:
                return _clean(parts[0], 32)
        return ""

    def _service_scan(self) -> None:
        target = self.selected_target
        if target is None:
            self._set_status("FoxHunt: no target", 4.0)
            return
        ip = self._resolve_target_ip()
        if not ip:
            with self.lock:
                self.service_target_ip = ""
                self.service_lines = []
            self._set_status("FoxHunt: no LAN IP for target", 4.0)
            self.redraw_cb()
            return
        out = self._run_cmd([self.tool_nmap, "-Pn", "--top-ports", str(self.top_ports), "--open", ip], timeout=18.0, privileged=False)
        lines: list[str] = []
        for raw in (out.stdout or "").splitlines():
            line = raw.strip()
            if "/tcp" in line or "/udp" in line:
                lines.append(_clean(line, 80))
        with self.lock:
            self.service_target_ip = ip
            self.service_lines = lines[:8]
        self._set_status("FoxHunt service scan complete", 4.0 if lines else 5.0)
        self.redraw_cb()

    def tick(self, gps: Any, force: bool = False) -> None:
        with self.lock:
            self.last_gps = _gps_payload(gps)
            if self.menu_open:
                return
            if self.state == "idle" and not force:
                return
            now = time.monotonic()
            interval = self.scan_interval_idle
            if self.state == "hunt":
                interval = self.scan_interval_hunt
            elif self.state in ("scan", "target"):
                interval = self.scan_interval_active
            if (not force) and (now - self.last_scan_started) < interval:
                return

        results = self._scan()
        with self.lock:
            if results:
                self.scan_results = results
                self._sort_results()
                self.selected_index = max(0, min(self.selected_index, len(self.scan_results) - 1))

            target = self.selected_target
            if target is None:
                self.redraw_cb()
                return

            match = None
            for item in self.scan_results:
                if item.bssid == target.bssid:
                    match = item
                    break

            if match and match.rssi is not None:
                if self.last_seen_ts is not None and (time.time() - self.last_seen_ts) > (self.scan_interval_active * 2.2):
                    self._set_status("FoxHunt target reacquired", 4.0)
                self._append_sample(match.rssi, self.last_gps)
                self.selected_target.channel = match.channel
                self.selected_target.security = match.security
                self.target_visible = True
            else:
                self.target_visible = False
                if self.last_seen_ts is not None:
                    age = time.time() - self.last_seen_ts
                    if age > (self.scan_interval_active * 2.2):
                        self.status_label = "lost"
            self._persist_active()
            self.redraw_cb()

    def move(self, delta: int) -> None:
        with self.lock:
            if self.menu_open:
                items = self._iface_menu() if self.state == "iface" else self._menu_items()
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
                if self.state == "iface":
                    self.state = "idle"
                self.redraw_cb()
                return True
            if self.state == "scan":
                self.state = "idle"
                self.redraw_cb()
                return True
            if self.state == "target":
                self.state = "scan"
                self.redraw_cb()
                return True
            if self.state == "hunt":
                self.state = "target"
                self.redraw_cb()
                return True
            if self.state == "summary":
                self.state = "idle"
                self.redraw_cb()
                return True
            return False

    def block_page_cycle(self) -> bool:
        with self.lock:
            return self.menu_open or self.state in ("scan", "iface")

    def secondary(self) -> None:
        with self.lock:
            if self.state == "scan":
                force = True
            elif self.state in ("target", "hunt"):
                force = False
            else:
                force = False
        if self.state == "scan":
            self.tick(self.last_gps, force=True)
            self._set_status("FoxHunt scan refreshed", 4.0)
            return
        if self.state in ("target", "hunt"):
            with self.lock:
                if self.current_rssi is None:
                    self._set_status("FoxHunt: target not visible", 4.0)
                    return
                self._append_sample(self.current_rssi, self.last_gps, marked=True)
                self._persist_active()
                self._set_status("FoxHunt point marked", 4.0)
                self.redraw_cb()
            return
        if self.state == "idle" and self.last_session_summary:
            self._resume_or_last(use_last=True)
            self.redraw_cb()

    def remote_action(self, action: str) -> bool:
        cmd = _clean(action, 32).lower()
        with self.lock:
            if cmd == "fh_menu":
                self.menu_open = True
                self.menu_index = 0
                self.redraw_cb()
                return True
            if cmd == "fh_scan":
                self.menu_open = False
                self.state = "scan"
            elif cmd == "fh_lock":
                if self.state != "scan" or not self.scan_results:
                    return False
                self._lock_selected_target()
                self.redraw_cb()
                return True
            elif cmd == "fh_mark":
                pass
            elif cmd == "fh_save":
                pass
            elif cmd == "fh_resume":
                pass
            elif cmd == "fh_end":
                pass
            elif cmd == "fh_clear":
                self._clear_target()
                self.redraw_cb()
                return True
            elif cmd == "fh_last":
                self._resume_or_last(use_last=True)
                self.redraw_cb()
                return True
            elif cmd == "fh_target":
                if self.selected_target is None:
                    return False
                self.state = "target"
                self.menu_open = False
                self.redraw_cb()
                return True
            else:
                return False

        if cmd == "fh_scan":
            self.tick(self.last_gps, force=True)
            self._set_status("FoxHunt scan started", 4.0)
            self.redraw_cb()
            return True
        if cmd == "fh_mark":
            self.secondary()
            return True
        if cmd == "fh_save":
            self._save_session(final=False)
            self.redraw_cb()
            return True
        if cmd == "fh_resume":
            if self.selected_target is not None:
                with self.lock:
                    self.state = "hunt"
                    self.menu_open = False
                self._set_status("FoxHunt hunt active", 4.0)
                self.redraw_cb()
                return True
            self._resume_or_last(use_last=False)
            self.redraw_cb()
            return True
        if cmd == "fh_end":
            with self.lock:
                if self.selected_target is None:
                    return False
            self._save_session(final=True)
            self.redraw_cb()
            return True
        return False

    def ok(self) -> None:
        with self.lock:
            if self.menu_open:
                menu_items = self._iface_menu() if self.state == "iface" else self._menu_items()
                self._execute_menu_item(menu_items[self.menu_index])
                self.redraw_cb()
                return
            if self.state == "scan":
                self._lock_selected_target()
                self.redraw_cb()
                return
            self.menu_open = True
            self.menu_index = 0
            self.redraw_cb()

    def _execute_menu_item(self, item: str) -> None:
        choice = _clean(item, 32).lower()
        self.menu_open = False
        if self.state == "iface":
            if choice == "back":
                self.state = "idle"
                return
            iface = _clean(item.split()[0], 24)
            if iface and self.set_iface_cb and self.set_iface_cb(iface):
                self.iface = iface
                self.state = "idle"
                self._set_status(f"FoxHunt iface {iface}", 4.0)
            else:
                self.state = "idle"
            return
        if choice == "start scan":
            self.scan_reset_attempted = False
            self.state = "scan"
            self.tick(self.last_gps, force=True)
            self._set_status("FoxHunt scan started", 4.0)
            return
        if choice == "select interface":
            self.state = "iface"
            self.menu_open = True
            self.menu_index = 0
            return
        if choice == "resume session":
            self._resume_or_last(use_last=False)
            return
        if choice == "last session":
            self._resume_or_last(use_last=True)
            return
        if choice == "settings":
            self._set_status(f"FoxHunt iface {self.iface} sort {self.sort_mode}", 5.0)
            return
        if choice == "lock target":
            self._lock_selected_target()
            return
        if choice == "refresh scan":
            self.scan_reset_attempted = False
            self.tick(self.last_gps, force=True)
            self._set_status("FoxHunt scan refreshed", 4.0)
            return
        if choice.startswith("sort "):
            self.sort_mode = "last" if self.sort_mode == "rssi" else "rssi"
            self._sort_results()
            self._set_status(f"FoxHunt sort {self.sort_mode}", 4.0)
            return
        if choice == "start hunt":
            self.state = "hunt"
            self._set_status("FoxHunt hunt active", 4.0)
            return
        if choice == "mark point":
            self.secondary()
            return
        if choice == "save session" or choice == "save again":
            self._save_session(final=False)
            return
        if choice == "clear target":
            self._clear_target()
            return
        if choice == "pause hunt":
            self.state = "target"
            self._set_status("FoxHunt hunt paused", 4.0)
            return
        if choice == "end hunt":
            self._save_session(final=True)
            return
        if choice == "return idle" or choice == "back":
            self.state = "idle"
            return
        if choice == "resume target":
            self.state = "target"

    def render_view(self) -> FoxhuntView:
        with self.lock:
            state = self.state
            gps_lock = self.last_gps.get("fix_label", "GPS offline")
            target = self.selected_target
            current_age = None if self.last_seen_ts is None else max(0.0, time.time() - self.last_seen_ts)

            if state == "scan":
                rows: list[tuple[str, str, str, str]] = []
                total = len(self.scan_results)
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
                else:
                    selected = 0
                return FoxhuntView(
                    state=state,
                    header="FOXHUNT/SCAN",
                    footer=self.footer_text(),
                    menu_open=self.menu_open,
                    menu_title="SCAN MENU",
                    menu_items=self._menu_items(),
                    menu_index=self.menu_index,
                    list_rows=rows,
                    list_selected=selected,
                    list_hint=f"{len(self.scan_results)} aps  {self.sort_mode}",
                )

            if state == "hunt":
                visible_text = "VISIBLE" if self.target_visible else "LOST"
                promising = self.current_rssi is not None and self.current_rssi >= -25
                return FoxhuntView(
                    state=state,
                    header="FOXHUNT/HUNT",
                    footer=self.footer_text(),
                    menu_open=self.menu_open,
                    menu_title="HUNT MENU",
                    menu_items=self._menu_items(),
                    menu_index=self.menu_index,
                    big_value="n/a" if self.current_rssi is None else f"{self.current_rssi} dBm",
                    big_caption="PROMISING" if promising else self.trend.upper(),
                    trend="hotter" if promising else self.trend,
                    target_name=_trim_ssid(target.ssid if target else "none", 18),
                    lines=[
                        (f"BEST {self.best_rssi if self.best_rssi is not None else 'n/a'}", "text"),
                        (f"LAST {_fmt_age(current_age)}", "text"),
                        ("GOOD LOCK" if promising else f"GPS {gps_lock}", "text"),
                        (f"SAMP {len(self.session_samples)} {visible_text}", "dim"),
                    ],
                )

            if state == "target":
                return FoxhuntView(
                    state=state,
                    header="FOXHUNT/TARGET",
                    footer=self.footer_text(),
                    menu_open=self.menu_open,
                    menu_title="TARGET MENU",
                    menu_items=self._menu_items(),
                    menu_index=self.menu_index,
                    lines=[
                        (f"SSID {target.ssid if target else 'none'}", "text"),
                        (f"BSSID ..{(target.bssid[-8:] if target else '--')}", "dim"),
                        (f"CH {target.channel if target and target.channel is not None else 'n/a'}  RSSI {self.current_rssi if self.current_rssi is not None else 'n/a'}", "text"),
                        (f"AVG {('n/a' if self.avg_short is None else int(round(self.avg_short)))}  BEST {self.best_rssi if self.best_rssi is not None else 'n/a'}", "text"),
                        (f"GPS {gps_lock}", "text"),
                        (f"LAST {_fmt_age(current_age)}  {'VISIBLE' if self.target_visible else 'LOST'}", "dim"),
                    ],
                )

            if state == "iface":
                return FoxhuntView(
                    state=state,
                    header="FOXHUNT/IFACE",
                    footer=self.footer_text(),
                    menu_open=True,
                    menu_title="IFACE MENU",
                    menu_items=self._iface_menu(),
                    menu_index=self.menu_index,
                    lines=[
                        (f"IFACE {self.iface}", "text"),
                        ("Pick scan adapter", "dim"),
                    ],
                )

            if state == "summary":
                summary = self.last_session_summary or self._summary_payload()
                duration = int(summary.get("duration_seconds", 0) or 0)
                return FoxhuntView(
                    state=state,
                    header="FOXHUNT/SUMMARY",
                    footer=self.footer_text(),
                    menu_open=self.menu_open,
                    menu_title="SUMMARY MENU",
                    menu_items=self._menu_items(),
                    menu_index=self.menu_index,
                    lines=[
                        (f"TARGET {target.ssid if target else 'none'}", "text"),
                        (f"BSSID ..{(target.bssid[-8:] if target else '--')}", "dim"),
                        (f"DUR {duration}s  SAMP {summary.get('sample_count', 0)}", "text"),
                        (f"GPS {summary.get('gps_valid_samples', 0)}  MARK {summary.get('mark_count', 0)}", "text"),
                        (f"BEST {summary.get('best_rssi', 'n/a')} WORST {summary.get('worst_rssi', 'n/a')}", "text"),
                        (f"SAVE {Path(self.session_saved_path).name if self.session_saved_path else 'n/a'}", "dim"),
                    ],
                )

            target_status = "NONE" if self.selected_target is None else self.status_label.upper()
            return FoxhuntView(
                state="idle",
                header="FOXHUNT",
                footer=self.footer_text(),
                menu_open=self.menu_open,
                menu_title="FOXHUNT MENU",
                menu_items=self._menu_items(),
                menu_index=self.menu_index,
                lines=[
                    (f"MODE {self.state.upper()}", "text"),
                    (f"IFACE {self.iface.upper()}", "text"),
                    (f"GPS {gps_lock}", "text"),
                    (f"TARGET {target_status}", "text"),
                    (f"SCAN {'READY' if not self.last_error else 'ERROR'}", "text"),
                    (self.last_error[:30] if self.last_error else f"LAST {_fmt_age(max(0.0, time.monotonic() - self.last_scan_completed) if self.last_scan_completed else None)}", "dim"),
                ],
            )

    def status_payload(self) -> dict[str, Any]:
        view = self.render_view()
        with self.lock:
            target = None if self.selected_target is None else asdict(self.selected_target)
            current_age = None if self.last_seen_ts is None else max(0.0, time.time() - self.last_seen_ts)
            gps_valid = sum(
                1
                for sample in self.session_samples
                if sample.gps.get("latitude") is not None and sample.gps.get("longitude") is not None
            )
            return {
                "state": self.state,
                "iface": self.iface,
                "menu_open": self.menu_open,
                "menu_index": self.menu_index,
                "menu_items": list(view.menu_items),
                "header": view.header,
                "footer": view.footer,
                "target": target,
                "scan_count": len(self.scan_results),
                "selected_index": self.selected_index,
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
                "current_rssi": self.current_rssi,
                "avg_short": self.avg_short,
                "avg_long": self.avg_long,
                "trend": self.trend,
                "last_seen_age_s": current_age,
                "target_visible": self.target_visible,
                "best_rssi": self.best_rssi,
                "worst_rssi": self.worst_rssi,
                "sample_count": len(self.session_samples),
                "gps_valid_samples": gps_valid,
                "mark_count": self.mark_count,
                "service_target_ip": self.service_target_ip,
                "service_lines": list(self.service_lines),
                "last_error": self.last_error,
                "last_gps": dict(self.last_gps),
                "saved_path": self.session_saved_path,
                "view": {
                    "state": view.state,
                    "header": view.header,
                    "footer": view.footer,
                    "menu_open": view.menu_open,
                    "menu_title": view.menu_title,
                    "menu_items": list(view.menu_items),
                    "menu_index": view.menu_index,
                    "lines": list(view.lines),
                    "list_rows": list(view.list_rows),
                    "list_selected": view.list_selected,
                    "list_hint": view.list_hint,
                    "big_value": view.big_value,
                    "big_caption": view.big_caption,
                    "trend": view.trend,
                    "target_name": view.target_name,
                },
            }
