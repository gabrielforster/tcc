# A tenant-scoped platform, a durable record, and an internal operations console

Design document — 2026-10-25.

## 1. Intent

Two defects started this, both visible in a single screenshot of the chat widget:

1. **The agent quotes instead of answering.** The customer asked about interest and late
   fees and received the retrieved passages verbatim, headed *"Encontrei estas
   informações:"*. That is the `TemplateResponder` doing exactly what it was built to do —
   quote, never invent — but it was never meant to be what a customer reads. Worse, the
   same path has quoted `limites-de-alcada.md` to a debtor: the 20% automatic discount
   ceiling, the twelve-instalment limit, the R$ 50,00 minimum, and the list of situations
   that trigger supervisor approval. A debtor who reads that knows exactly how far to push
   before a human appears.

2. **The sources are shown to the wrong audience.** Every answer carries a `Fonte:` footer
   in the bubble. The citation is valuable — it is how anyone later judges whether the
   answer followed from the evidence — but it is an *internal* artifact. The customer
   should see an answer. The company should see what the answer was built from.

Neither fix is deep on its own. What makes this a project is where the second one leads:
"the company should see" has no *where*. There is no internal interface, no login, no
roles, no durable record, and no tenant. Conversations live in a dictionary that dies with
the process, and the knowledge base is one directory of Markdown shared by everything.

So this document describes the platform those two fixes imply: a tenant-scoped domain, a
durable conversation record carrying the evidence behind every answer, and an internal
console where operators take over escalated conversations and analysts judge how the agent
is performing.

This is specified as a product, not as a thesis artifact. Where a decision here affects the
paper, §9 says so, but no decision in this document was made for the paper's benefit.

## 2. Goals

1. **A customer never sees a knowledge document.** Not quoted, not cited, not in a footer,
   not in a degraded path. Answers are written; evidence is recorded.
2. **Every answer is reconstructable.** For any turn, months later, it is possible to see
   which passages the agent retrieved, from which documents, *at which version of those
   documents*, with what similarity, and what the agent then said.
3. **The system degrades loudly.** When generation is unavailable, conversations go to
   people and the organisation is told, in one place, that it is happening.
4. **Conversations survive restarts.** Sessions, transcripts, deliveries and retrieval
   records are rows, not dictionaries.
5. **A tenant is a hard boundary.** Customers, receivables, conversations and knowledge
   belong to exactly one tenant, and no query returns another tenant's row. Models are
   the platform's, not a tenant's (§6.12).
6. **An operator can take over a live conversation** and the customer sees their messages
   arrive without reloading.
7. **An analyst can judge the agent** from the record, not from logs.
8. **Nothing lives on disk.** Rows and vectors in Postgres, files in object storage,
   ephemeral state in Redis.

## 3. Non-goals

These are out of scope but compatible; nothing here should be made harder to add later.

- Customer-facing accounts. The customer side stays token-identified or anonymous.
- A tenant self-service signup flow. Tenants are created by an administrator.
- Billing, usage metering, plan limits.
- The real-time voice channel (LiveKit). It is still owed from the previous design and is
  unaffected by this one, except that it will write the same record.
- WhatsApp or any carrier integration. Still replaced, per the 2026-10-22 design.
- Horizontal scaling of the websocket layer. One process is assumed; Redis is in the
  compose file when that stops being true.

## 4. Anti-goals

Things that would make this a failure even if every feature worked.

- **A tenant filter that is a convention.** If isolation depends on each handler
  remembering a `WHERE tenant_id = ...`, it will leak, and the leak will be a cross-tenant
  disclosure of debt data. It has to be structurally hard to write an unscoped query.
- **A "temporary" second source of truth.** Writing conversations to Postgres *and*
  keeping the in-memory store as the thing the channel actually reads would produce two
  histories that disagree.
- **Showing the operator a reconstruction.** If the console re-runs retrieval to show
  "what the agent saw", it is showing today's index, not the evidence. The record is
  written at retrieval time or it is not evidence.
- **A console that invents numbers.** Dashboards show measured quantities. A metric that
  cannot be computed from the record does not get a tile with a plausible figure in it.
- **Deleting the template responder.** It is the control arm the generation comparison is
  measured against. It stops being customer-facing; it does not stop existing.

## 5. Constraints

- **One backend, Python, FastAPI.** The internal API is a router in the existing
  application, not a separate service. Everything the console needs is produced inside this
  process; a second runtime's first job would be getting it back out over a network. The
  router is the seam to split on if that changes.
- **Postgres and Redis already exist** in `docker-compose.yml`, and SQLAlchemy and psycopg
  are already dependencies. Persistence is new tables, not new infrastructure.
- **pgvector is already the Postgres image.** The per-tenant knowledge index uses it.
- **Nothing the application depends on lives on disk.** Rows and vectors go to Postgres,
  files go to object storage, ephemeral state goes to Redis. A local path is a
  development convenience and never a source of truth. This adds S3-compatible object
  storage (MinIO in `docker-compose.yml`) to the stack.
- **Alembic for migrations.** New dependency; the project has no migration tool today.
- **The customer widget stays build-free.** It is pasted into someone else's page. The
  console is a separate application and is not bound by that rule.
- **The channel contract holds.** `Channel` implementations own transport, identity and
  the thread; they do not decide what to say or who may be contacted. Take-over is a
  channel-level capability, not an agent behaviour.
- **No real customer data in the repository.** Unchanged.

## 6. Decisions

### 6.1 Tenancy is a column, enforced in one place

Shared schema. Every domain table carries `tenant_id` with a foreign key to `tenants`.
Isolation is not left to call sites: all reads and writes go through a session-scoped
repository layer that binds a tenant on construction and injects the predicate, and a test
asserts that every mapped class carrying `tenant_id` is unreachable through any other path.

Schema-per-tenant was considered and rejected: cleaner isolation on paper, miserable to
migrate across fifty schemas, and it pushes the problem into operations rather than
solving it.

### 6.2 A tenant has a lifecycle, and chat is behind the gate

`PROVISIONED → INGESTING → READY → ACTIVE` (plus `SUSPENDED`).

A tenant that has just been created has no payment history, so the propensity and default
models have nothing to learn from and the knowledge base has nothing to answer from. Rather
than serve a degraded system, the tenant is not servable: channels refuse to open a
conversation for a tenant that is not `ACTIVE`, and the proactive agent schedules nothing.

To reach `ACTIVE` a tenant must have ingested its data and completed a pipeline run — the
`collection pipeline` command's work, per tenant — producing its receivables baseline,
its indexed knowledge documents, and scores for its customers from the platform model.
The pipeline imports and indexes the tenant's data; it does not train a model (§6.12).
The gate is checked in the channel, once, at session open.

### 6.3 Knowledge becomes versioned rows, not files

`knowledge/` stops being the source of truth. Documents are tenant-scoped rows with an
immutable **version** per revision: editing a document creates a new version and leaves the
old one addressable. Chunks and embeddings hang off a version, and retrieval records point
at the version they read.

This is what makes "what did it see?" answerable. Today a document edit silently rewrites
the history of every past answer that cited it.

The repository's own `knowledge/` directory becomes seed data for the development tenant.

### 6.4 The retrieval record is written at retrieval time

Per turn, the system stores every retrieved passage: the document, the document *version*,
the chunk, the similarity score, and the rank. Written when retrieval happens, by the
agent, as part of the turn — never reconstructed later.

It is the evidence behind the answer, and it is what the operator reads to answer quickly
and the analyst reads to judge faithfulness.

### 6.5 The customer-facing response carries no sources

The `Fonte:` line is removed from the template's composed text, `citations` is removed from
the customer-facing response schema, and the widget's source footer is deleted. Internally,
citations remain on `Answer` and in the record.

### 6.6 Generation unavailable means a person, and an announcement

The template responder stops being the customer-facing default. When generation is
unavailable — no API key, expired key, rate limit, timeout, transport failure — the
conversation escalates to a human with a plain handover sentence, and the system raises a
**provider outage** state: surfaced on the health endpoint, pushed to connected console
clients, and recorded in the audit trail. Not one event per failed call; one state change,
with a recovery when it clears.

The template responder keeps running in the evaluation harness, where it is the control
arm the LLM is compared against.

### 6.7 Authority limits become tenant policy

`limites-de-alcada.md` is a file encoding one organisation's rules: discount ceiling,
maximum instalments, minimum instalment value, and the situations that always need a
person. Those become tenant-scoped policy rows.

The agent **negotiates inside the ceiling**. Policy reaches generation as structured
parameters — a maximum discount, a maximum instalment count, a minimum instalment value —
so the agent may propose terms at or below them, and escalates anything outside them or
listed as always-escalate. What never reaches generation is the policy *document*: the
prose that states the ceilings as ceilings, names the escalation triggers, and explains the
reasoning. The agent offers 15%; it never says that 20% is the limit, that a limit exists,
or what makes a case escalate.

This is the real fix for the disclosure. Removing the quoting path stops the verbatim dump;
separating "terms I may offer" from "the document describing my authority" is what stops a
generated paraphrase from leaking the same thing.

### 6.8 Identity: internal users, sessions, roles, permissions

Internal users belong to a tenant and authenticate with email and password (Argon2),
receiving a server-side session in an HTTP-only cookie. Roles carry permissions rather than
being checked by name, so a new role is data:

- **operator** — view and take over conversations in their tenant.
- **analyst** — view conversations and dashboards; no take-over.
- **admin** — the above, plus managing users, knowledge documents and tenant policy.
- **platform admin** — creates tenants; crosses the tenant boundary deliberately and is
  audited whenever it does.

The customer API is unchanged and shares nothing with this.

### 6.9 Live updates and take-over run over websockets

One authenticated websocket per console client, subscribed to its tenant. It carries new
turns, conversation state changes, take-over events and the provider outage state.

Take-over makes a conversation **human-held**: the agent stands down for that thread, the
operator's messages are delivered to the customer as ordinary agent-role turns, and the
record marks who held it and when. Handing back is explicit.

This forces the customer widget onto a real-time transport, which was deferred as future
work in the channel contract work. Server-Sent Events are the choice there — the customer
side only needs server-to-client, and SSE survives proxies that mangle websockets. The
console, which is bidirectional, uses websockets.

### 6.10 The console is React, TypeScript and Vite

A separate application in its own directory, talking only to the internal API. A dashboard
with live updates and take-over is an application, not a page. This brings Node into a
Python repository; that is the price, and it is worth paying here and not for the widget.

### 6.11 What the console shows

- **Live conversations** — open threads for the tenant, state, who holds each, last turn.
- **Conversation detail** — the transcript, and per agent turn the retrieved passages with
  their document and version, the intent and confidence, the responder that wrote it, the
  latency, and the delivery status ladder. This is what the operator reads before replying
  and what the analyst reads to judge the answer.
- **Take-over** — claim, reply, hand back.
- **Dashboards** — measured quantities only: conversation volume, escalation rate and
  reasons, intent distribution, answer latency, delivery status distribution, retrieval
  score distribution, provider availability.

### 6.12 One platform model, per-tenant data

Models are **app-level**. There is one propensity model and one default-risk model, trained
by the platform on pooled data, versioned as platform artifacts. A tenant's pipeline run
imports and indexes *its* data; it does not train *its* model. Scoring is the platform model
applied to the tenant's receivables.

What is per-tenant is everything the model and the agent read: customers, receivables,
knowledge documents, retrieval, policy.

This removes per-tenant training, per-tenant artifact storage and per-tenant model
lifecycle from the whole project, and it is why §6.2's gate is about data being present,
not about a model existing.

### 6.13 Embeddings come from a platform model and live in Postgres

Retrieval today fits a TF-IDF vocabulary over whichever corpus is loaded at startup and
keeps the vectors in memory. Two consequences make it unusable under tenancy: the vectors
mean different things in different tenants because the vocabulary is fitted per corpus, and
every document edit invalidates every vector.

Retrieval moves to a fixed pretrained sentence embedding model — a platform model by §6.12,
one model for all tenants — with vectors written to `knowledge_chunks.embedding` in
pgvector when a document version is created, and similarity resolved by the database. A
document version's vectors are computed once and never recomputed; a new version is a new
set of rows.

Cost: a model dependency in the default install, and every retrieval number already
published in `docs/rag.md` and `docs/responder-comparison.md` is re-measured against the
new retriever. That re-measurement is tracked as part of the stage that lands it, not
deferred.

### 6.14 Disk is not storage

Everything the application reads or writes at runtime goes to a real store:

| Today on disk | Goes to |
|---|---|
| `knowledge/*.md` | `knowledge_documents` / `knowledge_versions` rows |
| in-memory / refitted vectors | `knowledge_chunks.embedding` (pgvector) |
| `data/raw`, `data/processed` parquet | object storage, with rows for the records the app serves |
| `models/*.joblib`, metrics, SHAP plots | object storage, keyed by platform model version |
| voice note audio and transcripts | object storage; the transcript text on the turn |
| session state, rate-limit counters | Redis |
| evaluation outputs | object storage, written by the harness |

The repository's `knowledge/` directory becomes seed material for the development tenant,
loaded by a command. CLI commands that today write a file write to object storage and print
the key.

### 6.15 Generation is multi-provider, and the choice is data

`LLMClient`, `LLMError` and the `CLIENTS` registry already make the provider replaceable;
what does not exist is more than one real implementation, a place to record which model
answered, and a way to choose without editing settings. All three are added.

**Clients.** `AnthropicClient` is joined by an OpenAI client, a Google client, and an
OpenAI-compatible client that takes a base URL — which covers OpenRouter, Groq, vLLM and
Ollama without a class each. Each SDK is a separate optional extra, imported lazily, in the
discipline `AnthropicClient` already follows: a clone with no extras installed and no keys
still runs the suite.

**Profiles, not settings.** A `llm_profiles` row names a provider, a model, max tokens and
temperature, for a purpose — the responder, intent classification, summarisation. The
platform owns the defaults; a tenant may point a purpose at a different profile. Changing
which model answers a tenant's customers is a row, not a deploy.

**Keys stay platform-level.** One set of provider credentials, held by the platform in the
environment or a secret store. Tenants select among configured providers; they do not bring
their own keys. Per-tenant credentials are a non-goal here — they need encrypted storage,
rotation and per-tenant billing, and none of that is in this design.

**Failover is ordered, and it is not a silent downgrade.** A purpose may list profiles in
order. On `LLMError` the next profile is tried; when every profile fails, §6.6 applies
unchanged — handover plus a provider-outage state. The outage state is keyed by provider,
so "OpenAI is down" and "everything is down" are different things on the health endpoint.
Failover never reaches the template responder; that path is closed by §6.6 and stays
closed.

**The record says who answered.** `turns` carries the provider and model alongside the
responder, because an answer's quality is not comparable across models and the analyst's
view is worthless if it cannot tell them apart. The evaluation harness reads profiles too:
the arms become the template control and one arm per configured profile.

**Embeddings are not part of this.** §6.13's embedder is one platform choice for all
tenants; changing it re-embeds every chunk, so it is a migration, not a setting.

## 7. Stages

They unblock each other in this order.

**Stage 1 — the record.** Alembic; models and migrations for tenants, internal users,
customers, receivables, knowledge documents and versions, chunks, conversations, turns,
retrieval records, deliveries; the repository layer with enforced scoping; the channel and
agent writing through it. It also lands the two storage moves, because they are the same
migration: the pretrained embedder with vectors in pgvector (§6.13), and object storage for
everything the pipeline writes to disk today (§6.14). Nothing is visible yet; everything
later depends on it.

**Stage 2 — the two fixes.** Sources out of the customer response and the widget; the
template responder out of the customer path; handover plus the outage state. Small once
stage 1 exists, because citations now have somewhere to live. *This is the stage that
stops the disclosure, and it should not wait for stages 3 and 4.*

**Stage 2b — providers.** The additional clients, the profile rows and resolution, ordered
failover, and provider-keyed outage state (§6.15). It follows stage 2 because it extends
the path stage 2 makes customer-facing, and it is small.

**Stage 3 — identity.** Tenants, internal users, sessions, roles, permissions, the tenant
lifecycle gate, and tenant policy replacing the authority-limits file.

**Stage 4 — the console.** The internal API router, the websocket layer, SSE on the
customer widget, take-over, and the React application.

Each stage gets its own implementation plan when it is reached. Stage 1 is specified deeply
enough here to plan from; stages 3 and 4 carry the decisions above and will need their own
design pass on detail — particularly the dashboard metrics and the take-over state machine.

## 8. Data model sketch

Not a schema. The shape the migrations implement, for a planner to work from.

| Table | Carries |
|---|---|
| `tenants` | name, slug, lifecycle state, created/activated timestamps |
| `tenant_policies` | discount ceiling, max instalments, minimum instalment, always-escalate intents |
| `internal_users` | tenant, email, password hash, role, active |
| `internal_sessions` | user, token hash, issued, expires, last seen |
| `customers` | tenant, external id, anonymised identifiers, preferred channel |
| `receivables` | tenant, customer, amount, due date, days overdue, status |
| `knowledge_documents` | tenant, path/title, current version |
| `knowledge_versions` | document, version number, content hash, body, created |
| `knowledge_chunks` | version, ordinal, text, embedding (pgvector) |
| `conversations` | tenant, customer, channel, state, holder, opened, closed, escalation reason |
| `turns` | conversation, role, text, intent, confidence, responder, provider, model, latency, created |
| `turn_retrievals` | turn, knowledge version, chunk, score, rank |
| `deliveries` | tenant, conversation, turn, status, owner, timestamps |
| `provider_incidents` | tenant-independent: provider, started, recovered, cause |
| `platform_models` | kind, version, trained, metrics, artifact key in object storage |
| `llm_profiles` | tenant (null for platform defaults), purpose, provider, model, max tokens, temperature, order |
| `customer_scores` | tenant, customer, platform model version, score, scored |
| `stored_objects` | tenant (nullable for platform artifacts), kind, key, content type, size, created |

Every tenant-scoped table carries `tenant_id` directly, including ones reachable through a
parent — a join is not a boundary.

## 9. Consequences the paper inherits

Recorded, not acted on. The article is not driving any decision here.

- The responsive module's description changes: the customer-facing answer is generated, and
  the extractive responder is identified as the control arm rather than a fallback.
- The comparison in `docs/responder-comparison.md` is unaffected — both arms still run in
  the harness, on the same retrieval.
- Multi-tenancy is a product property with no bearing on the research question. It belongs
  in the architecture description, not in the results.
- The predictive module stays as described — one model, trained on pooled data — and gains
  a sentence saying tenants supply data and consume scores, but do not own models.
- The retrieval numbers in `docs/rag.md` and `docs/responder-comparison.md` are re-measured
  against the pretrained embedder (§6.13). The comparison's shape is unchanged; the
  published figures are not the ones that ship.
- The disclosure defect and its fix are worth a paragraph in the limitations or discussion:
  a retrieval system whose extractive fallback could quote internal policy to the
  counterparty is a real and generalisable finding about RAG in adversarial settings.

## 10. Left to the builder

- Table and column naming, indexes, and whether receivables migrate from the existing
  processed-data path or are ingested afresh.
- The repository layer's exact shape, as long as an unscoped query is hard to write by
  accident and a test proves it.
- Session cookie parameters, CSRF strategy for the console, and password policy.
- Websocket message envelope and reconnection behaviour.
- The console's component structure, routing and state management.
- Whether the knowledge seed loads at migration time or through a CLI command.
- Which pretrained embedding model, and its vector dimension.
- Which provider SDKs ship as extras beyond Anthropic, and the default profile per purpose.
- The object-storage client and key layout, and whether MinIO or a real bucket backs
  development.
- How platform model training reads tenant data without violating §6.1 — a deliberate,
  audited, admin-only path, not an accident of a missing filter.
- How the existing in-memory `SessionStore` is retired: replaced outright or kept as a
  cache in front of the rows.

## 11. Calls made after the builder check

A fresh engineer read this document and reported what they would still have to guess. The
questions that only the author could answer are in §12. These are the ones settled here, as
the designer's call rather than anything agreed:

- **A customer request resolves its tenant from a public tenant key in the widget embed**,
  and signed identity tokens carry a tenant claim and are signed with a per-tenant secret.
  An anonymous visitor still has a tenant — the key identifies the deployment, not the
  person. The existing single-secret `issue_token()` becomes per-tenant, which is a
  breaking change for any host already minting tokens.
- **A scripted generator is sanctioned for development and CI only.** `llm/client.py`
  already has scripted and echo clients; one of them backs the tests and a key-less clone,
  selected by an explicit setting that is off by default. It is not a fallback: production
  with no key escalates, per §6.6. Without this, a key-less checkout escalates every
  conversation to an operator who does not exist until stage 4.
- **CI gains Postgres with pgvector.** The scoping guarantee in §6.1 and the vector columns
  cannot be exercised against a fake. Tests that touch no rows stay infrastructure-free, so
  the suite keeps a fast path.
- **An escalated conversation stays open.** Stage 2 lifts the 30-minute TTL and the
  40-turn cap for threads marked escalated, keeps accepting customer messages, and stops
  answering them. The handover sentence promises that a person will take over — it names no
  timeframe, because until stage 4 there is no queue anyone is watching, and a promise the
  system cannot keep is worse than a vague one.
- **A database outage is escalate-and-announce**, the same shape as the provider outage in
  §6.6, not a 500. The record is in the request path from stage 1 onward, so its failure is
  a service state, not an exception the customer reads.
- **Internal-only knowledge never enters the retrieval context.** `knowledge_documents`
  carries an audience and the retriever filters on it, so the policy document is
  unreachable from an answer. Superseded in part by §6.7 as decided by the author: the
  policy *values* do reach generation, as structured negotiation parameters, because the
  agent is meant to negotiate. The document does not.

## 12. The author's answers

The four questions from the builder check that only the author could settle, as answered on
2026-10-26. The decisions are folded into the sections above; this records what was asked
and what came back.

**Does the agent negotiate, or is anything touching the authority limits a handover?**
It negotiates, within the ceiling the tenant sets. Recorded as §6.7 — which means the fix
is no longer "policy never reaches generation", it is "the values reach generation as
parameters, the document never does". This is the harder version of the fix and the one
that matters: a generated answer can paraphrase, so the thing withheld has to be the
reasoning and the ceilings-as-ceilings, not merely the file.

**Real embeddings?** The question was badly put. Vectors are already stored and already
embeddings; the issue is that the *embedder* is a TF-IDF vocabulary fitted over whatever
corpus is loaded at startup, held in memory, and invalidated by any document edit. Answered
by §6.12: models are app-level. One pretrained embedding model for all tenants, vectors in
pgvector, computed once per document version (§6.13).

**How tenant-aware does the ML pipeline become?** Not at all, in the training sense. One
platform model; the tenant pipeline is data import and indexing, producing a baseline the
platform model scores against. This is the smaller of the two readings and removes weeks of
per-tenant training, artifact storage and model lifecycle (§6.12).

**What does the evaluation harness read?** Rows and objects, like everything else. Nothing
stays on disk — rows and vectors to Postgres, files to object storage, ephemeral state to
Redis (§6.14). The harness therefore reads the same retrieval the customer path reads,
which is what `docs/responder-comparison.md` depends on, at the cost of needing a database
to reproduce the paper's tables.
