# Channels: the seam between what the system decides and how it reaches someone

An agent decides *what* to say. The orchestrator decides *whether* anyone may be contacted
at all. Neither is supposed to know whether the words end up in a chat bubble, a voice
note or a synthesised sentence on a call. That claim is the architecture's central one —
it is what makes "add WhatsApp" a new adapter rather than a second copy of the
conversation logic — and a convention that nothing checks erodes the first time someone is
in a hurry. So it is not left as a convention.

So the seam is written down in `channels/base.py` and tested in `tests/test_channels.py`.

## What a channel owns, and what it must never decide

A channel owns the things only it can know:

- **Transport.** HTTP request and response for the chat channel; a WebRTC room for the
  voice channel when it lands.
- **Identity.** Only the channel knows whether the browser proved who it is. The chat
  channel mints and verifies signed tokens; anonymous sessions are a supported mode.
- **The thread.** HTTP has none, so the channel keeps the session, the turn cap and the
  rate limit.
- **Translation in both directions.** The orchestrator speaks in events; a widget speaks
  in messages. Turning one into the other is the adapter's whole job.
- **Refusing a payload before anything is spent on it.** A clip that is not audio, or is
  0.2 s long, is a bad request the caller can fix — not a conversation a person should
  pick up.

And a channel must never decide:

- **What to say.** The moment a channel composes or edits a message, the architecture
  section of the article is fiction.
- **Whether a customer may be contacted.** Opt-out is the orchestrator's rule. A channel
  that re-implements it can come to disagree with it, and the version that disagrees is
  the one running in production.
- **What the customer said.** The chat channel does *not* transcribe. It emits
  `audio_received` with a path and lets `ResponsiveAgent` run Whisper, because three
  decisions hang off the `Transcription` object — the `MIN_CONFIDENCE` escalation, the
  read-back of a spoken figure, and the transcript fields on `reply_sent`. A channel that
  recognised speech and handed over plain text would switch all three off silently.

The one place this rule bends is `_Mailer` in `channels/web.py`, a channel-side agent that
exists precisely so that seeding a proactive message goes *through* an orchestrator
instead of around it. It bends the rule in the direction of the rule.

### The test, not the promise

`test_no_agent_or_orchestrator_imports_a_channel` parses every module under
`collection.agents` with `ast` and fails if any of them imports `collection.channels` by
any spelling — `import`, `from`, aliased, or nested inside a function. A grep would have
been shorter and would have matched this paragraph.

The guard is itself guarded: `test_the_scanner_flags_every_way_of_importing_a_channel`
feeds the scanner each spelling and asserts it fires, and a companion test feeds it prose
and unrelated imports and asserts it stays quiet. A test that cannot be shown to fail is
not evidence, which is the same discipline the voice CI gate needed.

## The contract

`Channel` is a `Protocol`, not a base class. A channel is defined by what it can do, not
by what it inherits — and `ChatChannel` existed before the contract did and satisfies it
structurally, so imposing an ABC would have added coupling and bought nothing.
`runtime_checkable` only verifies that the members exist, which makes it a guard against
forgetting one rather than a type check.

`ChannelCapabilities` is data, so callers ask *"can this channel take audio?"* rather than
*"is this the voice channel?"*. The difference matters the day a third channel appears.

| Capability | `chat` | Why a caller asks |
|---|---|---|
| `accepts_audio` | yes | Whether to offer the microphone at all |
| `accepts_text` | yes | A voice call accepts none |
| `shows_delivery_status` | yes | Whether a status ladder means anything here |
| `realtime` | no | Whether the customer is present and waiting, or reading later |

Chat is **store-and-forward**: the widget picks a message up when it next opens or polls.
`realtime=False` is what makes the seeded-thread behaviour below honest rather than a
deficiency to be explained away.

## Why the delivery vocabulary is not the data vocabulary

The system's channel labels are `chat`, `voice`, `sms`, `email` (`DELIVERY_CHANNELS`).
The ERP's contact records and `customers.preferred_channel` still say `whatsapp`, `phone`,
`email`, `sms`. Those two lists overlap, and conflating them would be a mistake worth
naming.

`whatsapp` in the data dictionary is a **fact about a customer**: a real number on a real
app, a preference they declared, a history of contacts that genuinely went out that way.
That column is extracted from the ERP, anonymised, and fed to the predictive model as a
feature. `chat` in `CHANNEL_BY_STAGE` is a **decision this system takes**: which of its
own transports it intends to use for the next contact.

Renaming the data to match the system would corrupt the historical record — a contact that
happened on WhatsApp in 2024 did not happen on "chat", and a model trained on a relabelled
history would be learning from an edit. Renaming the system to match the data would be
worse: the code would claim a WhatsApp integration this project does not have, in exactly
the place a reader goes to check what it does have.

So the two vocabularies stay apart on purpose, and `docs/data-dictionary.md` is deliberately
untouched by the rename. When a WhatsApp adapter is eventually written, it joins
`DELIVERY_CHANNELS` as its own label and the mapping between the two becomes a one-line
table — which is the normal shape of this problem, not a workaround for it.

## Delivery status, and exactly what it proves

The schedule tracks every outbound message as `enviada / entregue / lida / respondida`.
Those four states were supposed to come from the WhatsApp Business API, whose webhooks
carry carrier receipts. They are modelled here instead, and the difference is not cosmetic:

| Rung | Who says so | What it proves |
|---|---|---|
| `enviada` | the server | The message was handed to the channel and recorded. Nothing about anyone seeing it. |
| `entregue` | **the client** | The widget rendered the bubble, and said so. |
| `lida` | **the client** | The bubble was on screen (`IntersectionObserver`, plus `visibilitychange`), and the widget said so. |
| `respondida` | the server | The customer's next turn arrived in the same thread. |

**`entregue` and `lida` are client-reported claims, not receipts.** A browser can lie, be
scripted, or simply never report — a closed tab sends nothing. So `enviada` does not mean
"delivery failed" and a missing `lida` does not mean "unread": both mean *we were not
told*. Neither state should be cited as evidence of notice, in the article or anywhere
else.

Nothing guesses them on a timer, either. A simulated receipt would be a number that looks
measured and is not, and it would have been the easier thing to build. The widget moves a
tick only after the server has answered a report (`widget.js`, "a tick nobody reported
would be a made-up receipt").

**`respondida` is the only rung the server observes first-hand**, and it is therefore the
strongest signal in the ladder — a reply is evidence the message was seen even when the
client never reported anything. It still says the customer answered *something*, not that
they understood or agreed.

Against a real WhatsApp receipt this is weaker in one specific way and stronger in another.
Weaker: Meta's double tick is asserted by the *platform*, from the device's own
acknowledgement, and a user cannot trivially fake it from a page they control. Stronger,
or at least more honest: here the provenance of every state is in the record. A real
receipt is also a claim — it proves a device acknowledged a packet, not that a person read
anything — and the usual mistake is to forget that because a vendor made the claim.

Two mechanical properties keep the record truthful:

- **The ladder is monotonic.** A step backwards raises `BackwardsStatus`, carrying both
  statuses so a caller can tell a late report from a nonsense one. Rungs may be skipped; a
  repeat is a no-op, because clients retry.
- **An unknown delivery id raises `KeyError` rather than creating a record.** A status
  update for a message nobody sent is a caller bug, and conjuring the record would turn it
  into fabricated history. The API endpoint also checks that the reporting session *owns*
  the record, so one session cannot report on another's messages.

## Proactive threads are seeded, not pushed

The proactive agent can decide to start a conversation. What it cannot do is interrupt
anyone: there is **no push transport**. `ChatChannel.seed` puts the composed message in a
per-customer mailbox, and `open()` drains it into the thread after the greeting, oldest
first.

`channels/outbound.py` is that path, run end to end: the proactive agent over overdue
receivables, the rules engine, composition, the compliance check, and the mailbox. Two
commands use it, and the difference between them is the mailbox's own nature.

```sh
make seed                        # the report: runs the path and prints the outcome per receivable
make serve ARGS="--seed-demo"    # the demo: the server seeds the channel it is serving
```

**A mailbox is an in-memory dict.** `make seed` therefore fills a mailbox that belongs to
its own process and vanishes with it — it is a report of what the path decides, not a way
to leave a message for a server to serve later. `serve --seed-demo` seeds the channel the
API itself holds, which is what makes "open the widget and the message is waiting" true
rather than a claim; in that mode the demo page is served carrying a token for the seeded
customer, since an anonymous session has a random id and so an empty mailbox. Nothing is
persisted either way: a restart loses the thread, exactly as it loses the sessions.

And the message still waits, possibly forever, for a customer who never comes back.

The receivables are a fixture written out in `channels/outbound.py`, not a query: no job in
this repository reads overdue invoices out of a dataset, because the datasets live under
`data/` and are never committed.

This is named rather than hidden because the alternative was to claim a delivery the
system cannot perform. A live-arrival transport — SSE or polling against this same
contract — is future work, and a small piece of it: the contract, the mailbox, the status
ladder and the opt-out enforcement are already the hard parts.

Opt-out is checked **twice**, and the duplication is deliberate. The orchestrator
suppresses at routing time, when the contact is scheduled; `_drain` checks again at the
moment of delivery, because a customer can opt out between the seed and the widget
opening, by any path at all. Only the moment of delivery sees all of them. A reviewer
tidying one of these away would reintroduce the bug.

## Why the channels are simulated — the substitution argument

Two schedule items, the WhatsApp Business API (meeting 12) and an automated phone call over
real telephony (meeting 15), were both blocked on commercial accounts, approval queues and
a phone number this project does not have. They are replaced by a chat channel that
simulates WhatsApp messaging and, next, a real-time voice channel on WebRTC. The argument
that this is a substitution and not a reduction in scope is below; `cronograma.md` carries
it in Portuguese, under *Mudanças de escopo*.

The research question is whether a multi-agent architecture with LLM, RAG and a predictive
model can conduct debt collection autonomously, safely and within the legal constraints.
That is answered by **what the agents decide**, not by which commercial API carries the
bytes.

**WhatsApp Business API.** What it would add over the simulated chat channel is a vendor's
transport, a template-approval process and a billing relationship. What it would *not* add
is a single new decision by any agent. The channel contract is where the risk actually
lives, and what is actually proven today is narrower than a second adapter would prove:
**no agent can import a channel**, enforced by the AST guard above and by that guard's own
self-test. That is the property a WhatsApp adapter would rely on. Integrating the API would
not establish it — with one channel there is nothing to be inconsistent with, so the
integration would mostly test Meta's uptime, while the guard tests the claim the thesis
makes. A second adapter would be stronger evidence still; the voice channel is where that
comes from.

**Telephony.** The interesting problems in an automated call are turn-taking,
interruption, the latency budget and the legal opening. All four exist identically in a
WebRTC call; none of them is created by SIP. LiveKit, the framework chosen for the voice
channel, carries the same agent over SIP to a real number without changing the agent — so
the substitution is **reversible**, and saying so is part of the defence rather than an
excuse for it.

### What is honestly lost

Named here, and inherited by the article's limitations section:

- **Real-network packet loss and carrier audio codecs**, and what they cost recognition
  accuracy. `docs/voice.md` measures what recognition error costs the pipeline, which
  gives a principled way to discuss degraded carrier audio — but it is a sweep over a
  modelled error process, not a measurement of a phone line.
- **Meta's template-approval constraints** on outbound copy. The compliance checks in
  `docs/outreach.md` are this project's own rules, not a platform's, and a real deployment
  would have to satisfy both.
- **True end-to-end delivery receipts**, as described above.
- **Push.** A seeded thread is not an arriving message.

## Where this lives

| File | What it holds |
|---|---|
| `src/collection/channels/base.py` | `Channel`, `ChannelCapabilities`, `DELIVERY_CHANNELS` |
| `src/collection/channels/delivery.py` | The status ladder, channel-neutral so the voice channel reuses it |
| `src/collection/channels/web.py` | `ChatChannel`: voice notes, mailbox, delivery tracking, opt-out |
| `src/collection/channels/outbound.py` | The join: proactive agent → scheduled contact → mailbox |
| `src/collection/channels/session.py` | Sessions, tokens, rate limits |
| `src/collection/api/chat.py` | The HTTP surface, including the delivery-report endpoint |
| `tests/test_channels.py` | The contract, the ladder, the seeding rules, and the import guard |

The chat channel itself — identity, limits, status codes, the widget — is documented in
[`docs/web-chat.md`](./web-chat.md).
