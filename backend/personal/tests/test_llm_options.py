import json
import unittest

import httpx
from openai import AsyncOpenAI
from livekit.plugins import openai as livekit_openai

from llm_options import completion_options


class LLMRequestTest(unittest.IsolatedAsyncioTestCase):
    async def request_body(self, style):
        bodies = []

        def respond(request):
            bodies.append(json.loads(request.content))
            event = {"id": "fictional", "object": "chat.completion.chunk", "created": 0,
                     "model": "fictional", "choices": [{"index": 0, "delta": {"content": "好。"}, "finish_reason": None}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

        async with AsyncOpenAI(api_key="fictional", base_url="https://fictional.invalid/v1",
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            options = completion_options(style)
            # The pinned LiveKit plugin must accept the same provider options.
            llm = livekit_openai.LLM(model="fictional", client=client, **options)
            self.assertIsNotNone(llm)
            stream = await client.chat.completions.create(model="fictional", stream=True,
                messages=[{"role": "user", "content": "请简短回答。"}], **options)
            self.assertEqual([chunk.choices[0].delta.content async for chunk in stream], ["好。"])
        return bodies[0]

    async def test_deepseek_wire_request_disables_thinking(self):
        body = await self.request_body("deepseek")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 256)
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("reasoning_effort", body)

    async def test_existing_gateway_request_stays_compatible(self):
        body = await self.request_body("openai")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_completion_tokens"], 256)
        self.assertNotIn("thinking", body)

    async def test_unknown_style_fails_before_any_request(self):
        with self.assertRaisesRegex(ValueError, "LLM_API_STYLE"):
            completion_options("typo")
