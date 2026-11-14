#!/usr/bin/env bash
# Deploy one image tag on the production host. Run as root from /opt/tcc, by the SSM command
# CI sends after it has unpacked this directory there (no SSH: port 22 is closed).
#
#   /opt/tcc/deploy.sh <image-tag>          # the tag is the commit SHA CI built
#
# 1. render /opt/tcc/.env from every SSM parameter under /tcc/prod/ (name -> env var);
# 2. pull the app image, build Caddy (a no-op after the first time);
# 3. run the one-shot `migrate` service: migrations, then the idempotent seed;
# 4. `up -d` everything and wait until every health check passes;
# 5. ask the API which version it is serving, and fail unless it is the one asked for.
#
# Rollback is this same script with the previous tag. Migrations only ever add (see
# `collection db downgrade`), so an older image runs against a newer schema.
#
# Credentials: the instance role, through the AWS CLI's default chain. Nothing here holds a
# key, and the rendered .env is root-only.
set -euo pipefail

tag="${1:?usage: deploy.sh <image-tag>}"
cd "$(dirname "$0")"

prefix="${SSM_PREFIX:-/tcc/prod/}"
region="${AWS_REGION:-us-east-1}"
data_dir="${DATA_DIR:-/srv/tcc/data}"

log() { printf '\n==> %s\n' "$*"; }

# Every parameter under the prefix, whatever it is: the contract is that a new setting is a
# new parameter, and no change to this script. Written to a temporary file first and moved
# into place, so a failed render (expired role, throttling) leaves the last good .env
# rather than a truncated one, and the running stack keeps its configuration.
render_env() {
  local tmp
  tmp="$(mktemp .env.XXXXXX)"
  chmod 600 "$tmp"
  aws ssm get-parameters-by-path \
    --region "$region" --path "$prefix" --recursive --with-decryption \
    --query 'Parameters[].{Name:Name,Value:Value}' --output json |
    python3 -c '
import json, re, sys

prefix = sys.argv[1]
# Terraform creates every secret with this value (infra/app/parameters.tf). An unset optional
# one -- the key of the LLM provider not in use -- is left out, so the app sees it unset; an
# unset required one stops the deploy before a stack starts with a guessable password.
PLACEHOLDER = "placeholder-set-me-with-aws-ssm-put-parameter"
OPTIONAL = {"ANTHROPIC_API_KEY", "LLM_API_KEY"}
lines = []
for item in sorted(json.load(sys.stdin), key=lambda p: p["Name"]):
    full, value = item["Name"], item["Value"]
    name = full[len(prefix):]
    # A nested path (/tcc/prod/x/Y) or a lower-case name is a mistake in Parameter Store,
    # not a variable: refuse it rather than write a line compose would misread.
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
        sys.exit(f"refusing parameter {full!r}: not an env var name")
    if value == PLACEHOLDER:
        if name not in OPTIONAL:
            sys.exit(f"{full} still holds the Terraform placeholder: set it first")
        print(f"skipping {name}: not set", file=sys.stderr)
        continue
    # Single quotes make compose read the value literally ($ and # included). It has no
    # escape for a quote inside them, so such a value is refused rather than mangled.
    if "\x27" in value or "\n" in value:
        sys.exit(f"refusing {name}: a quote or a newline cannot be written to .env safely")
    lines.append(f"{name}=\x27{value}\x27")
if not lines:
    sys.exit(f"no parameters under {prefix}: refusing to write an empty .env")
print("\n".join(lines))
' "$prefix" >"$tmp" || { rm -f "$tmp"; exit 1; }
  mv "$tmp" .env
  echo "$(wc -l <.env) variables from ${prefix}"
}

compose() {
  IMAGE_TAG="$tag" DATA_DIR="$data_dir" \
    docker compose --project-name tcc-prod --env-file .env -f compose.prod.yml "$@"
}

log "rendering .env from SSM ${prefix}"
render_env

# Docker would create missing bind-mount sources as root, and the app runs as UID 10001
# (see the Dockerfile): the recordings directory must be its own before the call server
# writes the first one. Caddy runs as UID 10002 (deploy/caddy/Dockerfile) and writes its
# certificates and ACME account under caddy/.
install -d -m 0750 -o 10001 -g 10001 "$data_dir/calls"
install -d -m 0700 -o 10002 -g 10002 "$data_dir/caddy"
install -d -m 0700 "$data_dir/postgres"

log "pulling ghcr.io/gabrielforster/tcc:${tag}"
compose pull --ignore-buildable --quiet
compose build --quiet caddy

log "migrating and seeding"
compose run --rm migrate

log "starting the stack"
compose up -d --remove-orphans --wait --wait-timeout 600

# `--wait` proves every container is healthy; this proves the healthy API is the build that
# was asked for, and not, say, a container compose decided not to recreate.
serving="$(compose exec -T api python -c \
  "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['version'])")"
if [ "$tag" != "latest" ] && [ "$serving" != "$tag" ]; then
  echo "deploy.sh: asked for ${tag}, the API reports ${serving}" >&2
  exit 1
fi
log "serving ${serving}"

# The previous image, now untagged, would otherwise accumulate at ~3 GB a deploy.
docker image prune -f >/dev/null
