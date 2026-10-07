"""Cloud-only real RTC smoke test using synthetic Chinese/English audio and a room-scoped JWT."""
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
    state = {"stage": "connect", "audio_samples": 0, "transcripts": [], "remote_participants": []}
    tasks = set()
    greeting = asyncio.Event()

    def spawn(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    async def read_audio(track):
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        try:
            async for event in stream:
                if max((abs(sample) for sample in event.frame.data), default=0) > 600:
                    state["audio_samples"] += event.frame.samples_per_channel
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
            if segment.final:
                state["transcripts"].append({"text": segment.text, "source": "transcription_event"})

    async def read_text(reader, identity):
        text = await reader.read_all()
        state["transcripts"].append({"text": text, "source": "text_stream"})

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
        # Let the greeting finish before the synthetic user speaks.
        await asyncio.sleep(10)
        before_audio = state["audio_samples"]
        before_transcripts = len(state["transcripts"])
        state["stage"] = "publish_bilingual_audio"
        with wave.open(str(SAMPLE), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
            pcm = wav.readframes(wav.getnframes())
        pcm = b"\0" * 24000 + pcm + b"\0" * 48000
        for offset in range(0, len(pcm), 960):
            chunk = pcm[offset:offset + 960]
            await source.capture_frame(rtc.AudioFrame(
                data=chunk, sample_rate=24000, num_channels=1, samples_per_channel=len(chunk) // 2
            ))
        await source.wait_for_playout()
        state["stage"] = "wait_for_transcription_and_spoken_reply"
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            new_texts = state["transcripts"][before_transcripts:]
            combined_transcript = " ".join(item["text"] for item in new_texts)
            bilingual_text = (
                "livekit" in combined_transcript.lower()
                and "api" in combined_transcript.lower()
                and "settings" in combined_transcript.lower()
                and any("\u4e00" <= character <= "\u9fff" for character in combined_transcript)
            )
            if state["audio_samples"] - before_audio >= 12000 and bilingual_text:
                state["stage"] = "passed"
                state["success"] = True
                print("Real RTC check passed: agent joined, synthetic bilingual speech transcribed, spoken reply received.")
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError("Did not receive both a bilingual transcript and a new spoken reply")
    except Exception as error:
        state["success"] = False
        state["error_type"] = type(error).__name__
        print("RTC smoke check failed at stage:", state["stage"], "error:", type(error).__name__)
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
