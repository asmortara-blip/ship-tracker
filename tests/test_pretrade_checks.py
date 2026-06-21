"""Tests for processing/pretrade_checks.py (R108).

run_pretrade vets a proposed order against the book + universe:
  * off-universe ticker → BLOCK
  * single-name cap breach (precise post-trade projection on a hand-built book)
    → BLOCK; near-cap → WARN
  * sector cap breach → BLOCK
  * ADV participation → WARN when volume present; SKIPPED (PASS) when absent
  * sanctions PLACEHOLDER set → BLOCK
  * clean in-universe small order → all PASS, blocked=False
  * never raises on garbage input.
"""

from __future__ import annotations

import pandas as pd

from processing.order_blotter import Order
from processing.pretrade_checks import (
    CheckResult,
    PretradeVerdict,
    run_pretrade,
    screen_blotter,
    covered_universe,
)


def _frame(close: float, volume: float, n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n),
            "close": [close] * n,
            "volume": [volume] * n,
        }
    )


def _result(verdict: PretradeVerdict, name: str) -> CheckResult:
    return next(r for r in verdict.results if r.name == name)


# Hand-built book + prices, reused across the concentration tests.
# ZIM (Container) @ $100 x 100 = $10,000
# SBLK (Dry Bulk) @ $50 x 100 = $5,000
# Total priced MV = $15,000
def _book():
    return [
        {"ticker": "ZIM", "shares": 100, "avg_cost": 90},
        {"ticker": "SBLK", "shares": 100, "avg_cost": 40},
    ]


def _stock_data():
    return {
        "ZIM": _frame(100.0, 1_000.0),
        "SBLK": _frame(50.0, 2_000.0),
    }


# ---------------------------------------------------------------------------
# Universe / restricted
# ---------------------------------------------------------------------------

def test_off_universe_ticker_blocks():
    order = Order("AAPL", "BUY", quantity=1, notional=1_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    assert v.blocked is True
    assert _result(v, "universe").status == "BLOCK"


def test_restricted_list_blocks_even_in_universe():
    order = Order("ZIM", "BUY", quantity=1, notional=1_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data(), restricted={"ZIM"})
    assert v.blocked is True
    assert _result(v, "universe").status == "BLOCK"
    assert "restricted" in _result(v, "universe").detail.lower()


def test_covered_universe_contains_tracked_names():
    u = covered_universe()
    assert "ZIM" in u and "SBLK" in u
    assert "AAPL" not in u


# ---------------------------------------------------------------------------
# Single-name concentration (precise projection)
# ---------------------------------------------------------------------------

def test_single_name_cap_block_precise_projection():
    # BUY $5,000 ZIM: ZIM 10k→15k, total 15k→20k → 75% > 15% cap → BLOCK.
    order = Order("ZIM", "BUY", quantity=50, notional=5_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    sn = _result(v, "single_name")
    assert sn.status == "BLOCK"
    assert "75.0%" in sn.detail
    assert v.blocked is True


def test_single_name_cap_warn_near_threshold():
    # Build a book where the post-trade weight lands in the WARN band.
    # ZIM 1 sh @ $100 = $100; book also holds $10,000 of SBLK → total $10,100.
    # BUY ZIM notional X so ZIM/(total+X) ∈ [0.9*cap, cap] with cap=0.50.
    # Target ~46% → solve: (100+X)/(10100+X)=0.46 → X≈8470.
    book = [
        {"ticker": "ZIM", "shares": 1, "avg_cost": 90},
        {"ticker": "SBLK", "shares": 200, "avg_cost": 40},
    ]
    order = Order("ZIM", "BUY", quantity=84, notional=8_470)
    # Generous sector cap so ONLY the single-name check engages near its cap.
    v = run_pretrade(
        order, book, stock_data=_stock_data(), single_name_cap=0.50, sector_cap=0.95
    )
    sn = _result(v, "single_name")
    assert sn.status == "WARN"
    assert v.blocked is False


def test_sell_reducing_exposure_passes_single_name():
    # A SELL that reduces (does not increase) the name's weight is risk-reducing
    # and must never BLOCK on single-name, even if the name stays concentrated.
    order = Order("ZIM", "SELL", quantity=50, notional=5_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    assert _result(v, "single_name").status == "PASS"


def test_unpriced_book_warns_not_crashes():
    # No stock_data → book unpriced → projection checks WARN, not BLOCK/crash.
    order = Order("ZIM", "BUY", quantity=1, notional=1_000)
    v = run_pretrade(order, _book(), stock_data={})
    assert _result(v, "single_name").status == "WARN"
    assert _result(v, "sector").status == "WARN"
    assert v.blocked is False


# ---------------------------------------------------------------------------
# Sector concentration
# ---------------------------------------------------------------------------

def test_sector_cap_block():
    # Two container names already, then a big container BUY pushes the sector
    # weight over the cap. ZIM + MATX are both Container-family sectors but the
    # sector LABELS differ ("Container" vs "Container/Jones Act"), so to test
    # sector aggregation cleanly use a single-sector breach via ZIM alone with a
    # low sector_cap.
    order = Order("ZIM", "BUY", quantity=50, notional=5_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data(), sector_cap=0.30)
    sec = _result(v, "sector")
    # ZIM (Container) post-trade is 75% of the book → its sector ≥ 75% > 30%.
    assert sec.status == "BLOCK"
    assert v.blocked is True


def test_sector_cap_pass_when_diversified():
    # Small ZIM buy keeps Container sector modest under a generous cap.
    order = Order("ZIM", "BUY", quantity=1, notional=100)
    v = run_pretrade(order, _book(), stock_data=_stock_data(), sector_cap=0.80)
    assert _result(v, "sector").status == "PASS"


# ---------------------------------------------------------------------------
# ADV participation
# ---------------------------------------------------------------------------

def test_adv_warn_when_volume_present():
    # ZIM ADV = 1,000 sh/day; order 200 sh = 20% > 10% cap → WARN.
    order = Order("ZIM", "BUY", quantity=200, notional=20_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    adv = _result(v, "adv")
    assert adv.status == "WARN"
    assert "20.0%" in adv.detail


def test_adv_pass_when_small():
    # 50 sh / 1,000 ADV = 5% < 10% → PASS.
    order = Order("ZIM", "BUY", quantity=50, notional=5_000)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    assert _result(v, "adv").status == "PASS"


def test_adv_skipped_when_volume_absent():
    # stock_data with no volume column → ADV check SKIPPED (PASS, documented).
    sd = {
        "ZIM": pd.DataFrame(
            {"date": pd.date_range("2024-01-01", periods=3), "close": [100.0] * 3}
        ),
        "SBLK": _frame(50.0, 2_000.0),
    }
    order = Order("ZIM", "BUY", quantity=100_000, notional=10_000_000)
    v = run_pretrade(order, _book(), stock_data=sd, single_name_cap=1.0, sector_cap=1.0)
    adv = _result(v, "adv")
    assert adv.status == "PASS"
    assert "skipped" in adv.detail.lower()


# ---------------------------------------------------------------------------
# Sanctions placeholder
# ---------------------------------------------------------------------------

def test_sanctions_placeholder_blocks():
    order = Order("ZIM", "BUY", quantity=1, notional=100)
    v = run_pretrade(order, _book(), stock_data=_stock_data(), sanctions={"ZIM"})
    san = _result(v, "sanctions")
    assert san.status == "BLOCK"
    assert "placeholder" in san.detail.lower()
    assert v.blocked is True


def test_sanctions_default_empty_passes():
    # Default sanctions set is the EMPTY placeholder → never blocks by default.
    order = Order("ZIM", "BUY", quantity=1, notional=100)
    v = run_pretrade(order, _book(), stock_data=_stock_data())
    assert _result(v, "sanctions").status == "PASS"


# ---------------------------------------------------------------------------
# Clean order + aggregation + robustness
# ---------------------------------------------------------------------------

def test_clean_small_in_universe_order_all_pass():
    # Small ZIM buy: in universe, well under single-name & sector caps, under
    # ADV, not sanctioned → every check PASS, blocked False, no warnings.
    order = Order("ZIM", "BUY", quantity=10, notional=1_000)
    v = run_pretrade(
        order,
        _book(),
        stock_data=_stock_data(),
        single_name_cap=0.90,
        sector_cap=0.95,
    )
    statuses = {r.name: r.status for r in v.results}
    assert all(s == "PASS" for s in statuses.values()), statuses
    assert v.blocked is False
    assert v.warnings == 0


def test_verdict_warnings_count():
    # An ADV breach alone → 1 warning, not blocked. Caps set so the ~85.7%
    # projected single-name/sector weight stays clear of the soft WARN band
    # (0.99 * 0.90 = 0.891 > 0.857) and only ADV warns.
    order = Order("ZIM", "BUY", quantity=200, notional=20_000)
    v = run_pretrade(
        order, _book(), stock_data=_stock_data(),
        single_name_cap=0.99, sector_cap=0.99,
    )
    assert v.blocked is False
    assert v.warnings == 1


def test_screen_blotter_runs_each_order():
    orders = [
        Order("ZIM", "BUY", quantity=10, notional=1_000),
        Order("AAPL", "BUY", quantity=10, notional=1_000),
    ]
    verdicts = screen_blotter(
        orders, _book(), stock_data=_stock_data(), single_name_cap=0.95, sector_cap=0.99
    )
    assert len(verdicts) == 2
    assert verdicts[0].blocked is False        # ZIM clean
    assert verdicts[1].blocked is True         # AAPL off-universe


def test_never_raises_on_garbage():
    # Garbage order object, None book, None stock_data → still a verdict.
    v = run_pretrade(object(), None, stock_data=None)
    assert isinstance(v, PretradeVerdict)
    # An order with no ticker → universe BLOCK, no crash.
    assert v.blocked is True
    # Totally empty order
    v2 = run_pretrade(Order("", "BUY"), [], stock_data={})
    assert isinstance(v2, PretradeVerdict)


def test_deterministic():
    order = Order("ZIM", "BUY", quantity=50, notional=5_000)
    a = run_pretrade(order, _book(), stock_data=_stock_data())
    b = run_pretrade(order, _book(), stock_data=_stock_data())
    assert [(r.name, r.status) for r in a.results] == [
        (r.name, r.status) for r in b.results
    ]
    assert a.blocked == b.blocked and a.warnings == b.warnings


# ---------------------------------------------------------------------------
# R108 review fixes
# ---------------------------------------------------------------------------
def test_oversell_sell_keeps_book_total_consistent():
    """An oversell SELL removes only the name's REAL mv from the book total,
    not the full notional — otherwise other names project to a >100% weight off
    an understated denominator (R108 review)."""
    from processing.pretrade_checks import _project_post_trade

    # ZIM held = $10k; SELL $12k notional (oversell). Book total = $15k.
    order = Order("ZIM", "SELL", quantity=120, notional=12_000)
    post, post_total, priced = _project_post_trade(order, _book(), _stock_data())
    assert post["ZIM"] == 0.0                       # name floors at 0
    assert abs(post_total - 5_000.0) < 1e-6         # remaining = SBLK's $5k, not $3k
    assert post["SBLK"] / post_total <= 1.0 + 1e-9  # other-name weight stays sane


def test_buy_into_single_name_book_at_cap_blocks():
    """A BUY into a name that is already 100% of the book stays at 100% (flat in
    weight) but is NOT risk-reducing → it must BLOCK, not pass as 'flat'
    (R108 review)."""
    book = [{"ticker": "ZIM", "shares": 100, "avg_cost": 90}]
    stock = {"ZIM": _frame(100.0, 1_000.0)}
    order = Order("ZIM", "BUY", quantity=10, notional=1_000)
    v = run_pretrade(order, book, stock_data=stock)
    sn = _result(v, "single_name")
    assert sn.status == "BLOCK"
    assert v.blocked is True
