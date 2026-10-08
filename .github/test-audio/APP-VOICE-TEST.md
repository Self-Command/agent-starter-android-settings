# Actual Android app voice diagnosis

Run the manually dispatched `Actual Android app voice diagnosis` workflow. All
builds and execution take place in GitHub Actions, with an API 35 Android emulator.
The normal PR/release tests skip this test unless `voiceE2E=true` is supplied.

The test fills the actual settings screen, saves a direct connection, starts a
call, opens chat, and feeds the fictional bilingual WAV into the Android emulator
microphone through Google's emulator gRPC controller. The app continues to use
its own Android AudioRecord, LiveKit Android SDK and Compose session. It measures
microphone publication, captured PCM, received messages, visible UI text, received
reply PCM, and emulator speaker output. A second turn exercises microphone mute
and re-enable. It ends the call through the UI.

Optional observers exist only in the debug variant; the release variant cannot
collect voice diagnostics. No test microphone or substitute RTC client is inserted
into the app. Secrets are supplied from `ANDROID_VOICE_TEST_CONFIG` to the installed
app's private `no_backup` directory, are never compiled into an APK, and are deleted
after the test. Recordings start after leaving the settings page.

The secret JSON has `url`, a short-lived `token` for an isolated test room, `llm`
(`base_url`, `api_key`, `model`) and `tts` (`base_url`, `api_key`, `model`, `voice`).
The provider fields are for instrumentation HTTP controls only; the app's settings
and production connection behavior have no provider fields.

After the real app call, separate Android instrumentation controls compare LLM
simple/voice-context requests and TTS WAV/streaming PCM16/short text. Two concurrent
short TTS requests check for obvious rate-limit responses; they do not establish
the provider's sustained quota. These direct HTTP controls are clearly distinguished
from the RTC call and originate in the emulator, not in a LiveKit Cloud container.

Artifacts include an Android monotonic-clock event timeline, a host/emulator clock
correlation, screenshots, the operation video, synthetic reply audio and provider
timings. Android first-audio arrival and emulator speaker output are measured
separately. Emulator results do not reproduce a particular phone's microphone,
Bluetooth behavior, cellular network, or geographic route.
