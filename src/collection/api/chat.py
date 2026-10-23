"""HTTP surface for the web chat channel.

Thin by design: it validates, maps channel errors onto status codes, and serves the widget.
Everything else belongs to `collection.channels.web`, which in turn delegates to the
orchestrator. If this file starts making decisions about what to say, the channel boundary
has been lost.

The status codes are chosen for a widget running on someone else's page, where the only
thing distinguishing "try again in a moment" from "start over" is the number it gets back:

* `401` the token did not verify -- the host application must mint a new one;
* `404` the session is unknown or expired -- open a new one and keep the transcript;
* `409` the thread hit its turn cap and a person has it -- stop sending;
* `422` the message was empty or too long -- the widget can say so without a round trip;
* `429` rate limited -- back off, do not reopen the session.

The index is built once at startup. It is the same `VectorIndex` the CLI uses, so the
answers served here are the answers the evaluation measured.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from collection.agents.responsive import Responder, get_responder
from collection.api.schemas import (
    MessageRequest,
    MessageResponse,
    SessionRequest,
    SessionResponse,
    TranscriptResponse,
    TranscriptTurn,
)
from collection.channels.session import (
    ConversationEnded,
    InvalidMessage,
    RateLimited,
    SessionNotFound,
    TokenError,
)
from collection.channels.web import ChatChannel
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_STATUS = {
    TokenError: 401,
    SessionNotFound: 404,
    ConversationEnded: 409,
    InvalidMessage: 422,
    RateLimited: 429,
}


def _http_error(error: Exception) -> HTTPException:
    for kind, status in _STATUS.items():
        if isinstance(error, kind):
            return HTTPException(status_code=status, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


def default_responder() -> Responder:
    """The LLM when a key is configured, the template otherwise.

    The templated answer quotes whole passages, which is correct and unreadable in a chat
    bubble -- a customer asking about interest gets the entire FAQ. So generation is the
    right default *when it is available*, and falling back rather than failing is what
    keeps a fresh clone, CI and the demo page working with no key at all.
    """
    if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
        return get_responder("llm")
    return get_responder("template")


class ChatService:
    """Holds the channel, so the index is built once rather than per request."""

    def __init__(self, channel: ChatChannel | None = None) -> None:
        self._channel = channel

    def load(self) -> None:
        if self._channel is None:
            index = VectorIndex()
            index.add_documents(load_directory(settings.dir_knowledge)).build()
            self._channel = ChatChannel(index=index, responder=default_responder())

    @property
    def channel(self) -> ChatChannel:
        if self._channel is None:
            raise HTTPException(
                status_code=503, detail="Chat is still starting. Try again in a moment."
            )
        return self._channel


@asynccontextmanager
async def chat_lifespan(service: ChatService) -> AsyncIterator[None]:
    service.load()
    yield


def create_router(service: ChatService) -> APIRouter:
    router = APIRouter(prefix="/chat", tags=["chat"])

    @router.post("/sessions", response_model=SessionResponse, status_code=201)
    def open_session(request: SessionRequest) -> SessionResponse:
        try:
            session, greeting = service.channel.open(request.token)
        except Exception as error:
            raise _http_error(error) from error
        return SessionResponse(
            session_id=session.session_id, greeting=greeting, identified=session.identified
        )

    @router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
    def send_message(session_id: str, request: MessageRequest) -> MessageResponse:
        try:
            reply = service.channel.send(session_id, request.text)
        except Exception as error:
            raise _http_error(error) from error
        return MessageResponse(**vars(reply))

    @router.get("/sessions/{session_id}", response_model=TranscriptResponse)
    def transcript(session_id: str) -> TranscriptResponse:
        try:
            session = service.channel.store.get(session_id)
        except Exception as error:
            raise _http_error(error) from error
        return TranscriptResponse(
            session_id=session.session_id,
            identified=session.identified,
            escalated=session.escalated,
            turns=[
                TranscriptTurn(role=turn.role, text=turn.text, at=turn.at, intent=turn.intent)
                for turn in session.transcript
            ],
        )

    @router.delete("/sessions/{session_id}", status_code=204)
    def close_session(session_id: str) -> None:
        service.channel.store.close(session_id)

    @router.get("/widget.js", include_in_schema=False)
    def widget() -> FileResponse:
        return FileResponse(
            WEB_DIR / "widget.js",
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @router.get("/demo", include_in_schema=False, response_class=HTMLResponse)
    def demo() -> HTMLResponse:
        """A host page that embeds the widget, for seeing it work end to end."""
        return HTMLResponse((WEB_DIR / "demo.html").read_text(encoding="utf-8"))

    return router
