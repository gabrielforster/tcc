"""HTTP surface for the voice call: the page, its network check, a join token, the record.

A separate app from the chat's, on its own port, because it loads what the chat never
needs -- Whisper and a voice model, about a gigabyte between them -- and a deployment that
serves only the chat should not pay for that at startup.

`POST /call/start` is the whole signalling story: it names a fresh room, mints a token for
the browser, and starts an agent that joins the same room. Audio never passes through this
process's HTTP side; it goes browser -> LiveKit -> the agent's WebRTC connection.

Before *Ligar*, the page checks that the visitor's network can carry the call at all:
browsers cannot open a raw UDP socket, so the check is LiveKit's own WebRTC connection
test against our server, then the TURN/TLS relay on 443 if the direct path failed. A
network that blocks both gets a plain warning and the chat instead of a call that rings
into silence. `POST /call/check` hands the page a token for that, and the page reports
the outcome back, as one log line the observability stack can count.
"""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

from collection.agents.responsive import get_responder
from collection.api import pages
from collection.channels.livekit_call import CallAgent, new_room_name, room_token
from collection.channels.voice_call import VoiceCallChannel
from collection.config import settings
from collection.rag.documents import load_customer_facing
from collection.rag.index import VectorIndex
from collection.voice.speak import PiperSynthesizer, Synthesizer
from collection.voice.transcribe import WhisperTranscriber

logger = logging.getLogger("collection.call")


class StartRequest(BaseModel):
    #: The same signed customer token the chat accepts; omitted for an anonymous call.
    token: str | None = None


class StartResponse(BaseModel):
    url: str
    room: str
    token: str


class CheckResponse(BaseModel):
    url: str
    token: str
    #: Whether a TURN/TLS fallback exists to be tried when the direct path fails.
    turn: bool


class CheckReport(BaseModel):
    #: `direct_ok`: the direct WebRTC path connects -- UDP, which LiveKit prefers, or its
    #: ICE/TCP port when only that is open; LiveKit's check does not say which, and both
    #: carry the call without a relay. `turn_tls_ok`: only the TURN relay on 443 does.
    #: `blocked`: neither. `signal_failed`: the signalling WebSocket itself did not open,
    #: so the media paths were never tried. `unavailable`: the check could not run at all
    #: (the SDK did not load, or no check token).
    outcome: Literal["direct_ok", "turn_tls_ok", "blocked", "signal_failed", "unavailable"]


def build_channel(whisper_model: str = "small") -> VoiceCallChannel:
    """The channel the call server runs: the same index and responder as the chat demo."""
    # Customer-facing only: an in-memory index has no audience filter, so it is never
    # given the internal documents to begin with.
    index = VectorIndex()
    index.add_documents(load_customer_facing(settings.dir_knowledge)).build()
    transcriber = WhisperTranscriber(model=whisper_model)
    # Loaded now rather than on the first turn, so the first caller does not wait for it.
    transcriber._load()
    return VoiceCallChannel(
        retriever=index, responder=get_responder("template"), transcriber=transcriber
    )


def create_call_app(
    channel: VoiceCallChannel | None = None,
    synthesizer: Synthesizer | None = None,
    whisper_model: str = "small",
) -> FastAPI:
    state: dict[str, object] = {"channel": channel, "synthesizer": synthesizer, "tasks": set()}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state["channel"] is None:
            state["channel"] = await asyncio.to_thread(build_channel, whisper_model)
        if state["synthesizer"] is None:
            voice = PiperSynthesizer()
            await asyncio.to_thread(voice.warm)
            state["synthesizer"] = voice
        yield
        for task in list(state["tasks"]):  # type: ignore[union-attr]
            task.cancel()

    app = FastAPI(title="Voice call", lifespan=lifespan)

    @app.get("/app/call", response_class=HTMLResponse)
    def page(request: Request) -> Response:
        if redirect := pages.gate(request):
            return redirect
        return HTMLResponse(pages.render("call.html"))

    @app.get("/call/demo", include_in_schema=False)
    def demo() -> RedirectResponse:
        """The page moved to `/app/call`; old links still point here."""
        return RedirectResponse("/app/call", status_code=301)

    @app.post("/call/check", response_model=CheckResponse, status_code=201)
    def check() -> CheckResponse:
        """A token for the page's network check: a throwaway room, and no agent in it.

        The check has to reach the same LiveKit the call will, with a real token, or it
        tests a path the call never takes. No agent is started -- the check carries no
        audio, and an agent waiting in an empty room would cost a Whisper slot for nothing.
        """
        room = f"check-{secrets.token_hex(6)}"
        return CheckResponse(
            url=settings.livekit_url,
            token=room_token(room, room),
            turn=bool(settings.turn_domain),
        )

    @app.post("/call/check/result", status_code=204)
    def check_result(report: CheckReport) -> None:
        """Where the page reports what the check found, for the observability stack.

        One log line with a stable event name and nothing but the outcome: what a
        dashboard counts is how many visitors could not have called, and why. No address,
        no candidate list -- an IP is personal data and the count does not need it.
        """
        logger.info("call_network_check outcome=%s", report.outcome)

    @app.get("/health")
    def health() -> dict[str, str]:
        # Answered only once the lifespan has loaded Whisper and the voice, because uvicorn
        # accepts no request before then: a container that answers here can take a call.
        return {"status": "ok", "version": settings.app_version}

    @app.post("/call/start", response_model=StartResponse, status_code=201)
    async def start(request: StartRequest) -> StartResponse:
        room = new_room_name()
        agent = CallAgent(
            channel=state["channel"],  # type: ignore[arg-type]
            synthesizer=state["synthesizer"],  # type: ignore[arg-type]
            room=room,
            customer_token=request.token,
        )
        task = asyncio.create_task(agent.run())
        tasks: set = state["tasks"]  # type: ignore[assignment]
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return StartResponse(
            url=settings.livekit_url, room=room, token=room_token(f"customer-{room}", room)
        )

    @app.get("/call/records/{call_id}")
    def record(call_id: str) -> dict[str, object]:
        channel: VoiceCallChannel = state["channel"]  # type: ignore[assignment]
        if call_id not in channel.calls:
            raise HTTPException(status_code=404, detail="Unknown call.")
        return channel.record(call_id)

    # Local runs only. In production every path below is routed to the API by Caddy before
    # it reaches this server; locally the API is on another port, and the call page's
    # links (back to `/`, over to the chat) must not land on a 404.
    @app.get("/", include_in_schema=False)
    @app.get("/app/chat", include_in_schema=False)
    @app.get("/app/fila", include_in_schema=False)
    def elsewhere(request: Request) -> RedirectResponse:
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(
            f"{settings.api_public_url}{request.url.path}{query}", status_code=307
        )

    return app
