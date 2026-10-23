# Channel contract, a grown-up chat channel, and a LiveKit voice channel

Design document — 2026-10-22.

## 1. Intent

The system can already answer a customer. It can answer in writing, it can answer a voice
note, it can decide on its own to start a conversation, and it can compose what it says.
What it cannot do is reach a customer anywhere except an embeddable web widget, and the
schedule's two remaining integration items — the WhatsApp Business API (meeting 12) and an
automated phone call over real telephony (meeting 15) — are both blocked on commercial
accounts, approval queues and a phone number that this project does not have and does not
need in order to prove what it set out to prove.

So the integrations are replaced by two channels that demonstrate the same behaviour
without the external dependencies:

- the existing web widget **grows into** a chat channel that simulates WhatsApp messaging
  — bubbles, delivery status, voice notes, system-initiated threads;
- a new **voice channel on LiveKit** stands in for the phone call: a real-time,
  browser-based conversation with barge-in and turn-taking.

Both sit behind a **channel contract** that the orchestrator and the agents are already
written against but which nothing currently enforces. Making that contract real is the
actual engineering content of this work; the two channels are what proves it.

### Why this is a legitimate substitution, not a reduction in scope

This section exists to be read by the examining professors, and a Portuguese version of it
is owed to the article and the schedule (see §8).

The research question is whether a multi-agent architecture with LLM, RAG and a predictive
model can conduct debt collection autonomously, safely and within the legal constraints.
That question is answered by what the agents decide, not by which commercial API carries
the bytes. Specifically:

1. **WhatsApp Business API.** What it would add over the simulated chat channel is a
   vendor's transport, a message-template approval process, and a billing relationship.
   What it would *not* add is a single new decision by any agent. The channel contract is
   where the risk lives — if the architecture is genuinely channel-agnostic, a WhatsApp
   adapter is a few hundred lines against a contract already proven by two other adapters;
   if it is not, no amount of API integration would reveal that, because with one channel
   there is nothing to be inconsistent with. The simulation tests the property the thesis
   claims. The integration would only test Meta's uptime.

2. **Telephony.** The interesting problems in an automated call are turn-taking,
   interruption, the latency budget, and the legal opening. All four exist identically in
   a WebRTC call; none of them is created by SIP. LiveKit, which is the framework used
   here, carries the same agent over SIP to a real number without changing the agent — so
   the substitution is reversible, and saying so is part of the defence rather than an
   excuse for it.

3. **What is honestly lost.** Real-network packet loss, carrier audio codecs and their
   effect on recognition accuracy, Meta's template-approval constraints on outbound copy,
   and true end-to-end delivery receipts. These go in the article's limitations section,
   named, not glossed. The voice module already measures what recognition error costs
   (`docs/voice.md`), which gives a principled way to discuss degraded carrier audio
   without having measured it directly.

## 2. Goals

- A **`Channel` contract** every channel implements, narrow enough that the orchestrator
  and the agents are provably unaware of which channel they are serving.
- The **chat channel**, grown from the existing web widget, carrying: voice notes in the
  thread, delivery status (`enviada / entregue / lida / respondida`), threads the proactive
  agent starts, and opt-out handled in-thread.
- The **voice channel** on LiveKit: a browser "phone" demo with local Whisper STT,
  barge-in, and a recording notice that cannot be skipped.
- Two measured findings (§6): cross-channel decision divergence, and the decomposed
  turn-time budget.
- The pivot documented in `cronograma.md`, in `docs/`, and in the article, in Portuguese.

## 3. Non-goals

- The WhatsApp Business API. No Meta account, no template approval, no webhooks.
- Telephony: no phone number, no SIP trunk, no carrier.
- **Cross-channel thread migration.** A conversation lives on the channel it started on.
  A customer on a call who asks for something in writing is *answered on the call*. The
  agent may cause an outbound written message as a side effect, but the thread does not
  move, and no identity reconciliation across transports is attempted.
- A third chat channel. The web widget **is** the chat channel; nothing new is created
  beside it.
- Sending customer audio to a third-party recognition service (see §4).

## 4. Anti-goals — what would make this a failure even if it worked

- **A channel that makes decisions.** The moment the voice channel decides what to say,
  or the chat channel filters who may be contacted, the architecture's central claim is
  false and the article's architecture section is fiction. The divergence measurement in
  §6 exists to make this fail loudly rather than quietly.
- **A demo that only works in the room.** If the only evidence is a live demo, there is no
  results section. Every claim here must survive being run in CI with no microphone and no
  network.
- **Quietly reversing the LGPD position taken in `docs/voice.md`.** That document argues
  that customer audio must not leave the machine for a third-party recogniser. A real-time
  pipeline is the obvious place to break that promise for latency. The decision below is
  that it is not broken.

## 5. Constraints and decisions already taken

These were decided in conversation and are not open to the builder.

| # | Decision | Rationale |
|---|---|---|
| 1 | **Whisper stays local.** The real-time pipeline uses the same CPU `faster-whisper` path as `src/collection/voice/transcribe.py`. | Consistency with the LGPD position already published. The latency cost is accepted and becomes a measured result rather than a hidden one. |
| 2 | LiveKit Agents (Python) is the voice transport and turn-taking framework. | It provides VAD, end-of-turn detection and barge-in — the parts that make a call different from a voice note. Its SIP support makes the telephony substitution reversible. |
| 3 | The web widget grows; no third channel. | One fewer adapter to keep consistent, and the widget already owns identity, sessions, rate limiting and opt-out. |
| 4 | Threads do not migrate between channels. | Identity reconciliation across transports is a separate problem and not this thesis's. |
| 5 | Agents and the orchestrator must not import anything from `collection.channels` or the voice channel. | This is the enforceable form of "channel-agnostic". Worth an explicit test. |

Inherited constraints that still bind: every implementation lands through a pull request,
never a commit to `master`; commits, code and docs in English while customer-facing copy
stays in Portuguese; real data and secrets are never committed; CI must pass with no
network, no microphone and no API key.

### Calls taken at builder review (the author's to overrule)

These were not settled in conversation. They follow from the goals and anti-goals above
rather than from preference, and each is recorded here so it can be reversed knowingly.

6. **The voice channel does not transcribe.** It writes each completed utterance to a
   temporary clip and emits `AUDIO_RECEIVED` with `audio_path`, exactly as the chat
   channel does for a voice note. The agent still runs Whisper, still applies
   `MIN_CONFIDENCE`, still decides the figure read-back. The alternative — the channel
   recognising speech and emitting text — would put a decision in a channel (§4) and would
   quietly disable the read-back, which is the behaviour PR #17 exists to defend. Cost: a
   temp file per turn on a path that has a stream in hand.

7. **Call audio is retained, and erasable on request.** *(Amended by the author.)* Both
   records are kept: the recording under `data/` (gitignored, never committed, beside the
   anonymised dataset) and the transcript in the event trail. They have different
   lifetimes on purpose — a recording is biometric personal data and is **deletable on
   request**, while the transcript carries the content without the voice and survives as
   the audit record. This is the LGPD right to erasure implemented as a property of the
   system rather than promised in prose: erasing a customer's audio must not destroy the
   account of why they were contacted.

   Two consequences the builder must carry through. There is a **deletion path** — a
   command or API that removes every clip for a customer id and leaves the event trail
   intact, with a test that asserts exactly that split. And the retained audio is what
   finally makes **calibrating `MIN_CONFIDENCE`** possible against real speech, which
   `docs/voice.md` currently lists as owed and unsupported; this design does not do that
   calibration, but it stops foreclosing it.

   The on-screen notice still reads *"está sendo transcrita e registrada"*, which is true
   of both records, and the schedule's "registro completo da chamada" is satisfied.

8. **The turn-time measurement uses synthesised clips, not recordings.** The anti-goal
   forbids a result that only exists on one machine, and the repo must not hold customer-
   shaped audio. So the benchmark speaks a fixed set of utterances through the chosen TTS,
   feeds them to the real pipeline, and publishes the table — reproducible by anyone with
   the extras installed, and runnable in CI as a smoke path even where the full model is
   not. This makes the TTS decision a prerequisite for the measurement, not a side issue.

9. **Meeting 14 is amended too.** Its text presumes the WhatsApp account and names the
   delivery-status tracking this work implements, so it is rewritten against the simulated
   chat channel and listed under "Mudanças de escopo" alongside 12 and 15. Composition
   itself was delivered by #16; the tracking half lands here.

### Decisions taken by the author at builder review

10. **One channel namespace: `chat` and `voice`.** The proactive agent's `whatsapp` /
    `phone` strings are renamed at every site that carries them — `CHANNEL_BY_STAGE`,
    `knowledge/outreach-briefs.json`, `compliance.LENGTH_CAP`, the composer's per-channel
    personalisation, and the grouping in `outreach/evaluate.py`. The published table in
    `docs/outreach.md` is regenerated. **It should move by the label and nothing else**;
    any other movement is a finding, not a nuisance, and must be chased before the table
    is republished.
11. **Proactive threads are seeded, with push named as future work.** The outbound message
    is delivered into the session store so the thread is waiting when the widget opens —
    no push transport, no delivery to a session-less customer, no dispatcher. The demo is
    "run the detection job, then open the chat". A live-arrival transport (SSE or polling)
    is recorded in the limitations section as named future work, built against this same
    contract.
12. **LiveKit is self-hosted via `docker-compose.yml`.** No vendor account, no credentials
    to hold or leak, and a professor can bring the whole thing up from the repo. The
    Silero VAD model download on first use must therefore be handled explicitly — vendored,
    cached, or skipped on the scripted arm — so that a cold checkout does not silently
    depend on the network.

### Still open — the builder must decide and document

- **TTS.** The system has never needed speech synthesis and has no provider. The decision
  must state where synthesis runs and, if it is a cloud service, must address why sending
  *the system's own words* to a vendor is acceptable while sending *the customer's* audio
  is not. (That asymmetry is defensible — the system's words are not personal data — but
  it has to be argued in writing, not assumed.)
- Whether delivery status is simulated on a timer or driven by real widget events, and how
  the article describes the difference.

## 6. What gets measured

Each module in this project earns its place in the article with one finding. Two here.

**Cross-channel decision divergence.** A fixed set of conversations is replayed through
both channels — as typed text on chat, and as the same utterances on the voice path with
the scripted transcriber from `collection.voice.transcribe` standing in for recognition.
For each turn, the agent's decisions are compared: intent, escalation, opt-out, the
citations retrieved, and whether a figure read-back was required. **Divergence should be
zero**, and the measurement is only interesting because it can be non-zero: the harness
must be shown to detect a planted divergence before the zero is believed. This is the same
discipline the voice CI gate needed, and for the same reason.

The one expected and *legitimate* difference is the figure read-back, which fires on the
voice path and not on typed text — that is deliberate behaviour from PR #17, so it is
compared as a labelled exception rather than counted as divergence.

**The turn-time budget.** Time from the customer finishing a sentence to the first word of
the reply, decomposed into end-of-turn detection, local transcription, retrieval,
generation and speech synthesis. A single number hides which stage to attack; the shape is
the finding. Local Whisper is expected to dominate, and if the total is poor, the honest
table is still a better results section than a flattering one. `Transcription.real_time_factor`
already exists for the transcription term.

## 7. Shape of the work

Left to the builder, but the following structure is implied by the constraints:

- `src/collection/channels/base.py` — the `Channel` contract. Narrow: accept an inbound
  turn, render an outbound one, report capabilities (does this channel accept audio? can
  it show delivery status?). Everything else stays where it already is.
- `src/collection/channels/web.py` — grows the four features in §2. The session store,
  token issuance and rate limiting are already there and should not be rewritten.
- `src/collection/channels/voice.py` (or a `voice/` package) — the LiveKit adapter,
  behind a provider seam with a scripted arm, in the same pattern as `LLMClient` /
  `ScriptedClient` and `Transcriber` / `ScriptedTranscriber`. **CI must exercise the
  scripted arm**; LiveKit itself is an optional extra, lazily imported.
- `src/collection/web/widget.js` + `demo.html` — the UI from the approved mockups
  (https://claude.ai/artifact/E6fcg1Hei3H2sFSoyThezR): bubbles and delivery ticks on chat,
  pulsing orb, live transcript, latency row, recording notice and a transfer-to-human
  control on voice.
- A CLI command per the project's rule that every `make` target maps to one, with a CI gate
  that fails on divergence — verified non-vacuously before it is trusted.
- `docs/channels.md` — the contract, the pivot argument, and both findings.

## 8. Roadmap changes

`cronograma.md` is the artefact the professors track, so the substitution is recorded
there rather than silently dropped. Proposed edits, all in Portuguese:

- **Meeting 12 (15/10)** — *Canal + áudio*: amend to record that the WhatsApp Business API
  was replaced by a simulated chat channel, with the justification from §1 in one or two
  sentences, and that the audio half was delivered by PR #17.
- **Meeting 15 (05/11)** — *Chamada telefônica automatizada*: becomes **Chamada por voz em
  tempo real (WebRTC/LiveKit)**. The deliverable changes from "chamada real gravada e
  transcrita" to "chamada em tempo real demonstrada e cronometrada", the artefact becomes
  the browser demo plus the turn-time table, and the *Texto* column keeps "Integração de
  voz (tempo real)". A note records that SIP would carry the same agent to a real number.
- **Meeting 14 (29/10)** — *Abordagem multicanal por texto*: `envio pelo WhatsApp` becomes
  envio pelo canal de chat simulado. Composition and compliance were delivered by #16; the
  `enviada / entregue / lida / respondida` tracking lands with this work.
- A short **"Mudanças de escopo"** section added to `cronograma.md`, listing each pivot with
  its date, what it replaced, why, and what is consequently absent from the results — so a
  reader meets the substitutions in one place and in the project's own words, rather than
  discovering them.
- The article's limitations section inherits the losses named in §1.3.

These edits ship with the implementation PR, not before it: the roadmap should not claim a
delivery that is not yet merged.

## 9. Open question for the author

None blocking. The TTS decision in §5 is the one real fork left, and it can be taken at
implementation time provided it is written down and argued — but note that call 8 makes it
a prerequisite for the turn-time measurement, so it cannot be deferred to the end.
