# Session handoff — TCC collection system

Written 2026-09-12, for migrating to a new workstation.
Repo: `gabrielforster/tcc` · package: `collection` (`src/collection/`) · Python 3.12 + `uv`.

---

## 1. What this project is

Undergraduate thesis: an intelligent multi-agent debt collection system combining LLMs with
RAG, autonomous agents, ML predictive models and (later) voice services. The repo started as
documentation only (`article/`, `rfc.pdf`, `cronograma.md`, `pitch/`); everything under
`src/`, `tests/`, `docs/` and `knowledge/` was built in this session.

## 2. Standing rules — these are not negotiable and must survive the migration

- **English by default** for commits, code, docs and replies. pt-BR only when explicitly asked.
- **Every implementation goes through a pull request.** Never commit directly to `master`.
- **PR titles describe the change** — never reference schedule phases or deliverables.
  Always assign `gabrielforster`. Always apply labels, using the house set mirrored from
  `github.com/simplificamais/scrap/labels`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014W9R3vz3HTKGVAVqUaL54d
  ```
  PR bodies end with the Claude Code footer.
- **Real data is never committed.** `data/` is gitignored; so are `.env` and `ANONYMIZATION_SALT`.

## 3. Where the PR stack stands

```
master (35c8c42)
└── #5  feat/orchestrator          MERGEABLE   ← rebased onto master after #4 merged
    └── #6  feat/collection-rules  CONFLICTING ← stale, needs rebase
        └── #9  feat/responsive-agent          ← stale, needs rebase after #6

master
├── #7  feat/rag-pipeline          independent
└── #8  ci/github-actions          independent
```

Merged so far: #1 data pipeline, #2 data acquisition plan, #3 public data sources,
#4 predictive module.

### The immediate next task

PR #5 was rebased onto the new `master`, which **rewrote its commit** (`1d0b599` → `23ec4bc`).
#6 and #9 are still built on the old commit, so they show conflicts and duplicated history.

Work down the stack, in this order:

1. `git rebase feat/orchestrator feat/collection-rules`, resolve, verify, force-push with
   `--force-with-lease`.
2. `git rebase feat/collection-rules feat/responsive-agent`, same.

Expect the **same two conflicts each time**, because master and each branch add content at the
same location:

- `src/collection/cli.py` — several branches append a command just before `pipeline`.
  Resolution: keep both sides and give each command its own `@app.command()` decorator.
  Afterwards all commands must still register: `extract, ingest, dictionary, eda, features,
  train, explain, serve, demo, pipeline` (plus whatever the branch adds).
- `README.md` — each branch appends its own `###` section in the same spot. Resolution: keep
  all sections, master's first.

Verify each rebase with `make lint && make test` (70 tests on #5) plus the branch's own command.

## 4. Setting up the new workstation

```bash
git clone git@github.com:gabrielforster/tcc.git && cd tcc
cp .env.example .env      # then fill it in — see below
make setup                # uv creates .venv on Python 3.12
make test
```

`.python-version` pins **3.12** deliberately: the system Python (3.14 on the old box) has no
wheels for the ML stack. Install 3.12 first if `uv` cannot find one.

### Local-only state that does NOT travel with the repo

| What | Where | Notes |
|---|---|---|
| `.env` | repo root | Copy it off the old machine, or regenerate. |
| `ANONYMIZATION_SALT` | `.env` | `openssl rand -hex 32`. A **new salt changes every pseudonym** — fine here, since the data is synthetic and regenerable, but keep the old one if you want byte-identical artifacts. |
| `data/raw`, `data/interim`, `data/processed` | gitignored, ~21 MB | Fully regenerable: `make pipeline`. Nothing in there is precious. |
| Docker volumes | Postgres (pgvector) `:5433`, Redis `:6380` | `make up` recreates them; nothing persistent lives there yet. |
| Kaggle credentials | `~/.kaggle/` | See the blocker below. |

Everything under `data/` is derived output — raw extract, anonymized interim, splits, fitted
preprocessors, the two champion models, metrics JSON and the TF-IDF index. `make pipeline`
rebuilds the lot from scratch; no manual step is needed.

## 5. Open blocker — Kaggle

The Home Credit source (`src/collection/data/sources/home_credit.py`) needs the competition
files in `data/raw/home-credit/`. Downloads return **403**, and it is *not* a credentials
problem: `kaggle datasets list` works, but `kaggle competitions list` reports
`userHasEntered: False`.

**Requires your action, in a browser:** open the Home Credit Default Risk competition and click
*Late Submission* / *Join Competition* to accept the rules (the competition closed in 2018, so
late submission is the only route). Also note the old machine had `~/.kaggle/access_token` but
**no `kaggle.json`** — create one from your Kaggle account settings on the new box.

Until then, Home Credit stays unexercised. The Bank Marketing source (UCI, direct download,
no auth) works and is what the real findings below came from.

## 6. Decisions and findings worth not relearning

**D1 — public datasets now, ERP as a parallel upgrade.** No public dataset has collection
contact events tied to receivables. That reframed the schedule's contingency plan: the
operational before/after comparison is downgraded to a documented limitation rather than a
deliverable. Recorded in `docs/data-acquisition-plan.md`.

**The project's first real finding.** Bank Marketing shows response rate falling
**13.0% → 5.5% across six contact attempts** (a 58% decline). This is the empirical grounding
for `max_attempts=4` and `cooldown_hours=48` in `src/collection/rules/policy.py` — those
numbers are evidence, not guesses. Don't let them drift without re-deriving them.

**Champion selection uses average precision, never accuracy.** At a 17% positive rate the
majority class scores high on accuracy and tells you nothing. `SELECTION_METRIC =
"average_precision"` in `src/collection/models/metrics.py`. Cross-validation is
`TimeSeriesSplit` k=5.

**Leakage discipline.** Temporal features use `expanding().shift(1)` and `merge_asof`; splits
are chronological 70/15/15. A censoring bug once put the positive rate at 0.70 in train and
0.53 in test — the fix was anchoring `as_of` to `invoices["payment_date"].max()` rather than
the maximum due date. If those rates diverge again, look there first.

**Retrieval baseline.** TF-IDF (word + `char_wb` n-grams), exact cosine over normalized
vectors: recall@5 = 1.0, MRR = 0.78. Six questions fail to rank first and all six are semantic
rather than lexical — that is the stated hypothesis for the neural comparison. Intent
classifier baseline: accuracy 0.913 on 23 labelled messages.

**LGPD posture.** Direct identifiers suppressed, HMAC-SHA256 pseudonymization with a salt,
quasi-identifiers generalized. Contact windows are evaluated in `America/Sao_Paulo`, never UTC
— 08:00 UTC is 05:00 local, which is an illegal contact hour under the superendividamento
amendments. `src/collection/rules/window.py`.

## 7. Traps already hit — don't pay for them twice

- **`sed`/`replace` no-ops that fail silently.** Bit me twice, both times anchoring on wording
  that only existed on a *different* branch, and once after `ruff format` had reshaped the file.
  Always assert the anchor exists before replacing, and verify the file changed afterwards.
- **`merge_asof` MergeError** on `datetime64[s]` vs `[us]` → cast both sides to
  `datetime64[ns]`.
- **No lambdas in anything `joblib.dump` touches** — use a module-level function
  (`_select_features`).
- **Custom estimators** need `ClassifierMixin, BaseEstimator` and a `self.classes_`, or sklearn
  raises on `__sklearn_tags__`.
- **starlette's `TestClient` needs `httpx2`** — it is in the dev group for that reason alone.
- **The API uses `lifespan`**, not the deprecated `on_event`.
- **`make demo` was missing** from the Makefile until the #5 rebase, a casualty of one of those
  silent no-ops. Every command should have a target; check that when adding one.

## 8. Map of the code

```
src/collection/
  domain/schema.py       Field/Table dataclasses, TABLES, target + window constants
  data/sources/          base.py (DataSource ABC, RawDataset.provides), synthetic,
                         home_credit, bank_marketing
  data/anonymize.py      pseudonym(), anonymize(), lgpd_report()
  features/build.py      build_features(task), build_splits(), Splits
  models/                metrics, candidates (6), train, explain (permutation + SHAP)
  api/app.py             create_app(), ModelRegistry, POST /score, GET /models/{task}, /health
  agents/                events (Event, EventType, Priority), queue, orchestrator (Trace,
                         MAX_CASCADE_DEPTH), base, proactive, responsive, intent, demo
  rules/                 window (ContactWindow), policy (ContactPolicy)
  rag/                   chunking, documents, embeddings, index, evaluate
  cli.py                 typer app — every make target maps to a command here
knowledge/               RAG corpus + messages.json + questions.json (gold labels)
docs/                    architecture, data-acquisition-plan, data-dictionary, eda/, models/,
                         collection-rules, rag, responsive-agent
```

Opt-out is enforced in `Orchestrator._suppressed()` **before any agent runs** — that ordering
is deliberate and load-bearing for the compliance story.

## 9. Suggested first moves on the new box

1. `make setup && make test` — confirms the environment before touching anything.
2. Rebase #6, then #9, as described in §3.
3. Sort out Kaggle (§5) whenever convenient; nothing is blocked on it right now.
4. Then pick up new work: #7 and #8 are independent of the stack and can merge any time.
