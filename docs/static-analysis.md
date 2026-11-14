# Static code and security analysis

What is checked before a change reaches `master`, by which tool, what fails a pull request,
and where the results are read. Every tool is open source and runs the same way in CI and
on a laptop.

## The tools

| Layer | Tool | What it catches | Gate |
|---|---|---|---|
| Quality + Python SAST | **ruff** (rules `E F I UP B SIM S C90`) | Style and likely bugs; `S` is the Bandit rule set ported to ruff: `eval`/`exec`, `shell=True`, unsafe deserialisation (`pickle`, `yaml.load`), SQL built from strings, hard-coded passwords, weak hashes and ciphers, `assert` used as a check, TLS verification turned off, binding to all interfaces; `C90` caps cyclomatic complexity at 10 | Any finding |
| SAST, cross-file | **Semgrep CE** (`p/python`, `p/owasp-top-ten`, `p/secrets`) | Taint-style patterns ruff cannot see in one file: request data reaching SQL, a shell, a template or a redirect; framework misuse (FastAPI, SQLAlchemy, requests); secrets in code; mutable action tags in workflows | `ERROR` severity |
| Secrets | **gitleaks** | Credentials committed anywhere in history (cloud keys, tokens, private keys, high-entropy assignments), including ones deleted in a later commit | Any leak |
| Dependencies | **pip-audit** | Known vulnerabilities (PyPI advisory database / OSV) in every version `uv.lock` resolves, all extras included | Any advisory |
| Dependencies, IaC, containers | **Trivy** `fs` (`vuln`, `misconfig`) | A severity-rated second opinion on `uv.lock`, and misconfiguration in every Dockerfile, Terraform root, Kubernetes manifest and Helm chart in the tree (root user, `latest` tags, open security groups, unencrypted storage...) | `HIGH`/`CRITICAL` |
| CI itself | **zizmor** | GitHub Actions attack surface: actions not pinned to a commit, template injection from event payloads, the token left in the checkout, over-broad `permissions:` | `high` |

**Why both pip-audit and Trivy on the dependencies.** pip-audit reads PyPI's own advisory
feed, which is the most complete for Python packages but mostly carries no severity; so its
gate is "any known vulnerability", and the fix is nearly always a lock bump. Trivy's
database rates severity, so its results land in code scanning sorted by risk.

**Containers and infrastructure.** Trivy's `misconfig` scanner walks the whole tree, so the
application's Dockerfile and `infra/` (Terraform) are checked as soon as they are merged, with
no change to the configuration. It does not read `docker-compose.yml`, which is not one of
its formats. The built image (OS packages and Python packages *as installed*) is scanned in
the deploy pipeline, where the image exists; the step is:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/aquasecurity/trivy:0.76.2 image --exit-code 1 --severity HIGH,CRITICAL \
  --ignore-unfixed ghcr.io/gabrielforster/tcc:<sha>
```

## The gate: one rule for every scanner

The tools that rate severity each use their own words (Semgrep `ERROR`, Trivy `HIGH`,
zizmor `high`), but each maps its scale onto SARIF's `level`, and the top of every scale is
`error`. `scripts/sarif_gate.py` applies one rule to every report: **a result at level
`error` fails the check; `warning` and `note` are printed and uploaded, nothing more.**
A scanner that crashes leaves no readable report, which the gate also fails on. Tools with
no severity (gitleaks, pip-audit, ruff) fail on any finding, for the reasons in the table.

## Where to see the results

- **Pull request checks:** six `analysis (<tool>)` jobs, parallel to `check`, so they never
  add to its time. A red one names the tool; its log lists each blocking finding.
- **Security → Code scanning** on GitHub: every SARIF report (all tools but pip-audit,
  which has no SARIF output), one category per tool, with severity, file and line, and
  history across commits. Findings below the gate are only visible here. On a pull request
  the same alerts are annotated on the diff.

## Running it locally

```bash
make analyze                       # every check, the same script CI runs
make analyze CHECKS="semgrep trivy"  # a subset
scripts/analyze.sh gitleaks        # or the script directly
```

Requires only **uv** and **Docker**. Reports are written to `analysis/` (git-ignored).

| Tool | How it runs | Pinned |
|---|---|---|
| ruff | `uv run` — the project's own, version from `uv.lock` | `uv.lock` |
| Semgrep, pip-audit, zizmor | `uvx <tool>==<version>`, cached by uv after the first run | `scripts/analyze.sh` |
| gitleaks, Trivy | `docker run` of the published image, pinned by digest | `scripts/analyze.sh` |

The first run downloads Semgrep's rules and Trivy's vulnerability database (about 130 MB,
kept in `~/.cache/trivy`); after that a full run takes under two minutes. No tool needs an
account, a token or a cloud call; Semgrep's usage metrics are turned off.

## Triage

Every finding is fixed or ignored with its reason written next to the ignore. As of
2026-11-11 (first run):

| Tool | Found | Outcome |
|---|---|---|
| ruff `S` | 1,474 `S101` and 12 others in tests | Ignored for `tests/**` in `pyproject.toml`: pytest asserts, fixture passwords, seeded RNGs, fixed-argv subprocesses, hard-coded table names in teardown |
| ruff `S` | 13 in `src/` | None exploitable; 12 ignored inline with the reason (`S105` on the documented local-dev MinIO/LiveKit defaults and on non-secret names such as the opt-out keyword, `S311` on deliberately seeded RNGs, `S310`/`S603` on loopback URLs and fixed argument lists); 1 removed by renaming a variable called `token` |
| ruff `C90` | 5 functions over 10 (11–22) | Ignored by file in `pyproject.toml`: route factories and event loops, refactor deferred |
| pip-audit | 70 advisories in 5 packages | **Fixed**: pillow 13.0.0, pypdf 6.21.4, urllib3 2.8.0, certifi, setuptools upgraded in `uv.lock` |
| Trivy | 1 HIGH, 1 MEDIUM, 1 LOW in `article/Dockerfile` (the LaTeX build image) | HIGH **fixed** (`USER nobody`); MEDIUM (`FROM ...:latest`) and LOW (no `HEALTHCHECK` on a one-shot build image) reported only |
| Trivy | 1 HIGH in `deploy/caddy/Dockerfile` (the ingress image, root by default), once the container image work landed | **Fixed**: runs as UID 10002 with `cap_net_bind_service` on the binary; `deploy.sh` hands it the certificate directory |
| Semgrep | 3 WARNING (actions on mutable tags) | **Fixed**: every action pinned by commit SHA |
| zizmor | 3 high, 2 medium | **Fixed**: actions pinned, `permissions: contents: read` by default, `persist-credentials: false` |
| gitleaks | 0 | — |

The local-dev credentials (`collection` / `collection-secret` for MinIO, `devkey` / `secret`
for LiveKit's dev mode) are deliberately public: they only work against containers on
`localhost`; production is to take its credentials from the instance role and SSM. No
`.gitleaks.toml` allowlist exists because gitleaks' default rules do not flag them; one
should be added only for a false positive, with its reason.
