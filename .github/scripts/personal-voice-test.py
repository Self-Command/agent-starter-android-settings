"""Actions-only self-hosted acceptance: App-resolved token, real RTC, fictional speech."""
import asyncio
import base64
import importlib.util
import json
import os
import struct
import time
import wave
from pathlib import Path

import aiohttp
from livekit import rtc

spec = importlib.util.spec_from_file_location('voice_probe', Path(__file__).with_name('request-voice-test.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


async def fixture(client, provider, name, text):
    body = {'model': provider['model'], 'stream': True,
            'messages': [{'role': 'assistant', 'content': text}],
            'audio': {'voice': provider['voice'], 'format': 'pcm16'}}
    pcm = bytearray()
    async with client.post(provider['base_url'].rstrip('/') + '/chat/completions',
                           headers={'Authorization': 'Bearer ' + provider['api_key']}, json=body) as response:
        if response.status != 200:
            raise RuntimeError('Fixture HTTP status ' + str(response.status))
        async for event in probe.events(response):
            for choice in event.get('choices', []):
                encoded = ((choice.get('delta') or {}).get('audio') or {}).get('data')
                if encoded:
                    pcm.extend(base64.b64decode(encoded, validate=True))
    if len(pcm) < 4800 or len(pcm) % 2:
        raise ValueError('Invalid fixture audio')
    path = Path(os.environ['RUNNER_TEMP']) / (name + '.wav')
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm)
    return path


def pause_fixture(path):
    with wave.open(str(path), 'rb') as source:
        pcm = source.readframes(source.getnframes())
    # Add 250ms at an existing quiet gap near the middle, rather than splitting a word.
    values = [value[0] for value in struct.iter_unpack('<h', pcm)]
    quiet = [i for i in range(len(values) // 3, len(values) * 2 // 3, 240)
             if max(abs(v) for v in values[i:i + 240]) < 400]
    if not quiet:
        raise ValueError('No natural pause in mixed-language fixture')
    position = min(quiet, key=lambda i: abs(i - len(values) // 2)) * 2
    target = Path(os.environ['RUNNER_TEMP']) / 'mixed-with-pause.wav'
    with wave.open(str(target), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm[:position] + b'\0' * 12000 + pcm[position:])
    return target


async def new_connection(client, config):
    async with client.post(config['token_endpoint'], json={'participant_identity': 'action-client'}) as response:
        if response.status != 200:
            raise RuntimeError('Token HTTP status ' + str(response.status))
        body = await response.json()
        return {'url': body['server_url'], 'token': body['participant_token']}


async def behavior_checks(client, config, interrupt_path):
    """Interrupt a fresh greeting, then disconnect and join again with a renewed token."""
    report = {'interruption_pass': False, 'reconnect_pass': False}
    room = rtc.Room()
    tasks = set()
    source = None
    last_audio = 0.0
    got_audio = asyncio.Event()
    finals = []

    async def read_audio(track):
        nonlocal last_audio
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        try:
            async for event in stream:
                if max((abs(value) for value in event.frame.data), default=0) > 600:
                    last_audio = time.monotonic()
                    got_audio.set()
        finally:
            await stream.aclose()

    @room.on('track_subscribed')
    def on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            task = asyncio.create_task(read_audio(track))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    @room.on('transcription_received')
    def on_text(segments, participant, publication):
        if participant and participant.identity == room.local_participant.identity:
            finals.extend(segment.text for segment in segments if segment.final)

    try:
        await asyncio.sleep(15)  # Server closes the previous single-user room.
        connection = await new_connection(client, config)
        await room.connect(connection['url'], connection['token'])
        source = rtc.AudioSource(24000, 1)
        track = rtc.LocalAudioTrack.create_audio_track('interrupt-microphone', source)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await asyncio.wait_for(got_audio.wait(), 30)
        with wave.open(str(interrupt_path), 'rb') as wav:
            pcm = wav.readframes(wav.getnframes())
        active = [i for i, (value,) in enumerate(struct.iter_unpack('<h', pcm)) if abs(value) > 600]
        base = time.monotonic()
        speech_start = base + active[0] / 24000
        stopped = None
        framed = pcm + b'\0' * 96000
        for offset in range(0, len(framed), 960):
            await asyncio.sleep(max(0, base + offset / 48000 - time.monotonic()))
            if time.monotonic() > speech_start + 0.3 and time.monotonic() - last_audio > 0.35:
                if stopped is None:
                    stopped = max(0.0, last_audio - speech_start)
            chunk = framed[offset:offset + 960]
            await source.capture_frame(rtc.AudioFrame(data=chunk, sample_rate=24000, num_channels=1,
                                                      samples_per_channel=len(chunk) // 2))
        await source.wait_for_playout()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if finals and any(p.attributes.get('lk.agent.state') == 'listening'
                              for p in room.remote_participants.values()) and time.monotonic() - last_audio > 0.8:
                break
            await asyncio.sleep(0.1)
        report['greeting_audio_ceased_after_speech_s'] = round(stopped, 3) if stopped is not None else None
        report['interrupt_transcription_received'] = bool(finals)
        report['interruption_pass'] = stopped is not None and stopped <= 1.5 and bool(finals)
        report['transport'] = await probe.transport_snapshot(room)
        await room.disconnect()
        await source.aclose()
        source = None
        got_audio.clear()
        await asyncio.sleep(15)
        connection = await new_connection(client, config)
        start = time.monotonic()
        await room.connect(connection['url'], connection['token'])
        await asyncio.wait_for(got_audio.wait(), 30)
        report['reconnect_to_audio_s'] = round(time.monotonic() - start, 3)
        report['reconnect_pass'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        await room.disconnect()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)
        if source:
            await source.aclose()
    return report


async def main():
    probe.OUT.mkdir(exist_ok=True)
    config = json.loads(os.environ['VOICE_TEST_CONFIG'])
    connection = json.loads(Path(os.environ['RESOLVED_CONNECTION_FILE']).read_text())
    for secret in (connection['token'], config['token_endpoint'], config['tts']['api_key']):
        print('::add-mask::' + secret)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as client:
        chinese = await fixture(client, config['tts'], 'chinese', '请用一句话告诉我，语音助手连接正常吗？')
        english = await fixture(client, config['tts'], 'english', 'Please say that the voice connection is working.')
        interrupt = await fixture(client, config['tts'], 'interrupt', '请停一下，只回答收到。')
        paused = pause_fixture(probe.SAMPLE)
        mixed = {'path': probe.SAMPLE, 'name': 'Chinese-English', 'terms': ['livekit', 'api', 'settings'], 'chinese': True}
        samples = [mixed,
                   {'path': chinese, 'name': 'Chinese', 'terms': ['语音', '连接'], 'chinese': True},
                   {'path': english, 'name': 'English', 'terms': ['voice', 'connection']},
                   {**mixed, 'path': paused, 'name': 'Chinese-English with 250ms pause'}, mixed]
        report = await probe.conversation(connection, int(os.getenv('VOICE_TEST_TURNS', '10')), samples)
        behavior = await behavior_checks(client, config, interrupt) if os.getenv('VOICE_TEST_BEHAVIOR') == 'true' else {'skipped': True}
    probe.OUT.joinpath('behavior.json').write_text(json.dumps(behavior, indent=2), encoding='utf-8')
    lines = ['# Personal LiveKit voice acceptance',
             'Production Android App TokenSource runs on JVM in Actions. Audio is synthetic RTC; no phone/UI test.',
             f"Functional: {report['functional_pass']}; realtime: {report['realtime_pass']}",
             f"Interruption/reconnect: {behavior}", '',
             '| Turn | Scenario | First transcript | Final after speech | Reply audio after speech |',
             '| --- | --- | ---: | ---: | ---: |']
    for turn in report['turns']:
        lines.append(f"| {turn['turn']} | {turn['scenario']} | {turn.get('first_transcript_s')} s | {turn.get('final_transcript_after_end_s')} s | {turn.get('reply_audio_after_end_s')} s |")
    lines.extend(['', 'Targets: first transcript <=1.5s; final <=1s; reply median <=2s and every sample <=3s.',
                  'ICE transport, RTT, packet loss, jitter and numerical pipeline metrics are in conversation.json.'])
    summary = '\n'.join(lines) + '\n'
    probe.OUT.joinpath('summary.md').write_text(summary, encoding='utf-8')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
        output.write(summary)
    behavior_pass = behavior.get('skipped') or (behavior.get('interruption_pass') and behavior.get('reconnect_pass'))
    if not report['realtime_pass'] or not behavior_pass:
        raise SystemExit('Acceptance target not met; sanitized reports retained.')


if __name__ == '__main__':
    asyncio.run(main())
