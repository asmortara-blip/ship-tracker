# Deployment

How to run Ship Tracker in production. Three supported paths, ordered
from fastest-to-deploy to most-control.

## 1. Streamlit Community Cloud (recommended for demos)

Zero infrastructure. Push to GitHub, point Streamlit Cloud at the repo,
done.

1. Push the branch you want to deploy to GitHub.
2. Open https://share.streamlit.io → **New app**.
3. Repo: `asmortara-blip/ship-tracker` · branch: your branch ·
   main file: `app.py`.
4. **Advanced settings → Secrets** — paste your API keys as TOML:
   ```toml
   FRED_API_KEY = "..."
   ALPHA_VANTAGE_KEY = "..."
   NEWS_API_KEY = "..."
   AISSTREAM_KEY = "..."
   ANTHROPIC_API_KEY = "..."
   ```
   See `.streamlit/secrets.toml.example` for the full list of expected
   keys; only `FRED_API_KEY` is meaningfully required — every other
   feed has either a free no-key fallback or a synthetic-data
   degradation path.
5. Deploy.

The `cache/` directory survives between reruns within a session but is
not persistent across cold starts on Streamlit Cloud. For persisted
narration/alerts/rules, use Docker or Fly.io below.

## 2. Docker (recommended for self-hosting)

Single-stage `Dockerfile` lives at the repo root. Build & run:

```bash
docker build -t ship-tracker:latest .

docker run --rm -p 8501:8501 \
    -e FRED_API_KEY="$FRED_API_KEY" \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    -v "$PWD/cache:/app/cache" \
    ship-tracker:latest
```

The `cache/` volume mount preserves:

  * Per-day narration cache (`cache/narrations/<YYYY-MM-DD>.json`)
  * User alert rules (`cache/alerts/rules.json`)
  * Fired alerts (`cache/alerts/alerts.json`)
  * Saved portfolio positions (`cache/portfolio/positions.json`)
  * Per-source feed cache files

Healthcheck is configured against Streamlit's built-in
`/_stcore/health` endpoint.

### Image notes
- Python 3.11-slim base; non-root user `app` (UID 10001).
- Build dependencies (`build-essential`, `libxml2`, `libxslt1.1`)
  are installed for the rare wheel-fallback path; on linux/amd64
  with current pip the scientific stack installs from prebuilt
  wheels and never compiles.
- `.dockerignore` keeps `cache/`, `logs/`, `tests/`, `docs/`,
  `.venv/`, and any `.streamlit/secrets.toml` out of the image.

## 3. Docker Compose: app + worker + webhook

For self-hosting where you want the Streamlit UI, the daily
investor-briefing worker, **and** the inbound ack webhook listener
running side-by-side, the repo ships a `docker-compose.yml` that wires
up three containers sharing the same `cache/` (SQLite DB + saved
reports) and `logs/` bind mounts:

| Service   | Role                                       | Port |
|-----------|--------------------------------------------|------|
| `app`     | Streamlit UI                               | 8501 |
| `worker`  | Daily `python -m worker.scheduler --push`, looped every 24h | —    |
| `webhook` | Stdlib HTTP listener for inbound acks (PagerDuty / curl) | 8502 |

All three services build from the existing `Dockerfile`; the worker
and webhook services just override the `CMD`.

```bash
cp .env.example .env
# edit .env — at minimum set FRED_API_KEY and ANTHROPIC_API_KEY
docker compose up -d
```

Tail logs from either container:

```bash
docker compose logs -f app
docker compose logs -f worker
```

Notes:

- The worker uses an **internal 24h sleep loop** (`while true; do … ; sleep 86400; done`)
  rather than host `cron` or a dedicated scheduler container. That means
  `docker compose up -d` is the whole deployment — no crontab edits, no
  systemd unit. The worker runs once immediately on `up`, then every 24h
  thereafter.
- On first start the SQLite DB at `cache/ship_tracker.db` is **auto-created**
  via the v1+v2+v3 schema migrations the first time either container
  opens a connection — no manual `init-db` step.
- Both containers bind-mount the same `./cache` directory, so reports the
  worker writes (`cache/reports/*.html`) are immediately listable in the
  app's report-history tab, and alert rules added in the UI are
  immediately visible to the worker's `--push` delivery step.
- Stop everything with `docker compose down`; the bind mounts ensure
  the DB + reports survive container removal.

### Webhook listener (`webhook` service)

The `webhook` container exposes a small stdlib `http.server` listener
on port `8502` that turns inbound HTTP POSTs into
`engine.alert_engine_v2.acknowledge_alert` calls against the shared
SQLite DB. This closes the loop between Ship Tracker alerts and the
external paging stack: when PagerDuty marks an incident resolved (or
an on-call engineer hits the endpoint via curl), the matching alert
flips to acknowledged inside Ship Tracker too — no manual UI click
required.

Endpoints:

| Method · Path                  | Body                                                  | Auth header                  | Effect                                                                    |
|--------------------------------|--------------------------------------------------------|------------------------------|---------------------------------------------------------------------------|
| `POST /ack/{alert_id}`         | empty (or anything — only the path matters)            | `X-Signature-SHA256`         | `acknowledge_alert(alert_id)`. Idempotent — unknown IDs return 200.       |
| `POST /ack-all`                | empty                                                  | `X-Signature-SHA256`         | `acknowledge_all()` — marks every unacked alert as acknowledged.          |
| `POST /webhooks/pagerduty`     | PagerDuty Webhooks v3 envelope (JSON)                  | `X-PagerDuty-Signature`      | When `event_type == "incident.resolved"` and `dedup_key` is non-empty, calls `acknowledge_alert(dedup_key)`. Other event types are 200 no-ops. |
| `POST /events`                 | External alert payload (JSON, see "Inbound /events" below) | `X-Hub-Signature-256` **or** `Authorization: Bearer …` | Creates a fresh `ShippingAlert` in the DB. Either auth header works; if both are present, BOTH must validate. |
| `GET /health`                  | n/a                                                    | **none — public**            | Liveness + cheap system-health probe. Returns JSON status block (see below). |

Every POST endpoint verifies an HMAC SHA256 signature against the raw
request body using the `WEBHOOK_SECRET` env variable (see
`.env.example`). Mismatches return `401`. Unknown paths return `404`,
`PUT`/`DELETE`/`PATCH`/`HEAD` return `405`, malformed JSON on the
PagerDuty endpoint returns `400`. The constant-time comparison
(`hmac.compare_digest`) guards against partial-match timing attacks.

`GET /health` is intentionally **unauthenticated** — it's the
liveness probe Docker / k8s / load balancers hit, and forcing HMAC on
those callers would mean every operator ships a signing helper
alongside the probe. The response carries no secrets.

Generate a secret (≥ 32 random bytes recommended):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Ack a single alert from the CLI:

```bash
SECRET="$WEBHOOK_SECRET"
ALERT_ID="alert-uuid-here"
# HMAC over an empty body (the path carries the alert_id).
SIG=$(printf '' | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -X POST "http://localhost:8502/ack/$ALERT_ID" \
     -H "X-Signature-SHA256: $SIG" \
     -d ''
# → {"acknowledged": true, "alert_id": "alert-uuid-here"}
```

Ack everything at once:

```bash
SIG=$(printf '' | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -X POST "http://localhost:8502/ack-all" \
     -H "X-Signature-SHA256: $SIG" \
     -d ''
```

For PagerDuty integration, point the v3 webhook at
`https://<your-host>:8502/webhooks/pagerduty` and configure the
shared secret in the PagerDuty webhook config. Because each Ship
Tracker alert is sent to PagerDuty with `alert_id` as the
`dedup_key`, the resolution event PagerDuty fires back already
carries the ID needed to ack the original alert.

#### `GET /health` — liveness + system probe

Returns a small JSON status block. Public on purpose so Docker /
k8s / load balancers can probe it without HMAC signing.

```bash
curl http://localhost:8502/health
```

Sample 200 response (status='ok' branch):

```json
{
  "status": "ok",
  "schema_version": 12,
  "users": 3,
  "now_utc": "2026-05-22T12:00:00+00:00",
  "up_seconds": 12345.678,
  "unacked_critical_count": 0,
  "recent_render_success_rate": 0.99,
  "current_outages": []
}
```

Field semantics:

| Field                          | Type             | Meaning                                                                 |
|--------------------------------|------------------|-------------------------------------------------------------------------|
| `status`                       | str              | `"ok"` · `"degraded"` · `"down"`                                        |
| `schema_version`               | int              | `state.db.SCHEMA_VERSION` — bumps when migrations run.                  |
| `users`                        | int              | Registered users (`auth.users.count_users()`).                          |
| `now_utc`                      | ISO timestamp    | Server's current UTC time.                                              |
| `up_seconds`                   | float            | Process uptime since the webhook listener bound the port.               |
| `unacked_critical_count`       | int              | Open `severity=CRITICAL` alerts in the last 30 days.                    |
| `recent_render_success_rate`   | float \| null    | UI render success rate over the last hour. `null` when no data yet.     |
| `current_outages`              | list[str]        | Sources whose most-recent ping was `down`.                              |

Status logic:

- `down` (HTTP `503`) — `count_users` raised or returned `-1` (DB is
  unreadable). Load balancers should pull this instance out.
- `degraded` (HTTP `200`) — `unacked_critical_count > 0` OR
  `recent_render_success_rate < 0.95` OR `current_outages` non-empty.
  Informational; keep the instance in rotation.
- `ok` (HTTP `200`) — none of the above.

Wire it into Docker as a HEALTHCHECK in a standalone container (the
`docker-compose.yml` already does this for the `webhook` service):

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:8502/health || exit 1
```

`wget --spider` is used instead of `curl` because the `python:3.11-slim`
base image doesn't ship curl but busybox `wget` is available.

## 4. Fly.io / Render / other PaaS

Any platform that runs a Docker container works. Minimum config:

  - Port: `8501`
  - Healthcheck: HTTP GET `/_stcore/health` (status 200)
  - Environment: set the API keys listed above
  - Volume: mount a 1-5 GB persistent volume at `/app/cache`

For Fly.io specifically, `fly launch` against the existing Dockerfile
will pick up the EXPOSE and CMD. Add a `[mounts]` block in `fly.toml`
to attach a volume:

```toml
[[mounts]]
source = "ship_tracker_cache"
destination = "/app/cache"
```

## Required secrets / environment variables

| Variable               | Source              | Required? | Fallback when missing |
|------------------------|---------------------|-----------|-----------------------|
| `FRED_API_KEY`         | https://fredaccount.stlouisfed.org/apikey | Recommended | Macro tabs show "no key" status |
| `ALPHA_VANTAGE_KEY`    | https://www.alphavantage.co/support/#api-key | Optional | Stock data uses Yahoo |
| `NEWS_API_KEY`         | https://newsapi.org | Optional | RSS feeds only |
| `AISSTREAM_KEY`        | https://aisstream.io | Optional | Synthetic vessel data |
| `ANTHROPIC_API_KEY`    | https://console.anthropic.com | Optional | tab_briefing uses template path |
| `VAULT_KEY`            | `python3 -c "import secrets; print(secrets.token_hex(32))"` | Recommended in prod | Auto-generated master key lands in kv_state (equivalent to no at-rest encryption against a DB-file leak) |

The app never crashes on a missing key — every feed degrades to either
a synthetic-data fallback or a clearly-labeled "not configured"
status. The "Data Sources" panel in the sidebar shows the freshness
state of every configured source.

### `VAULT_KEY` — opt-in delivery-channel secret encryption

`state.vault` wraps sensitive `delivery_channels.target` values
(Slack webhook URLs, PagerDuty integration keys, future user-supplied
secrets) in a self-describing `vault:v1:<base64>` envelope at rest in
SQLite. The vault is **opt-in per channel** — `save_channel(...,
encrypt_target=True)` persists the encrypted envelope while keeping
the dataclass field plaintext for the rest of the alert pipeline;
`load_channels()` decrypts transparently on read.

**Threat model.** This is a *"protect against casual DB leaks"* scheme:
it stops a copied-off `ship_tracker.db` or a leaked
`bulk_export.tar.gz` archive from immediately exposing every webhook
URL. It does **NOT** protect against an attacker with access to the
running process — the master key has to be readable by the process at
delivery time, so anyone who can read process memory, attach a
debugger, or read the same `VAULT_KEY` env var can read every secret.

**Master-key sources (in order of precedence).**

  1. `VAULT_KEY` env var (hex-encoded, 64 chars recommended).
  2. `st.secrets['VAULT_KEY']` if Streamlit secrets are configured.
  3. `kv_state['vault_master_key']` — auto-generated on first call,
     stored hex-encoded in the same SQLite file as the secrets it
     protects. Convenient for local development; **DO NOT rely on
     this in production** — it is equivalent to no at-rest encryption
     against a DB-file leak.

**Rotation.** `state.vault.rotate_key()` generates a fresh master key
and re-encrypts every currently-encrypted `delivery_channels.target`
against it. Plaintext targets (channels saved with the default
`encrypt_target=False`) are untouched — opt-in semantics persist
through rotation. The rotation records a `rotate_vault_key` event in
`audit_events` with `{"channels_rerencrypted": N}` — key material is
deliberately omitted from the audit payload.

**Stdlib only.** The implementation deliberately avoids adding the
`cryptography` library as a dependency. It uses `hashlib.blake2b` to
derive a per-message subkey, `hashlib.sha256` for the keystream, XOR
for the cipher, and `hmac.HMAC(SHA256)` (verified with
`hmac.compare_digest`) for authentication. This is **not** a
recommended construction against a motivated attacker; rotate the
key regularly to limit blast radius if it ever leaks.

## API server (`worker/api_server.py`)

A stdlib HTTP API bound to port `8503` that exposes the read +
narrow-write surface of Ship Tracker to external scripts. Sibling
to `worker/webhook_listener.py` (port `8502`, INBOUND ack /
webhooks); the API server is the OUTBOUND read + scoped-write
surface. Every authenticated endpoint requires an
`Authorization: Bearer <token>` header — generate a token per user
via `python -m tools.ops_cli tokens create <user_id> <label>` and
hand the raw secret to the script.

Run the server:

```bash
python -m worker.api_server --host 0.0.0.0 --port 8503
```

Endpoints:

| Method · Path                                | Body                              | Effect                                                                                  |
|----------------------------------------------|-----------------------------------|-----------------------------------------------------------------------------------------|
| `GET    /api/v1/health`                      | n/a                               | Public liveness + system-health probe. Same shape as `webhook_listener` `/health`.       |
| `GET    /api/v1/alerts`                      | n/a                               | Caller's alerts (window, severity filter, capped at 500).                               |
| `GET    /api/v1/alerts/<id>`                 | n/a                               | One alert, scoped to caller; 404 on cross-user.                                          |
| `POST   /api/v1/alerts/<id>/ack`             | empty                             | Acknowledge one alert. Idempotent; cross-user no-ops silently.                          |
| `GET    /api/v1/reports`                     | n/a                               | Caller's saved reports (metadata only).                                                  |
| `GET    /api/v1/reports/diff`                | n/a                               | Structured diff between two reports. Query: `?from=<id_a>&to=<id_b>`. 400 on missing param, 404 on unknown/cross-user. Response: `{report_a_id, report_b_id, summary, entries}` (see "Report-to-report diff" below). |
| `GET    /api/v1/reports/<id>/html`           | n/a                               | Raw HTML of one saved report.                                                            |
| `POST   /api/v1/reports/<id>/public`         | `{"expires_in_days": 30}` (opt)   | Generate a public-share slug; returns `{"slug": "..."}`. 404 on unknown/cross-user.      |
| `DELETE /api/v1/reports/<id>/public`         | empty                             | Revoke a public slug; returns `{"revoked": true}`. 404 on unknown/cross-user.            |
| `GET    /api/v1/rules`                       | n/a                               | Caller's alert-rule list.                                                                |
| `POST   /api/v1/rules`                       | `[ {rule}, … ]`                   | Replace caller's rule set. 415 on non-JSON Content-Type, 400 on malformed JSON.          |
| `DELETE /api/v1/rules`                       | empty                             | Wipe caller's rule set (per-user — does NOT touch other users' rules).                   |
| `GET    /api/v1/channels`                    | n/a                               | Caller's delivery channels (`target` omitted to avoid leaking webhook URLs). Each row carries `monthly_budget` (v25). |
| `POST   /api/v1/channels`                    | `DeliveryChannel` JSON dict       | Insert/upsert a delivery channel. Accepts optional `monthly_budget` (v25; 0 = unlimited). 400 if `channel_id` missing. |
| `PATCH  /api/v1/channels/<channel_id>`       | `{"monthly_budget": N}`           | Partial update on one channel (v25). 404 on cross-user / unknown id. Today exposes `monthly_budget` only; structured for additive fields. |
| `DELETE /api/v1/channels/<channel_id>`       | empty                             | Delete one channel. Cross-user deletes silently no-op (returns 200; row untouched).      |
| `GET    /api/v1/channels/<channel_id>/usage` | n/a                               | Per-channel monthly delivery counter (v25): `{channel_id, name, kind, budget, usage, pct, over_budget}`. 404 on cross-user / unknown id. |
| `POST   /api/v1/channels/<channel_id>/reset-usage` | empty                       | Zero the current month's counter (v25). 404 on cross-user / unknown id.                  |
| `GET    /api/v1/telemetry/llm`               | n/a                               | LLM-call usage summary, scoped to caller.                                                |
| `GET    /api/v1/telemetry/perf`              | n/a                               | Render-performance summary (process-wide; still gated by auth).                          |
| `GET    /api/v1/audit`                       | n/a                               | Caller's audit-log rows. Query: `?limit=100` (max 1000), `?action=login_success` filter. |
| `GET    /api/v1/incidents`                   | n/a                               | Caller's correlated alert incidents. Query: `?window=7` (days).                          |
| `GET    /api/v1/source-health`               | n/a                               | Global feed-health summary (NOT user-scoped). Query: `?window_hours=24`.                 |
| `GET    /api/v1/schedules`                   | n/a                               | Caller's report schedules. List of `{schedule_id, name, cron_expr, enabled, …}` rows.    |
| `POST   /api/v1/schedules`                   | `{name, cron_expr, enabled?}`     | Create a recurring report schedule. 400 on missing name / invalid cron_expr.             |
| `PATCH  /api/v1/schedules/<id>`              | `{name?, cron_expr?, enabled?}`   | Update one schedule; only the supplied fields move. 404 on cross-user / unknown id.      |
| `DELETE /api/v1/schedules/<id>`              | empty                             | Delete one schedule. 404 on cross-user / unknown id.                                     |
| `GET    /api/v1/openapi.json`                | n/a                               | Public OpenAPI 3.0 spec for the full surface above. No auth — SDK generators must be able to fetch the contract before they have a token. |

### OpenAPI 3.0 specification

The full API surface is documented as an OpenAPI 3.0 spec at
`docs/openapi.json` (+ a YAML twin at `docs/openapi.yaml`). The same
spec is served live by the API server at `GET /api/v1/openapi.json`
(public, no auth — same contract as `/health`).

Regenerate the on-disk artifacts from the in-tree spec builder
(`tools/openapi_gen.py`):

```bash
python -m tools.openapi_cli json     # → docs/openapi.json
python -m tools.openapi_cli yaml     # → docs/openapi.yaml
python -m tools.openapi_cli validate # structural smoke check; exits non-zero on error
```

The spec is hand-written (not auto-introspected from the dispatch
table) so it can carry descriptions / examples / per-response error
shapes that runtime introspection cannot recover. When you add or
change an endpoint in `worker/api_server.py`, edit
`tools/openapi_gen.py` in the same commit and rerun the CLI above.

Use the spec with standard OpenAPI tooling:

- **Swagger UI** — point its `url` config field at
  `http://<host>:8503/api/v1/openapi.json` to get a browsable docs UI
  with try-it-out request runner. Docker quick start:
  `docker run -p 8080:8080 -e SWAGGER_JSON_URL=http://host.docker.internal:8503/api/v1/openapi.json swaggerapi/swagger-ui`.
- **Redoc** — `redocly preview-docs docs/openapi.yaml` renders the
  three-pane reference docs layout.
- **openapi-generator-cli** — generates typed SDKs in ~50 languages
  from the spec. Example for Python: `openapi-generator-cli generate
  -i docs/openapi.json -g python -o sdks/python`.

**Per-user scoping.** Every write threads the `user_id` resolved from
the bearer token to the underlying engine call. Alice's token cannot
delete Bob's channel — the engine's `scope_filter_sql` excludes
Bob's row from the DELETE. The API still returns `200` in that case
so a caller cannot enumerate other users' channel ids by status code.

**Body conventions for writes.**

- `Content-Type: application/json` is required when a body is
  present; anything else → `415`.
- Malformed JSON → `400`.
- Empty body is permitted on `DELETE` and on `POST /reports/<id>/public`
  (in the latter case it defaults to 30 days).

### curl examples — one per write endpoint

```bash
TOKEN="<raw bearer token from tokens create>"
BASE="http://localhost:8503"

# POST /api/v1/rules — replace the caller's rule set.
curl -X POST "$BASE/api/v1/rules" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '[{"rule_id":"r1","name":"BDI surge","metric":"bdi","threshold_pct":5.0,"severity":"HIGH"}]'
# → {"saved": true, "count": 1}

# DELETE /api/v1/rules — wipe the caller's rules.
curl -X DELETE "$BASE/api/v1/rules" \
     -H "Authorization: Bearer $TOKEN"
# → {"reset": true}

# POST /api/v1/channels — add a Slack delivery channel.
curl -X POST "$BASE/api/v1/channels" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
           "channel_id": "ch-trading-desk",
           "name": "Trading desk Slack",
           "kind": "slack",
           "target": "https://hooks.slack.com/services/AAA/BBB/CCC",
           "severity_threshold": "HIGH",
           "enabled": true
         }'
# → {"saved": true, "channel_id": "ch-trading-desk"}

# DELETE /api/v1/channels/<id> — remove one channel.
curl -X DELETE "$BASE/api/v1/channels/ch-trading-desk" \
     -H "Authorization: Bearer $TOKEN"
# → {"deleted": true, "channel_id": "ch-trading-desk"}

# PATCH /api/v1/channels/<id> — set the per-channel monthly budget.
# 0 = unlimited (legacy). Positive cap suppresses further deliveries
# once usage >= budget for the current calendar month.
curl -X PATCH "$BASE/api/v1/channels/ch-trading-desk" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"monthly_budget": 200}'
# → {"channel_id": "ch-trading-desk", "updated": {"monthly_budget": 200}}

# GET /api/v1/channels/<id>/usage — peek the per-channel counter.
curl "$BASE/api/v1/channels/ch-trading-desk/usage" \
     -H "Authorization: Bearer $TOKEN"
# → {"channel_id": "ch-trading-desk", "budget": 200, "usage": 137,
#    "pct": 68.5, "over_budget": false, "name": "...", "kind": "slack"}

# POST /api/v1/channels/<id>/reset-usage — zero this month's counter.
curl -X POST "$BASE/api/v1/channels/ch-trading-desk/reset-usage" \
     -H "Authorization: Bearer $TOKEN"
# → {"channel_id": "ch-trading-desk", "reset": true}

# POST /api/v1/reports/<id>/public — issue a public-share slug.
curl -X POST "$BASE/api/v1/reports/<report-uuid>/public" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"expires_in_days": 7}'
# → {"slug": "xK3pYqR-ab12"}

# DELETE /api/v1/reports/<id>/public — revoke the slug.
curl -X DELETE "$BASE/api/v1/reports/<report-uuid>/public" \
     -H "Authorization: Bearer $TOKEN"
# → {"revoked": true}

# GET /api/v1/audit — caller's audit-log rows, newest-first.
#   Optional ?action=<verb> filters to one action verb.
#   ?limit defaults to 100, hard cap 1000.
curl "$BASE/api/v1/audit?limit=50&action=login_success" \
     -H "Authorization: Bearer $TOKEN"
# → {"items": [{"event_id": "...", "created_at": "...", "user_id": "...",
#               "action": "login_success", "entity_type": "...",
#               "entity_id": "...", "detail_json": {...}}, ...],
#    "count": 12}

# GET /api/v1/audit/export — JSONL export of audit rows for SIEM
#   ingestion. Same filter params as /audit (?action, ?limit) plus
#   ?since=<ISO-8601>, ?until=<ISO-8601>, and ?format=jsonl (default)
#   or ?format=json (envelope).
#
#   The JSONL format returns one JSON object per line, \n-delimited,
#   ending in a trailing newline. Content-Type is
#   application/x-ndjson; charset=utf-8 so Splunk / Vector / Loki
#   scrapers route the body to the right index.
#
#   Bodies over 100 KB use chunked Transfer-Encoding so the server
#   doesn't buffer the whole export in memory.
curl "$BASE/api/v1/audit/export?since=2026-05-01T00:00:00%2B00:00&limit=10000" \
     -H "Authorization: Bearer $TOKEN" \
     -o /var/log/ship-tracker-audit.jsonl
# → application/x-ndjson body; one event per line, e.g.
#   {"event_id":"...","created_at":"...","user_id":"...","action":"login_success",...}
#   {"event_id":"...","created_at":"...","user_id":"...","action":"save_rules",...}

# GET /api/v1/incidents — caller's correlated alert incidents.
#   Optional ?window=<days> (default 7).
curl "$BASE/api/v1/incidents?window=7" \
     -H "Authorization: Bearer $TOKEN"
# → {"items": [{"incident_id": "...", "started_at": "...",
#               "severity_max": "HIGH", "alert_count": 3,
#               "dominant_alert_type": "RATE_SURGE",
#               "entities_touched": {"tickers": [...], "routes": [...],
#                                    "ports": [...]},
#               "alert_ids": [...]}, ...],
#    "count": 4}

# GET /api/v1/source-health — global feed-health summary. NOT
#   user-scoped: every authenticated caller sees the same response.
#   Optional ?window_hours=<n> (default 24).
curl "$BASE/api/v1/source-health?window_hours=24" \
     -H "Authorization: Bearer $TOKEN"
# → {"items": [{"source": "fred", "count": 24, "up_count": 22,
#               "degraded_count": 1, "down_count": 1,
#               "avg_duration_ms": 312.5, "last_status": "up",
#               "last_started_at": "...", "is_outage": false}, ...],
#    "count": 9, "window_hours": 24, "total_pings": 216,
#    "current_outages": ["worldbank"]}

# GET /api/v1/schedules — caller's recurring report schedules.
curl "$BASE/api/v1/schedules" -H "Authorization: Bearer $TOKEN"
# → [{"schedule_id": "...", "name": "Morning Macro",
#     "cron_expr": "0 9 * * *", "enabled": true,
#     "last_run_at": null, "last_run_status": null,
#     "next_run_at": "2026-05-24T09:00:00+00:00", ...}]

# POST /api/v1/schedules — create a recurring schedule.
#   cron_expr supports *, */N, single ints, comma-lists.
#   Ranges (1-5) and L/# extensions are NOT supported.
curl -X POST "$BASE/api/v1/schedules" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "Morning Macro", "cron_expr": "0 9 * * *", "enabled": true}'
# → {"saved": true, "schedule_id": "...", "schedule": {...}}

# PATCH /api/v1/schedules/<id> — toggle / rename / re-cron.
#   Only the supplied fields are updated; the rest survive untouched.
curl -X PATCH "$BASE/api/v1/schedules/<schedule-uuid>" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"enabled": false}'
# → {"updated": true, "schedule_id": "...", "schedule": {...}}

# DELETE /api/v1/schedules/<id> — remove one schedule. 404 on cross-user.
curl -X DELETE "$BASE/api/v1/schedules/<schedule-uuid>" \
     -H "Authorization: Bearer $TOKEN"
# → {"deleted": true, "schedule_id": "..."}
```

`GET /api/v1/health` is intentionally **unauthenticated** so load
balancers / k8s probes can poll it without shipping a token:

```bash
curl http://localhost:8503/api/v1/health
```

The response shape is identical to `webhook_listener`'s `/health`
(see [GET /health — liveness + system probe](#get-health--liveness--system-probe)
above) so a single probe template works for both ports.

### Report-to-report diff (`GET /api/v1/reports/diff`)

Operators frequently want to know "what changed between today's
briefing and yesterday's" — new alpha signals, signals that flipped
direction, routes whose rate moved more than 5%, sentiment / risk
drift. Opening two browser tabs to eyeball the difference is the
status quo; this endpoint returns the same information as structured
JSON so the Streamlit tab, the CLI, and any external script consume
it the same way.

```bash
curl -H "Authorization: Bearer $TOKEN" \
    "http://localhost:8503/api/v1/reports/diff?from=<id_a>&to=<id_b>"
```

Response (200 OK):

```json
{
  "report_a_id": "uuid-a",
  "report_b_id": "uuid-b",
  "summary": {"added": 2, "removed": 1, "changed": 4},
  "entries": [
    {
      "category": "sentiment",
      "change_type": "changed",
      "key": "sentiment_score",
      "before": 0.12,
      "after": 0.41,
      "description": "Sentiment score: 0.12 -> 0.41 (+0.29)"
    },
    {
      "category": "signal",
      "change_type": "added",
      "key": "ZIM-momentum-up",
      "before": null,
      "after": {"direction": "LONG", "confidence": 0.78},
      "description": "New signal: ZIM-momentum-up (LONG, conf 0.78)"
    },
    {
      "category": "route",
      "change_type": "changed",
      "key": "FBX01_CHINA_US_WEST",
      "before": {"value": 2400.0, "status": "Stable"},
      "after": {"value": 2580.0, "status": "Accelerating"},
      "description": "FBX01_CHINA_US_WEST: value 2400.00 -> 2580.00 (+7.5%)"
    }
  ]
}
```

**Categories:** `signal`, `route`, `sentiment`, `risk`, `metadata`.
**Change types:** `added`, `removed`, `changed`.

**Thresholds (defaults):**

- Signal confidence shift surfaces when `|conf_b - conf_a| > 0.10`.
- Route value change surfaces when the percent move exceeds `5%`.
- Sentiment-score change surfaces when `|delta| > 0.05`.
- Signal direction flips and risk-level changes always surface.

**Per-user scoping.** Both `from` and `to` are resolved within the
caller's report scope via `list_reports(user_id=...)`. A user who
tries to diff another user's report sees the same 404 they would for
a non-existent id — no enumeration leak. Same indistinguishable-
failure contract used by `/reports/<id>/html`.

**Schema-version safety.** When the two payloads carry different
`schema_version` stamps, the diff includes an explicit
`metadata / schema_version` entry warning that subsequent rows may
not be directly comparable.

**CLI mirror.** The same diff is available from the operator CLI:

```bash
python -m tools.ops_cli reports diff <id_a> <id_b> [--user-id <id>] \
    [--format md|json]
```

`--format md` (default) prints Markdown; `--format json` prints the
same JSON shape the API returns. Missing ids exit with code 1 and a
single-line stderr message.

**UI mirror.** The Streamlit Report tab has a "Compare to a previous
report" section under "History" that exposes the same diff via two
selectboxes + a Markdown download button.

### Per-user rate limiting

Every authenticated endpoint is gated by an in-process **token-bucket
rate limiter** keyed on the `user_id` resolved from the bearer token.
A misbehaving client (or a leaked token) gets throttled with a `429
Too Many Requests` response rather than starving the worker of CPU
or filling SQLite with audit-log churn.

**Defaults:** capacity `120` (burst size) + refill `2.0` tokens/sec
(steady-state). A fresh user can fire 120 requests back-to-back, then
sustain 2 req/sec indefinitely.

**Health endpoint is exempt.** `GET /api/v1/health` is short-circuited
*before* the auth check, so load balancers can probe it in a tight
loop without ever tripping the limiter.

**Tuning via env vars** (read on every request, so updates take effect
on process restart):

| Variable                    | Default | Meaning                                |
|-----------------------------|---------|----------------------------------------|
| `RATE_LIMIT_CAPACITY`       | `120`   | Maximum burst (tokens in a full bucket).|
| `RATE_LIMIT_REFILL_PER_SEC` | `2.0`   | Steady-state refill rate (tokens/sec). |

Malformed values (non-int, ≤ 0) silently fall back to the default —
a typo in your `.env` will not crash the server at request time.

**429 response shape:**

```json
{"error": "rate_limited", "retry_after_seconds": 1}
```

The standard **`Retry-After`** header (RFC 7231 §7.1.3, integer
seconds) is also set so off-the-shelf HTTP clients can back off
without parsing the body. The value is rounded UP and floored at `1`
— `Retry-After: 0` would contradict the 429.

**Scope:** in-process only. Each worker container maintains its own
bucket dict, so horizontally scaling the API behind a round-robin
load balancer will let a single user effectively get `N × capacity`
burst headroom across `N` workers. If that becomes a problem, swap
the in-process limiter for a Redis-backed one — `auth.rate_limit`'s
public API (`check_rate_limit(user_id, *, capacity, refill_per_sec)`)
is stable across that swap.

## Operator CLI (`python -m tools.ops_cli`)

Every common admin action the Streamlit UI exposes is also reachable
from the shell via `python -m tools.ops_cli <command>`. Useful when
SSH'd into the container, in CI for bulk maintenance, or wired into
host cron. Every subcommand accepts `--json` for machine-readable
output and follows the exit-code contract: `0` success, `1` handler
raised (single-line stderr message — no traceback), `2` argparse
rejected the invocation.

| Subcommand                         | What it does                                                       |
|------------------------------------|--------------------------------------------------------------------|
| `status`                           | Schema version + counts (users, alerts, channels).                 |
| `alerts list / ack / ack-all / metrics` | Recent alerts, acknowledge one or all, aggregate ack metrics. |
| `channels list / delete / usage / reset-usage / set-budget` | Delivery-channel admin including per-channel monthly delivery budgets (v25). |
| `reports list / delete / stats / diff` | Saved-report admin, plus `diff <id_a> <id_b> [--format md\|json]` to compare two reports (matches `GET /api/v1/reports/diff`). |
| `telemetry usage / recent / prune` | LLM call telemetry.                                                |
| `perf summary`                     | Render-performance summary.                                        |
| `health summary / ping`            | Data-source health.                                                |
| `health-alerts status / enable / disable / run-once` | Auto-fire ShippingAlerts when a data source goes red / yellow. |
| `perf-budgets list / set / reset / check` | Per-tab render-latency budgets; auto-fire PERF_BUDGET alerts when a tab's p95 blows past its budget. |
| `anomalies check / configs / enable / disable / set` | Time-series anomaly detection across BDI, FBX, SCFI, WTI, bunker, port-wait, transpacific delay; fires ANOMALY alerts on z-score / pct-drift / rolling-mean drift past the per-metric threshold. |
| `users list / create`              | User-account admin.                                                |
| `tokens list / create / revoke`    | Per-user API-token admin.                                          |
| `export`                           | Build a bulk-state tar.gz (see `Backup / Restore` below).          |
| `mfa enable / disable / status`    | TOTP second-factor enrollment per user.                            |
| `mfa recovery-codes / regenerate-codes` | Count or regenerate single-use scratch codes for MFA recovery. |
| `invite create / list / revoke`    | Admin-issued signup invitations (pre-authorize signups by email).  |
| `filters list / delete`            | Per-user saved filter presets.                                     |
| `incidents list / stats`           | Correlated-incident view over the alert table.                     |
| `settings show / set`              | Per-user preferences (timezone, theme, defaults).                  |
| `schedules list / create / delete / enable / disable / run-once` | Cron-driven recurring report schedules (per-user). |
| `audit export`                     | JSONL export of audit_events for SIEM ingestion (Splunk / Vector / Loki). |

### MFA enrollment from the CLI

```bash
# 1. Generate a fresh secret + provisioning URI and flip the DB flag.
python -m tools.ops_cli mfa enable <user_id>
# stdout carries two lines an operator can copy-paste into an
# authenticator app:
#   secret: JBSWY3DPEHPK3PXP...
#   provisioning_uri: otpauth://totp/Ship%20Tracker:alice?secret=...
# The secret is what you paste into apps that don't ship a QR scanner;
# the URI is the canonical KeyURI form a QR generator consumes.

# 2. Confirm enrollment.
python -m tools.ops_cli mfa status <user_id>
#   user_id : <user_id>
#   enabled : True

# 3. Disable (falls back to password-only on next login).
python -m tools.ops_cli mfa disable <user_id>
```

### MFA recovery codes (v21)

When `mfa enable` succeeds, the platform auto-mints 10 single-use
recovery codes alongside the TOTP secret. They are surfaced on stdout
**exactly once** in the format `XXXXX-XXXXX`:

```bash
python -m tools.ops_cli mfa enable <user_id>
#   secret: ...
#   provisioning_uri: ...
#   user_id : <user_id>
#   enabled : True
#   recovery_codes (save these — shown once):
#     A7K2P-9Q3MN
#     ...
```

After the operator dismisses the terminal, the plaintext codes are
unrecoverable — only the per-code pbkdf2-sha256 hash + salt land in
the `mfa_recovery_codes` table. If the user loses their authenticator
they can sign in with any **unused** code from the original batch.

```bash
# How many codes does this user still have?
python -m tools.ops_cli mfa recovery-codes <user_id>
#   user_id      : <user_id>
#   unused_count : 8

# Wipe the current batch and issue a fresh one.
python -m tools.ops_cli mfa regenerate-codes <user_id>
#   user_id : <user_id>
#   count   : 10
#   recovery_codes (save these — shown once):
#     ...
```

Disabling MFA wipes the recovery codes as a side effect — leaving them
behind would create an invisible back door past the "MFA off" state
the user sees.

### User invitations (v21)

An admin can pre-authorize a signup by minting an invitation. The
recipient signs up via the standard signup form with the token
supplied as `?invite=<token>` (or by passing `invite_token=` to
`auth.users.signup`). The token is consumed atomically with the
new-user insert and cannot be reused.

```bash
# Mint an invite. The token is shown EXACTLY ONCE — share it
# out-of-band with the recipient.
python -m tools.ops_cli invite create \
    --invited-by <admin_user_id> \
    --email alice \
    --role user \
    --expires-days 7
#   invite_token: <32-char-url-safe-token>
#   invite_id  : <uuid>
#   email      : alice
#   role       : user
#   expires_at : 2026-05-30T...

# List pending invites (consumed ones are hidden by default).
python -m tools.ops_cli invite list

# Revoke an unconsumed invite. Already-consumed invites cannot be
# revoked — those rows are part of the audit trail.
python -m tools.ops_cli invite revoke <invite_id>
```

The `--email` field is optional. When set, the signup's username must
equal that value exactly — usernames double as the canonical login
identifier in this codebase. The `--role` field defaults to `user`;
pass `--role admin` ONLY when you mean it (admin invites can never be
silently granted by typo).

### Saved filter presets

```bash
# List a user's saved presets, optionally narrowed to one surface.
python -m tools.ops_cli filters list --user-id <id>
python -m tools.ops_cli filters list --user-id <id> --scope alerts

# Delete one preset by name + scope.
python -m tools.ops_cli filters delete <name> --scope alerts --user-id <id>
```

Saving a new preset is a UI-only operation — encoding the per-scope
payload vocabulary on the command line isn't worth the surface area.

### Per-tab perf budgets

`engine/perf_telemetry.py` records per-tab render durations; `engine/perf_budgets.py`
is the read-only consumer that classifies them against operator-chosen
budgets and fires a `PERF_BUDGET` ShippingAlert when a tab's observed p95
blows past its ceiling. The worker scheduler invokes
`run_perf_budget_check_job` once per pass (after the source-health alerter,
before the bulk-export prune) so a regression is surfaced without manual
scrolling of the perf panel.

Defaults ship with the module:
`ui.tab_overview` 1.5s · `ui.tab_alerts` 2.0s · `ui.tab_deep_dive` 4.0s ·
`ui.tab_operator_overview` 3.0s · anything else 2.5s. Severity classification:
observed_p95 between 1x and 2x budget → `warn` (MEDIUM alert);
observed_p95 > 2x budget → `critical` (HIGH alert). A min-sample threshold
of 5 observations prevents one slow render from firing a spurious breach
on a low-traffic tab. Per-tab cooldown = the budget's `window_hours` so a
chronically-slow tab does not carpet-bomb the alert table.

Budgets + cooldowns ride the existing `kv_state` table — no schema bump.

```bash
# Show every budget, the current p95, the sample count, and status
# (ok / warn / critical / no-data).
python -m tools.ops_cli perf-budgets list

# Tighten the budget for one tab.
python -m tools.ops_cli perf-budgets set ui.tab_overview --max-p95 1.0

# Wipe customisations and revert to the shipped defaults.
python -m tools.ops_cli perf-budgets reset

# Force the check + alert pass to run NOW (useful when debugging cooldown
# or verifying a freshly-saved budget). Prints the count dict.
python -m tools.ops_cli perf-budgets check
```

The same panel surfaces in the Data Health tab under "Tab Performance" >
"Performance Budgets" — operators can edit a budget inline via the
"Edit budgets…" expander without leaving the UI.

### Time-series anomaly detection

`engine/anomaly_detect.py` is the drift detector that catches the
subtler patterns static rules miss: BDI creeping 2%/day for ten
sessions, freight gradually diverging from its 30-day baseline, bunker
quietly walking up by a standard deviation a week. The module is a
read-only consumer that lazy-loads the source feeds (FRED for BDI /
WTI / bunker / SCFI proxy; the Freightos scraper for FBX routes; the
port loader for LA / Long Beach wait days), runs one of three
statistical checks against per-metric configs, and fires an `ANOMALY`
ShippingAlert when one trips. The worker scheduler invokes
`run_anomaly_detection_job` once per pass (after the per-tab perf-budget
check, before the alert-escalation pass) — sub-daily cadence is right
for drift that builds over days while staying inside any single-day
threshold.

The three methods:

* **`zscore`** — mean / std on the lookback window; test statistic is
  `|x - mu| / sigma` on the most recent value. The classic "is the
  latest tick a tail event" check.
* **`pct_drift`** — mean on the lookback window; test statistic is the
  % deviation of the most recent value vs the mean. The `z_threshold`
  field is re-interpreted as a percentage. Use for non-stationary
  series where the z-score is too volatile (oil during a regime shift).
* **`rolling_mean_deviation`** — compares the last-7 rolling mean
  against the lookback mean. Catches sustained drift that a
  single-observation z-score misses: the drift IS the smoothed series
  walking steadily away from baseline.

Severity bands (closed at the lower end):

* `|z| in [z_threshold, 2x)` → MEDIUM
* `|z| in [2x, 3x)`          → HIGH
* `|z| >= 3x`                → CRITICAL

Configs ship with the module for the eight built-in metrics: BDI, WTI,
bunker, SCFI, FBX trans-Pacific eastbound, FBX global, LA / Long Beach
port wait, transpacific delay days. Each carries a 30-day default
lookback and a 2.5σ threshold (rate-style metrics use pct_drift with
a percentage threshold). Configs + cooldowns ride the existing
`kv_state` table — no schema bump. The per-metric cooldown defaults to
24 hours so an already-fired metric stays quiet until tomorrow
regardless of how often the worker re-checks.

A min-samples threshold (default 14 observations) prevents cold-start
noise: a metric we just started tracking cannot fire an anomaly until
its baseline has enough history to mean anything.

```bash
# Show every config — metric, enabled flag, method, lookback, threshold.
python -m tools.ops_cli anomalies configs

# Force the detection + alert pass to run NOW (prints counts + every
# detected hit). Useful when verifying a freshly-saved config or debugging
# cooldown.
python -m tools.ops_cli anomalies check

# Toggle a single metric without deleting its config row.
python -m tools.ops_cli anomalies enable bdi
python -m tools.ops_cli anomalies disable bdi

# Tighten the threshold + extend the lookback for one metric.
python -m tools.ops_cli anomalies set bdi --z-threshold 3.0 --lookback-days 45

# Switch detection method (zscore / pct_drift / rolling_mean_deviation).
python -m tools.ops_cli anomalies set fbx_global --method rolling_mean_deviation
```

The same panel surfaces in the Data Health tab under "Tab Performance"
> "Anomaly Detection" — operators see the current hit list (metric,
observed, baseline, z-score, severity) and can edit any config inline
via the "Anomaly detection settings" expander. A "Run detection now"
button forces an immediate pass without waiting for the cron tick.

What this module is NOT: it does NOT bypass alert dedup (every fire
flows through `save_alerts`); it does NOT fire for metrics under the
min-sample threshold; it does NOT log raw proprietary metric values
verbatim — alert bodies carry the summary statistics (z, drift %,
baseline mean + std) operators need to triage without leaking the
point value of a commercially-sensitive series.

### Per-channel monthly delivery budgets (v25)

A delivery channel can carry an integer monthly cap on outbound
alerts — useful for "Slack #trading-desk gets max 200 alerts/month;
PagerDuty gets max 50" policies that protect against runaway noise.
The schema is a single `delivery_channels.monthly_budget` column
(v25). `0` is the legacy "unlimited" sentinel and preserves the
pre-v25 behaviour for every existing channel until an operator
opts in by setting a positive cap.

How the cap is enforced:

- `deliver_alert` runs the budget check AFTER `channel.enabled`,
  per-channel severity threshold, per-channel quiet hours, AND the
  per-user notification-prefs gate. So the counter only reflects
  deliveries the operator actually intended to send.
- When the per-user-per-channel-per-month counter is at or above
  the cap, the dispatch is skipped + a `budget_suppressed_counter`
  kv_state row is bumped. The `DeliveryResult` carries
  `success=False` + `status_code=429` so the caller can distinguish
  "throttled by budget" from "transport failure".
- The counter ONLY increments on a SUCCESSFUL delivery. A 5xx
  / timeout / SMTP outage does NOT burn the budget.
- `send_test_ping` (the operator "verify this channel works"
  button) is EXEMPT from the budget — verification must not
  consume production quota.

Counter storage uses a per-month kv_state key
`channel_usage:<user_id>:<channel_id>:<YYYY-MM>` so the monthly
rollover is implicit (each new month writes a fresh row) and an
operator-triggered reset is a single DELETE. Per-user scoping is
enforced by the `user_id` segment of the key — alice's saturated
counter does not affect bob's deliveries on the same channel.

```bash
# Show every channel's current monthly counter + budget + pct.
python -m tools.ops_cli channels usage --user-id <id>

# Zero one channel's counter for the current month (operator-
# triggered reset after a noisy week ended early).
python -m tools.ops_cli channels reset-usage <channel_id> --user-id <id>

# Set / change the monthly cap on one channel. 0 = unlimited.
python -m tools.ops_cli channels set-budget <channel_id> \
    --budget 200 --user-id <id>
```

API surface (mirrors the CLI):

- `GET /api/v1/channels/<id>/usage` — read the current month's
  counter + budget for one channel.
- `POST /api/v1/channels/<id>/reset-usage` — zero the counter.
- `PATCH /api/v1/channels/<id>` with `{"monthly_budget": N}` —
  update the cap. (`POST /api/v1/channels` also accepts the field
  on create.)
- `GET /api/v1/channels` returns each row with its `monthly_budget`
  alongside the existing fields.

UI surface (Alert Center → Delivery Channels):

- The channels table gains "Budget" + "Usage" columns. Usage cell
  flips amber at 80% and red once the cap is exhausted so an
  operator scanning the table can triage at-a-glance.
- Each channel with a positive budget gets a "Reset usage" button
  next to its "Send test ping" / "Delete" buttons.
- The Add Channel form has a "Monthly delivery budget" input
  (defaults to 0 — unlimited).

### Alert replay (channel verification with real alerts)

Operators sometimes need to re-deliver a historical alert (last week's
BDI spike, last month's port shutdown) to a single channel for
verification — to confirm a new channel config works against REAL
alerts they remember, not the synthetic `send_test_ping` payload.
`engine.alert_replay` + the `tools.replay_cli` CLI front this.

How replay differs from a real fire and from `send_test_ping`:

- The dispatched alert's title is prefixed with `[REPLAY] ` so the
  channel recipient can tell at a glance that this is a re-send, not
  a live event. The original DB row is NOT mutated — only the
  dispatched payload.
- The per-channel monthly delivery budget is NEITHER checked NOR
  incremented. Replay traffic is operator-driven test traffic and
  must not exhaust the cap that's there to silence noisy real fires.
- The dispatch is recorded in the audit log as
  `action='alert_replay'`, carrying `{alert_id, channel_id, success}`
  in the detail payload. A security review can distinguish replays
  from real fires (`action='alert_fire'`) and synthetic test pings
  (`action='test_ping'`).
- The wire protocol per channel kind (Slack / Email / SMS / Webhook /
  Discord / PagerDuty) is the same one used by `deliver_alert` —
  the replay path goes through `_dispatch_alert`, so what hits the
  network is exactly what a real fire would have looked like.

Per-user scoping is enforced on BOTH the `alert_id` AND the
`channel_id`. bob cannot replay alice's alert; alice cannot replay
her alert to bob's channel. A cross-user attempt returns
`ReplayResult(success=False, message='alert not found or not owned')`
— the same observable outcome as querying an unknown id, so an
attacker cannot enumerate other users' ids by probing.

```bash
# Replay one alert to one channel.
python -m tools.replay_cli replay <alert_id> \
    --channel-id <channel_id> --user-id <user_id>

# Bulk replay every HIGH severity alert from the last 7 days.
python -m tools.replay_cli bulk \
    --channel-id <channel_id> --user-id <user_id> \
    --since 7d --severity HIGH --limit 10

# Bulk replay all BDI_MOVE alerts from the last 30 days, JSON output
# suitable for piping into jq.
python -m tools.replay_cli bulk \
    --channel-id <channel_id> --user-id <user_id> \
    --since 30d --alert-type BDI_MOVE --json
```

Recurring use cases:

- **Channel config validation.** Operator just rewired a Slack
  webhook or swapped a PagerDuty integration key. They replay last
  week's CRITICAL fires against the rewired channel to confirm the
  payload formatting + routing still work end-to-end.
- **Channel migration testing.** Two channels point at the same
  destination during a migration. Operator replays the prior week's
  alerts to the NEW channel and visually confirms parity before
  deleting the old one.
- **Weekly review.** Replay the week's HIGH+CRITICAL alerts to a
  review-only channel so the trading desk + ops on-call can
  re-examine what fired in one place, with the audit log proving
  these were retrospective (not new events).

CLI exit codes:

- `0` — every replay succeeded (or no work was requested)
- `1` — handler raised; message went to stderr
- `2` — argparse rejected the invocation (missing required flag)
- `3` — handler ran cleanly but at least one individual replay
  failed (unknown id, cross-user, dispatch failure). Distinct from
  `1` so automated wrappers can pin the boundary.

Defaults + caps:

- `--limit` defaults to 50. The hard cap is 200 — values exceeding it
  are silently clamped down. The clamp holds the blast radius if an
  operator forgets to narrow their filter.
- `--since` accepts `Nd` / `Nh` / `Nm` (days, hours, minutes). A
  malformed spec writes a one-line warning to stderr and proceeds
  WITHOUT the since filter (matches the rest of the CLI family:
  typos shouldn't abort the whole command).
- The 30-day lookback inside `replay_alert` matches the implicit
  retention window of the alerts table (`_MAX_STORED=500`); older
  rows may have been trimmed and will surface as
  `'alert not found or not owned'`.

### Incident correlation

The correlation engine groups alerts that fired together (same window,
related entities) into incidents at read time — no schema bump, no
back-fill.

```bash
# List the most recent correlated incidents.
python -m tools.ops_cli incidents list --window 7

# One-shot summary (incident count, avg alerts/incident, breakdown).
python -m tools.ops_cli incidents stats --window 7 --json
```

### Per-user preferences

```bash
# Show a user's saved preferences (or the defaults if none saved).
python -m tools.ops_cli settings show --user-id <id>

# Set one or more keys in a single call. Invalid values coerce to the
# default rather than raising (defensive — UI is responsible for
# offering valid choices, but the CLI is forgiving).
python -m tools.ops_cli settings set --user-id <id> \
    --timezone America/New_York \
    --theme dark \
    --report-window 60 \
    --alert-severity HIGH
```

### Alert silences (planned downtime)

Schema v22 adds an `alert_silences` table so an operator can shut up a
rule (or a ticker, or a severity, or any cross-product of those) for a
bounded planned-maintenance window — instead of disabling the rule and
forgetting to re-enable it. The silence auto-expires at `expires_at`;
the silence gate inside `fire_rule` sits AFTER cooldown + flap so
silenced rules still record their crossings for flap-detection
consistency.

Silences are per-user. Alice's silence does NOT mute Bob's alerts, and
Bob cannot delete Alice's silence. Match keys (`rule_id` / `ticker` /
`severity`) are NULLable — NULL means "matches any value for this
column"; the broadest silence (all three NULL) shuts up every alert
for the user. Expired silences are kept around for an audit retention
window (default 30 days, swept once per day by the worker's
`run_silence_cleanup_job`) so "what was muted yesterday?" stays
answerable.

```bash
# List a user's active silences.
python -m tools.ops_cli silences list --user-id <id>

# Include expired silences in the listing (audit-retention tail).
python -m tools.ops_cli silences list --user-id <id> --include-expired

# Silence one rule for 4 hours during a maintenance window.
python -m tools.ops_cli silences create --user-id <id> \
    --duration-minutes 240 \
    --rule-id rule_bdi \
    --reason "FRED maintenance"

# Silence every CRITICAL alert for any rule + any ticker for 30 min.
python -m tools.ops_cli silences create --user-id <id> \
    --duration-minutes 30 \
    --severity CRITICAL

# Cancel a silence early.
python -m tools.ops_cli silences delete <silence_id> --user-id <id>
```

The API mirrors the CLI 1:1:

```
GET    /api/v1/silences                         list_silences(user_id=...)
GET    /api/v1/silences?include_expired=true    list_silences(..., include_expired=True)
POST   /api/v1/silences   body: {duration_minutes (req),
                                  rule_id?, ticker?, severity?, reason?}
DELETE /api/v1/silences/<silence_id>
```

Cross-user DELETE attempts return 404 (the silence does not exist in
the caller's scope) — the same no-leak contract used by `/schedules`.

The Streamlit UI surfaces the same controls in a collapsed
"🔕 Silence alerts for planned downtime" expander under the
Configuration section of the Alert Center tab. Active silences appear
in a table at the top with an inline Cancel button; the form below
mints a new silence via a rule dropdown + ticker / severity / duration
inputs + an optional reason text field.

Each silenced fire bumps the kv_state counter
`alerts_suppressed_by_silence` — readable via
`engine.alert_silences.get_suppressed_by_silence_count()` — so the
operator overview can surface "N alerts silenced in the last run"
without a dedicated table.

### Alert annotations (per-alert operator commentary threads)

Schema v23 adds an `alert_annotations` table so an operator can leave
a running thread of context on an alert as the response evolves:
"escalated to ops team", "monitoring overnight", "RCA in JIRA-1234".
Pre-v23 the only writable field on an alert was `acknowledged_note`
— a single string set once at ack — which forced the team to either
lose context or rewrite the same note repeatedly.

Annotations are per-user. Alice cannot see annotations on Bob's
alerts; bob cannot see, edit, or delete annotations on alice's.
Edit and delete are author-only — only the operator who WROTE the
annotation can mutate it (the alert owner may differ from the
annotation author in a multi-user-share workflow). Cross-author
attempts return False / 404 with no-leak guarantees (a probing
caller cannot enumerate other authors' annotation ids by error-code
differential).

Bodies are stored VERBATIM (no HTML stripping, no markdown
rendering) — the UI is the render-safe boundary (`st.text`, NOT
`st.markdown`). The engine layer silently truncates bodies longer
than 4000 characters so a pasted JIRA dump cannot blow up the row
size. The body is NEVER written to the loguru log so an operator
can paste sensitive context without leaking it to the audit feed.

```bash
# List the annotation thread for one alert (created_at ASC).
python -m tools.ops_cli annotations list <alert_id> --user-id <id>

# Add an annotation (silently truncated at 4000 chars).
python -m tools.ops_cli annotations add <alert_id> --user-id <id> \
    --body "escalated to ops team — RCA in JIRA-1234"

# Delete an annotation. Author-only — must match the writer's user_id.
python -m tools.ops_cli annotations delete <annotation_id> --user-id <id>
```

The API exposes both `/alerts/<alert_id>/annotations` for the
per-alert thread and `/annotations/<annotation_id>` for the
per-row mutations:

```
GET    /api/v1/alerts/<alert_id>/annotations    list_annotations(user_id=...)
POST   /api/v1/alerts/<alert_id>/annotations    body: {body (required)}
PATCH  /api/v1/annotations/<annotation_id>      body: {body (required)}
DELETE /api/v1/annotations/<annotation_id>
```

Cross-author PATCH / DELETE attempts return 404 (the annotation does
not exist in the caller's scope) — the same no-leak contract used
by `/silences` and `/schedules`. POST with a missing or whitespace-
only `body` returns 400. The caller's bearer-token user_id is
stamped as BOTH the alert owner AND the author — there is no admin
path via the API to annotate on someone else's behalf.

The Streamlit UI surfaces the same controls in a collapsed
"💬 Alert annotations — leave context as work evolves" expander
under the Configuration section of the Alert Center tab. Each of
the most-recent 50 persisted alerts appears as its own expander,
labelled with a "💬 N" badge (counts loaded in ONE batch query via
`count_annotations_per_alert`). Opening an expander reveals the
thread in chronological order plus a `text_area` + "Add comment"
button at the bottom; Edit / Delete buttons appear next to each
annotation ONLY when the current user is the author.

### Alert escalation chains (per-rule fallback ladders)

Schema v24 adds an `alert_escalation_chains` table so an
unacknowledged alert can climb a ladder of fallback delivery
channels instead of going stale on the first channel that missed
the page. Each chain is per-rule and per-user; every step carries
its own `after_minutes` timer (measured from the previous step's
fire, or from `alerts.created_at` for step 1) and a target
`channel_id`. The worker's `run_escalation_pass` walks every
unacked alert each tick (default 5 min) and dispatches the next
due step, then stamps `alerts.last_escalated_at` +
`alerts.escalation_step` so the next pass picks up step N+1.

Chains are per-user. Alice's chain on rule X does NOT escalate
Bob's alerts on the same rule (the `get_alerts_due_for_escalation`
SELECT joins `alerts.user_id` to `alert_escalation_chains.user_id`
so cross-user isolation is mechanical). The chain step's
`channel_id` must reference a channel in the chain owner's
delivery-channel set; cross-tenant references are rejected at
write time on the CLI + API and silently fail at dispatch on the
engine. Acknowledging an alert mid-chain stops further escalation —
the unacked filter excludes ack'd rows from the due query.

```bash
# List a rule's chain ordered by step number.
python -m tools.ops_cli escalations list <rule_id> --user-id <id>

# Add (or replace) one step in a chain. Re-using --step REPLACES.
python -m tools.ops_cli escalations add <rule_id> --user-id <id> \
    --step 1 \
    --after-minutes 15 \
    --channel-id <channel_id>

# Delete one step by chain_id (per-user scoped).
python -m tools.ops_cli escalations delete <chain_id> --user-id <id>

# Bulk-clear every step in a rule's chain.
python -m tools.ops_cli escalations clear <rule_id> --user-id <id>
```

The CLI validates `--channel-id` exists in the user's channel set
BEFORE the engine write — a typo or cross-tenant reference yields
exit 1 with a clear error instead of a chain step that fails
silently at dispatch time.

The API mirrors the CLI 1:1:

```
GET    /api/v1/rules/<rule_id>/escalations    get_escalation_chain(user_id=...)
POST   /api/v1/rules/<rule_id>/escalations    body: {step_number (req),
                                                     after_minutes (req),
                                                     channel_id (req)}
DELETE /api/v1/rules/<rule_id>/escalations    delete_chain(rule_id, user_id=...)
DELETE /api/v1/escalations/<chain_id>         delete_escalation_step(...)
```

POST validates `channel_id` against the caller's channel set —
unknown ids return 400 with a descriptive error. Cross-user DELETE
attempts on `/escalations/<chain_id>` return 404 (no-leak contract
identical to `/silences` and `/annotations`).

The escalation engine (`run_escalation_pass`, `escalate_alert`) is
worker-internal — there is NO API surface for triggering an
escalation on demand; that would let a token-holder spam any
channel the chain references. The worker tick is the only
dispatcher.

The Streamlit UI surfaces the same controls in a collapsed
"🪜 Alert escalation chains — climb a ladder of channels" expander
under the Configuration section of the Alert Center tab. Pick a
rule from the dropdown; the current chain renders as a table with
inline Delete buttons. The Add-step form auto-increments the
suggested step number to next-after-last and filters the channel
selectbox to the user's own channels. A two-click "Clear entire
chain" button bulk-deletes the chain (the first click arms a
confirm; the second click executes).

### Weekly digest (per-user automated summary)

A per-user opt-in weekly summary is dispatched every Monday at 14:00 UTC
by default (configurable) through whichever existing delivery channels
the user picks. The summary covers the prior 7 days of alerts grouped by
severity, the top alert types / routes / tickers, incident counts,
source-feed health, per-channel budget usage, and ack rate.

The worker's `run_weekly_digest_job_wrapper` runs every tick; the
underlying engine helper self-gates on each user's
`day_of_week` + `hour_utc` config AND a per-user `kv_state` idempotency
lock so a back-to-back hourly fire never double-sends. The digest
synthesises a `WEEKLY_DIGEST` `ShippingAlert` and dispatches via
`alert_delivery.deliver_alert` — the existing per-channel severity,
quiet-hours, per-user notification-prefs, and monthly-budget gates all
apply. The synthetic alert is NEVER persisted via `save_alerts`, so
escalation / cooldown / dedup do NOT trigger on the digest itself.

**Recommended channels:** `email` for the full HTML layout (8-KPI tile
header + tables for severity / types / routes / tickers / outages /
channel budgets), `slack` / `discord` for a markdown summary in a team
channel, and `webhook` for a generic receiver (e.g. a
notifications-aggregator). `sms` and `pagerduty` channels are dropped
automatically — the digest payload is too long for SMS and the wrong
shape for PagerDuty's incident model.

```bash
# Show current config (defaults to disabled).
python -m tools.ops_cli digest config --user-id <id>

# Opt in. --channels is a comma-separated list of channel_ids.
python -m tools.ops_cli digest enable --user-id <id> \
    --channels "ch1,ch2" --day-of-week monday --hour 14

# Disable (wipes the config row + idempotency lock).
python -m tools.ops_cli digest disable --user-id <id>

# Preview this week's digest as markdown (no dispatch).
python -m tools.ops_cli digest preview --user-id <id>

# Override the week: any date inside the target week works; the
# helper snaps to that week's Monday automatically.
python -m tools.ops_cli digest preview --user-id <id> --week-start 2026-05-20

# Force a one-shot dispatch right now (bypasses the schedule lock).
python -m tools.ops_cli digest send-now --user-id <id>
```

The Alert Center tab also surfaces a "Weekly Digest" panel next to the
delivery-channels card with the same enable / preview / send-now
controls.

### Recurring report schedules

Schema v20 added a `report_schedules` table so operators can configure
auto-generated reports on a cron-like schedule instead of clicking
"generate" by hand. The worker's `run_report_scheduler_job` fires every
due schedule each tick. The cron parser is stdlib-only (no `croniter`)
and supports `*`, `*/N`, single ints, and comma-lists; ranges (`1-5`)
and `L`/`#` extensions are intentionally NOT supported — expand into a
comma-list.

```bash
# List a user's schedules.
python -m tools.ops_cli schedules list --user-id <id>

# Create a daily-9am-UTC schedule (cron string in quotes — the shell
# would otherwise expand the *).
python -m tools.ops_cli schedules create --user-id <id> \
    --name "Morning Macro" --cron "0 9 * * *"

# Toggle a schedule on / off without deleting it.
python -m tools.ops_cli schedules disable <schedule_id> --user-id <id>
python -m tools.ops_cli schedules enable  <schedule_id> --user-id <id>

# Trigger an immediate manual run of one schedule (bypasses enabled).
python -m tools.ops_cli schedules run-once <schedule_id> --user-id <id>

# Delete one schedule.
python -m tools.ops_cli schedules delete <schedule_id> --user-id <id>
```

Schedule timestamps are stored as ISO-8601 UTC. The UI displays them
in the user's local timezone via `utils.tz`.

### Source-health auto-alerting

`engine.source_health` collects per-feed liveness pings on every worker
tick. When a probe comes back `down`, `degraded`, or simply hasn't
returned at all in N minutes, the operator should know without scanning
the data-health panel. The `health-alerts` subcommand exposes the
auto-alerting controls; the worker's
`run_source_health_alert_job` invokes the same logic on its periodic
pass (right after `run_health_ping_job` / `run_health_prune_job`, before
the bulk-export prune), so a degraded feed surfaces as a CRITICAL or
HIGH ShippingAlert within one scheduler tick.

```bash
# Show current config + fire counts in the last hour.
python -m tools.ops_cli health-alerts status
#   enabled                  : True
#   red_threshold_minutes    : 60
#   yellow_threshold_minutes : 30
#   cooldown_minutes         : 120
#   recent_fires_last_hour   : 0

# Disable during planned maintenance (operator doesn't want the noise).
python -m tools.ops_cli health-alerts disable

# Re-enable.
python -m tools.ops_cli health-alerts enable

# Fire the orchestrator NOW (useful for testing thresholds without
# waiting for the next worker tick). Prints the count dict
# {"fired": N, "skipped_cooldown": N, "errored": N}.
python -m tools.ops_cli health-alerts run-once --json
```

Severity rules:

* `last_status='down'` → **CRITICAL** (status alone is enough — a
  freshly-failed feed is still broken).
* `last_started_at` older than `red_threshold_minutes` → **CRITICAL**
  (even an `up` source counts as red when its last ping is too stale
  to be evidence of current health).
* `last_status='degraded'` → **HIGH**.
* `last_started_at` older than `yellow_threshold_minutes` (but inside
  red) → **HIGH**.
* Otherwise → no alert.

Recovery (a source going red → green) intentionally does NOT fire an
alert — the operator already sees the green badge in the UI, and an
"all clear" notification would just fight the cooldown.

Cooldown is per-source-per-user, stored as an ISO timestamp in
`kv_state`. The cooldown prevents a flapping feed from filling the
alert table: at most one alert per source per `cooldown_minutes`
window. Different sources are independent; different users are
independent (alice's cooldown on `fred` doesn't suppress bob's alert
on `fred`).

Config + cooldown both ride the existing `kv_state` table — no schema
bump. The Streamlit data-health tab exposes the same knobs (red /
yellow / cooldown thresholds, master enable / disable, "fired N alerts
in the last hour" status line) so a non-shell operator can manage it
from the UI.

### Alert rules — config-as-code (YAML + CSV round-trip)

Operators commonly want to version their alert-rule sets in git and
ship them to colleagues without copy-pasting from the UI. The `rules`
subcommand supports two wire formats:

* **YAML** — engineer-friendly, structured, ideal for a git diff.
  Hand-rolled parser ships in `tools/rules_yaml.py`; PyYAML is
  optional. The round-trip works on every deployment regardless of
  PyYAML presence.
* **CSV** — operator-friendly, opens in Excel for a grid edit. Built
  on the stdlib `csv` module (no extra dep), UTF-8 with a BOM so
  Excel detects the encoding correctly on macOS / Windows. Lives in
  `tools/rules_csv.py`.

Both formats round-trip the same field set: `rule_id`, `name`,
`metric`, `threshold_pct`, `severity` (CRITICAL/HIGH/MEDIUM/LOW
only), `condition`, `enabled`, `email_notify`, `target_channels`,
`cooldown_minutes`, plus the v19 `flap_*` fields.

**When to use which:**

* Reach for **YAML** when you want to diff in git, write rules by
  hand in an editor, or pipe through configuration-management. The
  structured shape makes line-by-line diffs semantic.
* Reach for **CSV** when you want to open in Excel for bulk edits
  (sort, filter, copy-paste between rules), email a snapshot to a
  non-engineer, or feed into a spreadsheet-driven workflow. One row
  per rule.

```bash
# YAML — export to stdout / file, diff / dry-run / apply import.
python -m tools.ops_cli rules export --user-id <id>
python -m tools.ops_cli rules export --user-id <id> --out config/rules.yaml
python -m tools.ops_cli rules diff   --user-id <id> --in config/rules.yaml
python -m tools.ops_cli rules import --user-id <id> --in config/rules.yaml --dry-run
python -m tools.ops_cli rules import --user-id <id> --in config/rules.yaml

# CSV — same trio of operations with the -csv suffix.
python -m tools.ops_cli rules export-csv --user-id <id>
python -m tools.ops_cli rules export-csv --user-id <id> --out config/rules.csv
python -m tools.ops_cli rules diff-csv   --user-id <id> --in config/rules.csv
python -m tools.ops_cli rules import-csv --user-id <id> --in config/rules.csv --dry-run
python -m tools.ops_cli rules import-csv --user-id <id> --in config/rules.csv
```

**CSV format notes:**

* Header row carries the canonical column order; rows are emitted
  sorted by `rule_id` for a deterministic diff.
* `target_channels` joins with `|` (pipe), not comma — the comma is
  the CSV delimiter and would tear the row. Split is symmetric on
  import; empty tokens are dropped (so `a||b` parses to `['a', 'b']`).
* Booleans render as lower-case `true` / `false`. The parser also
  accepts `True` / `False` / `1` / `0` / `yes` / `no`
  (case-insensitive) so a hand-edited Excel round-trip survives.
* Apply OVERWRITES the user's rule set, same as the YAML variant.
  Audit-logged via `auth.audit.record_audit`.

The UI surfaces both formats in `Alert Center → Rules Management →
📥 Export / Import rules (YAML / CSV)` (collapsed expander, two
sub-tabs). The Validate button gives the operator a preview +
warnings without saving; Import overwrites + reruns.

**Recommended git workflow (YAML is the default for git):**

1. Author / edit rules in the UI for the rapid-iteration phase.
2. Once the rule set stabilises, export to `config/rules.yaml` in your
   deployment repo: `python -m tools.ops_cli rules export --user-id <id>
   --out config/rules.yaml`. Commit it.
3. On a new deployment (or a colleague's machine), restore the set
   with: `python -m tools.ops_cli rules import --user-id <id> --in
   config/rules.yaml`.
4. Before shipping a rule-set change, run `python -m tools.ops_cli
   rules diff --user-id <id> --in config/rules.yaml` to confirm the
   live set matches the committed file — drift between the two is a
   process-failure signal.

Substitute `export-csv` / `import-csv` / `diff-csv` and
`config/rules.csv` if you prefer the CSV format for the repo. CSV
diffs in git are noisier than YAML diffs (every field-shift moves a
cell across columns rather than across lines), so YAML is the
recommended default for version control.

Warnings are non-fatal hints (unknown field ignored, missing field
defaulted to N, severity rejected) and are surfaced to the operator
through stdout (CLI) or `st.warning` (UI). They never carry sensitive
data — only field names and the canonical replacement value.

### Audit log export — JSONL for SIEM ingestion

Operators running a Splunk / Vector / Loki / Elastic sidecar want the
`audit_events` table piped in line-delimited JSON so the scraper can
ingest events without writing custom envelope-unwrapping code. The
`audit export` subcommand bridges that gap:

```bash
# Default: stream the entire audit log to stdout as JSONL.
python -m tools.ops_cli audit export

# Per-user pull (multi-tenant scrapers).
python -m tools.ops_cli audit export --user-id <id>

# Filter to one action verb.
python -m tools.ops_cli audit export --action login_success

# Bracket a time window (ISO-8601 UTC). --since is inclusive,
# --until is exclusive — the standard half-open convention.
python -m tools.ops_cli audit export \
    --since 2026-05-01T00:00:00+00:00 \
    --until 2026-05-23T00:00:00+00:00

# Write to a file (uses the streaming code path — memory-efficient
# for very large pulls).
python -m tools.ops_cli audit export --out /var/log/ship-tracker-audit.jsonl
# stdout: /var/log/ship-tracker-audit.jsonl
# stderr: exported 1842 rows in 12 ms to /var/log/ship-tracker-audit.jsonl
```

Output is one JSON object per line, `\n`-delimited, with a trailing
newline on the last line. Each line carries the full audit-event
shape:

```json
{"event_id":"...","created_at":"2026-05-23T12:00:00+00:00","user_id":"u-1","action":"login_success","entity_type":"","entity_id":"","detail_json":{}}
```

`detail_json` is the parsed dict (not a re-escaped JSON string) so a
SIEM with native JSON-in-JSON support can index its keys directly.
Per-recording-site redaction (e.g. Slack webhook URLs in
`save_channel`) is already applied; the export passes the stored
value through verbatim — it does NOT decrypt vault-encrypted fields.

**Recommended Vector / Splunk cron entry.** Run the export every
five minutes with a sliding window matching the previous run's
checkpoint:

```cron
# /etc/cron.d/ship-tracker-audit-export
# Every 5 minutes, append the last 5m of audit rows to the SIEM
# tail file. The since=now-6min lookback overlaps by 1m to absorb
# clock skew + the time the cron handler itself takes.
*/5 * * * *  shiptracker  cd /opt/ship-tracker && python -m tools.ops_cli audit export \
    --since "$(date -u -d '6 minutes ago' +%Y-%m-%dT%H:%M:%S+00:00)" \
    >> /var/log/ship-tracker-audit.jsonl 2>>/var/log/ship-tracker-audit.err
```

Then point your Vector / Splunk / Loki agent at
`/var/log/ship-tracker-audit.jsonl` as a JSONL source. Example Vector
source block:

```toml
[sources.ship_tracker_audit]
type = "file"
include = ["/var/log/ship-tracker-audit.jsonl"]
read_from = "end"
[transforms.parse_audit_jsonl]
type = "remap"
inputs = ["ship_tracker_audit"]
source = ". = parse_json!(.message)"
```

The same export is also reachable via `GET /api/v1/audit/export` on
the API server (port 8503 by default) — useful when the SIEM agent
runs on a separate host without filesystem access to the Ship Tracker
data directory. `?format=jsonl` is the default; `?format=json`
returns the same `{items: [...], count: N}` envelope as `/audit` for
legacy consumers.

### Audit log search panel — operator UI in the Data Health tab

The Data Health tab carries two audit panels: the legacy "Recent audit
events" list (top-N rows in a fixed time window) and the new "Audit
Log Search" panel that exposes the richer multi-filter query an
operator wants while investigating an incident.

The search panel sits next to the legacy panel and offers:

* `user_id` / `action` / `entity_type` / `entity_id` exact-match filters
* `action_prefix` — `login_` matches `login_success` AND `login_failure`
* `since` / `until` date pickers (half-open: `since` inclusive,
  `until` exclusive, both at 00:00:00 UTC)
* free-text grep over `detail_json` + `action` + `entity_id`
  (case-insensitive; `%` / `_` / `'` in the query string are
  LIKE-escaped and bound, not interpolated)
* configurable result `limit` (default 100, max 10 000) with a
  "showing N of M" caption so the operator knows when pagination is
  needed
* "Download as CSV" and "Download as JSONL" buttons on the result set

**Default scope is the current user.** An operator without admin
scope sees only their own audit events. The "All users (admin)"
checkbox widens scope but is opt-in; the active scope is echoed in
the result caption so the operator cannot accidentally believe they
are viewing a per-user slice. An explicit `user_id` in the text box
overrides both defaults.

The query is driven by a "Search" button — the panel does not refetch
on every keystroke. The last result is kept in `st.session_state` so
the CSV / JSONL download buttons remain populated across reruns until
the operator submits a new search.

#### Helper API — `engine.audit_search`

The UI panel is a thin wrapper around `engine.audit_search`, which is
also callable from tests, ad-hoc Python scripts, and any future API
endpoint that wants the richer query surface:

```python
from engine.audit_search import (
    AuditSearchQuery,
    search_audit,
    search_audit_count,
    get_distinct_actions,
    get_distinct_entity_types,
)

query = AuditSearchQuery(
    user_id="u-alice",                  # exact match; "" matches legacy "no user" rows
    action="login_success",             # exact verb
    action_prefix="login_",              # OR'd with `action` if both set
    entity_type="report",
    entity_id="report-abc",
    since="2026-05-22T00:00:00+00:00",
    until="2026-05-23T00:00:00+00:00",  # half-open
    text="rotated",                      # case-insensitive grep
    limit=100,                           # hard-capped at SQL level
)

result = search_audit(query)
result.total_matched       # count BEFORE limit was applied
result.events              # list of dicts (detail_json already parsed)
result.query               # echo of the input AuditSearchQuery

# Count-only — cheaper than the full search; useful for the "M matches"
# caption before the operator commits to a larger fetch.
n = search_audit_count(query)

# Dropdown population for the UI (sorted, deduped, capped).
get_distinct_actions(limit=100)
get_distinct_entity_types(limit=50)
```

Every helper **NEVER raises** — a DB outage or a malformed row returns
an empty result with the same shape so the calling UI keeps rendering
the rest of the Data Health tab.

The module does NOT inject `current_user_id()` automatically — the UI
passes it explicitly. That keeps `engine.audit_search` a pure search
helper; the per-user scoping policy belongs upstream where the caller
can decide whether they have admin scope or not.

### Alert search panel — operator UI in the Alert Center tab

The Alert Center tab carries the regular Active Alerts table (recent
firings, freshness-ordered) and the new "Alert search…" expander that
exposes the richer multi-filter query an operator wants when scroll
runs out — "where did the firing about Suez go?" / "find the one
mentioning DB connections".

The search panel sits between the Saved Filters panel and the main
alert table, wrapped in a collapsed expander so the regular table
stays the default view. The panel offers:

* `severity` exact match (CRITICAL / HIGH / MEDIUM / LOW)
* `severity_min` tier-or-worse match (HIGH includes CRITICAL; MEDIUM
  includes HIGH and CRITICAL; LOW matches everything)
* `alert_type` selectbox (DB-populated from distinct values)
* `acknowledged` tri-state (ack'd only / un-ack'd only / both)
* `ticker` / `port_locode` / `route_id` exact-match text inputs
* `since` / `until` date pickers (half-open: `since` inclusive,
  `until` exclusive, both at 00:00:00 UTC)
* free-text grep over `title || ' ' || body` (case-insensitive;
  `%` / `_` / `'` in the query string are LIKE-escaped and bound,
  not interpolated)
* configurable result `limit` (default 100, max 10 000) with a
  "showing N of M" caption so the operator knows when pagination is
  needed
* "Download as CSV" and "Download as JSONL" buttons on the result set

**Default scope is the current user.** An operator without admin scope
sees only their own alerts (PLUS legacy `user_id=''` rows — the same
dual-set semantics `load_alerts` uses so the search panel never hides
a row that the operator can see in the main table). The "All users
(admin)" checkbox widens scope but is opt-in; the active scope is
echoed in the result caption so the operator cannot accidentally
believe they are viewing a per-user slice.

The query is driven by a "Search" button — the panel does not refetch
on every keystroke. The last result is kept in `st.session_state` so
the CSV / JSONL download buttons remain populated across reruns until
the operator submits a new search.

The panel is **additional** surface — it does not replace the regular
Active Alerts table, the Saved Filters panel, the incident correlation
panel, or any other Alert Center surface.

#### Helper API — `engine.alert_search`

The UI panel is a thin wrapper around `engine.alert_search`, which is
also callable from tests, ad-hoc Python scripts, and any future API
endpoint that wants the richer query surface:

```python
from engine.alert_search import (
    AlertSearchQuery,
    search_alerts,
    search_alerts_count,
    get_distinct_alert_types,
    get_distinct_tickers,
    get_distinct_route_ids,
    get_distinct_port_locodes,
)

query = AlertSearchQuery(
    user_id="u-alice",                  # dual-set: own rows + legacy ''
    severity="HIGH",                    # exact match
    severity_min="HIGH",                # tier-or-worse: HIGH + CRITICAL
    alert_type="CONGESTION",
    ticker="ZIM",
    port_locode="USLAX",
    route_id="transpac",
    acknowledged=False,                  # tri-state: True/False/None
    since="2026-05-22T00:00:00+00:00",
    until="2026-05-23T00:00:00+00:00",  # half-open
    text="suez canal",                   # case-insensitive grep on title || body
    limit=100,                           # hard-capped at SQL level
)

result = search_alerts(query)
result.total_matched       # count BEFORE limit was applied
result.alerts              # list of dicts (canonical column projection)
result.query               # echo of the input AlertSearchQuery

# Count-only — cheaper than the full search; useful for the "M matches"
# caption before the operator commits to a larger fetch.
n = search_alerts_count(query)

# Dropdown population for the UI (sorted, deduped, capped, per-user
# scoped). Each helper accepts ``user_id=None`` for admin scope.
get_distinct_alert_types(limit=100)
get_distinct_tickers(limit=200)
get_distinct_route_ids(limit=200)
get_distinct_port_locodes(limit=200)
```

Every helper **NEVER raises** — a DB outage or a malformed row returns
an empty result with the same shape so the calling UI keeps rendering
the rest of the Alert Center tab.

The module is read-only — `engine.alert_engine_v2` retains exclusive
ownership of the `alerts` table write surface (`save_alerts`,
`acknowledge_alert`). No schema bump.

### Calendar feed (ICS subscription for incidents)

Operators subscribe to a per-user iCalendar feed that surfaces recent
shipping incidents inside Google Calendar / Outlook / Apple Calendar
/ Thunderbird — no dashboard-checking required.

**Token model:** Calendar apps fetch via plain GET with no
`Authorization` header, so we cannot store the secret hashed. The
token is stored PLAIN in `UserSettings.extras['calendar_token']` —
the secret IS the URL. Treat it like a webhook URL: anyone with the
link can subscribe. Rotate via `token-generate` or `token-revoke`.

**CLI:**

```bash
python -m tools.ops_cli calendar token-show     --user-id <id>
python -m tools.ops_cli calendar token-generate --user-id <id>
python -m tools.ops_cli calendar token-revoke   --user-id <id>
python -m tools.ops_cli calendar export         --user-id <id> --window 30 --out feed.ics
```

**API:**

```bash
curl "$BASE/api/v1/incidents.ics?token=<token>&window=30"
# → Content-Type: text/calendar; charset=utf-8
# → Content-Disposition: inline; filename=ship-tracker-incidents.ics
```

**Subscribing in common calendar apps:**

* **Google Calendar** — Settings → Add calendar → From URL → paste
  the `https://<host>/api/v1/incidents.ics?token=…` URL. Polls
  hourly.
* **Outlook (web)** — Add calendar → Subscribe from web → paste URL.
* **Apple Calendar (macOS)** — File → New Calendar Subscription →
  paste URL. Refresh interval configurable.
* **Thunderbird** — Calendar → New Calendar → On the Network →
  iCalendar (.ics) → paste URL.

The feed renders the most-recent `window` days of incidents (default
30) as `VEVENT` blocks with severity prefix on the SUMMARY, alert
count + summary in the DESCRIPTION, and CATEGORIES set to the
severity for client-side colouring. RFC 5545 compliant (CRLF line
endings, ≤75-octet line folding, text escaping). Stdlib only — no
`icalendar` package dependency.

### Delivery retry queue (schema v26)

When a webhook / Slack / email dispatch fails with a retriable error
(HTTP 5xx, connection timeout, SMTP temporary failure), the alert was
previously logged + lost. The retry queue persists it; worker walks
every 5 minutes with exponential backoff until success OR
`MAX_RETRIES = 5` attempts exhausted (60s → 120s → 240s → 480s →
960s wait between attempts).

**Retriable classification** (`engine.alert_delivery._is_retriable`):

| Result | Retried? | Why |
|---|---|---|
| HTTP 500 / 502 / 503 / 504 | yes | server-side, will likely self-heal |
| HTTP 408 (timeout) / 429 (rate limit) | yes | transient |
| HTTP 400 / 401 / 403 / 404 / 422 | **no** | client misconfig — won't fix itself |
| Connection / timeout / "temporary" in error | yes | transient |
| Budget exceeded | **no** | the budget is intentional |

**Operator CLI** (`tools.ops_cli retries …`):

```bash
python -m tools.ops_cli retries list [--status pending|failed|succeeded] [--user-id ID] [--limit N]
python -m tools.ops_cli retries cancel <queue_id> --user-id <id>
python -m tools.ops_cli retries manual <queue_id> --user-id <id>
python -m tools.ops_cli retries cleanup [--retention-days 14]
python -m tools.ops_cli retries process     # run retry pass NOW
```

**API** (`/api/v1/delivery-retries`):

```bash
curl "$BASE/api/v1/delivery-retries?status=pending&limit=100" \
     -H "Authorization: Bearer $TOKEN"
curl -X POST "$BASE/api/v1/delivery-retries/<queue_id>/retry" \
     -H "Authorization: Bearer $TOKEN"
curl -X DELETE "$BASE/api/v1/delivery-retries/<queue_id>" \
     -H "Authorization: Bearer $TOKEN"
```

**UI**: Data Health → Delivery Retry Queue panel (Movement 1.698).
Per-row Retry / Cancel buttons on pending entries; Recently failed +
Recently succeeded expanders for visibility; Maintenance expander
with the cleanup action.

### Tab completion (bash + zsh)

The ops CLI carries ~30 top-level subcommands. To avoid memorising the
list — and to avoid hand-maintaining a brittle completion script —
completion is **auto-generated** from the argparse tree and committed
under `docs/completion/`.

Regenerate after adding or renaming a subcommand:

```bash
python -m tools.completion_cli all --out-dir docs/completion
# writes ops_cli.bash, _ops_cli, backup_cli.bash, _backup_cli, … per known CLI
# plus docs/completion/INSTALL.md with the same instructions below.
```

Or render one CLI to stdout:

```bash
python -m tools.completion_cli bash --program ops_cli         # bash to stdout
python -m tools.completion_cli zsh  --program ops_cli --out _ops_cli
```

**bash install** — source the file per shell, or persist host-wide:

```bash
# This shell only:
source docs/completion/ops_cli.bash
source docs/completion/backup_cli.bash

# Or persist for all users on the host:
sudo cp docs/completion/ops_cli.bash      /etc/bash_completion.d/
sudo cp docs/completion/backup_cli.bash   /etc/bash_completion.d/
```

**zsh install** — add the completion dir to `$fpath` and autoload:

```bash
fpath=("$PWD/docs/completion" $fpath)
autoload -U _ops_cli _backup_cli _replay_cli
compinit
```

**Scope of completion**

* Only subcommand *names* are completed — option *values* (e.g.
  `--user-id <id>`) are not, since they need live DB lookups. Operators
  type the value themselves.
* The bash `complete -F` hook binds to argv[0]. If you invoke via
  `python -m tools.ops_cli`, the hook won't fire — wrap the invocation
  in a shell function or add an `ops_cli` wrapper to `$PATH`.

## Backup / Restore

Two complementary tools cover backups:

* **`tools.backup_cli`** — operator CLI for snapshot-and-restore of the
  SQLite DB and the generated-reports tree. The recommended path when
  you need an in-app, recoverable backup before a risky change.
* **`utils.bulk_export`** — wider-scope dataset export that ALSO
  includes per-source parquet caches. Use this for hand-off / migration
  scenarios, not for routine backup-and-restore (it does not have a
  matching `restore` command).

### `tools.backup_cli` — snapshot + restore the state DB

Four subcommands. Exit codes follow the operator-CLI contract (0 ok,
1 handler failure with stderr message, 2 argparse rejection).

```bash
# Create — default output is ./backups/ship_tracker_<timestamp>.tar.gz
python -m tools.backup_cli create
python -m tools.backup_cli create --out /tmp/before_migration.tar.gz

# List backups in a directory (default: ./backups)
python -m tools.backup_cli list
python -m tools.backup_cli list --dir /var/backups/ship

# Verify a backup — opens the snapshot, re-derives schema + row counts,
# compares to the manifest. Exits 1 + FAIL lines if any check failed.
python -m tools.backup_cli verify --from ./backups/ship_tracker_<ts>.tar.gz

# Restore — REQUIRES --confirm because it overwrites cache/ship_tracker.db
python -m tools.backup_cli restore --from /tmp/before_migration.tar.gz --confirm
```

Archive layout:

```
ship_tracker_<timestamp>.tar.gz
  manifest.json              # schema_version, created_at, table row counts,
                             # hostname, tool_version
  ship_tracker.db            # SQLite snapshot via Connection.backup() —
                             # safe against concurrent WAL writes
  cache/reports/*.html       # saved investor-briefing reports (if any)
```

What is intentionally NOT in a backup: logs (`logs/`), secrets
(`.env`, vault-key material), and the per-source parquet caches under
`cache/<source>/*.parquet`. The parquets are derived state and the
secrets are out-of-band by design — they should never end up in a
backup archive on disk.

Safety properties enforced by the CLI:

* **DB snapshot via the online-backup API.** A plain `shutil.copy`
  races against WAL writes; `Connection.backup` is the only safe way
  to copy a live SQLite DB at a transactionally-consistent point.
* **Restore requires `--confirm`.** Restore is destructive — without
  the flag the CLI exits 1 and tells the operator what to add.
* **No restore-forward.** If the backup's `manifest.schema_version`
  is greater than the running code's `state.db.SCHEMA_VERSION`, the
  CLI refuses. Upgrade the running code first.
* **Atomic swap.** The restored DB is moved into place via
  `os.replace`, so a concurrent reader sees either the old DB or the
  new DB, never a half-written file. Stale `-wal` / `-shm` sidecars
  are unlinked after the swap.

#### Recommended cron entry

Take a backup every night at 03:30 server time and keep the newest 30:

```cron
30 3 * * * cd /path/to/ship && /usr/bin/python3 -m tools.backup_cli create >> logs/backup.log 2>&1 && ls -1t backups/ship_tracker_*.tar.gz | tail -n +31 | xargs -r rm --
```

(Adjust the retention number to taste; on a small DB the archives are
a few MB each, so a 30-day rolling window is cheap. Skip the second
half of the line — the `ls … xargs rm` — if you'd rather keep every
archive forever.)

### `tools.db_check_cli` — DB integrity check

Operator CLI for verifying the SQLite state DB has not gotten corrupted
(after a crash, before a backup, during incident response) and that the
logical relationships the schema does NOT enforce (FK-less rule
references, orphaned audit rows, …) still hold.

```bash
# Quick check — runs PRAGMA quick_check + every logical check
python -m tools.db_check_cli

# Full check — runs the heavier PRAGMA integrity_check (page-by-page
# scan). Slow on multi-GB DBs; run at off-peak hours, or take a
# backup first and run the check against the backup.
python -m tools.db_check_cli --full

# Auto-fix safe issues — see "Auto-fix" below
python -m tools.db_check_cli --fix

# Check a specific file (e.g. a backup snapshot) instead of the live DB
python -m tools.db_check_cli --db /path/to/snapshot.db

# JSON output for scripts / dashboards (jq-friendly)
python -m tools.db_check_cli --json | jq '.failed'
```

Checks performed:

* **schema_version** — `kv_state.schema_version` vs the running code's
  `state.db.SCHEMA_VERSION`. PASS on match, WARN on mismatch (the DB
  is still usable; open through `state.db.get_connection()` to
  auto-migrate). The `PRAGMA user_version` slot is also read for
  completeness (the project does NOT use it as the source of truth).
* **integrity_check** — `PRAGMA quick_check` by default,
  `PRAGMA integrity_check` with `--full`. Returns `ok` on a healthy DB
  or one row per corruption issue.
* **foreign_key_check** — `PRAGMA foreign_key_check`. Reports any rows
  that violate FK constraints (the v21 mfa_recovery_codes FK to users,
  the v22 alert_silences FK to users).
* **orphan_alerts** — counts `alerts` rows whose `rule_id` is set but
  not present in `alert_rules` (FK-less relationship).
* **orphan_audit_events** — counts `audit_events` rows whose `user_id`
  is set but not present in `users`.
* **stale_api_tokens** — counts `api_tokens` past their `expires_at`
  that are still active (informational; the current schema does not
  carry `expires_at` so this degrades to INFO until a future migration
  adds the column).
* **stale_invitations** — counts `user_invitations` past `expires_at`
  that are not consumed (informational).
* **stale_silences** — counts `alert_silences` whose `expires_at` is
  more than 30 days in the past (informational; `--fix` deletes them).
* **duplicate_rule_ids** — checks `alert_rules` for the same
  `(user_id, rule_id)` group appearing more than once. FAIL on any
  duplicate — that is a data-correctness issue.
* **index_health** — confirms every known CREATE INDEX from
  `state/db.py` exists in `sqlite_master`. Missing indexes are WARN
  (the DB still works, queries are just slower).
* **known_tables** (`--full` only) — confirms every known table
  exists. A missing table is WARN (a hand-restored partial backup may
  not have every v20/v21/v22 table).

Output format:

* Text mode (default) — hierarchical PASS / WARN / FAIL / INFO per
  check, colourised via ANSI when stdout is a real tty (and never
  when piped / redirected).
* JSON mode (`--json`) — single JSON document on stdout with
  `{checks, passed, warned, failed, info, db_path, schema_version,
  ran_at}` plus an optional `fixes` array when `--fix` was passed.

Exit codes:

* `0` — every check returned PASS / INFO / WARN.
* `1` — at least one FAIL, or the DB file could not be opened.
* `2` — argparse rejected the invocation.

Read-only by default. Without `--fix`, the DB is opened with the
SQLite `mode=ro` URI flag so a misconfigured operator cannot
accidentally mutate the DB they are diagnosing.

#### Auto-fix (`--fix`)

The `--fix` mode opens the DB read-write and runs the supported
auto-fixes:

* **Mark expired api_tokens inactive** — `UPDATE api_tokens SET
  revoked=1 WHERE revoked=0 AND expires_at < now`. Skipped when the
  `expires_at` column is not present (the current api_tokens schema
  does not carry one yet).
* **Mark expired invitations consumed** — `UPDATE user_invitations
  SET consumed_at=expires_at, consumed_by_user_id='SYSTEM_EXPIRED'
  WHERE consumed_at IS NULL AND expires_at < now`. The `SYSTEM_EXPIRED`
  sentinel makes auto-consumed invites distinguishable from real
  signups in the audit log.
* **Delete ancient alert_silences** — calls
  `engine.alert_silences.cleanup_expired_silences()` (which deletes
  rows expired more than 30 days ago); falls back to a direct DELETE
  if the helper is unavailable. When `--db` targets a non-live file,
  the helper is bypassed and a direct DELETE runs against the
  supplied DB so the wrong DB never gets mutated.

`--fix` does NOT attempt to repair `integrity_check` or
`foreign_key_check` failures. Those mean the file is corrupted on
disk and need a DBA + a recent backup — the auto-fixer's "safe"
charter explicitly excludes them.

#### Recommended cron entry

Run a quick check every morning and a full check on Sundays at
04:00. Both append to a log file; a non-zero exit is captured by
cron's email-on-failure behaviour:

```cron
# Daily quick check at 04:00
0 4 * * * cd /path/to/ship && /usr/bin/python3 -m tools.db_check_cli --json >> logs/db_check.log 2>&1
# Weekly full check + cleanup on Sundays at 04:30
30 4 * * 0 cd /path/to/ship && /usr/bin/python3 -m tools.db_check_cli --full --fix --json >> logs/db_check.log 2>&1
```

Pipe the JSON into your monitoring system (jq + a webhook) if you
want alerts on a non-zero `failed` count.

### `tools.anonymize_cli` — DB anonymization for safe sharing

Produces an anonymized COPY of the SQLite state DB so an operator can
share it with a teammate, populate a staging environment from a prod
snapshot, or hand a QA contractor a realistic dataset — without
leaking PII, secrets, or operational fingerprints.

The source DB is opened READ-ONLY (`mode=ro`) and copied to the output
path before any mutation runs, so the source file is guaranteed to be
untouched even if a downstream pass errors out.

Three profiles:

* **`standard`** (default) — Scrubs PII + secrets but KEEPS alert /
  report / schedule shape. Replaces user emails with stable
  `user_<hash>@example.com` aliases (deterministic SHA-256), wipes
  password hashes + MFA secrets, deletes every `api_tokens` row
  (forces re-creation), stubs delivery channel targets per kind
  (slack → fake webhook, pagerduty → fake key, etc.), drops `kv_state`
  rows matching `vault:*` / `*secret*` / `*token*` / `*key*` /
  `*password*` / `*credential*` (case-insensitive), redacts
  annotation bodies to `REDACTED ANNOTATION (N chars)` so thread
  shape is preserved, zeroes `audit_events.detail_json` for
  sensitive actions only (`login_*`, `mfa_*`, `token_*`, `channel_*`,
  `signup_*`, `password_*`, `invitation_*`), stubs every
  `report_history.file_path` to `cache/reports/REDACTED.html`, wipes
  `user_settings.settings_json`, drops unconsumed invitations.

* **`aggressive`** — Same as standard PLUS empties annotation bodies
  to `""`, zeroes EVERY `audit_events.detail_json` (not just
  sensitive actions), and redacts `alerts.body` to `REDACTED`. Use
  when the recipient should see structure only, no operational
  content whatsoever.

* **`redact-only`** — Preserves row counts everywhere (no row drops)
  but replaces every scrubbed string field with a `REDACTED` marker.
  Useful when you want to hand someone a shape-faithful skeleton
  with zero real data — every `api_tokens` row stays, every
  `mfa_recovery_codes` row stays, every `kv_state` row stays, but
  the values are wiped.

```bash
# Share a dev DB with a teammate (default standard profile)
python -m tools.anonymize_cli \
    --source cache/ship_tracker.db \
    --output ~/share/ship_dev.db --verbose

# Populate staging from a prod snapshot
python -m tools.anonymize_cli \
    --source /var/lib/ship/prod.db \
    --output /var/lib/ship/staging.db --profile standard

# Hand a QA contractor a structurally identical shell
python -m tools.anonymize_cli \
    --source cache/ship_tracker.db \
    --output /tmp/qa.db --profile redact-only

# Preview what WOULD change without writing — source opened read-only
python -m tools.anonymize_cli \
    --source cache/ship_tracker.db \
    --dry-run --verbose
```

`--verbose` prints per-table scrubbed / dropped counts to stderr so
the operator can confirm at a glance that the expected tables were
touched.

Exit codes: `0` on success, `1` on handler error (e.g. source file
missing), `2` on argparse rejection (unknown flag, missing
`--source`).

Determinism: the email-replacement function uses a stable SHA-256
hash of the original value, so re-anonymizing the same source DB
twice produces the same user-mapping byte-for-byte. This makes
diffs between two anonymized snapshots meaningful.

Use cases:

* **Bug repro from a teammate** — anonymize, ship the .db, they
  rebuild the issue locally without ever seeing real customer data.
* **Staging refresh** — overnight cron pulls prod, anonymizes, drops
  the output into the staging volume.
* **QA contractor onboarding** — `redact-only` profile gives the
  contractor row-count-faithful data so query plans + UI layouts
  behave like prod, but every string is `REDACTED`.

What this tool does NOT do:

* It does NOT touch the `cache/reports/*.html` files on disk. If
  you're shipping the DB to a teammate and report HTML files exist
  outside the DB, exclude `cache/reports/` from whatever transport
  you use (or run `tools.backup_cli create` with `--exclude-reports`
  if shipping a tar.gz).
* It does NOT re-encrypt or re-sign any data. Recipients will need
  to sign up + create their own credentials in the anonymized DB.

### `tools.changelog_gen` — Changelog regeneration

`CHANGELOG.md` at the repo root is auto-generated from `git log` and
should NEVER be hand-edited. The header banner (`DO NOT EDIT MANUALLY`)
exists for this reason — any local edits will be silently lost on the
next regeneration. Regenerate after any sizeable batch of commits, or
let the cron job below handle it.

```bash
# Default — writes CHANGELOG.md covering the last 90 days, grouped by date
python -m tools.changelog_cli

# Absolute date cutoff
python -m tools.changelog_cli --since 2026-01-01

# Relative shorthand — d / w / m / y
python -m tools.changelog_cli --since 30d
python -m tools.changelog_cli --since 12w

# Upper-bound cutoff (everything before this date)
python -m tools.changelog_cli --until 2026-05-01

# Cap on commit count
python -m tools.changelog_cli --limit 200

# Group by category instead of date (Features → Fixes → UI → …)
python -m tools.changelog_cli --group-by category

# Flat layout — one bullet per commit, newest-first, no sub-sections
python -m tools.changelog_cli --group-by flat

# Alternative output destination
python -m tools.changelog_cli --out docs/CHANGELOG.md

# Print to stdout (no file written) — handy for piping / previewing
python -m tools.changelog_cli --print | head -40
```

Subject-prefix → category mapping:

| Prefix              | Category bucket |
| ------------------- | --------------- |
| `feat:` / `feature:`| `feature`       |
| `fix:` / `bug:`     | `fix`           |
| `ui:` / `ui(scope):`| `ui`            |
| `engine:`           | `engine`        |
| `api:` / `ingress:` | `api`           |
| `ops:` / `auth:` / `worker:` / `scheduler:` | `ops` |
| `tools:`            | `tools`         |
| `docs:`             | `docs`          |
| `test:` / `tests:`  | `test`          |
| (anything else)     | `other`         |

For combined prefixes (`engine+ui: combined change`) the FIRST token
wins, so the example above lands in `engine`. Merge commits are
skipped (`git log --no-merges`), and `Co-Authored-By:` trailers are
stripped from the per-commit summary line.

Exit codes:

* `0` — the changelog was rendered (file written, or `--print` flushed).
* `1` — failed to write the output file (printed to stderr).
* `2` — argparse rejected the invocation.

#### Recommended cron entry

Regenerate every night at 04:15 server time so the next morning's
review has yesterday's commits included. Skip the regeneration on a
shallow clone — if the repo only has a partial history, the rendered
window will be incomplete.

```cron
# Nightly CHANGELOG regeneration (last 90 days, grouped by date)
15 4 * * * cd /path/to/ship && /usr/bin/python3 -m tools.changelog_cli --since 90d >> logs/changelog.log 2>&1
```

The regeneration is fast — well under a second on a repo with a few
thousand commits — so running it from a post-receive hook on the
deployment server is also fine. If you'd rather regenerate only on
pushes to the default branch, use the snippet below in
`.git/hooks/post-receive`:

```bash
#!/usr/bin/env bash
while read _ _ ref; do
  if [ "$ref" = "refs/heads/main" ]; then
    cd /path/to/ship && /usr/bin/python3 -m tools.changelog_cli --since 90d
    git -C /path/to/ship add CHANGELOG.md
    git -C /path/to/ship commit --no-verify -m "docs: regenerate CHANGELOG.md" || true
  fi
done
```

### `utils.bulk_export` — wider-scope dataset export

Use this when you want to hand someone a working copy of every
parquet cache alongside the DB and reports — for example, migrating
from laptop A to laptop B without losing alerts / reports / cached
feeds, or sharing a known-good dataset with a colleague.

```bash
# Default — writes cache/exports/ship-tracker-YYYYMMDD-HHMMSS.tar.gz
python -m utils.bulk_export

# Skip slices you don't need (smaller archives)
python -m utils.bulk_export --no-reports
python -m utils.bulk_export --no-cache

# Custom output path
python -m utils.bulk_export --output /tmp/myexport.tar.gz

# Build + keep only the newest 5 archives
python -m utils.bulk_export --prune
```

The archive layout is:

```
ship-tracker-YYYYMMDD-HHMMSS.tar.gz
  MANIFEST.json              # generated_at, schema_version, counts
  ship_tracker.db            # SQLite snapshot (top level)
  cache/
    <source>/*.parquet       # per-source caches (fred, yfinance, ...)
    reports/*.html           # saved investor-briefing reports
```

`logs/` and `cache/exports/` are intentionally excluded — logs grow
unbounded, and including prior exports would cause recursive bloat.

To restore manually from a `utils.bulk_export` archive:

```bash
tar -xzf ship-tracker-20260522-143012.tar.gz -C /path/to/ship
# The DB lands at the archive root; move it into cache/:
mv /path/to/ship/ship_tracker.db /path/to/ship/cache/
```

The `MANIFEST.json` records the schema version at export time —
refuse a restore where the archive's `schema_version` is **greater**
than `state.db.SCHEMA_VERSION` in your checkout (the running code
does not know the newer schema yet). The `tools.backup_cli restore`
command enforces this automatically; manual `tar -xzf` does not.

### Automatic retention

`worker.scheduler.run_bulk_export_prune_job` runs once per daily cron
pass (alongside the LLM-call / render-event / health-ping prunes) and
keeps the newest 5 `bulk_export` archives, deleting the rest. The
backups produced by `tools.backup_cli create` are NOT pruned by this
job — they live under `./backups/` and the operator decides retention
via the cron one-liner above.

## Logs

`loguru` writes structured logs to stdout by default. In Docker:

```bash
docker logs -f <container_id>
```

Streamlit's own server logs are interleaved on the same stream.

## Test flakiness tracker (`python -m tools.test_flakiness_cli`)

The suite is at 5260+ tests and growing — intermittent flakes (a test
that fails 1-in-20 runs but passes on retry) start to drown out real
regressions. `tools.test_flakiness` ingests pytest's JUnit XML output
into the `kv_state` table and surfaces:

  * **Flaky tests** — nodeids whose failure rate across recent runs
    exceeds a configurable threshold (default 10%).
  * **Consistently-slow tests** — nodeids whose mean duration across
    runs exceeds 1.0s (configurable via `SLOW_TEST_THRESHOLD_SECONDS`).
  * **Trending flakes** — newly flaking in the current window but
    clean in the prior window.
  * **Regressions** — passed before, failed in the newest run.
  * **Per-test streaks** — current pass / fail streak for any nodeid.

Storage: a single rolling list at kv_state key `'test_runs'`, capped
at 200 runs (oldest dropped on each persist). No schema bump required.

### Subcommands

```bash
# Parse a JUnit XML and persist as a new run.
python -m tools.test_flakiness_cli ingest /tmp/junit.xml

# List flaky tests (failure_rate >= threshold, total_runs >= min_runs).
python -m tools.test_flakiness_cli flaky --min-runs 5 --threshold 0.1

# List consistently-slow tests (mean_duration desc, top N).
python -m tools.test_flakiness_cli slow --top-n 20

# Big-picture summary across the most-recent N runs.
python -m tools.test_flakiness_cli summary --runs 10

# Pass/fail streak for one nodeid.
python -m tools.test_flakiness_cli history tests/test_foo.py::test_bar

# Wipe all stored runs (destructive — requires --confirm).
python -m tools.test_flakiness_cli clear --confirm

# Convenience: run pytest with --junitxml + ingest + report in one call.
# Pytest's own exit code is preserved so CI still fails on real failures.
python -m tools.test_flakiness_cli run --pytest-args "tests/ -q"
```

Every read-side subcommand accepts `--json` for machine-readable
output. The CLI never bubbles an exception to the shell — handler
failures print to stderr and return exit 1.

### Recommended CI workflow

The expected pattern is one ingest per CI run, followed by a flake
check that operators can inspect in the CI log:

```bash
# Run the suite, capturing JUnit XML.
pytest --junitxml=/tmp/junit.xml

# Persist the run's summary into the rolling kv_state list.
python -m tools.test_flakiness_cli ingest /tmp/junit.xml

# Surface flakes; tee to a log for the operator to inspect.
python -m tools.test_flakiness_cli flaky | tee logs/flaky.log
```

The flake list is a leading indicator: tests appearing here are
candidates for `@pytest.mark.flaky` quarantine, a `@pytest.fixture`
refactor (e.g. pin a clock), or genuine bug investigation. The tracker
deliberately does NOT auto-rerun flakes or quarantine them — that's
an operator decision.

### What this tool does NOT do

* It does not modify `pytest.ini` or auto-emit JUnit XML. The
  operator/CI invokes pytest with `--junitxml` explicitly.
* It does not delete the source XML file after ingesting. The operator
  may want to keep it for offline inspection.
* It does not surface test source code — only nodeids + messages
  (which is enough for grep + click-through in any editor).
* It does not track per-pass nodeids. Only failures + slow tests are
  persisted to keep the kv_state blob bounded; "total runs since first
  observed failure" is the denominator for the failure-rate math.
