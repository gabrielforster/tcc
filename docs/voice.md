# Voice notes: hearing a customer, and knowing what not to believe

A large share of inbound messages in Brazilian collection channels are not typed. The
customer holds the button and talks, and WhatsApp delivers an OGG/OPUS clip. The event
contract already assumed this — `audio_received` has been in `EventType` since the
orchestrator landed, and `ResponsiveAgent.handles` has declared it ever since — but
nothing produced text from a clip, so an audio event reached the agent, found no `text`
in its payload and escalated as an empty message. This module closes that gap.

## The pipeline, and why it is in three pieces

1. **`voice/audio.py` — the file.** Probes the clip with `ffprobe`, refuses the containers
   and codecs nobody sends, refuses anything under 0.4 s (a misfired button) or over
   120 s (that is a call, not a question), and decodes to the 16 kHz mono PCM every
   recogniser expects. None of this loads a model, so a clip can be rejected cheaply and
   the rejection is a testable decision rather than a failure inside a provider.
2. **`voice/transcribe.py` — the recogniser.** A `Transcriber` interface with two
   implementations, the same split as every other provider seam here: `WhisperTranscriber`
   is the real one and `ScriptedTranscriber` returns the labelled transcript, so tests and
   CI exercise everything downstream of recognition without a model on disk.
3. **`voice/degrade.py` and `voice/evaluate.py` — the measurement.** Explained below.

Whisper runs **locally**, via `faster-whisper` on CPU. That is a privacy decision before
it is a performance one: a voice note is personal data under the LGPD, and sending
customer audio to a third-party recognition API is a transfer the operation would have to
disclose and justify. Running the model in-process removes the question. The cost is
latency, which the evaluation reports rather than hides.

## The measurement problem, and how it is turned around

Every other evaluation in this project compares two arms on the same input. This one
cannot. The control transcriber returns the reference transcript, so it scores perfectly
by construction — and a real recogniser needs audio, which this repository does not and
must not contain. Measuring word error rate against a control that cannot be wrong would
be theatre.

So the question is inverted. Instead of asking how often recognition is wrong, the sweep
**fixes** the error rate and asks what survives it. `voice/degrade.py` corrupts the
labelled references word by word with the five mistakes a recogniser actually makes in
this domain — lost accents, confused domain words, swapped numerals, dropped function
words, transcribed hesitations — deterministically for a given seed. `voice/evaluate.py`
then measures, at each rate, the word error rate, whether the message still classifies the
way the clean transcript did, and whether the figures the customer actually said are still
there.

A corruption rate is the probability that a given word is touched, so it is an upper bound
on the resulting word error rate, not the rate itself. Both columns are in the report for
that reason: the corruption rate is the knob, the WER is what it is worth.

Two deliberate choices in the measures:

- **Intent agreement, not intent accuracy.** The lexical classifier gets 15 of the 18
  labelled messages right on clean text. Scoring degraded transcripts against the *label*
  would charge the classifier's own three errors to the recogniser. Agreement — does the
  degraded transcript classify the way the clean one did — isolates what transcription
  broke. Accuracy is reported too, in the next column, so the classifier's ceiling is
  visible rather than concealed.
- **Figure survival counts whole tokens, over the messages that have figures.** `dez`
  inside `dezoito` is not a surviving `dez`, it is precisely the confusion that must be
  caught. And messages with no figure are excluded rather than scored a trivial 1.0, which
  would dilute the measure towards exactly the reassurance it exists to deny.

## The result

| corruption | wer | intent agreement | figures intact |
|---|---|---|---|
| 0.00 | 0.000 | 1.000 | 1.000 |
| 0.05 | 0.021 | 0.967 | 0.975 |
| 0.10 | 0.046 | 0.967 | 0.958 |
| 0.20 | 0.068 | 0.956 | 0.908 |
| 0.30 | 0.099 | 0.856 | 0.867 |
| 0.40 | 0.151 | 0.833 | 0.750 |

The full table, with character error rate and the unknown-intent rate, is regenerated into
[voice-report.md](./voice-report.md) by `make voice`.

**The finding is the gap between the last two columns.** At a word error rate of 0.099 —
around what a small Whisper checkpoint reports for pt-BR, and a transcript that reads
perfectly well — intent still agrees 86% of the time, but one spoken figure in seven is
already wrong. Intent is redundantly encoded: "olha eu queria parcelar a minha dívida em
seis vezes dá pra fazer" says *negotiation* five or six different ways, and losing any one
of them changes nothing. An amount is said once. When recognition breaks it, nothing
downstream can tell: `mil duzentos e cinquenta` misheard as `meu duzentos e cinquenta`
produces a transcript that reads fine and an offer against the wrong debt.

## What the agent does about it

Two rules, both in `ResponsiveAgent`:

1. **A transcript below `MIN_CONFIDENCE` is not answered.** It escalates with
   `reason="audio_unintelligible"`, and the transcript travels on the event so the person
   picking it up sees what the system thought it heard. A low-confidence transcript is
   worse than none, because it reads like a message and routes like one.
2. **A figure heard in audio is read back, never acted on.** `needs_confirmation` detects
   an amount or an instalment count in either notation — `R$ 1.250,00` and `mil duzentos
   e cinquenta` — and the reply is prefixed with `Entendi: "…". Confirma que é isso?`. On
   the labelled set that fires on 44% of messages. The same words *typed* are not read
   back: this is a response to recognition risk, not to the word `reais`.

The second rule is the one the sweep paid for. The confidence threshold alone would not
have caught it — figures corrupt at error rates where the recogniser is still confident
and the intent is still right.

## Running it

```bash
make voice                            # the sweep; no model, no audio, no network
make voice ARGS="--audio note.ogg"    # transcribe one clip with Whisper
uv sync --extra voice                 # installs faster-whisper, ~200 MB of wheels
```

CI runs `collection voice` on every pull request. It needs neither a model nor audio, and
it fails the build if the control arm stops reproducing the references exactly. Worth being
precise about what that catches: at zero corruption the transcript *is* the reference, so
the word error rate and the intent agreement are 0 and 1 by construction. The column that
can actually fail is figure survival, and it fails when a labelled message claims a figure
its own transcript does not contain as a whole token. The gate is therefore an integrity
check on the labelled set — which is the part a future contributor is most likely to break,
and the part every number further down the sweep rests on.

## Known limitations

- **`MIN_CONFIDENCE = 0.55` is not calibrated.** It is the one number in this module that
  no measurement here supports: calibrating a confidence threshold needs real audio with
  known transcripts. It is set where Whisper's duration-weighted likelihood sits on a
  clean short pt-BR clip, and it is owed work.
- **The error model is a model.** Its families and weights come from observed Whisper
  output on Portuguese collection audio, but it is not a recogniser and the WER column is
  not a measurement of one. It answers *what breaks*, not *how often*.
- **No real recogniser has been run end to end here.** `collection voice --audio` exists
  and works; the numbers it produces on real customer audio are not in this repository,
  for the same reason the audio is not.
- **Eighteen labelled messages** is enough to show the ordering and not enough to put a
  confidence interval on any single cell.
