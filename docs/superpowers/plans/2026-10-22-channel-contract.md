# Channel Contract and the Grown-Up Chat Channel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the channel seam explicit and enforceable, and grow the web widget into the chat channel that stands in for WhatsApp — voice notes, delivery status, proactive-started threads, in-thread opt-out.

**Architecture:** A narrow `Channel` protocol in `collection.channels.base` that every channel implements, declaring a name and its capabilities. The orchestrator and the agents stay unaware of it — enforced by an import test, not by convention. `WebChatChannel` becomes `ChatChannel`, implements the protocol, and gains the four features. The outbound *delivery* channel vocabulary is renamed to match (`whatsapp`→`chat`, `phone`→`voice`), leaving the ERP's record of a customer's real-world contact details alone.

**Tech Stack:** Python 3.12, `uv`, FastAPI, typer, pytest, plain ES5 JavaScript in `widget.js` (no build step — keep it that way).

**Spec:** `docs/superpowers/specs/2026-10-22-channel-contract-design.md`

## Global Constraints

- Every change lands through a pull request; never commit to `master`.
- Commits, code, comments and docs in English. Customer-facing copy in pt-BR.
- Commit messages end with the `Co-Authored-By:` and `Claude-Session:` lines used throughout this repo.
- CI must pass with no network, no API key, no microphone. `ffmpeg` is installed by CI as of PR #17.
- Real data and audio are never committed. `data/` is gitignored.
- Agents and the orchestrator must not import from `collection.channels` (spec §5, decision 5).
- Threads never migrate between channels (spec §3).
- Every `make` target maps one-to-one to a `collection` CLI command.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has a test assigned to the task that owns the code.

1. **A brief whose channel is not in `LENGTH_CAP`** — today `compose.py:158` and `compliance.py:192` fall back to `LENGTH_CAP["whatsapp"]`, a key that Task 2 deletes. A `KeyError` at compose time. → Task 2.
2. **A proactive seed aimed at an opted-out customer** — the orchestrator suppresses the event, so nothing must reach the session store. → Task 5.
3. **An audio upload that is too long, too short, or not audio** — must be rejected by `voice.audio.accept` before any model loads, and surface as a readable error rather than a 500. → Task 3.
4. **A seeded thread whose session expired before the customer opened it** — `SessionStore` sweeps on TTL; the seed must not resurrect a dead session or vanish silently. → Task 5.
5. **Delivery status on a thread that escalated to a human** — the widget stops accepting input after handover; status must not keep advancing to `respondida`. → Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/collection/channels/base.py` | **Create.** The `Channel` protocol and `ChannelCapabilities`. |
| `src/collection/channels/web.py` | **Modify.** `WebChatChannel` → `ChatChannel`; implements the protocol; gains audio, delivery status, seeding. |
| `src/collection/channels/delivery.py` | **Create.** Delivery-status state machine, separate from transport so the voice channel can reuse it. |
| `src/collection/agents/proactive.py:36-40,85` | **Modify.** `CHANNEL_BY_STAGE` to the new vocabulary. |
| `src/collection/outreach/{brief,compliance,compose,evaluate}.py` | **Modify.** Rename, and remove the now-invalid fallback key. |
| `src/collection/rules/policy.py:36` | **Modify.** `CHANNEL_COST` to the new vocabulary. |
| `src/collection/cli.py:353` | **Modify.** `--channel` help and default. |
| `knowledge/outreach-briefs.json` | **Modify.** Channel labels only; no copy changes. |
| `src/collection/api/{chat,schemas}.py` | **Modify.** Audio upload endpoint, status fields. |
| `src/collection/web/{widget.js,demo.html}` | **Modify.** The approved mockup. |
| `docs/channels.md` | **Create.** Contract, pivot argument, what each channel owns. |
| `cronograma.md` | **Modify.** Meetings 12, 14, 15 and a "Mudanças de escopo" section. |

---

### Task 1: The `Channel` contract

**Files:**
- Create: `src/collection/channels/base.py`
- Modify: `src/collection/channels/__init__.py`
- Test: `tests/test_channels.py` (create)

**Interfaces:**
- Produces: `ChannelCapabilities(accepts_audio: bool, accepts_text: bool, shows_delivery_status: bool, realtime: bool)` — a frozen dataclass. `Channel` — a `typing.Protocol`, runtime-checkable, with `name: str`, `capabilities: ChannelCapabilities`, and `def deliver(self, customer_id: str, text: str, **meta: object) -> str` returning a delivery id.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_channel_declares_what_it_can_carry():
    caps = ChannelCapabilities(
        accepts_audio=True, accepts_text=True, shows_delivery_status=True, realtime=False
    )
    assert caps.accepts_audio and not caps.realtime


def test_no_agent_or_orchestrator_imports_a_channel():
    """The enforceable form of channel-agnostic (spec §5, decision 5)."""
    offenders = []
    for path in Path("src/collection/agents").rglob("*.py"):
        if "collection.channels" in path.read_text():
            offenders.append(path.name)
    assert offenders == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_channels.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChannelCapabilities'`

- [ ] **Step 3: Implement `ChannelCapabilities` and `Channel` in `src/collection/channels/base.py`**

`Channel` is a `Protocol`, not an ABC: `ChatChannel` already exists and should satisfy it structurally rather than inherit. Re-export both from `channels/__init__.py`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_channels.py -v`
Expected: PASS

- [ ] **Step 5: Rename `WebChatChannel` to `ChatChannel` and assert it satisfies the protocol**

It is no longer "the web one" — it is the chat channel, and Plan B adds the voice one beside it. Rename at every call site (`api/chat.py`, `tests/test_web_chat.py`) and add:

```python
def test_the_chat_channel_satisfies_the_contract():
    assert isinstance(ChatChannel(index=stub_index()), Channel)
    assert ChatChannel(index=stub_index()).capabilities.accepts_audio
```

Opt-out is **already** handled in-thread by `web.py:_render` (the `OPT_OUT_REQUESTED` branch) — do not rebuild it. Spec §2 lists it as a goal because it must keep working through the three features that follow, not because it is missing.

- [ ] **Step 6: Run the suite**

Run: `uv run pytest -q`
Expected: PASS — the rename must break nothing

- [ ] **Step 7: Commit**

```bash
git add -u && git commit -m "feat: declare the contract every channel implements"
```

---

### Task 2: Rename the delivery-channel vocabulary

The *delivery* namespace (how the system chooses to reach someone) becomes `chat`, `voice`, `sms`, `email`. The *data* namespace (`domain/schema.py`, `data/sources/*`, `data/anonymize.py` — what the ERP records about a customer) is **not touched**: a customer's phone number is still a phone number.

Mapping is one-to-one — `whatsapp`→`chat`, `phone`→`voice`, `sms` and `email` unchanged — so `outreach/evaluate.py`'s `(stage, channel)` grouping keeps its shape and the published table must move **by the label and nothing else**.

**Files:**
- Modify: `src/collection/agents/proactive.py:36-40`, `src/collection/rules/policy.py:36`, `src/collection/outreach/compliance.py:78,192`, `src/collection/outreach/compose.py:158`, `src/collection/outreach/brief.py:97,164`, `src/collection/cli.py:353`, `knowledge/outreach-briefs.json`
- Test: `tests/test_outreach.py`, `tests/test_rules.py`, `tests/test_agents.py`

**Interfaces:**
- Produces: `DELIVERY_CHANNELS: frozenset[str] = frozenset({"chat", "voice", "sms", "email"})` in `collection/channels/base.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stages_route_to_delivery_channels_that_exist():
    assert set(CHANNEL_BY_STAGE.values()) <= DELIVERY_CHANNELS


def test_an_unknown_channel_does_not_crash_composition():
    """Review Focus 1: the old fallback key is gone."""
    brief = replace(load_briefs()[0], channel="carrier_pigeon")
    result = check(TemplateComposer().compose(brief).text, brief)
    assert result.length_cap == LENGTH_CAP["chat"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_outreach.py -k "delivery_channels or unknown_channel" -v`
Expected: FAIL

- [ ] **Step 3: Apply the rename**

Replace the literals at the sites listed above. Both fallbacks (`compose.py:158`, `compliance.py:192`) become `LENGTH_CAP["chat"]`. `LENGTH_CAP` becomes `{"chat": 480, "voice": 480, "sms": 160, "email": 1200}`; `CHANNEL_COST` becomes `{"chat": 0.12, "sms": 0.09, "email": 0.01, "voice": 1.40}`. In `knowledge/outreach-briefs.json` change only the `channel` values.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, 329+ tests

- [ ] **Step 5: Regenerate the outreach table and diff it**

Run: `uv run collection outreach --composer template` then `git diff docs/outreach.md`
Expected: **only channel labels change.** Any movement in `distinct_pairs`, mean length, or a compliance count is a bug in the rename — find it before continuing. Do not republish over it.

- [ ] **Step 6: Commit**

```bash
git add -u && git commit -m "refactor: name delivery channels after what the system actually does"
```

---

### Task 3: Voice notes in the chat thread

**Files:**
- Modify: `src/collection/channels/web.py`, `src/collection/api/chat.py`, `src/collection/api/schemas.py`
- Test: `tests/test_web_chat.py`

**Interfaces:**
- Consumes: `collection.voice.audio.accept`, `collection.voice.transcribe.Transcriber` (Task 1's `ChannelCapabilities`).
- Produces: `ChatChannel.send_audio(self, session_id: str, path: Path) -> ChatReply`. `ChatReply` gains `transcript: str | None = None`, `transcription_confidence: float = 0.0`, `needs_confirmation: bool = False`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_voice_note_is_answered_like_a_typed_message():
    reply = channel.send_audio(session_id, FIXTURE_OGG)
    assert reply.transcript and not reply.escalated


def test_a_spoken_figure_is_read_back_in_the_thread():
    assert channel.send_audio(sid, clip_saying_an_amount).needs_confirmation


def test_audio_the_decoder_refuses_does_not_reach_the_model(tmp_path):
    """Review Focus 3."""
    junk = tmp_path / "x.ogg"
    junk.write_bytes(b"not audio")
    with pytest.raises(AudioError):
        channel.send_audio(session_id, junk)


def test_the_transcript_is_what_lands_in_the_session_record():
    channel.send_audio(session_id, FIXTURE_OGG)
    assert channel.transcript(session_id)[-2]["text"] == EXPECTED_TRANSCRIPT
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_web_chat.py -k audio -v`
Expected: FAIL — `AttributeError: 'ChatChannel' object has no attribute 'send_audio'`

- [ ] **Step 3: Implement `send_audio` in `web.py`**

It mirrors `send()`: clean, record the customer turn, submit **`EventType.AUDIO_RECEIVED`** with `audio_path` in the payload, run, render. The channel does **not** transcribe (spec §5, call 6) — the agent does, so the read-back and the `MIN_CONFIDENCE` escalation keep working unchanged. Carry `transcript` / `transcription_confidence` / `needs_confirmation` out of the `reply_sent` payload in `_render`.

- [ ] **Step 4: Add the upload endpoint**

`POST /chat/sessions/{session_id}/audio` taking an `UploadFile`, writing it to a temp path, calling `send_audio`. Reuse `_http_error` so `AudioError` and `TranscriptionError` become 4xx, not 500. Add `AudioMessageResponse` to `schemas.py` extending `MessageResponse` with the three new fields.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_web_chat.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -u && git commit -m "feat: accept voice notes in the chat thread"
```

---

### Task 4: Delivery status

**Files:**
- Create: `src/collection/channels/delivery.py`
- Modify: `src/collection/channels/web.py`, `src/collection/api/schemas.py`
- Test: `tests/test_channels.py`

**Interfaces:**
- Produces: `DeliveryStatus(StrEnum)` with `ENVIADA`, `ENTREGUE`, `LIDA`, `RESPONDIDA`; `DeliveryRecord(id: str, status: DeliveryStatus, at: float)`; `DeliveryLog` with `.record(text) -> DeliveryRecord`, `.advance(delivery_id, status) -> DeliveryRecord`, `.of(delivery_id) -> DeliveryRecord`.

The status is modelled as a monotonic ladder: it never goes backwards, and `RESPONDIDA` is only reachable when the customer actually sends the next turn. Keeping it in its own module is what lets the voice channel reuse it in Plan B.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_only_moves_forward():
    log = DeliveryLog()
    rec = log.record("oi")
    log.advance(rec.id, DeliveryStatus.LIDA)
    with pytest.raises(ValueError):
        log.advance(rec.id, DeliveryStatus.ENTREGUE)


def test_a_customer_turn_marks_the_previous_message_answered():
    channel.send(sid, "oi")
    assert channel.delivery.of(previous_id).status is DeliveryStatus.RESPONDIDA


def test_an_escalated_thread_stops_advancing():
    """Review Focus 5: handover ends the thread, so nothing becomes respondida."""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_channels.py -k delivery -v`
Expected: FAIL

- [ ] **Step 3: Implement `delivery.py`, then wire it into `ChatChannel`**

Every agent turn records; the next customer turn advances the previous record to `RESPONDIDA`. `ENTREGUE` and `LIDA` are set by the widget reporting back (Task 6), not guessed on a timer — document that in `docs/channels.md` as the honest difference from a real WhatsApp receipt.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_channels.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u && git commit -m "feat: track whether an outbound message was delivered, read and answered"
```

---

### Task 5: Threads the proactive agent starts

**Files:**
- Modify: `src/collection/channels/web.py`
- Test: `tests/test_channels.py`

**Interfaces:**
- Consumes: Task 4's `DeliveryLog`; `collection.agents.proactive.ProactiveAgent`; `EventType.CONTACT_SCHEDULED`.
- Produces: `ChatChannel.seed(self, event: Event) -> str | None` — delivers an outbound message into a waiting thread, returning the delivery id, or `None` when it was suppressed. `ChatChannel.open()` returns the seeded thread when one exists for that customer.

No push transport (spec §5, decision 11): the message waits, and the widget finds it on open.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_scheduled_contact_is_waiting_when_the_customer_opens_the_widget():
    channel.seed(contact_scheduled_event(customer_id="c1", text="Olá, Marina..."))
    session, greeting = channel.open(token=issue_token("c1"))
    assert "Marina" in channel.transcript(session.id)[-1]["text"]


def test_an_opted_out_customer_is_never_seeded():
    """Review Focus 2."""
    channel.opted_out.add("c1")
    assert channel.seed(contact_scheduled_event(customer_id="c1")) is None


def test_a_seed_whose_session_expired_is_still_waiting_on_the_next_open():
    """Review Focus 4: the seed outlives the session, because it is not one."""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_channels.py -k seed -v`
Expected: FAIL

- [ ] **Step 3: Implement `seed`**

The pending outbound lives in a per-customer mailbox on the channel, **not** in a `Session` — sessions expire and a scheduled contact must not. `seed` routes through an `Orchestrator` so opt-out suppression happens in the one place that owns it, rather than being re-checked here. `open()` drains the mailbox into the new session after the greeting.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_channels.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u && git commit -m "feat: let the proactive agent start a chat thread"
```

---

### Task 6: The widget, as designed

**Files:**
- Modify: `src/collection/web/widget.js`, `src/collection/web/demo.html`
- Test: `tests/test_web_chat.py`

Mockup: https://claude.ai/artifact/E6fcg1Hei3H2sFSoyThezR (artboard `Canal: chat`). Plain ES5, no build step, no framework — match the file's existing style.

- [ ] **Step 1: Rebuild the thread view**

Message bubbles (inbound left, outbound right), a date chip, and a delivery tick per outbound bubble bound to Task 4's status. Report `ENTREGUE` on render and `LIDA` when the thread is scrolled into view.

- [ ] **Step 2: Add the audio affordance**

A hold-to-record mic button using `MediaRecorder`, posting to Task 3's endpoint; an audio bubble with a play control and duration for what was sent. When the reply carries `needs_confirmation`, render the read-back prominently — it is the behaviour #17 exists to defend and the thing to point at during the demo.

- [ ] **Step 3: Degrade honestly where the browser cannot help**

No `MediaRecorder`, or permission denied: hide the mic, keep the text input, and say why in one line. Never a silent dead button.

- [ ] **Step 4: Verify against the demo page**

Run: `uv run collection serve` and open `/chat/demo`
Expected: the thread matches the mockup; a voice note round-trips; ticks advance.

- [ ] **Step 5: Commit**

```bash
git add -u && git commit -m "feat: render the chat channel the way the demo needs it"
```

---

### Task 7: Document the contract and amend the roadmap

**Files:**
- Create: `docs/channels.md`
- Modify: `cronograma.md`, `README.md`, `docs/architecture.md`, `docs/web-chat.md`

- [ ] **Step 1: Write `docs/channels.md`**

What a channel owns and what it must not decide; the capability table; why the delivery vocabulary is not the data vocabulary (Task 2); how delivery status differs from a real WhatsApp receipt; and the pivot argument from spec §1, in English here and summarised in pt-BR in `cronograma.md`.

- [ ] **Step 2: Amend `cronograma.md`** (pt-BR)

Meetings 12, 14 and 15 per spec §8, plus a **"Mudanças de escopo"** section listing each substitution with its date, what it replaced, why, and what is consequently absent from the results.

- [ ] **Step 3: Update `README.md`, `docs/architecture.md`, `docs/web-chat.md`**

`web-chat.md`'s "no push, no read receipts" line is now half false — correct it rather than leaving a document that contradicts the code.

- [ ] **Step 4: Run the full suite and the linters**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: all pass

- [ ] **Step 5: Commit and open the PR**

```bash
git add -u && git commit -m "docs: describe the channel contract and record the scope changes"
```

PR title: `feat: make the channel seam explicit and grow the chat channel`. Labels `feature` + `area/channels` + `area/agents`. Assign `gabrielforster`. Base `master`.
