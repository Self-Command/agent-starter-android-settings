"""Realtime Deepgram Chinese + English streams; bounded timestamp merge, no LLM calls."""
import asyncio
import dataclasses
import logging
import os
import re
import time

from livekit.agents import stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN
from livekit.plugins import deepgram

logger = logging.getLogger("voice.latency")
CJK = re.compile(r"[\u3400-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")


def join_words(words):
    text = " ".join(word["text"].strip() for word in words if word["text"].strip())
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"\s+([，。！？、；：,.!?;:])", r"\1", text)
    return text.strip()


def merge_words(chinese, english):
    """Keep Chinese intact; only replace Latin spans with confident, aligned English."""
    output = []
    index = 0
    while index < len(chinese):
        word = chinese[index]
        if not LATIN.search(word["text"]) or CJK.search(word["text"]):
            output.append(word)
            index += 1
            continue
        last = index + 1
        while last < len(chinese) and LATIN.search(chinese[last]["text"]) and not CJK.search(chinese[last]["text"]):
            last += 1
        span = chinese[index:last]
        start, end = span[0]["start"], span[-1]["end"]
        matches = [item for item in english if start - 0.06 <= (item["start"] + item["end"]) / 2 <= end + 0.06]
        duration = max(end - start, 0.01)
        coverage = sum(max(0, min(end, item["end"]) - max(start, item["start"])) for item in matches)
        confident = matches and sum(item["confidence"] for item in matches) / len(matches) >= 0.72
        if confident and coverage / duration >= 0.50:
            output.extend(matches)
        else:
            output.extend(span)
        index = last
    return join_words(output)


def word_data(data):
    return [
        {"text": str(word), "start": word.start_time, "end": word.end_time,
         "confidence": word.confidence if isinstance(word.confidence, (int, float)) else 0.0}
        for word in (data.words or [])
    ]


class BilingualDeepgramSTT(stt.STT):
    def __init__(self):
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True, offline_recognize=False))
        options = dict(model="nova-3", api_key=os.environ["DEEPGRAM_API_KEY"],
                       interim_results=True, no_delay=True, smart_format=False,
                       endpointing_ms=300, filler_words=False,
                       keyterm=["LiveKit", "Android", "API", "LLM", "STT", "TTS"])
        self.chinese = deepgram.STT(language="zh-CN", **options)
        self.english = deepgram.STT(language="en-US", **options)

    @property
    def model(self):
        return "nova-3"

    @property
    def provider(self):
        return "deepgram"

    async def _recognize_impl(self, buffer, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        raise NotImplementedError("Use realtime audio streaming")

    def stream(self, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return BilingualStream(stt=self, conn_options=conn_options)

    async def aclose(self):
        await asyncio.gather(self.chinese.aclose(), self.english.aclose())


class BilingualStream(stt.RecognizeStream):
    async def _run(self):
        chinese = self._stt.chinese.stream(conn_options=self._conn_options)
        english = self._stt.english.stream(conn_options=self._conn_options)
        english_final = []
        english_interim = []
        english_available = True
        latest_primary_end = 0.0
        latest_delivered_end = 0.0
        last_primary_text_wall = 0.0
        input_samples = 0
        first_interim = False

        async def feed():
            nonlocal input_samples, english_available
            async for item in self._input_ch:
                if isinstance(item, self._FlushSentinel):
                    chinese.flush()
                    if english_available:
                        english.flush()
                else:
                    input_samples += item.samples_per_channel / item.sample_rate
                    chinese.push_frame(item)
                    if english_available:
                        try:
                            english.push_frame(item)
                        except RuntimeError:
                            english_available = False
            chinese.end_input()
            if english_available:
                english.end_input()

        async def read_english():
            nonlocal english_interim, english_final, english_available, latest_delivered_end
            try:
                async for event in english:
                    if event.alternatives:
                        data = event.alternatives[0]
                        words = word_data(data)
                        confident = words and sum(w['confidence'] for w in words) / len(words) >= 0.80
                        if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                            english_final = [word for word in english_final if word["end"] >= latest_primary_end - 15] + words
                            english_interim = []
                            if confident:
                                # Chinese remains preferred for overlapping speech. A pure
                                # English utterance must not disappear when zh-CN is silent.
                                await asyncio.sleep(0.30)
                                cutoff = max(latest_primary_end, latest_delivered_end)
                                fresh = [word for word in words if word['end'] > cutoff + 0.08]
                                if fresh:
                                    latest_delivered_end = fresh[-1]['end']
                                    fallback = dataclasses.replace(data, text=join_words(fresh), words=None,
                                                                   start_time=fresh[0]['start'], end_time=fresh[-1]['end'])
                                    self._event_ch.send_nowait(dataclasses.replace(event, alternatives=[fallback]))
                        elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                            english_interim = words
                            if confident and time.monotonic() - last_primary_text_wall > 0.35:
                                self._event_ch.send_nowait(event)
            except Exception:
                english_available = False
                logger.warning("English secondary STT unavailable; Chinese realtime STT continues")

        async def read_chinese():
            nonlocal latest_primary_end, latest_delivered_end, last_primary_text_wall, first_interim
            async for event in chinese:
                if event.alternatives and event.alternatives[0].text:
                    last_primary_text_wall = time.monotonic()
                if event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT and not first_interim:
                    first_interim = True
                    logger.info("first_interim_transcript", extra={"audio_seconds_received": round(input_samples, 3)})
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    # Never hold the primary transcript for an HTTP request or an LLM.
                    # A secondary result has at most 120ms to catch up.
                    data = event.alternatives[0]
                    primary_words = word_data(data)
                    primary_end = primary_words[-1]['end'] if primary_words else data.end_time
                    latest_primary_end = max(latest_primary_end, primary_end)
                    if primary_end <= latest_delivered_end + 0.05:
                        continue  # A delayed duplicate of already delivered English speech.
                    started = time.monotonic()
                    await asyncio.sleep(0.12)
                    seen = set()
                    secondary = []
                    for word in english_final + english_interim:
                        key = (round(word["start"], 2), round(word["end"], 2))
                        if key not in seen:
                            seen.add(key)
                            secondary.append(word)
                    secondary.sort(key=lambda word: word["start"])
                    text = merge_words(primary_words, secondary) if primary_words else data.text
                    event = dataclasses.replace(event, alternatives=[dataclasses.replace(data, text=text, words=None)])
                    latest_delivered_end = max(latest_delivered_end, primary_end)
                    logger.info("final_transcript_ready", extra={"merge_delay_seconds": round(time.monotonic() - started, 3)})
                self._event_ch.send_nowait(event)

        tasks = [asyncio.create_task(feed()), asyncio.create_task(read_english()), asyncio.create_task(read_chinese())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(chinese.aclose(), english.aclose())
