"""Run on the deployed server: numeric direct-provider timing, no SDK or transcript logs."""
import asyncio
import base64
import json
import os
import time
from datetime import datetime, timezone

import aiohttp
from llm_options import completion_options


async def measure(client, provider, index, trace):
    start = time.monotonic()
    result = {'provider': provider['name'], 'sample': index, 'first_usable_ms': None, 'chunks': 0}
    text = ''
    try:
        async with client.post(provider['url'] + '/chat/completions',
                               headers={'Authorization': 'Bearer ' + provider['key']},
                               json=provider['body'], trace_request_ctx=trace) as response:
            result.update(status=response.status, headers_ms=round((time.monotonic() - start) * 1000))
            if response.status != 200:
                retry = response.headers.get('Retry-After', '')
                result['retry_after_s'] = int(retry) if retry.isdigit() else None
                return result
            data = []
            async for raw in response.content:
                line = raw.decode('utf-8').rstrip('\r\n')
                if line.startswith('data:'):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    payload = '\n'.join(data)
                    data = []
                    if payload == '[DONE]':
                        break
                    event = json.loads(payload)
                    if event.get('error'):
                        raise ValueError('Provider SSE error')
                    for choice in event.get('choices', []):
                        delta = choice.get('delta') or {}
                        content = ((delta.get('audio') or {}).get('data') if provider['name'] == 'tts'
                                   else delta.get('content'))
                        if not content:
                            continue
                        if provider['name'] == 'tts':
                            pcm = base64.b64decode(content, validate=True)
                            if len(pcm) % 2:
                                raise ValueError('Incomplete PCM16')
                            result['audio_bytes'] = result.get('audio_bytes', 0) + len(pcm)
                        result['chunks'] += 1
                        if result['first_usable_ms'] is None:
                            result['first_usable_ms'] = round((time.monotonic() - start) * 1000)
                        if provider['name'] != 'tts':
                            text += content
                            if result.get('first_sentence_ms') is None and any(c in text for c in '。！？.!?\n'):
                                result['first_sentence_ms'] = round((time.monotonic() - start) * 1000)
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        result['total_ms'] = round((time.monotonic() - start) * 1000)
        result['network'] = {name: round(value * 1000) for name, value in trace.items() if name.endswith('_s')}
    return result


async def main():
    llm = {'name': 'llm', 'url': os.environ['LLM_BASE_URL'].rstrip('/'), 'key': os.environ['LLM_API_KEY'],
           'body': {'model': os.environ['LLM_MODEL'], 'stream': True,
                    'messages': [{'role': 'user', 'content': '语音助手连接正常吗？'}]}}
    options = completion_options(os.getenv('LLM_API_STYLE', 'openai'))
    llm['body'].update(options.pop('extra_body', {}))
    llm['body'].update(options)
    tts = {'name': 'tts', 'url': os.environ['MIMO_BASE_URL'].rstrip('/'), 'key': os.environ['MIMO_API_KEY'],
           'body': {'model': os.environ['MIMO_TTS_MODEL'], 'stream': True,
                    'messages': [{'role': 'assistant', 'content': '连接正常，你可以开始与我交流。'}],
                    'audio': {'voice': os.environ['MIMO_VOICE'], 'format': 'pcm16'}}}
    trace = aiohttp.TraceConfig()

    async def dns_start(session, context, params):
        context.request_context_start = time.monotonic()

    async def dns_end(session, context, params):
        context.trace_request_ctx['dns_s'] = time.monotonic() - context.request_context_start

    async def connect_start(session, context, params):
        context.connect_start = time.monotonic()

    async def connect_end(session, context, params):
        context.trace_request_ctx['connect_including_tls_s'] = time.monotonic() - context.connect_start

    trace.on_dns_resolvehost_start.append(dns_start)
    trace.on_dns_resolvehost_end.append(dns_end)
    trace.on_connection_create_start.append(connect_start)
    trace.on_connection_create_end.append(connect_end)
    cases = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40), trace_configs=[trace]) as client:
        for provider in (llm, tts):
            for index in range(1, 4):
                result = await measure(client, provider, index, {})
                cases.append(result)
                print(json.dumps(result), flush=True)
                await asyncio.sleep(max(3, min(result.get('retry_after_s') or 0, 30)))
    report = {'origin': 'self-hosted server direct HTTP; TCP/TLS timing is not pure packet RTT',
              'measured_at': datetime.now(timezone.utc).isoformat(), 'cases': cases,
              'observed_429': any(case.get('status') == 429 for case in cases),
              'limit': 'Three serial samples per provider cannot establish a sustained rate limit.'}
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
