# The voice call: a real-time channel over WebRTC

This is the reduced proof of concept of the real-time call: a customer opens a page,
presses *Ligar*, hears a recording notice they cannot skip, speaks, and is answered by the
same agent that answers the chat. Audio travels over WebRTC through a self-hosted LiveKit
server. Nothing leaves the machine: recognition is local Whisper, synthesis is local Piper,
and the media server is a container from `docker-compose.yml`.

It replaces the switched-telephony call in the schedule. The argument that this is a
substitution, and not a cut, is in [`docs/channels.md`](./channels.md); this document is
about what was built, what it measures, and what it leaves out.

```sh
make voices          # once: the two Piper pt-BR voices, ~120 MB, into data/voices
make call            # starts LiveKit, then the page on http://127.0.0.1:8100/call/demo
make call-bench      # the turn-time table, in-process  -> docs/voice-call-report.md
make call-bench ARGS="--live"   # the same, through a LiveKit room -> docs/voice-call-report-live.md
```

## How a call runs

```
browser ──WebRTC──► LiveKit (docker) ──WebRTC──► CallAgent ──► VoiceCallChannel ──► Orchestrator ──► ResponsiveAgent
 (mic)                                            endpointer      notice, silence,     opt-out            Whisper, intent,
                                                  Piper, playout  records, erasure     suppression        RAG, responder
```

Three layers, each owning one thing.

**`VoiceCallChannel`** (`channels/voice_call.py`) holds every rule the call has. It has no
transport. The tests drive it from WAV files and the benchmark from synthesised speech.
The live server drives it from a LiveKit room. None of the three can get around the rules,
because none of them holds the state the rules live in.

- **The recording notice comes first, and nothing counts until it has played.** The call
  opens in an `OPENING` state. The transport has to play the notice and the greeting, then
  call `opening_played`. Speech heard before that is logged as
  `speech_during_notice_ignored` and dropped, not queued. If an utterance slipped in under
  the notice, the customer would be recorded before being told. The browser page shows the
  notice too, but the page is code the customer controls, so it is not where the rule lives.
- **A quiet line is asked once, then hung up.** After `SILENCE_TIMEOUT_S` (8 s) of
  silence the call asks *"Você ainda está na linha?"*. A second silence ends the call with a
  goodbye. A collection call left open on a silent line is a call nobody is accountable for.
- **A handover or an opt-out ends the call**, after the sentence that says so. A call is
  also cut at `MAX_CALL_S` (10 min).
- **Two records, with two lifetimes** (spec decision 7). The transcript goes into the
  session store, the same one the chat writes to. For a tenant that store is rows, so it
  persists the way chat threads do, under channel `voice`. The audio is kept separately
  under `data/calls/<customer>/`, which is git-ignored. `erase_audio(customer_id)` deletes
  every recording and leaves the transcript and event log intact, then adds an
  `audio_erased` event. The test asserts exactly that split. A voice is biometric personal
  data. The transcript is the account of why the customer was contacted, and erasing one
  must not erase the other.

**The channel does not recognise speech.** Each finished utterance becomes a WAV clip,
handed to the orchestrator as `audio_received`. This is the chat channel's own voice-note
path; `ChatChannel.send_audio` is reused, not copied. So the `MIN_CONFIDENCE` escalation,
the read-back of a spoken figure and the opt-out suppression all apply on a call, and none
of them was re-implemented. That reuse is why cross-channel decision divergence is zero
*by construction*. See the limitations for why that is not the same as measured.

**`CallAgent`** (`channels/livekit_call.py`) moves audio and keeps time. It joins the room
as an ordinary participant, publishes the agent's voice and subscribes to the customer's
microphone. Customer frames (16 kHz, 20 ms) go through the endpointer. A finished utterance
goes to `channel.hear` in a worker thread, so the event loop keeps reading audio while
Whisper runs. The reply is synthesised one sentence at a time and played as each sentence
arrives. A short JSON message on the room's data channel tells the page what was heard and
how long the turn took. That message fills the turn-time strip on the call screen.

**The endpointer** (`channels/endpointing.py`) decides that the customer has finished. It
compares frame energy with a fixed −45 dBFS threshold. A turn ends after 700 ms of silence
(the hangover), with 200 ms of pre-roll, a 200 ms minimum so a click is not a turn, and a
30 s cap on a monologue. It is pure (bytes in, utterances out), so every decision it makes
is a unit test.

### Why not the LiveKit agents framework

The spec named LiveKit Agents. The prototype uses the lower-level `livekit` SDK instead.
The framework's voice pipeline is STT → LLM → TTS with the recogniser inside it, and it
hands the agent text. That is the shape the channel contract forbids: a channel that
decides what the customer said switches off the confidence escalation and the figure
read-back. Using the framework would have meant either breaking the contract or fighting
the framework to put recognition back in the agent.

What that costs is what the framework does well: Silero VAD, a learned end-of-turn model,
and barge-in. All three are listed as limitations below. Each can be added at a single
seam (the endpointer, or the playout loop) without touching the channel.

## The TTS decision: Piper, locally

**Piper** (`piper-tts`, a VITS model exported to ONNX), running on the CPU in-process. The
agent speaks with `pt_BR-faber-medium`. The benchmark uses `pt_BR-cadu-medium` as the
customer, so Whisper is not hearing the agent's own voice back.

The spec asked that the decision say where synthesis runs, and, if it ran in the cloud,
why sending the system's words to a vendor is acceptable when sending the customer's voice
is not. That asymmetry is real but weaker than it looks. The words are the system's, yet a
collection reply reads back one named person's debt: the amount, the instalments, the due
date. A cloud TTS would be a third-party processor of a debtor's financial situation, and
the operation would owe a disclosure for it.

The usual argument for a cloud engine is latency, and here it does not hold. Piper's
**first audio is 58 ms at the median** (p90 103 ms), measured below. That is roughly
twenty times smaller than the recogniser's term. So local synthesis costs nothing in the
budget, and it is the position that needs no defending. It also keeps the whole call
inside the LGPD position `docs/voice.md` already took for recognition.

The cost is voice quality: Piper's medium pt-BR voices are clearly synthetic. For a
collection call that has to say in its first sentence that it is automated
(`channels/web.py`, CDC art. 42), that is not a defect.

## The turn-time budget

Spec §6 asked for the time from the end of the customer's sentence to the first word of
the reply, split by stage, because one number hides which stage to attack. Below are 18
lines from `knowledge/voice-messages.json`, spoken by Piper, one fresh call each, with the
template responder (no API key). Full tables, per line and including what Whisper heard,
are in [`voice-call-report.md`](./voice-call-report.md) (in-process) and
[`voice-call-report-live.md`](./voice-call-report-live.md) (through LiveKit).

Measured on 2026-11-08 on an AMD Ryzen 7 5700X3D (8 cores / 16 threads), CPU only,
faster-whisper `small` int8, with the spoken replies described below, and retrieving only
from the customer-facing documents, as the call server does:

| Stage | In-process median | p90 | Through LiveKit median | p90 |
|---|---:|---:|---:|---:|
| End-of-turn wait (hangover) | 700 | 700 | 700 | 700 |
| Local Whisper `small` | 1,189 | 1,230 | 1,213 | 1,242 |
| Agent: intent, retrieval, responder | 56 | 60 | 55 | 55 |
| Piper, first audio | 83 | 110 | 98 | 118 |
| **Sum of stages** | **2,044** | 2,074 | **2,057** | 2,111 |
| **End of speech → first audible reply, over WebRTC** | — | — | **2,174** | 2,240 |

The first measurement, on 2026-11-06 with whole-passage replies, gave 2,184 ms over WebRTC.
Shortening the reply did not move this number, and was not expected to: the table times the
first audio, and Piper synthesises one sentence at a time.

What the table says:

1. **About two seconds between the customer stopping and the agent starting.** That is
   noticeably slower than a person, who answers in well under a second. It is usable and
   it is not natural, and the honest description is a call with a short pause before
   every reply.
2. **Whisper is 60% of it, and the hangover is a third.** The agent is under 3% of the
   turn and the synthesiser under 4%, so neither is worth optimising. The two terms worth attacking
   are the recogniser (a smaller model is ruled out by `transcribe.py`'s note that `tiny`
   and `base` mangle pt-BR numerals; a GPU or a streaming recogniser is not) and the
   endpointing wait (a learned end-of-turn model shortens it without cutting people off).
3. **The network costs about a tenth of a second.** Through LiveKit, end-to-end exceeds
   the sum of in-process stages by a **median of 111 ms** (range 70–150 ms over 18 lines;
   110 ms and 75–155 ms in the first run).
   That covers Opus encoding, the room hop, the jitter buffer and playout, on loopback.
   On a real network add the round trip. The transport was never the problem.
4. **Load matters as much as the model.** This machine was shared with other jobs while it
   was measured. Under load the same in-process run gave a median Whisper of 2,002 ms, and
   one live line took 6.3 s to recognise. The table above is from quiet runs. The loaded
   ones are part of the result: on a shared CPU, recognition latency is set by everything
   else running on that CPU.
5. **Synthetic speech flatters the recogniser.** These lines are spoken by a clean TTS
   voice. A person on a laptop microphone will be slower to recognise and less accurate.
   The Whisper term is a floor, not an estimate.

### What the agent says, cut for the ear

The finding the turn-time table does not show was the one that mattered. In the first
measurement (2026-11-06) **the template responder's spoken reply lasted a median of 102
seconds** (max 133 s). It quoted two retrieved passages whole, headings and lists
included. That is already long in a chat bubble and unusable on a call, where the customer
can neither skim nor scroll: they sat through a minute and a half to get one answer.

**Now the median is 12.1 seconds** (max 19.2 s; all 18 lines inside the 20 s target;
`voice-call-report.md`, column *Reply s*). Lines that end in a handover, an opt-out or small
talk are 4–5 s, because those are fixed sentences.

**Who shortens it.** Not the channel. A channel that cut replies would be deciding which
part of a policy the customer hears, and the contract forbids it. The call instead *states*
what it can carry -- `ChannelCapabilities.max_reply_words` -- and that budget rides on the
event as `reply_max_words`. The responder decides what fits. The chat states no budget, and
its replies are byte-for-byte what they were (`make answer`: grounded 0.875, faithful 1.0,
citation accuracy 0.857, unchanged).

**The budget is derived.** One number is chosen: a 20-second target turn, about five of the
customer's own turns (their lines run a median 4 s) and room for some twenty exchanges in
the ten-minute call cap. The rest is measured. Piper's `faber` voice speaks every sentence
the responder could say -- 47 of them, prose and the instalment table -- at 2.15 words a
second or faster. A budget at the *slowest* rate holds for any reply, so 20 s × 2.15 = **43
words** (`voice_call.REPLY_TARGET_S`, `speak.SLOWEST_WORDS_PER_SECOND`). `make call-bench`
re-measures the rate on every run and says whether the constant still holds; on 2026-11-07
it measured 2.16 and 2.17 slowest.

**"Words" had to be redefined.** The first derivation counted words with `split()` and the
re-measurement caught it: the instalment table's rows spoke at 1.15 words a second against
2.15 for prose. `entrada_minima_percentual` is one token to `split` and three words aloud;
`1.5` is one token and *"um vírgula cinco"*. `speak.spoken_words` counts runs of letters
and of digits separately, and `%` as *"por cento"*. With that unit the table and the prose
speak at the same rate (about 2.2–2.5 words a second against 2.15–3.7), and one budget
fits both. Piper's synthesis is not bit-identical between runs, so these move in the second
decimal: three measurements of the slowest sentence gave 2.15, 2.16 and 2.17.

**What gets said.** The template's spoken reply (`agents/spoken.py`) is the one section of
the retrieved passages -- a heading and what sits under it -- that best matches the question,
read in the order it was written. The heading is scored, because in the FAQ the heading
*is* the question, but never spoken. The sentences are verbatim and never cut; the first
one that would overrun ends the reply. Nothing is paraphrased, so the template keeps its
guarantee: across the 34 labelled questions and voice lines every spoken reply is inside
the budget and fully supported (sentence support 1.0, no unsupported figure;
`tests/test_spoken.py`). A first version took the best sentences from anywhere and filled
the budget greedily. It filled it with fragments of the escalation list that matched one
word of the question and meant nothing on their own. A reply shorter than its budget is the
point, not a waste of it.

The `Fonte:` line is not spoken -- a file name read aloud tells the customer nothing -- but
the documents stay on the answer's citations and in the call record.

**The LLM arm** is told the budget in its prompt, and its `Fonte:` line is kept as a
citation and removed from what is said. A model that ignores the length falls back to the
spoken template, labelled `llm:over_budget`: the budget is what makes the call usable, so it
is enforced, not requested. A figure heard in audio is still read back first; the read-back
is the customer's own words and cannot be shortened, so it is paid for out of the answer's
share.

**What this does not fix.** The section chosen is only as good as retrieval. *"quero
parcelar em seis vezes"* still gets the policy's sentence on contested debts, because that
is the passage the lexical index ranks first for it -- a retrieval miss the chat has too,
only hidden there under a second passage.

Until 2026-11-08 the call server's in-memory index held the internal documents too, so a
reply could be an instruction to the operator (*"Nunca prometer desconto fora da
alçada."*), or the discount ceilings. The pgvector path filters them in SQL; the in-memory
index has no audience, so the call server, the chat with no tenant and the benchmark now
build it from `load_customer_facing`, which leaves them out
(`tests/test_internal_documents.py`). Answering only from customer-facing text made the
replies a little longer: the median went from 11.7 s to 12.1 s.

### Recognition, briefly

Whisper `small` got the labelled intent on **15 of 18** lines in the first run, **14 of
18** in both runs on 2026-11-07, and **15 of 18** in both on 2026-11-08 (median WER 0.11
in-process, 0.10 through LiveKit). Piper's synthesis is not bit-identical between runs, so
one line moves in and out.
Punctuation is folded before scoring; digits are not, because "6" against "seis" is a real
difference downstream. The misses are informative:

- `voz_11`, *"dois mil e quatrocentos"*, came back as *"2.400"*: correct, but in a
  notation the intent classifier's vocabulary does not cover.
- `voz_02` heard *"a minha dívida em"* as *"a minha dividência em"*. Another run heard
  *"a minha Dividend"*. Piper's synthesis is not bit-identical between runs, so the
  mishearing moves while the intent survives both times.

Both are the kind of figure-and-vocabulary damage `docs/voice.md` predicted from its error
model. Spoken figures still trigger the read-back on a call, exactly as on a voice note.

## What was verified, and how

| Claim | How it was checked |
|---|---|
| Notice first, unskippable; silence prompt then hang-up; handover and opt-out end the call; max duration | Unit tests, `tests/test_voice_call.py`, no LiveKit needed |
| Turns go through the orchestrator as `audio_received` labelled `voice` | Unit test spying on `Orchestrator.submit` |
| The call record holds the transcript, events and timings; erasing audio keeps the transcript | Unit tests |
| Whisper, the agent and Piper produce the measured turn on real synthesised audio | `make call-bench`, 18 lines |
| A call's reply fits its budget and quotes only the passages; a chat's is unchanged | `tests/test_spoken.py`; `make call-bench` reports reply length against the target and re-measures the speaking rate |
| The whole loop over WebRTC: speech in through a LiveKit room, reply audible back out | `make call-bench ARGS="--live"`: a bot participant speaks into a real room and times the first audible frame of the reply, 18 lines |
| The page starts a call: the token is minted, the browser and the agent join the same room | Done in an automated browser. The browser joined; that browser then refused microphone access, so the spoken leg of the browser call was **not** exercised there |

**Not verified by a machine: a person speaking into the page.** Run `make call`, open
`/call/demo` in a browser with a microphone, press *Ligar*, and listen. The live benchmark
exercises everything behind the page, but not the browser's own capture path:
echo cancellation, noise suppression, and the browser's choice of microphone.

## Limitations, named

- **No barge-in.** While the agent speaks, the customer's audio is dropped. With replies cut
  to a median 12 s this costs a correction at most one sentence late; with the 100-second
  replies of the first build it was the call's main defect. When barge-in is added, the
  recording notice must remain the one utterance it cannot interrupt.
- **The target turn length is a choice.** Twenty seconds is reasoned, not measured on
  callers. Only the conversion to words is measured. A study with people on the line would
  set the target; the derivation would then carry it through unchanged.
- **Energy endpointing.** It works on a headset in a quiet room. Background noise above
  −45 dBFS keeps a turn open, and a speaker who pauses for more than 700 ms is cut off.
  Silero VAD plus a learned end-of-turn model is the upgrade, and it touches only
  `endpointing.py`.
- **No SIP / PSTN.** LiveKit's SIP bridge would put the same agent on a real number without
  changing it. That is the reversibility argument in `docs/channels.md`, and it is unbuilt.
  Carrier codecs (8 kHz G.711) would also make recognition worse than measured here.
- **No outbound dialling.** `deliver` holds a message to be spoken on the customer's next
  call, after the notice. It does not place a call, for the same reason the chat has no push.
- **Cross-channel divergence is zero by construction, not by measurement.** The call reuses
  the chat's routing, so the decisions cannot differ for the same transcript. The spec's
  harness, which must be shown to catch a planted divergence before its zero is believed,
  is still owed. It would mostly be testing the reuse.
- **The call page loads the LiveKit browser SDK from a CDN.** Only the page's script comes
  from there; audio goes browser → local LiveKit → agent. Vendoring the script would make
  the demo work fully offline.
- **In-memory call registry.** The transcript persists through the session store, but the
  `Call` objects (state, events, timings) live in the server process. Each call's full
  record is also written to `data/calls/records/<call_id>.json` when the call ends.
