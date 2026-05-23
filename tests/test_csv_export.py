"""Tests for utils.csv_export — the in-memory CSV-export substrate.

Coverage
--------
* ``dataframe_to_csv_bytes`` round-trips (write + read back via pd.read_csv
  with ``encoding='utf-8-sig'`` which strips the BOM).
* ``rows_to_csv_bytes`` with an empty list returns header-only (or just
  the BOM when no column hint is supplied).
* ``rows_to_csv_bytes`` with mixed types (int, str, None, datetime) coerces
  cleanly without raising.
* ``rows_to_csv_bytes`` with a ``columns`` restriction respects order +
  drops extra columns + fills missing keys with empties.
* ``safe_filename`` normalizes ASCII (lowercased, non-alphanumerics → ``_``,
  trimmed underscores).
* ``safe_filename`` appends a timestamp suffix.
* ``safe_filename`` with empty / all-symbol base falls back to
  ``'export_<timestamp>.csv'``.
* BOM prefix preserved on every code path — Excel-friendly invariant.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pandas as pd

from utils.csv_export import (
    dataframe_to_csv_bytes,
    rows_to_csv_bytes,
    safe_filename,
)


# UTF-8 BOM — repeated here so the test file documents the invariant
# without importing the private module constant.
_BOM = b"\xef\xbb\xbf"


# ─── dataframe_to_csv_bytes ────────────────────────────────────────────────


def test_dataframe_to_csv_bytes_round_trip() -> None:
    """Write a DataFrame to bytes, then read it back and compare cell-by-cell."""
    df = pd.DataFrame({
        "ticker":   ["ZIM", "MAERSK-B"],
        "price":    [12.34, 1234.56],
        "trending": [True, False],
    })
    payload = dataframe_to_csv_bytes(df)

    # BOM invariant — Excel reads UTF-8 only when it sees this prefix.
    assert payload.startswith(_BOM)

    # Read back. encoding='utf-8-sig' silently strips the BOM.
    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    assert list(df_back.columns) == ["ticker", "price", "trending"]
    assert df_back["ticker"].tolist() == ["ZIM", "MAERSK-B"]
    assert df_back["price"].tolist() == [12.34, 1234.56]


def test_dataframe_to_csv_bytes_none_input() -> None:
    """A None DataFrame degrades to just the BOM — never raises."""
    payload = dataframe_to_csv_bytes(None)  # type: ignore[arg-type]
    assert payload == _BOM


def test_dataframe_to_csv_bytes_preserves_unicode() -> None:
    """Non-ASCII column values must survive the round-trip exactly — the
    BOM exists for this purpose. If we ever silently lose the unicode, this
    test breaks loudly."""
    df = pd.DataFrame({"name": ["São Paulo", "København", "東京"]})
    payload = dataframe_to_csv_bytes(df)
    assert payload.startswith(_BOM)
    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    assert df_back["name"].tolist() == ["São Paulo", "København", "東京"]


# ─── rows_to_csv_bytes ─────────────────────────────────────────────────────


def test_rows_to_csv_bytes_empty_returns_bom_only() -> None:
    """No rows AND no columns hint → just the BOM. Caller is on the hook
    for skipping the download button in this case (the UI does this)."""
    assert rows_to_csv_bytes([]) == _BOM


def test_rows_to_csv_bytes_empty_with_columns_returns_header() -> None:
    """No rows but a column hint → header row only (no data rows)."""
    payload = rows_to_csv_bytes([], columns=["Source", "Status"])
    assert payload.startswith(_BOM)
    text = payload[len(_BOM):].decode("utf-8")
    # CSV writes trailing newline after header.
    assert text.strip() == "Source,Status"


def test_rows_to_csv_bytes_mixed_types_coerced() -> None:
    """Mixed cell types (int, str, None, datetime) — must not raise.

    datetimes are not natively CSV-serializable in a stable way across
    pandas versions; our coercion forces ``str()`` so the cell lands as a
    readable ISO string. None stays None (pandas writes empty)."""
    when = datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc)
    rows = [
        {"id": 1, "name": "alpha", "ts": when, "missing": None},
        {"id": 2, "name": "beta",  "ts": when, "missing": "present"},
    ]
    payload = rows_to_csv_bytes(rows)
    assert payload.startswith(_BOM)

    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    assert df_back["id"].tolist() == [1, 2]
    assert df_back["name"].tolist() == ["alpha", "beta"]
    # The datetime cell should round-trip as a string containing the ISO
    # date — we don't pin the exact format because pandas / str(datetime)
    # are version-sensitive, but the date must be present.
    assert all("2026-05-23" in str(v) for v in df_back["ts"].tolist())


def test_rows_to_csv_bytes_columns_restriction() -> None:
    """When ``columns`` is supplied: order respected, extras dropped,
    missing keys filled with empty strings."""
    rows = [
        {"a": 1, "b": 2, "c": 3, "extra": "should-be-dropped"},
        {"a": 4, "b": 5},  # 'c' missing → empty cell
    ]
    payload = rows_to_csv_bytes(rows, columns=["c", "a"])
    assert payload.startswith(_BOM)
    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    # Order matches `columns`, extras dropped.
    assert list(df_back.columns) == ["c", "a"]
    # Second row's 'c' should be missing → pandas reads as NaN / empty.
    c_vals = df_back["c"].tolist()
    assert c_vals[0] == 3
    # NaN or empty for the missing cell.
    assert pd.isna(c_vals[1]) or c_vals[1] == "" or c_vals[1] == 0
    assert df_back["a"].tolist() == [1, 4]


def test_rows_to_csv_bytes_handles_non_dict_row() -> None:
    """A stray non-dict row (e.g. someone passed a list of strings) is
    coerced into a single 'value' column — must not crash."""
    payload = rows_to_csv_bytes(["just-a-string"])  # type: ignore[list-item]
    assert payload.startswith(_BOM)
    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    assert "value" in df_back.columns
    assert df_back["value"].tolist() == ["just-a-string"]


def test_rows_to_csv_bytes_non_serializable_value_coerced() -> None:
    """An object with no JSON / CSV representation must still serialize
    via str(). A custom class with __str__ proves the coercion path."""
    class Marker:
        def __str__(self) -> str:
            return "marker-instance"
    rows = [{"k": Marker()}]
    payload = rows_to_csv_bytes(rows)
    assert payload.startswith(_BOM)
    df_back = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    assert df_back["k"].tolist() == ["marker-instance"]


# ─── safe_filename ─────────────────────────────────────────────────────────


def test_safe_filename_ascii_normalization() -> None:
    """Spaces, punctuation, mixed case → lowercase + underscore-collapsed."""
    name = safe_filename("Alert Breakdown")
    # Strip the timestamp suffix to assert on the stem.
    assert name.startswith("alert_breakdown_")
    assert name.endswith(".csv")
    # No uppercase, no spaces, no punctuation in the stem.
    stem = name.rsplit(".", 1)[0]
    assert stem == stem.lower()
    assert " " not in stem


def test_safe_filename_collapses_runs_of_symbols() -> None:
    """Multiple back-to-back symbols collapse to a single underscore."""
    name = safe_filename("LLM---Spend!!")
    assert name.startswith("llm_spend_")
    # No double underscores in the slug portion.
    stem = name.rsplit(".", 1)[0]
    # The timestamp portion has no underscores in a row either (YYYYMMDD_HHMMSS),
    # so a global no-double-underscore assertion is safe.
    assert "__" not in stem


def test_safe_filename_appends_timestamp() -> None:
    """A YYYYMMDD_HHMMSS suffix must be present before the extension."""
    name = safe_filename("channel_health")
    # Match the structure: base_YYYYMMDD_HHMMSS.csv
    pattern = r"^channel_health_\d{8}_\d{6}\.csv$"
    assert re.match(pattern, name), f"unexpected filename shape: {name}"


def test_safe_filename_empty_base_falls_back_to_export() -> None:
    """Empty base, None base, and all-symbol base all fall back to 'export'."""
    for empty in ("", None, "!!!", "---"):
        name = safe_filename(empty)  # type: ignore[arg-type]
        assert name.startswith("export_"), f"empty base {empty!r} did not fall back"
        assert name.endswith(".csv")


def test_safe_filename_respects_custom_extension() -> None:
    """The ext kwarg controls the suffix — leading dot tolerated."""
    name1 = safe_filename("export", ext="tsv")
    assert name1.endswith(".tsv")
    name2 = safe_filename("export", ext=".json")
    assert name2.endswith(".json")


# ─── BOM invariant — the load-bearing Excel-friendly contract ──────────────


def test_bom_prefix_preserved_on_every_path() -> None:
    """Every public-function return value starts with the BOM. If we ever
    regress this, Excel starts mis-decoding non-ASCII labels — and that's
    the entire reason this module exists."""
    # DataFrame path
    assert dataframe_to_csv_bytes(pd.DataFrame({"x": [1]})).startswith(_BOM)
    assert dataframe_to_csv_bytes(None).startswith(_BOM)  # type: ignore[arg-type]

    # Rows paths
    assert rows_to_csv_bytes([]).startswith(_BOM)
    assert rows_to_csv_bytes([], columns=["a"]).startswith(_BOM)
    assert rows_to_csv_bytes([{"a": 1}]).startswith(_BOM)
    assert rows_to_csv_bytes([{"a": 1}], columns=["a"]).startswith(_BOM)
