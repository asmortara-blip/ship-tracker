"""Tests for utils.helpers — small pure utilities used app-wide.

Covers:
  - slugify: lowercase, strip, collapse whitespace/underscores/dashes to "_",
    drop punctuation, idempotency, empty/whitespace input, non-string input
  - format_usd: compact T/B/M tiering on |value|, negative values, sub-million
    fallback to comma-formatted dollars, compact=False bypass
  - score_to_label: High / Moderate / Low boundary behaviour (>=high, <=low),
    custom thresholds, extreme inputs
  - trend_label: Rising / Falling / Stable thresholds (>threshold, <-threshold),
    default 0.05 boundary, custom threshold
  - delta_color: "normal" by default, "inverse" when flag set; ignores value
  - now_iso: returns a parseable ISO-8601 string with UTC offset; two calls in
    sequence are monotonically non-decreasing
  - safe_normalize: maps min→0, max→1, midpoint→0.5; returns 0.5 series when
    no variance; honours injected min_val/max_val overrides; preserves index
  - sigmoid: sigmoid(0)=0.5, monotone increasing, bounded in (0,1), symmetric
    around 0, clamps to avoid math.exp overflow on extreme negative/positive
  - stable_hash: deterministic across calls in-process, non-negative 32-bit
    integer, sensitive to input differences, accepts str / bytes / int / tuple
    (anything stringifiable), and (the defining property) does NOT depend on
    PYTHONHASHSEED — compare against a known precomputed digest.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import sys
from datetime import datetime

import pandas as pd
import pytest

from utils.helpers import (
    delta_color,
    format_usd,
    now_iso,
    safe_normalize,
    score_to_label,
    sigmoid,
    slugify,
    stable_hash,
    trend_label,
)


# ─── slugify ───────────────────────────────────────────────────────────────

def test_slugify_lowercases_and_strips() -> None:
    assert slugify("  Hello World  ") == "hello_world"


def test_slugify_collapses_runs_of_separators() -> None:
    # spaces, underscores, dashes (and mixes) all collapse to a single "_"
    assert slugify("foo   bar") == "foo_bar"
    assert slugify("foo___bar") == "foo_bar"
    assert slugify("foo---bar") == "foo_bar"
    assert slugify("foo _-_ bar") == "foo_bar"


def test_slugify_drops_punctuation_keeps_word_chars() -> None:
    # ".", "!", "?", "/" are not \w or \s or "-" → dropped entirely
    assert slugify("hello, world!") == "hello_world"
    assert slugify("a/b?c.d") == "abcd"


def test_slugify_empty_input_returns_empty_string() -> None:
    assert slugify("") == ""
    assert slugify("   ") == ""


def test_slugify_is_idempotent_on_clean_slug() -> None:
    once = slugify("Container Throughput 2026")
    twice = slugify(once)
    assert once == twice == "container_throughput_2026"


def test_slugify_accepts_non_string_input() -> None:
    # Signature is typed str but the implementation does str(text) — exercise it.
    assert slugify(42) == "42"


# ─── format_usd ────────────────────────────────────────────────────────────

def test_format_usd_compact_tiers() -> None:
    assert format_usd(2_500_000_000_000) == "$2.50T"
    assert format_usd(3_400_000_000) == "$3.40B"
    assert format_usd(1_250_000) == "$1.25M"


def test_format_usd_below_million_uses_comma_format() -> None:
    assert format_usd(750_000) == "$750,000"
    assert format_usd(0) == "$0"


def test_format_usd_negative_values_use_abs_for_tiering() -> None:
    # Tier is chosen by |value| but the sign is preserved in the output.
    assert format_usd(-2_500_000_000) == "$-2.50B"
    assert format_usd(-1_500_000) == "$-1.50M"


def test_format_usd_compact_false_bypasses_tiering() -> None:
    # Even trillion-scale values fall through when compact=False.
    assert format_usd(2_500_000_000_000, compact=False) == "$2,500,000,000,000"


# ─── score_to_label ────────────────────────────────────────────────────────

def test_score_to_label_default_thresholds() -> None:
    assert score_to_label(0.90) == "High"
    assert score_to_label(0.70) == "High"        # >=high boundary
    assert score_to_label(0.50) == "Moderate"
    assert score_to_label(0.35) == "Low"         # <=low boundary
    assert score_to_label(0.10) == "Low"


def test_score_to_label_strictly_between_returns_moderate() -> None:
    assert score_to_label(0.36) == "Moderate"
    assert score_to_label(0.69) == "Moderate"


def test_score_to_label_custom_thresholds() -> None:
    assert score_to_label(0.55, high=0.50, low=0.20) == "High"
    assert score_to_label(0.15, high=0.50, low=0.20) == "Low"
    assert score_to_label(0.30, high=0.50, low=0.20) == "Moderate"


def test_score_to_label_extreme_inputs() -> None:
    # No domain enforcement — anything >= high is "High".
    assert score_to_label(5.0) == "High"
    assert score_to_label(-3.0) == "Low"


# ─── trend_label ───────────────────────────────────────────────────────────

def test_trend_label_default_threshold() -> None:
    assert trend_label(0.10) == "Rising"
    assert trend_label(-0.10) == "Falling"
    assert trend_label(0.0) == "Stable"


def test_trend_label_boundary_is_exclusive() -> None:
    # Strict inequality: exactly +threshold / -threshold → Stable.
    assert trend_label(0.05) == "Stable"
    assert trend_label(-0.05) == "Stable"
    # Just past the boundary → trend.
    assert trend_label(0.0501) == "Rising"
    assert trend_label(-0.0501) == "Falling"


def test_trend_label_custom_threshold() -> None:
    assert trend_label(0.08, threshold=0.10) == "Stable"
    assert trend_label(0.15, threshold=0.10) == "Rising"
    assert trend_label(-0.15, threshold=0.10) == "Falling"


# ─── delta_color ───────────────────────────────────────────────────────────

def test_delta_color_default_is_normal() -> None:
    assert delta_color(1.0) == "normal"
    assert delta_color(-1.0) == "normal"
    assert delta_color(0.0) == "normal"


def test_delta_color_inverse_flag_overrides_value() -> None:
    # The function only cares about `inverse`; value is informational.
    assert delta_color(1.0, inverse=True) == "inverse"
    assert delta_color(-99.0, inverse=True) == "inverse"


# ─── now_iso ───────────────────────────────────────────────────────────────

def test_now_iso_is_parseable_iso8601_with_utc_offset() -> None:
    s = now_iso()
    parsed = datetime.fromisoformat(s)
    assert parsed.tzinfo is not None
    # UTC offset is exactly zero.
    assert parsed.utcoffset().total_seconds() == 0.0
    # Format ends with +00:00 (datetime.isoformat for tz=UTC).
    assert s.endswith("+00:00")


def test_now_iso_is_monotonic_non_decreasing() -> None:
    a = now_iso()
    b = now_iso()
    # Two consecutive calls in the same microsecond can tie, but never invert.
    assert a <= b


# ─── safe_normalize ────────────────────────────────────────────────────────

def test_safe_normalize_maps_min_to_zero_and_max_to_one() -> None:
    s = pd.Series([10.0, 20.0, 30.0])
    out = safe_normalize(s)
    assert out.iloc[0] == pytest.approx(0.0)
    assert out.iloc[1] == pytest.approx(0.5)
    assert out.iloc[2] == pytest.approx(1.0)


def test_safe_normalize_returns_half_when_no_variance() -> None:
    s = pd.Series([7.0, 7.0, 7.0, 7.0])
    out = safe_normalize(s)
    assert list(out) == [0.5, 0.5, 0.5, 0.5]
    # Index is preserved.
    assert list(out.index) == list(s.index)


def test_safe_normalize_preserves_index() -> None:
    s = pd.Series([1.0, 2.0, 3.0], index=["a", "b", "c"])
    out = safe_normalize(s)
    assert list(out.index) == ["a", "b", "c"]


def test_safe_normalize_with_explicit_min_max() -> None:
    # Forcing min=0/max=100 anchors the scale regardless of series content.
    s = pd.Series([25.0, 50.0, 75.0])
    out = safe_normalize(s, min_val=0.0, max_val=100.0)
    assert out.iloc[0] == pytest.approx(0.25)
    assert out.iloc[1] == pytest.approx(0.50)
    assert out.iloc[2] == pytest.approx(0.75)


def test_safe_normalize_explicit_min_equals_max_returns_half() -> None:
    # The mn == mx branch fires off the injected values too.
    s = pd.Series([1.0, 2.0, 3.0])
    out = safe_normalize(s, min_val=5.0, max_val=5.0)
    assert list(out) == [0.5, 0.5, 0.5]


# ─── sigmoid ───────────────────────────────────────────────────────────────

def test_sigmoid_at_zero_is_half() -> None:
    assert sigmoid(0.0) == pytest.approx(0.5)


def test_sigmoid_is_bounded_open_interval() -> None:
    # Strictly between 0 and 1 for finite inputs.
    for x in [-10.0, -1.0, 0.0, 1.0, 10.0]:
        y = sigmoid(x)
        assert 0.0 < y < 1.0


def test_sigmoid_is_monotone_increasing() -> None:
    xs = [-3.0, -1.0, -0.1, 0.0, 0.1, 1.0, 3.0]
    ys = [sigmoid(x) for x in xs]
    assert ys == sorted(ys)


def test_sigmoid_is_symmetric_around_zero() -> None:
    # sigmoid(-x) == 1 - sigmoid(x)
    for x in [0.5, 1.0, 2.5, 7.0]:
        assert sigmoid(-x) == pytest.approx(1.0 - sigmoid(x))


def test_sigmoid_clamps_extreme_inputs() -> None:
    # Without the clamp, math.exp(800) would raise OverflowError.
    assert sigmoid(1e6) == pytest.approx(1.0)
    assert sigmoid(-1e6) == pytest.approx(0.0)
    # Math doesn't blow up.
    assert math.isfinite(sigmoid(1e9))
    assert math.isfinite(sigmoid(-1e9))


# ─── stable_hash ───────────────────────────────────────────────────────────

def test_stable_hash_is_deterministic_in_process() -> None:
    # Same input → same value, every call.
    h1 = stable_hash("seed")
    h2 = stable_hash("seed")
    h3 = stable_hash("seed")
    assert h1 == h2 == h3


def test_stable_hash_returns_non_negative_32_bit_int() -> None:
    h = stable_hash("anything")
    assert isinstance(h, int)
    assert 0 <= h < 2**32


def test_stable_hash_distinguishes_different_inputs() -> None:
    # Not a hard requirement of the contract, but a sanity check —
    # two clearly different short strings should not collide on blake2b/32.
    assert stable_hash("a") != stable_hash("b")
    assert stable_hash("seed_1") != stable_hash("seed_2")


def test_stable_hash_accepts_heterogeneous_inputs() -> None:
    # The impl stringifies via str(s); any object that has a stable repr works.
    for obj in ["text", 42, (1, 2, 3), 3.14, True]:
        h = stable_hash(obj)
        assert isinstance(h, int)
        assert 0 <= h < 2**32


def test_stable_hash_matches_known_blake2b_digest() -> None:
    # Pinning the algorithm: stable_hash(s) must equal the int form of the
    # first 4 bytes of blake2b(s.encode()) — this is the property that breaks
    # if anyone swaps in Python's salted hash() again.
    s = "transpacific_eb"
    expected = int.from_bytes(
        hashlib.blake2b(s.encode("utf-8"), digest_size=4).digest(), "big"
    )
    assert stable_hash(s) == expected


def test_stable_hash_survives_process_restart() -> None:
    """The defining property: identical value across separate Python processes.

    Python's built-in ``hash()`` for str is salted per process (PYTHONHASHSEED
    defaults to random), so a fresh interpreter would return a different number.
    ``stable_hash`` must not — that's its entire reason to exist.
    """
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "from utils.helpers import stable_hash; "
        "print(stable_hash('cross_process_seed'))"
    )
    # Derive the repo root from THIS file's location (tests/ -> repo root) so the
    # subprocess can resolve `.` to import utils.helpers. Hardcoding an absolute
    # path made this fail anywhere but the original author's machine (it raised
    # FileNotFoundError on CI, whose checkout lives under /home/runner/...).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    a = subprocess.check_output(
        [sys.executable, "-c", code], cwd=repo_root, text=True
    ).strip()
    b = subprocess.check_output(
        [sys.executable, "-c", code], cwd=repo_root, text=True
    ).strip()
    assert a == b
    # Also matches the in-process value.
    assert int(a) == stable_hash("cross_process_seed")
