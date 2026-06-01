"""Tests for engine.tab_commentary — per-tab LLM-narrated editorial commentary.

Coverage:
  - TabCommentary dataclass shape
  - Template fallback: returns source="template" when no API key;
    deterministic given the same context
  - SQLite cache: round-trip; cache hit skips the LLM call; different
    hash → different cache key
  - stable_hash usage: same context dict (any insertion order) → same
    cache key across processes
  - Anthropic call: mock ``anthropic.Anthropic`` so the API is NEVER hit;
    verify the system message carries cache_control; verify token counts
    flow from the mock response into the dataclass
  - Cache TTL: an entry past TTL is regenerated, not returned

Every test is hermetic — no network, no real Anthropic SDK calls. The DB
is isolated per test via the same monkeypatch idiom used in test_state_db.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── DB isolation: every test gets a per-test SQLite file ──────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB pointed at tmp_path. Critical because
    the commentary cache lives in kv_state — without isolation, tests
    would see each other's cached entries."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ── Anthropic isolation: never let a test reach the real SDK ──────────────

@pytest.fixture(autouse=True)
def no_real_api_key(monkeypatch):
    """Strip ANTHROPIC_API_KEY from the environment so tests that don't
    explicitly mock the SDK can't accidentally hit it. Tests that need a
    key pass it explicitly via the api_key kwarg."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ── Helpers ───────────────────────────────────────────────────────────────

def _mock_anthropic_response(
    *, text: str, tokens_in: int = 123, tokens_out: int = 45,
) -> MagicMock:
    """Build a MagicMock that quacks like an anthropic.Messages.Create
    response object: ``.content[0].type/.text`` and ``.usage.input_tokens
    /.output_tokens``."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    usage = MagicMock()
    usage.input_tokens = tokens_in
    usage.output_tokens = tokens_out

    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    return resp


def _make_mock_anthropic_class(response: MagicMock):
    """Build a MagicMock that stands in for ``anthropic.Anthropic`` — a
    callable class whose instances expose ``.messages.create(...)``.
    Returns (MockClass, the_create_mock) so callers can assert against
    the inner ``create`` call."""
    create_mock = MagicMock(return_value=response)
    instance = MagicMock()
    instance.messages.create = create_mock

    cls = MagicMock(return_value=instance)
    return cls, create_mock


# ═══════════════════════════════════════════════════════════════════════════
# Dataclass shape
# ═══════════════════════════════════════════════════════════════════════════

def test_tab_commentary_dataclass_fields() -> None:
    """Locks the public surface area of the dataclass — adding a field
    here is a deliberate API change, not a silent breakage."""
    from engine.tab_commentary import TabCommentary
    from dataclasses import fields

    field_names = {f.name for f in fields(TabCommentary)}
    assert field_names == {
        "headline", "body", "source", "model",
        "tokens_in", "tokens_out", "generated_at",
    }


def test_tab_commentary_is_frozen() -> None:
    """Immutability invariant — once built, the dataclass cannot be
    mutated by the caller (defensive against UI code that might try
    to overwrite fields post-render)."""
    from engine.tab_commentary import TabCommentary

    c = TabCommentary(
        headline="h", body="b", source="llm", model="m",
        tokens_in=0, tokens_out=0, generated_at="x",
    )
    with pytest.raises((AttributeError, Exception)):
        c.headline = "mutated"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# Template fallback
# ═══════════════════════════════════════════════════════════════════════════

def test_template_fallback_when_no_api_key() -> None:
    """No ANTHROPIC_API_KEY in env → source must be 'template'."""
    from engine.tab_commentary import build_commentary

    out = build_commentary("overview", {"score": 0.72, "trend": "Rising"})
    assert out.source == "template"
    assert out.model == ""
    assert out.tokens_in == 0
    assert out.tokens_out == 0
    assert out.headline
    assert out.body


def test_template_fallback_is_deterministic() -> None:
    """Same context dict → identical commentary across calls (excluding
    generated_at, which is wall-clock)."""
    from engine.tab_commentary import build_commentary

    ctx = {"score": 0.72, "trend": "Rising", "bdi": 1847}
    a = build_commentary("overview", ctx)
    b = build_commentary("overview", ctx)
    assert a.headline == b.headline
    assert a.body == b.body
    assert a.source == "template" == b.source


def test_template_fallback_surfaces_context_metrics() -> None:
    """Sanity check: at least one of the supplied keys appears in the
    body text, so callers know the template is actually using their data."""
    from engine.tab_commentary import build_commentary

    out = build_commentary(
        "portfolio",
        {"composite_score": 0.81, "top_signal": "Trans-Pacific tightening"},
    )
    assert (
        "composite score" in out.body.lower()
        or "top signal" in out.body.lower()
        or "0.81" in out.body
    )


def test_template_fallback_handles_empty_context() -> None:
    """Empty context still produces a non-empty template — UI must always
    have something to render."""
    from engine.tab_commentary import build_commentary

    out = build_commentary("overview", {})
    assert out.source == "template"
    assert out.headline
    assert out.body


# ═══════════════════════════════════════════════════════════════════════════
# stable_hash / cache-key behaviour
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_key_is_order_independent() -> None:
    """Two dicts with identical contents but different insertion order
    must produce the SAME cache key. Without sort_keys this regresses."""
    from engine.tab_commentary import _cache_key

    ctx_a = {"score": 0.7, "trend": "Rising", "bdi": 1847}
    ctx_b = {"bdi": 1847, "trend": "Rising", "score": 0.7}
    assert _cache_key("overview", ctx_a) == _cache_key("overview", ctx_b)


def test_cache_key_differs_when_context_differs() -> None:
    """Different context → different key (otherwise we'd serve stale
    commentary across unrelated tab states)."""
    from engine.tab_commentary import _cache_key

    a = _cache_key("overview", {"score": 0.7})
    b = _cache_key("overview", {"score": 0.8})
    assert a != b


def test_cache_key_differs_across_tabs() -> None:
    """Same context on different tabs must produce different keys."""
    from engine.tab_commentary import _cache_key

    ctx = {"score": 0.7}
    assert _cache_key("overview", ctx) != _cache_key("portfolio", ctx)


def test_cache_key_uses_stable_hash_not_pythons_hash() -> None:
    """Process-stability invariant — the hash digest must be reproducible
    without PYTHONHASHSEED set. We assert the digest matches what
    ``utils.helpers.stable_hash`` would produce for the same input."""
    from engine.tab_commentary import _cache_key, _serialize_context
    from utils.helpers import stable_hash

    ctx = {"score": 0.7, "trend": "Rising"}
    payload = f"overview|{_serialize_context(ctx)}"
    expected_digest = f"{stable_hash(payload):08x}"
    key = _cache_key("overview", ctx)
    assert key == f"commentary:overview:{expected_digest}"


# ═══════════════════════════════════════════════════════════════════════════
# SQLite cache round-trip
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_roundtrip_via_write_then_read() -> None:
    """Write a TabCommentary to kv_state, read it back, get the same fields."""
    from engine.tab_commentary import (
        TabCommentary, _cache_key, _read_cache, _write_cache,
    )

    key = _cache_key("overview", {"score": 0.7})
    original = TabCommentary(
        headline="Test headline",
        body="Test body across two\n\nparagraphs.",
        source="llm",
        model="claude-haiku-4-5-20251001",
        tokens_in=120,
        tokens_out=45,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_cache(key, original)
    loaded = _read_cache(key)
    assert loaded is not None
    assert loaded.headline == original.headline
    assert loaded.body == original.body
    assert loaded.source == "llm"
    assert loaded.tokens_in == 120
    assert loaded.tokens_out == 45


def test_cache_hit_skips_the_llm_call() -> None:
    """First call writes to cache; second call with same context must NOT
    invoke the Anthropic SDK at all (zero ``create`` calls)."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H1", "body": "B1 paragraph."}),
        tokens_in=100, tokens_out=30,
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    ctx = {"score": 0.7, "trend": "Rising"}

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        first = tc.build_commentary("overview", ctx, api_key="sk-test")
        second = tc.build_commentary("overview", ctx, api_key="sk-test")

    assert first.source == "llm"
    assert second.source == "llm"
    # The KEY assertion: the LLM was called exactly once across both calls.
    assert create_mock.call_count == 1
    # Same body comes back from the cache hit.
    assert first.headline == second.headline
    assert first.body == second.body


def test_use_cache_false_bypasses_lookup_and_write() -> None:
    """When use_cache=False, both the read AND the write paths are skipped —
    the caller forces a fresh LLM call and nothing lands in kv_state."""
    from engine import tab_commentary as tc
    from state.db import get_connection

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H", "body": "B."})
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    ctx = {"score": 0.5}
    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        tc.build_commentary("overview", ctx, api_key="sk-test", use_cache=False)
        tc.build_commentary("overview", ctx, api_key="sk-test", use_cache=False)

    # Two LLM calls because cache was bypassed both times.
    assert create_mock.call_count == 2

    # Nothing was written to kv_state under the commentary: prefix.
    conn = get_connection()
    rows = conn.execute(
        "SELECT key FROM kv_state WHERE key LIKE 'commentary:%'"
    ).fetchall()
    assert rows == []


def test_template_fallback_is_not_cached() -> None:
    """When no API key is configured we return a template, but the cache
    slot must stay open so the LLM can fill it once a key shows up."""
    from engine import tab_commentary as tc
    from state.db import get_connection

    tc.build_commentary("overview", {"score": 0.7})

    conn = get_connection()
    rows = conn.execute(
        "SELECT key FROM kv_state WHERE key LIKE 'commentary:%'"
    ).fetchall()
    assert rows == []


# ═══════════════════════════════════════════════════════════════════════════
# Anthropic call wiring
# ═══════════════════════════════════════════════════════════════════════════

def test_llm_call_passes_cache_control_on_system_prompt() -> None:
    """The system block MUST be sent with ``cache_control: ephemeral`` so
    Anthropic caches the editorial-style preamble between calls.

    This is the prompt-caching invariant from CLAUDE.md guidance — drop
    it and per-call cost spikes."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H", "body": "B."})
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        tc.build_commentary("overview", {"score": 0.7}, api_key="sk-test")

    assert create_mock.call_count == 1
    call_kwargs = create_mock.call_args.kwargs
    system = call_kwargs.get("system")
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # The system text must mention the editorial voice — sanity check.
    assert "WSJ" in system[0]["text"] or "editorial" in system[0]["text"].lower()


def test_llm_call_token_counts_flow_into_dataclass() -> None:
    """``tokens_in`` and ``tokens_out`` on the returned TabCommentary must
    come from the response's usage block, not be hardcoded."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H", "body": "B."}),
        tokens_in=987, tokens_out=321,
    )
    MockClass, _ = _make_mock_anthropic_class(resp)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        out = tc.build_commentary("overview", {"x": 1}, api_key="sk-test")

    assert out.source == "llm"
    assert out.tokens_in == 987
    assert out.tokens_out == 321
    assert out.model == tc.DEFAULT_LLM_MODEL


def test_llm_call_uses_default_model_and_max_tokens() -> None:
    """Without an explicit override, the default model + max_tokens go to
    the SDK call. Catches accidental default-flip during refactors."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H", "body": "B."})
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        tc.build_commentary("overview", {"x": 1}, api_key="sk-test")

    kwargs = create_mock.call_args.kwargs
    assert kwargs["model"] == tc.DEFAULT_LLM_MODEL
    assert kwargs["max_tokens"] == tc.DEFAULT_MAX_TOKENS


def test_llm_failure_falls_back_to_template() -> None:
    """Any exception from the SDK path → template fallback, no raise."""
    from engine import tab_commentary as tc

    broken_create = MagicMock(side_effect=RuntimeError("network down"))
    instance = MagicMock()
    instance.messages.create = broken_create
    MockClass = MagicMock(return_value=instance)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        out = tc.build_commentary(
            "overview", {"score": 0.7}, api_key="sk-test",
        )
    assert out.source == "template"


def test_llm_bad_json_falls_back_to_template() -> None:
    """Response that isn't valid JSON → template fallback (not raised)."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(text="not json at all {{{ broken")
    MockClass, _ = _make_mock_anthropic_class(resp)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        out = tc.build_commentary("overview", {"x": 1}, api_key="sk-test")
    assert out.source == "template"


def test_llm_tolerates_markdown_fenced_json() -> None:
    """Haiku occasionally wraps JSON in ```json ... ``` fences despite the
    system prompt asking otherwise. We strip the fence and recover."""
    from engine import tab_commentary as tc

    fenced = "```json\n" + json.dumps({"headline": "H", "body": "B body."}) + "\n```"
    resp = _mock_anthropic_response(text=fenced)
    MockClass, _ = _make_mock_anthropic_class(resp)

    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        out = tc.build_commentary("overview", {"x": 1}, api_key="sk-test")
    assert out.source == "llm"
    assert out.headline == "H"
    assert out.body == "B body."


def test_llm_call_includes_tab_name_and_context_in_user_prompt() -> None:
    """The user prompt must carry the tab name AND the serialized context
    so Claude can ground its commentary in what the user is actually
    looking at."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "H", "body": "B."})
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    ctx = {"bdi": 1847, "trend": "Rising"}
    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        tc.build_commentary("portfolio", ctx, api_key="sk-test")

    msgs = create_mock.call_args.kwargs["messages"]
    user_prompt = msgs[0]["content"]
    assert "portfolio" in user_prompt.lower()
    assert "1847" in user_prompt
    assert "Rising" in user_prompt


# ═══════════════════════════════════════════════════════════════════════════
# Cache TTL
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_entry_past_ttl_is_regenerated() -> None:
    """Backdate the updated_at column past CACHE_TTL_SECONDS — the next
    build_commentary call must NOT return the stale entry and must
    instead invoke the LLM."""
    from engine import tab_commentary as tc
    from state.db import get_connection

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "FRESH", "body": "Fresh body."}),
        tokens_in=10, tokens_out=5,
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    ctx = {"score": 0.7}
    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        first = tc.build_commentary("overview", ctx, api_key="sk-test")
        assert first.source == "llm"
        assert create_mock.call_count == 1

        # Backdate the kv_state row by 2 hours (TTL is 1h).
        conn = get_connection()
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cache_key = tc._cache_key("overview", ctx)
        conn.execute(
            "UPDATE kv_state SET updated_at = ? WHERE key = ?",
            (stale, cache_key),
        )

        second = tc.build_commentary("overview", ctx, api_key="sk-test")

    # A second LLM call was made because the cached row was past TTL.
    assert create_mock.call_count == 2
    assert second.source == "llm"
    assert second.headline == "FRESH"


def test_cache_entry_within_ttl_is_served() -> None:
    """Sanity counterpart to the TTL test — within TTL, the cache is
    returned and the LLM is NOT re-invoked."""
    from engine import tab_commentary as tc

    resp = _mock_anthropic_response(
        text=json.dumps({"headline": "ORIGINAL", "body": "Original body."})
    )
    MockClass, create_mock = _make_mock_anthropic_class(resp)

    ctx = {"score": 0.7}
    with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=MockClass)}):
        first = tc.build_commentary("overview", ctx, api_key="sk-test")
        second = tc.build_commentary("overview", ctx, api_key="sk-test")

    assert create_mock.call_count == 1
    assert first.headline == second.headline == "ORIGINAL"


def test_cache_ttl_default_is_one_hour() -> None:
    """Lock the documented TTL — bumping this is a deliberate decision,
    not a silent edit."""
    from engine.tab_commentary import CACHE_TTL_SECONDS

    assert CACHE_TTL_SECONDS == 3_600
