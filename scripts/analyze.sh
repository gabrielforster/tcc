#!/usr/bin/env bash
# Static code and security analysis: the same commands in CI and on a laptop.
#
#   scripts/analyze.sh              # every check, in order
#   scripts/analyze.sh semgrep      # one of: ruff semgrep gitleaks pip-audit trivy zizmor
#   GITLEAKS_LOG_OPTS="base..head" scripts/analyze.sh gitleaks   # only a range of commits
#
# CI calls this script one check per step rather than repeating the commands in the
# workflow, so `make analyze` cannot drift from what gates a pull request. Each check
# writes a SARIF report to analysis/ (git-ignored), which the workflow uploads to GitHub
# code scanning, and exits non-zero only on a HIGH or CRITICAL finding (see
# docs/static-analysis.md for what that means per tool).
#
# Every tool is pinned here and nowhere else. The Python ones run through `uvx` (cached by
# uv after the first run); gitleaks and Trivy are Go binaries and run as their published
# images, pinned by digest, so the only thing to have installed is uv and Docker.
set -euo pipefail

SEMGREP_VERSION=1.185.0
PIP_AUDIT_VERSION=2.10.1
ZIZMOR_VERSION=1.32.0
GITLEAKS_IMAGE=ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
# ghcr.io rather than Docker Hub: the same image, and not subject to Hub's pull limits.
TRIVY_IMAGE=ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/analysis"
mkdir -p "$out"
cd "$root"

gate() { python3 "$root/scripts/sarif_gate.py" "$@"; }

# Each check runs as `run_x || failed+=(x)`, and bash suspends `set -e` inside a function
# called that way. So every step below ends in `|| return 1`, and each check deletes its
# previous report first: a scanner that crashed must not leave last run's clean SARIF for
# the gate to pass on.

run_ruff() {
  # The project's own ruff, at the version uv.lock pins: the Bandit (`S`) rules and the
  # rest of the configured set, so a finding here is exactly a finding in the `check` job.
  # The SARIF copy is for code scanning; `--exit-zero` on it because the second, plain run
  # is the gate and prints the findings readably.
  rm -f "$out/ruff.sarif"
  uv run --frozen --only-group dev ruff check src tests \
    --output-format sarif --output-file "$out/ruff.sarif" --exit-zero || return 1
  uv run --frozen --only-group dev ruff check src tests || return 1
}

run_semgrep() {
  # Registry rulesets, fetched from semgrep.dev on each run (no account, no token):
  # Python-specific rules, the OWASP Top 10 mapping and hard-coded secrets. Metrics off: the
  # CLI otherwise reports usage back to Semgrep.
  rm -f "$out/semgrep.sarif"
  uvx "semgrep==$SEMGREP_VERSION" scan --metrics off --quiet --disable-version-check \
    --config p/python --config p/owasp-top-ten --config p/secrets \
    --sarif-output "$out/semgrep.sarif" || return 1
  gate semgrep "$out/semgrep.sarif" || return 1
}

run_gitleaks() {
  # The whole history by default: a secret deleted in a later commit is still published.
  # In a git worktree `.git` is a file pointing into the main checkout's `.git`, so that
  # directory is mounted at the same path for git inside the container to follow.
  rm -f "$out/gitleaks.sarif"
  local common
  common="$(cd "$(git rev-parse --git-common-dir)" && pwd)" || return 1
  local args=(git /repo --no-banner --redact --report-format sarif --report-path /out/gitleaks.sarif)
  if [ -n "${GITLEAKS_LOG_OPTS:-}" ]; then
    args+=(--log-opts "$GITLEAKS_LOG_OPTS")
  fi
  # Every leak fails: gitleaks has no severity, and there is no harmless committed secret.
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "$root:/repo:ro" -v "$common:$common:ro" -v "$out:/out" \
    "$GITLEAKS_IMAGE" "${args[@]}" || return 1
}

run_pip_audit() {
  # The exact versions uv.lock resolves for every extra, hashes included, audited against
  # the PyPI advisory database without installing anything (`--disable-pip`). PyPI
  # advisories mostly carry no severity, so any known vulnerability fails; one that has to
  # be accepted is added as `--ignore-vuln <ID>` here, with its reason above it.
  rm -f "$out/requirements.txt"
  uv export --frozen --all-extras --no-emit-project --quiet -o "$out/requirements.txt" ||
    return 1
  uvx "pip-audit==$PIP_AUDIT_VERSION" --disable-pip --require-hashes --progress-spinner off \
    -r "$out/requirements.txt" || return 1
}

run_trivy() {
  # `fs` with two scanners: `misconfig` checks every Dockerfile, Terraform root,
  # Kubernetes manifest and Helm chart in the tree (infra/ and the app's Dockerfile are
  # picked up as they land, with no change here), and `vuln` reads uv.lock -- a second,
  # severity-rated opinion on the dependencies, which pip-audit cannot give. The image
  # itself is scanned in the deploy pipeline, where it is built. The cache keeps the
  # 130 MB vulnerability database between runs.
  rm -f "$out/trivy.sarif"
  local cache="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}"
  mkdir -p "$cache" || return 1
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "$root:/repo:ro" -v "$cache:/cache" -v "$out:/out" \
    "$TRIVY_IMAGE" fs /repo --cache-dir /cache --quiet \
    --scanners vuln,misconfig --skip-dirs /repo/.venv --skip-dirs /repo/analysis \
    --format sarif --output /out/trivy.sarif || return 1
  gate trivy "$out/trivy.sarif" || return 1
}

run_zizmor() {
  # GitHub Actions' own attack surface: unpinned actions, template injection, credentials
  # left in the checkout, over-broad token permissions. Offline: the online audits need a
  # GitHub token and only add checks on the pinned actions' upstream repositories.
  # zizmor exits non-zero whenever it finds anything at all, so its status is ignored and
  # the gate decides; a crash leaves an empty report, which the gate refuses as unreadable.
  rm -f "$out/zizmor.sarif"
  uvx "zizmor==$ZIZMOR_VERSION" --offline --format sarif .github/workflows \
    >"$out/zizmor.sarif" || true
  gate zizmor "$out/zizmor.sarif" || return 1
}

checks=("$@")
if [ ${#checks[@]} -eq 0 ]; then
  checks=(ruff semgrep gitleaks pip-audit trivy zizmor)
fi

failed=()
for check in "${checks[@]}"; do
  if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$check"; fi
  case "$check" in
    ruff | semgrep | gitleaks | trivy | zizmor) "run_$check" || failed+=("$check") ;;
    pip-audit) run_pip_audit || failed+=("$check") ;;
    *)
      echo "unknown check: $check" >&2
      exit 2
      ;;
  esac
  if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "analysis failed: ${failed[*]}" >&2
  exit 1
fi
echo "analysis passed: ${checks[*]}"
