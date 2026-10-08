"""Small private TokenSource endpoint; no credentials in URLs except its own key."""
import datetime
import hmac
import os
import uuid

from aiohttp import web
from livekit import api

ROOM = "personal-voice"


def create_app():
    app = web.Application(client_max_size=4096)
    app["endpoint_secret"] = os.environ["TOKEN_ENDPOINT_SECRET"]
    app["public_url"] = os.environ["LIVEKIT_PUBLIC_URL"]
    app["api_key"] = os.environ["LIVEKIT_API_KEY"]
    app["api_secret"] = os.environ["LIVEKIT_API_SECRET"]

    async def health(request):
        return web.json_response({"ok": True})

    async def token(request):
        supplied = request.match_info.get("secret", "")
        if not hmac.compare_digest(supplied, app["endpoint_secret"]):
            return web.Response(status=404, headers={"Cache-Control": "no-store"})
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            return web.json_response({"error": "invalid_request"}, status=400)
        # Keep a single fixed room. Caller-supplied room names never grant access
        # to other rooms; participant identity is opaque, with one diagnostic ID.
        identity = "action-client" if body.get("participant_identity") == "action-client" else "user-" + uuid.uuid4().hex
        jwt = (api.AccessToken(app["api_key"], app["api_secret"])
               .with_identity(identity)
               .with_ttl(datetime.timedelta(minutes=10))
               .with_grants(api.VideoGrants(room_join=True, room=ROOM,
                                           can_publish=True, can_subscribe=True,
                                           can_publish_data=True,
                                           can_publish_sources=["microphone"]))
               .to_jwt())
        return web.json_response({"server_url": app["public_url"], "participant_token": jwt},
                                 headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    app.router.add_get("/health", health)
    app.router.add_post("/token/{secret}", token)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8082, access_log=None, print=None)
