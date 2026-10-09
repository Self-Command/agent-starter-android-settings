import json
import unittest

import httpx
from openai import AsyncOpenAI
from livekit.plugins import openai as livekit_openai
from livekit.agents.llm import ChatContext

from llm_options import completion_options
from voice_policy import VOICE_INSTRUCTIONS


class LLMRequestTest(unittest.IsolatedAsyncioTestCase):
    async def request_body(self, style):
        bodies = []
        reply = "完整回答。" * 80

        def respond(request):
            bodies.append(json.loads(request.content))
            event = {"id": "fictional", "object": "chat.completion.chunk", "created": 0,
                     "model": "fictional", "choices": [{"index": 0, "delta": {"content": reply}, "finish_reason": None}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

        async with AsyncOpenAI(api_key="fictional", base_url="https://fictional.invalid/v1",
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            options = completion_options(style)
            # Inspect the actual LiveKit wire request, not a parallel SDK request.
            llm = livekit_openai.LLM(model="fictional", client=client, **options)
            context = ChatContext()
            context.add_message(role="system", content=VOICE_INSTRUCTIONS)
            context.add_message(role="user", content="请详细回答。")
            async with llm.chat(chat_ctx=context) as stream:
                content = "".join([chunk.delta.content or "" async for chunk in stream if chunk.delta])
            self.assertEqual(content, reply)
            self.assertGreater(len(content), 80)
        return bodies[0]

    async def test_deepseek_wire_request_disables_thinking(self):
        body = await self.request_body("deepseek")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 1024)
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["messages"][0], {"role": "system", "content": VOICE_INSTRUCTIONS})

    async def test_existing_gateway_request_stays_compatible(self):
        body = await self.request_body("openai")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("thinking", body)

    async def test_unknown_style_fails_before_any_request(self):
        with self.assertRaisesRegex(ValueError, "LLM_API_STYLE"):
            completion_options("typo")
