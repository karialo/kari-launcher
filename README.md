# launcher

launcher for the Waveshare 1.3in 240x240 display

Author: K.A.R.I

## Layout
```
launcher/
├─ bin/launcher
├─ src/
└─ VERSION
```

## Run
```
launcher [args]
```

## Dashboard Mode
`bin/dashboard` runs a `pygame`-rendered dashboard on the Waveshare 1.3in 240x240 panel without changing RaspyJack.

### Controls
- `A`: previous page
- `B`: next page
- `X/Y`: context actions per page
  - GPS: refresh / scroll
  - Network Ops: open menu or confirm / refresh or back through submenus
  - Wifite page: open menu or set target / refresh passive scan
  - RaspyJack page: launch / stop
  - AngryOxide page: start/stop + summary/log toggle
  - Overview: manual refresh on `X`

### What it shows
- Local node: hostname, tailscale IPv4, CPU temp, load, RAM usage.
- Remote nodes (PiTemplar/Bjorn): ping latency, optional TCP ports, health endpoint status.
- PiTemplar SMB health + deep share stats (file count/capacity when available).
- Overview is now intentionally minimal and focuses on local health plus remote node summary; GPS-heavy detail was moved to its own page.
- GPS page: fix status, coordinates, altitude, speed, track, DOP/error estimates, receiver path, and per-satellite detail when gpsd provides it.
- Network Ops page: permanent `wlan0` status plus a live inventory of all detected wireless adapters, including mode, state, active profile, and vendor/driver labels for external cards, with recovery actions.
- Network Ops on-device view now scrolls when the adapter inventory exceeds the visible rows.
- Network Ops interface control is nested by adapter: choose an external radio, then switch it between `monitor` and `managed` without touching permanent onboard `wlan0`.
- External wireless adapters are automatically restored to monitor mode when idle unless Foxhunt or AngryOxide is actively holding one of them for a current run.
- RaspyJack page: service/interface/runtime stats.
- AngryOxide page: status, live log tail, and run control.
- FoxHunt page: defensive Wi-Fi scan, target lock by BSSID, and RSSI/GPS-assisted tracking.
- Wifite page: passive Wi-Fi target prep only. It can scan, stage a target BSSID/channel, clear the staged target, and switch the external adapter, but it does not launch `wifite`.
- FoxHunt, Wifite, and AngryOxide now expose external wireless interface selection from their on-device menus for future scans/runs.

### Phone Web Control
The dashboard now exposes a lightweight web UI for remote control and status.

- The web UI now includes a launcher-specific virtual device panel so the K.A.R.I screen state can be mirrored remotely in a RaspyJack-style shell.
- The virtual device is now driven from the real launcher framebuffer (`/api/frame.png`) instead of an approximation, so menus, highlights, and transient device state match the physical panel.
- The web virtual D-pad now uses dedicated directional actions, so menu movement and scrolling match the physical controls instead of jumping pages.
- The virtual page indicators now match the real device styling: active page filled green, inactive pages black-filled with a green ring.
- The `Network Ops` web page now renders live per-adapter `Monitor` and `Managed` buttons from the current adapter inventory.
- The web UI sidebar was simplified by removing the old quick-action and auth-token controls.

- URL: `http://<pi-ip>:8787`
- Endpoints:
  - `GET /api/status`
  - `POST /api/action` with JSON body `{"action":"page_next"}` etc.
  - `GET /api/action` is intentionally disabled (`405`) to avoid accidental triggers.
- Remote safe mode:
  - `"remote.safe_mode": true` blocks RaspyJack command actions.
  - Allowed in safe mode: page nav (`page_prev/page_next`), `refresh`, page jumps (`overview/networkops/foxhunt/wifite/raspyjack/angryoxide`), Network Ops controls, Wifite passive target controls, and AngryOxide controls (`ao_toggle`, `ao_view`, `ao_monitor_on`, `ao_monitor_off`).
- Supported actions:
  - `page_prev`, `page_next`
  - `up`, `down`, `left`, `right`
  - `context_x`, `context_y`
  - `refresh`
  - `overview`, `gps`, `networkops`, `foxhunt`, `wifite`, `raspyjack`, `angryoxide`
  - `wf_select_network`, `wf_lock_target`, `wf_clear_target`
  - `net_refresh`, `net_reconnect_wlan0`, `net_restart_networkmanager`, `net_restart_tailscale`, `net_iface_menu`, `net_reboot`
  - `ao_toggle`, `ao_view`, `ao_monitor_on`, `ao_monitor_off`
  - `rj_core_start|stop|restart`
  - `rj_device_start|stop|restart`
  - `rj_web_start|stop|restart`
  - `rj_all_start|stop|restart`
- Safety:
  - High-impact actions (`ao_toggle`, `ao_monitor_*`, `net_reboot`, all `rj_*_start|stop|restart`) require a second identical tap within ~2.5s.
  - Action audit log: `/tmp/portableops-remote-actions.log`

Set auth token (optional) in config:
```json
"remote": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8787,
  "token": "set-a-token-here"
}
```

### Config file
On first run, a config is created at:
`~/.config/launcher/dashboard.json`

Edit node hosts/endpoints to your Tailscale names or IPs, for example:
```json
{
  "refresh_seconds": 30,
  "idle_redraw_seconds": 2.0,
  "local_buttons_enabled": false,
  "backlight_level": 1.0,
  "backlight_pwm": false,
  "request_timeout_seconds": 2.0,
  "smb_detail_refresh_seconds": 60,
  "nodes": [
    {
      "name": "PiTemplar",
      "host": "100.113.244.100",
      "ports": [22, 8080],
      "health_url": "http://100.113.244.100:8080",
      "health_json_path": "",
      "health_expect": "",
      "smb": {
        "host": "100.113.244.100",
        "share": "private",
        "username": "bjorn",
        "password": "bjorn"
      }
    },
    {
      "name": "Bjorn",
      "host": "100.113.244.100",
      "ports": [22, 8000],
      "health_url": "http://100.113.244.100:8000",
      "health_json_path": "",
      "health_expect": ""
    }
  ],
  "angryoxide": {
    "interface": "wlan1",
    "start_monitor_cmd": "startmonitormode",
    "command": "/home/kali/angryoxide -i wlan1",
    "whitelist_flag": "--whitelist",
    "whitelist_networks": ["192.168.0.0/24"]
  },
  "raspyjack": {
    "service_names": ["raspyjack.service", "raspyjack-device.service", "raspyjack-webui.service"],
    "webui_service_names": ["raspyjack-webui.service", "caddy.service"],
    "webui_host": "127.0.0.1",
    "webui_port": 8080,
    "webui_url": "http://127.0.0.1:8080"
  },
"remote": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8787,
  "token": "",
  "safe_mode": true
}
}
```

Operational note:
- `wlan1` is treated as the persistent monitor interface. Launcher flows may temporarily switch it to managed mode for AP discovery, but AO stop should not switch it back to managed mode.
- RaspyJack `all start/stop/restart` flows should go through `/home/kari/Projects/start_raspyjack.sh` and `/home/kari/Projects/stop_raspyjack.sh`. The wrapper is responsible for launcher handoff sequencing; the actual 1.3in display adaptation still lives inside RaspyJack’s `LCD_1in44.py` and `raspyjack.py`.
- Dedicated `node:*` pages were retired in favor of a compact `Network Ops` page while keeping Bjorn/PiTemplar telemetry in the overview/web UI.
- `Network Ops` keeps `wlan0` as the permanent hotspot/client interface and inventories any additional wireless adapters without changing that policy.
- Foxhunt idle state no longer performs background scanning; a scan now starts only when explicitly requested from the page/menu.

### Troubleshooting
- If you see `No access to /dev/mem`, run with sudo:
  - `sudo ./bin/dashboard`
- If edge interrupt setup fails (`Failed to add edge detection`), dashboard now falls back to button polling automatically.
- If RaspyJack receives phantom button presses while dashboard is running:
  - keep `"local_buttons_enabled": false`
  - keep `"backlight_level": 1.0` (GPIO13 overlap safety)
  - keep `"backlight_pwm": false` (avoid GPIO13 PWM pulses)

## Run as Service
Install and start at boot:

```bash
cd ~/Projects/launcher
sudo ./install_dashboard_service.sh
```

Useful commands:

```bash
sudo systemctl status kari-dashboard.service
sudo journalctl -u kari-dashboard.service -f
sudo systemctl restart kari-dashboard.service
```

### Pi Zero 2 W Tuning
For Raspberry Pi Zero 2 W nodes, keep the dashboard conservative:

- Use `LAUNCHER_FPS=12` in `kari-dashboard.service`.
- Keep `LAUNCHER_ANIM_ENABLE=0` and recurring display effects disabled after startup.
- Keep `"refresh_seconds": 30` on Pi Zero 2 W class nodes unless you have measured spare CPU headroom.
- Keep `"idle_redraw_seconds": 2.0` or higher so the screen redraws on change instead of acting like a game loop.
- Keep `"smb_deep_stats_enabled": false` unless you explicitly want recursive share statistics on-screen.
- Prefer fixed Tailscale IPs or FQDNs in `dashboard.json` for remote nodes instead of relying on short hostnames from every client environment.

Recommended swap on Pi Zero 2 W:

- Add a `512M` swap file at `/swapfile`.
- Use `vm.swappiness=80` so the node has breathing room during transient Python/UI spikes.
- Do not use multi-gigabyte swap on SD-backed Pi Zero systems; it hides pressure and increases SD wear.

Apply on the live node as root:

```bash
sudo systemctl stop kari-dashboard.service
sudo install -d -m 0755 /etc/sysctl.d
sudo fallocate -l 512M /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
printf 'vm.swappiness=80\nvm.vfs_cache_pressure=150\n' | sudo tee /etc/sysctl.d/99-kari-memory.conf >/dev/null
sudo sysctl --system
sudo sed -i 's/^Environment=LAUNCHER_FPS=.*/Environment=LAUNCHER_FPS=12/' /etc/systemd/system/kari-dashboard.service
sudo systemctl daemon-reload
sudo systemctl start kari-dashboard.service
sudo systemctl restart kari-dashboard.service
swapon --show
systemctl status kari-dashboard.service --no-pager
```

Verify after the change:

```bash
free -h
swapon --show
systemctl show -p Environment kari-dashboard.service
top -b -n1 | head -n 20
```

Remove service:

```bash
cd ~/Projects/launcher
sudo ./uninstall_dashboard_service.sh
```

## Connectivity Watchdog
The watchdog checks and self-heals these paths:
- Internet reachability (ICMP ping targets)
- Tailscale health (`tailscaled.service` + local Tailscale IP)
- Launcher web UI (`http://127.0.0.1:8787/`)
- RaspyJack web UI (`http://127.0.0.1:8080`)

If a check fails, it restarts the related service(s). If failures continue for multiple cycles, it reboots the Pi.
By default, internet-only ping failures are treated as degraded if other enabled checks are healthy.
Defaults are conservative: 180s boot grace, launcher/tailscale/internet checks enabled, RaspyJack check disabled.

Install and enable watchdog:

```bash
cd ~/Projects/launcher
sudo ./install_watchdog_service.sh
```

Tune behavior in:
`/etc/default/kari-watchdog`

Common toggles:
- `KARI_WD_BOOT_GRACE_SECONDS=180`
- `KARI_WD_CHECK_RASPYJACK=false`
- `KARI_WD_NETWORK_SERVICES="NetworkManager.service"`
- `KARI_WD_TREAT_INTERNET_ONLY_DOWN_AS_FAILURE=false`

Useful commands:

```bash
sudo systemctl status kari-watchdog.timer
sudo systemctl status kari-watchdog.service
sudo journalctl -u kari-watchdog.service -f
```

Remove watchdog:

```bash
cd ~/Projects/launcher
sudo ./uninstall_watchdog_service.sh
```
