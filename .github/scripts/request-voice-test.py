"""Actions-only: production JVM App requests, then synthetic RTC; no Android UI/hardware."""
import asyncio
import base64
import io
import json
import logging
import os
import statistics
import struct
import time
import wave
from pathlib import Path

import aiohttp
from livekit import rtc

logging.basicConfig(level=logging.ERROR)
OUT = Path('voice-reports')
SAMPLE = Path('.github/test-audio/chinese-english.wav')
VOICE_CONTEXT = ('你是自然、友好的中英双语语音助手。主要使用简体中文，'
                 '先直接回答，通常一到两句，最多80个汉字，适合朗读，不使用 Markdown。')
QUESTION = '我正在使用 LiveKit 开发 Android App。Please check the API key and settings.'


async def events(response):
    """SSE records are delimited by blank lines, including fragmented network reads."""
    lines = []
    async for raw in response.content:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if lines:
                data = '\n'.join(lines)
                lines = []
                if data == '[DONE]':
                    return
                yield json.loads(data)
        elif line.startswith('data:'):
            lines.append(line[5:].lstrip())
    if lines:
        data = '\n'.join(lines)
        if data != '[DONE]':
            yield json.loads(data)


async def http_case(client, provider, name, body, audio=False):
    start = time.monotonic()
    result = {'case': name, 'first_usable_ms': None, 'chunks': 0}
    try:
        async with client.post(provider['base_url'].rstrip('/') + '/chat/completions',
                               headers={'Authorization': 'Bearer ' + provider['api_key']},
                               json=body) as response:
            result['status'] = response.status
            result['headers_ms'] = round((time.monotonic() - start) * 1000)
            if response.status != 200:
                return result
            if body.get('stream'):
                async for event in events(response):
                    if event.get('error'):
                        raise ValueError('Provider stream returned an error')
                    for choice in event.get('choices', []):
                        delta = choice.get('delta') or {}
                        content = (delta.get('audio') or {}).get('data') if audio else delta.get('content')
                        if not content:
                            continue
                        if audio:
                            pcm = base64.b64decode(content, validate=True)
                            if not pcm or len(pcm) % 2:
                                raise ValueError('Invalid PCM16')
                            result['audio_bytes'] = result.get('audio_bytes', 0) + len(pcm)
                        else:
                            result['characters'] = result.get('characters', 0) + len(content)
                        result['chunks'] += 1
                        if result['first_usable_ms'] is None:
                            result['first_usable_ms'] = round((time.monotonic() - start) * 1000)
            else:
                event = await response.json()
                encoded = event['choices'][0]['message']['audio']['data']
                with wave.open(io.BytesIO(base64.b64decode(encoded, validate=True)), 'rb') as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                        raise ValueError('Invalid WAV format')
                    result['audio_bytes'] = len(wav.readframes(wav.getnframes()))
                result['chunks'] = 1
                result['first_usable_ms'] = round((time.monotonic() - start) * 1000)
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        result['total_ms'] = round((time.monotonic() - start) * 1000)
    return result


async def provider_controls(config):
    """Direct HTTP controls originate on the Actions runner, separate from RTC metrics."""
    timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async def llm_cases():
            results = []
            for i in range(3):
                body = {'model': config['llm']['model'], 'stream': True,
                        'reasoning_effort': 'none', 'max_completion_tokens': 256,
                        'messages': [{'role': 'system', 'content': VOICE_CONTEXT},
                                     {'role': 'user', 'content': QUESTION}]}
                results.append(await http_case(client, config['llm'], 'llm_voice_context_' + str(i + 1), body))
            return results

        async def tts_cases():
            provider = config['tts']
            results = []
            # Warm both paths before comparing them. Warm-up remains in the report.
            for name, stream, text in [('tts_warm_stream', True, '连接正常。'),
                                       ('tts_warm_wav', False, '连接正常。'),
                                       ('tts_wav_1', False, '连接正常，我正在使用 LiveKit 开发 Android App。'),
                                       ('tts_stream_1', True, '连接正常，我正在使用 LiveKit 开发 Android App。'),
                                       ('tts_stream_2', True, '连接正常，我正在使用 LiveKit 开发 Android App。'),
                                       ('tts_wav_2', False, '连接正常，我正在使用 LiveKit 开发 Android App。')]:
                body = {'model': provider['model'], 'stream': stream,
                        'messages': [{'role': 'assistant', 'content': text}],
                        'audio': {'voice': provider['voice'], 'format': 'pcm16' if stream else 'wav'}}
                results.append(await http_case(client, provider, name, body, audio=True))
            # Small concurrency control only; this cannot establish a sustained quota.
            body = {'model': provider['model'], 'stream': True,
                    'messages': [{'role': 'assistant', 'content': '连接正常。'}],
                    'audio': {'voice': provider['voice'], 'format': 'pcm16'}}
            results.extend(await asyncio.gather(*(http_case(client, provider, 'tts_concurrent_' + str(i), body, True) for i in (1, 2))))
            return results

        llm, tts = await asyncio.gather(llm_cases(), tts_cases())
        report = {'origin': 'GitHub Actions HTTP controls, not Cloud container or Android device',
                  'llm_model': config['llm']['model'], 'tts_model': config['tts']['model'],
                  'cases': llm + tts, 'observed_429': any(item.get('status') == 429 for item in llm + tts)}
        OUT.joinpath('provider-controls.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report


async def conversation(connection, count):
    room = rtc.Room()
    start = time.monotonic()
    now = lambda: time.monotonic() - start
    texts, metrics, turns, tasks = [], [], [], set()
    current = None
    last_audio = 0.0
    audio_samples = 0
    greeting = asyncio.Event()

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def read_audio(track):
        nonlocal last_audio, audio_samples
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        try:
            async for event in stream:
                if max((abs(sample) for sample in event.frame.data), default=0) > 600:
                    last_audio = now()
                    audio_samples += event.frame.samples_per_channel
                    greeting.set()
                    if current is not None and now() >= current['speech_start_s']:
                        if now() < current['speech_end_s'] - 0.2:
                            current['premature_reply_audio'] = True
                        elif now() >= current['speech_end_s']:
                            current.setdefault('first_reply_audio_s', now())
        finally:
            await stream.aclose()

    @room.on('track_subscribed')
    def on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            spawn(read_audio(track))

    @room.on('transcription_received')
    def on_text(segments, participant, publication):
        for segment in segments:
            if segment.text.strip():
                texts.append({'text': segment.text, 'final': segment.final, 'source': 'event',
                              'identity': participant.identity if participant else None, 'received_s': now()})

    async def read_text(reader, identity):
        content = ''
        async for chunk in reader:
            content += chunk
            texts.append({'text': content, 'final': False, 'source': 'stream', 'identity': identity, 'received_s': now()})
        if content:
            texts.append({'text': content, 'final': True, 'source': 'stream', 'identity': identity, 'received_s': now()})

    room.register_text_stream_handler('lk.transcription', lambda reader, identity: spawn(read_text(reader, identity)))

    @room.on('data_received')
    def on_metrics(packet):
        if packet.topic != 'voice.pipeline.metrics':
            return
        try:
            payload = json.loads(packet.data)
            # Allowlist numeric fields. Do not accept arbitrary secret/text payloads.
            metrics.append({'received_s': now(), 'type': payload.get('type'),
                            'revision': payload.get('revision'),
                            **{k: v for k, v in payload.items() if k in ('ttft', 'ttfb', 'duration', 'end_of_utterance_delay', 'transcription_delay', 'on_user_turn_completed_delay') and isinstance(v, (int, float))}})
        except (ValueError, UnicodeError):
            pass

    source = None
    report = {'origin': 'synthetic Python RTC client using credentials resolved by production App JVM code',
              'label': os.getenv('VOICE_TEST_LABEL'), 'turns': turns, 'pipeline_metrics': metrics,
              'targets': {'first_transcript_s': 1.5, 'final_transcript_after_end_s': 1.0,
                          'reply_median_after_end_s': 2.0, 'reply_max_after_end_s': 3.0},
              'measurement_limit': 'Runner network and synthetic speech; no phone microphone, Android audio or UI measurement. Small sample, not a p95 claim.'}
    try:
        await room.connect(connection['url'], connection['token'])
        report['room_connect_s'] = round(now(), 3)
        source = rtc.AudioSource(24000, 1)
        track = rtc.LocalAudioTrack.create_audio_track('synthetic-microphone', source)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await asyncio.wait_for(greeting.wait(), 55)
        deadline = now() + 30
        while now() - last_audio < 1.0:
            if now() > deadline:
                raise TimeoutError('Greeting did not finish')
            await asyncio.sleep(0.1)
        with wave.open(str(SAMPLE), 'rb') as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
            pcm = wav.readframes(wav.getnframes())
        active = [i for i, (value,) in enumerate(struct.iter_unpack('<h', pcm)) if abs(value) > 600]
        if not active:
            raise ValueError('Synthetic speech is silent')
        framed = b'\0' * 24000 + pcm + b'\0' * 72000
        for turn in range(count):
            before = len(texts)
            before_audio = audio_samples
            base = now()
            current = {'turn': turn + 1, 'speech_start_s': base + 0.5 + active[0] / 24000,
                       'speech_end_s': base + 0.5 + (active[-1] + 1) / 24000,
                       'max_input_pacing_lag_s': 0.0}
            turns.append(current)
            for offset in range(0, len(framed), 960):
                due = base + offset / 48000
                await asyncio.sleep(max(0, due - now()))
                current['max_input_pacing_lag_s'] = max(current['max_input_pacing_lag_s'], now() - due)
                chunk = framed[offset:offset + 960]
                await source.capture_frame(rtc.AudioFrame(data=chunk, sample_rate=24000, num_channels=1, samples_per_channel=len(chunk) // 2))
            await source.wait_for_playout()
            deadline = now() + 35
            while now() < deadline:
                incoming = texts[before:]
                user = [item for item in incoming if item['identity'] == room.local_participant.identity]
                finals = [item for item in user if item['final'] and item['source'] == 'event']
                if not finals:
                    finals = [item for item in user if item['final']]
                combined = ' '.join(item['text'] for item in finals)
                bilingual = ('livekit' in combined.lower() and 'api' in combined.lower() and 'settings' in combined.lower()
                             and any('\u4e00' <= c <= '\u9fff' for c in combined))
                if user:
                    current['first_transcript_s'] = round(min(item['received_s'] for item in user) - current['speech_start_s'], 3)
                if finals:
                    current['final_transcript_after_end_s'] = round(max(item['received_s'] for item in finals) - current['speech_end_s'], 3)
                    current['final_transcript'] = combined  # Only fictional test speech.
                if 'first_reply_audio_s' in current:
                    current['reply_audio_after_end_s'] = round(current['first_reply_audio_s'] - current['speech_end_s'], 3)
                agent_final = any(item['final'] and item['identity'] != room.local_participant.identity for item in incoming)
                agents_listening = any(p.attributes.get('lk.agent.state') == 'listening' for p in room.remote_participants.values())
                complete = agent_final and agents_listening and now() - last_audio > 0.8 and audio_samples - before_audio >= 12000
                current['bilingual_recognition_pass'] = bilingual
                current['spoken_reply_pass'] = complete
                if bilingual and complete:
                    break
                await asyncio.sleep(0.1)
            else:
                raise TimeoutError('A complete bilingual conversational turn was not received')
            current['max_input_pacing_lag_s'] = round(current['max_input_pacing_lag_s'], 4)
            current = None
        report['functional_pass'] = all(t['bilingual_recognition_pass'] and t['spoken_reply_pass'] for t in turns)
        delays = [t['reply_audio_after_end_s'] for t in turns]
        report['reply_median_after_end_s'] = round(statistics.median(delays), 3)
        report['reply_max_after_end_s'] = max(delays)
        report['realtime_pass'] = (report['functional_pass'] and statistics.median(delays) <= 2.0 and max(delays) <= 3.0
                                  and all(t['first_transcript_s'] <= 1.5 and t['final_transcript_after_end_s'] <= 1.0
                                          and t['max_input_pacing_lag_s'] <= 0.1
                                          and not t.get('premature_reply_audio', False) for t in turns))
    except Exception as error:
        report['functional_pass'] = False
        report['realtime_pass'] = False
        report['error_type'] = type(error).__name__
    finally:
        current = None
        await room.disconnect()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if source:
            await source.aclose()
        OUT.joinpath('conversation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


async def main():
    OUT.mkdir(exist_ok=True)
    config = json.loads(os.environ['VOICE_TEST_CONFIG'])
    connection = json.loads(Path(os.environ['RESOLVED_CONNECTION_FILE']).read_text())
    for secret in (connection['token'], config['llm']['api_key'], config['tts']['api_key']):
        print('::add-mask::' + secret)
    # Sequential to avoid HTTP control requests competing with the measured voice turn.
    providers = await provider_controls(config)
    report = await conversation(connection, int(os.getenv('VOICE_TEST_TURNS', '3')))
    lines = ['# Fast request and conversational latency measurement',
             'No emulator or UI. App connection selection and SDK token requests run on JVM; speech uses a synthetic RTC client.',
             f"Functional pass: {report['functional_pass']}; real-time pass: {report['realtime_pass']}",
             '', '| Turn | First transcript | Final after speech | Reply audio after speech |',
             '| --- | ---: | ---: | ---: |']
    for turn in report['turns']:
        lines.append(f"| {turn['turn']} | {turn.get('first_transcript_s')} s | {turn.get('final_transcript_after_end_s')} s | {turn.get('reply_audio_after_end_s')} s |")
    lines.extend(['', 'Targets: first transcript <=1.5s; final <=1s; reply median <=2s and every sample <=3s.',
                  'Small synthetic sample; no phone network/hardware or p95 claim.', '',
                  '| HTTP control | Status | First usable | Chunks |', '| --- | ---: | ---: | ---: |'])
    for case in providers['cases']:
        lines.append(f"| {case['case']} | {case.get('status')} | {case.get('first_usable_ms')} ms | {case['chunks']} |")
    summary = '\n'.join(lines) + '\n'
    OUT.joinpath('summary.md').write_text(summary, encoding='utf-8')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write(summary)
    print('Functional pass:', report['functional_pass'], 'Real-time pass:', report['realtime_pass'])
    if not report['realtime_pass']:
        raise SystemExit('Real-time latency target not met; detailed reports retained.')


if __name__ == '__main__':
    asyncio.run(main())
