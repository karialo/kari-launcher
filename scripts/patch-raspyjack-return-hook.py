#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


RETURN_FUNC = r'''

def ReturnToLauncher():
    cmd = os.environ.get("RJ_RETURN_TO_LAUNCHER_CMD", "").strip()
    if not cmd:
        print("RJ_RETURN_TO_LAUNCHER_CMD is not configured.")
        try:
            Dialog_info("Set RJ_RETURN_TO_LAUNCHER_CMD", wait=True, timeout=2)
        except Exception:
            pass
        return

    print("Returning to launcher...")
    try:
        Dialog("Returning...", False)
    except Exception:
        pass
    try:
        _stop_evt.set()
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
    subprocess.Popen(
        ["/usr/bin/env", "bash", "-lc", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.4)
'''


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if old not in text:
        print(f"warn: marker not found for {label}", file=sys.stderr)
        return text, False
    return text.replace(old, new, 1), True


def add_return_function(text: str) -> tuple[str, bool]:
    if "def ReturnToLauncher(" in text:
        return text, False

    markers = [
        "\ndef safe_kill(",
        "\n### Two threaded functions ###",
        "\ndef is_responder_running(",
    ]
    for marker in markers:
        if marker in text:
            return text.replace(marker, RETURN_FUNC + marker, 1), True

    match = re.search(r"\ndef Restart\(\):\n(?:    .+\n)+", text)
    if not match:
        raise RuntimeError("Could not find a safe insertion point for ReturnToLauncher()")
    insert_at = match.end()
    return text[:insert_at] + RETURN_FUNC + text[insert_at:], True


def add_menu_entries(text: str) -> tuple[str, bool]:
    changed = False

    if '[" Return to Launcher", ReturnToLauncher]' not in text:
        old = '            [" Lock",           OpenLockMenu],'
        new = '            [" Return to Launcher", ReturnToLauncher],\n' + old
        text, ok = replace_once(text, old, new, "main menu before Lock")
        changed = changed or ok
        if not ok:
            old = '            [" Payload", "ap"],            # p'
            new = old + '\n            [" Return to Launcher", ReturnToLauncher],'
            text, ok = replace_once(text, old, new, "main menu after Payload")
            changed = changed or ok

    system_item = '            [" Return to Launcher", ReturnToLauncher],\n            [" Shutdown system", [Leave, True]],'
    if system_item not in text:
        old = '            [" Shutdown system", [Leave, True]],'
        new = '            [" Return to Launcher", ReturnToLauncher],\n' + old
        text, ok = replace_once(text, old, new, "system menu")
        changed = changed or ok

    return text, changed


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch-raspyjack-return-hook.py /path/to/raspyjack.py", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    original = text

    text, func_changed = add_return_function(text)
    text, menu_changed = add_menu_entries(text)

    if text == original:
        print("RaspyJack return hook already present")
        return 0

    backup = path.with_suffix(path.suffix + ".kari-return-hook-backup")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print(
        "Patched RaspyJack return hook "
        f"(function={'yes' if func_changed else 'already'}, menu={'yes' if menu_changed else 'already'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
