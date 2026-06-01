# Ship Tracker — production container image.
#
# Single-stage build because the dependencies are mostly pure-Python +
# scientific wheels (numpy, pandas, scipy, scikit-learn) that already
# distribute precompiled binaries. A multi-stage build would shave a
# few hundred MB at the cost of significantly more complex caching.
# For Streamlit Cloud / Fly.io / Render, this single stage is fine.
#
# Build:
#   docker build -t ship-tracker:latest .
#
# Run (binds Streamlit's default port 8501):
#   docker run --rm -p 8501:8501 \
#     -e FRED_API_KEY=$FRED_API_KEY \
#     -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#     -v "$PWD/cache:/app/cache" \
#     ship-tracker:latest
#
# Mounting ./cache as a volume preserves the per-day narration cache,
# alerts, and any persisted user rules across container restarts.

FROM python:3.11-slim AS base

# System dependencies for scientific stack + curl for healthchecks.
# build-essential is needed only when wheels fall back to source builds
# (e.g., on architectures without prebuilt scipy/sklearn wheels). On
# linux/amd64 with current pip you'll usually get pure-wheel installs
# and these never compile — left in defensively.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Streamlit is happiest with a non-root user — and so are the
# orchestrators (Fly.io, ECS Fargate) that enforce non-root by default.
RUN useradd --create-home --uid 10001 --shell /bin/bash app

WORKDIR /app

# Install Python dependencies first (separately from app source) so
# `docker build` caches the heavy install layer across most edits.
# A change to requirements.txt invalidates this cache; a change to a
# python file does not.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copy app source. .dockerignore (sibling file) keeps cache/logs/
# .venv/__pycache__ out of the image.
COPY --chown=app:app . /app

# Streamlit runtime defaults:
#  - bind to 0.0.0.0 so it's reachable from outside the container
#  - port 8501 is the convention; orchestrators usually map to that
#  - disable usage stats (Streamlit's opt-in telemetry)
#  - server.headless prevents Streamlit from trying to open a browser
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Ensure the cache directory exists with the right ownership so the
# per-day narration cache + alerts file + rules file can be written
# without permission gymnastics.
RUN mkdir -p /app/cache /app/logs \
    && chown -R app:app /app

USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8501/_stcore/health || exit 1

# `app.py` is the Streamlit entry point. Using `streamlit run` (not
# `python -m streamlit`) gives us cleaner argv handling for the runtime
# flags Streamlit Cloud-style platforms expect.
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--browser.gatherUsageStats=false"]
