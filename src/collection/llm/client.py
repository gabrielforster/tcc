"""Talking to a language model, behind an interface so the provider stays replaceable.

Three implementations, each with a job:

* `AnthropicClient` is the real one. The SDK is imported lazily and the extra is optional,
  so a fresh clone, CI and every test still run without an API key -- the same discipline
  `SentenceTransformerEmbedder` follows for torch.
* `ScriptedClient` returns canned text. Tests need the responder's *behaviour* (prompt
  assembly, citation parsing, refusal handling), not a network call, and a test that
  depends on a live model is a test that fails for reasons unrelated to the code.
* `EchoClient` quotes the context back. It is the degenerate generator: useful when
  debugging whether a bad answer came from retrieval or from generation.

`LLMError` wraps every provider failure. The responsive agent must be able to fall back to
the template responder when generation fails, and it cannot do that if each provider
raises its own exception type.
"""

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from collection.config import settings


class LLMError(RuntimeError):
    """Generation failed: no key, transport error, rate limit, refusal to answer."""


@dataclass
class Completion:
    text: str
    model: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMClient(ABC):
    name: str
    model: str

    @abstractmethod
    def complete(self, system: str, user: str) -> Completion:
        """Answer `user` under the instructions in `system`."""


class AnthropicClient(LLMClient):
    """Claude, via the official SDK.

    `max_tokens` is deliberately small. A collection reply that runs past a few sentences
    is already a bad reply -- the customer reads it on a phone, in a WhatsApp bubble --
    and a tight budget also caps the cost per contact, which is one of the operational
    indicators the evaluation reports.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 20.0,
    ) -> None:
        self.model = model or settings.llm_model
        self.max_tokens = max_tokens
        # Zero temperature: the same question must not produce a different instalment plan
        # on a second reading, and the evaluation has to be reproducible to be reportable.
        self.temperature = temperature
        self.timeout = timeout
        self._client = None

    def _load(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as error:
                raise LLMError(
                    "The LLM responder needs the anthropic SDK: uv sync --extra llm"
                ) from error
            key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise LLMError(
                    "No ANTHROPIC_API_KEY. Set it in .env or the environment, or run with "
                    "--responder template."
                )
            self._client = Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    def complete(self, system: str, user: str) -> Completion:
        client = self._load()
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except LLMError:
            raise
        except Exception as error:  # the SDK raises a family of transport/API errors
            raise LLMError(f"{type(error).__name__}: {error}") from error
        latency_ms = (time.perf_counter() - started) * 1000
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = getattr(response, "usage", None)
        return Completion(
            text=text.strip(),
            model=self.model,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


@dataclass
class ScriptedClient(LLMClient):
    """Returns prepared replies in order, and records what it was asked.

    The recorded prompts are the point: asserting that the context block actually carried
    the retrieved passages is what proves the responder grounds its prompt, and no live
    model is needed to check it.
    """

    replies: list[str] = field(default_factory=list)
    name: str = "scripted"
    model: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> Completion:
        self.calls.append((system, user))
        if not self.replies:
            raise LLMError("ScriptedClient ran out of replies")
        return Completion(text=self.replies.pop(0), model=self.model, latency_ms=0.0)


class EchoClient(LLMClient):
    """Returns the context verbatim: generation reduced to a pass-through."""

    name = "echo"
    model = "echo"

    def complete(self, system: str, user: str) -> Completion:
        return Completion(text=user, model=self.model, latency_ms=0.0)


CLIENTS: dict[str, type[LLMClient]] = {
    "anthropic": AnthropicClient,
    "echo": EchoClient,
}


def get_client(name: str | None = None) -> LLMClient:
    name = name or settings.llm_provider
    if name not in CLIENTS:
        raise ValueError(f"Unknown LLM provider: {name!r}. Available: {', '.join(CLIENTS)}")
    return CLIENTS[name]()
