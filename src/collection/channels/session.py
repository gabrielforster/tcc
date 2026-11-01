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

`SessionStore` is in-memory. That is a real limitation, stated here rather than discovered
in production: it does not survive a restart and does not span processes. It stays because
the tests and the demo run without a database. `DatabaseSessionStore` is the same contract
on rows, within one tenant: the thread is a `conversations` row, every turn is a `turns`
row, and the session id *is* the conversation id, so a restarted process can be handed one
and find the thread again.

The database store also owns the gate from §6.2 of the spec: a tenant that is not `ACTIVE`
has nothing to answer from, so no conversation opens for it and nothing is scheduled into
it. The check is in the store rather than in the channel because the store is the only
part that knows which tenant it serves.

What does *not* become durable, deliberately: the rate-limit counter and the mailbox. Both
are about the live process -- a limit is per process by the same argument that made it per
session, and a counter read from rows would need a write per message to be worth anything.

The model imports are inside the methods, so importing this module for the in-memory store
does not load the database layer (`channels/delivery.py` does the same, for the same
reason).
"""

import hashlib
import hmac
import secrets
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock, RLock
from typing import TYPE_CHECKING, Any

from collection.crypto import derive_key

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session as SQLSession

    from collection.db.models.conversation import Conversation
    from collection.db.models.conversation import Turn as TurnRow
    from collection.db.models.customer import Customer
    from collection.db.models.tenant import Tenant
    from collection.db.repository import TenantScope

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
#: Fields a caller may record alongside a turn that are evidence rather than transcript:
#: what was retrieved, how sure the classifier was, who wrote the answer and how long it
#: took, and why the thread was handed over. The in-memory transcript is what was
#: displayed, so it keeps none of them.
EVIDENCE_FIELDS = frozenset(
    {"passages", "confidence", "responder", "latency_ms", "escalation_reason"}
)

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


class TenantNotServable(SessionError):
    """The tenant is not `ACTIVE`, so it has nothing to answer from yet (spec 6.2)."""


class UnknownTenant(SessionError):
    """No tenant answers to that key. An identifier, not a secret, so it is simply wrong."""


def _signing_key() -> bytes:
    """The key tokens are signed with.

    Derived from `ANONYMIZATION_SALT` rather than introducing a second secret to manage.
    Both are process-wide secrets that must never be committed and whose rotation
    invalidates what came before; splitting them would double the number of things to get
    wrong for no gain in isolation.

    Derived, not the salt itself: the same secret also stands behind every tenant's
    pseudonyms, and if the two uses shared one key, anyone who could mint pseudonyms could
    mint identity tokens too. `derive_key` makes them independent at the cost of one HMAC.
    """
    return derive_key("session-token")


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
    signature = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
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
    expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
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
    #: The delivery record this turn is tracked by, for agent turns. It is on the turn
    #: rather than only on the reply because the widget draws the thread from the
    #: transcript when a session opens, and a bubble drawn with no delivery id would
    #: silently stop reporting that it was rendered and read.
    delivery_id: str | None = None


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
        """Add a turn to the thread.

        Anything in `EVIDENCE_FIELDS` describes how the answer was produced rather than
        what was displayed, so it is not part of the transcript a widget draws. The
        in-memory session drops it; a durable one writes it to the turn's row.
        """
        shown = {key: value for key, value in extra.items() if key not in EVIDENCE_FIELDS}
        turn = Turn(role=role, text=text, **shown)  # type: ignore[arg-type]
        self.transcript.append(turn)
        self.last_seen_at = turn.at
        return turn

    def amend(self, turn: Turn) -> None:
        """A turn was edited after it was recorded -- its intent, or what was heard.

        Nothing to do for a transcript that is a list: the object the caller edited is the
        record. A durable session carries the edit to the row.
        """

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

    @property
    def servable(self) -> bool:
        """An in-memory store serves no tenant, so there is no lifecycle to be behind."""
        return True

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


def resolve_tenant(tenant_key: str, session: "SQLSession | None" = None) -> "Tenant":
    """The tenant a widget embed's key names, or `UnknownTenant`.

    The key is public -- it travels in a page the tenant does not control -- so it grants
    nothing beyond reaching that tenant's own channel, and being wrong about it is an
    ordinary mistake rather than an attack. An empty key resolves to nobody: a deployment
    with no `default_tenant_key` must fail to serve rather than serve whoever is first.

    `session` is for a caller that already has one (the tests, and anything inside a unit
    of work); without it a platform session is opened, because `tenants` belongs to no
    tenant and no tenant-bound session may read another's row.
    """
    from sqlalchemy import select

    from collection.db.models.tenant import Tenant
    from collection.db.session import unit_of_work

    def found(where: "SQLSession") -> "Tenant":
        tenant = where.scalars(select(Tenant).where(Tenant.tenant_key == tenant_key)).first()
        if tenant is None:
            raise UnknownTenant("No tenant answers to that key.")
        return tenant

    if not tenant_key:
        raise UnknownTenant("No tenant key was given.")
    if session is not None:
        return found(session)
    with unit_of_work(platform=True) as platform:
        return found(platform)


class DatabaseSession(Session):
    """A session whose transcript is rows.

    The channel drives this exactly as it drives the in-memory one -- it must not learn
    that there is a database -- so the writing happens on the two methods it already
    calls: `record` for a new turn and `amend` for an edit made to one afterwards. An
    edit is a normal part of a turn here: the intent is only known once the agent has
    run, and a voice note's text is only known once it has been heard.
    """

    def __init__(
        self, store: "DatabaseSessionStore", conversation: "Conversation", **fields: Any
    ) -> None:
        super().__init__(**fields)
        self.store = store
        self.conversation = conversation
        #: In-memory turn -> the row holding it, keyed by identity. The transcript holds
        #: every turn for as long as the session does, so an id cannot be reused.
        self.rows: dict[int, TurnRow] = {}

    def record(self, role: str, text: str, **extra: object) -> Turn:
        turn = super().record(role, text, **extra)
        self.rows[id(turn)] = self.store.write_turn(self, turn, **extra)
        return turn

    def amend(self, turn: Turn) -> None:
        self.store.amend_turn(self, turn)

    def restore(self, turn: Turn, row: "TurnRow") -> None:
        """Put a turn read back from the record into the transcript, without rewriting it."""
        self.transcript.append(turn)
        self.rows[id(turn)] = row


class DatabaseSessionStore:
    """`SessionStore`'s contract on rows, within one tenant.

    **Transactions.** Every method here is one short unit of work: it commits before it
    returns, and rolls back if it raises. Short is not tidiness. `DatabaseDeliveryLog`
    takes a `SELECT ... FOR UPDATE` on the row it advances and holds it until the
    transaction ends, so a unit of work that spanned the orchestrator -- and therefore an
    LLM call or a voice transcription -- would block every other writer to that delivery
    for as long as inference takes. Nothing in this module opens a transaction that
    outlives a single call, which is what makes that impossible rather than merely
    discouraged. The cost, stated: a turn and the delivery that tracks it are committed
    separately, so a process killed between them leaves a turn whose message was never
    recorded as sent.

    **Threads.** A SQLAlchemy session is not thread-safe and FastAPI runs synchronous
    endpoints in a thread pool, so one lock serialises every unit of work on the scope.
    It is never held across the orchestrator, for the same reason the transaction is not.
    """

    def __init__(
        self,
        scope: "TenantScope",
        channel: str = "chat",
        ttl_seconds: int = SESSION_TTL_SECONDS,
    ) -> None:
        self._scope = scope
        self._channel = channel
        self.ttl_seconds = ttl_seconds
        #: Live sessions, so the rate-limit counter and the thread survive between
        #: requests without a read. A session missing from here is read back from rows.
        self._sessions: dict[str, DatabaseSession] = {}
        #: Reentrant: a unit of work may call a helper that opens one of its own.
        self.lock = RLock()
        #: How many `unit()` blocks are open. Only the outermost ends the transaction.
        self._depth = 0
        #: In-memory changes that may only be made once the writes they go with are
        #: committed. Run after the outermost commit, dropped on a rollback.
        self._on_commit: list[Callable[[], None]] = []

    # ------------------------------------------------------------ units of work
    @contextmanager
    def unit(self) -> "Iterator[TenantScope]":
        """One short unit of work on this tenant's scope. See the class docstring.

        A nested `with` joins the one already open instead of ending it. Without that the
        inner block's commit would split the outer one in two -- `open()` would check that
        the tenant is servable in one transaction and insert the conversation in another,
        so a tenant suspended between them would still get a conversation, and a failure
        after the inner block would leave what it wrote behind. The lock makes the nesting
        safe; the counter makes it invisible.
        """
        with self.lock:
            self._depth += 1
            try:
                yield self._scope
                if self._depth == 1:
                    self._scope.session.commit()
                    pending, self._on_commit = self._on_commit, []
                    for action in pending:
                        action()
            except Exception:
                if self._depth == 1:
                    self._scope.session.rollback()
                    self._on_commit.clear()
                raise
            finally:
                self._depth -= 1

    # ------------------------------------------------------------------ the gate
    @property
    def servable(self) -> bool:
        """Whether this tenant may open a conversation or be scheduled into (spec 6.2).

        Read every time rather than cached, so a tenant suspended while the process is
        running is refused on the next `open()` or `seed()` rather than on the next
        restart. It is not asked again during a conversation: the spec puts the gate at
        session open, so a thread already in progress runs to its TTL. Suspending a tenant
        stops new conversations, not the sentence someone is in the middle of.
        """
        with self.unit() as scope:
            return self._tenant(scope).servable

    def _tenant(self, scope: "TenantScope") -> "Tenant":
        from collection.db.models.tenant import Tenant

        # `tenants` is not tenant-scoped, so a tenant-bound session may read its own row;
        # `populate_existing` because the identity map would otherwise answer with the
        # state this process saw first.
        tenant = scope.session.get(Tenant, scope.tenant_id, populate_existing=True)
        if tenant is None:
            raise UnknownTenant("This channel serves a tenant that no longer exists.")
        return tenant

    # ------------------------------------------------------------------ sessions
    def open(self, token: str | None = None) -> DatabaseSession:
        """Start a conversation; refuse one for a tenant that is not servable.

        A new thread every time, as `SessionStore.open` does. Resuming the customer's last
        one would put the greeting -- shown once, at the top -- in the middle of a restored
        transcript, feed the whole of it back to the agent, and count the turn cap across
        reopenings, so a returning visitor would be handed to a person sooner than a new
        one for having come back.
        """
        if token:
            customer_id, identified = verify_token(token), True
        else:
            customer_id, identified = f"{ANONYMOUS_PREFIX}{secrets.token_hex(8)}", False

        with self.unit() as scope:
            if not self._tenant(scope).servable:
                raise TenantNotServable("This channel is not open for conversations yet.")
            self._sweep()
            conversation = self._new_conversation(scope, customer_id)
            session = DatabaseSession(
                store=self,
                conversation=conversation,
                session_id=str(conversation.id),
                customer_id=customer_id,
                identified=identified,
            )
            self._read_transcript(session)
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> DatabaseSession:
        expired = False
        with self.unit():
            session = self._sessions.get(session_id)
            if session is None:
                session = self._read_session(session_id)
            if session.expired():
                # Closed and committed, then refused: an expired thread that stayed open
                # would be resumed by the next `open()` for the same customer.
                self._close(session.conversation)
                self._sessions.pop(session_id, None)
                expired = True
            else:
                self._sessions[session_id] = session
        if expired:
            raise SessionNotFound("This conversation expired. Start a new one.")
        if session.turns >= MAX_TURNS:
            raise ConversationEnded("This conversation is being transferred to a person.")
        return session

    def close(self, session_id: str) -> None:
        with self.unit():
            session = self._sessions.pop(session_id, None)
            conversation = (
                session.conversation if session is not None else self._conversation(session_id)
            )
            if conversation is not None:
                self._close(conversation)

    def __len__(self) -> int:
        with self.lock:
            return len(self._sessions)

    # ------------------------------------------------------------------- writing
    def write_turn(self, session: DatabaseSession, turn: Turn, **extra: object) -> "TurnRow":
        """Write a turn and, in the same savepoint, every passage it was answered from."""
        from collection.db.models.conversation import ConversationState
        from collection.db.transcripts import record_turn

        with self.unit() as scope:
            row = record_turn(
                scope,
                session.conversation,
                role=turn.role,
                text=turn.text,
                passages=list(extra.get("passages") or ()),  # type: ignore[arg-type]
                intent=_intent(turn.intent),
                confidence=extra.get("confidence"),
                responder=extra.get("responder") or None,
                latency_ms=_milliseconds(extra.get("latency_ms")),
            )
            if turn.escalated:
                # The thread is a person's now; the record has to say so, and why.
                session.conversation.state = ConversationState.ESCALATED
                session.conversation.escalation_reason = extra.get("escalation_reason")  # type: ignore[assignment]
            self._link_delivery(scope, turn, row)
            scope.session.flush()
        return row

    def amend_turn(self, session: DatabaseSession, turn: Turn) -> None:
        """Carry a retroactive edit -- the intent, the transcript, the delivery -- to the row."""
        with self.unit() as scope:
            row = session.rows.get(id(turn))
            if row is None:
                return
            row.text = turn.text
            row.intent = _intent(turn.intent)
            self._link_delivery(scope, turn, row)
            scope.session.flush()

    def conversation_for(self, customer_id: str) -> "Conversation":
        """The thread an outbound message for this customer belongs to.

        Not a session: this is the seeding path, where a contact is recorded before
        anyone has opened a widget. A message seeded while a thread is live belongs in
        that thread, so an open and unexpired one is reused; otherwise a thread is
        started to hold it, and whichever session finally shows the message takes the
        delivery over (`DatabaseDeliveryLog.reassign`). An open thread that has gone past
        the TTL is closed here rather than reused -- the sweep the in-memory store does
        on write.
        """
        from collection.db.models.conversation import Conversation, ConversationState

        with self.unit() as scope:
            customer = self._customer(scope, customer_id)
            open_threads = scope.all(
                Conversation,
                Conversation.customer_id == customer.id,
                Conversation.state == ConversationState.OPEN,
            )
            latest = max(open_threads, key=lambda row: row.opened_at, default=None)
            if latest is not None:
                if self._last_seen(scope, latest) + self.ttl_seconds >= time.time():
                    return latest
                self._close(latest)
            return self._new_conversation(scope, customer_id)

    def _new_conversation(self, scope: "TenantScope", customer_id: str) -> "Conversation":
        from collection.db.models.conversation import Conversation

        customer = self._customer(scope, customer_id)
        conversation = Conversation(customer_id=customer.id, channel=self._channel)
        scope.add(conversation)
        scope.session.flush()
        return conversation

    def conversation_of(self, owner: str) -> "Conversation":
        """The conversation an outbound message owned by `owner` belongs to.

        The in-memory log takes an opaque owner string and a row needs a conversation.
        `owner` is a session id for a message shown in a thread and a customer id for one
        seeded before any thread existed, and the two are told apart by shape: a session
        id is a conversation's own uuid.
        """
        with self.lock:
            session = self._sessions.get(owner)
            if session is not None:
                return session.conversation
            conversation = self._conversation(owner)
            if conversation is not None:
                return conversation
            return self.conversation_for(owner)

    # ------------------------------------------------------------------ internals
    def _customer(self, scope: "TenantScope", external_id: str) -> "Customer":
        """This tenant's customer, created if the conversation is the first we hear of them.

        An anonymous visitor is a party to a conversation and the schema requires one, so
        they get a row too, under the `anon_` id the store minted. The prefix is how a
        reader tells a visitor from an ingested debtor.
        """
        from collection.data.anonymize import pseudonym
        from collection.db.models.customer import Customer

        existing = scope.all(Customer, Customer.external_id == external_id)
        if existing:
            return existing[0]
        customer = Customer(
            external_id=external_id,
            pseudonym=pseudonym(external_id, "cus_", tenant=scope.tenant_id),
        )
        scope.add(customer)
        scope.session.flush()
        return customer

    def _conversation(self, session_id: str) -> "Conversation | None":
        from collection.db.models.conversation import Conversation

        try:
            key = uuid.UUID(session_id)
        except (ValueError, AttributeError, TypeError):
            return None
        return self._scope.get(Conversation, key)

    def _turns(self, scope: "TenantScope", conversation: "Conversation") -> "list[TurnRow]":
        from collection.db.models.conversation import Turn as TurnRow

        rows = scope.all(TurnRow, TurnRow.conversation_id == conversation.id)
        return sorted(rows, key=lambda row: row.created_at)

    def _last_seen(self, scope: "TenantScope", conversation: "Conversation") -> float:
        rows = self._turns(scope, conversation)
        return (rows[-1].created_at if rows else conversation.opened_at).timestamp()

    def _read_session(self, session_id: str) -> DatabaseSession:
        """Build a live session from the rows of a conversation this process never opened."""
        from collection.db.models.conversation import ConversationState
        from collection.db.models.customer import Customer

        conversation = self._conversation(session_id)
        if conversation is None or conversation.state is ConversationState.CLOSED:
            raise SessionNotFound("Unknown session. Start a new conversation.")
        customer = self._scope.get(Customer, conversation.customer_id)
        external_id = customer.external_id if customer is not None else ""
        session = DatabaseSession(
            store=self,
            conversation=conversation,
            session_id=str(conversation.id),
            customer_id=external_id,
            identified=not external_id.startswith(ANONYMOUS_PREFIX),
        )
        self._read_transcript(session)
        return session

    def _read_transcript(self, session: DatabaseSession) -> None:
        from collection.db.models.conversation import ConversationState
        from collection.db.models.delivery import Delivery

        rows = self._turns(self._scope, session.conversation)
        deliveries = {
            row.turn_id: str(row.id)
            for row in self._scope.all(
                Delivery, Delivery.conversation_id == session.conversation.id
            )
            if row.turn_id is not None
        }
        for row in rows:
            turn = Turn(
                role=row.role,
                text=row.text,
                at=row.created_at.timestamp(),
                intent=row.intent.value if row.intent is not None else None,
                delivery_id=deliveries.get(row.id),
            )
            session.restore(turn, row)
        if rows:
            session.last_seen_at = rows[-1].created_at.timestamp()
        session.escalated = session.conversation.state is ConversationState.ESCALATED

    def _link_delivery(self, scope: "TenantScope", turn: Turn, row: "TurnRow") -> None:
        """Point the delivery tracking this turn at it, once both exist.

        The two are written in either order -- the greeting is recorded as sent before the
        turn exists, an answer after -- so the link is made from the side that knows the
        turn, whenever the channel puts a delivery id on it. An id this tenant never
        issued, or one from the in-memory log, is left alone rather than guessed at.
        """
        from collection.db.models.delivery import Delivery

        if not turn.delivery_id:
            return
        try:
            key = uuid.UUID(turn.delivery_id)
        except (ValueError, AttributeError, TypeError):
            return
        delivery = scope.get(Delivery, key)
        if delivery is not None:
            delivery.turn_id = row.id

    def _sweep(self) -> None:
        """Drop the live sessions that have expired and close their threads.

        On write, like `SessionStore._sweep`, and for the same reason: a store nobody is
        writing to is a store whose memory nobody is growing. It matters more here,
        because an abandoned session holds its whole transcript *and* leaves a
        `conversations` row open for the life of the process -- a visitor who opens the
        widget and walks away would otherwise never be closed by anything.

        Call it inside a unit of work: it writes.

        The live sessions go only once those writes are committed. Dropping them here
        would part company with the rows if the unit of work later rolled back -- `open()`
        sweeps before it inserts, so a refused insert would leave those conversations
        `OPEN` in the record with nothing in the process tracking them.
        """
        now = time.time()
        expired = [key for key, s in self._sessions.items() if s.expired(now)]
        for session_id in expired:
            self._close(self._sessions[session_id].conversation)
        if not expired:
            return

        def forget() -> None:
            for session_id in expired:
                self._sessions.pop(session_id, None)

        self._on_commit.append(forget)

    def _close(self, conversation: "Conversation") -> None:
        from collection.db.models.conversation import ConversationState

        if conversation.state is ConversationState.OPEN:
            conversation.state = ConversationState.CLOSED
        conversation.closed_at = datetime.now(UTC)


def _intent(value: object) -> object:
    """The turn's intent as the column's enum, or `None` for one it does not name."""
    from collection.domain.intent import Intent

    try:
        return Intent(value) if value else None
    except ValueError:
        return None


def _milliseconds(value: object) -> int | None:
    return round(float(value)) if isinstance(value, int | float) else None


def clean_message(raw: str) -> str:
    """What the customer actually typed, or `SessionError`."""
    text = " ".join(str(raw).split())
    if not text:
        raise InvalidMessage("Empty message.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise InvalidMessage(f"Message too long (limit {MAX_MESSAGE_CHARS} characters).")
    return text
