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

## 3. Fly.io / Render / other PaaS

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

The app never crashes on a missing key — every feed degrades to either
a synthetic-data fallback or a clearly-labeled "not configured"
status. The "Data Sources" panel in the sidebar shows the freshness
state of every configured source.

## Logs

`loguru` writes structured logs to stdout by default. In Docker:

```bash
docker logs -f <container_id>
```

Streamlit's own server logs are interleaved on the same stream.
