# K.A.R.I Launcher

K.A.R.I Launcher is a field dashboard and control surface for a Raspberry Pi with a Waveshare 1.3in 240x240 display, joystick, and three front buttons. It provides a local device UI, a remote web mirror, service management, local node telemetry, and page-specific workflows for supported modules.

This repository is for the launcher itself. It is not a toy UI sample. It is closer to a field dashboard with opinions, sharp edges, and a tendency to notice when the rest of your stack is lying to you. Treat it like an operational tool:

- Use it only on hardware, services, and networks you own or are explicitly authorized to administer.
- Read every command you wire into it before enabling remote control or unattended startup.
- Assume any page that manipulates radios, services, or managed apps can interrupt connectivity.
- Validate changes from a console or SSH session before relying on the physical display alone.
- Do not expose the remote UI to untrusted networks without an auth token and a clear threat model.

## Hardware

Current hardware target:

- Waveshare 1.3in LCD, 240x240
- 5-way joystick: `UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`
- Front buttons: `KEY1`, `KEY2`, `KEY3`
- Optional PiSugar battery telemetry

Default input pin mapping is defined in [dashboard.py](/home/kari/Projects/kari-launcher/src/launcher/dashboard.py):

```json
"input": {
  "pins": {
    "UP": 6,
    "DOWN": 19,
    "LEFT": 5,
    "RIGHT": 26,
    "OK": 13,
    "KEY1": 21,
    "KEY2": 20,
    "KEY3": 16
  }
}
```

The old `A / B / X / Y` wording no longer applies to the physical device. Those names still appear internally in the remote action layer as compatibility aliases for page navigation and context buttons. The hardware in your hand, however, is joystick + `KEY1`/`KEY2`/`KEY3`, not a tiny gamepad from a previous life.

## What It Does

The launcher currently provides these top-level pages:

- `Overview`: local system summary, Tailscale IP, battery, CPU/RAM, GPS summary, node summary
- `GPS`: lock, coordinates, altitude, speed, satellites, active device path
- `Network Ops`: `wlan0` health, detected wireless adapters, service restarts, interface mode changes, launcher restart, device reboot/shutdown
- `Lantern`: local subnet discovery using neighbor cache with optional active enrichment
- `FoxHunt`: saved-session and live-target tracking workflow
- `Wifite`: passive target staging plus a generic configurable command runner
- `RaspyJack`: managed-app handoff page for starting/stopping the RaspyJack stack
- `AngryOxide`: run status, log/summary view, interface selection, and workflow control

It also exposes:

- a remote web UI on port `8787` by default
- a framebuffer-backed virtual device preview
- remote action logging
- optional watchdog self-healing for launcher, Tailscale, internet reachability, and RaspyJack

## Repository Layout

```text
kari-launcher/
├─ bin/
│  ├─ dashboard
│  ├─ launcher
│  └─ watchdog
├─ src/launcher/
│  ├─ dashboard.py
│  ├─ foxhunt.py
│  ├─ wifite_prep.py
│  ├─ angryoxide_menu.py
│  ├─ lantern.py
│  └─ ...
├─ systemd/
│  ├─ kari-dashboard.service
│  ├─ kari-watchdog.service
│  ├─ kari-watchdog.timer
│  └─ kari-watchdog.env
├─ install_dashboard_service.sh
├─ uninstall_dashboard_service.sh
├─ install_watchdog_service.sh
├─ uninstall_watchdog_service.sh
├─ requirements.txt
└─ VERSION
```

## Controls

General device control model:

- `LEFT` / `RIGHT`: change page
- `UP` / `DOWN`: scroll or move selection
- `OK`: open a page menu, confirm a highlighted item, or refresh on simple pages
- `KEY1`: primary page action in some contexts
- `KEY2`: alternate page action in some contexts
- `KEY3`: home/page-exit behavior where supported

Footer hints on the physical display are the source of truth for the current page state.

Common patterns:

- `Overview`: `OK` refresh, `LEFT/RIGHT` change page
- `GPS`: `UP/DOWN` scroll, `OK` refresh
- `Network Ops`: `UP/DOWN` scroll, `OK` open menu, `KEY2` refresh
- `Lantern`: `UP/DOWN` move through hosts, `OK` open menu, `KEY2` refresh
- `FoxHunt`, `Wifite`, `AngryOxide`: menu-driven pages with scan lists and context-specific footer hints
- `RaspyJack`: action-row page with launch/stop controls

## Installation

### 1. Clone the repository

```bash
cd ~/Projects
git clone <your-repo-url> kari-launcher
cd ~/Projects/kari-launcher
```

### 2. Create a Python environment

The launcher wrapper will try to create and use `.venv` automatically, but for first-time setup it is better to do it explicitly:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the dashboard once

```bash
./bin/dashboard
```

On first run the launcher writes:

```text
~/.config/launcher/dashboard.json
```

Edit that file before enabling the service.

### 4. Enable local buttons

Physical controls are disabled by default in the config:

```json
"local_buttons_enabled": false
```

For an actual handheld build, set it to:

```json
"local_buttons_enabled": true
```

### 5. Install the systemd service

```bash
cd ~/Projects/kari-launcher
sudo ./install_dashboard_service.sh
```

Useful service commands:

```bash
sudo systemctl status kari-dashboard.service
sudo journalctl -u kari-dashboard.service -f
sudo systemctl restart kari-dashboard.service
```

## Quick Start Tutorial

### Bring up the launcher on a new Pi

1. Connect the display and confirm SPI/GPIO are enabled.
2. Clone the repo and install requirements.
3. Run `./bin/dashboard` once to generate `~/.config/launcher/dashboard.json`.
4. Set `"local_buttons_enabled": true`.
5. Review `hardware`, `input`, `network_ops`, and `remote` sections in the config.
6. Restart the launcher and confirm the screen renders.
7. Open `http://<pi-ip>:8787` from another device and verify the remote UI mirrors the physical display.
8. Only after that, install the systemd service.

### First operational checks

After boot:

- `Overview` should show hostname, Tailscale IP, battery, CPU, RAM, and GPS summary.
- `Network Ops` should show `wlan0` plus any attached external radios.
- `Lantern` should populate once the Pi has ARP/neigh data or after a refresh.
- The web UI should load without depending on the physical display.

If any of those fail, fix them before enabling watchdog or remote control. Future-you will appreciate the restraint.

## Configuration

Main config path:

```text
~/.config/launcher/dashboard.json
```

Important top-level keys:

- `refresh_seconds`: full snapshot refresh cadence
- `idle_redraw_seconds`: redraw cadence when nothing changes
- `history_points`: web history graph depth
- `local_buttons_enabled`: enables joystick/front buttons
- `backlight_level`: display backlight level
- `backlight_pwm`: PWM backlight mode toggle
- `request_timeout_seconds`: network probe timeout
- `smb_deep_stats_enabled`: enable recursive SMB stats
- `smb_detail_refresh_seconds`: SMB deep-stat cache interval
- `hardware`: display backend, rotation, SPI, and pin settings
- `input`: joystick/button pin mapping and debounce
- `managed_apps`: managed fullscreen or handoff-style apps
- `nodes`: remote systems shown in overview and node summaries
- `raspyjack`, `angryoxide`, `foxhunt`, `wifite`, `lantern`, `network_ops`, `remote`: page-specific settings

### Minimal example config

```json
{
  "refresh_seconds": 30,
  "idle_redraw_seconds": 2.0,
  "local_buttons_enabled": true,
  "backlight_level": 1.0,
  "backlight_pwm": false,
  "network_ops": {
    "primary_iface": "wlan0",
    "monitor_iface": "wlan1",
    "wifi_profile": "YourSSID",
    "networkmanager_service": "NetworkManager.service",
    "tailscale_service": "tailscaled.service",
    "reboot_cmd": "systemctl reboot"
  },
  "remote": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8787,
    "token": "",
    "safe_mode": false
  }
}
```

## Node Statuses

`Bjorn` and `PiTemplar` are not built-in products. They are just example node definitions in the default config. New users should replace them with their own systems and pretend those names were never there.

The launcher’s node model is simple:

- `host`: the main target hostname or IP
- `ports`: optional TCP ports to probe
- `health_url`: optional HTTP endpoint to fetch
- `health_json_path`: optional dot-path into a JSON response
- `health_expect`: optional text that must appear in the endpoint result
- `smb`: optional SMB share probe block

Node status is derived like this:

- `online`: the node is reachable and its health/SMB checks do not report failure
- `degraded`: the host is reachable but a health or SMB check failed
- `offline`: no successful reachability signal was found

### Example: simple node check

```json
"nodes": [
  {
    "name": "SensorPi",
    "host": "sensorpi.tailnet.ts.net",
    "ports": [22, 8080],
    "health_url": "http://sensorpi.tailnet.ts.net:8080/health",
    "health_json_path": "status",
    "health_expect": "ok"
  }
]
```

### Example: SMB-enabled node

```json
"nodes": [
  {
    "name": "FilesPi",
    "host": "filespi.tailnet.ts.net",
    "ports": [22, 445],
    "health_url": "",
    "health_json_path": "",
    "health_expect": "",
    "smb": {
      "host": "filespi.tailnet.ts.net",
      "share": "private",
      "username": "filespi",
      "password": ""
    }
  }
]
```

### How to add your own nodes

1. Open `~/.config/launcher/dashboard.json`.
2. Replace the sample `nodes` list with your own systems.
3. Start with only `name`, `host`, and a small `ports` list.
4. Add `health_url` only after you have confirmed the endpoint manually with `curl`.
5. Add `health_json_path` if the endpoint returns JSON and you only care about one field.
6. Add `health_expect` if you want the launcher to treat anything else as degraded.
7. Add the optional `smb` block only if you need SMB visibility and understand the credential risk.
8. Restart the launcher and watch `Overview` and the web node summary.

### Health endpoint examples

Plain text endpoint:

```json
{
  "name": "EdgeNode",
  "host": "edge.tailnet.ts.net",
  "ports": [22, 9000],
  "health_url": "http://edge.tailnet.ts.net:9000/health",
  "health_expect": "ok"
}
```

JSON endpoint:

```json
{
  "name": "LabNode",
  "host": "labnode.tailnet.ts.net",
  "ports": [22, 5000],
  "health_url": "http://labnode.tailnet.ts.net:5000/api/status",
  "health_json_path": "service.state",
  "health_expect": "ready"
}
```

Warnings for node setup:

- Do not put production passwords into a public repo.
- Prefer hostnames or fixed Tailscale addresses over changing DHCP addresses.
- Test every `health_url` manually before assuming launcher status is meaningful.
- Do not enable deep SMB stats casually on slow storage or weak Pi hardware.
- Treat SMB credentials in `dashboard.json` as sensitive local secrets.

## Page Guide

### Overview

Shows:

- Tailscale IPv4
- battery percentage
- CPU temperature and usage
- RAM usage
- GPS fix summary
- node summary counts

Best use:

- quick boot sanity check
- confirming the device is alive before launching anything heavier

### GPS

Shows:

- fix label
- coordinates
- satellite counts
- altitude
- speed
- GPS device path

Best use:

- checking receiver health
- confirming gpsd is actually providing data

### Network Ops

Shows:

- `wlan0` state, IP, and active NetworkManager profile
- default route
- NetworkManager and Tailscale service states
- external wireless adapter inventory

Menu actions:

- refresh
- reconnect `wlan0`
- reset `wlan1`
- restart launcher
- restart NetworkManager
- restart Tailscale
- change interface modes on external radios
- shutdown or reboot the device

Warnings:

- mode changes and resets can interrupt active workflows
- restarting networking can drop your SSH session
- restarting the launcher from the launcher is expected behavior now, but still interrupts the UI briefly

### Lantern

Passive local subnet discovery page.

Shows:

- local interface
- local IP and gateway
- cached neighbor entries
- best-effort hostnames
- vendor-enriched labels when available

Menu actions:

- refresh data
- clear cache

Use it for:

- confirming who is present on the local subnet
- checking gateway and device presence

### FoxHunt

Target-tracking page with scan, lock, and hunt states. It keeps saved sessions under:

```text
~/.local/share/launcher/foxhunt
```

Best use:

- lock onto one target
- watch RSSI trend over time
- mark points and save a session

Warnings:

- it depends on the external radio selection being correct
- scanning and tracking workflows can temporarily reconfigure an external adapter

### Wifite

Current implementation in this launcher is a passive prep and generic command-runner page.

What it does today:

- scan nearby APs
- stage a target SSID/BSSID/channel/security tuple
- select the external interface used for passive scans
- run a configured command via `wifite.run_command`
- capture and display stdout/stderr in the lower panel and web UI

What it does not guarantee:

- that your configured command is safe
- that the command is lightweight enough not to impact the launcher
- that the command supports non-interactive execution

Warnings:

- anything you place in `run_command` is your responsibility
- long-running or noisy commands can degrade launcher responsiveness
- do not commit live command strings or secrets into a public repo

Example placeholder:

```json
"wifite": {
  "interface": "wlan1",
  "scan_max_results": 32,
  "scan_interval_active_seconds": 4.0,
  "run_command": "/home/kari/.local/bin/sd-list"
}
```

The runner exports:

- `WIFITE_TARGET_SSID`
- `WIFITE_TARGET_BSSID`
- `WIFITE_TARGET_CHANNEL`
- `WIFITE_TARGET_SECURITY`

so local wrapper commands can consume the selected target context.

### RaspyJack

Managed-app page for handing the display over to RaspyJack through external wrapper scripts.

Defaults:

- start: `/home/kari/Projects/start_raspyjack.sh`
- stop: `/home/kari/Projects/stop_raspyjack.sh`

Use it for:

- clean launcher-to-RaspyJack handoff
- stopping RaspyJack and returning to the launcher

### RaspyJack Setup Notes

The launcher assumes RaspyJack is already installed separately. The launcher does not install RaspyJack for you, and it does not want RaspyJack permanently owning the display or grabbing input at boot.

The intended model is:

- launcher starts on boot
- launcher owns the screen and controls by default
- RaspyJack is launched on demand from the `RaspyJack` page
- wrapper scripts handle the handoff
- when RaspyJack exits, the launcher comes back

In practice that means new users should:

1. Install RaspyJack manually first.
2. Make sure it can run successfully on its own before integrating it with K.A.R.I.
3. Disable or avoid any RaspyJack service that would auto-start it at boot.
4. Provide launcher-controlled wrapper scripts for start and stop.

Why this matters:

- if RaspyJack starts itself as a service, it will fight the launcher for display ownership
- if both stacks think they own GPIO/input, you get ghost presses, missing input, or a dead screen
- if both stacks think they own the framebuffer, you get chaos with extra confidence

### Example RaspyJack Handoff Model

Your local wrapper scripts should be the traffic cops.

Typical start wrapper responsibilities:

- stop `kari-dashboard.service`
- prepare any RaspyJack-specific environment
- launch RaspyJack

Typical stop wrapper responsibilities:

- stop RaspyJack services/processes
- restart `kari-dashboard.service`

The launcher points to those wrappers here:

```json
"managed_apps": {
  "raspyjack": {
    "label": "RaspyJack",
    "start_cmd": "/home/kari/Projects/start_raspyjack.sh",
    "stop_cmd": "/home/kari/Projects/stop_raspyjack.sh",
    "status_cmd": "systemctl is-active raspyjack.service raspyjack-device.service raspyjack-webui.service",
    "takes_over_display": true
  }
}
```

If you keep the wrappers stable, the launcher integration stays simple.

### Making RaspyJack Work on the Waveshare 1.3in Display

Out of the box, RaspyJack is aimed at a 1.44in display layout. K.A.R.I is not. If you want the handoff to feel clean, RaspyJack needs to be taught the new screen geometry.

What needs to change on the RaspyJack side:

- display driver selection
- width and height assumptions
- rotation/invert settings
- font sizing
- menu spacing
- any hard-coded coordinates copied from the 1.44in layout

The target you want RaspyJack to match is:

- Waveshare 1.3in LCD
- `240x240`

When adapting RaspyJack, look for:

- files like `LCD_1in44.py`
- any display abstraction layer that hard-codes `1.44`
- render code that assumes the old resolution
- fixed pixel offsets for headers, menus, and icons

The fix is not magic. It is mostly boring, honest UI plumbing:

- swap the driver/config to the 1.3in model
- set the correct resolution
- update rotation so the screen is upright in your case
- reflow menu rows so they fit a square `240x240` layout
- check that button prompts and status text do not clip

The easiest validation loop:

1. Run RaspyJack directly outside the launcher.
2. Verify the screen is upright.
3. Verify text is not clipped.
4. Verify menus fit.
5. Verify the device returns cleanly to the launcher when stopped.

Warnings:

- do not try to debug launcher handoff and RaspyJack display porting at the same time if you can avoid it
- first make RaspyJack render correctly by itself
- then wire in the launcher handoff
- then test start/stop behavior from the `RaspyJack` page

### AngryOxide

Managed workflow page with:

- run status
- summary view
- log view
- interface selection
- profile-based launch control

Warnings:

- this page is for operator-owned environments only
- do not assume the defaults are appropriate for your hardware or legal environment
- treat external command wiring as a deliberate administrative action

## Remote Web UI

Default URL:

```text
http://<pi-ip>:8787
```

Provides:

- page navigation
- action buttons per page
- launcher status panel
- framebuffer mirror
- remote device controls

Important endpoints:

- `GET /api/status`
- `POST /api/action`
- `GET /api/frame.png`

Example action call:

```bash
curl -X POST http://<pi-ip>:8787/api/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"networkops"}'
```

Optional token:

```json
"remote": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8787,
  "token": "change-this",
  "safe_mode": true
}
```

Remote safety notes:

- set a token before exposing the UI beyond a trusted LAN
- `safe_mode` reduces what remote callers can trigger
- high-impact actions require confirmation logic
- all remote actions are logged to `/tmp/portableops-remote-actions.log`

## Watchdog

The optional watchdog checks:

- internet reachability
- Tailscale health
- launcher availability
- optional RaspyJack availability

Install it with:

```bash
cd ~/Projects/kari-launcher
sudo ./install_watchdog_service.sh
```

Default runtime config lives at:

```text
/etc/default/kari-watchdog
```

Default behavior:

- boot grace period before heal attempts
- periodic checks via systemd timer
- service restarts on failure
- optional reboot after repeated failures

Warnings:

- do not enable aggressive reboot behavior until you trust your health checks
- a bad watchdog config can turn transient failures into reboot loops

## Tutorials

### Tutorial: add your own node summary

1. Pick one device you own.
2. Add it to `nodes` with just `name`, `host`, and `ports`.
3. Restart the launcher.
4. Verify it appears in the web UI node summary.
5. Add an HTTP health endpoint only after the basic host probe works.

### Tutorial: bring up Lantern on a new network

1. Confirm `wlan0` has an IP.
2. Open `Lantern`.
3. Press refresh.
4. If names are sparse, that is normal. The page uses local neighbor data and best-effort enrichment, not router-specific discovery magic.
5. Use it as a quick subnet presence panel, not a full inventory system.

### Tutorial: enable safe remote access

1. Set a non-empty `remote.token`.
2. Temporarily set `"safe_mode": true`.
3. Restart `kari-dashboard.service`.
4. Confirm page navigation and refresh work remotely.
5. Only then decide whether you want to permit more powerful remote actions.

### Tutorial: install launcher and watchdog cleanly

```bash
cd ~/Projects/kari-launcher
sudo ./install_dashboard_service.sh
sudo ./install_watchdog_service.sh
sudo systemctl status kari-dashboard.service
sudo systemctl status kari-watchdog.timer
```

## Troubleshooting

### The physical buttons do nothing

Check:

- `"local_buttons_enabled": true`
- correct pin mapping in `input.pins`
- your display/input wiring matches the Waveshare board, not an older DisplayHAT Mini layout

### The web UI loads but actions do nothing

Check:

- `remote.enabled`
- `remote.port`
- `remote.token` if one is required
- the launcher log:

```bash
sudo journalctl -u kari-dashboard.service -f
```

### Nodes always show offline

Check:

- hostname or IP is correct
- ports are actually open
- the health endpoint responds locally with `curl`
- SMB settings are correct and intentionally enabled

### The launcher crashes or restarts repeatedly

Check:

- `dashboard.json` is valid JSON
- any configured shell command strings are quoted correctly
- custom wrapper scripts exist and are executable

### A page becomes sluggish

Common causes:

- heavy external commands
- over-frequent scans
- deep SMB stats on weak hardware
- over-aggressive remote refresh usage

## Operational Warnings

- This launcher can manage services, radios, and external workflows. Review every command path before enabling unattended use.
- Keep secrets out of the repository. Store site-specific credentials only in the local config on the target device.
- Never assume sample hostnames, interfaces, or commands fit your environment unchanged.
- Validate every hardware pin mapping on the bench before field deployment.
- If a page controls a managed app that takes over the display, make sure you have a reliable way back to the launcher.

## Removing Services

```bash
cd ~/Projects/kari-launcher
sudo ./uninstall_watchdog_service.sh
sudo ./uninstall_dashboard_service.sh
```

## Current Defaults Worth Reviewing

Before you call a setup complete, review these values in your local config:

- `local_buttons_enabled`
- `hardware.rotation`
- `hardware.invert`
- `network_ops.primary_iface`
- `network_ops.monitor_iface`
- `network_ops.wifi_profile`
- `remote.token`
- `remote.safe_mode`
- `nodes`
- any page-specific command strings

If you are publishing this repository, publish code and defaults, not your live site config.
