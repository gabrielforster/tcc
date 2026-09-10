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
| [`docs/`](./docs) | Pipeline-generated documentation (data dictionary, EDA report). |
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
make test lint
```

`make pipeline` runs schedule deliverables 3-5 end to end and produces:

| Output | Contents |
|---|---|
| `data/raw/` | Raw dataset from the configured source (personal data; never committed) |
| `data/interim/` | Anonymized dataset (LGPD) — the starting point for analysis |
| `data/processed/` | Chronological 70/15/15 splits and the serialized preprocessor, per task |
| `docs/data-dictionary.md` | Dictionary generated from the domain schema |
| `docs/eda.md` + `docs/eda/*.png` | Exploratory report and the four charts |

### Data source

The pipeline talks to an interface (`DataSource`), not to a specific database:

- `DATA_SOURCE=synthetic` — generator that reproduces the real domain schema (customers,
  receivables, collection events, agreements) with latent propensity, seasonality and
  contact effects. The fallback foreseen in the schedule while ERP access is pending.
- `DATA_SOURCE=erp` — real extraction; fill in `ERP_DATABASE_URL` and adjust the table
  names in `src/collection/data/sources/erp.py`. Nothing downstream changes.

### Predictive tasks

| Task | Population | Reference date | Target |
|---|---|---|---|
| Payment propensity | Already-due receivables | Due date | Settled within 30 days |
| Future default | Not-yet-due receivables | Issue date | Goes past 60 days unsettled |

No feature uses information dated after the reference date, and the split is
chronological — there are automated tests covering exactly that (`tests/test_features.py`).

### Contributing

Code, commit messages and documentation are written in English. Every change lands
through a pull request; `master` is never committed to directly.

## Status

📚 Artigo e planejamento completos. Implementação em andamento: o pipeline de dados
(entregas 3–5) está pronto; o próximo passo é o módulo preditivo do Bloco II.

---

<sub>Centro Universitário Católica de Santa Catarina — Engenharia de Software.</sub>
