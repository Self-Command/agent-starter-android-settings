"""Cloud-only latency check using synthetic bilingual speech and a room-scoped JWT."""
import asyncio
import json
import logging
import os
import time
import wave
from pathlib import Path

from livekit import rtc

logging.basicConfig(level=logging.ERROR)
REPORT = Path("cloud-agent-report.json")
SAMPLE = Path(".github/test-audio/chinese-english.wav")


async def main():
    token = os.environ["LIVEKIT_SMOKE_TOKEN"]
    print("::add-mask::" + token)
    room = rtc.Room()
    started = time.monotonic()
    state = {"stage": "connect", "audio_samples": 0, "transcripts": [], "remote_participants": [],
             "stt_passed": False, "spoken_reply_passed": False}
    tasks = set()
    greeting = asyncio.Event()
    last_active_audio = 0.0

    def now():
        return round(time.monotonic() - started, 4)

    def spawn(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    async def read_audio(track):
        nonlocal last_active_audio
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        try:
            async for event in stream:
                if max((abs(sample) for sample in event.frame.data), default=0) > 600:
                    state["audio_samples"] += event.frame.samples_per_channel
                    last_active_audio = time.monotonic()
                    if "speech_start_seconds" in state:
                        state.setdefault("first_reply_audio_seconds", now())
                    if state["audio_samples"] >= 4800:
                        greeting.set()
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            spawn(read_audio(track))

    @room.on("transcription_received")
    def on_transcription(segments, participant, publication):
        for segment in segments:
            if segment.text.strip():
                state["transcripts"].append({"text": segment.text, "source": "transcription_event",
                    "identity": participant.identity if participant else None,
                    "final": segment.final, "received_seconds": now()})

    async def read_text(reader, identity):
        chunks = []
        first_received = None
        async for chunk in reader:
            first_received = first_received if first_received is not None else now()
            chunks.append(chunk)
        state["transcripts"].append({"text": "".join(chunks), "source": "text_stream", "identity": identity,
            "final": True, "received_seconds": now(), "first_chunk_seconds": first_received})

    def on_text(reader, identity):
        spawn(read_text(reader, identity))

    room.register_text_stream_handler("lk.transcription", on_text)
    source = None
    try:
        await room.connect(os.environ["LIVEKIT_SMOKE_URL"], token)
        source = rtc.AudioSource(24000, 1)
        track = rtc.LocalAudioTrack.create_audio_track("synthetic-microphone", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        state["stage"] = "wait_for_agent_greeting"
        await asyncio.wait_for(greeting.wait(), timeout=80)
        state["remote_participants"] = [p.identity for p in room.remote_participants.values()]
        assert state["remote_participants"], "No remote agent joined the test room"
        # Require the greeting to stop: its remaining audio must not count as a reply.
        greeting_deadline = time.monotonic() + 40
        while time.monotonic() - last_active_audio < 1.0:
            assert time.monotonic() < greeting_deadline, "Agent greeting never finished"
            await asyncio.sleep(0.1)
        before_audio = state["audio_samples"]
        before_transcripts = len(state["transcripts"])
        state["stage"] = "publish_bilingual_audio"
        with wave.open(str(SAMPLE), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
            pcm = wav.readframes(wav.getnframes())
        state["speech_start_seconds"] = now() + 0.5
        state["speech_duration_seconds"] = len(pcm) / 48000
        pcm = b"\0" * 24000 + pcm + b"\0" * 72000
        for offset in range(0, len(pcm), 960):
            chunk = pcm[offset:offset + 960]
            await source.capture_frame(rtc.AudioFrame(
                data=chunk, sample_rate=24000, num_channels=1, samples_per_channel=len(chunk) // 2
            ))
        await source.wait_for_playout()
        state["speech_end_seconds"] = now() - 1.5
        state["stage"] = "wait_for_transcription_and_spoken_reply"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            new_texts = state["transcripts"][before_transcripts:]
            user_texts = [item for item in new_texts if item.get("identity") == room.local_participant.identity]
            finals = [item for item in user_texts if item["final"]]
            combined = " ".join(item["text"] for item in finals)
            bilingual_text = ("livekit" in combined.lower() and "api" in combined.lower()
                and "settings" in combined.lower() and any("\u4e00" <= c <= "\u9fff" for c in combined))
            if bilingual_text:
                state["final_transcript_delay_seconds"] = round(max(item["received_seconds"] for item in finals) - state["speech_end_seconds"], 3)
                first_text = min(item.get("first_chunk_seconds") or item["received_seconds"] for item in user_texts)
                state["first_transcript_after_speech_start_seconds"] = round(first_text - state["speech_start_seconds"], 3)
                state["transcript_received_while_speaking"] = first_text < state["speech_end_seconds"]
                state["stt_passed"] = state["final_transcript_delay_seconds"] <= 3.0
            agent_reply = any(item["source"] == "transcription_event" and item["final"]
                and item.get("identity") in state["remote_participants"] for item in new_texts)
            state["spoken_reply_passed"] = bool(agent_reply and state["audio_samples"] - before_audio >= 12000)
            if "first_reply_audio_seconds" in state:
                state["reply_audio_delay_seconds"] = round(state["first_reply_audio_seconds"] - state["speech_end_seconds"], 3)
            if state["stt_passed"] and state["spoken_reply_passed"]:
                state["stage"] = "passed"
                state["success"] = True
                print("Real RTC check passed: bilingual streaming STT, final-transcript latency <=3s, completed spoken reply.")
                print("Measured final transcript delay (seconds):", state["final_transcript_delay_seconds"])
                print("Measured reply audio delay (seconds):", state.get("reply_audio_delay_seconds"))
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("Streaming STT or completed spoken reply did not meet the checks")
    except Exception as error:
        state["success"] = False
        state["error_type"] = type(error).__name__
        print("RTC check failed:", state["stage"], "STT passed:", state["stt_passed"], "spoken reply passed:", state["spoken_reply_passed"])
        raise
    finally:
        await room.disconnect()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)
        if source is not None:
            await source.aclose()
        REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
