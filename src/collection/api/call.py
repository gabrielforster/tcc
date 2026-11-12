"""HTTP surface for the voice call: the page, a join token, and the call record.

A separate app from the chat's, on its own port, because it loads what the chat never
needs -- Whisper and a voice model, about a gigabyte between them -- and a deployment that
serves only the chat should not pay for that at startup.

`POST /call/start` is the whole signalling story: it names a fresh room, mints a token for
the browser, and starts an agent that joins the same room. Audio never passes through this
process's HTTP side; it goes browser -> LiveKit -> the agent's WebRTC connection.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from collection.agents.responsive import get_responder
from collection.channels.livekit_call import CallAgent, new_room_name, room_token
from collection.channels.voice_call import VoiceCallChannel
from collection.config import settings
from collection.rag.documents import load_customer_facing
from collection.rag.index import VectorIndex
from collection.voice.speak import PiperSynthesizer, Synthesizer
from collection.voice.transcribe import WhisperTranscriber

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class StartRequest(BaseModel):
    #: The same signed customer token the chat accepts; omitted for an anonymous call.
    token: str | None = None


class StartResponse(BaseModel):
    url: str
    room: str
    token: str


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

    @app.get("/call/demo", response_class=HTMLResponse)
    def demo() -> str:
        return (WEB_DIR / "call.html").read_text(encoding="utf-8")

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

    return app
