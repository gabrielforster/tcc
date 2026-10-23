"""Request and response bodies for the scoring API."""

from typing import Any

from pydantic import BaseModel, Field


class ScoreRequest(BaseModel):
    """A batch of receivables to score.

    Each record carries the feature columns the model was trained on. Unknown keys are
    rejected rather than silently ignored, because a typo in a feature name would otherwise
    become a missing value and a quietly wrong score.
    """

    task: str = Field(default="propensity", description="propensity | default")
    receivables: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class ScoredReceivable(BaseModel):
    invoice_id: str | None = None
    customer_id: str | None = None
    probability: float = Field(ge=0.0, le=1.0)
    decision: bool
    priority: int = Field(description="1 is contacted first")


class ScoreResponse(BaseModel):
    task: str
    target: str
    model: str
    source: str
    threshold: float
    scored: int
    results: list[ScoredReceivable]


class ModelInfo(BaseModel):
    task: str
    target: str
    champion: str
    source: str
    threshold: float
    features: list[str]


class HealthResponse(BaseModel):
    status: str
    models: list[str]


# ------------------------------------------------------------------- web chat
class SessionRequest(BaseModel):
    """Open a conversation.

    `token` is minted by the host application's backend with
    `collection.channels.session.issue_token`. Without it the session is anonymous, which
    is a supported mode rather than a degraded one: the knowledge base holds policy, FAQ
    and instalment tables, none of it customer-specific.
    """

    token: str | None = Field(default=None, description="signed identity token, server-issued")


class SessionResponse(BaseModel):
    session_id: str
    greeting: str
    identified: bool = Field(description="false when the visitor is anonymous")
    delivery_id: str | None = Field(default=None, description="delivery id of the greeting")


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    session_id: str
    text: str
    intent: str | None = None
    confidence: float = 0.0
    citations: list[str] = Field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None
    opted_out: bool = False
    latency_ms: float = 0.0
    responder: str = ""
    events: list[str] = Field(default_factory=list)
    delivery_id: str | None = Field(
        default=None, description="id to report entregue/lida against; client-reported"
    )


class AudioMessageResponse(MessageResponse):
    """A voice note's reply: the message fields plus what the agent heard.

    The widget shows `transcript` so the customer can see what was understood, and
    `needs_confirmation` tells it the reply opens with a figure read back for a yes or no.
    """

    transcript: str | None = None
    transcription_confidence: float = 0.0
    needs_confirmation: bool = False


class TranscriptTurn(BaseModel):
    role: str
    text: str
    at: float
    intent: str | None = None


class TranscriptResponse(BaseModel):
    session_id: str
    identified: bool
    escalated: bool
    turns: list[TranscriptTurn]
