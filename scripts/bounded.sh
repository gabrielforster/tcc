#!/usr/bin/env bash
# Run a command with a ceiling on the memory and CPU it may take, at low priority.
#
# One suite peaks near 1.8 GB and one core, but several at once (parallel worktrees, an
# agent per branch) once pushed a 32 GB laptop into swap until it stopped responding. Inside
# a systemd scope the kernel enforces the ceiling: past TEST_MEMORY the run is killed, and
# with swap disallowed it is killed instead of thrashing the disk for everyone else.
#
#   scripts/bounded.sh uv run pytest
#   TEST_MEMORY=6G TEST_CPU=400% scripts/bounded.sh uv run pytest
#   TEST_BOUNDED=0 scripts/bounded.sh uv run pytest     # no ceiling, e.g. in CI
#
# Without systemd (macOS, a container) only the lower priority applies, with a warning.
set -euo pipefail

memory="${TEST_MEMORY:-4G}"
cpu="${TEST_CPU:-200%}"

if [ "${TEST_BOUNDED:-1}" = "0" ]; then
  exec "$@"
fi

if command -v systemd-run >/dev/null 2>&1 && systemd-run --user --scope --quiet true 2>/dev/null; then
  set +e
  systemd-run --user --scope --quiet --collect \
    -p MemoryMax="$memory" -p MemorySwapMax=0 -p CPUQuota="$cpu" \
    -- nice -n 10 "$@"
  status=$?
  set -e
  # 137 is SIGKILL, which is how the kernel's OOM killer ends a process in the scope.
  if [ "$status" -eq 137 ]; then
    echo "bounded.sh: killed at the ${memory} memory ceiling; raise it with TEST_MEMORY=" >&2
  fi
  exit "$status"
fi

echo "bounded.sh: systemd-run unavailable; running at low priority with no memory ceiling" >&2
exec nice -n 10 "$@"
