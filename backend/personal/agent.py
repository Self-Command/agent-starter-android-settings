"""Single-session self-hosted voice agent; provider credentials stay in env."""
import asyncio
import json
import logging
import os
from pathlib import Path

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, tts, tokenize
from livekit.plugins import openai, silero

from bilingual_stt import BilingualDeepgramSTT
from mimo_tts import MiMoTTS

for logger_name in ("httpx", "httpcore", "openai"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# SDK debug events can include transcripts. Keep operational errors and our
# numerical metrics, without logging provider requests or conversation text.
logging.getLogger("livekit.agents").setLevel(logging.WARNING)
logging.getLogger("voice.latency").setLevel(logging.INFO)

ENDPOINT_DELAY = float(os.getenv("VOICE_ENDPOINT_DELAY", "0.45"))
PREEMPTIVE = os.getenv("VOICE_PREEMPTIVE", "false").lower() == "true"
REVISION = os.getenv("VOICE_REVISION", "personal-baseline")


class Assistant(Agent):
    def __init__(self):
        super().__init__(instructions=(
            "你是自然、友好的中英双语语音助手。主要使用简体中文，"
            "用户使用英语时可用英语回答，用户混用中英文时保持术语准确。"
            "先用一句简短完整的句子直接回答，再按需要补充。通常一到两句，最多80个汉字，适合朗读，不使用 Markdown、表格、星号或表情。"
            "不要声称已经执行你没有工具完成的操作。"
        ))


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
    session = AgentSession(
        vad=vad,
        stt=BilingualDeepgramSTT(),
        llm=openai.LLM(
            model=os.getenv("LLM_MODEL", "gpt-5.6-luna"),
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            reasoning_effort="none",
            max_completion_tokens=256,
        ),
        tts=tts.StreamAdapter(
            tts=MiMoTTS(),
            sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
                min_sentence_len=6, stream_context_len=1, retain_format=True,
            ),
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad", interruption={"mode": "vad"},
            endpointing={"mode": "fixed", "min_delay": ENDPOINT_DELAY, "max_delay": 1.5},
            preemptive_generation={"enabled": PREEMPTIVE},
        ),
    )
    metric_tasks = set()

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

    await session.start(room=ctx.room, agent=Assistant(), record=False)
    await session.say("你好，语音助手已就绪。你可以用中文、英文，或者中英文混合与我交流。")


if __name__ == "__main__":
    agents.cli.run_app(server)
