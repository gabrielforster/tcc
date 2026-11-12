# Sistema Multiagente Inteligente de Cobrança

> **LLM · RAG · Agentes Autônomos · Machine Learning · Voz**
>
> Trabalho de Conclusão de Curso (TCC) — Engenharia de Software
> Centro Universitário Católica de Santa Catarina

**Autor:** Gabriel Forster Rocha

---

## Sobre o projeto

Este repositório reúne a pesquisa e a documentação de um **sistema multiagente que
automatiza o ciclo de recuperação de crédito**. Em vez de depender de operadores
humanos para cada etapa da cobrança, o sistema combina quatro tecnologias:

- 🧠 **LLMs com RAG** — atendimento responsivo e contextualizado, respondendo dúvidas
  com base na documentação interna da empresa.
- 🤖 **Agentes autônomos** — abordagem proativa, identificando pendências e iniciando
  a cobrança sem intervenção humana.
- 📊 **Modelos preditivos de ML** — classificam inadimplentes por propensão ao pagamento
  e preveem inadimplência futura, permitindo priorização e cobrança preventiva.
- 🎙️ **Serviços de voz** — transcrição de áudios (STT) e síntese de fala (TTS), cobrindo
  as duas formas como o cliente fala: o áudio enviado no fio da conversa e a chamada de
  voz em tempo real.

### Modos de operação

| Modo | O que faz | Gatilho |
|------|-----------|---------|
| **Responsivo** | O cliente entra em contato; o agente usa RAG para responder sobre suas pendências. | Mensagem recebida (texto ou áudio) |
| **Proativo (Ativo)** | O sistema detecta documentos vencidos / clientes em risco e inicia a cobrança. | Agendamento automático ou sinal do modelo preditivo |

### Arquitetura em alto nível

```
                ┌─────────────────────────────┐
                │     ORQUESTRADOR CENTRAL     │
                │  (coordena agentes e filas)  │
                └─────────────────────────────┘
                  │           │            │
          ┌───────┘           │            └───────┐
          ▼                   ▼                    ▼
   ┌─────────────┐    ┌─────────────┐      ┌──────────────┐
   │  RESPONSIVO │    │   ATIVO     │      │   PREDITIVO  │
   │  (LLM+RAG)  │    │  (Agentes)  │      │     (ML)     │
   └─────────────┘    └─────────────┘      └──────────────┘
          │                   │                    │
   ┌─────────────┐    ┌─────────────┐      ┌──────────────┐
   │ Transcrição │    │   Canais    │      │    Dados     │
   │   de Áudio  │    │ chat · voz  │      │  Históricos  │
   └─────────────┘    └─────────────┘      └──────────────┘
```

## Pergunta de pesquisa

> Em que medida um sistema multiagente inteligente, baseado em LLMs com RAG e modelos
> preditivos de Machine Learning, integrado a serviços de transcrição de áudio e
> telefonia programável, é capaz de automatizar o processo de cobrança — tanto de forma
> responsiva quanto proativa — e contribuir para o aumento da eficiência na recuperação
> de crédito em comparação com abordagens convencionais?

> [!NOTE]
> A pergunta segue valendo, mas *telefonia programável* e *WhatsApp Business API* foram
> substituídas por um canal de chat embedável e uma chamada de voz em tempo real sobre
> WebRTC. O critério, o que a troca custa e o que explicitamente não mudou estão em
> **Mudanças de escopo** no [`cronograma.md`](./cronograma.md) e em
> [`docs/channels.md`](./docs/channels.md).

**Hipótese:** ao combinar agentes conversacionais (LLM+RAG), agentes autônomos proativos
e um módulo preditivo de ML, o sistema **reduz o tempo médio de recuperação de crédito** e
**aumenta a taxa de resolução de pendências** frente a processos manuais ou parcialmente
automatizados.

**Métricas de sucesso:**

- *Modelos preditivos:* acurácia, precisão, revocação, F1-score.
- *Indicadores operacionais:* taxa de recuperação de crédito e tempo médio de resolução,
  comparados a baselines de processos convencionais.

## Estrutura do repositório

| Caminho | Conteúdo |
|---|---|
| [`src/collection/`](./src/collection) | System code: domain, data pipeline, EDA and features. |
| [`docs/`](./docs) | Pipeline-generated documentation (data dictionary, EDA reports), the [architecture](./docs/architecture.md) and the [data acquisition plan](./docs/data-acquisition-plan.md). |
| `data/` | A download cache (`raw/`), Piper voices (`voices/`) and call audio (`calls/`) — **never committed** (LGPD). Pipeline output lives in object storage. |
| [`article/`](./article) | Artigo científico (LaTeX, formato **SBC Reviews 2025**) sobre o sistema construído e o que foi medido. Veja o [README do artigo](./article/README.md) para compilar. |
| [`cronograma.md`](./cronograma.md) | Cronograma de 19 entregas (jul.–dez. 2026) e as mudanças de escopo. |
| [`rfc.pdf`](./rfc.pdf) | RFC do projeto: documento de proposta detalhada. |

## Plano de desenvolvimento

O projeto é desenvolvido em oito fases sequenciais:

| Fase | Etapa |
|---|---|
| 1 | Requisitos e análise do domínio (inclui aspectos legais: CDC, LGPD) |
| 2 | Coleta e preparação de dados para ML |
| 3 | Módulo preditivo (ML): propensão ao pagamento e inadimplência futura |
| 4 | Arquitetura do sistema multiagente |
| 5 | Módulo responsivo (LLM + RAG) |
| 6 | Módulo ativo/proativo (agentes autônomos) |
| 7 | Integração de serviços de voz (STT/TTS, canal de voz em tempo real) |
| 8 | Testes, experimentos e avaliação |

## O artigo

O artigo em [`article/`](./article) (formato SBC Reviews 2025) descreve o sistema que foi
construído e os resultados medidos, inclusive as mudanças de escopo e as limitações. Para
gerá-lo sem TeX local:

```sh
make article      # XeLaTeX + BibTeX num container; gera article/main.pdf
```

> A primeira execução constrói a imagem de TeX (cerca de 15 minutos); as seguintes a
> reaproveitam. Com um TeX Live local, `article/build.sh` faz o mesmo. Detalhes e notas de
> compilação estão no [README do artigo](./article/README.md).

## Running it

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```sh
cp .env.example .env      # set your own ANONYMIZATION_SALT
make setup                # creates .venv (Python 3.12) and installs dependencies
make up                   # docker compose up -d: Postgres with pgvector, MinIO and LiveKit
make migrate              # apply the migrations (alembic upgrade head)
make seed                 # create the development tenant and leave it servable
export COLLECTION_TENANT=desenvolvimento   # the tenant the pipeline commands work on
make pipeline             # ingest (extracts) -> dictionary -> eda -> features
make train                # train, publish a platform model version, write the comparison report
make test lint            # database tests run only against TEST_DATABASE_URL, never DATABASE_URL
```

Every target has a help line in the `Makefile`. The ones that reproduce the evaluation:

| Target | What it does |
|---|---|
| `make coverage` | The suite under coverage, then the critical-function report; fails below 80% in any group — see [`docs/testing.md`](./docs/testing.md) |
| `make ollama` | Starts the local model server (Ollama, `llm` compose profile) and pulls `qwen2.5:3b` for the LLM arms |
| `make experiment` | The simulated experiment against the manual and semi-automated baselines (about 15 minutes) |
| `make concurrency` | Holds up to 400 chat conversations at once and times every request |
| `make voices` | Downloads the two Piper pt-BR voices the call uses into `data/voices` |
| `make call` | The voice call demo on http://127.0.0.1:8100/call/demo (LiveKit is started for it) |
| `make call-bench` | The call's turn-time budget on synthesised speech; `ARGS="--live"` goes through LiveKit |
| `make article` / `make cronograma` | Build the article and the schedule PDFs in Docker |

`make seed` is what makes a fresh clone usable. Everything the serving path reads lives in
the database now — the tenant, the knowledge, the conversations — so a clone with an empty
database has nothing to answer from. The command creates a tenant called *Desenvolvimento*,
publishes each document under `knowledge/` into it as a versioned document (the authority
limits as `internal`, the rest as `customer`), ingests the synthetic dataset, and advances
the tenant through `ingesting → ready → active`, which is the only state the chat serves.
It climbs that ladder one rung at a time, so a run that fails half way leaves a tenant that
is honestly not servable rather than one that claims to be.

Run it again whenever a document under `knowledge/` changes: unchanged bytes keep their
version, an edited document mints the next one, and ingestion matches each customer by its
external id and replaces that tenant's receivables as a set, instead of adding to them.
The labelled `.json` question sets are evaluation fixtures, not knowledge, and are not
published. It prints the tenant's `tenant_key`; put that in
`DEFAULT_TENANT_KEY` in `.env` to have the chat widget serve this tenant.

`make pipeline` runs schedule deliverables 3-5 end to end and produces:

| Output | Contents |
|---|---|
| Objects under `tenants/<id>/` | Everything the pipeline produces: raw and anonymized frames, splits, preprocessors, models, metrics |
| `data/raw/` | A download cache, not pipeline output: sources that read local files (e.g. Home Credit CSVs) keep the fetched archive here rather than fetch it again |
| `docs/data-dictionary.md` | Dictionary generated from the domain schema |
| `docs/eda-<source>.md` + `docs/eda/<source>/*.png` | Exploratory report and charts, one set per data source |

### Data source

The pipeline talks to an interface (`DataSource`), not to a specific database. A source
declares which of the four domain tables it covers, and the rest of the pipeline adapts —
see [the data acquisition plan](./docs/data-acquisition-plan.md) for why the project runs
on public data.

| `DATA_SOURCE` | Covers | Notes |
|---|---|---|
| `synthetic` | all four tables | Generated. Reproduces the domain schema with latent propensity, seasonality and contact effects. The default, and what the test suite runs on |
| `home-credit` | customers, receivables | Real payment behavior from [Home Credit Default Risk](https://www.kaggle.com/competitions/home-credit-default-risk). Needs a Kaggle account: put `application_train.csv` and `installments_payments.csv` in `data/raw/home-credit/` |
| `bank-marketing` | customers, contacts | Real contact attempts from [UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing), CC BY 4.0. Downloads on first use, no account needed |
| `erp` | all four tables | The real extraction. Fill in `ERP_DATABASE_URL` and adjust table names in `src/collection/data/sources/erp.py` |

The two predictive tasks need receivables, so they do not run on `bank-marketing`; that
source feeds contact-strategy analysis instead, and `collection eda` produces the matching
report automatically.

### Predictive tasks

| Task | Population | Reference date | Target |
|---|---|---|---|
| Payment propensity | Already-due receivables | Due date | Settled within 30 days |
| Future default | Not-yet-due receivables | Issue date | Goes past 60 days unsettled |

No feature uses information dated after the reference date, and the split is
chronological — there are automated tests covering exactly that (`tests/test_features.py`).

### Predictive module

`make train` compares six candidates — logistic regression as the baseline, random forest
and XGBoost, each under both imbalance strategies (class weighting and SMOTE) — and writes
`docs/model-<source>-<task>.md`.

The protocol is fixed before any result is read: candidates are cross-validated on the
training split with `TimeSeriesSplit` (k=5, so no fold is scored on rows preceding its own
training data), the two tunable families get a small randomised search over the same folds,
the champion is whichever maximises **average precision on validation**, and the test split
is scored exactly once at the end. Accuracy is reported because the schedule asks for it,
but it never decides: at a 17% positive rate the majority class already scores 83%, so
every report carries the majority-class baseline next to the champion.

`make explain` adds interpretability — permutation importance on the test split plus SHAP
values — writing `docs/interpretability-<source>-<task>.md`.

### Scoring API

`make serve` runs the inference API the active module consumes:

| Endpoint | Purpose |
|---|---|
| `POST /score` | Score a batch of receivables; returns probability, decision at the tuned threshold, and position in the contact queue |
| `GET /models/{task}` | Champion name, threshold and the exact feature columns the model expects |
| `GET /health` | Which models are loaded |
| `POST /chat/sessions` | Open a chat conversation (see the web chat channel below) |
| `POST /chat/sessions/{id}/messages` | Send one customer message and get the agent's reply |
| `POST /chat/sessions/{id}/audio` | Send one voice note; the agent transcribes it |
| `POST /chat/sessions/{id}/deliveries/{id}` | The widget reporting `entregue` or `lida` |
| `GET /chat/widget.js` | The embeddable widget |

The ranking is the operational output, not the probability: the active module works down a
finite queue each day, which is also why models are selected on average precision. Unknown
or missing feature columns are rejected with a 422 rather than silently scored.

### Multi-agent architecture

`collection demo` routes a mix of events through the orchestrator and shows what happened
to each — see [`docs/architecture.md`](./docs/architecture.md).

Every interaction is an immutable event carrying `caused_by`, so the log reconstructs why
any contact happened. The orchestrator does four things only: route, prioritise
(responsive → escalation → proactive), enforce opt-out **before any agent runs**, and
record. All three agents are now real: the [responsive one](./docs/responsive-agent.md)
classifies and answers from the knowledge base — in writing or
[from a voice note](./docs/voice.md) — the proactive one runs the
[collection rules](./docs/collection-rules.md) and
[writes the outbound message](./docs/outreach.md), and the predictive one is behind
`POST /score`.

### RAG

`collection rag` builds the knowledge index from `knowledge/` and evaluates retrieval
against a labelled question set — see [`docs/rag.md`](./docs/rag.md).

Retrieval is measured **before** any LLM is involved: an agent that invents an answer
because nothing relevant was retrieved has a retrieval failure, not a generation one, and
the eventual hallucination numbers only mean something if retrieval quality is already
known.

The serving path no longer uses that in-memory index: it retrieves from `pgvector`, over
the chunks a document's current version was embedded into at publish time, filtered to one
tenant and to the customer audience in SQL. `collection rag --retriever pgvector` measures
that path over a seeded tenant, and `--retriever tfidf` keeps the in-memory index as the
lexical baseline it is measured against. Adding `--customer-only` to both withholds the
internal documents from either corpus, which is what makes the two comparable — and
measured that way, the pretrained embedder does **not** beat TF-IDF on this corpus. The
tables, and why the move was made anyway, are in `docs/rag.md`.

### Generation

`collection answer` answers a customer message, or evaluates the responders against the
labelled questions — see [`docs/llm-responder.md`](./docs/llm-responder.md).

The LLM sits behind the same `Responder` seam as the templated baseline, so retrieval and
intent classification are identical between the two arms and any difference belongs to
generation alone. That is why the baseline stays: it is the control, and because it quotes
rather than writes it cannot hallucinate, which makes it the ceiling the LLM is read
against. Every answer is scored for faithfulness — invented figures, unsupported claims
and citations of documents that were never retrieved — and
[`docs/responder-comparison.md`](./docs/responder-comparison.md) carries the results.

The template arm needs no API key and runs in CI. The LLM arm is run by hand, against
either provider. The published LLM numbers come from the local model, because no hosted key
was available — read them as a floor for a hosted model, not an estimate of one:

```bash
# hosted Claude
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... make answer ARGS="--compare"

# a local model, no account and no key: Ollama in docker-compose, OpenAI protocol
make ollama                      # starts the server, pulls qwen2.5:3b (OLLAMA_MODEL=...)
LLM_PROVIDER=openai LLM_MODEL=qwen2.5:3b make answer ARGS="--compare"
```

The `openai` provider is any server speaking the OpenAI chat protocol, so Groq or
OpenRouter are the same provider with `LLM_BASE_URL` and `LLM_API_KEY` set. Every generated
report names the provider, model and date its LLM arm ran against.

### Outbound messages

`collection outreach` composes the message the proactive agent would send, or evaluates the
composers against labelled briefs — see [`docs/outreach.md`](./docs/outreach.md).

The commercial terms come from `knowledge/tabela-de-parcelamento.csv`, the same file the
retriever serves to the chat, so the offer the system sends and the answer it gives cannot
disagree. Every message is checked against its own brief before it is scheduled — invented
figures, threatening vocabulary, missing disclosures, offers beyond the stage's authority,
length — and one that fails is escalated to a person instead of sent. The check lives in the
agent rather than in the composer, because a component that grades its own output cannot be
compared against an alternative.

The comparison is two-sided on purpose. The template arm is the compliance ceiling and the
personalisation floor; what the LLM arm has to buy is **distinctness**, measured among
customers who get the same offer on the same channel.

### Voice notes

`collection voice` sweeps what recognition error costs the pipeline; `--audio FILE`
transcribes one clip — see [`docs/voice.md`](./docs/voice.md). A customer's OGG/OPUS voice
note is probed, decoded to 16 kHz mono and transcribed by Whisper running **locally**: a
voice note is personal data under the LGPD, and sending it to a third-party recognition API
is a transfer the operation would have to justify.

The measurement is turned around, because a control transcriber that returns the labelled
reference cannot be wrong. Instead of asking how often recognition fails, the sweep fixes
the error rate and asks **what survives it** — and the answer is the module's reason for
existing. At a word error rate of 0.099, a transcript that reads perfectly well, intent
still classifies the same way 86% of the time while one spoken figure in seven is already
wrong. Intent is said several ways in a sentence; an amount is said once, and nothing
downstream can tell when it breaks.

So an amount heard in audio is a proposal to confirm, never an instruction to execute: the
agent reads it back before acting, and a transcript it is not confident about goes to a
person with the transcript attached rather than being answered.

### Real-time voice call

`make call` serves a page where a customer presses *Ligar* and talks to the same agent the
chat uses, over WebRTC through a self-hosted LiveKit — see
[`docs/voice-call.md`](./docs/voice-call.md). A recording notice plays first and cannot be
skipped; Whisper recognises and Piper speaks, both locally, because the reply reads out a
named person's debt. It is a reduced proof of concept: no barge-in, no SIP, no outbound
dialling. The turn takes a median of 2.2 s end to end over WebRTC on a CPU. The call
states a reply budget of 43 words (a 20 s turn at Piper's slowest measured rate) and the
responder picks the passage section that fits, sentences intact, so a spoken reply lasts a
median of 12.1 s where the first measurement read whole passages for 102 s. The chat's
replies are unchanged. Retrieval can still pick the wrong passage, and then a short reply
is short and wrong.

### Testing and the controlled experiment

[`docs/testing.md`](./docs/testing.md) says what the suite proves: every critical function
the schedule names is executed, and the proactive, responsive and handoff flows run end to
end through the orchestrator.

[`docs/experiment-protocol.md`](./docs/experiment-protocol.md) fixes how the system is
compared with a manual desk and a rules-only campaign, and
[`docs/experiment-results.md`](./docs/experiment-results.md) reports it. With no
operational data, it is a **simulation**: 30 replications paired by seed, contact response
taken from the Bank Marketing curve, 95% bootstrap intervals. Prediction recovers about
4.8 pp more of the at-risk amount within 30 days when contact capacity is scarce, and
nothing once every receivable can be reached; the compliance audit found zero violations.
The same document carries the concurrency check: no errors from 1 to 400 simultaneous
conversations, measured in memory with the template responder.

### Web chat channel

`make serve` exposes an embeddable chat widget at `/chat/demo`, documented in
[`docs/web-chat.md`](./docs/web-chat.md). One `<script>` tag embeds it on any page:

```html
<script src="https://host/chat/widget.js" data-api="https://host" defer></script>
```

It is a **channel adapter**: every message becomes a `message_received` event handed to the
orchestrator, never a direct call to the agent. The chat therefore inherits responsive
priority, the audit trail and the opt-out enforced before any agent runs — and the next
channel is another adapter over the same contract rather than a second copy of the
conversation logic. That contract is now explicit and tested, not a convention: see
[`docs/channels.md`](./docs/channels.md).

The thread carries voice notes, delivery status (`enviada / entregue / lida / respondida`)
and conversations the proactive agent starts. Two things that document states plainly,
because both are easy to read as stronger claims than they are: `entregue` and `lida` are
**reported by the browser, not by a carrier**, and nothing is guessed on a timer — only
`respondida` is observed by the server. And a proactive thread is **seeded, not pushed**:
with no push transport, the message waits in a mailbox until the customer opens the widget.

```sh
make contacts                    # report what the outbound path decides, receivable by receivable
make serve ARGS="--seed-demo"    # the live demo: the server seeds the channel it serves
```

The two are not interchangeable. A mailbox is in memory, so `make contacts` fills one that
dies with the command; only `--seed-demo` leaves a message waiting in a thread a browser
can open, because then it is the same channel object.

The WhatsApp Business API and real telephony were replaced by this simulated chat channel
and a real-time voice channel, deliberately and for a stated reason — the integrations add
transport, approval queues and a billing relationship, not a single new decision by any
agent, while the channel contract is where the architectural risk actually lives. The
argument and what it costs are in [`docs/channels.md`](./docs/channels.md), and in
Portuguese under *Mudanças de escopo* in [`cronograma.md`](./cronograma.md).

Identity never comes from the browser. The host application's backend mints a signed token
with `issue_token(customer_id)`; without one the session is anonymous, which still answers
anything in the knowledge base because none of it is customer-specific.

Conversations are multi-turn: "e em 6 vezes?" after "posso parcelar?" resolves against the
thread rather than being read as a fresh question — see
[`docs/multi-turn.md`](./docs/multi-turn.md). Context **adds** a retrieval query and never
replaces the customer's own words, because the version that replaced them measurably lost
passages the bare message had found.

### Contributing

Code, commit messages and documentation are written in English. Every change lands
through a pull request; `master` is never committed to directly.

## Status

🧪 Construído e medido. Implementado: o pipeline de dados, o módulo preditivo, a
arquitetura multiagente, o RAG e o módulo responsivo — com geração por LLM e medição de
fidelidade —, o módulo proativo com composição e checagem de conformidade, o widget de chat
embedável que recebe áudios, rastreia status de entrega e abre conversas iniciadas pelo
sistema, e a chamada de voz em tempo real (prova de conceito reduzida), todos atrás de um
contrato de canal explícito. Tudo isso grava num registro durável multi-inquilino
(Postgres + pgvector), com retenção de transcrições e fila de escalação para humanos.

A avaliação está feita: cobertura das funções críticas, testes dos três fluxos, o
experimento simulado contra os dois baselines, a concorrência e os braços LLM num modelo
local. O artigo foi reescrito sobre esses resultados. Falta o braço LLM num modelo
hospedado (não há chave de API) e a entrega final — *tag*, *slides* e ensaio da defesa.

---

<sub>Centro Universitário Católica de Santa Catarina — Engenharia de Software.</sub>
