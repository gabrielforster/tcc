# Testing

What the suite proves, how much of the critical code it reaches, and how to run it.

## Running it

```bash
make up          # Postgres (pgvector) and MinIO; without them the db tests skip
make test        # the whole suite
make coverage    # the suite under coverage, then the critical-function report
```

Tests that need Postgres or MinIO **skip** when the services are unreachable, so the suite
runs on a laptop with nothing started. `COLLECTION_REQUIRE_DB=1` turns those skips into
failures; CI sets it, so a CI run cannot go green by skipping every database test.

### Resource limits

One run peaks near 1.8 GB and one core (measured 2026-11-08: 802 tests, 1 min 55 s). Several
at once, one per worktree, once pushed a 32 GB laptop into swap until it stopped responding.
Three limits keep that from happening again:

- **One suite at a time per machine.** `tests/conftest.py` takes a lock in the system's
  temporary directory; a second run, from any checkout or worktree, prints
  `waiting for another test run to finish` and starts when the first ends.
  `COLLECTION_TEST_LOCK=0` opts out.
- **A memory and CPU ceiling.** `make test` and `make coverage` run through
  `scripts/bounded.sh`, a systemd scope capped at 4G of memory, no swap, two cores and low
  priority. A run past the ceiling is killed with a message, instead of the machine
  thrashing. Raise the limits with `TEST_MEMORY=6G TEST_CPU=400% make test`, or remove them
  with `TEST_BOUNDED=0`. Without systemd (macOS) only the low priority applies.
- **Thread pools capped at 2** (`COLLECTION_TEST_THREADS` raises it), so numpy, torch and
  the boosters do not each start a thread per core.

`uv run pytest` directly still gets the lock and the thread cap, not the ceiling.

No test touches the network. The language model, the speech recogniser and the embedding
model each sit behind an interface, and the tests use a scripted or fake implementation:
the case that matters (the API timing out, the model refusing, the recogniser mishearing a
figure) cannot be summoned on demand from a real service.

## Coverage of the critical functions

The schedule's target is **at least 80% of the critical functions covered**. That is a claim
about functions, not lines: a module at 90% line coverage can still hide an untested
function behind a long, well-tested one. `scripts/critical_coverage.py` reads coverage.py's
per-function breakdown and reports, for each group of critical modules, the share of
functions the suite executes. It exits non-zero below 80%, for any group or overall.

"Critical" is a fixed list in that script: the schedule's own (parser, features, rules
engine, intent, templates), plus the orchestrator and the responder, which every flow
passes through, plus the two external-service seams (the LLM client and the speech
recogniser), whose failure paths decide whether a customer gets an answer or a person.

Measured on 2026-11-07, re-run on `master` on 2026-11-08 with the same result: 802 tests,
Postgres and MinIO up (no skips). *Before* is `master` on 2026-11-06, before these tests
were added. Two groups have grown since: the responder gained two functions with the
spoken reply (14 → 16) and the service seams gained the OpenAI-compatible client (27 → 33),
so their
*before* column is over the smaller set — the newcomers arrived with their tests.


| Group | Modules | Functions | Executed (before) | Executed (now) | Fully covered | Lines (before → now) |
|---|---|---:|---:|---:|---:|---:|
| Parser / ingest | `data/ingest`, `data/anonymize`, `rag/documents`, `rag/chunking` | 33 | 90.9% | **100%** | 87.9% | 92.4% → 97.6% |
| Features | `features/build`, `features/preprocess` | 9 | 77.8% | **100%** | 100% | 88.1% → 100% |
| Rules engine | `rules/policy`, `rules/window` | 9 | 100% | **100%** | 100% | 96.6% → 100% |
| Intent | `agents/intent` | 4 | 100% | **100%** | 100% | 100% → 100% |
| Templates + compliance | `outreach/brief`, `outreach/compose`, `outreach/compliance` | 26 | 92.3% | **100%** | 100% | 98.6% → 100% |
| Orchestrator | `agents/orchestrator`, `agents/queue`, `agents/events`, `agents/proactive` | 22 | 86.4% | **100%** | 100% | 97.9% → 100% |
| Responder | `agents/responsive`, `llm/responder`, `llm/prompt` | 16 | 100% | **100%** | 93.8% | 99.5% → 99.5% |
| Service seams | `llm/client`, `voice/transcribe`, `voice/audio` | 33 | 85.2% | **100%** | 84.8% | 77.2% → 96.1% |
| **All critical** | | **152** | **90.3%** | **100%** | **93.4%** | |

The whole package is at 84.3% line coverage (85.4% before the experiment and the voice
call landed; both brought command-line and LiveKit wiring the suite does not drive). Most
of what is not covered is
`cli.py` (wiring for the `make` targets) and the report writers (`eda/report.py`,
`models/explain.py`, `models/report.py`), which produce the generated documents and are
checked by reading those documents, not by the suite.

The fourteen critical functions that had never run before this measurement are worth
naming, because several were claims the project was making without evidence: the **PDF and
DOCX parsers** (the schedule names both formats; only Markdown and CSV had ever been loaded),
the **real Whisper and Anthropic backends** (only their scripted stand-ins had run), and the
`extract` half of ingestion. Each is now exercised: the PDF from a hand-built file, the DOCX
from `python-docx`, and the two backends against fake SDK modules that pin what is sent and
how each failure becomes the one error type the agent falls back or escalates on.

## What the integration tests prove

`tests/test_flows.py` runs each of the three flows end to end through the orchestrator,
from the event that opens it to the state a customer or an operator would see. The module
tests pin each piece where it lives; these show the pieces still meet.

**Proactive.** A detected receivable passes the rules engine, is written by the composer,
passes the compliance check and waits in the customer's thread with a tracked delivery,
and the customer's reply is answered by the responsive agent and marks the contact
answered. Every way it can be stopped is shown stopping it, with a reason:

- outside the legal window (05:00 local): suppressed, with the time it would be allowed;
- opted out before scheduling: suppressed by the orchestrator before any message is written;
- opted out *between* scheduling and delivery: the waiting message is never shown, its
  delivery never advances past `enviada`, and the next run does not schedule it again;
- a composed message that threatens and invents a figure: escalated to a person, not sent.

**Responsive.** A question is classified, answered from the knowledge base with citations
and tracked, inside the 5 s latency requirement (on the template arm). A dispute is handed
to a person. A message nothing in the knowledge base answers is never guessed at: it goes
to a person every time, and the third in a row is recorded as a pattern
(`unclear_repeatedly`) rather than a one-off (`no_grounding`). An opt-out typed in chat
suppresses the next proactive contact, because both flows share one opt-out set.

**Escalation handoff** (on the durable record, needs Postgres). A dispute typed in chat
leaves the thread `ESCALATED` with `holder = queue`. It is the only entry in the
operators' queue, a named operator claims it and it leaves the queue, and resolving it
closes it and keeps who handled it. A resolved thread cannot be resumed from another
process; the customer's next contact opens a new thread, held by the agent again.

**External-service seams.** A model outage still answers the customer, from the template,
and the failure is recorded rather than hidden. A working model's text is what the customer
sees, credited to the passages it was shown. A recogniser that crashes, or that is unsure of
what it heard, hands the voice note to a person, and an unsure guess travels with the
escalation but is not written into the thread as something the customer said. A voice note
the recogniser hears is answered like typed text.

## What is not tested

- **Real services.** No test calls a real language model, a real recogniser or a real
  embedding model. The comparisons in `docs/responder-comparison.md`, `docs/outreach.md` and
  `docs/voice-report.md` are where real backends are measured, and they record the model
  and the date.
- **Concurrency and load.** Nothing here runs turns in parallel; the "≥ 100 concurrent
  interactions" indicator is a separate measurement.
- **The confidence threshold.** `MIN_CONFIDENCE` is tested as a rule, not calibrated:
  calibrating it needs real audio with known transcripts, which this repository does not
  contain (see `docs/voice.md`).
