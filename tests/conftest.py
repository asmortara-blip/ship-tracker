"""Shared pytest fixtures for the Ship Tracker suite.

The fixtures here are intentionally small and synthetic — they exercise
code paths without requiring live API access. For realistic-scale parquet
fixtures recorded from a known-good run, see ``tests/fixtures/``.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_fetch_provenance_writes(monkeypatch):
    """Disable the per-fetch provenance ledger (rec R003/R097) for every test so
    a cache fetch in a non-DB-isolated test never writes to the real DB. Tests
    that exercise the ledger re-enable it explicitly (with an isolated DB)."""
    try:
        import state.fetch_ledger as _fl
        monkeypatch.setattr(_fl, "RECORDING_ENABLED", False, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _gdelt_offline_by_default(monkeypatch, tmp_path):
    """No test makes a REAL GDELT call OR reads the on-disk GDELT cache. The R014
    chokepoint-risk job is wired _run_always into the scheduler main() loop, so
    any test that runs main() would otherwise fire 9 rate-limited (1 req/5s) live
    GDELT fetches — slow enough to trip the per-test timeout AND, on success (or
    from a cache a prior real run populated), escalate + mutate the process-global
    CHOKEPOINTS registry, leaking risk levels into later tests. Neutralize BOTH
    the live ``requests.get`` path AND the cache dir, so an un-injected fetch
    reports honestly ``unavailable`` (a no-op overlay). The feed's own tests
    inject ``http_get`` / set their own ``_CACHE_DIR``, and the job-wiring tests
    monkeypatch ``fetch_chokepoint_events``, so all of them override this."""
    try:
        import data.gdelt_feed as _gf

        class _NoNet:
            @staticmethod
            def get(*_a, **_k):
                raise ConnectionError("GDELT network disabled in tests")

        monkeypatch.setattr(_gf, "requests", _NoNet, raising=False)
        monkeypatch.setattr(_gf, "_CACHE_DIR", tmp_path / "gdelt_cache",
                            raising=False)
        # The courtesy inter-request delay only fires on the real (http_get=None)
        # path — i.e. exactly the scheduler main() path a test exercises. Zero it
        # so a main()-running test doesn't sleep 9×5s before each fetch fails.
        monkeypatch.setattr(_gf, "_INTER_REQUEST_DELAY", 0, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _live_feeds_offline_by_default(monkeypatch, tmp_path):
    """No test makes a REAL OFAC-sanctions or Open-Meteo-marine call OR reads the
    on-disk cache. The Compliance / Geopolitical tab renders call fetch_ofac_sdn()
    with http_get=None, so a tab-smoke on a cold cache would otherwise pull the
    real ~8MB OFAC SDN list over the network (slow + flaky). Neutralize the live
    ``requests`` path + cache dir for both new feeds; the feeds' own tests inject
    ``http_get`` / set their own ``_CACHE_DIR`` so they override this. (Same R014
    lesson as the GDELT fixture above.)"""
    class _NoNet:
        @staticmethod
        def get(*_a, **_k):
            raise ConnectionError("live feed network disabled in tests")

    for mod_name, cache_sub in (("data.sanctions_feed", "sanctions_cache"),
                                ("data.marine_weather_feed", "marine_cache"),
                                ("data.portwatch_feed", "portwatch_cache")):
        try:
            mod = __import__(mod_name, fromlist=["_CACHE_DIR"])
            monkeypatch.setattr(mod, "requests", _NoNet, raising=False)
            monkeypatch.setattr(mod, "_CACHE_DIR", tmp_path / cache_sub,
                                raising=False)
        except Exception:
            pass
    yield


@pytest.fixture
def block_network(monkeypatch):
    """Fail every outbound TCP connection instantly.

    Network-backed data feeds (stock / FRED / freight / World Bank, …) wrap
    their fetches in try/except and degrade to a fallback or empty result, so
    blocking the socket makes those paths run *fast and deterministically*
    instead of waiting on real HTTP — which is what flaked these tests under
    the suite's per-test timeout. Local file / SQLite I/O uses no sockets and
    is untouched.
    """
    import socket

    def _blocked(*_args, **_kwargs):
        raise ConnectionError("network disabled in this test")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked, raising=False)
    yield


@pytest.fixture
def unavailable_data_feeds(monkeypatch):
    """Make every external data-feed loader raise, so callers like
    ``load_data_bundle`` exercise their "source unavailable → fallback/empty"
    path deterministically and FAST. This patches the loader functions
    themselves (not the socket) because some feeds use HTTP clients that
    bypass Python's ``socket`` module (e.g. curl_cffi-backed stock fetchers),
    so a socket block alone can still hang on the real network.
    """
    def _unavailable(*_args, **_kwargs):
        raise ConnectionError("data feed unavailable in this test")

    for target in (
        "data.stock_feed.fetch_all_stocks",
        "data.fred_feed.fetch_macro_series",
        "data.freight_scraper.fetch_fbx_rates",
        "data.comtrade_feed.fetch_all_ports",
        "data.ais_feed.fetch_vessel_counts",
        "data.worldbank_feed.fetch_port_throughput",
    ):
        monkeypatch.setattr(target, _unavailable)
    yield


@pytest.fixture
def freight_data_fixture() -> dict[str, pd.DataFrame]:
    """Synthetic freight rate history for two routes × 90 days."""
    base = date.today() - timedelta(days=90)
    dates = [base + timedelta(days=i) for i in range(90)]

    def _route(rid: str, rate_start: float, slope: float) -> pd.DataFrame:
        return pd.DataFrame({
            "date":       pd.to_datetime(dates),
            "route_id":   rid,
            "rate_usd":   [rate_start + slope * i for i in range(90)],
            "source":     "fixture",
        })
    return {
        "transpacific_eb": _route("transpacific_eb", 2200.0, 3.5),
        "asia_europe":     _route("asia_europe",     1800.0, -2.1),
    }


@pytest.fixture
def route_results_fixture() -> list[dict]:
    """Minimal route result rows — matches the shape consumed by ``tab_routes``."""
    return [
        {
            "route_id":               "transpacific_eb",
            "origin_locode":          "CNSHA",
            "dest_locode":            "USLAX",
            "origin_region":          "APAC",
            "dest_region":            "NAM",
            "transit_days":           14,
            "current_rate_usd_feu":   2850.0,
            "rate_trend":             "Rising",
            "rate_pct_change_30d":    0.045,
            "opportunity_score":      0.72,
            "opportunity_label":      "Strong",
            "rate_momentum_component":        0.8,
            "demand_imbalance_component":     0.65,
            "congestion_clearance_component": 0.70,
            "macro_tailwind_component":       0.55,
            "rationale":              "Strong demand, easing congestion.",
        },
    ]


@pytest.fixture
def insights_fixture() -> list[dict]:
    return [
        {"title": "China exports accelerating", "score": 0.82,
         "action": "Prioritize", "category": "MACRO",
         "rationale": "PMI 52.1, exports +6.3% YoY"},
    ]


# ─── Streamlit mocking — for UI tab smoke tests ────────────────────────────

class _MockStreamlitContextManager:
    """Stand-in for st.expander()/st.container()/st.sidebar — any call that
    returns a single context manager."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def __call__(self, *args, **kwargs):
        return self
    def __getattr__(self, name):
        # Any chained st.something inside a container behaves the same
        return _silent_callable


def _silent_callable(*args, **kwargs):
    """The default no-op call surface — returns a fresh context manager
    so `with st.something()` works."""
    return _MockStreamlitContextManager()


def _mock_columns_or_tabs(spec=None, *args, **kwargs):
    """st.columns(n) and st.columns([3, 2]) → list of n context managers.
    st.tabs(["a", "b"]) → list of 2 context managers. The length is
    inferred from the first arg shape — int OR list-like."""
    if spec is None:
        n = 1
    elif isinstance(spec, int):
        n = spec
    elif hasattr(spec, "__len__"):
        n = len(spec)
    else:
        n = 1
    return [_MockStreamlitContextManager() for _ in range(max(1, n))]


class _MockSessionState(dict):
    """st.session_state behaves both as a dict AND attribute-accessible."""
    def __getattr__(self, name):
        return self.get(name)
    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def mock_streamlit(monkeypatch):
    """Inject a mock `streamlit` module into sys.modules so UI tab files
    can be imported and their render() called without a real Streamlit
    runtime. Each test gets a fresh session_state."""
    import sys
    import types

    mock = types.ModuleType("streamlit")

    # Most st.* functions are no-ops that return _MockStreamlitContextManager
    # (so context-manager usage AND tuple-unpacking AND chained attribute
    # access all work without raising).
    for name in (
        "title", "header", "subheader", "markdown", "write", "text", "caption",
        "code", "latex", "info", "warning", "error", "success", "exception",
        "divider", "image", "video", "audio", "metric", "dataframe", "table",
        "json", "line_chart", "bar_chart", "area_chart", "scatter_chart",
        "altair_chart", "plotly_chart", "pyplot", "vega_lite_chart",
        "button", "download_button", "checkbox", "radio", "selectbox",
        "multiselect", "slider", "select_slider", "text_input", "number_input",
        "date_input", "time_input", "text_area", "file_uploader", "color_picker",
        "toggle", "data_editor", "form_submit_button", "snow", "balloons",
        "stop", "rerun", "experimental_rerun", "set_page_config",
        "progress", "spinner", "toast", "status", "empty", "container",
        "expander", "popover", "form", "sidebar",
        "switch_page", "page_link", "fragment",
    ):
        setattr(mock, name, _silent_callable)

    # columns / tabs need to return a list of context managers so
    # tuple-unpacking (`c1, c2 = st.columns(2)`) works correctly.
    mock.columns = _mock_columns_or_tabs
    mock.tabs = _mock_columns_or_tabs

    # Value-returning widgets — must return a real Python primitive so
    # downstream comparisons (`if value > threshold:`) don't TypeError.
    # The defaults match what an unselected widget on first render returns.
    def _slider(label, min_value=0, max_value=100, value=None, *args, **kwargs):
        return value if value is not None else min_value
    def _number_input(label, min_value=0.0, max_value=None, value=None, *args, **kwargs):
        return value if value is not None else min_value
    def _text_input(label, value="", *args, **kwargs):
        return value
    def _text_area(label, value="", *args, **kwargs):
        return value
    def _selectbox(label, options=None, index=0, *args, **kwargs):
        if options is None or len(list(options)) == 0:
            return None
        opts = list(options)
        return opts[min(index, len(opts) - 1)]
    def _multiselect(label, options=None, default=None, *args, **kwargs):
        return list(default) if default else []
    def _radio(label, options=None, index=0, *args, **kwargs):
        if options is None:
            return None
        opts = list(options)
        return opts[min(index, len(opts) - 1)] if opts else None
    def _checkbox(label, value=False, *args, **kwargs):
        return value
    def _toggle(label, value=False, *args, **kwargs):
        return value
    def _date_input(label, value=None, *args, **kwargs):
        from datetime import date
        return value if value is not None else date.today()
    def _button(label, *args, **kwargs):
        return False
    def _download_button(label, *args, **kwargs):
        return False
    def _form_submit_button(label="Submit", *args, **kwargs):
        return False

    mock.slider = _slider
    mock.select_slider = _slider
    mock.number_input = _number_input
    mock.text_input = _text_input
    mock.text_area = _text_area
    mock.selectbox = _selectbox
    mock.multiselect = _multiselect
    mock.radio = _radio
    mock.checkbox = _checkbox
    mock.toggle = _toggle
    mock.date_input = _date_input
    mock.button = _button
    mock.download_button = _download_button
    mock.form_submit_button = _form_submit_button

    # cache_data and cache_resource are decorators that should be passthrough
    def _passthrough_decorator(*args, **kwargs):
        # Support both @st.cache_data and @st.cache_data(ttl=...)
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        def _wrap(fn):
            return fn
        return _wrap

    mock.cache_data = _passthrough_decorator
    mock.cache_resource = _passthrough_decorator

    # secrets is a dict-like
    mock.secrets = {}

    # session_state is per-test
    mock.session_state = _MockSessionState()

    # Inject as both `streamlit` and the common alias `st` (some modules
    # do `import streamlit as st` at function level — the sys.modules
    # injection covers both cases since they resolve through the same key).
    monkeypatch.setitem(sys.modules, "streamlit", mock)

    return mock
