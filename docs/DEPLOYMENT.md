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
```

`GET /api/v1/health` is intentionally **unauthenticated** so load
balancers / k8s probes can poll it without shipping a token:

```bash
curl http://localhost:8503/api/v1/health
```

The response shape is identical to `webhook_listener`'s `/health`
(see [GET /health — liveness + system probe](#get-health--liveness--system-probe)
above) so a single probe template works for both ports.

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
| `users list / create`              | User-account admin.                                                |
| `tokens list / create / revoke`    | Per-user API-token admin.                                          |
| `export`                           | Build a bulk-state tar.gz (see `Backup / Restore` below).          |
| `mfa enable / disable / status`    | TOTP second-factor enrollment per user.                            |
| `filters list / delete`            | Per-user saved filter presets.                                     |
| `incidents list / stats`           | Correlated-incident view over the alert table.                     |
| `settings show / set`              | Per-user preferences (timezone, theme, defaults).                  |

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

## Backup / Restore

The `utils.bulk_export` module bundles durable state — the SQLite DB,
per-source parquet caches, and saved HTML reports — into a single
timestamped tar.gz archive. Use it before a schema migration, when
handing a dataset to a colleague, or as a regular backup.

### Create an archive

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

### Restore

```bash
# From a clean checkout
tar -xzf ship-tracker-20260522-143012.tar.gz -C /path/to/ship
# The DB lands at the archive root; move it into cache/:
mv /path/to/ship/ship_tracker.db /path/to/ship/cache/
```

The `MANIFEST.json` at the archive root records the SQLite schema
version at export time — refuse a restore where the archive's
`schema_version` is **greater** than `state.db.SCHEMA_VERSION` in your
checkout (the running code does not know the newer schema yet).

### Automatic retention

`worker.scheduler.run_bulk_export_prune_job` runs once per daily cron
pass (alongside the LLM-call / render-event / health-ping prunes) and
keeps the newest 5 archives, deleting the rest. Override the policy by
calling `prune_old_exports(keep_n=N)` directly or running the CLI with
`--prune`.

## Logs

`loguru` writes structured logs to stdout by default. In Docker:

```bash
docker logs -f <container_id>
```

Streamlit's own server logs are interleaved on the same stream.
