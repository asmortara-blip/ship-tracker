"""tests/test_tradeability.py — tests for the R035 real-liquidity tradeability filter.

Covers:
  * dollar_adv computes mean(raw close × volume) on a seeded frame (arithmetic),
    and returns None when volume is absent / dark.
  * crowding_score counts ideas sharing a dominant_driver_key.
  * borrow_feasibility tiers are monotonic with liquidity.
  * assess_tradeability flags a thin High-conviction idea (caution/untradeable),
    passes a deep-liquid one (tradeable), and returns a neutral 'unknown' verdict
    on empty input — and never raises on garbage.
  * screen_ideas preserves order and degrades gracefully.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from processing import tradeability as T


# ── Minimal idea stand-in (avoids constructing a full EquityIdea) ──────────

@dataclass
class FakeIdea:
    ticker: str
    direction: str = "Bullish"
    conviction_label: str = "High"
    conviction_score: float = 0.8
    dominant_driver_key: str = "chokepoint"


def _frame(close, volume, n=40):
    """Build a normalized-shape OHLCV frame with constant close/volume."""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": [float(close)] * n,
        "volume": [float(volume)] * n,
    })


# ── dollar_adv — arithmetic ─────────────────────────────────────────────────

def test_dollar_adv_computes_raw_close_times_volume_mean() -> None:
    # close=20, volume=500_000 -> 10,000,000 every day -> mean is exactly that.
    sd = {"ZIM": _frame(20.0, 500_000)}
    adv = T.dollar_adv("ZIM", sd)
    assert adv == pytest.approx(10_000_000.0)


def test_dollar_adv_uses_window_tail() -> None:
    # Ramp volume so the trailing-window mean differs from the full-history mean.
    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "close": [10.0] * 60,
        # volume ramps 1..60 (×1000); last 30 are 31..60.
        "volume": [(i + 1) * 1_000 for i in range(60)],
    })
    adv = T.dollar_adv("X", {"X": df}, window=30)
    # mean of close×volume over last 30 = 10 * 1000 * mean(31..60) = 10000 * 45.5
    assert adv == pytest.approx(10_000.0 * 45.5)


def test_dollar_adv_none_when_volume_column_absent() -> None:
    # The tab-smoke populated bundle has NO volume column — must read as dark.
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame({"date": dates, "close": [10.0] * 30})
    assert T.dollar_adv("ZIM", {"ZIM": df}) is None


def test_dollar_adv_none_when_ticker_absent_or_empty_inputs() -> None:
    assert T.dollar_adv("NOPE", {"ZIM": _frame(10, 100)}) is None
    assert T.dollar_adv("ZIM", {}) is None
    assert T.dollar_adv("ZIM", None) is None
    assert T.dollar_adv("", None) is None


def test_dollar_adv_none_when_all_volume_zero() -> None:
    # A frame with a volume column but every value zero has no real liquidity.
    assert T.dollar_adv("ZIM", {"ZIM": _frame(10.0, 0)}) is None


def test_dollar_adv_uses_raw_close_not_adjusted() -> None:
    # Even with an adj_factor column present, dollar-ADV must use the RAW close
    # (point-in-time liquidity fact, not a return). adj_factor of 2.0 must NOT
    # double the dollar value.
    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "close": [10.0] * 40,
        "volume": [100_000.0] * 40,
        "adj_factor": [2.0] * 40,
    })
    adv = T.dollar_adv("X", {"X": df})
    assert adv == pytest.approx(10.0 * 100_000.0)  # NOT 20 * 100_000


# ── crowding_score ──────────────────────────────────────────────────────────

def test_crowding_score_counts_shared_driver() -> None:
    ideas = [
        FakeIdea("A", dominant_driver_key="chokepoint"),
        FakeIdea("B", dominant_driver_key="chokepoint"),
        FakeIdea("C", dominant_driver_key="chokepoint"),
        FakeIdea("D", dominant_driver_key="rate"),
    ]
    crowd = T.crowding_score(ideas)
    assert crowd["A"] == 3
    assert crowd["B"] == 3
    assert crowd["C"] == 3
    assert crowd["D"] == 1


def test_crowding_score_empty_driver_is_crowd_of_one() -> None:
    ideas = [
        FakeIdea("A", dominant_driver_key=""),
        FakeIdea("B", dominant_driver_key=""),
    ]
    crowd = T.crowding_score(ideas)
    # Unkeyed ideas are not crowding each other.
    assert crowd["A"] == 1
    assert crowd["B"] == 1


def test_crowding_score_empty_input() -> None:
    assert T.crowding_score([]) == {}
    assert T.crowding_score(None) == {}


# ── borrow_feasibility — monotonic with liquidity ───────────────────────────

def test_borrow_feasibility_monotonic_with_liquidity() -> None:
    deep = T.borrow_feasibility("X", 100_000_000.0)   # Deep
    liquid = T.borrow_feasibility("X", 20_000_000.0)  # Liquid
    moderate = T.borrow_feasibility("X", 5_000_000.0)  # Moderate
    thin = T.borrow_feasibility("X", 1_000_000.0)     # Thin
    illiquid = T.borrow_feasibility("X", 100_000.0)   # Illiquid

    assert deep == "borrowable"
    assert liquid == "borrowable"
    assert moderate == "hard-to-borrow"
    assert thin == "hard-to-borrow"
    assert illiquid == "likely-unborrowable"

    # Map each flag to an ordinal severity and assert it never improves as
    # liquidity falls.
    sev = {"borrowable": 0, "hard-to-borrow": 1, "likely-unborrowable": 2,
           "borrow-unknown": 9}
    order = [deep, liquid, moderate, thin, illiquid]
    sevs = [sev[f] for f in order]
    assert sevs == sorted(sevs), "borrow severity must rise as liquidity falls"


def test_borrow_feasibility_unknown_when_adv_none() -> None:
    assert T.borrow_feasibility("X", None) == "borrow-unknown"


# ── assess_tradeability ─────────────────────────────────────────────────────

def test_assess_thin_high_conviction_is_not_tradeable() -> None:
    # $10 × 50_000 = $500K/day -> "Thin" tier (verdict floor "caution").
    sd = {"THIN": _frame(10.0, 50_000)}
    idea = FakeIdea("THIN", direction="Bullish", conviction_label="High")
    read = T.assess_tradeability(idea, sd, [idea])
    assert read.liquidity_tier == "Thin"
    assert read.verdict in ("caution", "untradeable")
    assert read.dollar_adv == pytest.approx(500_000.0)


def test_assess_illiquid_short_is_untradeable_via_borrow() -> None:
    # $5 × 20_000 = $100K/day -> "Illiquid"; a SHORT there is likely-unborrowable.
    sd = {"ILLQ": _frame(5.0, 20_000)}
    idea = FakeIdea("ILLQ", direction="Bearish", conviction_label="High")
    read = T.assess_tradeability(idea, sd, [idea])
    assert read.liquidity_tier == "Illiquid"
    assert read.is_short is True
    assert read.borrow_flag == "likely-unborrowable"
    assert read.verdict == "untradeable"


def test_assess_deep_liquid_idea_is_tradeable() -> None:
    # $50 × 5_000_000 = $250M/day -> "Deep" tier, tradeable.
    sd = {"DEEP": _frame(50.0, 5_000_000)}
    idea = FakeIdea("DEEP", direction="Bullish", conviction_label="High")
    read = T.assess_tradeability(idea, sd, [idea])
    assert read.liquidity_tier == "Deep"
    assert read.verdict == "tradeable"
    assert read.max_size_usd is not None
    # Max size = 10% of $250M ADV.
    assert read.max_size_usd == pytest.approx(25_000_000.0)


def test_assess_crowding_drags_liquid_idea_to_caution() -> None:
    # A deep-liquid name that is also heavily crowded must not stay "tradeable".
    sd = {f"T{i}": _frame(50.0, 5_000_000) for i in range(5)}
    ideas = [FakeIdea(f"T{i}", dominant_driver_key="chokepoint") for i in range(5)]
    read = T.assess_tradeability(ideas[0], sd, ideas)
    assert read.is_crowded is True
    assert read.crowding_count == 5
    assert read.verdict == "caution"


def test_assess_unknown_when_volume_dark() -> None:
    # No volume column -> honest unknown, never a fabricated number.
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    df = pd.DataFrame({"date": dates, "close": [10.0] * 30})
    idea = FakeIdea("ZIM")
    read = T.assess_tradeability(idea, {"ZIM": df}, [idea])
    assert read.verdict == "unknown"
    assert read.liquidity_tier == "unknown"
    assert read.dollar_adv is None
    assert read.max_size_usd is None


def test_assess_neutral_unknown_on_none_idea_and_empty_data() -> None:
    read = T.assess_tradeability(None, {}, [])
    assert read.verdict == "unknown"
    # No idea -> no liquidity read.
    read2 = T.assess_tradeability(FakeIdea("X"), None, None)
    assert read2.verdict == "unknown"
    assert read2.dollar_adv is None


def test_long_idea_has_no_borrow_flag() -> None:
    sd = {"ILLQ": _frame(5.0, 20_000)}
    idea = FakeIdea("ILLQ", direction="Bullish")
    read = T.assess_tradeability(idea, sd, [idea])
    assert read.is_short is False
    assert read.borrow_flag == ""


# ── never-raises on garbage ─────────────────────────────────────────────────

@pytest.mark.parametrize("garbage", [
    {"X": "not a frame"},
    {"X": 12345},
    {"X": pd.DataFrame()},
    {"X": pd.DataFrame({"close": [1, 2]})},  # no volume
    {"X": None},
])
def test_dollar_adv_never_raises_on_garbage(garbage) -> None:
    # Each garbage input must yield None, not an exception.
    assert T.dollar_adv("X", garbage) is None


def test_assess_never_raises_on_garbage_idea() -> None:
    class Weird:
        pass
    read = T.assess_tradeability(Weird(), {"X": "junk"}, [Weird()])
    assert read.verdict in ("unknown", "caution", "untradeable", "tradeable")


def test_crowding_score_never_raises_on_garbage() -> None:
    class Weird:
        pass
    # An object missing .ticker is skipped, not a crash.
    crowd = T.crowding_score([Weird(), FakeIdea("A")])
    assert crowd.get("A") == 1


# ── screen_ideas convenience ────────────────────────────────────────────────

def test_screen_ideas_preserves_order_and_shape() -> None:
    sd = {
        "DEEP": _frame(50.0, 5_000_000),
        "THIN": _frame(10.0, 50_000),
    }
    ideas = [FakeIdea("DEEP"), FakeIdea("THIN")]
    reads = T.screen_ideas(ideas, sd)
    assert [r.ticker for r in reads] == ["DEEP", "THIN"]
    assert reads[0].verdict == "tradeable"
    assert reads[1].verdict in ("caution", "untradeable")


def test_screen_ideas_empty() -> None:
    assert T.screen_ideas([], {}) == []
    assert T.screen_ideas(None, None) == []


# ── integration with the real EquityIdea / score_equity_ideas ───────────────

def test_screen_real_equity_ideas_never_raises() -> None:
    from processing.disruption_cascade import score_equity_ideas

    ideas = score_equity_ideas(None, None, {}, None)
    assert ideas  # COMPANY_COMMODITY_EXPOSURE is always populated
    reads = T.screen_ideas(ideas, {})
    # Empty stock_data -> every read is honest 'unknown' (no real volume).
    assert len(reads) == len(ideas)
    assert all(r.verdict == "unknown" for r in reads)
