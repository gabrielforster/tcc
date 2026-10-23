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
  also a voice note the decoder refused (not audio, too short, too long);
* `429` rate limited -- back off, do not reopen the session.

The index is built once at startup. It is the same `VectorIndex` the CLI uses, so the
answers served here are the answers the evaluation measured.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from collection.agents.responsive import Responder, get_responder
from collection.api.schemas import (
    AudioMessageResponse,
    DeliveryReport,
    DeliveryResponse,
    MessageRequest,
    MessageResponse,
    SessionRequest,
    SessionResponse,
    TranscriptResponse,
    TranscriptTurn,
)
from collection.channels.delivery import BackwardsStatus, DeliveryStatus
from collection.channels.outbound import SEED_DEMO_CUSTOMER
from collection.channels.session import (
    ConversationEnded,
    InvalidMessage,
    RateLimited,
    SessionNotFound,
    TokenError,
    issue_token,
)
from collection.channels.web import ChatChannel
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex
from collection.voice.audio import AudioError

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_STATUS = {
    TokenError: 401,
    SessionNotFound: 404,
    ConversationEnded: 409,
    InvalidMessage: 422,
    RateLimited: 429,
    # A clip the decoder refused is a request the caller can fix, like an empty message.
    # `TranscriptionError` is deliberately absent: the agent turns it into an escalation,
    # so it never reaches this layer.
    AudioError: 422,
}

#: Upload ceiling. A two-minute OPUS note is well under a megabyte; this only stops a
#: caller streaming something enormous into a temp file before the length check runs.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


def _read_capped(file: UploadFile) -> bytes:
    """Read the upload in chunks, refusing with 413 as soon as it passes the ceiling."""
    chunks: list[bytes] = []
    total = 0
    while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail=f"upload is larger than {MAX_UPLOAD_BYTES} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
            session_id=session.session_id,
            greeting=greeting,
            identified=session.identified,
            delivery_id=session.greeting_delivery_id,
        )

    @router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
    def send_message(session_id: str, request: MessageRequest) -> MessageResponse:
        try:
            reply = service.channel.send(session_id, request.text)
        except Exception as error:
            raise _http_error(error) from error
        return MessageResponse(**vars(reply))

    @router.post("/sessions/{session_id}/audio", response_model=AudioMessageResponse)
    def send_audio(session_id: str, file: UploadFile) -> AudioMessageResponse:
        """Accept a voice note.

        The client's file name is kept, directories stripped: `audio.accept` judges the
        container by its suffix, and the scripted recogniser keys its transcripts on the stem.
        """
        name = Path(file.filename or "").name or "upload"
        try:
            # Resolve the session and spend a rate-limit hit *before* touching the body.
            # Session creation needs no auth, so anything read first is reachable by a
            # caller who invented the id and is not yet limited.
            session = service.channel.store.get(session_id)
            session.check_rate()
            data = _read_capped(file)
            with tempfile.TemporaryDirectory(prefix="voice-") as directory:
                path = Path(directory) / name
                path.write_bytes(data)
                reply = service.channel.send_audio(session_id, path, rate_checked=True)
        # Only the errors this layer knows how to explain. Anything else -- an OSError
        # writing the temp file, a bug in rendering -- is ours, not the caller's, and
        # should surface as a 500 rather than a tidy 400.
        except tuple(_STATUS) as error:
            raise _http_error(error) from error
        return AudioMessageResponse(**vars(reply))

    @router.post("/sessions/{session_id}/deliveries/{delivery_id}", response_model=DeliveryResponse)
    def report_delivery(
        session_id: str, delivery_id: str, report: DeliveryReport
    ) -> DeliveryResponse:
        """The widget reporting that it rendered (`entregue`) or showed (`lida`) a message.

        Client-reported, so it is a claim and not a receipt; see `channels.delivery`.

        A report that lands after the customer already replied finds the record at
        `respondida`. That is an ordinary race (the reply and the report travel
        independently), so it answers 200 with `applied: false` and the status on record.
        Any other step backwards, such as `entregue` after `lida`, contradicts the record and
        is a 409. An unknown delivery id, or one that belongs to another session, is a 404.
        """
        try:
            service.channel.store.get(session_id)
        except Exception as error:
            raise _http_error(error) from error
        status = DeliveryStatus(report.status)
        try:
            owner = service.channel.delivery.of(delivery_id).owner
            # Another session's message is reported as absent, not forbidden: the caller
            # learns nothing about ids that are not theirs. `None` is unowned and allowed.
            if owner is not None and owner != session_id:
                raise KeyError(delivery_id)
            record = service.channel.delivery.advance(delivery_id, status)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown delivery id") from None
        except BackwardsStatus as late:
            if late.current is DeliveryStatus.RESPONDIDA:
                return DeliveryResponse(
                    delivery_id=delivery_id, status=late.current.value, applied=False
                )
            raise HTTPException(status_code=409, detail=str(late)) from late
        return DeliveryResponse(delivery_id=delivery_id, status=record.status.value, applied=True)

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
                TranscriptTurn(
                    role=turn.role,
                    text=turn.text,
                    at=turn.at,
                    intent=turn.intent,
                    delivery_id=turn.delivery_id,
                )
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
        html = (WEB_DIR / "demo.html").read_text(encoding="utf-8")
        if settings.chat_seed_demo:
            # A seeded message is addressed to a customer, and this page is otherwise
            # anonymous -- an anonymous session gets a random id and so an empty mailbox.
            # In demo mode the page is therefore served carrying a token for the fixture
            # customer whose contact was seeded at startup. The rule still holds: the
            # token is minted on the server, and the browser cannot mint one itself.
            html = html.replace(
                'data-api=""',
                f'data-api="" data-token="{issue_token(SEED_DEMO_CUSTOMER)}"',
                1,
            )
        return HTMLResponse(html)

    return router
