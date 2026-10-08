"""Run actual Android app instrumentation and inject audio via emulator microphone.

Never import the LiveKit Python client or replace the app's RTC/audio implementation.
Credentials enter the installed app's no_backup directory only and are removed.
"""
from __future__ import annotations

import array
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import wave

import grpc

PACKAGE = "io.livekit.android.example.voiceassistant"
DEVICE_DIR = f"/sdcard/Android/data/{PACKAGE}/files/voice-e2e"
OUTPUT = Path("app/build/voice-e2e")
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, os.environ["VOICE_PROTO_DIR"])
pb = importlib.import_module("emulator_audio_pb2")
rpc = importlib.import_module("emulator_audio_pb2_grpc")


def adb(*args, **kwargs):
    return subprocess.run(["adb", *args], check=True, capture_output=True, timeout=90, **kwargs)


def exists(name):
    return subprocess.run(["adb", "shell", "test", "-f", f"{DEVICE_DIR}/{name}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def marker(name):
    adb("shell", "touch", f"{DEVICE_DIR}/{name}")


def android_clock():
    before = time.time()
    uptime = float(adb("shell", "cat", "/proc/uptime").stdout.split()[0])
    after = time.time()
    return {"epoch_seconds": (before + after) / 2, "android_uptime_ms": uptime * 1000,
            "uncertainty_ms": (after - before) * 500}


def main():
    config = json.loads(os.environ["ANDROID_VOICE_TEST_CONFIG"])
    secret_values = [config["token"], config["llm"]["api_key"], config["tts"]["api_key"]]
    for secret in secret_values:
        print(f"::add-mask::{secret}", flush=True)
    # A boot-time Pixel Launcher ANR must not cover or pause the app under test.
    adb("shell", "am", "force-stop", "com.google.android.apps.nexuslauncher")
    adb("shell", "pm", "disable-user", "--user", "0", "com.google.android.apps.nexuslauncher")
    adb("install", "-r", "app/build/outputs/apk/debug/app-debug.apk")
    adb("install", "-r", "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk")
    print("App and test APKs installed", flush=True)
    adb("shell", "run-as", PACKAGE, "mkdir", "-p", "no_backup")
    # File transfer avoids adb exec-out stdin/EOF deadlocks. This isolated emulator
    # contains only the test app; delete the temporary shell file immediately.
    remote_config = "/data/local/tmp/voice-test-config.private.json"
    with tempfile.NamedTemporaryFile(dir=os.environ["RUNNER_TEMP"], mode="w", delete=False) as private:
        private.write(json.dumps(config))
        private_path = Path(private.name)
    try:
        adb("push", str(private_path), remote_config)
        adb("shell", "chmod", "644", remote_config)
        adb("shell", "run-as", PACKAGE, "cp", remote_config, "no_backup/voice-test-config.json")
        adb("shell", "run-as", PACKAGE, "chmod", "600", "no_backup/voice-test-config.json")
    finally:
        private_path.unlink(missing_ok=True)
        adb("shell", "rm", "-f", remote_config)
    print("Runtime test configuration installed in private no_backup directory", flush=True)
    adb("shell", "pm", "grant", PACKAGE, "android.permission.RECORD_AUDIO")
    adb("shell", "settings", "put", "system", "volume_voice", "5")
    adb("shell", "settings", "put", "system", "volume_music", "10")
    adb("shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "5")
    adb("shell", "cmd", "media_session", "volume", "--stream", "3", "--set", "10")
    adb("shell", "rm", "-rf", DEVICE_DIR)
    channel = grpc.insecure_channel("127.0.0.1:8554")
    grpc.channel_ready_future(channel).result(timeout=30)
    stub = rpc.EmulatorControllerStub(channel)
    stub.setMicrophoneState(pb.MicrophoneState(realAudioEnabled=False), timeout=10)
    print("Emulator gRPC microphone ready", flush=True)
    audio_format = pb.AudioFormat(samplingRate=24000, channels=pb.AudioFormat.Mono,
                                format=pb.AudioFormat.AUD_FMT_S16, mode=pb.AudioFormat.MODE_REAL_TIME)
    host_events = []
    stop = threading.Event()
    stream = stub.streamAudio(audio_format, timeout=600)

    def speaker():
        last_event = 0.0
        try:
            with wave.open(str(OUTPUT / "emulator-speaker.wav"), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                for packet in stream:
                    if stop.is_set():
                        break
                    wav.writeframes(packet.audio)
                    samples = array.array("h", packet.audio)
                    peak = max((abs(value) for value in samples), default=0)
                    now = time.time()
                    if peak >= 350 and now - last_event >= 0.1:
                        last_event = now
                        host_events.append({"event": "emulator_speaker_pcm", "epoch_seconds": now,
                                            "emulator_timestamp_us": packet.timestamp, "peak": peak})
        except grpc.RpcError as error:
            if not stop.is_set():
                host_events.append({"event": "speaker_capture_error", "code": error.code().name})

    thread = threading.Thread(target=speaker, daemon=True)
    thread.start()
    # Capture only after leaving settings so editable tokens never enter the video.
    video = None
    instrumentation = subprocess.Popen([
        "adb", "shell", "am", "instrument", "-w", "-r", "-e", "voiceE2E", "true",
        "-e", "class", f"{PACKAGE}.VoicePathTest", f"{PACKAGE}.test/androidx.test.runner.AndroidJUnitRunner"
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("Actual app UI instrumentation launched", flush=True)
    frames = None
    with wave.open(".github/test-audio/chinese-english.wav", "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
        frames = wav.readframes(wav.getnframes())
    injected = set()
    clock_sync = android_clock()
    deadline = time.monotonic() + 540
    try:
        while instrumentation.poll() is None and time.monotonic() < deadline:
            if video is None and exists("video.ready"):
                video = subprocess.Popen(["adb", "shell", "screenrecord", "--time-limit", "180",
                                           "--bit-rate", "2000000", "/sdcard/voice-e2e.mp4"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for number in (1, 2):
                if number in injected or not exists(f"inject-{number}.ready"):
                    continue
                injected.add(number)
                print(f"Injecting bilingual speech for app turn {number}", flush=True)
                marker(f"inject-{number}.started")
                started = time.time()
                host_events.append({"event": f"turn_{number}_inject_start", "epoch_seconds": started})

                def packets():
                    # 20ms pacing keeps emulator's 300ms microphone buffer from overflowing.
                    chunk_size = 960
                    silence = bytes(24000 // 2 * 2)
                    payload = silence + frames + silence
                    begin = time.monotonic()
                    for offset in range(0, len(payload), chunk_size):
                        target = begin + offset / 48000
                        remaining = target - time.monotonic()
                        if remaining > 0:
                            time.sleep(remaining)
                        yield pb.AudioPacket(format=audio_format, timestamp=int(time.time() * 1e6),
                                             audio=payload[offset:offset + chunk_size])

                stub.injectAudio(packets(), timeout=30)
                host_events.append({"event": f"turn_{number}_inject_end", "epoch_seconds": time.time(),
                                    "speech_seconds": len(frames) / 48000})
                marker(f"inject-{number}.finished")
            time.sleep(0.15)
        if instrumentation.poll() is None:
            instrumentation.terminate()
            raise RuntimeError("App instrumentation exceeded 540 seconds")
        text = instrumentation.communicate(timeout=15)[0].decode(errors="replace")
        for secret in secret_values:
            text = text.replace(secret, "[REDACTED]")
        (OUTPUT / "instrumentation.txt").write_text(text)
        print(text, flush=True)
        if "OK (1 test)" not in text or "FAILURES!!!" in text or "INSTRUMENTATION_FAILED" in text:
            raise RuntimeError("Actual app voice test failed; inspect the timing report")
    finally:
        stop.set()
        stream.cancel()
        thread.join(timeout=5)
        channel.close()
        if video:
            # Ask screenrecord to finalize the MP4 container.
            subprocess.run(["adb", "shell", "pkill", "-INT", "screenrecord"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                video.wait(timeout=10)
            except subprocess.TimeoutExpired:
                video.terminate()
        subprocess.run(["adb", "pull", DEVICE_DIR, str(OUTPUT / "android")], check=False)
        subprocess.run(["adb", "pull", "/sdcard/voice-e2e.mp4", str(OUTPUT / "app-operations.mp4")], check=False)
        (OUTPUT / "emulator-timeline.json").write_text(json.dumps(
            {"clock_sync": clock_sync, "events": host_events}, indent=2))
        (OUTPUT / "android-audio-routing.txt").write_bytes(adb("shell", "dumpsys", "audio").stdout)
        adb("shell", "run-as", PACKAGE, "rm", "-f", "no_backup/voice-test-config.json")
        adb("shell", "am", "force-stop", PACKAGE)


if __name__ == "__main__":
    main()
