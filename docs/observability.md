# Observability: traces, metrics and logs of the running system

The two servers (`collection serve`, the API; `collection call serve`, the voice call) export
OpenTelemetry traces, metrics and logs over OTLP/HTTP to one container, `lgtm`: Grafana's
single-image distribution of an OpenTelemetry Collector, Prometheus (metrics), Tempo
(traces), Loki (logs) and Grafana. A second container, `otelcol`, adds the host's and the
containers' vital signs and a black-box probe of each server's `/health`. Grafana is at
`https://$GRAFANA_DOMAIN`, behind a login.

```
api  ──┐                          ┌─ Prometheus  (metrics, 30 days)
call ──┼─ OTLP/HTTP ─► lgtm:4318 ─┼─ Tempo       (traces, 7 days)  ─► Grafana ◄─ Caddy ◄─ GRAFANA_DOMAIN
otelcol┘   (collector)            └─ Loki        (logs, 7 days)
```

Telemetry is **off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set**. The test suite, the pipeline
steps and a plain `uv run` export nothing, install nothing and need no collector; the
instruments are created against the OpenTelemetry API, whose calls are no-ops until an SDK is
installed. `tests/test_telemetry.py` checks that, in a fresh process, nothing changes: no
provider, no log handler, no middleware, no patched HTTP client.

## What is instrumented

All of it is in [`src/collection/telemetry.py`](../src/collection/telemetry.py) and at the
call sites that use it.

**Automatic.** Every HTTP request to either server (FastAPI; `/health` is left out, the
container healthcheck would otherwise be most of the traces), every SQL query (SQLAlchemy,
statements without their values), every call to the openai-protocol LLM server
(`requests`). The Anthropic SDK emits its own spans through the same API, metadata only.

**Spans** for the steps only this code knows about. A chat message is one trace:

```
POST /chat/sessions/{session_id}/messages        (FastAPI)                    ~50 ms
├─ SELECT / INSERT / UPDATE ...                  the session and the customer's turn
├─ orchestrator.run
│  └─ agent.responsive                           event_type
│     ├─ retrieval                               retriever, queries, hits, top_score
│     │  └─ SELECT ...                           the tenant's pgvector search
│     └─ responder                               responder, intent, provider, model
│        └─ llm.complete                         provider, model, token counts
│           └─ POST /v1/chat/completions         (requests; Anthropic: its SDK's span)
└─ INSERT / UPDATE ...                           the answer, the retrieval record
```

(Measured locally against the development tenant: 29 spans, the pgvector search inside
`retrieval`. With the template responder there is no `llm.complete`.)

`/score` adds `scoring.predict` (task, model, batch size); a call turn is `call.turn` with
`stt` inside it.

**Metrics** (Prometheus names; durations in seconds):

| Metric | Labels | Where |
|---|---|---|
| `http_server_request_duration_seconds` | `job`, `http_route`, `http_request_method`, `http_response_status_code` | every request |
| `collection_scoring_batch_size`, `collection_scoring_duration_seconds` | `task`, `model` | `POST /score` |
| `collection_retrieval_duration_seconds` | `retriever`, `outcome` (hit/empty) | each message |
| `collection_retrieval_top_score` | `retriever` | best passage's cosine |
| `collection_responder_duration_seconds` | `responder` (`template`, `llm`, `llm:fallback`, `llm:over_budget`), `grounded`, provider, model | each answer |
| `collection_responder_fallbacks_total` | `reason`, provider, model | LLM replaced by the template |
| `collection_llm_tokens_total` | `gen_ai_token_type`, provider, model | each completion |
| `collection_compliance_checks_total`, `collection_compliance_violations_total` | `outcome` / `kind`, `composer`, `channel` | the proactive agent's gate |
| `collection_escalations_total` | `agent`, `reason` | every handover, counted in the orchestrator |
| `collection_voice_turn_duration_seconds` | `stage` (`endpointing`, `stt`, `agent`, `tts_first_audio`, `total`) | each call turn |
| `httpcheck_status` | `http_url`, `http_status_class` | `otelcol`'s probe of `/health` |
| `system_*`, `container_*` | `state`, `mountpoint`, `container_name`, ... | `otelcol`: host and containers |

Tempo also derives request rate and latency per span (`traces_spanmetrics_*`) and the service
graph from the traces.

**Logs.** Every `collection.*` logger at INFO and above, and every other logger at WARNING,
goes to Loki with the trace and span id of the request it was written in; in Grafana a log
line links to its trace and a trace to its logs. The handlers are on the root logger, so a
module that logs is shipped without being listed anywhere: the call page's network check
(`call_network_check outcome=<direct_ok|turn_tls_ok|blocked|signal_failed>` on
`collection.call`) is counted on the Voice call dashboard straight from Loki. The container's
stderr keeps showing warnings, now with `trace_id=`.

**Resource.** `service.name` is `collection-api` or `collection-call` (`OTEL_SERVICE_NAME`
wins), `service.version` is `APP_VERSION` (the commit), `deployment.environment` is
`DEPLOYMENT_ENVIRONMENT` (`prod` in compose, `dev` otherwise).

**No personal data.** Attributes and labels are bounded vocabularies: a task, a model, a
responder, an intent, a reason, a stage. Never a message, a transcript, a customer, a session
or an amount (a compliance violation is counted by kind, `invented`, not by the figure it
invented). The data in production is synthetic; the habit is what LGPD asks, and it is also
what keeps the number of series bounded. `tests/test_telemetry.py` asserts no span attribute
carries the message text.

## Dashboards

Provisioned from [`deploy/lgtm/grafana/dashboards/`](../deploy/lgtm/grafana/dashboards), in
the **TCC** folder. They are files, not database rows: a redeploy, or a lost data volume,
brings them back exactly as committed, and Grafana will not save an edit over them (copy one
with *Save as* to experiment).

| Dashboard | Shows |
|---|---|
| **Service overview** (home) | RED per server: request rate, 5xx share, p50/p95 latency per route, status codes, `/health` probe, recent error logs |
| **AI pipeline** | Scoring batch size and latency by model; retrieval latency, top score and empty rate; responder latency by responder, fallbacks, LLM tokens; escalations by reason; compliance outcomes |
| **Voice call** | Turn time split by stage (p50/p95), turns per minute, network-check outcomes (from Loki) |
| **Host** | CPU, memory, load, disk use of each real filesystem, network; CPU and memory per container |

Screenshots are in the Wiki. To find a single request, open *Explore → Tempo*, search by
service and span name; every log line of that request is one click away.

## Alerts

Provisioned from [`deploy/lgtm/grafana/alerting.yaml`](../deploy/lgtm/grafana/alerting.yaml):

- **API down** — the `otelcol` probe has had no 2xx from the API's `/health` for 2 minutes
  (or the probe itself stopped reporting). Black-box on purpose: a crashed process cannot
  report its own absence, and a hung one may keep exporting.
- **API 5xx rate above 5%** over 5 minutes; no traffic is not an incident.

Both go to the email contact point, address `GRAFANA_ALERT_EMAIL`, sent through
`GRAFANA_SMTP_HOST`/`_USER`/`_PASSWORD`/`_FROM`. All optional parameters in SSM: without
them the alerts still fire and are listed under *Alerting* in Grafana, only the mail is not
sent. No address is in the repository.

## Access

- Login is required. Anonymous access, sign-up and new organisations are disabled.
- Two accounts. **admin**, password `GRAFANA_ADMIN_PASSWORD`; and a **Viewer** for the
  examiners (login `GRAFANA_VIEWER_LOGIN`, default `banca`), password
  `GRAFANA_VIEWER_PASSWORD`. Both passwords are SSM `SecureString` parameters set by hand;
  the credentials are handed over at delivery and are written nowhere else — not here, not in
  the Wiki, not in Terraform state.
- `deploy.sh` runs [`deploy/lgtm/grafana-accounts.sh`](../deploy/lgtm/grafana-accounts.sh)
  inside `lgtm` after every `up`: it resets the admin password to the parameter (Grafana reads
  `GF_SECURITY_ADMIN_PASSWORD` only when it first creates its database), creates the Viewer if
  missing, sets its password and pins its role. Changing a password is: change the parameter,
  redeploy.
- The Viewer can use *Explore* (`GF_USERS_VIEWERS_CAN_EDIT`), because following a log line to
  its trace happens there. It still cannot save a dashboard, add a data source or change an
  alert rule (checked: 403).
- Grafana is not linked from the landing page. Nothing but Caddy reaches it, and no
  observability port is published on the host: the apps reach the collector on the compose
  network.

## Retention and footprint

Logs and traces 7 days, metrics 30 days (and at most 4 GB), all under `/srv/tcc/data/lgtm` on
the data volume, which `deploy.sh` creates. `lgtm` is capped at 1.5 GB of memory and
`otelcol` at 192 MB. Measured locally after half an hour of traffic: `lgtm` ~0.9 GB of
anonymous memory (processes: Grafana 410 MB, its collector 180 MB, Tempo 170 MB, Loki
155 MB, Pyroscope 115 MB, Prometheus 110 MB; `docker stats` says 1.4 GB with page cache),
`otelcol` ~80 MB. Grafana's own directory is the largest on disk at first (~640 MB, mostly
the plugins the image installs on its first start).

Why `otelcol` and not node-exporter + cAdvisor: one ~80 MB container instead of two (cAdvisor
alone takes a few hundred MB), the same collector build `lgtm` bundles, metrics pushed over
the same OTLP path as the apps' (nothing for Prometheus to scrape, so `lgtm`'s own
configuration is untouched), and the Docker socket it needs kept out of the container that
faces the internet. It also hosts the `/health` probe the "API down" alert reads.

## Running it locally

```sh
docker compose --profile obs up -d lgtm otelcol   # Grafana on http://127.0.0.1:3000
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
uv sync --extra otel                               # the SDK and exporters (--all-extras has it)
uv run collection serve                            # and/or: uv run collection call serve
```

Locally Grafana keeps the image's anonymous admin, bound to loopback; the dashboards and
alert rules are the production ones. The endpoint can also go in `.env`. Stop with
`docker compose --profile obs stop lgtm otelcol`; the data is the `lgtmdata` volume.

To check the instrumentation without a collector, `uv run pytest tests/test_telemetry.py`
runs the same SDK with in-memory exporters.
