import base64
import io
import json
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

from bilingual_stt import merge_words, word_data
from livekit.agents import AgentSession, TurnHandlingOptions, stt
from livekit.agents import APIConnectionError
from livekit.agents.types import TimedString
from mimo_tts import decode_audio, decode_pcm_event, request_payload, sse_events, MiMoChunkedStream


def audio_response(sample_rate=24000, channels=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x01\x00" * 2400 * channels)
    return {"choices": [{"message": {"audio": {"data": base64.b64encode(stream.getvalue()).decode()}}}]}


class ProviderProtocolTest(unittest.TestCase):
    def test_mimo_requires_assistant_role_and_keeps_chinese_voice(self):
        body = request_payload("你好 hello", "白桦", "mimo-v2.5-tts")
        self.assertEqual(body["messages"], [{"role": "assistant", "content": "你好 hello"}])
        self.assertEqual(body["audio"], {"format": "pcm16", "voice": "白桦"})
        self.assertTrue(body["stream"])
        self.assertNotIn("api_key", body)

    def test_wav_header_is_removed_before_pcm_emission(self):
        self.assertEqual(decode_audio(audio_response()), b"\x01\x00" * 2400)

    def test_stream_pcm_is_validated_and_control_events_are_ignored(self):
        event = {"choices": [{"delta": {"audio": {"data": base64.b64encode(b"\x01\x00").decode()}}}]}
        self.assertEqual(decode_pcm_event(event), b"\x01\x00")
        self.assertEqual(decode_pcm_event({"choices": [{"delta": {"role": "assistant"}}]}), b"")
        for payload in ("invalid!", base64.b64encode(b"\x01").decode()):
            with self.assertRaises(ValueError):
                decode_pcm_event({"choices": [{"delta": {"audio": {"data": payload}}}]})
        with self.assertRaises(ValueError):
            decode_pcm_event({"error": {"message": "fictional error"}})

    def test_mismatched_rate_or_channels_are_rejected(self):
        for kwargs in ({"sample_rate": 16000}, {"channels": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                decode_audio(audio_response(**kwargs))

    def test_english_words_are_repaired_without_translating_chinese(self):
        zh = [self.word("你好", 0, 0.5), self.word("pleasecheck", 0.5, 1.2), self.word("settings", 1.2, 1.8)]
        en = [self.word("you", 0, 0.5), self.word("please", 0.5, 0.8), self.word("check", 0.8, 1.2), self.word("settings", 1.2, 1.8)]
        self.assertEqual(merge_words(zh, en), "你好 please check settings")

    def test_unrelated_english_is_not_inserted(self):
        zh = [self.word("你好", 0, 0.5), self.word("LiveKit", 0.5, 1.2)]
        en = [self.word("ignore", 3, 4)]
        self.assertEqual(merge_words(zh, en), "你好 LiveKit")

    def test_low_confidence_english_cannot_replace_primary(self):
        zh = [self.word("LiveKit", 0, 1)]
        en = [self.word("like", 0, 0.5, 0.3), self.word("it", 0.5, 1, 0.3)]
        self.assertEqual(merge_words(zh, en), "LiveKit")

    def test_primary_stream_does_not_wait_for_missing_secondary(self):
        zh = [self.word("我", 0, 0.2), self.word("正在", 0.2, 0.4), self.word("说话。", 0.4, 1)]
        self.assertEqual(merge_words(zh, []), "我正在说话。")

    def test_sdk_string_words_are_decoded(self):
        data = stt.SpeechData(language="zh-CN", text="你好", words=[TimedString("你好", 0, 0.5, 0.9)])
        self.assertEqual(word_data(data), [self.word("你好", 0, 0.5, 0.9)])

    def test_session_accepts_turn_options(self):
        session = AgentSession(turn_handling=TurnHandlingOptions(
            turn_detection="vad", interruption={"mode": "vad"},
            endpointing={"mode": "fixed", "min_delay": 0.45, "max_delay": 1.5},
            preemptive_generation={"enabled": False},
        ))
        self.assertIsNotNone(session)

    @staticmethod
    def word(text, start, end, confidence=0.95):
        return {"text": text, "start": start, "end": end, "confidence": confidence}

class StreamingDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_is_emitted_before_response_finishes(self):
        emitted = []
        encoded = base64.b64encode(b"\x01\x00" * 240).decode()
        event = json.dumps({"choices": [{"delta": {"audio": {"data": encoded}}}]})

        async def content():
            yield ("data: " + event + "\n").encode()
            yield b"\n"
            self.assertEqual(len(emitted), 1, "First chunk must already be sent while HTTP is open")
            yield ("data: " + event + "\n").encode()
            yield b"\n"
            yield b"data: [DONE]\n"
            yield b"\n"

        response = SimpleNamespace(status=200, content=content())
        class Context:
            async def __aenter__(self): return response
            async def __aexit__(self, *args): pass
        provider = SimpleNamespace(_base_url="https://fictional.invalid", _api_key="fictional", _voice="白桦", _model="mimo-v2.5-tts")
        stream = SimpleNamespace(_tts=provider, _input_text="你好")
        emitter = SimpleNamespace(initialize=lambda **kwargs: None, push=emitted.append)
        client = SimpleNamespace(post=lambda *args, **kwargs: Context())
        with patch("mimo_tts.utils.http_context.http_session", return_value=client):
            await MiMoChunkedStream._run(stream, emitter)
        self.assertEqual(len(emitted), 2)

    async def test_sse_control_comments_crlf_and_done(self):
        async def lines():
            for line in (b":keepalive\r\n", b"data: {\r\n", b'data: "choices": []}\r\n', b"\r\n", b"data: [DONE]\r\n", b"\r\n"):
                yield line
        self.assertEqual([event async for event in sse_events(lines())], [{"choices": []}, {"done": True}])


if __name__ == "__main__":
    unittest.main()
