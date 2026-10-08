"""Xiaomi MiMo preset-voice TTS adapter. Provider credentials stay in env only."""
import asyncio
import base64
import io
import json
import os
import uuid
import wave

import aiohttp
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, tts, utils
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

SAMPLE_RATE = 24000


def request_payload(text: str, voice: str, model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "assistant", "content": text}],
        "audio": {"format": "pcm16", "voice": voice},
        "stream": True,
    }


def decode_audio(body: dict) -> bytes:
    encoded = body["choices"][0]["message"]["audio"]["data"]
    audio = base64.b64decode(encoded, validate=True)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("MiMo audio format must be 24 kHz mono PCM16")
        if wav.getcomptype() != "NONE":
            raise ValueError("MiMo audio must be uncompressed PCM")
        pcm = wav.readframes(wav.getnframes())
    if not pcm:
        raise ValueError("MiMo returned empty audio")
    return pcm


def decode_pcm_event(event: dict) -> bytes:
    if event.get("error"):
        raise ValueError("MiMo stream reported an error")
    audio = b""
    for choice in event.get("choices", []):
        encoded = ((choice.get("delta") or {}).get("audio") or {}).get("data")
        if not encoded:
            continue
        pcm = base64.b64decode(encoded, validate=True)
        if not pcm or len(pcm) % 2:
            raise ValueError("MiMo streaming audio must contain complete PCM16 samples")
        audio += pcm
    return audio


async def sse_events(content):
    data = []
    async for raw in content:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                payload = "\n".join(data)
                data = []
                if payload == "[DONE]":
                    yield {"done": True}
                    return
                yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        payload = "\n".join(data)
        yield {"done": True} if payload == "[DONE]" else json.loads(payload)


class MiMoTTS(tts.TTS):
    def __init__(self, *, api_key=None, base_url=None, model=None, voice=None):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
        )
        self._api_key = api_key or os.environ["MIMO_API_KEY"]
        self._base_url = (base_url or os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")).rstrip("/")
        self._model = model or os.getenv("MIMO_TTS_MODEL", "mimo-v2.5-tts")
        self._voice = voice or os.getenv("MIMO_VOICE", "白桦")

    @property
    def model(self):
        return self._model

    @property
    def provider(self):
        return "xiaomimimo"

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return MiMoChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class MiMoChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter):
        provider = self._tts
        emitted = False
        try:
            async with utils.http_context.http_session().post(
                provider._base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + provider._api_key},
                json=request_payload(self._input_text, provider._voice, provider._model),
                timeout=aiohttp.ClientTimeout(total=45, sock_connect=15),
            ) as response:
                if response.status != 200:
                    raise APIStatusError("MiMo TTS request failed", status_code=response.status)
                finished = False
                async for event in sse_events(response.content):
                    finished = finished or event.get("done", False) or any(
                        choice.get("finish_reason") for choice in event.get("choices", [])
                    )
                    pcm = decode_pcm_event(event)
                    if not pcm:
                        continue
                    if not emitted:
                        output_emitter.initialize(
                            request_id=str(event.get("id") or uuid.uuid4()),
                            sample_rate=SAMPLE_RATE, num_channels=1, mime_type="audio/pcm",
                        )
                    # Send each provider chunk immediately, without waiting for EOF.
                    output_emitter.push(pcm)
                    emitted = True
                if not emitted or not finished:
                    raise ValueError("MiMo stream was empty or incomplete")
        except asyncio.TimeoutError:
            raise APITimeoutError("MiMo TTS timed out", retryable=not emitted) from None
        except APIStatusError:
            raise
        except Exception:
            raise APIConnectionError("MiMo TTS returned invalid audio or could not be reached", retryable=not emitted) from None
