"""Azure's official text-stream SDK bridged to LiveKit's streaming TTS API."""
import asyncio
import logging
import os

import azure.cognitiveservices.speech as speechsdk
from livekit.agents import APIConnectionError, APITimeoutError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS


class AzureStreamingTTS(tts.TTS):
    def __init__(self, *, speech_key=None, speech_region=None, voice=None, _synthesizer=None):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=True), sample_rate=24000,
                         num_channels=1)
        self.region = speech_region or os.environ["AZURE_SPEECH_REGION"]
        self.voice = voice or os.getenv("AZURE_TTS_VOICE", "zh-CN-XiaoxiaoMultilingualNeural")
        # Derive the protocol-specific endpoint; never reuse the portal/REST URL.
        self.endpoint = f"wss://{self.region}.tts.speech.microsoft.com/cognitiveservices/websocket/v2"
        if _synthesizer is None:
            config = speechsdk.SpeechConfig(endpoint=self.endpoint,
                                           subscription=speech_key or os.environ["AZURE_SPEECH_KEY"])
            config.speech_synthesis_voice_name = self.voice
            config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
            # The LLM may pause between text chunks; keep a bounded request timeout below.
            config.set_property(speechsdk.PropertyId.SpeechSynthesis_FrameTimeoutInterval, "30000")
            config.set_property(speechsdk.PropertyId.SpeechSynthesis_RtfTimeoutThreshold, "10")
            self._synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        else:
            self._synthesizer = _synthesizer
        self._connection = speechsdk.Connection.from_speech_synthesizer(self._synthesizer)
        self._lock = asyncio.Lock()
        self._warm_task = None
        self._closed = False

    @property
    def model(self):
        return self.voice

    @property
    def provider(self):
        return "Azure Speech text streaming"

    def prewarm(self):
        if self._closed or self._lock.locked() or (self._warm_task and not self._warm_task.done()):
            return

        async def connect():
            try:
                await asyncio.to_thread(self._connection.open, True)
            except Exception:
                # The next synthesis reports a sanitized error; no SDK request details.
                logging.getLogger("voice.latency").warning("azure_preconnect_failed")

        self._warm_task = asyncio.create_task(connect())

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return self._synthesize_with_stream(text, conn_options=conn_options)

    def stream(self, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return AzureSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self):
        self._closed = True
        if self._warm_task:
            await self._warm_task
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

    async def _render(self, text_stream, output, *, on_started=None):
        first_token = await anext(text_stream, None)
        if first_token is None:
            return
        async with self._lock:
            if self._closed:
                raise APIConnectionError("Azure TTS is closed")
            if self._warm_task:
                await self._warm_task
            loop = asyncio.get_running_loop()
            chunks = asyncio.Queue()
            accepting_audio = True
            finished = False
            request = speechsdk.SpeechSynthesisRequest(
                input_type=speechsdk.SpeechSynthesisRequestInputType.TextStream)

            def audio_callback(event):
                if accepting_audio and event.result.audio_data:
                    try:
                        loop.call_soon_threadsafe(chunks.put_nowait, bytes(event.result.audio_data))
                    except RuntimeError:
                        pass

            self._synthesizer.synthesizing.connect(audio_callback)
            tasks = []
            try:
                if on_started:
                    on_started()
                future = self._synthesizer.speak_async(request)

                async def feed():
                    try:
                        await asyncio.to_thread(request.input_stream.write, first_token)
                        async for token in text_stream:
                            await asyncio.to_thread(request.input_stream.write, token)
                    finally:
                        await asyncio.to_thread(request.input_stream.close)

                async def wait_result():
                    result = await asyncio.to_thread(future.get)
                    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                        code = getattr(getattr(result, "cancellation_details", None), "error_code", "unknown")
                        raise APIConnectionError(f"Azure streaming synthesis canceled ({code})")
                    chunks.put_nowait(None)

                async def emit():
                    while True:
                        chunk = await chunks.get()
                        if chunk is None:
                            return
                        output.push(chunk)

                tasks = [asyncio.create_task(coro()) for coro in (feed, wait_result, emit)]
                async with asyncio.timeout(40):
                    await asyncio.gather(*tasks)
                finished = True
            except TimeoutError:
                raise APITimeoutError("Azure text stream timed out") from None
            except asyncio.CancelledError:
                raise
            except APIConnectionError:
                raise
            except Exception:
                raise APIConnectionError("Azure text stream failed") from None
            finally:
                accepting_audio = False
                # Stop and drain before releasing the single synthesizer to the next
                # utterance. A canceled answer must never leak into the new reply.
                if not finished:
                    try:
                        await asyncio.wait_for(asyncio.to_thread(
                            lambda: self._synthesizer.stop_speaking_async().get()), timeout=5)
                    except Exception:
                        logging.getLogger("voice.latency").warning("azure_stop_failed")
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                # Azure EventSignal exposes disconnect_all(), not disconnect(callback).
                # This synthesizer has one owned callback, serialized by _lock.
                self._synthesizer.synthesizing.disconnect_all()


class AzureSynthesizeStream(tts.SynthesizeStream):
    async def _run(self, output_emitter):
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=24000,
                                  num_channels=1, mime_type="audio/pcm", stream=True,
                                  frame_size_ms=20)
        started = False

        async def text():
            nonlocal started
            async for token in self._input_ch:
                if isinstance(token, self._FlushSentinel):
                    if started:
                        break
                    continue
                if not token:
                    continue
                if not started:
                    output_emitter.start_segment(segment_id=utils.shortuuid())
                    started = True
                yield token

        await self._tts._render(text(), output_emitter, on_started=self._mark_started)
        if started:
            output_emitter.end_segment()
