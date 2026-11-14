#!/usr/bin/env bash
# Grafana's two accounts, from the environment: run inside the `lgtm` container by
# deploy.sh after every `up`, so Parameter Store stays the one place the passwords live.
#
#   docker compose exec -T lgtm bash -s <lgtm/grafana-accounts.sh
#
# * admin: GF_SECURITY_ADMIN_PASSWORD only takes effect when Grafana creates its database,
#   so a password changed in SSM afterwards would be silently ignored. It is reset here,
#   on the database directly, which works whatever the current password is.
# * the examiners' Viewer account (GRAFANA_VIEWER_LOGIN, `banca` unless set): created if
#   missing, its password set from GRAFANA_VIEWER_PASSWORD, and its role pinned to Viewer
#   -- it can look at every dashboard and explore the data, and change nothing.
#
# Grafana has no file provisioning for users, which is why this is a script and not YAML.
# Passwords go to curl on stdin (`--config -`), never on a command line where `ps` shows
# them; there is no jq or python in the image, hence the plain-shell JSON.
set -euo pipefail
# The request bodies below carry a password; only this process may read them.
umask 077

admin_password="${GF_SECURITY_ADMIN_PASSWORD:?}"
viewer_login="${GRAFANA_VIEWER_LOGIN:-banca}"
viewer_password="${GRAFANA_VIEWER_PASSWORD:?}"
api="http://127.0.0.1:3000/api"

for value in "$admin_password" "$viewer_password"; do
  case "$value" in
  *'"'* | *'\'*)
    echo "grafana-accounts: a password with a quote or a backslash cannot be sent safely" >&2
    exit 1
    ;;
  esac
done

# Grafana starts last in the stack and takes a few seconds to migrate its database.
for _ in $(seq 90); do
  curl -fsS "$api/health" >/dev/null 2>&1 && break
  sleep 2
done

# The same home and data paths run-grafana.sh gives the server, or the CLI would open an
# empty database of its own.
(
  cd /otel-lgtm/grafana
  GF_PATHS_HOME=/data/grafana GF_PATHS_DATA=/data/grafana/data \
    ./bin/grafana cli --homepath /otel-lgtm/grafana \
    admin reset-admin-password --password-from-stdin <<<"$admin_password" >/dev/null
)

# Admin basic auth, given to curl as a config file on stdin.
call() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -o /tmp/grafana-accounts.out -w '%{http_code}' -X "$method"
    -H 'Content-Type: application/json' --config -)
  [ -n "$body" ] && args+=(--data-binary @/tmp/grafana-accounts.body)
  [ -n "$body" ] && printf '%s' "$body" >/tmp/grafana-accounts.body
  printf 'user = "admin:%s"\nurl = "%s%s"\n' "$admin_password" "$api" "$path" | curl "${args[@]}"
}

trap 'rm -f /tmp/grafana-accounts.out /tmp/grafana-accounts.body' EXIT

status="$(call GET "/users/lookup?loginOrEmail=${viewer_login}")"
if [ "$status" = 404 ]; then
  status="$(call POST /admin/users \
    "{\"name\":\"Banca\",\"login\":\"${viewer_login}\",\"password\":\"${viewer_password}\"}")"
  [ "$status" = 200 ] || { echo "grafana-accounts: creating the viewer failed ($status)" >&2; exit 1; }
  echo "viewer '${viewer_login}' created"
elif [ "$status" != 200 ]; then
  echo "grafana-accounts: looking the viewer up failed ($status)" >&2
  exit 1
fi
id="$(grep -o '"id":[0-9]*' /tmp/grafana-accounts.out | head -n1 | cut -d: -f2)"

# Only when the viewer cannot already log in with it: Grafana answers 500 to a "change" to
# the password the account already has.
viewer_ok="$(printf 'user = "%s:%s"\nurl = "%s/user"\n' "$viewer_login" "$viewer_password" "$api" |
  curl -sS -o /dev/null -w '%{http_code}' --config -)"
if [ "$viewer_ok" != 200 ]; then
  status="$(call PUT "/admin/users/${id}/password" "{\"password\":\"${viewer_password}\"}")"
  [ "$status" = 200 ] || { echo "grafana-accounts: setting the viewer password failed ($status)" >&2; exit 1; }
  echo "viewer '${viewer_login}' password set"
fi
status="$(call PATCH "/org/users/${id}" '{"role":"Viewer"}')"
[ "$status" = 200 ] || { echo "grafana-accounts: pinning the viewer role failed ($status)" >&2; exit 1; }
echo "grafana accounts in step with the environment (admin, ${viewer_login})"
