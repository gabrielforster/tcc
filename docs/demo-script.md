# Live demo script — defense, 03/12/2026

About three minutes, inserted after slide 12 of `pitch/defesa.html` (the cue is in
`pitch/roteiro-defesa.md`). Everything below runs on the presenting laptop with no network:
the chat demo needs nothing started, the call needs only the LiveKit container.

The rule for a live demo: **rehearse it on the presenting machine the day before, leave every
process running, and have the fallback open in a browser tab before you start.**

## The day before

```sh
make setup                      # uv sync --all-extras: the chat and call need the extras
make voices                     # once: the two Piper pt-BR voices (~120 MB) into data/voices
docker compose pull livekit     # so `make call` does not download an image on stage
```

Then run the whole demo once, end to end, on the same machine and the same browser. Grant the
browser microphone permission for `http://127.0.0.1:8100` during the rehearsal — the
permission prompt is the most likely thing to stall on stage.

`make up` also starts Postgres, Redis and MinIO; the demo does not need them. Leave
`DEFAULT_TENANT_KEY` empty in `.env` so the chat serves the in-memory demo tenant: it starts
with nothing else running and cannot be broken by the state of a database.

## Before the session (15 minutes early)

Open three terminals and start both servers before the talk:

```sh
# terminal 1 — the chat, with the proactive agent's contacts seeded at startup
make serve ARGS="--seed-demo"   # http://127.0.0.1:8000/chat/demo

# terminal 2 — the voice call
make call                       # starts LiveKit, then http://127.0.0.1:8100/call/demo
```

Terminal 1 must print, among the startup lines:

```
seed: 2 seeded, 1 suppressed, 1 for another channel, 0 escalated (4 receivables considered)
```

Warnings that the `propensity` and `default` models could not be loaded are expected when
Postgres is not running: they only mean `POST /score` answers 503, which the demo does not
use. (To show `/score` as well, run `make up && make migrate && make seed`, then
`COLLECTION_TENANT=desenvolvimento make pipeline`, before starting the server.)

Open in browser tabs, in this order: the deck, `http://127.0.0.1:8000/chat/demo`,
`http://127.0.0.1:8100/call/demo`, and the fallback recording.

## On stage

### 1. The chat — about 2 minutes

| Step | Do | Say / point at |
|---|---|---|
| 1 | Open `/chat/demo`. | The thread opens saying it is **automated**, before anyone types (CDC art. 42). The first message was **written by the proactive agent**, passed the compliance checker, and waited in the mailbox until the widget opened — opt-out is checked again at this moment. |
| 2 | Type `quais são os juros e a multa por atraso?` | The answer **cites its source document**. It is the template responder: faithful, but long in a chat bubble. The chat states no length budget, so its replies are unchanged; only the call asks for a short one. |
| 3 | Type `consigo parcelar em 6 vezes?` | A negotiation answer from the instalment table, within the authority limits. |
| 4 | Type `eu nunca contratei esse servico` | A **dispute always goes to a person** (`knowledge/limites-de-alcada.md`): the thread escalates and says so. |
| 5 | *(optional)* In terminal 3: `COLLECTION_TENANT=desenvolvimento make queue` | Only with a database-backed tenant; with the in-memory demo, skip it and describe the queue → claim → resolve flow instead. |

If time allows, show opt-out in a fresh session: `parem de me mandar mensagem` is recognised,
recorded and propagated, and every later outbound contact for that customer is suppressed.

### 2. The call — about 1 minute

| Step | Do | Say / point at |
|---|---|---|
| 1 | Open `/call/demo`, press **Ligar**. | The **recording notice plays first and cannot be skipped**: speech during it is discarded. |
| 2 | Say: "quanto eu tô devendo?" | Whisper and Piper run **locally**; the turn takes about 2 s. |
| 3 | Let the answer play to the end (about 12 s), then hang up. | Slide 12's fix, live: the call declares a 43-word budget and the responder reads the one section that answers, verbatim. Before the fix this same answer ran for over a minute and a half. |

## Fallback — if anything fails live

Do not debug on stage. Say "vou mostrar a gravação da mesma demonstração" and switch to the
recording tab.

- **Chat fails to load or answer:** play the recorded walkthrough of the chat demo (proactive
  message → answer with citation → escalation), or fall back to the screenshots on slides 8
  and 10, which show the same three moments.
- **The call fails** (microphone permission, LiveKit not up, no audio): skip it. Slide 12
  already carries the measured turn-time table, which came from `make call-bench ARGS="--live"`
  through a real LiveKit room; say that the browser-microphone path is exactly the one the work
  lists as not verified.
- **Anything else:** go back to the deck at slide 13. The demo is illustration; every result is
  on the slides.

Record the fallback the day before, on the presenting machine, by running the chat and call
steps above with a screen recorder.

## Showing the experiment

Do **not** run `make experiment` on stage: it takes about 15 minutes at two threads, and it
**rewrites** `docs/experiment-results.md` and `docs/images/experiment-capacity.png`. If the
panel asks to see it run, a two-replication run takes about a minute:

```sh
uv run collection experiment --replications 2
git restore docs/experiment-results.md docs/images/experiment-capacity.png   # put the 30-replication results back
```

The same applies to `make concurrency`, which rewrites the concurrency table in the same file;
restore it the same way afterwards.

## After the session

```sh
# Ctrl-C both servers, then
docker compose stop livekit
git status    # must be clean: nothing the demo ran should have changed a tracked file
```
