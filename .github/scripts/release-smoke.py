#!/usr/bin/env python3
"""Cloud-only smoke check of the signed APK, including a real process restart."""
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PACKAGE = "io.livekit.android.example.voiceassistant"
OUT = Path("app/build/cloud-smoke")
OUT.mkdir(parents=True, exist_ok=True)


def adb(*args):
    return subprocess.check_output(["adb", *args], text=True)


def nodes():
    adb("shell", "uiautomator", "dump", "/sdcard/livekit-window.xml")
    xml = adb("shell", "cat", "/sdcard/livekit-window.xml")
    (OUT / "screen.xml").write_text(xml, encoding="utf-8")
    return list(ET.fromstring(xml).iter("node"))


def find(text=None, description=None, widget=None):
    for node in nodes():
        if text is not None and node.get("text") != text:
            continue
        if description is not None and node.get("content-desc") != description:
            continue
        if widget is not None and node.get("class") != widget:
            continue
        return node
    raise AssertionError(f"UI element not found: {text or description or widget}")


def tap(node):
    left, top, right, bottom = map(int, re.findall(r"\d+", node.get("bounds")))
    adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))


def wait_for(text):
    for _ in range(12):
        try:
            return find(text=text)
        except (AssertionError, ET.ParseError):
            time.sleep(1)
    raise AssertionError(f"UI did not display {text}")


def start():
    adb("shell", "am", "start", "-W", "-n", f"{PACKAGE}/.MainActivity")
    tap(wait_for("LiveKit 设置"))
    wait_for("Token 接口地址")


start()
# Replace the endpoint using Android input. No real token or LiveKit account is used.
field = find(widget="android.widget.EditText")
tap(field)
adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
for _ in range(len("https://livekit.com/api/homepage-agent/token")):
    adb("shell", "input", "keyevent", "KEYCODE_DEL")
adb("shell", "input", "text", "https://smoke.example.com/token")
adb("shell", "input", "keyevent", "KEYCODE_BACK")
tap(find(text="保存"))
wait_for("START CALL")
adb("shell", "am", "force-stop", PACKAGE)
start()
field = find(widget="android.widget.EditText")
assert "https://smoke.example.com/token" in field.get("text", ""), "Configuration did not survive process restart"
adb("shell", "screencap", "-p", "/sdcard/livekit-settings.png")
adb("pull", "/sdcard/livekit-settings.png", str(OUT / "settings.png"))
logs = adb("logcat", "-d", "-s", "AndroidRuntime:E")
(OUT / "android-runtime.log").write_text(logs, encoding="utf-8")
assert "FATAL EXCEPTION" not in logs, "APK crashed during smoke testing"
print("Signed APK installed, settings opened, configuration survived process restart.")
