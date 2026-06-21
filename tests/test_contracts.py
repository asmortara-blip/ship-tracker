"""Pin R116 data-quality contracts as a PURE-OBSERVABILITY gate.

These tests lock in the two non-negotiable properties of ``data/contracts.py``:

1. ``check_contract`` is a pure, total function — it correctly flags every
   contract violation (missing column, null in a non-null column, non-monotone
   dates, out-of-range value, too-few rows, stale data) and NEVER raises.

2. The wiring helpers (``apply_contract`` / ``observe_contract``) are pure
   observability: they may downgrade the ``DataSource.quality`` label and
   record a violation, but they NEVER touch the DataFrame and NEVER raise.
   An unknown source is a complete no-op; a broken contract / bad frame
   returns the source unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data.contracts import (
    ContractResult,
    FeedContract,
    apply_contract,
    check_contract,
    get_contract,
    get_violation_count,
    observe_contract,
)
from data.quality import DataQuality, DataSource


# ─── Fixture: isolate SQLite per test (violation recording writes kv_state) ──

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Fresh DB per test so violation recording never touches the real DB."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# A representative contract reused across the check_contract cases.
_STOCK_CONTRACT = FeedContract(
    name="Equities",
    required_cols=("date", "symbol", "close", "volume"),
    non_null_cols=("date", "close"),
    date_col="date",
    min_rows=3,
    value_ranges={"close": (0.0, None), "volume": (0.0, None)},
    max_staleness_hours=48.0,
)


def _clean_stock_df(n: int = 5, *, end: datetime | None = None) -> pd.DataFrame:
    """A frame that is clean against BOTH ``_STOCK_CONTRACT`` and the registry
    ``stock`` contract (full OHLCV), so it can drive every test path."""
    end = end or datetime.now(timezone.utc).replace(tzinfo=None)
    dates = pd.date_range(end=end, periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "symbol": ["AAPL"] * n,
        "open": [99.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [98.0 + i for i in range(n)],
        "close": [100.0 + i for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    })


# ─── check_contract: happy path ─────────────────────────────────────────────

def test_check_contract_clean_df_passes() -> None:
    res = check_contract(_clean_stock_df(), _STOCK_CONTRACT)
    assert isinstance(res, ContractResult)
    assert res.passed is True
    assert res.violations == ()


# ─── check_contract: each violation kind ────────────────────────────────────

def test_check_contract_missing_column_violation() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("volume" in v and "missing" in v for v in res.violations)


def test_check_contract_null_in_non_null_col_violation() -> None:
    df = _clean_stock_df()
    df.loc[2, "close"] = None
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("close" in v and "null" in v for v in res.violations)


def test_check_contract_non_monotone_dates_violation() -> None:
    df = _clean_stock_df()
    # Swap two dates so the column is no longer non-decreasing.
    df.loc[0, "date"], df.loc[4, "date"] = df.loc[4, "date"], df.loc[0, "date"]
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("monotone" in v for v in res.violations)


def test_check_contract_out_of_range_value_violation() -> None:
    df = _clean_stock_df()
    df.loc[1, "close"] = -5.0  # below the 0.0 floor
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("close" in v and "below min" in v for v in res.violations)


def test_check_contract_out_of_range_above_max_violation() -> None:
    contract = FeedContract(name="capped", value_ranges={"x": (None, 10.0)})
    df = pd.DataFrame({"x": [1, 2, 99]})
    res = check_contract(df, contract)
    assert res.passed is False
    assert any("above max" in v for v in res.violations)


def test_check_contract_too_few_rows_violation() -> None:
    df = _clean_stock_df(n=2)  # min_rows is 3
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("too few rows" in v for v in res.violations)


def test_check_contract_stale_data_violation() -> None:
    old_end = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    df = _clean_stock_df(end=old_end)
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("stale" in v for v in res.violations)


def test_check_contract_fresh_data_not_stale() -> None:
    df = _clean_stock_df()  # newest row is "today"
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is True


# ─── check_contract: structural / safety properties ─────────────────────────

def test_check_contract_empty_frame_is_exempt_from_row_checks() -> None:
    """An empty frame must not trip min_rows / range / staleness — only the
    column-presence check applies. Feeds return empty on outage by design."""
    df = pd.DataFrame(columns=["date", "symbol", "close", "volume"])
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is True


def test_check_contract_empty_frame_missing_column_still_flags() -> None:
    df = pd.DataFrame(columns=["date", "symbol"])  # close/volume absent
    res = check_contract(df, _STOCK_CONTRACT)
    assert res.passed is False
    assert any("missing" in v for v in res.violations)


def test_check_contract_never_raises_on_garbage_df() -> None:
    """A non-DataFrame input must fail open (pass), not raise."""
    res = check_contract("not a dataframe", _STOCK_CONTRACT)
    assert isinstance(res, ContractResult)
    assert res.passed is True  # fail-open


def test_check_contract_never_raises_on_none_contract() -> None:
    res = check_contract(_clean_stock_df(), None)  # type: ignore[arg-type]
    assert res.passed is True


def test_check_contract_does_not_mutate_frame() -> None:
    df = _clean_stock_df()
    before = df.copy(deep=True)
    check_contract(df, _STOCK_CONTRACT)
    pd.testing.assert_frame_equal(df, before)


# ─── get_contract registry lookups ──────────────────────────────────────────

def test_get_contract_known_source() -> None:
    assert get_contract("comtrade") is not None
    assert get_contract("FRED") is not None  # case-insensitive
    assert get_contract("aishub") is not None


def test_get_contract_alias() -> None:
    # "Yahoo Finance" is the DataSource.name the stock feed stamps.
    assert get_contract("Yahoo Finance") is get_contract("stock")


def test_get_contract_unknown_source_is_none() -> None:
    assert get_contract("totally_unknown_feed") is None
    assert get_contract(None) is None
    assert get_contract("") is None


# ─── apply_contract: downgrade label, never touch the frame ─────────────────

def test_apply_contract_downgrades_quality_on_failure() -> None:
    df = _clean_stock_df().drop(columns=["volume"])  # schema break
    source = DataSource(name="Yahoo Finance", quality=DataQuality.UNOFFICIAL)
    out = apply_contract(df, source)
    assert out is not source  # a copy
    assert out.quality == DataQuality.DEMO
    assert out.name == source.name  # other fields preserved
    assert "contract:" in out.notes


def test_apply_contract_returns_df_untouched() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    before = df.copy(deep=True)
    source = DataSource(name="Yahoo Finance", quality=DataQuality.UNOFFICIAL)
    apply_contract(df, source)
    # The frame object is never mutated.
    pd.testing.assert_frame_equal(df, before)


def test_apply_contract_passing_returns_source_unchanged() -> None:
    df = _clean_stock_df()
    source = DataSource(name="Yahoo Finance", quality=DataQuality.UNOFFICIAL)
    out = apply_contract(df, source)
    assert out is source  # no copy, no downgrade


def test_apply_contract_unknown_source_is_noop() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    source = DataSource(name="some_unregistered_feed", quality=DataQuality.GOOD)
    out = apply_contract(df, source)
    assert out is source
    assert out.quality == DataQuality.GOOD


def test_apply_contract_none_source_is_noop() -> None:
    assert apply_contract(_clean_stock_df(), None) is None


def test_apply_contract_broken_df_returns_source_unchanged() -> None:
    """A bad frame must not raise; the source is returned unchanged."""
    source = DataSource(name="Yahoo Finance", quality=DataQuality.GOOD)
    out = apply_contract(object(), source)  # not a DataFrame at all
    assert out is source


# ─── observe_contract: frame-only hook used by the normalizer ───────────────

def test_observe_contract_records_violation() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    res = observe_contract(df, "stock")
    assert res.passed is False
    assert get_violation_count("stock") == 1


def test_observe_contract_clean_records_nothing() -> None:
    res = observe_contract(_clean_stock_df(), "stock")
    assert res.passed is True
    assert get_violation_count("stock") == 0


def test_observe_contract_unknown_source_is_noop() -> None:
    res = observe_contract(_clean_stock_df().drop(columns=["volume"]),
                           "totally_unknown_feed")
    assert res.passed is True
    assert res.violations == ()
    assert get_violation_count("totally_unknown_feed") == 0


def test_observe_contract_increments_counter() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    observe_contract(df, "stock")
    observe_contract(df, "stock")
    assert get_violation_count("stock") == 2


def test_observe_contract_never_raises_on_garbage() -> None:
    res = observe_contract(object(), "stock")  # bad frame, real source
    assert isinstance(res, ContractResult)
    assert res.passed is True  # fail-open


def test_observe_contract_does_not_mutate_frame() -> None:
    df = _clean_stock_df().drop(columns=["volume"])
    before = df.copy(deep=True)
    observe_contract(df, "stock")
    pd.testing.assert_frame_equal(df, before)


# ─── A broken FeedContract must not break check_contract ────────────────────

def test_check_contract_survives_broken_value_ranges() -> None:
    """A malformed value_ranges entry must be skipped, not raise."""
    bad = FeedContract(name="bad", value_ranges={"close": "not_a_tuple"})  # type: ignore[dict-item]
    res = check_contract(_clean_stock_df(), bad)
    assert isinstance(res, ContractResult)
    assert res.passed is True  # the broken range is silently skipped
