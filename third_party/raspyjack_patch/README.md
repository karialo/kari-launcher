# RaspyJack 1.3in Patch Bundle

This directory contains the RaspyJack files we can justify as part of the Waveshare 1.3in ST7789 port.

The bundle was built by comparing:

- a fresh RaspyJack clone on Freyja at `~/Projects/Raspyjack`
- a modified RaspyJack snapshot from `Raspyjack.zip`

Only files with clear display-port changes were included.

## Included Files

- `files/LCD_1in44.py`
- `files/LCD_ST7789.py`
- `files/raspyjack.py`

## Why These Files

### `LCD_ST7789.py`

This is the added driver module for the Waveshare 1.3in ST7789 screen.

### `LCD_1in44.py`

This file was modified to act as a compatibility bridge:

- payloads and older code still import `LCD_1in44`
- when `RJ_LCD=st7789`, the legacy API is transparently routed to `LCD_ST7789`
- the legacy logical canvas stays at `128x128` so older payload layouts still render

This is the key reason old payloads continue to work without each one being rewritten.

### `raspyjack.py`

This is where the main display-port work happened:

- backend selection via `RJ_LCD`
- support for `LCD_ST7789`
- logical canvas sizing for ST7789
- display init changes
- splash handling changes
- path cleanup from hard-coded `/root/Raspyjack`

## How Legacy Payloads Keep Working

This was the important trick.

Originally, a lot of RaspyJack code and payloads assumed:

- `LCD_1in44` is the display module
- the logical UI canvas is `128x128`
- the main app and payloads are all speaking to the same 1.44in/ST7735-style API

For the 1.3in port, the fix was not “rewrite every payload”.

Instead:

1. `raspyjack.py` was taught to pick a backend using `RJ_LCD`.
2. A new `LCD_ST7789.py` driver was added for the 240x240 panel.
3. `LCD_1in44.py` was modified so that, when `RJ_LCD=st7789`, it silently forwards its old API calls into `LCD_ST7789`.
4. The compatibility layer keeps the legacy logical size at `128x128`, so payloads that were designed around the old square mini-canvas still render in roughly the same coordinate space.

That means most payloads can keep doing this:

```python
import LCD_1in44
lcd = LCD_1in44.LCD()
lcd.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
```

and they will still function, because `LCD_1in44` is now acting as a bridge when the ST7789 backend is selected.

## If You Want To Port Additional Payloads Yourself

If you do not want to ship patched payload files, the practical replication path is:

1. Apply the three base files in this bundle:
   - `LCD_1in44.py`
   - `LCD_ST7789.py`
   - `raspyjack.py`
2. Run RaspyJack with:

```bash
RJ_LCD=st7789 RJ_ROTATE=0 sudo -E python3 raspyjack.py
```

3. Test payloads one by one.
4. Only patch a payload if it still has a real layout problem.

Typical payload fixes, if needed:

- reduce text density
- reflow menu rows
- widen truncation logic
- stop assuming the bottom prompt line only has room for tiny labels
- avoid hard-coded clipping tied to the 1.44in border

What usually does **not** need to change once the compatibility bridge exists:

- the payload’s import path if it already uses `LCD_1in44`
- the payload’s basic `LCD_Init()` flow
- the payload’s button constants, if it was already using the same Waveshare HAT inputs

In other words:

- first try the compatibility bridge alone
- only patch payload modules that still look wrong after that

## Files We Explicitly Did Not Include

These files differed, but the diffs were not clearly part of the 1.3in display port:

- `payloads/reconnaissance/autoNmapScan.py`
- `payloads/reconnaissance/device_scout.py`
- `wifi/wifi_lcd_interface.py`
- `web/index.html`
- `web/app.js`
- `install_raspyjack.sh`
- `README.md`
- `gui_conf.json`

Reasons they were excluded:

- some diffs were UI cleanup or workflow drift rather than display-port work
- some were content/config changes, not code required for the ST7789 adaptation
- some touched unrelated features and would be hard to defend as “1.3in patch files” in a public repo

## How To Patch An Existing RaspyJack Install

1. Back up your current RaspyJack tree.
2. Copy these files into the root of your RaspyJack install:
   - `LCD_1in44.py`
   - `LCD_ST7789.py`
   - `raspyjack.py`
3. Keep your existing payloads unless you have separately audited and chosen to replace them.
4. Run RaspyJack with:

```bash
RJ_LCD=st7789 RJ_ROTATE=0 sudo -E python3 raspyjack.py
```

5. If the screen orientation is wrong, adjust `RJ_ROTATE`.

## Important Notes

- This bundle is not a full RaspyJack fork.
- It is not an installer.
- It is not a promise that every payload is now “240x240 aware”.
- It is the smallest set of files we can currently defend as the actual 1.3in/ST7789 port.

If you later want a larger patch set, generate it from a fresh upstream diff and document the reason for each extra file.
