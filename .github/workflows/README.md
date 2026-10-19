# CI

`ci.yml` runs on every pull request and on pushes to `master`:

| Step | What it protects |
|---|---|
| Lint and format check | Style stays uniform without review comments about it |
| Tests | The evaluation protocol, the leakage guards, the legal rules |
| Pipeline smoke test | End-to-end breakage that unit tests miss — a report template naming a column the features no longer produce, for instance |

The smoke test runs on the synthetic source with a reduced dataset, so it needs no
credentials, no network and no Kaggle account.

Steps are added alongside the code they exercise, not ahead of it: the orchestrator demo
and the retrieval evaluation join this table in the pull requests that introduce those
commands.

The test suite trains real models, so it is slower than a typical unit-test suite. That is
deliberate — the protocol tests are the ones worth having — and the job timeout accounts
for it.
