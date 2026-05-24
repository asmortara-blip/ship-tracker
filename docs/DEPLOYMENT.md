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
| `GET    /api/v1/reports/<id>/html`           | n/a                               | Raw HTML of one saved report.                                                            |
| `POST   /api/v1/reports/<id>/public`         | `{"expires_in_days": 30}` (opt)   | Generate a public-share slug; returns `{"slug": "..."}`. 404 on unknown/cross-user.      |
| `DELETE /api/v1/reports/<id>/public`         | empty                             | Revoke a public slug; returns `{"revoked": true}`. 404 on unknown/cross-user.            |
| `GET    /api/v1/rules`                       | n/a                               | Caller's alert-rule list.                                                                |
| `POST   /api/v1/rules`                       | `[ {rule}, … ]`                   | Replace caller's rule set. 415 on non-JSON Content-Type, 400 on malformed JSON.          |
| `DELETE /api/v1/rules`                       | empty                             | Wipe caller's rule set (per-user — does NOT touch other users' rules).                   |
| `GET    /api/v1/channels`                    | n/a                               | Caller's delivery channels (`target` omitted to avoid leaking webhook URLs).             |
| `POST   /api/v1/channels`                    | `DeliveryChannel` JSON dict       | Insert/upsert a delivery channel. 400 if `channel_id` missing.                           |
| `DELETE /api/v1/channels/<channel_id>`       | empty                             | Delete one channel. Cross-user deletes silently no-op (returns 200; row untouched).      |
| `GET    /api/v1/telemetry/llm`               | n/a                               | LLM-call usage summary, scoped to caller.                                                |
| `GET    /api/v1/telemetry/perf`              | n/a                               | Render-performance summary (process-wide; still gated by auth).                          |
| `GET    /api/v1/audit`                       | n/a                               | Caller's audit-log rows. Query: `?limit=100` (max 1000), `?action=login_success` filter. |
| `GET    /api/v1/incidents`                   | n/a                               | Caller's correlated alert incidents. Query: `?window=7` (days).                          |
| `GET    /api/v1/source-health`               | n/a                               | Global feed-health summary (NOT user-scoped). Query: `?window_hours=24`.                 |
| `GET    /api/v1/schedules`                   | n/a                               | Caller's report schedules. List of `{schedule_id, name, cron_expr, enabled, …}` rows.    |
| `POST   /api/v1/schedules`                   | `{name, cron_expr, enabled?}`     | Create a recurring report schedule. 400 on missing name / invalid cron_expr.             |
| `PATCH  /api/v1/schedules/<id>`              | `{name?, cron_expr?, enabled?}`   | Update one schedule; only the supplied fields move. 404 on cross-user / unknown id.      |
| `DELETE /api/v1/schedules/<id>`              | empty                             | Delete one schedule. 404 on cross-user / unknown id.                                     |

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
| `channels list / delete`           | Delivery-channel admin.                                            |
| `reports list / delete / stats`    | Saved-report admin.                                                |
| `telemetry usage / recent / prune` | LLM call telemetry.                                                |
| `perf summary`                     | Render-performance summary.                                        |
| `health summary / ping`            | Data-source health.                                                |
| `health-alerts status / enable / disable / run-once` | Auto-fire ShippingAlerts when a data source goes red / yellow. |
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

### Alert rules — config-as-code (YAML round-trip)

Operators commonly want to version their alert-rule sets in git and
ship them to colleagues without copy-pasting from the UI. The `rules`
subcommand exports the persisted set to YAML, imports a YAML file back
(replacing the user's set), and shows a unified diff between a YAML
file and the live rule set.

The wire format is a small YAML subset — `schema_version`, then a list
of rules with `rule_id`, `name`, `metric`, `threshold_pct`, `severity`
(CRITICAL/HIGH/MEDIUM/LOW only), `condition`, `enabled`,
`email_notify`, `target_channels`, `cooldown_minutes`, plus the v19
`flap_*` fields. PyYAML is an optional dependency; a hand-rolled parser
ships in `tools/rules_yaml.py` so the round-trip works on every
deployment regardless of PyYAML presence.

```bash
# Export the current user's rules to stdout (YAML).
python -m tools.ops_cli rules export --user-id <id>

# Export to a file (useful for committing into git).
python -m tools.ops_cli rules export --user-id <id> --out config/rules.yaml

# Diff a YAML file against the live rule set — shows what an import
# would change without writing anything.
python -m tools.ops_cli rules diff --user-id <id> --in config/rules.yaml

# Dry-run the import — surfaces parsed rules + any warnings (unknown
# fields, missing fields defaulted, severities rejected) without
# touching the DB.
python -m tools.ops_cli rules import --user-id <id> --in config/rules.yaml --dry-run

# Apply the import — OVERWRITES the user's rule set with the contents
# of the YAML file. Audit-logged via auth.audit.record_audit so the
# replace shows up alongside UI saves.
python -m tools.ops_cli rules import --user-id <id> --in config/rules.yaml
```

The UI surfaces the same export/import in `Alert Center → Rules
Management → 📥 Export / Import rules (YAML)` (collapsed expander). The
Validate button gives the operator a preview + warnings without
saving; Import overwrites + reruns.

**Recommended git workflow:**

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
