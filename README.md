# K.A.R.I Launcher

K.A.R.I Launcher is a field dashboard and control surface for a Raspberry Pi with a Waveshare 1.3in 240x240 display, a joystick, and three front buttons. It gives you a local device UI, a remote web mirror, module-specific workflows, service control, and a cleaner way to keep a small operations box honest.

This repository is the launcher, not a toy UI demo and not a generic kiosk sample. It sits in the middle of displays, services, radios, helper scripts, and whatever else you wire into it. Treat it like a real tool:

- Use it only on hardware, services, and networks you own or are explicitly authorized to administer.
- Read every command, wrapper, and service name before enabling unattended startup or remote control.
- Assume any page that manipulates radios, interfaces, or managed apps can interrupt connectivity.
- Validate changes from SSH or a console before trusting the handheld UI alone.
- Keep site-specific secrets, tokens, and credentials out of the repository.

The launcher is supposed to be useful, not mystical. When it behaves well, it should tell you what it is doing. When it behaves badly, it should at least fail loudly enough that you can go looking for the right thing.

## Contents

- [Hardware Target](#hardware-target)
- [Current Page Set](#current-page-set)
- [Repository Layout](#repository-layout)
- [Controls](#controls)
- [Installation](#installation)
- [DIY Build on Raspberry Pi OS](#diy-build-on-raspberry-pi-os)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Page Guide](#page-guide)
- [Overview](#overview)
- [GPS](#gps)
- [Network-Ops](#network-ops)
- [Lantern](#lantern)
- [SocketWatch](#socketwatch)
- [TrafficView](#trafficview)
- [Kismet](#kismet)
- [FoxHunt](#foxhunt)
- [Wifite](#wifite)
- [RaspyJack](#raspyjack)
- [AngryOxide](#angryoxide)
- [Remote Web UI](#remote-web-ui)
- [Nodes and Status Boards](#nodes-and-status-boards)
- [Upstream Projects](#upstream-projects)
- [RaspyJack Patch Bundle](#raspyjack-patch-bundle)
- [Watchdog](#watchdog)
- [Troubleshooting](#troubleshooting)
- [Operational Warnings](#operational-warnings)
- [Service Removal](#service-removal)

## Hardware Target

Current hardware target:

- Waveshare 1.3in LCD
- 240x240 resolution
- 5-way joystick: `UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`
- front buttons: `KEY1`, `KEY2`, `KEY3`
- optional PiSugar telemetry

Default input pin mapping lives in [dashboard.py](/home/kari/Projects/kari-launcher/src/launcher/dashboard.py).

The old `A / B / X / Y` language belongs to the earlier DisplayHAT Mini era. The physical unit this launcher is built around is joystick plus three front keys. Some internal compatibility aliases still use older names, but the handheld control model is not that board anymore.

## Current Page Set

The launcher currently ships with these user-facing pages:

- `Overview`: local health, battery, CPU, RAM, Wi-Fi, Tailscale, node summary
- `GPS`: lock, position, speed, altitude, satellites, active GPS device
- `Network Ops`: interface state, service state, mode switching, recovery actions
- `Lantern`: connected-LAN discovery and per-host service inventory
- `SocketWatch`: local sockets and connection summary
- `TrafficView`: per-interface traffic counters and rates
- `Kismet`: passive capture status, source reporting, device browse, FoxHunt handoff
- `FoxHunt`: target selection, target lock, hunt flow, session saves
- `Wifite`: passive target staging plus a generic configurable command runner
- `Pika`: generic launcher-managed handoff slot for a second full-screen app
- `RaspyJack`: launcher-managed handoff into a separate full-screen stack
- `AngryOxide`: run status, logs, target selection, workflow control

The launcher also provides:

- a remote web UI on port `8787` by default
- a virtual device mirror backed by the real framebuffer
- remote action handling
- optional watchdog recovery

## Repository Layout

```text
kari-launcher/
├─ bin/
│  ├─ dashboard
│  ├─ launcher
│  └─ watchdog
├─ scripts/
│  ├─ kismet-source-autoconfig.sh
│  └─ kismet.service.override.conf
├─ src/launcher/
│  ├─ dashboard.py
│  ├─ foxhunt.py
│  ├─ wifite_prep.py
│  ├─ angryoxide_menu.py
│  ├─ lantern.py
│  ├─ ops_pages.py
│  └─ ...
├─ systemd/
│  ├─ kari-dashboard.service
│  ├─ kari-watchdog.service
│  ├─ kari-watchdog.timer
│  └─ kari-watchdog.env
├─ third_party/
│  └─ raspyjack_patch/
├─ install_dashboard_service.sh
├─ uninstall_dashboard_service.sh
├─ install_watchdog_service.sh
├─ uninstall_watchdog_service.sh
├─ start_raspyjack.sh
├─ stop_raspyjack.sh
├─ requirements.txt
└─ VERSION
```

The main code paths you will actually edit most often are:

- `src/launcher/dashboard.py`
- `src/launcher/lantern.py`
- `src/launcher/foxhunt.py`
- `src/launcher/wifite_prep.py`
- `src/launcher/angryoxide_menu.py`
- `src/launcher/ops_pages.py`

## Controls

General control model:

- `LEFT` / `RIGHT`: page change, unless the current page is intentionally trapping local navigation
- `UP` / `DOWN`: scroll, move selection, or move between records
- `OK`: open a page menu or confirm the highlighted item
- `KEY1`: page-specific action in some flows
- `KEY2`: alternate action, often refresh
- `KEY3`: home/escape behavior where supported

Footer hints on the physical display are the source of truth for the current page state.

Common patterns:

- `Overview`: `OK` refresh
- `GPS`: `UP/DOWN` scroll, `OK` refresh
- `Network Ops`: `UP/DOWN` scroll, `OK` menu, `KEY2` refresh
- `Lantern`: idle menu with `Light the Way`, then `UP/DOWN` pages through discovered hosts
- `SocketWatch`: `UP/DOWN` through socket rows, `OK` menu
- `TrafficView`: `UP/DOWN` through interface rows, `OK` menu
- `Kismet`: `OK` menu, `Browse Devices` splits to `Wireless` or `Bluetooth`, `UP/DOWN` navigates the active picker
- `FoxHunt`, `Wifite`, `AngryOxide`: menu-driven pages with mode-specific footer hints
- `RaspyJack`: handoff and return controls

## Installation

### 1. Clone the repository

```bash
cd ~/Projects
git clone <your-repo-url> kari-launcher
cd ~/Projects/kari-launcher
```

### 2. Create the virtual environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The launcher wrapper will try to help itself, but first-time setup is cleaner if you do this explicitly instead of hoping it reads your mind.

### 3. Run the dashboard once

```bash
./bin/dashboard
```

On first run it will write:

```text
~/.config/launcher/dashboard.json
```

That file becomes your live local truth. Edit it before enabling the service.

### 4. Enable local buttons

Physical controls are disabled by default:

```json
"local_buttons_enabled": false
```

For a real handheld build:

```json
"local_buttons_enabled": true
```

### 5. Install the launcher service

```bash
cd ~/Projects/kari-launcher
sudo ./install_dashboard_service.sh
```

Useful commands:

```bash
sudo systemctl status kari-bootscreen.service
sudo systemctl status kari-dashboard.service
sudo journalctl -u kari-dashboard.service -f
sudo systemctl restart kari-dashboard.service
```

## DIY Build on Raspberry Pi OS

Nothing in this launcher is married to Kali. Kali just happened to be the box on the bench when a lot of the defaults were first written down.

If somebody wants the barebones route on Raspberry Pi OS, that is a perfectly sensible choice. The launcher itself is just Python, systemd units, GPIO/display handling, a small web server, and whatever external tools you choose to bolt onto it. The part that needs care is not "can it run on Raspberry Pi OS?" The part that needs care is "did you wire your own services, interfaces, and helper paths honestly?"

### 1. Start with a sane Pi

Use a current Raspberry Pi OS image and do the boring foundations first:

```bash
sudo raspi-config
```

Enable:

- `SPI` for the ST7789 display
- `I2C` only if your own build needs it
- `SSH` if you want to manage the box remotely

Then bring the base system up to date and install the pieces the launcher expects to find:

```bash
sudo apt update
sudo apt install -y \
  git \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  build-essential \
  python3-setuptools \
  python3-wheel \
  network-manager \
  curl \
  util-linux
```

Notes:

- `util-linux` matters because the launcher uses `script(1)` when starting `AngryOxide` so PTY-hungry tools behave less badly under systemd.
- If your Raspberry Pi OS release has to build `Pillow` or `pygame` from source instead of pulling wheels, install the matching image and SDL development headers for that release before blaming Python for your afternoon.

### 2. Clone the repo and build the venv

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone <your-repo-url> kari-launcher
cd ~/Projects/kari-launcher
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements.txt
```

Current Python package set in [requirements.txt](/home/kari/Projects/kari-launcher/requirements.txt):

- `Pillow`
- `pygame`
- `RPi.GPIO`
- `spidev`
- `numpy`
- `st7789`
- `displayhatmini`

Why those matter:

- `Pillow` and `pygame` are the rendering stack
- `RPi.GPIO`, `spidev`, and `st7789` are the physical-panel and button path
- `numpy` is used by the display path when the ST7789 backend is active
- `displayhatmini` stays in the list because the code still supports that older backend for compatibility

If you skip the venv and rely on whatever your distro Python happens to have lying around, you are volunteering for a class of bugs that do not deserve your loyalty.

### 3. Run it once and let it write the real config

```bash
./bin/dashboard
```

First run creates:

```text
~/.config/launcher/dashboard.json
```

That file is not optional decoration. It is the live contract between your hardware, your services, and the launcher.

At minimum, review these blocks before you install any service:

- `hardware`
- `input`
- `network_ops`
- `managed_apps`
- `raspyjack`
- `angryoxide`
- `kismet`
- `remote`

For a real joystick-plus-three-keys handheld, make sure this is true:

```json
"local_buttons_enabled": true
```

### 4. Understand the launcher's ownership model

This matters more than the package list.

- `wlan0` is usually your management link
- one external adapter is usually your monitor or capture radio
- Kismet, FoxHunt, Wifite, AngryOxide, and RaspyJack can all fight over radios if you let them
- the launcher will happily run with bad assumptions if you feed it bad assumptions

Do not carry our interface names forward just because they worked on our box. Pick your own ownership model and write it down in config.

Minimal Raspberry Pi OS-flavoured example:

```json
{
  "local_buttons_enabled": true,
  "network_ops": {
    "primary_iface": "wlan0",
    "monitor_iface": "wlan1",
    "wifi_profile": "YourSSID",
    "networkmanager_service": "NetworkManager.service",
    "tailscale_service": "tailscaled.service",
    "reboot_cmd": "systemctl reboot"
  },
  "managed_apps": {
    "raspyjack": {
      "label": "RaspyJack",
      "start_cmd": "/home/pi/Projects/kari-launcher/start_raspyjack.sh",
      "stop_cmd": "/home/pi/Projects/kari-launcher/stop_raspyjack.sh",
      "status_cmd": "systemctl is-active raspyjack.service raspyjack-device.service raspyjack-webui.service",
      "takes_over_display": true
    }
  },
  "raspyjack": {
    "service_names": [
      "raspyjack.service",
      "raspyjack-device.service",
      "raspyjack-webui.service"
    ],
    "webui_service_names": [
      "raspyjack-webui.service",
      "caddy.service"
    ],
    "webui_host": "127.0.0.1",
    "webui_port": 8080,
    "loot_path": "/home/pi/Projects/Raspyjack/loot",
    "primary_interface": "wlan0",
    "monitor_interface": "wlan1"
  },
  "angryoxide": {
    "interface": "wlan1",
    "start_monitor_cmd": "airmon-ng start wlan1",
    "command": "/home/pi/bin/angryoxide -i wlan1",
    "log_path": "/home/pi/Results/angryoxide-live.log",
    "results_dir": "/home/pi/Results",
    "results_prefix": "oxide"
  },
  "kismet": {
    "service_names": ["kismet.service"],
    "primary_interface": "wlan0",
    "networkmanager_service": "NetworkManager.service",
    "webui_host": "127.0.0.1",
    "webui_port": 2501,
    "capture_dirs": ["/var/log/kismet", "/home/pi/kismet"]
  }
}
```

Use that as a shape reference, not a sacred text.

### 5. Plumb Kismet into the launcher

Read the upstream docs first:

- <https://www.kismetwireless.net/docs/readme/>
- <https://github.com/kismetwireless/kismet>

Then do the launcher-specific wiring:

1. Install Kismet and make sure `kismet.service` works by itself before the launcher gets involved.
2. Decide which interface Kismet is allowed to own.
3. Install the launcher helper script and service override:

```bash
sudo install -m 0755 scripts/kismet-source-autoconfig.sh /usr/local/bin/kismet-source-autoconfig.sh
sudo install -d /etc/systemd/system/kismet.service.d
sudo install -m 0644 scripts/kismet.service.override.conf /etc/systemd/system/kismet.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart kismet.service
```

What that helper currently does:

- leaves `wlan0` alone
- auto-adds `hci0` only when Bluetooth exists at service start
- auto-prefers `wlan2` for passive Wi-Fi capture when it exists
- otherwise leaves Wi-Fi capture manual instead of silently stealing `wlan1`

If your box uses different interface names, edit [kismet-source-autoconfig.sh](/home/kari/Projects/kari-launcher/scripts/kismet-source-autoconfig.sh). Do not keep our `wlan2` policy on your machine out of nostalgia.

Then confirm the launcher config matches reality:

- `kismet.service_names`
- `kismet.webui_host`
- `kismet.webui_port`
- `kismet.capture_dirs`
- `kismet.networkmanager_service`

### 6. Plumb AngryOxide into the launcher

Read the upstream project first:

- <https://github.com/Ragnt/AngryOxide>

Then wire it honestly:

1. Install `angryoxide` somewhere stable.
2. Run it manually once, outside the launcher, and confirm your chosen adapter, result directory, and log path are correct.
3. Point the launcher at the real binary, not a wish:

- `angryoxide.command`
- `angryoxide.interface`
- `angryoxide.log_path`
- `angryoxide.results_dir`
- `angryoxide.results_prefix`

4. If your adapter needs an explicit monitor-mode command, set `angryoxide.start_monitor_cmd` to whatever actually works on your distro and driver stack.

Practical note:

- the launcher wraps AngryOxide through `script(1)` so the process gets a PTY under service control
- if your build uses a different binary name, wrapper script, or working directory, reflect that in the config instead of expecting autodetection to save you

### 7. Plumb RaspyJack into the launcher

Read upstream first:

- <https://github.com/7h30th3r0n3/Raspyjack>

Then do the launcher part:

1. Get RaspyJack working on Raspberry Pi OS by itself.
2. Make sure it does not auto-start and steal the display before K.A.R.I is ready.
3. Adapt [start_raspyjack.sh](/home/kari/Projects/kari-launcher/start_raspyjack.sh) and [stop_raspyjack.sh](/home/kari/Projects/kari-launcher/stop_raspyjack.sh) for your install.
4. Point `managed_apps.raspyjack.start_cmd` and `managed_apps.raspyjack.stop_cmd` at those wrappers.
5. Set `raspyjack.service_names`, `raspyjack.webui_service_names`, `raspyjack.webui_host`, `raspyjack.webui_port`, and `raspyjack.loot_path` to your real layout.

Why wrappers matter:

- the launcher needs to stop its own UI before handing the screen away
- RaspyJack needs a clean start path
- the launcher needs a clean return path after RaspyJack exits

If your RaspyJack install is not service-based, replace the `systemctl` calls in the wrappers with direct launch and shutdown commands. The launcher does not care whether the handoff target is a service or a script. It cares whether the handoff is clean.

If you need the 1.3in panel adaptation and return hook, inspect [third_party/raspyjack_patch](/home/kari/Projects/kari-launcher/third_party/raspyjack_patch). It is a narrow patch bundle, not a claim that we have somehow become RaspyJack headquarters.

### 8. Install the launcher service

Once the config is real and the external tools are real:

```bash
sudo ./install_dashboard_service.sh
```

That installs and enables:

- `kari-bootscreen.service`
- `kari-dashboard.service`

It also installs `termie.service` as part of the same service bundle so the launcher-managed handoff target is present when you choose to use it.

Useful checks:

```bash
sudo systemctl status kari-bootscreen.service
sudo systemctl status kari-dashboard.service
sudo journalctl -u kari-dashboard.service -f
```

The dashboard service intentionally runs as `root`. That is not because root is fashionable. It is because GPIO access, framebuffer ownership, interface control, and service orchestration are all much less annoying when you stop pretending they are ordinary unprivileged desktop tasks.

### 9. First-run checklist for a DIY build

Before calling the build "done", confirm all of this:

1. `./bin/dashboard` starts cleanly inside the venv.
2. The physical panel is upright, readable, and not color-garbled.
3. Joystick and front-key input matches your wiring.
4. `http://<pi-ip>:8787` mirrors the device display.
5. `Network Ops` shows the interfaces you actually own.
6. `Kismet` reports the source policy you intended.
7. `AngryOxide` logs to the path you configured.
8. `RaspyJack` handoff stops the launcher, gives away the display cleanly, and returns cleanly.

If one of those fails, fix that layer first. Layering more tools on top of a lie does not make the lie more sophisticated.

## Quick Start

### First boot on a new Pi

1. Confirm SPI and GPIO are enabled.
2. Clone the repo and install dependencies.
3. Run `./bin/dashboard` once.
4. Enable local buttons in `~/.config/launcher/dashboard.json`.
5. Review `hardware`, `input`, `network_ops`, `remote`, and any module-specific config you care about.
6. Start the launcher again and confirm the 240x240 screen is upright and readable.
7. Open `http://<pi-ip>:8787` and confirm the web mirror matches the device.
8. Only after that, install the systemd service.

### First sanity checks

After boot:

- the panel should show `booting.png` early instead of sitting black while the rest of the system wakes up
- the launcher should then crossfade into the normal `KARI.png` splash
- `Overview` should show hostname, Tailscale IP, battery, CPU, RAM, and local Wi-Fi state
- `Network Ops` should show `wlan0` and any attached external radios
- `Lantern` should show at least the local IP and gateway before you run discovery
- `Kismet` should report its active source policy clearly if you have it installed
- the web UI should load without depending on the physical screen

If those basics are broken, fix them first. Fancy workflows built on top of bad footing just give you faster confusion.

## Configuration

Main config path:

```text
~/.config/launcher/dashboard.json
```

Important top-level keys:

- `refresh_seconds`
- `idle_redraw_seconds`
- `history_points`
- `local_buttons_enabled`
- `backlight_level`
- `backlight_pwm`
- `request_timeout_seconds`
- `smb_deep_stats_enabled`
- `smb_detail_refresh_seconds`
- `hardware`
- `input`
- `managed_apps`
- `nodes`
- `raspyjack`
- `angryoxide`
- `foxhunt`
- `wifite`
- `lantern`
- `network_ops`
- `kismet`
- `remote`

Boot splash assets:

- `booting.png`: early boot screen shown by `kari-bootscreen.service`
- `KARI.png`: launcher startup splash shown by the dashboard itself

Practical reality:

- this project does not auto-detect your install layout
- it does not auto-discover your helper scripts
- it does not guess your service names
- it does not know which interfaces you consider sacred
- it does not know where your third-party tools live

You will almost certainly need to change paths, service names, interfaces, hostnames, and command strings to fit your own device.

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

## Page Guide

This section is the real operator manual. Every current module gets its own section and its own short walkthrough.

## Overview

What it shows:

- battery percentage
- CPU temperature
- CPU usage
- RAM usage
- local Wi-Fi state
- hostname
- Tailscale IP
- node summary

What it is for:

- quick health check
- "is this box alive and connected?"
- spotting low battery or thermal drift before you start other work

Tutorial:

1. Open `Overview`.
2. Confirm battery, CPU temp, CPU usage, and RAM all look sane.
3. Confirm `wlan0` is present and Tailscale IP is populated if you expect remote access.
4. Check the node summary cards.
5. If something is missing here, do not trust the more complicated pages yet.

Notes:

- `Overview` is meant to be glanceable, not encyclopedic.
- Local `wlan0` IP is shown on the device summary line as `W0 ...`.

## GPS

What it shows:

- GPS mode / fix state
- coordinates
- altitude
- speed
- satellite count
- active GPS device path

What it is for:

- confirming receiver health
- checking that gpsd is actually providing useful data

Tutorial:

1. Open `GPS`.
2. Press `OK` or `KEY2` to refresh if the page looks stale.
3. Check mode first, then satellite count, then coordinates.
4. If you have no fix, confirm the GPS device path is populated before blaming the sky.

Warnings:

- `GPS offline` can mean receiver issue, cable issue, or gpsd issue. It is not automatically the launcher's fault, tempting though that accusation may be.

## Network Ops

What it shows:

- primary interface state
- monitor interface state
- route and active Wi-Fi profile
- NetworkManager and Tailscale service state
- detected external adapters

Actions:

- refresh
- reconnect `wlan0`
- restart launcher
- restart NetworkManager
- restart Tailscale
- shutdown / reboot
- per-adapter mode control for external radios

Tutorial:

1. Open `Network Ops`.
2. Confirm `wlan0` is up and on the expected profile.
3. Confirm your external radios appear with useful labels.
4. If networking is confused, try `Reconnect wlan0` before the heavier options.
5. Use adapter mode control only on external radios, not your management link.

Warnings:

- restarting networking can drop SSH
- changing interface modes can break active capture workflows
- restarting the launcher from the launcher is expected and briefly interrupts the UI

## Lantern

What it is:

`Lantern` is the connected-LAN discovery page. It is the page you use when K.A.R.I is already on a network and you want to know who is around, what looks alive, and which open services the currently selected host is exposing.

Current workflow:

- idle mode shows local interface, local IP, and gateway
- `Light the Way` performs host discovery and selected-host service scans
- detail mode shows one host per device page

What it shows in detail mode:

- IP
- best available name or identifier
- MAC
- state / vendor
- wrapped open-port summary

Tutorial:

1. Confirm `wlan0` is connected.
2. Open `Lantern`.
3. Make sure the idle page shows the local IP and gateway.
4. Open the menu and choose `Light the Way`.
5. Wait for discovery and service probing to finish.
6. Use `UP/DOWN` to move host-by-host.
7. Use `LEFT` to return to the idle page.
8. Open the menu from detail mode if you want `Refresh` or `Exit`.

Warnings:

- the progress bar is real, not decorative
- the page is intentionally slower than a simple ARP view because it also gathers service data
- name resolution is best-effort; some devices will still look anonymous without richer local discovery sources

## SocketWatch

What it shows:

- local listening sockets
- connection counts
- protocol / port summary

What it is for:

- checking what this Pi is listening on
- spotting whether a local service is actually bound

Tutorial:

1. Open `SocketWatch`.
2. Scroll through the socket rows.
3. Confirm expected services are listening.
4. If something that should be local is missing, check `systemctl` and `journalctl` next.

Warnings:

- this is local-socket visibility, not a remote host scanner

## TrafficView

What it shows:

- per-interface RX/TX counters
- rough live rate estimates

What it is for:

- checking whether an interface is active at all
- spotting whether one interface is carrying all the traffic while another is idle

Tutorial:

1. Open `TrafficView`.
2. Move through interfaces with `UP/DOWN`.
3. Watch counters over a few refreshes.
4. Compare what you expect to what is actually moving traffic.

Warnings:

- it is a throughput glance page, not a full traffic analysis tool

## Kismet

What it is:

`Kismet` is the passive capture status page. It is the launcher-side control and summary view for a running Kismet stack, with enough local controls to survive the awkward moment where the remote browser becomes less useful than the device in your hand.

What it shows:

- service state
- web state
- active Wi-Fi source
- active Bluetooth source
- Wi-Fi AP / device counts
- Bluetooth device count
- selected device summary
- recent source / warning / log lines

What it can do:

- refresh Kismet status
- start, stop, or restart the Kismet service
- recover the management link by stopping Kismet and restarting NetworkManager
- browse detected devices on the 240x240 screen
- hand a selected Wi-Fi target into `FoxHunt`

### Kismet source policy on this build

This is important, because confusion here is expensive:

- `wlan0` is the management link and should stay out of Kismet
- if `wlan2` exists, Kismet auto-prefers `wlan2` at service start
- if `wlan2` does not exist, the launcher does not silently volunteer `wlan1`
- Bluetooth is auto-added only when `hci0` exists at service start

That behavior is implemented by:

- [kismet-source-autoconfig.sh](/home/kari/Projects/kari-launcher/scripts/kismet-source-autoconfig.sh)
- [kismet.service.override.conf](/home/kari/Projects/kari-launcher/scripts/kismet.service.override.conf)

Tutorial:

1. Install Kismet separately first.
2. Open `Kismet`.
3. Check the active Wi-Fi source and Bluetooth source lines.
4. Confirm `wlan0` is not being used as a Kismet source.
5. Use `Browse Devices` and choose `Wireless` or `Bluetooth`.
6. Move through devices with `UP/DOWN`.
7. Press `OK` to select one.
8. If the selected device is Wi-Fi and appropriate for `FoxHunt`, open the menu and use `Hunt`.

Warnings:

- if you force Kismet onto `wlan0`, you should expect to lose your management link
- source policy depends on what exists at service start, especially Bluetooth
- Kismet can coexist with admin/passive pages, but it still competes with other workflows that want to own the same capture radio
- startup log warnings such as duplicate alert registration are not the same thing as live device detections

## FoxHunt

What it is:

`FoxHunt` is the target lock and hunt page. It is meant for the off-network, radio-facing workflow where you care about selecting a target and tracking it over time, not doing connected-LAN service inventory.

What it shows:

- scan mode and selected interface
- target SSID / BSSID
- RSSI and trend state
- GPS sample state
- recent visible target list

Tutorial:

1. Open `FoxHunt`.
2. Start a scan.
3. Choose a target from the discovered wireless list.
4. Lock the target.
5. Start the hunt.
6. Watch RSSI trend and proximity state change as you move.
7. Save the session if you want a record.

Warnings:

- this page expects the external radio flow to be sane
- it is not the place for connected-LAN port scanning
- if Kismet is already using the same capture radio, expect contention unless you have separated radio ownership cleanly

## Wifite

What it is:

In this launcher, `Wifite` is a passive target staging page plus a generic command runner. The launcher-side page is intentionally limited to prep, selection, and command execution plumbing.

What it does:

- scans nearby APs
- stages target SSID / BSSID / channel / security
- lets you pick the external interface used for passive scans
- runs `wifite.run_command`
- captures stdout/stderr into the page and web UI

Environment exported to the configured command:

- `WIFITE_TARGET_SSID`
- `WIFITE_TARGET_BSSID`
- `WIFITE_TARGET_CHANNEL`
- `WIFITE_TARGET_SECURITY`

Tutorial:

1. Open `Wifite`.
2. Select a network.
3. Set the target.
4. Confirm the staged target fields look correct.
5. Trigger `Run`.
6. Watch the output panel for stdout/stderr from your configured command.

Warnings:

- the launcher does not guarantee your configured command is safe
- noisy commands can still affect responsiveness
- some terminal-hungry tools expect a TTY and will fail in a detached command-runner model
- do not commit secrets or live operational command strings to a public repo

Example:

```json
"wifite": {
  "interface": "wlan1",
  "scan_max_results": 32,
  "scan_interval_active_seconds": 4.0,
  "run_command": "/home/<user>/.local/bin/sd-list"
}
```

## Pika

What it is:

## RaspyJack

What it is:

`RaspyJack` is a managed handoff page. The launcher owns the screen by default, and this page hands the display and input stack off to RaspyJack through wrapper scripts.

What it expects:

- RaspyJack is already installed separately
- RaspyJack does not auto-own the screen at boot
- launcher wrappers control the start / stop handoff

Defaults:

- [start_raspyjack.sh](/home/kari/Projects/kari-launcher/start_raspyjack.sh)
- [stop_raspyjack.sh](/home/kari/Projects/kari-launcher/stop_raspyjack.sh)

Tutorial:

1. Get RaspyJack working correctly by itself first.
2. Disable any RaspyJack service that would auto-start and grab the display.
3. Adapt the wrapper scripts for your own paths and service names.
4. Point `managed_apps.raspyjack` at those wrappers.
5. Test start and stop from the launcher.

Warnings:

- do not debug display porting and launcher handoff at the same time if you can avoid it
- first make RaspyJack render correctly on your hardware
- then wire in the launcher handoff
- some RaspyJack payloads, especially Bluetooth-heavy ones like `WallOfFlippers`, depend on helper binaries and backend quirks outside the launcher's control

## AngryOxide

What it is:

`AngryOxide` is the launcher page for a separate managed workflow. It exposes run state, logs, target selection, and controls through the same device/web surface as the rest of the launcher.

What it shows:

- process status
- target context
- result counters
- recent log output

Tutorial:

1. Open `AngryOxide`.
2. Refresh the page and confirm the selected interface is what you expect.
3. Select a target if the workflow requires one.
4. Start the run.
5. Watch the status and log sections.
6. Stop the run cleanly before changing interface state elsewhere.

Warnings:

- this page controls an external workflow, not a toy simulator
- radio ownership still matters
- if the chosen adapter drifts out of the expected mode, use `Network Ops` to repair it before assuming the module is haunted

## Remote Web UI

Default URL:

```text
http://<pi-ip>:8787
```

It provides:

- page navigation
- page-specific action buttons
- launcher status panel
- framebuffer mirror
- remote directional controls

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

Warnings:

- set a token before exposing the UI anywhere you do not completely trust
- `safe_mode` exists for a reason
- remote actions are logged

## Nodes and Status Boards

`Bjorn` and `PiTemplar` in the screenshots and default examples are just local examples from this build. They are not magical built-ins and they are not required.

New users should replace them with their own systems in the `nodes` config.

Typical node data you can define:

- name
- host or IP
- ports to probe
- optional HTTP health endpoint
- optional SMB probe settings

Tutorial:

1. Pick one host you own.
2. Add a minimal node entry with `name`, `host`, and one port.
3. restart the launcher
4. confirm the node appears in `Overview`
5. add HTTP or SMB checks only after the basic probe works

The launcher is much happier when you teach it your world instead of making it pretend it already knows it.

## Upstream Projects

K.A.R.I Launcher does not exist in a vacuum. Several pages in this project are wrappers, handoff layers, or integration surfaces for other projects built by other people, and they deserve to be credited clearly.

### Kismet

- Upstream: <https://github.com/kismetwireless/kismet>
- Official project: <https://www.kismetwireless.net>
- Primary developer / long-time maintainer: Mike Kershaw (`dragorn`)

Why it matters here:

- the `Kismet` launcher page is a control and status layer around a separately installed Kismet service
- the launcher-side `wlan2` / Bluetooth autostart policy is our integration logic, not Kismet's upstream default behavior

If you use the `Kismet` page, read the upstream Kismet docs too. They know more about Kismet than this launcher ever will, and pretending otherwise would be a very silly hobby.

### RaspyJack

- Upstream: <https://github.com/7h30th3r0n3/Raspyjack>
- Developer: `7h30th3r0n3`

Why it matters here:

- the `RaspyJack` launcher page only manages the handoff into a separately installed RaspyJack stack
- the display patch bundle in this repository is a narrow adaptation for the 1.3in panel plus launcher-return behavior, not a replacement for RaspyJack itself

Special thanks are due here because the launcher integration only exists at all because RaspyJack existed first.

### AngryOxide

- Upstream: <https://github.com/Ragnt/AngryOxide>
- Developer: `Ragnt`

Why it matters here:

- the `AngryOxide` page in the launcher is a management surface around an external project
- launcher-side status parsing, target selection, and UI integration are our glue, but the actual tool and its behavior belong to the upstream project

If you are using AngryOxide through the launcher, you should still read the upstream guide and understand what the tool itself expects.

### General Rule

If a page in this launcher wraps another project:

- install and validate that upstream project separately first
- read its own documentation
- then wire it into K.A.R.I Launcher

The launcher is the conductor. It is not the orchestra.

## Boot Screen

What it is:

This build now has a two-stage visual startup path so the panel does not spend boot looking dead.

What happens:

- `kari-bootscreen.service` starts early and paints `booting.png`
- that image fades in and stays on the display while the rest of the box is still waking up
- when `kari-dashboard.service` finally starts, the launcher crossfades from `booting.png` into `KARI.png`

Files involved:

- [booting.png](/home/kari/Projects/kari-launcher/booting.png)
- [bootscreen.py](/home/kari/Projects/kari-launcher/src/launcher/bootscreen.py)
- [bootscreen](/home/kari/Projects/kari-launcher/bin/bootscreen)
- [kari-bootscreen.service](/home/kari/Projects/kari-launcher/systemd/kari-bootscreen.service)

Tutorial:

1. Place your preferred early boot image at `booting.png`.
2. Install the dashboard service with `sudo ./install_dashboard_service.sh`.
3. Reboot the Pi.
4. Confirm the panel shows the early boot image before the main launcher is fully ready.
5. Confirm the handoff into the normal launcher splash looks clean on your hardware.

Warnings:

- this is a visual handoff, not a second full launcher instance
- if the early boot screen ever feels slower than it should, profile the bootscreen path before adding more moving parts
- the panel retains the last written frame, which is useful here and dangerous if you forget how much work the display is doing for you

## RaspyJack Patch Bundle

The small RaspyJack patch bundle in:

- [third_party/raspyjack_patch](/home/kari/Projects/kari-launcher/third_party/raspyjack_patch)

contains only the changes we could defend publicly as part of the 1.3in/ST7789 adaptation plus the optional `Return to Launcher` hook.

It does not claim to be the whole of RaspyJack, and it should not be read that way.

Use it when:

- you need the 1.3in display port
- you need the launcher return hook
- you want a narrower public patch set instead of a giant hand-wavy fork

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

Runtime config usually lives at:

```text
/etc/default/kari-watchdog
```

Warnings:

- a bad watchdog config can turn small failures into reboot theatre
- do not enable aggressive reboot behavior until you trust your checks

## Troubleshooting

### The physical buttons do nothing

Check:

- `"local_buttons_enabled": true`
- the `input.pins` mapping
- your wiring matches the Waveshare 1.3in board, not an older layout

### The web UI loads but actions do nothing

Check:

- `remote.enabled`
- `remote.port`
- `remote.token` if required
- launcher logs:

```bash
sudo journalctl -u kari-dashboard.service -f
```

### Nodes always show offline

Check:

- hostnames or IPs
- ports
- health endpoint response with `curl`
- SMB settings if you enabled them

### The launcher restarts or crashes repeatedly

Check:

- `dashboard.json` is valid JSON
- custom command strings are quoted correctly
- wrapper scripts exist and are executable

### Kismet shows Bluetooth as off

Check:

- `hci0` really exists before or during Kismet start
- `/etc/kismet/kismet_site.conf`
- `systemctl cat kismet.service`
- the launcher Kismet page source lines

Current expected behavior on this build:

- `wlan2` is auto-preferred when present
- `hci0` is auto-added when present
- `wlan0` is left alone

### RaspyJack WallOfFlippers says bluepy failed

Check:

- `python3 -m pip show bluepy`
- whether `bluepy-helper` exists and has the capabilities it needs
- `systemctl is-active bluetooth`
- whether RaspyJack falls back to `bluetoothctl`

Current expected behavior on this build:

- `WallOfFlippers` will try `bluepy` first
- if `bluepy` runtime management commands keep failing, the payload should fall through to its `bluetoothctl` backend instead of looping forever
- `bluetoothctl` fallback is good enough for basic nearby Bluetooth presence, but it is weaker than a healthy `bluepy` or `bleak` path

### A page feels sluggish

Common causes:

- heavy external commands
- over-frequent scans
- deep SMB stats
- over-aggressive remote polling

## Operational Warnings

- This launcher can manage services, radios, and external workflows. Review every command path before enabling unattended use.
- Keep secrets and credentials out of the repository.
- Do not assume example paths, service names, or interfaces match your environment.
- Validate hardware pin mappings before field deployment.
- If a page hands the display off to another stack, make sure you have a reliable way back.

## Service Removal

```bash
cd ~/Projects/kari-launcher
sudo ./uninstall_watchdog_service.sh
sudo ./uninstall_dashboard_service.sh
```
