"""Conversations: who is talking, for how long, and how fast.

A web widget is a public endpoint on someone else's page, which makes three problems real
that no other part of this system has had to face.

**Identity cannot come from the browser.** If the page could name the customer, anyone
could name any customer and read answers meant for them. So the host application's backend
mints a signed token with `issue_token()` using a shared secret, and the widget presents
it. Without a token the session is anonymous and still works -- the knowledge base is
policy, FAQ and instalment tables, none of it customer-specific, so an anonymous visitor
asking "quais sao os juros por atraso?" gets a correct answer and no one's data.

**A session is a conversation, not a login.** It expires, it has a turn cap, and it holds
the transcript for audit. The cap is not only abuse control: a thread that has run for
forty turns without resolving is a thread that needed a person a long time ago.

**A public endpoint gets hammered.** The rate limit is per session and deliberately
simple.

The store is in-memory. That is a real limitation, stated here rather than discovered in
production: it does not survive a restart and does not span processes. Redis is already in
`docker-compose.yml` for when this needs to, and `SessionStore` is the seam it would
replace.
"""

import hashlib
import hmac
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from collection.config import settings

#: An idle conversation is closed. Long enough to read an answer and reply, short enough
#: that an abandoned tab does not hold a customer id indefinitely.
SESSION_TTL_SECONDS = 30 * 60
#: Turns before the thread is handed to a person. See the module docstring.
MAX_TURNS = 40
#: Messages per minute, per session.
RATE_LIMIT_PER_MINUTE = 20
#: Longest message accepted. Longer is a paste or a probe, not a customer question.
MAX_MESSAGE_CHARS = 1000
#: Anonymous sessions get a stable, non-identifying customer id with this prefix.
ANONYMOUS_PREFIX = "anon_"

_TOKEN_SEPARATOR = "."


class SessionError(RuntimeError):
    """The session cannot continue: expired, exhausted, rate limited or unknown."""


class TokenError(SessionError):
    """The identity token is malformed, expired or not ours."""


class SessionNotFound(SessionError):
    """No such conversation, or it has expired."""


class RateLimited(SessionError):
    """Too many messages from one session in too short a time."""


class InvalidMessage(SessionError):
    """The message is empty or longer than the channel accepts."""


class ConversationEnded(SessionError):
    """The thread hit its turn cap and belongs to a person now."""


def _secret() -> str:
    """The signing secret.

    Reuses `ANONYMIZATION_SALT` rather than introducing a second secret to manage. Both
    are process-wide secrets that must never be committed and whose rotation invalidates
    what came before; splitting them would double the number of things to get wrong for
    no gain in isolation.
    """
    return settings.anonymization_salt


def issue_token(customer_id: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """Mint an identity token for a known customer. Server side only.

    The host application calls this from its own backend, where it already knows who is
    logged in, and hands the result to the widget. The browser never sees the secret and
    cannot mint one.
    """
    if not customer_id or _TOKEN_SEPARATOR in customer_id:
        raise TokenError(f"Invalid customer id for a token: {customer_id!r}")
    expires = int(time.time()) + ttl_seconds
    payload = f"{customer_id}{_TOKEN_SEPARATOR}{expires}"
    signature = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}{_TOKEN_SEPARATOR}{signature}"


def verify_token(token: str) -> str:
    """The customer id a token attests to, or `TokenError`.

    Compared with `compare_digest`: a plain `==` leaks the signature one byte at a time to
    anyone willing to measure, which is the textbook way these get forged.
    """
    try:
        customer_id, expires, signature = token.split(_TOKEN_SEPARATOR)
    except ValueError as error:
        raise TokenError("Malformed token") from error

    payload = f"{customer_id}{_TOKEN_SEPARATOR}{expires}"
    expected = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        raise TokenError("Bad signature")
    if int(expires) < time.time():
        raise TokenError("Token expired")
    return customer_id


@dataclass
class Turn:
    role: str
    text: str
    at: float = field(default_factory=time.time)
    intent: str | None = None
    escalated: bool = False


@dataclass
class Session:
    session_id: str
    customer_id: str
    identified: bool
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    transcript: list[Turn] = field(default_factory=list)
    escalated: bool = False
    #: Delivery id of the greeting, so the widget can report on the first message.
    greeting_delivery_id: str | None = None
    _hits: deque[float] = field(default_factory=deque, repr=False)

    @property
    def turns(self) -> int:
        return sum(1 for turn in self.transcript if turn.role == "customer")

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_seen_at > SESSION_TTL_SECONDS

    def record(self, role: str, text: str, **extra: object) -> Turn:
        turn = Turn(role=role, text=text, **extra)  # type: ignore[arg-type]
        self.transcript.append(turn)
        self.last_seen_at = turn.at
        return turn

    def check_rate(self, now: float | None = None) -> None:
        now = now or time.time()
        while self._hits and now - self._hits[0] > 60:
            self._hits.popleft()
        if len(self._hits) >= RATE_LIMIT_PER_MINUTE:
            raise RateLimited("Too many messages. Wait a moment before sending another.")
        self._hits.append(now)


class SessionStore:
    """In-memory conversations, swept lazily.

    Sweeping on write rather than on a timer keeps the store free of background tasks: a
    store nobody is writing to is a store whose memory nobody is growing.
    """

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def open(self, token: str | None = None) -> Session:
        """Start a conversation, identified by a valid token or anonymous without one."""
        if token:
            customer_id, identified = verify_token(token), True
        else:
            # Random, not derived from anything about the visitor: an anonymous id that
            # could be recomputed from an IP or a fingerprint would not be anonymous.
            customer_id, identified = f"{ANONYMOUS_PREFIX}{secrets.token_hex(8)}", False

        session = Session(
            session_id=secrets.token_urlsafe(24),
            customer_id=customer_id,
            identified=identified,
        )
        with self._lock:
            self._sweep()
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound("Unknown session. Start a new conversation.")
            if session.expired():
                del self._sessions[session_id]
                raise SessionNotFound("This conversation expired. Start a new one.")
        if session.turns >= MAX_TURNS:
            raise ConversationEnded("This conversation is being transferred to a person.")
        return session

    def close(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _sweep(self) -> None:
        now = time.time()
        for session_id in [s for s, v in self._sessions.items() if v.expired(now)]:
            del self._sessions[session_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


def clean_message(raw: str) -> str:
    """What the customer actually typed, or `SessionError`."""
    text = " ".join(str(raw).split())
    if not text:
        raise InvalidMessage("Empty message.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise InvalidMessage(f"Message too long (limit {MAX_MESSAGE_CHARS} characters).")
    return text
