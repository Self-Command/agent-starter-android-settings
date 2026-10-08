"""Receive a timing report computed by the Agent inside LiveKit Cloud, not on this runner."""
import asyncio
import json
import logging
import os
from pathlib import Path

from livekit import rtc

logging.basicConfig(level=logging.ERROR)
REPORT = Path("provider-network-report.json")


async def main():
    token = os.environ["LIVEKIT_PROBE_TOKEN"]
    print("::add-mask::" + token)
    room = rtc.Room()
    received = asyncio.get_running_loop().create_future()

    @room.on("data_received")
    def on_data(packet):
        if packet.topic == "voice.latency.report" and not received.done():
            try:
                received.set_result(json.loads(packet.data))
            except (UnicodeError, ValueError):
                received.set_exception(ValueError("Invalid network measurement report"))

    try:
        await room.connect(os.environ["LIVEKIT_SMOKE_URL"], token)
        report = await asyncio.wait_for(received, timeout=360)
        # Curl reports exact per-transfer times. Its timing write-out may be buffered
        # until the next transfer, so stdout arrival offsets cannot define request latency.
        for case in report.get("cases", []):
            for sample in case["samples"]:
                for field in ("first_body_observed_ms", "first_model_token_ms", "first_sentence_ms"):
                    sample.pop(field, None)
        for summary in report.get("summaries", []):
            summary.pop("first_model_token_ms", None)
            summary.pop("first_sentence_ms", None)
        report["measurement_notes"] = [note for note in report.get("measurement_notes", []) if "first token/sentence" not in note]
        report["measurement_notes"].append("Use curl per-transfer timings; stdout delivery offsets discarded because timing write-out may be buffered across transfers.")
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert report.get("execution_environment") == "LiveKit Cloud agent runtime container", "Missing container report"
        assert len(report.get("cases", [])) == 4, "Incomplete benchmark report"
        assert all(len(case["samples"]) == 3 for case in report["cases"]), "Expected three samples per case"
        print("Measurements executed inside the LiveKit Cloud Agent container; this runner only received the report.")
        print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))
    finally:
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
