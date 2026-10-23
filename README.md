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
- 🎙️ **Serviços de voz** — transcrição de áudios (STT) e telefonia programável (TTS),
  cobrindo os canais reais usados pelos clientes (ex.: áudios de WhatsApp e ligações).

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
   │ Transcrição │    │  Telefonia  │      │    Dados     │
   │   de Áudio  │    │ Programável │      │  Históricos  │
   └─────────────┘    └─────────────┘      └──────────────┘
```

## Pergunta de pesquisa

> Em que medida um sistema multiagente inteligente, baseado em LLMs com RAG e modelos
> preditivos de Machine Learning, integrado a serviços de transcrição de áudio e
> telefonia programável, é capaz de automatizar o processo de cobrança — tanto de forma
> responsiva quanto proativa — e contribuir para o aumento da eficiência na recuperação
> de crédito em comparação com abordagens convencionais?

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
| `data/` | `raw` / `interim` / `processed` datasets — **never committed** (LGPD). |
| [`article/`](./article) | Artigo científico (LaTeX, formato **SBC Reviews 2025**) — proposta de portfólio do PAC 8. Veja o [README do artigo](./article/README.md) para compilar. |
| [`cronograma.md`](./cronograma.md) | Cronograma de 19 entregas (jul.–dez. 2026). |
| [`rfc.pdf`](./rfc.pdf) | RFC do projeto: documento de proposta detalhada. |
| `tabela-comparativa.pdf` | Tabela comparativa de trabalhos relacionados (artigos e soluções comerciais). |
| `old-article/` | Versões anteriores do artigo (histórico). |
| `template/` | Template original da classe SBC Reviews 2025. |

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
| 7 | Integração de serviços de voz (STT/TTS, telefonia) |
| 8 | Testes, experimentos e avaliação |

## O artigo

A proposta foi consolidada em um artigo científico (4–6 páginas, formato SBC Reviews 2025)
em [`article/`](./article). Para gerá-lo:

```sh
cd article
./build.sh        # XeLaTeX + BibTeX; gera article/main.pdf
```

> O script contorna uma instalação local de TeX Live incompleta e se autoconfigura na
> primeira execução. Detalhes, alternativas (Overleaf) e notas de compilação estão no
> [README do artigo](./article/README.md).

## Running it

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```sh
cp .env.example .env      # set your own ANONYMIZATION_SALT
make setup                # creates .venv (Python 3.12) and installs dependencies
make up                   # Postgres with pgvector + Redis
make pipeline             # extract -> ingest -> dictionary -> eda -> features
make train                # train the models and write the comparison report
make test lint
```

`make pipeline` runs schedule deliverables 3-5 end to end and produces:

| Output | Contents |
|---|---|
| `data/raw/` | Raw dataset from the configured source (personal data; never committed) |
| `data/interim/` | Anonymized dataset (LGPD) — the starting point for analysis |
| `data/processed/` | Chronological 70/15/15 splits and the serialized preprocessor, per task |
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
known. The default embedder is TF-IDF over word and character n-grams — no API key, no
model download, and a real lexical baseline for a neural retriever to be measured against.

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

The template arm needs no API key and runs in CI. The LLM arm needs `ANTHROPIC_API_KEY`
and is run by hand.

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
make seed           # schedule outbound contacts and leave them waiting in the chat
make serve          # open the widget as one of those customers and the message is there
```

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

📚 Artigo e planejamento completos. Implementação em andamento: o pipeline de dados, o
módulo preditivo, a arquitetura multiagente, o RAG e o módulo responsivo — agora com
geração por LLM e medição de fidelidade — estão prontos, e o agente é acessível por um
widget de chat embedável que já recebe áudios, rastreia status de entrega e abre conversas
iniciadas pelo sistema, atrás de um contrato de canal explícito. O próximo passo é o canal
de voz em tempo real (WebRTC/LiveKit).

---

<sub>Centro Universitário Católica de Santa Catarina — Engenharia de Software.</sub>
