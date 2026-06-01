"""Tests for engine.narration_engine — daily LLM briefing path.

Only covers the NEW DailyNarration / generate_daily_narration code added
alongside the existing rule-based NarrationEngine. The rule-based path
has its own coverage elsewhere.

All tests are hermetic — the Claude SDK call is monkeypatched, never
hits the real API. Cache writes go to per-test tmp dirs so the real
cache/narrations/ folder is never touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from engine import narration_engine as ne
from engine.narration_engine import (
    DEFAULT_LLM_MODEL,
    DailyNarration,
    NarrationContext,
    NarrationSection,
    build_narration_context,
    _build_daily_user_prompt,
    _narration_cache_path,
    _parse_claude_json,
    _read_narration_cache,
    _summarize_forecast,
    _summarize_idea,
    _summarize_stress,
    _template_daily_narration,
    _write_narration_cache,
    generate_daily_narration,
)


# ─── Fake input dataclasses — duck-typed against the real ones ──────────────

@dataclass
class _FakeStressReport:
    overall_ssi: float = 0.62
    ssi_label: str = "Elevated"
    ssi_color: str = "#c9962b"
    wow_change: float = 0.04
    top_disruptions: list[str] = field(default_factory=lambda: [
        "Suez Canal — moderate disruption (Houthi activity)",
        "Panama Canal — restricted slots (drought)",
    ])
    component_scores: dict[str, float] = field(default_factory=lambda: {
        "chokepoint": 0.71, "congestion": 0.55, "weather": 0.42,
        "rate": 0.48, "vulnerability": 0.33,
    })


@dataclass
class _FakeIdea:
    ticker: str
    direction: str = "Bullish"
    conviction_label: str = "High"
    conviction_score: float = 0.78
    thesis: str = "Trans-Pacific rate uplift from front-loading, ZIM exposed."
    supporting_signals: list[str] = field(default_factory=list)


@dataclass
class _FakeForecast:
    route_id: str
    route_name: str = ""
    current_stress: float = 0.5
    stress_30d: float = 0.55
    trend: str = "Worsening"
    rate_forecast_pct: float = 0.04


def _make_full_context(target_date: date = date(2026, 5, 20)) -> NarrationContext:
    """Reasonable populated context for end-to-end tests."""
    return NarrationContext(
        target_date=target_date,
        stress_report=_FakeStressReport(),
        top_ideas=[
            _FakeIdea(ticker="ZIM", direction="Bullish", conviction_score=0.82),
            _FakeIdea(ticker="MATX", direction="Bullish", conviction_score=0.66,
                      conviction_label="Moderate"),
            _FakeIdea(ticker="DAC", direction="Neutral", conviction_score=0.41,
                      conviction_label="Watch"),
        ],
        top_forecasts=[
            _FakeForecast(route_id="transpacific_eb", route_name="Trans-Pacific EB",
                          current_stress=0.55, stress_30d=0.62, trend="Worsening"),
            _FakeForecast(route_id="asia_europe", route_name="Asia-Europe",
                          current_stress=0.71, stress_30d=0.68, trend="Improving"),
        ],
        notable_indicators={"BDI": 1450.0, "WCI": 2680.0, "FBX": 2610.0},
    )


# ─── Per-test isolation: redirect the cache dir + clear env API key ─────────

@pytest.fixture(autouse=True)
def isolate_cache_and_key(monkeypatch, tmp_path):
    """Every test gets a fresh tmp cache_dir; no ANTHROPIC_API_KEY in env."""
    monkeypatch.setattr(ne, "NARRATION_CACHE_DIR", tmp_path / "narrations")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield tmp_path / "narrations"


# ─── _summarize_* helpers ───────────────────────────────────────────────────

def test_build_narration_context_never_raises_on_empty_inputs() -> None:
    """The shared UI/worker context factory always returns a valid
    NarrationContext — each sub-computation is independently guarded."""
    ctx = build_narration_context([], [], {}, {})
    assert isinstance(ctx, NarrationContext)
    assert isinstance(ctx.top_forecasts, list)
    assert isinstance(ctx.notable_indicators, dict)
    assert isinstance(ctx.top_port_deficits, list)


def test_build_narration_context_handles_all_none() -> None:
    assert isinstance(build_narration_context(), NarrationContext)


def test_summarize_stress_handles_none() -> None:
    assert _summarize_stress(None) == {}


def test_summarize_stress_extracts_expected_fields() -> None:
    report = _FakeStressReport()
    out = _summarize_stress(report)
    assert out["overall_ssi"] == pytest.approx(0.62)
    assert out["ssi_label"] == "Elevated"
    assert out["wow_change"] == pytest.approx(0.04)
    assert "Suez Canal" in out["top_disruptions"][0]
    assert out["component_scores"]["chokepoint"] == pytest.approx(0.71)


def test_summarize_idea_truncates_long_thesis() -> None:
    idea = _FakeIdea(ticker="ZIM", thesis="x" * 1000)
    out = _summarize_idea(idea)
    assert len(out["thesis"]) <= 280
    assert out["ticker"] == "ZIM"


def test_summarize_forecast_rounds_floats() -> None:
    fc = _FakeForecast(
        route_id="r1", current_stress=0.123456789, stress_30d=0.5555555,
        rate_forecast_pct=0.04321,
    )
    out = _summarize_forecast(fc)
    assert out["current_stress"] == pytest.approx(0.123, abs=1e-3)
    assert out["stress_30d"] == pytest.approx(0.556, abs=1e-3)
    assert out["rate_forecast_pct"] == pytest.approx(0.043, abs=1e-3)


# ─── _build_daily_user_prompt ───────────────────────────────────────────────

def test_user_prompt_contains_target_date_and_payload() -> None:
    ctx = _make_full_context(date(2026, 5, 20))
    prompt = _build_daily_user_prompt(ctx)
    assert "2026-05-20" in prompt
    # The structured payload should embed at least the SSI + a ticker.
    assert "Elevated" in prompt
    assert "ZIM" in prompt
    # JSON is valid.
    json_start = prompt.find("{")
    json_end = prompt.rfind("}") + 1
    parsed = json.loads(prompt[json_start:json_end])
    assert parsed["date"] == "2026-05-20"
    assert parsed["ssi"]["ssi_label"] == "Elevated"
    assert len(parsed["top_disruption_ideas"]) == 3


# ─── _template_daily_narration ──────────────────────────────────────────────

def test_template_handles_empty_context() -> None:
    ctx = NarrationContext(target_date=date(2026, 5, 20))
    n = _template_daily_narration(ctx)
    assert isinstance(n, DailyNarration)
    assert n.date == "2026-05-20"
    assert n.source == "template"
    assert n.headline                  # non-empty
    assert n.body                      # non-empty
    assert n.tokens_in == 0 and n.tokens_out == 0
    assert n.model == ""


def test_template_full_context_builds_all_sections() -> None:
    ctx = _make_full_context()
    n = _template_daily_narration(ctx)
    titles = [s.title for s in n.sections]
    assert "SSI Component Breakdown" in titles
    assert "Top Equity Ideas" in titles
    assert "Route Forecasts" in titles
    assert "Notable Indicators" in titles
    # Every section has at least one bullet.
    for s in n.sections:
        assert len(s.bullets) >= 1


def test_template_headline_includes_ssi_when_present() -> None:
    ctx = _make_full_context()
    n = _template_daily_narration(ctx)
    assert "0.62" in n.headline
    assert "Elevated" in n.headline


# ─── Cache round-trip ───────────────────────────────────────────────────────

def test_cache_round_trip(isolate_cache_and_key: Path) -> None:
    ctx = _make_full_context()
    narration = _template_daily_narration(ctx)
    path = _narration_cache_path(date(2026, 5, 20), isolate_cache_and_key)
    _write_narration_cache(path, narration)

    loaded = _read_narration_cache(path)
    assert loaded is not None
    assert loaded.date == narration.date
    assert loaded.headline == narration.headline
    assert loaded.body == narration.body
    assert len(loaded.sections) == len(narration.sections)


def test_cache_read_missing_returns_none(isolate_cache_and_key: Path) -> None:
    path = _narration_cache_path(date(2026, 1, 1), isolate_cache_and_key)
    assert not path.exists()
    assert _read_narration_cache(path) is None


def test_cache_read_corrupt_returns_none(isolate_cache_and_key: Path) -> None:
    path = isolate_cache_and_key / "2026-01-01.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this isn't json", encoding="utf-8")
    assert _read_narration_cache(path) is None


# ─── _parse_claude_json ─────────────────────────────────────────────────────

def test_parse_claude_valid_json() -> None:
    ctx = _make_full_context()
    raw = json.dumps({
        "headline": "Test headline",
        "body": "Test body para.\n\nSecond para.",
        "sections": [
            {"title": "S1", "bullets": ["b1", "b2"]},
            {"title": "S2", "bullets": ["b3"]},
        ],
    })
    n = _parse_claude_json(raw, ctx, tokens_in=42, tokens_out=99, model="m")
    assert n is not None
    assert n.headline == "Test headline"
    assert n.body.startswith("Test body")
    assert len(n.sections) == 2
    assert n.source == "claude"
    assert n.tokens_in == 42 and n.tokens_out == 99


def test_parse_claude_strips_markdown_fence() -> None:
    ctx = _make_full_context()
    raw = (
        "```json\n"
        + json.dumps({
            "headline": "X", "body": "Y", "sections": [],
        })
        + "\n```"
    )
    n = _parse_claude_json(raw, ctx, 0, 0, "m")
    assert n is not None
    assert n.headline == "X"


def test_parse_claude_rejects_missing_headline() -> None:
    ctx = _make_full_context()
    raw = json.dumps({"body": "missing headline", "sections": []})
    assert _parse_claude_json(raw, ctx, 0, 0, "m") is None


def test_parse_claude_rejects_empty_body() -> None:
    ctx = _make_full_context()
    raw = json.dumps({"headline": "X", "body": "", "sections": []})
    assert _parse_claude_json(raw, ctx, 0, 0, "m") is None


def test_parse_claude_rejects_non_dict_payload() -> None:
    ctx = _make_full_context()
    assert _parse_claude_json("[1, 2, 3]", ctx, 0, 0, "m") is None


def test_parse_claude_rejects_garbage() -> None:
    ctx = _make_full_context()
    assert _parse_claude_json("not json at all", ctx, 0, 0, "m") is None


# ─── generate_daily_narration — end-to-end paths ────────────────────────────

def test_no_api_key_falls_back_to_template(isolate_cache_and_key: Path) -> None:
    """No ANTHROPIC_API_KEY → template path; output NOT cached."""
    ctx = _make_full_context()
    n = generate_daily_narration(ctx)
    assert n.source == "template"
    # Template path doesn't cache, so no file appears.
    cache_file = _narration_cache_path(ctx.target_date, isolate_cache_and_key)
    assert not cache_file.exists()


def test_cache_hit_short_circuits_api_call(monkeypatch, isolate_cache_and_key: Path) -> None:
    """Pre-seed the cache; generate should return the cached value without
    touching the SDK at all."""
    ctx = _make_full_context()
    cached = DailyNarration(
        date=ctx.target_date.isoformat(),
        headline="From cache",
        body="From cache body.",
        sections=[NarrationSection(title="Cached", bullets=["x"])],
        source="claude",
        model="prev-model",
        tokens_in=100, tokens_out=200,
        generated_at="2026-05-20T12:00:00+00:00",
    )
    _write_narration_cache(
        _narration_cache_path(ctx.target_date, isolate_cache_and_key), cached,
    )

    # Sentinel: blow up if anyone tries to call Claude.
    def _explode(*args, **kwargs):
        raise AssertionError("Claude should NOT be called on a cache hit")
    monkeypatch.setattr(ne, "_call_claude", _explode)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    n = generate_daily_narration(ctx)
    assert n.headline == "From cache"
    assert n.source == "claude"      # preserved from cache
    assert n.tokens_in == 100        # preserved


def test_claude_path_success_writes_cache(monkeypatch, isolate_cache_and_key: Path) -> None:
    """With API key + successful (mocked) Claude call, output should:
       - have source='claude'
       - be written to the cache file
       - not retry on a second call (next call should be a cache hit)
    """
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    claude_call_count = {"n": 0}

    def _fake_call(system, user, *, model, api_key):
        claude_call_count["n"] += 1
        payload = {
            "headline": "Mocked Claude headline",
            "body": "Mocked body.\n\nSecond paragraph.",
            "sections": [
                {"title": "Mock Section", "bullets": ["one", "two", "three"]},
            ],
        }
        return json.dumps(payload), 42, 99

    monkeypatch.setattr(ne, "_call_claude", _fake_call)

    # First call: Claude path.
    n1 = generate_daily_narration(ctx)
    assert n1.source == "claude"
    assert n1.headline == "Mocked Claude headline"
    assert n1.tokens_in == 42 and n1.tokens_out == 99
    assert n1.model == DEFAULT_LLM_MODEL
    assert claude_call_count["n"] == 1

    # Cache file should exist now.
    cache_file = _narration_cache_path(ctx.target_date, isolate_cache_and_key)
    assert cache_file.exists()

    # Second call: cache hit, no Claude invocation.
    n2 = generate_daily_narration(ctx)
    assert n2.headline == "Mocked Claude headline"
    assert claude_call_count["n"] == 1   # not incremented


def test_claude_call_exception_falls_back_to_template(monkeypatch, isolate_cache_and_key: Path) -> None:
    """If the SDK call raises (network, bad credentials, etc.), template
    fallback runs and is NOT cached."""
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def _failing_call(*args, **kwargs):
        raise RuntimeError("network error")
    monkeypatch.setattr(ne, "_call_claude", _failing_call)

    n = generate_daily_narration(ctx)
    assert n.source == "template"
    cache_file = _narration_cache_path(ctx.target_date, isolate_cache_and_key)
    assert not cache_file.exists()


def test_claude_bad_json_falls_back_to_template(monkeypatch, isolate_cache_and_key: Path) -> None:
    """If Claude returns garbage that can't be parsed, template runs and
    nothing is cached."""
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    monkeypatch.setattr(ne, "_call_claude",
                        lambda *a, **k: ("this is not json", 50, 30))

    n = generate_daily_narration(ctx)
    assert n.source == "template"
    cache_file = _narration_cache_path(ctx.target_date, isolate_cache_and_key)
    assert not cache_file.exists()


def test_claude_bad_json_still_records_billable_tokens(monkeypatch, isolate_cache_and_key: Path) -> None:
    """A successful-but-unparseable Claude response is BILLABLE: its tokens
    must be recorded even though the text falls back to the template.
    Regression for the telemetry under-count bug — previously the cost was
    recorded only inside the parsed-OK branch, so malformed JSON silently
    dropped the spend."""
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(ne, "_call_claude",
                        lambda *a, **k: ("this is not json", 50, 30))

    calls: list = []
    import engine.llm_telemetry as tel
    monkeypatch.setattr(tel, "record_call", lambda **kw: calls.append(kw))

    n = generate_daily_narration(ctx)
    assert n.source == "template"            # text still falls back…
    # …but the billable tokens were recorded exactly once (not dropped, not
    # double-counted).
    assert len(calls) == 1
    assert calls[0]["tokens_in"] == 50
    assert calls[0]["tokens_out"] == 30
    assert calls[0]["source"] == "narration"


def test_claude_success_records_billable_tokens_once(monkeypatch, isolate_cache_and_key: Path) -> None:
    """The happy path records cost exactly once (guards against a double-count
    after moving the recorder out of the parsed-OK branch)."""
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def _fake_call(system, user, *, model, api_key):
        payload = {"headline": "H", "body": "B1.\n\nB2.",
                   "sections": [{"title": "S", "bullets": ["a", "b"]}]}
        return json.dumps(payload), 42, 99
    monkeypatch.setattr(ne, "_call_claude", _fake_call)

    calls: list = []
    import engine.llm_telemetry as tel
    monkeypatch.setattr(tel, "record_call", lambda **kw: calls.append(kw))

    n = generate_daily_narration(ctx)
    assert n.source == "claude"
    assert len(calls) == 1
    assert calls[0]["tokens_in"] == 42 and calls[0]["tokens_out"] == 99


def test_use_cache_false_bypasses_cache(monkeypatch, isolate_cache_and_key: Path) -> None:
    """use_cache=False forces a fresh call even when a cache file exists."""
    ctx = _make_full_context()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    # Pre-seed the cache.
    cached = DailyNarration(
        date=ctx.target_date.isoformat(),
        headline="Stale cache",
        body="Stale body.",
        sections=[], source="claude", model="old-model",
        tokens_in=0, tokens_out=0, generated_at="2026-01-01T00:00:00+00:00",
    )
    _write_narration_cache(
        _narration_cache_path(ctx.target_date, isolate_cache_and_key), cached,
    )

    monkeypatch.setattr(ne, "_call_claude",
                        lambda *a, **k: (
                            json.dumps({
                                "headline": "Fresh result",
                                "body": "Fresh body.",
                                "sections": [],
                            }),
                            10, 5,
                        ))

    n = generate_daily_narration(ctx, use_cache=False)
    assert n.headline == "Fresh result"
    assert n.source == "claude"


# ─── port-deficit context wiring ───────────────────────────────────────────

class _FakePort:
    """Lightweight stand-in for PortSupplyState — same duck-typed shape
    the narrator's _summarize_port_deficit reads."""
    def __init__(self, *, locode, name, region, supply_deficit_days,
                 severity_label, container_type="40FT_DRY"):
        self.locode = locode
        self.name = name
        self.region = region
        self.supply_deficit_days = supply_deficit_days
        self.severity_label = severity_label
        self.container_type = container_type


class _FakeExposure:
    def __init__(self, ticker, weight=0.5):
        self.ticker = ticker
        self.exposure_weight = weight


class _FakeChain:
    def __init__(self, port, exposed_companies):
        self.port = port
        self.exposed_companies = exposed_companies


def _make_chain(locode, name, region, deficit, severity, tickers):
    return _FakeChain(
        port=_FakePort(
            locode=locode, name=name, region=region,
            supply_deficit_days=deficit, severity_label=severity,
        ),
        exposed_companies=[_FakeExposure(t) for t in tickers],
    )


def test_narration_context_carries_top_port_deficits_field() -> None:
    """The NarrationContext dataclass must accept the new
    top_port_deficits kwarg without breaking older call sites that
    leave it unset (default empty list)."""
    ctx_empty = NarrationContext(target_date=date(2026, 5, 20))
    assert ctx_empty.top_port_deficits == []

    chains = [_make_chain("CNSHA", "Shanghai", "Asia East", -5.0,
                          "Deficit", ["ZIM", "MATX"])]
    ctx_full = NarrationContext(target_date=date(2026, 5, 20),
                                top_port_deficits=chains)
    assert ctx_full.top_port_deficits == chains


def test_template_skips_paragraph_when_no_port_in_deficit() -> None:
    """A green day (all chains have non-negative deficit) must NOT
    surface a port-deficit paragraph — otherwise the briefing reads
    alarmist on calm conditions."""
    chains = [_make_chain("CNSHA", "Shanghai", "Asia East", +5.0,
                          "Surplus", ["ZIM"])]
    ctx = NarrationContext(target_date=date(2026, 5, 20),
                           top_port_deficits=chains)
    n = _template_daily_narration(ctx)
    assert "Port container supply" not in n.body


def test_template_renders_port_deficit_paragraph_with_tickers() -> None:
    """When a port IS in deficit, the paragraph must surface:
      * port name + locode
      * deficit days with sign
      * exposed tickers inline"""
    chains = [
        _make_chain("CNSHA", "Shanghai", "Asia East", -8.0,
                    "Deficit", ["ZIM", "MATX", "DAC"]),
        _make_chain("AEJEA", "Jebel Ali", "Middle East", -12.0,
                    "Critical Deficit", ["DSX"]),
    ]
    ctx = NarrationContext(target_date=date(2026, 5, 20),
                           top_port_deficits=chains)
    n = _template_daily_narration(ctx)
    assert "Port container supply" in n.body
    # Both port names + their tickers must appear inline.
    assert "Shanghai" in n.body and "CNSHA" in n.body
    assert "Jebel Ali" in n.body and "AEJEA" in n.body
    assert "ZIM" in n.body
    assert "DSX" in n.body


def test_template_caps_port_deficit_paragraph_at_three_ports() -> None:
    """Only the top-3 most-stressed ports render in the paragraph so the
    body stays scannable."""
    chains = [
        _make_chain(f"P{i:02d}", f"Port{i}", "Region", -(i + 1) * 2.0,
                    "Deficit", [f"T{i}"])
        for i in range(7)
    ]
    ctx = NarrationContext(target_date=date(2026, 5, 20),
                           top_port_deficits=chains)
    n = _template_daily_narration(ctx)
    body = n.body
    # First three should appear in the briefing
    assert "Port0" in body
    assert "Port2" in body
    # Beyond the cap shouldn't
    assert "Port5" not in body
    assert "Port6" not in body


def test_user_prompt_payload_includes_top_port_deficits() -> None:
    """The LLM prompt's JSON payload must carry the port-deficit context
    alongside the other structured signals."""
    chains = [_make_chain("CNSHA", "Shanghai", "Asia East", -5.0,
                          "Deficit", ["ZIM", "MATX"])]
    ctx = NarrationContext(target_date=date(2026, 5, 20),
                           top_port_deficits=chains)
    from engine.narration_engine import _build_daily_user_prompt
    prompt = _build_daily_user_prompt(ctx)
    assert "top_port_deficits" in prompt
    assert "Shanghai" in prompt
    assert "CNSHA" in prompt
