"""Single-session self-hosted voice agent; provider credentials stay in env."""
import asyncio
import json
import logging
import os
from pathlib import Path

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, room_io, tts, tokenize
from livekit.plugins import azure, openai, silero

from bilingual_stt import BilingualDeepgramSTT
from mimo_tts import MiMoTTS
from llm_options import completion_options
from azure_streaming_tts import AzureStreamingTTS

for logger_name in ("httpx", "httpcore", "openai"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# SDK debug events can include transcripts. Keep operational errors and our
# numerical metrics, without logging provider requests or conversation text.
logging.getLogger("livekit.agents").setLevel(logging.WARNING)
logging.getLogger("voice.latency").setLevel(logging.INFO)

ENDPOINT_DELAY = float(os.getenv("VOICE_ENDPOINT_DELAY", "0.45"))
PREEMPTIVE = os.getenv("VOICE_PREEMPTIVE", "false").lower() == "true"
REVISION = os.getenv("VOICE_REVISION", "personal-baseline")
TURN_DETECTION = os.getenv("VOICE_TURN_DETECTION", "vad")
if TURN_DETECTION not in ("vad", "stt"):
    raise ValueError("Unsupported VOICE_TURN_DETECTION")


class Assistant(Agent):
    def __init__(self):
        # No custom response style or length instructions.
        super().__init__(instructions="")


server = AgentServer(num_idle_processes=1, host="127.0.0.1", port=8081,
                     job_memory_warn_mb=700, job_memory_limit_mb=900, log_level="WARN")
server.load_fnc = lambda worker: min(float(len(worker.active_jobs)), 1.0)


def prewarm(proc: agents.JobProcess):
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=ENDPOINT_DELAY)
    Path('/tmp/livekit-agent-ready').write_text(str(os.getpid()))


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    vad = ctx.proc.userdata["vad"]
    provider = os.getenv("TTS_PROVIDER", "azure").lower()
    if provider == "azure_streaming":
        speech_tts = AzureStreamingTTS()
    elif provider == "azure":
        speech_tts = azure.TTS(
            speech_key=os.environ["AZURE_SPEECH_KEY"],
            speech_region=os.environ["AZURE_SPEECH_REGION"],
            voice=os.getenv("AZURE_TTS_VOICE", "zh-CN-XiaoxiaoMultilingualNeural"),
            language=os.getenv("AZURE_TTS_LANGUAGE", "zh-CN"),
            sample_rate=24000,
        )
    else:
        speech_tts = MiMoTTS()

    voice_output = speech_tts if speech_tts.capabilities.streaming else tts.StreamAdapter(
        tts=speech_tts,
        sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
            min_sentence_len=6, stream_context_len=1, retain_format=True,
        ),
    )
    if speech_tts.capabilities.streaming:
        speech_tts.prewarm()
        ctx.add_shutdown_callback(speech_tts.aclose)

    session = AgentSession(
        vad=vad,
        stt=BilingualDeepgramSTT(),
        llm=openai.LLM(
            model=os.getenv("LLM_MODEL", "gpt-5.6-luna"),
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            **completion_options(os.getenv("LLM_API_STYLE", "openai")),
        ),
        tts=voice_output,
        turn_handling=TurnHandlingOptions(
            turn_detection=TURN_DETECTION, interruption={"mode": "vad"},
            endpointing={"mode": "fixed", "min_delay": ENDPOINT_DELAY, "max_delay": 1.5},
            preemptive_generation={"enabled": PREEMPTIVE},
        ),
    )
    metric_tasks = set()

    @session.on("user_state_changed")
    def on_user_state(event):
        if event.new_state == "speaking" and speech_tts.capabilities.streaming:
            speech_tts.prewarm()

    async def publish_metric(payload):
        # Numerical diagnostics only, directed to the isolated CI participant.
        targets = [p.identity for p in ctx.room.remote_participants.values() if p.identity == "action-client"]
        if not targets:
            return
        try:
            await ctx.room.local_participant.publish_data(
                json.dumps(payload), topic="voice.pipeline.metrics", reliable=True,
                destination_identities=targets,
            )
        except Exception:
            logging.getLogger("voice.latency").warning("metric_delivery_failed")

    @session.on("metrics_collected")
    def on_metrics(event):
        metrics = event.metrics
        values = {name: getattr(metrics, name) for name in (
            "ttft", "ttfb", "duration", "end_of_utterance_delay", "transcription_delay",
            "on_user_turn_completed_delay",
        ) if hasattr(metrics, name) and isinstance(getattr(metrics, name), (int, float))}
        logging.getLogger("voice.latency").info("pipeline_metrics", extra={"metric_type": metrics.type, **values})
        task = asyncio.create_task(publish_metric({"type": metrics.type, "revision": REVISION, **values}))
        metric_tasks.add(task)
        task.add_done_callback(metric_tasks.discard)

    # Forward generated text immediately instead of pacing it to TTS playback.
    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(
            text_output=room_io.TextOutputOptions(sync_transcription=False),
        ),
        record=False,
    )
    await session.say("你好，请讲。")


if __name__ == "__main__":
    agents.cli.run_app(server)
