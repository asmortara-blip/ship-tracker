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
