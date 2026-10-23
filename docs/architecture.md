# Multi-agent architecture

How the three agents are coordinated, what they exchange, and which rules are enforced
centrally rather than trusted to each agent.

Run `collection demo` to watch a mix of events routed end to end.

## Layers

```
              ┌──────────────────────────────────────────┐
              │              ORCHESTRATOR                │
              │  routes · prioritises · enforces opt-out │
              │              · records                   │
              └──────────────────────────────────────────┘
                   │              │               │
        ┌──────────┘              │               └──────────┐
        ▼                         ▼                          ▼
┌────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│   RESPONSIVE   │      │     PROACTIVE    │      │     PREDICTIVE     │
│  intent + RAG  │      │  rules engine    │      │  ranking via API   │
│                │      │                  │      │  POST /score       │
└────────────────┘      └──────────────────┘      └────────────────────┘
        │                         │                          │
        ▼                         ▼                          ▼
┌────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│  STT · channel │      │  channel · tmpl  │      │  features · model  │
└────────────────┘      └──────────────────┘      └────────────────────┘
```

All three agents are real now. The coordination was delivered first, as stubs, so it could
be reviewed and tested before the agents existed — and each stub was then replaced in place
without the orchestrator changing, which is the property that layer was built for. Generation is
real too: the responsive agent answers through an LLM held to the retrieved passages, with
the templated responder kept as the control arm (see
[`docs/llm-responder.md`](./llm-responder.md)). Voice notes are real too
([`docs/voice.md`](./voice.md)); what remains unbuilt is the real-time voice channel.

Below the agents sits a fourth seam: the **channel contract**
([`docs/channels.md`](./channels.md)). It exists because the two integrations the RFC named
— the WhatsApp Business API and switched telephony — were replaced by a simulated chat
channel and a real-time voice channel, on the stated ground that they add transport rather
than a decision, and that the architectural risk lives in the seam instead. The argument,
and what the substitution costs, is in *Mudanças de escopo* in
[`cronograma.md`](../cronograma.md). A channel owns transport, identity and the thread;
it never decides what to say, whom to contact or what the customer said. That used to be a
convention and is now a `Protocol` plus a test that parses every module under
`collection.agents` and fails if one of them imports a channel.

The embeddable web chat ([`docs/web-chat.md`](./web-chat.md)) is the first implementation:
it submits `message_received` and `audio_received` events to the orchestrator, never
calling an agent directly, so it inherits the priority rule, the cascade guard, the audit
trail and the opt-out check for free. It carries voice notes, delivery status and threads
the proactive agent starts — the last of these *seeded* rather than pushed, since there is
no push transport. Conversations carry a thread: the channel puts the transcript on the
event, and the agent resolves follow-ups against it
([`docs/multi-turn.md`](./multi-turn.md)).

## The event contract

Every interaction is an immutable `Event`. Agents never mutate what they receive; they emit
a new event carrying `caused_by`, so the log reconstructs why any contact happened — which
the LGPD requires and the evaluation needs as evidence.

| Event | Meaning | Priority |
|---|---|---|
| `message_received` | Customer wrote in | responsive |
| `audio_received` | Customer sent a voice message, transcribed before it is answered | responsive |
| `opt_out_requested` | Customer asked not to be contacted | responsive |
| `escalated_to_human` | Agent handed the conversation over | escalation |
| `overdue_detected` | A receivable passed its due date | proactive |
| `risk_detected` | The predictive module flagged a not-yet-due receivable | proactive |
| `contact_scheduled` | The rules engine cleared a contact, and the message was written and checked | proactive |
| `contact_sent` | A message went out — produced by the chat channel when it seeds a scheduled contact | proactive |
| `reply_sent` | The system answered the customer | proactive |
| `suppressed` | A contact was blocked by a rule | proactive |

## The priority rule

Ordering is `(priority, created_at)`: **responsive → escalation → proactive**, and within
a priority the longest wait goes first.

A customer who writes in always outranks an outbound campaign, however long that campaign
has been queued. Someone who reached out is already engaged, and making them wait behind a
batch job is both worse service and a worse outcome — a queued campaign loses nothing by
waiting a few seconds, while a waiting person may simply leave.

## What the orchestrator enforces

Deliberately few responsibilities. Everything about *what to say*, *whom to prioritise*
and *which channel* belongs to an agent; the orchestrator only guarantees:

1. **Routing** — each event reaches the agent that declares it, and an event nobody handles
   is recorded rather than silently dropped.
2. **Priority** — via the queue, as above.
3. **Opt-out** — checked *before any agent runs*. A customer who asked not to be contacted
   must not be reachable through a bug in an agent, so the check cannot live in the agents
   (CDC art. 42 and the LGPD right to object). An opt-out registered mid-run suppresses
   outbound events still queued for that customer.
4. **Audit** — every processed, produced, suppressed and unrouted event is recorded in the
   run's `Trace`.

One deliberate asymmetry: an opted-out customer who writes in **is still answered**.
Opt-out stops outbound collection; it is not a refusal of service, and refusing to reply
would be worse service for no legal gain.

## Safeguards

- **Cascade depth** is capped, so two agents that trigger each other cannot loop forever.
- **Deduplication** — the same event id cannot be queued twice.
- **Bounded runs** — `run(max_events=…)` processes a slice and leaves the rest queued, so a
  cycle cannot starve anything else.

## What is intentionally not here yet

The queue is in-memory and synchronous. Its job in this work is to make the scheduling
policy explicit and testable; swapping it for Redis or SQS means implementing `push`/`pop`,
because the policy lives in the queue's ordering rather than in the transport. The same
applies to the agents: the contract is `handle(event) -> list[Event]`, and nothing else.
