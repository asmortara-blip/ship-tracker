"""Pin defining properties of data.normalizer's six normalizers.

Every normalizer is a pure DataFrame-shaping utility: take a raw upstream
frame, return a standardised frame whose columns match a published schema
constant. The properties below lock in the contract that downstream code
relies on, regardless of which upstream feed produced the input.

Branches covered
----------------
normalize_trade_df
    * empty input  → empty schema-only frame
    * None input   → empty schema-only frame
    * "period" path: YYYYMM → datetime parsing
    * "refPeriodId" fallback path
    * neither period nor refPeriodId → all NaT → all rows dropped
    * flow encoded as a Series (str → upper → first char)
    * value_usd / net_weight_kg numeric coercion with NaN → 0 fill
    * malformed period strings → NaT row dropped
    * exception path (broken column wiring) → empty schema-only frame
    * idempotency: normalising the output of a normaliser fails by design
      (column shape changes), so we instead pin column order

normalize_freight_df
    * empty / None input → empty schema-only frame
    * uses DataFrame "date" column when present
    * route_id and index_name arguments flow through as scalar columns
    * rate_usd_per_feu fallback chain (rate_usd_per_feu → value → close)
    * rows with rate <= 0 are dropped
    * rows with un-parseable date are dropped
    * result is sorted by date
    * idempotent: normalise(normalise(df)) == normalise(df)

normalize_ais_df
    * empty / None input → empty schema-only frame
    * uses "date" column then falls back to "timestamp"
    * vessel_count fallback chain (vessel_count → count)
    * default vessel_type == "cargo"
    * source is always overwritten to "aishub"

normalize_macro_df
    * empty / None input → empty schema-only frame
    * Series input is converted to a two-column frame
    * NaN values are dropped
    * series_id / series_name arguments flow through as scalar columns

normalize_stock_df
    * empty / None input → empty schema-only frame
    * tz-aware DatetimeIndex is stripped of timezone
    * yfinance-shaped frames (DatetimeIndex + capitalized OHLC) round-trip
      with the expected row count — the index-misalignment bug was fixed
      by building `out` in one shot via pd.DataFrame({...}) with
      reset_index() on the underlying Series
    * lowercase-column path produces the same correct output
    * symbol argument flows through as a scalar column

normalize_throughput_df
    * empty / None input → empty schema-only frame
    * happy path with all six expected columns present
    * rows with year <= 0 are filtered out
    * when "connectivity_index" is absent the fallback uses a same-length
      Series of zeros so the resulting column is all-0 (not an exception)
    * source is always overwritten to "worldbank"

_empty
    * returns an empty DataFrame whose columns match the schema list
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.normalizer import (
    AIS_COLS,
    FREIGHT_COLS,
    MACRO_COLS,
    STOCK_COLS,
    THROUGHPUT_COLS,
    TRADE_COLS,
    _empty,
    normalize_ais_df,
    normalize_freight_df,
    normalize_macro_df,
    normalize_stock_df,
    normalize_throughput_df,
    normalize_trade_df,
)


# ─── _empty helper ─────────────────────────────────────────────────────────


def test_empty_returns_schema_only_frame() -> None:
    """_empty pins both column order and zero-row shape."""
    out = _empty(TRADE_COLS)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == TRADE_COLS
    assert len(out) == 0


def test_empty_works_for_every_schema_constant() -> None:
    """All six schema constants are usable as _empty inputs."""
    for schema in (TRADE_COLS, FREIGHT_COLS, AIS_COLS, MACRO_COLS,
                   STOCK_COLS, THROUGHPUT_COLS):
        out = _empty(schema)
        assert list(out.columns) == schema
        assert len(out) == 0


# ─── Schema constants ──────────────────────────────────────────────────────


def test_schema_constants_are_lists_of_strings() -> None:
    """Schemas are list[str] — downstream code does `df[TRADE_COLS]`."""
    for schema in (TRADE_COLS, FREIGHT_COLS, AIS_COLS, MACRO_COLS,
                   STOCK_COLS, THROUGHPUT_COLS):
        assert isinstance(schema, list)
        assert all(isinstance(c, str) for c in schema)
        # No duplicate column names within a schema.
        assert len(schema) == len(set(schema))


def test_trade_schema_includes_required_business_keys() -> None:
    """Pin the trade contract: date, hs_code, flow, value_usd."""
    for required in ("date", "hs_code", "flow", "value_usd", "source"):
        assert required in TRADE_COLS


# ─── normalize_trade_df ────────────────────────────────────────────────────


def test_normalize_trade_df_returns_empty_on_none() -> None:
    """None input → empty schema-only frame."""
    out = normalize_trade_df(None)
    assert list(out.columns) == TRADE_COLS
    assert len(out) == 0


def test_normalize_trade_df_returns_empty_on_empty_df() -> None:
    """Empty input → empty schema-only frame."""
    out = normalize_trade_df(pd.DataFrame())
    assert list(out.columns) == TRADE_COLS
    assert len(out) == 0


def test_normalize_trade_df_parses_period_as_yyyymm() -> None:
    """Comtrade's YYYYMM integer period becomes a real datetime."""
    df = pd.DataFrame({
        "period": ["202401", "202402", "202403"],
        "reporterISO": ["USA", "USA", "USA"],
        "cmdCode": ["8542", "8542", "8542"],
        "flowCode": ["X", "X", "X"],
        "primaryValue": [100.0, 200.0, 300.0],
        "netWgt": [10.0, 20.0, 30.0],
    })
    out = normalize_trade_df(df)
    assert len(out) == 3
    assert pd.api.types.is_datetime64_any_dtype(out["date"])
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")
    assert out["date"].iloc[-1] == pd.Timestamp("2024-03-01")


def test_normalize_trade_df_falls_back_to_refPeriodId() -> None:
    """When `period` is absent, `refPeriodId` is parsed instead."""
    df = pd.DataFrame({
        "refPeriodId": ["202401"],
        "reporterISO": ["CHN"],
        "cmdCode": ["8542"],
        "flowCode": ["M"],
        "primaryValue": [500.0],
        "netWgt": [50.0],
    })
    out = normalize_trade_df(df)
    assert len(out) == 1
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")


def test_normalize_trade_df_drops_rows_with_no_period_columns() -> None:
    """Neither `period` nor `refPeriodId` → date is all NaT → all rows dropped."""
    df = pd.DataFrame({
        "reporterISO": ["USA"],
        "cmdCode": ["8542"],
        "flowCode": ["X"],
        "primaryValue": [100.0],
        "netWgt": [10.0],
    })
    out = normalize_trade_df(df)
    assert len(out) == 0
    assert list(out.columns) == TRADE_COLS


def test_normalize_trade_df_emits_canonical_columns() -> None:
    """The output columns match TRADE_COLS exactly, in order."""
    df = pd.DataFrame({
        "period": ["202401"],
        "reporterISO": ["USA"],
        "cmdCode": ["8542"],
        "flowCode": ["X"],
        "primaryValue": [100.0],
        "netWgt": [10.0],
    })
    out = normalize_trade_df(df)
    assert list(out.columns) == TRADE_COLS


def test_normalize_trade_df_truncates_hs_code_to_four_chars() -> None:
    """HS code is sliced to 4 characters — the chapter-level granularity."""
    df = pd.DataFrame({
        "period": ["202401"],
        "reporterISO": ["USA"],
        "cmdCode": ["854212345"],
        "flowCode": ["X"],
        "primaryValue": [100.0],
        "netWgt": [10.0],
    })
    out = normalize_trade_df(df)
    assert out["hs_code"].iloc[0] == "8542"


def test_normalize_trade_df_uppercases_flow_to_one_char() -> None:
    """flowCode 'export' → 'E', 'import' → 'I' — single-char direction."""
    df = pd.DataFrame({
        "period": ["202401", "202402"],
        "reporterISO": ["USA", "USA"],
        "cmdCode": ["8542", "8542"],
        "flowCode": ["export", "import"],
        "primaryValue": [100.0, 200.0],
        "netWgt": [10.0, 20.0],
    })
    out = normalize_trade_df(df)
    assert out["flow"].iloc[0] == "E"
    assert out["flow"].iloc[1] == "I"


def test_normalize_trade_df_coerces_non_numeric_value_to_zero() -> None:
    """Junk strings in primaryValue / netWgt become 0, not NaN."""
    df = pd.DataFrame({
        "period": ["202401", "202402"],
        "reporterISO": ["USA", "USA"],
        "cmdCode": ["8542", "8542"],
        "flowCode": ["X", "X"],
        "primaryValue": ["not_a_number", 200.0],
        "netWgt": ["junk", 20.0],
    })
    out = normalize_trade_df(df)
    assert out["value_usd"].iloc[0] == pytest.approx(0.0)
    assert out["value_usd"].iloc[1] == pytest.approx(200.0)
    assert out["net_weight_kg"].iloc[0] == pytest.approx(0.0)
    assert out["net_weight_kg"].iloc[1] == pytest.approx(20.0)


def test_normalize_trade_df_drops_unparseable_periods() -> None:
    """Malformed YYYYMM → NaT → row dropped."""
    df = pd.DataFrame({
        "period": ["202401", "BOGUS", "202403"],
        "reporterISO": ["USA", "USA", "USA"],
        "cmdCode": ["8542", "8542", "8542"],
        "flowCode": ["X", "X", "X"],
        "primaryValue": [100.0, 200.0, 300.0],
        "netWgt": [10.0, 20.0, 30.0],
    })
    out = normalize_trade_df(df)
    assert len(out) == 2
    assert "BOGUS" not in out["date"].astype(str).tolist()


def test_normalize_trade_df_is_sorted_by_date() -> None:
    """Output rows are in ascending date order regardless of input order."""
    df = pd.DataFrame({
        "period": ["202403", "202401", "202402"],
        "reporterISO": ["USA", "USA", "USA"],
        "cmdCode": ["8542", "8542", "8542"],
        "flowCode": ["X", "X", "X"],
        "primaryValue": [3.0, 1.0, 2.0],
        "netWgt": [3.0, 1.0, 2.0],
    })
    out = normalize_trade_df(df)
    assert out["date"].is_monotonic_increasing
    assert out["value_usd"].tolist() == [1.0, 2.0, 3.0]


def test_normalize_trade_df_marks_source_as_comtrade() -> None:
    """source is hard-coded to 'comtrade' for traceability."""
    df = pd.DataFrame({
        "period": ["202401"],
        "reporterISO": ["USA"],
        "cmdCode": ["8542"],
        "flowCode": ["X"],
        "primaryValue": [100.0],
        "netWgt": [10.0],
    })
    out = normalize_trade_df(df)
    assert out["source"].iloc[0] == "comtrade"


def test_normalize_trade_df_handles_missing_cmd_columns() -> None:
    """When BOTH cmdCode and cmdDesc are absent, the fallback uses a
    same-length Series of empty strings — the function now produces a
    properly-shaped row with hs_code='' instead of returning empty via
    the broad except path (previously raised AttributeError on .astype).
    """
    df = pd.DataFrame({"period": ["202401"]})  # no cmdCode, no cmdDesc
    out = normalize_trade_df(df)
    assert list(out.columns) == TRADE_COLS
    assert len(out) == 1
    assert out["hs_code"].iloc[0] == ""
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")


# ─── normalize_freight_df ──────────────────────────────────────────────────


def test_normalize_freight_df_returns_empty_on_none() -> None:
    out = normalize_freight_df(None)
    assert list(out.columns) == FREIGHT_COLS
    assert len(out) == 0


def test_normalize_freight_df_returns_empty_on_empty_df() -> None:
    out = normalize_freight_df(pd.DataFrame())
    assert list(out.columns) == FREIGHT_COLS
    assert len(out) == 0


def test_normalize_freight_df_uses_explicit_date_column() -> None:
    """An explicit `date` column drives the parsing."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
        "rate_usd_per_feu": [2000.0, 2100.0, 2200.0],
    })
    out = normalize_freight_df(df, route_id="r1", index_name="FBX")
    assert len(out) == 3
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")
    assert out["rate_usd_per_feu"].iloc[2] == pytest.approx(2200.0)


def test_normalize_freight_df_threads_route_id_and_index_name() -> None:
    """The scalar kwargs land as columns of the output frame."""
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "rate_usd_per_feu": [2000.0],
    })
    out = normalize_freight_df(df, route_id="transpacific_eb", index_name="WCI")
    assert (out["route_id"] == "transpacific_eb").all()
    assert (out["index_name"] == "WCI").all()


def test_normalize_freight_df_falls_back_to_value_column() -> None:
    """When `rate_usd_per_feu` is absent, `value` becomes the rate."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02"],
        "value": [1500.0, 1600.0],
    })
    out = normalize_freight_df(df)
    assert out["rate_usd_per_feu"].tolist() == [1500.0, 1600.0]


def test_normalize_freight_df_falls_back_to_close_column() -> None:
    """When neither `rate_usd_per_feu` nor `value` is present, `close` works."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02"],
        "close": [1700.0, 1800.0],
    })
    out = normalize_freight_df(df)
    assert out["rate_usd_per_feu"].tolist() == [1700.0, 1800.0]


def test_normalize_freight_df_drops_non_positive_rates() -> None:
    """Rates of zero or below are dropped — a zero rate is a feed bug."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        "rate_usd_per_feu": [0.0, 1500.0, -100.0, 2000.0],
    })
    out = normalize_freight_df(df)
    assert len(out) == 2
    assert (out["rate_usd_per_feu"] > 0).all()


def test_normalize_freight_df_drops_unparseable_dates() -> None:
    """Unparseable date strings → NaT → row dropped."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "garbage", "2024-01-03"],
        "rate_usd_per_feu": [100.0, 200.0, 300.0],
    })
    out = normalize_freight_df(df)
    assert len(out) == 2
    assert out["rate_usd_per_feu"].tolist() == [100.0, 300.0]


def test_normalize_freight_df_is_sorted_by_date() -> None:
    df = pd.DataFrame({
        "date": ["2024-03-01", "2024-01-01", "2024-02-01"],
        "rate_usd_per_feu": [300.0, 100.0, 200.0],
    })
    out = normalize_freight_df(df)
    assert out["date"].is_monotonic_increasing
    assert out["rate_usd_per_feu"].tolist() == [100.0, 200.0, 300.0]


def test_normalize_freight_df_emits_canonical_columns() -> None:
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "rate_usd_per_feu": [100.0],
    })
    out = normalize_freight_df(df)
    assert list(out.columns) == FREIGHT_COLS


def test_normalize_freight_df_is_idempotent() -> None:
    """normalize(normalize(df)) == normalize(df) — a fully-shaped frame
    is preserved on a second pass.
    """
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=5),
        "route_id": ["r1"] * 5,
        "rate_usd_per_feu": [100.0, 200.0, 300.0, 400.0, 500.0],
        "index_name": ["FBX"] * 5,
        "source": ["src"] * 5,
    })
    once = normalize_freight_df(df)
    twice = normalize_freight_df(once)
    pd.testing.assert_frame_equal(once, twice)


# ─── normalize_ais_df ──────────────────────────────────────────────────────


def test_normalize_ais_df_returns_empty_on_none() -> None:
    out = normalize_ais_df(None)
    assert list(out.columns) == AIS_COLS
    assert len(out) == 0


def test_normalize_ais_df_returns_empty_on_empty_df() -> None:
    out = normalize_ais_df(pd.DataFrame())
    assert list(out.columns) == AIS_COLS
    assert len(out) == 0


def test_normalize_ais_df_uses_explicit_date_column() -> None:
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02"],
        "port_locode": ["USLAX", "CNSHA"],
        "vessel_count": [5, 7],
    })
    out = normalize_ais_df(df)
    assert len(out) == 2
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")


def test_normalize_ais_df_falls_back_to_timestamp_column() -> None:
    """`timestamp` column is honored when `date` is missing."""
    df = pd.DataFrame({
        "timestamp": ["2024-01-01"],
        "port_locode": ["USLAX"],
        "vessel_count": [5],
    })
    out = normalize_ais_df(df)
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")


def test_normalize_ais_df_falls_back_to_count_column() -> None:
    """`count` column becomes vessel_count when the canonical name is absent."""
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "port_locode": ["USLAX"],
        "count": [12],
    })
    out = normalize_ais_df(df)
    assert out["vessel_count"].iloc[0] == 12


def test_normalize_ais_df_defaults_vessel_type_to_cargo() -> None:
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "port_locode": ["USLAX"],
        "vessel_count": [5],
    })
    out = normalize_ais_df(df)
    assert (out["vessel_type"] == "cargo").all()


def test_normalize_ais_df_overrides_source_to_aishub() -> None:
    """The source column is forced to 'aishub' even if the caller set one."""
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "port_locode": ["USLAX"],
        "vessel_count": [5],
        "source": ["custom_source"],
    })
    out = normalize_ais_df(df)
    assert (out["source"] == "aishub").all()


def test_normalize_ais_df_is_sorted_by_date() -> None:
    df = pd.DataFrame({
        "date": ["2024-03-01", "2024-01-01", "2024-02-01"],
        "port_locode": ["A", "B", "C"],
        "vessel_count": [3, 1, 2],
    })
    out = normalize_ais_df(df)
    assert out["date"].is_monotonic_increasing
    assert out["vessel_count"].tolist() == [1, 2, 3]


def test_normalize_ais_df_emits_canonical_columns() -> None:
    df = pd.DataFrame({
        "date": ["2024-01-01"],
        "port_locode": ["USLAX"],
        "vessel_count": [5],
    })
    out = normalize_ais_df(df)
    assert list(out.columns) == AIS_COLS


# ─── normalize_macro_df ────────────────────────────────────────────────────


def test_normalize_macro_df_returns_empty_on_none() -> None:
    out = normalize_macro_df(None)
    assert list(out.columns) == MACRO_COLS
    assert len(out) == 0


def test_normalize_macro_df_returns_empty_on_empty_df() -> None:
    out = normalize_macro_df(pd.DataFrame())
    assert list(out.columns) == MACRO_COLS
    assert len(out) == 0


def test_normalize_macro_df_accepts_pandas_series() -> None:
    """FRED-style Series (DatetimeIndex → values) is rotated to a frame."""
    s = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.date_range("2024-01-01", periods=3),
        name="GDP",
    )
    out = normalize_macro_df(s, series_id="GDP", series_name="Gross Dom. Product")
    assert len(out) == 3
    assert out["value"].tolist() == [1.0, 2.0, 3.0]
    assert (out["series_id"] == "GDP").all()
    assert (out["series_name"] == "Gross Dom. Product").all()


def test_normalize_macro_df_accepts_dataframe_with_explicit_columns() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=3),
        "value": [1.0, 2.0, 3.0],
    })
    out = normalize_macro_df(df, series_id="X")
    assert len(out) == 3
    assert pd.api.types.is_datetime64_any_dtype(out["date"])


def test_normalize_macro_df_drops_rows_with_nan_value() -> None:
    """NaN values are dropped — FRED publishes NaN for missing periods."""
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=4),
        "value": [1.0, np.nan, 3.0, 4.0],
    })
    out = normalize_macro_df(df)
    assert len(out) == 3
    assert not out["value"].isna().any()


def test_normalize_macro_df_threads_series_id_and_name() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=2),
        "value": [1.0, 2.0],
    })
    out = normalize_macro_df(df, series_id="BSXRLM", series_name="BDI")
    assert (out["series_id"] == "BSXRLM").all()
    assert (out["series_name"] == "BDI").all()


def test_normalize_macro_df_default_source_is_fred() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=1),
        "value": [1.0],
    })
    out = normalize_macro_df(df)
    assert (out["source"] == "fred").all()


def test_normalize_macro_df_respects_explicit_source() -> None:
    """Unlike AIS, macro keeps the input source if one is provided."""
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=1),
        "value": [1.0],
        "source": ["worldbank"],
    })
    out = normalize_macro_df(df)
    assert (out["source"] == "worldbank").all()


def test_normalize_macro_df_is_sorted_by_date() -> None:
    df = pd.DataFrame({
        "date": ["2024-03-01", "2024-01-01", "2024-02-01"],
        "value": [3.0, 1.0, 2.0],
    })
    out = normalize_macro_df(df)
    assert out["date"].is_monotonic_increasing
    assert out["value"].tolist() == [1.0, 2.0, 3.0]


def test_normalize_macro_df_emits_canonical_columns() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=1),
        "value": [1.0],
    })
    out = normalize_macro_df(df)
    assert list(out.columns) == MACRO_COLS


# ─── normalize_stock_df ────────────────────────────────────────────────────


def test_normalize_stock_df_returns_empty_on_none() -> None:
    out = normalize_stock_df(None)
    assert list(out.columns) == STOCK_COLS
    assert len(out) == 0


def test_normalize_stock_df_returns_empty_on_empty_df() -> None:
    out = normalize_stock_df(pd.DataFrame())
    assert list(out.columns) == STOCK_COLS
    assert len(out) == 0


def test_normalize_stock_df_strips_timezone() -> None:
    """A tz-aware DatetimeIndex is converted to tz-naive."""
    idx = pd.date_range("2024-01-01", periods=3, tz="US/Eastern")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [102.0, 103.0, 104.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [101.0, 102.0, 103.0],
            "Volume": [1000, 2000, 3000],
        },
        index=idx,
    )
    out = normalize_stock_df(df, symbol="AAPL")
    assert "date" in out.columns
    assert len(out) == 3
    assert out["date"].dt.tz is None


def test_normalize_stock_df_emits_canonical_columns() -> None:
    """Even on the empty-result bug path, the schema is preserved."""
    idx = pd.date_range("2024-01-01", periods=2)
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 2000],
        },
        index=idx,
    )
    out = normalize_stock_df(df, symbol="AAPL")
    assert list(out.columns) == STOCK_COLS


def test_normalize_stock_df_yfinance_shape_roundtrips_with_correct_row_count() -> None:
    """yfinance returns a DataFrame with a DatetimeIndex and capitalized
    OHLC columns. The previous implementation built `out` column-by-column
    starting from the DatetimeIndex, then pandas reindexed every subsequent
    OHLC Series to the resulting RangeIndex, producing all-NaN that
    dropna(subset=['close']) stripped entirely (zero rows out).

    The fix builds the frame in one shot via pd.DataFrame({...}) with
    reset_index() on each Series so every column lands on the same index.
    """
    idx = pd.date_range("2024-01-01", periods=5)
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "High": [102.0, 103.0, 104.0, 105.0, 106.0],
            "Low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "Close": [101.0, 102.0, 103.0, 104.0, 105.0],
            "Volume": [1000, 2000, 3000, 4000, 5000],
        },
        index=idx,
    )
    out = normalize_stock_df(df, symbol="AAPL")
    assert len(out) == 5
    assert list(out.columns) == STOCK_COLS
    assert out["close"].tolist() == [101.0, 102.0, 103.0, 104.0, 105.0]
    assert out["open"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert out["volume"].tolist() == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert (out["symbol"] == "AAPL").all()
    assert out["date"].iloc[0] == pd.Timestamp("2024-01-01")
    assert out["date"].iloc[-1] == pd.Timestamp("2024-01-05")


def test_normalize_stock_df_lowercase_columns_also_work() -> None:
    """Lowercase OHLC columns travel through the same one-shot construction
    and produce the expected row count.
    """
    idx = pd.date_range("2024-01-01", periods=3)
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000, 2000, 3000],
        },
        index=idx,
    )
    out = normalize_stock_df(df, symbol="AAPL")
    assert len(out) == 3
    assert out["close"].tolist() == [101.0, 102.0, 103.0]


def test_normalize_stock_df_drops_rows_with_nan_close() -> None:
    """A NaN close still gets dropped — the dropna(subset=['close']) guard
    survives the fix. Only the *all-NaN* misalignment bug was repaired.
    """
    idx = pd.date_range("2024-01-01", periods=4)
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0],
            "High": [102.0, 103.0, 104.0, 105.0],
            "Low": [99.0, 100.0, 101.0, 102.0],
            "Close": [101.0, float("nan"), 103.0, 104.0],
            "Volume": [1000, 2000, 3000, 4000],
        },
        index=idx,
    )
    out = normalize_stock_df(df, symbol="AAPL")
    assert len(out) == 3
    assert not out["close"].isna().any()


# ─── normalize_throughput_df ───────────────────────────────────────────────


def test_normalize_throughput_df_returns_empty_on_none() -> None:
    out = normalize_throughput_df(None)
    assert list(out.columns) == THROUGHPUT_COLS
    assert len(out) == 0


def test_normalize_throughput_df_returns_empty_on_empty_df() -> None:
    out = normalize_throughput_df(pd.DataFrame())
    assert list(out.columns) == THROUGHPUT_COLS
    assert len(out) == 0


def test_normalize_throughput_df_happy_path_with_all_columns() -> None:
    """When every input column is present, all rows pass through."""
    df = pd.DataFrame({
        "year": [2020, 2021, 2022],
        "port_locode": ["A", "B", "C"],
        "country_iso3": ["USA", "CHN", "DEU"],
        "teu_millions": [1.5, 2.5, 3.5],
        "connectivity_index": [80.0, 85.0, 90.0],
    })
    out = normalize_throughput_df(df)
    assert len(out) == 3
    assert out["year"].tolist() == [2020, 2021, 2022]
    assert out["teu_millions"].tolist() == [1.5, 2.5, 3.5]
    assert (out["source"] == "worldbank").all()


def test_normalize_throughput_df_filters_year_zero_rows() -> None:
    """Rows with year <= 0 are dropped (sentinel for missing year)."""
    df = pd.DataFrame({
        "year": [0, 2020, 2021],
        "port_locode": ["A", "B", "C"],
        "country_iso3": ["USA", "CHN", "DEU"],
        "teu_millions": [0.0, 1.5, 2.5],
        "connectivity_index": [10.0, 20.0, 30.0],
    })
    out = normalize_throughput_df(df)
    assert len(out) == 2
    assert (out["year"] > 0).all()


def test_normalize_throughput_df_uses_countryiso3code_fallback() -> None:
    """World Bank API names the column `countryiso3code` — the fallback works."""
    df = pd.DataFrame({
        "year": [2020],
        "port_locode": ["A"],
        "countryiso3code": ["USA"],
        "teu_millions": [1.5],
        "connectivity_index": [80.0],
    })
    out = normalize_throughput_df(df)
    assert out["country_iso3"].iloc[0] == "USA"


def test_normalize_throughput_df_uses_value_fallback_for_teu() -> None:
    """World Bank API names the metric column `value` — the fallback works."""
    df = pd.DataFrame({
        "year": [2020],
        "port_locode": ["A"],
        "country_iso3": ["USA"],
        "value": [1.5],
        "connectivity_index": [80.0],
    })
    out = normalize_throughput_df(df)
    assert out["teu_millions"].iloc[0] == pytest.approx(1.5)


def test_normalize_throughput_df_is_sorted_by_year() -> None:
    df = pd.DataFrame({
        "year": [2022, 2020, 2021],
        "port_locode": ["A", "B", "C"],
        "country_iso3": ["USA", "CHN", "DEU"],
        "teu_millions": [3.5, 1.5, 2.5],
        "connectivity_index": [90.0, 80.0, 85.0],
    })
    out = normalize_throughput_df(df)
    assert out["year"].tolist() == [2020, 2021, 2022]


def test_normalize_throughput_df_emits_canonical_columns() -> None:
    df = pd.DataFrame({
        "year": [2020],
        "port_locode": ["A"],
        "country_iso3": ["USA"],
        "teu_millions": [1.5],
        "connectivity_index": [80.0],
    })
    out = normalize_throughput_df(df)
    assert list(out.columns) == THROUGHPUT_COLS


def test_normalize_throughput_df_missing_connectivity_index_defaults_to_zero() -> None:
    """When `connectivity_index` is absent, the fallback uses a same-length
    Series of zeros (not the bare int 0), so pd.to_numeric returns a Series
    and .fillna(0) works. The caller now gets a properly-shaped row with
    connectivity_index=0 instead of an empty frame.
    """
    df = pd.DataFrame({
        "year": [2020, 2021],
        "port_locode": ["A", "B"],
        "country_iso3": ["USA", "CHN"],
        "teu_millions": [1.5, 2.5],
        # connectivity_index intentionally missing
    })
    out = normalize_throughput_df(df)
    assert len(out) == 2
    assert list(out.columns) == THROUGHPUT_COLS
    assert out["connectivity_index"].tolist() == [0.0, 0.0]
    assert out["teu_millions"].tolist() == [1.5, 2.5]


def test_normalize_throughput_df_marks_source_as_worldbank() -> None:
    df = pd.DataFrame({
        "year": [2020],
        "port_locode": ["A"],
        "country_iso3": ["USA"],
        "teu_millions": [1.5],
        "connectivity_index": [80.0],
        "source": ["custom"],  # should be overridden
    })
    out = normalize_throughput_df(df)
    assert (out["source"] == "worldbank").all()


def test_normalize_throughput_df_year_is_int_dtype() -> None:
    """year is coerced to int — fractional / string years become integers."""
    df = pd.DataFrame({
        "year": ["2020", "2021.0", 2022],
        "port_locode": ["A", "B", "C"],
        "country_iso3": ["USA", "CHN", "DEU"],
        "teu_millions": [1.5, 2.5, 3.5],
        "connectivity_index": [80.0, 85.0, 90.0],
    })
    out = normalize_throughput_df(df)
    assert pd.api.types.is_integer_dtype(out["year"])
    assert out["year"].tolist() == [2020, 2021, 2022]
