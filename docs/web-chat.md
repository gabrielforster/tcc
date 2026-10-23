# The embeddable web chat channel

A chat widget that drops into any page with one `<script>` tag and talks to the responsive
agent. It is the first real channel the system has — until now the agent could only be
reached from a Python process.

## It is an adapter, not a second agent

Every message becomes a `message_received` event submitted to the **orchestrator**, which
routes it to the responsive agent. The channel never calls the agent directly.

```
browser → POST /chat/sessions/{id}/messages → ChatChannel
                                                   ↓ message_received
                                              Orchestrator ── opt-out check (before any agent)
                                                   ↓
                                              ResponsiveAgent → reply_sent | escalated_to_human
                                                   ↓
                                              ChatReply → JSON → widget
```

That indirection is the design, not ceremony. Routing through the orchestrator means the
web chat inherits the global rules for free: responsive priority, the cascade guard, the
audit trail, and an opt-out enforced *before any agent sees the event*. A channel that
called the agent directly would pass its own tests and quietly opt out of all four — and
the compliance story would be true of the demo and false of the product. It also means
WhatsApp is another adapter over the same contract rather than a second copy of the
conversation logic.

`test_a_message_goes_through_the_orchestrator` is the guard: it asserts the reply carries
the events the run produced, which is only true if the orchestrator ran.

## Identity: the browser is never believed

If the page could name the customer, anyone could name any customer. So:

- The host application's **backend** mints a token with `issue_token(customer_id)`, signed
  with HMAC-SHA256 under the existing `ANONYMIZATION_SALT`. The browser never sees the
  secret and cannot mint one.
- The widget presents the token; `verify_token` checks the signature with
  `hmac.compare_digest` and the expiry. A plain `==` leaks the signature one byte at a
  time to anyone willing to measure.
- **Without a token the session is anonymous**, and that is a supported mode rather than a
  degraded one. The knowledge base is policy, FAQ and instalment tables — none of it
  customer-specific — so an anonymous visitor asking *"quais são os juros por atraso?"*
  gets a correct answer and nobody's data. Anonymous ids are random, never derived from an
  IP or a fingerprint: an id that could be recomputed from the visitor would not be
  anonymous.

Reusing `ANONYMIZATION_SALT` rather than adding a second secret is deliberate. Both are
process-wide secrets that must never be committed and whose rotation invalidates what came
before; splitting them doubles what can go wrong for no gain in isolation.

## Limits, and what each one is for

| Limit | Value | Why |
|---|---|---|
| Session TTL | 30 min idle | Long enough to read and reply; an abandoned tab stops holding a customer id |
| Turns | 40 | A thread this long needed a person well before it got here |
| Rate | 20 msg/min per session | A public endpoint gets hammered |
| Message | 1000 chars | Longer is a paste or a probe, not a question |

## Status codes the widget acts on

A widget on someone else's page has only the status code to distinguish "try again" from
"start over":

| Code | Meaning | Widget does |
|---|---|---|
| 401 | Token did not verify | Host must mint a new one |
| 404 | Session unknown or expired | Tells the customer to reload |
| 409 | Turn cap reached, a person has it | Closes the thread |
| 422 | Empty or overlong message | Says so without a round trip |
| 429 | Rate limited | Backs off, keeps the session |

## The widget

One file, no build step, no dependencies — `src/collection/web/widget.js`, served from
`/chat/widget.js`.

```html
<script src="https://host/chat/widget.js" data-api="https://host" defer></script>
```

| Attribute | Default | |
|---|---|---|
| `data-api` | same origin | Where the API lives |
| `data-token` | none | Signed identity token from your backend |
| `data-title` | `Atendimento` | Header text |
| `data-accent` | `#1d4ed8` | Accent colour |
| `data-open` | `false` | Open on load |

Two constraints shaped it:

**No framework.** This gets pasted into someone else's page. A widget that needs a bundler,
or a version of React that disagrees with the host's, is a widget that does not get
embedded.

**Shadow DOM.** Everything renders inside a shadow root, so the host page's CSS cannot
reach in and the widget's cannot leak out. Widgets that skip this look fine in the demo and
break on the first real site with a global `button { }` rule — which is why
`/chat/demo` sets exactly that rule, as a visible check that the isolation holds.

**Replies are inserted as text nodes, never as HTML.** The reply is generated text quoting
a knowledge base; treating it as markup would hand whoever can influence either one a
script tag on the host's page.

## Transparency

The thread opens by saying it is automated, before the customer types anything. An
automated collection contact passing itself off as a person is the kind of thing CDC
art. 42 exists to prevent, and the widget header repeats it for the length of the
conversation.

Opt-out works mid-conversation: *"parem de me mandar mensagem"* is recognised, recorded on
the channel and propagated to the orchestrator, so every later outbound event for that
customer is suppressed — including proactive ones arriving through a different channel.
A customer who has opted out and then writes in is still answered: opt-out stops outbound
collection, it does not refuse service.

## Running it

```bash
make serve          # http://127.0.0.1:8000/chat/demo
```

With `ANTHROPIC_API_KEY` set the chat answers with the LLM responder; without it, the
template. That fallback is why the demo works on a fresh clone — and why the default
matters: a templated answer quotes whole passages, so a customer asking about interest
gets the entire FAQ in one bubble.

CORS is open (`*`) by default, because an anonymous FAQ chat has nothing to leak. **A
deployment that issues identity tokens should narrow `CHAT_ALLOWED_ORIGINS` to its own
domains.**

## Known limitations

**~~Single turn.~~** Resolved: the channel now puts the session transcript on the
`message_received` event and the agent resolves follow-ups against it — see
[`docs/multi-turn.md`](./multi-turn.md). The greeting is dropped on the way, since nobody
typed it.

**Sessions are in-memory.** They do not survive a restart and do not span processes. Redis
is already in `docker-compose.yml` and `SessionStore` is the seam it would replace.

**No delivery guarantees.** HTTP request/response only — no typing indicators from the
server, no push, no read receipts. WebSockets or SSE would change the transport without
touching the channel.
