import json
import os
import time
import unittest
from unittest.mock import patch

import jwt
from aiohttp.test_utils import TestClient, TestServer

from token_server import create_app, ROOM


class TokenEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "TOKEN_ENDPOINT_SECRET": "fictional-private-path",
            "LIVEKIT_PUBLIC_URL": "wss://fictional.invalid",
            "LIVEKIT_API_KEY": "fictional-key",
            "LIVEKIT_API_SECRET": "fictional-secret-with-more-than-32-characters",
        })
        self.env.start()
        self.client = TestClient(TestServer(create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.env.stop()

    async def test_private_path_required(self):
        r = await self.client.post("/token/incorrect", json={})
        self.assertEqual(r.status, 404)
        self.assertNotIn("participant_token", await r.text())

    async def test_sdk_shape_and_room_limited_short_lived_signature(self):
        r = await self.client.post("/token/fictional-private-path", json={"room_name": "unauthorized-room"})
        body = await r.json()
        self.assertEqual(set(body), {"server_url", "participant_token"})
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        decoded = jwt.decode(body["participant_token"], os.environ["LIVEKIT_API_SECRET"], algorithms=["HS256"])
        self.assertEqual(decoded["video"]["room"], ROOM)
        self.assertTrue(decoded["video"]["roomJoin"])
        self.assertFalse(decoded["video"].get("roomAdmin", False))
        self.assertEqual(decoded["video"]["canPublishSources"], ["microphone"])
        self.assertLessEqual(decoded["exp"] - time.time(), 600)
        self.assertGreater(decoded["exp"] - time.time(), 590)
        self.assertTrue(decoded["sub"].startswith("user-"))

    async def test_fresh_identity_and_ci_identity(self):
        async def fetch(payload):
            r = await self.client.post("/token/fictional-private-path", json=payload)
            return jwt.decode((await r.json())["participant_token"], options={"verify_signature": False})["sub"]
        self.assertNotEqual(await fetch({}), await fetch({}))
        self.assertEqual(await fetch({"participant_identity": "action-client"}), "action-client")

    async def test_invalid_body_is_rejected(self):
        for body in ("not json", "[]", "null"):
            r = await self.client.post("/token/fictional-private-path", data=body,
                                       headers={"Content-Type": "application/json"})
            self.assertEqual(r.status, 400)

    async def test_health_returns_no_credentials(self):
        r = await self.client.get("/health")
        self.assertEqual(await r.json(), {"ok": True})
