#!/usr/bin/env bash
# Deploy one image tag to the production host through SSM Run Command -- the `deploy` job of
# .github/workflows/ci.yml, kept in a file so it can be read, linted and dry-run with a fake
# `aws` on PATH instead of only ever running for real.
#
#   scripts/ssm_deploy.sh <commit-sha>
#
# 1. find the instance by its tags (no instance id stored anywhere: it changes whenever
#    Terraform replaces the box);
# 2. send it an AWS-RunShellScript command that downloads deploy/ at that commit from
#    codeload.github.com into /opt/tcc and runs `deploy.sh <sha>` there;
# 3. poll the command until it finishes, print what it printed, and fail unless it succeeded.
#
# No SSH and no key: the caller holds short-lived credentials for tcc-ci-deploy, which may
# only describe instances, send AWS-RunShellScript to instances tagged Project=tcc and read
# the results (infra/bootstrap/iam_deploy.tf). Every AWS call below is one of those.
#
# The bundle and the image come from the same commit, so a rollback to an older tag also
# runs that commit's compose file and deploy.sh -- the pair that was tested together.
set -euo pipefail

sha="${1:?usage: scripts/ssm_deploy.sh <commit-sha>}"
repo="${GITHUB_REPOSITORY:-gabrielforster/tcc}"
region="${AWS_REGION:-us-east-1}"
# The host's own limit is below ours, so a stuck deploy is reported by SSM as TimedOut (with
# its output) before this script gives up on it. tcc-ci-deploy cannot cancel a command, so
# giving up first would leave it running unobserved.
remote_timeout="${REMOTE_TIMEOUT_SECONDS:-1800}"
deadline_seconds="${DEPLOY_TIMEOUT_SECONDS:-2400}"
poll_seconds="${POLL_SECONDS:-10}"

# Both end up inside a shell script that runs as root on the host, so they are checked
# against what they can legitimately be rather than quoted and hoped for.
if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ssm_deploy: '$sha' is not a full commit SHA" >&2
  exit 2
fi
if ! [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "ssm_deploy: '$repo' is not owner/name" >&2
  exit 2
fi

err="$(mktemp)"
trap 'rm -f "$err"' EXIT

group() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$*"; else echo "==> $*"; fi; }
endgroup() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi; }

# --- 1. the instance -----------------------------------------------------------------------
# Project=tcc is what the deploy role may send commands to; Name=tcc-prod narrows it to the
# one box, so a second Project=tcc instance (a replacement mid-apply) is an error to look
# at, not a coin toss.
instances="$(aws ec2 describe-instances --region "$region" \
  --filters Name=tag:Project,Values=tcc Name=tag:Name,Values=tcc-prod \
  Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
read -r -a ids <<<"$instances"
if [ "${#ids[@]}" -ne 1 ]; then
  echo "ssm_deploy: expected one running instance tagged Project=tcc Name=tcc-prod, found ${#ids[@]}: ${instances:-none}" >&2
  exit 1
fi
instance="${ids[0]}"
echo "instance: $instance"

# --- 2. the command ------------------------------------------------------------------------
# AWS-RunShellScript runs this with /bin/sh (dash on Ubuntu), so it is POSIX sh: no
# pipefail, hence download to a file and then unpack, never `curl | tar`, where a failed
# download would be hidden behind tar's exit status. deploy.sh itself is bash.
#
# `cloud-init status --wait`: on a freshly created instance the user data (Docker, the AWS
# CLI, the data volume mount) may still be running when the first deploy arrives. Its exit
# status is ignored because it also reports harmless warnings as non-zero; a host that really
# is broken fails inside deploy.sh, loudly.
#
# Only deploy/ is unpacked, and copied over /opt/tcc rather than replacing it: the rendered
# .env lives there and is not in the bundle.
read -r -d '' remote <<EOF || true
set -eu
sha=$sha
cloud-init status --wait >/dev/null 2>&1 || true
work=\$(mktemp -d)
trap 'rm -rf "\$work"' EXIT
curl -fsSL --retry 3 -o "\$work/src.tar.gz" "https://codeload.github.com/$repo/tar.gz/\$sha"
tar -xzf "\$work/src.tar.gz" -C "\$work" --strip-components=1 --wildcards "${repo#*/}-\$sha/deploy/*"
test -x "\$work/deploy/deploy.sh"
install -d -m 0755 /opt/tcc
cp -a "\$work/deploy/." /opt/tcc/
/opt/tcc/deploy.sh "\$sha"
EOF

params="$(jq -cn --arg script "$remote" --arg timeout "$remote_timeout" \
  '{commands: ($script | split("\n")), executionTimeout: [$timeout]}')"

# A just-created instance is not registered with SSM until its agent starts, and until then
# SendCommand answers InvalidInstanceId. That is worth a few minutes of retrying; any other
# error is not.
command_id=""
for attempt in $(seq 1 10); do
  if command_id="$(aws ssm send-command --region "$region" \
    --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "deploy ${sha:0:12} (run ${GITHUB_RUN_ID:-local})" \
    --timeout-seconds 600 --parameters "$params" \
    --query Command.CommandId --output text 2>"$err")"; then
    break
  fi
  if grep -q InvalidInstanceId "$err" && [ "$attempt" -lt 10 ]; then
    echo "instance not registered with SSM yet (attempt $attempt), retrying in 30s"
    sleep "${REGISTER_RETRY_SECONDS:-30}"
    continue
  fi
  cat "$err" >&2
  exit 1
done
echo "command: $command_id"

# --- 3. the result -------------------------------------------------------------------------
# Right after SendCommand the invocation may not exist yet (InvocationDoesNotExist); that is
# "not started", not an error.
status=Pending
start=$SECONDS
while :; do
  if status="$(aws ssm get-command-invocation --region "$region" \
    --command-id "$command_id" --instance-id "$instance" \
    --query Status --output text 2>"$err")"; then
    :
  elif grep -q InvocationDoesNotExist "$err"; then
    status=Pending
  else
    cat "$err" >&2
    exit 1
  fi
  case "$status" in
    Pending | InProgress | Delayed | Cancelling) ;;
    *) break ;;
  esac
  if [ $((SECONDS - start)) -ge "$deadline_seconds" ]; then
    echo "ssm_deploy: still $status after ${deadline_seconds}s; giving up (the command keeps running on the host)" >&2
    break
  fi
  sleep "$poll_seconds"
done

# SSM keeps only the last 24,000 characters of each stream; deploy.sh is terse enough that
# this is the whole log. The full one is in the host's SSM agent log.
result="$(aws ssm get-command-invocation --region "$region" \
  --command-id "$command_id" --instance-id "$instance" --output json)"
group "deploy.sh output"
jq -r '.StandardOutputContent // ""' <<<"$result"
endgroup
stderr="$(jq -r '.StandardErrorContent // ""' <<<"$result")"
if [ -n "$stderr" ]; then
  group "deploy.sh stderr"
  printf '%s\n' "$stderr"
  endgroup
fi

if [ "$status" != Success ]; then
  echo "ssm_deploy: command $command_id ended $status (exit $(jq -r '.ResponseCode' <<<"$result"))" >&2
  exit 1
fi
echo "deployed $sha on $instance"
