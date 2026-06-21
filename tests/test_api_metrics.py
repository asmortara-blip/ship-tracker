"""Tests for the Prometheus ``/metrics`` endpoint (R129).

The endpoint exposes the platform's REAL telemetry SLIs (render latency,
feed up/down, job success/failure, delivery-retry depth) in the
Prometheus text exposition format (v0.0.4) so an ops/risk desk can wire
Datadog/Grafana straight at the worker without scraping Streamlit.

We reuse the ``tests/test_api_server.py`` harness shape: a real
``HTTPServer`` bound to ``127.0.0.1:0`` in a daemon thread, per-test
SQLite isolation, ``requests`` fired from the test process. The metrics
endpoint is PUBLIC (no auth) — these tests assert that, plus the
exposition-format validity, the expected ``ship_*`` families with the
right values from seeded telemetry, and the crash-proof contract (a
failing getter does NOT 500 the scrape).
"""
from __future__ import annotations

import socket
import threading
from http.server import HTTPServer

import pytest
import requests

from worker import api_server


# ─── Fixtures (mirror tests/test_api_server.py) ───────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation — every ``state.db.get_connection`` call
    lands in this tmp file so the seeded telemetry is deterministic."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _isolated_rate_limit():
    from auth import rate_limit as rl
    rl.clear_buckets()
    yield
    rl.clear_buckets()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    port = _find_free_port()
    httpd = HTTPServer(("127.0.0.1", port), api_server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)


# ─── Prometheus exposition-format parser (test-side validation) ───────────


_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _parse_exposition(text: str) -> dict:
    """Parse a Prometheus text-exposition body and assert structural
    validity inline.

    Returns a dict with:
      * ``samples``  — {metric_name: [(labels_str, value_float), ...]}
      * ``help``     — set of metric names that carried a ``# HELP`` line
      * ``types``    — {metric_name: type_str}

    Raises ``AssertionError`` on any malformed line so the test body can
    just call this and trust the structure.
    """
    samples: dict = {}
    help_names: set = set()
    types: dict = {}
    for raw_line in text.split("\n"):
        line = raw_line.rstrip("\r")
        if line == "":
            continue
        if line.startswith("#"):
            # Comment OR HELP/TYPE metadata.
            parts = line.split(None, 3)
            if len(parts) >= 3 and parts[1] == "HELP":
                help_names.add(parts[2])
            elif len(parts) >= 4 and parts[1] == "TYPE":
                types[parts[2]] = parts[3]
            # A bare "# ..." comment line is allowed (we use them for the
            # "(…unavailable)" markers) — nothing to assert.
            continue
        # Sample line: "name value" OR "name{labels} value".
        # Split off the value as the final whitespace-delimited token.
        assert " " in line, f"sample line has no value: {line!r}"
        name_and_labels, _, value_token = line.rpartition(" ")
        assert value_token, f"empty value token: {line!r}"
        # Value must parse as a float (Prometheus values are float64).
        value = float(value_token)
        if "{" in name_and_labels:
            assert name_and_labels.endswith("}"), (
                f"malformed label set: {line!r}"
            )
            name = name_and_labels[: name_and_labels.index("{")]
            labels_str = name_and_labels[
                name_and_labels.index("{") + 1 : -1
            ]
        else:
            name = name_and_labels
            labels_str = ""
        assert name, f"empty metric name: {line!r}"
        # Metric name must be a valid Prometheus identifier.
        assert all(c.isalnum() or c == "_" for c in name), (
            f"bad metric name chars: {name!r}"
        )
        samples.setdefault(name, []).append((labels_str, value))
    return {"samples": samples, "help": help_names, "types": types}


def _label_value(labels_str: str, key: str) -> str:
    """Extract one label value from a rendered ``k="v",k2="v2"`` string."""
    for piece in labels_str.split('","'):
        piece = piece.strip('{}')
        if "=" in piece:
            k, _, v = piece.partition("=")
            if k == key:
                return v.strip('"')
    return ""


# ─── Seeding helpers ──────────────────────────────────────────────────────


def _seed_render(surface: str, durations_ms: list[int], errors: int = 0) -> None:
    from engine.perf_telemetry import record_render
    for d in durations_ms:
        record_render(tab_name=surface, duration_ms=d, success=True)
    for _ in range(errors):
        record_render(
            tab_name=surface, duration_ms=5, success=False, error_msg="boom",
        )


def _seed_health(source: str, status: str, duration_ms: int = 100) -> None:
    import uuid
    from datetime import datetime, timezone
    from engine.source_health import HealthPing, _record_ping
    _record_ping(HealthPing(
        ping_id=str(uuid.uuid4()),
        source=source,
        started_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=duration_ms,
        status=status,
        error_msg="" if status == "up" else "degraded",
    ))


def _seed_job(job_name: str, status: str) -> None:
    from datetime import datetime, timezone
    from state.worker_runs import record_run
    now = datetime.now(timezone.utc).isoformat()
    record_run(
        job_name,
        started_at=now,
        finished_at=now,
        status=status,
        result=({"fired": 1} if status == "ok" else None),
        error_message=(None if status == "ok" else "kaboom"),
    )


# ─── Tests: public + content-type + validity ──────────────────────────────


def test_metrics_returns_200_without_auth(server):
    """``GET /metrics`` is unauthenticated by design (Prometheus
    convention + matches /health) — no bearer header required."""
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200


def test_metrics_emits_prometheus_content_type(server):
    """The Content-Type carries the v0.0.4 exposition-format media type."""
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("Content-Type") == _PROM_CONTENT_TYPE


def test_metrics_alias_under_api_v1(server):
    """The ``/api/v1/metrics`` alias also serves the same endpoint."""
    r = requests.get(f"{server}/api/v1/metrics", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("Content-Type") == _PROM_CONTENT_TYPE


def test_metrics_body_parses_as_valid_exposition(server):
    """Every non-comment line is ``name value`` or ``name{labels} value``;
    every sampled family carries a HELP + TYPE line; the body ends with a
    trailing newline."""
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    assert r.text.endswith("\n"), "exposition body must end with a newline"
    parsed = _parse_exposition(r.text)
    # Every metric family that produced a sample must have HELP + TYPE.
    for name in parsed["samples"]:
        assert name in parsed["help"], f"{name} missing # HELP"
        assert name in parsed["types"], f"{name} missing # TYPE"
        assert parsed["types"][name] in {"gauge", "counter"}, (
            f"{name} has unexpected TYPE {parsed['types'][name]!r}"
        )
    # The liveness sentinel is always present at 1.
    assert ("", 1.0) in parsed["samples"].get("ship_up", [])


def test_metrics_does_not_require_authorization_header(server):
    """A bogus bearer header is ignored — the endpoint is public, so it
    must not 401 even with a garbage token (it short-circuits ABOVE the
    auth gate)."""
    r = requests.get(
        f"{server}/metrics",
        headers={"Authorization": "Bearer not-a-real-token"},
        timeout=5,
    )
    assert r.status_code == 200


# ─── Tests: real values from seeded telemetry ─────────────────────────────


def test_render_families_reflect_seeded_perf(server):
    """With seeded render telemetry the render families appear with the
    expected per-surface values, and latency is reported in SECONDS
    (ms / 1000)."""
    # 200ms renders → median 200ms = 0.2s. One error row.
    _seed_render("overview", [200, 200, 200, 200], errors=1)
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    parsed = _parse_exposition(r.text)

    p50 = parsed["samples"].get("ship_render_latency_p50_seconds", [])
    overview_p50 = [
        v for lbls, v in p50 if _label_value(lbls, "surface") == "overview"
    ]
    assert overview_p50, "overview p50 sample missing"
    # Median of [200,200,200,200,5] (the 5 is the error row) → 200ms = 0.2s.
    assert overview_p50[0] == pytest.approx(0.2, abs=1e-6)

    count = parsed["samples"].get("ship_render_count", [])
    overview_count = [
        v for lbls, v in count if _label_value(lbls, "surface") == "overview"
    ]
    assert overview_count and overview_count[0] == 5.0  # 4 ok + 1 error

    errs = parsed["samples"].get("ship_render_error_count", [])
    overview_errs = [
        v for lbls, v in errs if _label_value(lbls, "surface") == "overview"
    ]
    assert overview_errs and overview_errs[0] == 1.0

    # Platform-wide success rate: 4 of 5 → 0.8.
    sr = parsed["samples"].get("ship_render_success_rate", [])
    assert sr and sr[0][1] == pytest.approx(0.8, abs=1e-6)


def test_source_up_family_maps_status_to_one_zero(server):
    """``ship_source_up`` is 1 for a feed whose latest ping was 'up' and
    0 for 'degraded' / 'down'."""
    _seed_health("fred", "up")
    _seed_health("yfinance", "degraded")
    _seed_health("worldbank", "down")
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    parsed = _parse_exposition(r.text)

    up = dict(
        (_label_value(lbls, "source"), v)
        for lbls, v in parsed["samples"].get("ship_source_up", [])
    )
    assert up.get("fred") == 1.0
    assert up.get("yfinance") == 0.0   # degraded → 0
    assert up.get("worldbank") == 0.0  # down → 0


def test_job_families_reflect_seeded_runs(server):
    """Seeded ok + error job runs surface in the success/failure totals
    and the last-ok gauge."""
    _seed_job("run_daily_briefing_job", "ok")
    _seed_job("run_daily_briefing_job", "ok")
    _seed_job("run_alert_prune_job", "error")
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    parsed = _parse_exposition(r.text)

    ok = dict(
        (_label_value(lbls, "job"), v)
        for lbls, v in parsed["samples"].get("ship_job_success_total", [])
    )
    fail = dict(
        (_label_value(lbls, "job"), v)
        for lbls, v in parsed["samples"].get("ship_job_failure_total", [])
    )
    last_ok = dict(
        (_label_value(lbls, "job"), v)
        for lbls, v in parsed["samples"].get("ship_job_last_ok", [])
    )
    assert ok.get("run_daily_briefing_job") == 2.0
    assert fail.get("run_daily_briefing_job") == 0.0
    assert last_ok.get("run_daily_briefing_job") == 1.0

    assert ok.get("run_alert_prune_job") == 0.0
    assert fail.get("run_alert_prune_job") == 1.0
    assert last_ok.get("run_alert_prune_job") == 0.0

    # A KNOWN job that never ran reports last_ok=0 (visible gap, not omitted).
    assert last_ok.get("run_audit_prune_job") == 0.0


def test_delivery_retry_pending_family_present(server):
    """The delivery-retry depth gauge is emitted (0 on an empty queue)."""
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    parsed = _parse_exposition(r.text)
    depth = parsed["samples"].get("ship_delivery_retry_pending", [])
    assert depth, "ship_delivery_retry_pending family missing"
    assert depth[0] == ("", 0.0)


# ─── Tests: crash-proof contract ──────────────────────────────────────────


def test_one_failing_getter_does_not_500_the_scrape(server, monkeypatch):
    """If a single telemetry getter raises, the scrape still returns 200
    and the OTHER families remain present — one broken store must not take
    down the whole observability surface."""
    import engine.perf_telemetry as perf_mod

    def _boom(*a, **k):
        raise RuntimeError("perf store exploded")

    # Patch the SOURCE module — the handler imports get_perf_summary lazily
    # inside its try block, so patching the origin name takes effect.
    monkeypatch.setattr(perf_mod, "get_perf_summary", _boom)

    # Seed a healthy source so the source-health block still has content.
    _seed_health("fred", "up")
    _seed_job("run_daily_briefing_job", "ok")

    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200, "a failing getter must NOT 500 the scrape"
    parsed = _parse_exposition(r.text)

    # The perf families are absent (the block bailed), but the other
    # families AND the liveness sentinel are still emitted.
    assert "ship_render_latency_p50_seconds" not in parsed["samples"]
    assert ("", 1.0) in parsed["samples"].get("ship_up", [])
    up = dict(
        (_label_value(lbls, "source"), v)
        for lbls, v in parsed["samples"].get("ship_source_up", [])
    )
    assert up.get("fred") == 1.0
    ok = dict(
        (_label_value(lbls, "job"), v)
        for lbls, v in parsed["samples"].get("ship_job_success_total", [])
    )
    assert ok.get("run_daily_briefing_job") == 1.0  # one ok run seeded above


def test_label_values_are_escaped(server):
    """A surface name carrying a quote / backslash / newline is escaped so
    it cannot break the exposition framing."""
    _seed_render('weird"\\name', [10])
    r = requests.get(f"{server}/metrics", timeout=5)
    assert r.status_code == 200
    # The raw body must contain the escaped form, not a bare quote that
    # would corrupt the line.
    assert 'surface="weird\\"\\\\name"' in r.text
    # And it must still parse cleanly.
    _parse_exposition(r.text)
