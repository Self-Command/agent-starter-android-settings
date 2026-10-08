import asyncio
import unittest
from types import SimpleNamespace

from livekit.agents import stt
from livekit.agents.types import TimedString
from bilingual_stt import BilingualStream


def speech(text, start, end, confidence=0.95, final=True):
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT if final else stt.SpeechEventType.INTERIM_TRANSCRIPT,
        alternatives=[stt.SpeechData(language='en-US', text=text, start_time=start, end_time=end,
                                     words=[TimedString(text, start, end, confidence)])],
    )


class FakeStream:
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for delay, event in self.events:
            await asyncio.sleep(delay)
            yield event

    def end_input(self): pass
    async def aclose(self): pass


class BilingualStreamRegression(unittest.IsolatedAsyncioTestCase):
    async def run_streams(self, chinese, english):
        async def empty_input():
            if False:
                yield

        emitted = []
        zh, en = FakeStream(chinese), FakeStream(english)
        stream = SimpleNamespace(
            _stt=SimpleNamespace(chinese=SimpleNamespace(stream=lambda **kwargs: zh),
                                 english=SimpleNamespace(stream=lambda **kwargs: en)),
            _conn_options=None, _input_ch=empty_input(), _FlushSentinel=type('Flush', (), {}),
            _event_ch=SimpleNamespace(send_nowait=emitted.append),
        )
        await BilingualStream._run(stream)
        return emitted

    async def test_pure_english_is_delivered_when_chinese_is_silent(self):
        events = await self.run_streams([], [(0, speech('voice connection', 0.1, 1.5, final=False)),
                                            (0.05, speech('voice connection', 0.1, 1.5))])
        self.assertEqual([e.alternatives[0].text for e in events], ['voice connection', 'voice connection'])
        self.assertEqual(events[-1].type, stt.SpeechEventType.FINAL_TRANSCRIPT)

    async def test_chinese_prevents_overlapping_english_hallucination_and_duplicate(self):
        events = await self.run_streams([(0.06, speech('语音连接正常', 0.1, 1.5))],
                                       [(0, speech('you in lian jie', 0.1, 1.5))])
        self.assertEqual([e.alternatives[0].text for e in events], ['语音连接正常'])

    async def test_low_confidence_english_is_not_promoted(self):
        events = await self.run_streams([], [(0, speech('unrelated guess', 0.1, 1.5, confidence=0.3))])
        self.assertEqual(events, [])

    async def test_new_english_segment_after_chinese_is_delivered(self):
        events = await self.run_streams([(0, speech('你好', 0.1, 0.5))],
                                       [(0.2, speech('voice connection', 0.6, 1.5))])
        self.assertEqual([e.alternatives[0].text for e in events], ['你好', 'voice connection'])
