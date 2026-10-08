import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import azure.cognitiveservices.speech as speechsdk
from livekit.agents import APIConnectionError
from azure_streaming_tts import AzureStreamingTTS


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)


class Request:
    def __init__(self, **kwargs):
        self.done = threading.Event()
        self.tokens = []
        self.input_stream = self
        self.synth = None

    def write(self, text):
        self.tokens.append(text)
        event = SimpleNamespace(result=SimpleNamespace(audio_data=b"\x01\x00" * 480))
        for callback in list(self.synth.synthesizing.callbacks):
            callback(event)

    def close(self):
        self.done.set()


class Synthesizer:
    def __init__(self, canceled=False):
        self.synthesizing = Signal()
        self.requests = []
        self.stop_count = 0
        self.canceled = canceled

    def speak_async(self, request):
        request.synth = self
        self.requests.append(request)

        def get():
            if not request.done.wait(3):
                raise TimeoutError("Fake request did not close")
            return SimpleNamespace(
                reason=(speechsdk.ResultReason.Canceled if self.canceled
                        else speechsdk.ResultReason.SynthesizingAudioCompleted),
                cancellation_details=SimpleNamespace(error_code="AuthenticationFailure",
                                                     error_details="fictional-private-key"),
            )
        return SimpleNamespace(get=get)

    def stop_speaking_async(self):
        self.stop_count += 1
        self.requests[-1].done.set()
        return SimpleNamespace(get=lambda: None)


class Output:
    def __init__(self):
        self.chunks = []
        self.first_audio = asyncio.Event()

    def push(self, chunk):
        self.chunks.append(chunk)
        self.first_audio.set()


class AzureStreamingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = SimpleNamespace(open=lambda *_: None, close=lambda: None)
        self.connection_patch = patch("azure_streaming_tts.speechsdk.Connection.from_speech_synthesizer",
                                      return_value=self.connection)
        self.request_patch = patch("azure_streaming_tts.speechsdk.SpeechSynthesisRequest", Request)
        self.connection_patch.start()
        self.request_patch.start()

    async def asyncTearDown(self):
        self.request_patch.stop()
        self.connection_patch.stop()

    def provider(self, synth):
        return AzureStreamingTTS(speech_region="koreacentral", _synthesizer=synth)

    async def test_audio_arrives_before_llm_finishes_and_synth_is_reused(self):
        synth = Synthesizer()
        provider = self.provider(synth)
        output = Output()
        provider.prewarm()

        async def text():
            yield "你好，"
            await asyncio.wait_for(output.first_audio.wait(), 1)
            self.assertFalse(synth.requests[0].done.is_set())
            self.assertEqual(synth.requests[0].tokens, ["你好，"])
            yield "hello。"

        await provider._render(text(), output)
        self.assertEqual(len(output.chunks), 2)
        second_output = Output()

        async def again():
            yield "第二次。"

        await provider._render(again(), second_output)
        self.assertEqual(len(synth.requests), 2)
        self.assertEqual(len(second_output.chunks), 1)
        self.assertEqual(synth.stop_count, 0)
        self.assertFalse(synth.synthesizing.callbacks)
        await provider.aclose()

    async def test_interruption_stops_sdk_and_discards_late_audio(self):
        synth = Synthesizer()
        provider = self.provider(synth)
        output = Output()

        async def text():
            yield "被打断的回答。"
            await asyncio.Event().wait()

        task = asyncio.create_task(provider._render(text(), output))
        await asyncio.wait_for(output.first_audio.wait(), 1)
        old_callback = synth.synthesizing.callbacks[0]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(synth.stop_count, 1)
        self.assertFalse(synth.synthesizing.callbacks)
        old_callback(SimpleNamespace(result=SimpleNamespace(audio_data=b"late-old-audio")))
        await asyncio.sleep(0)
        self.assertEqual(len(output.chunks), 1)
        await provider.aclose()

    async def test_error_is_sanitized_and_connection_is_stopped(self):
        synth = Synthesizer(canceled=True)
        provider = self.provider(synth)

        async def text():
            yield "测试。"

        with self.assertRaises(APIConnectionError) as error:
            await provider._render(text(), Output())
        self.assertNotIn("fictional-private-key", str(error.exception))
        self.assertIn("AuthenticationFailure", str(error.exception))
        self.assertEqual(synth.stop_count, 1)
        await provider.aclose()

    async def test_empty_input_does_not_call_provider(self):
        synth = Synthesizer()
        provider = self.provider(synth)

        async def text():
            if False:
                yield ""

        await provider._render(text(), Output())
        self.assertFalse(synth.requests)
        await provider.aclose()
