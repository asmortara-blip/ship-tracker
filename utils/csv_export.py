"""utils/csv_export.py — in-memory CSV-export helpers for UI download buttons.

The Operator Overview (and, eventually, every other dashboard) wants to let
an operator grab whatever's currently on screen and pull it into Excel for
offline analysis without scraping the page. This module is the tiny
substrate that makes that one-line in a Streamlit panel:

    csv_bytes = rows_to_csv_bytes(rows, columns=["Source", "Status"])
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=safe_filename("source_health"),
        mime="text/csv",
    )

Design contract
---------------
* Everything happens **in memory** — bytes never touch disk. Streamlit
  serves the bytes directly to the browser.
* Output is **UTF-8 with a BOM**. Excel on macOS / Windows mis-detects
  plain UTF-8 (decodes as cp1252) and a CSV with any non-ASCII character
  in a label will display "Â" droppings. The BOM forces UTF-8 detection
  and is silently consumed by every reasonable CSV parser including
  pandas (``encoding='utf-8-sig'``).
* All functions are **best-effort**. ``rows_to_csv_bytes`` coerces
  non-serializable values to ``str()`` so an unexpected ``datetime`` /
  ``Decimal`` / custom-class row value can never crash the download
  button.
* ``safe_filename`` produces a filesystem-safe, lowercase, ASCII-only
  filename with a timestamp suffix so two clicks in the same session
  don't collide on the operator's download dir.

Edge cases pinned by tests
--------------------------
* Empty rows → returns header-only CSV (one row of column names).
* Empty rows AND no columns hint → returns just the BOM.
* Non-serializable values → ``str()`` coerced.
* Empty ``base`` for ``safe_filename`` → fallback ``export``.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


# Raw UTF-8 BOM. Spelled out as bytes (not as a literal '﻿'.encode())
# so it's obvious at a glance what we're prepending and there's no source-
# editor invisible-char surprise.
_BOM: bytes = b"\xef\xbb\xbf"


# ─── Public: DataFrame → CSV bytes ──────────────────────────────────────────


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Encode a DataFrame as UTF-8-BOM CSV bytes (Excel-friendly).

    Wraps ``df.to_csv(index=False)`` and prepends the BOM. ``None`` /
    missing DataFrame falls back to bytes containing just the BOM, so a
    download button never serves a corrupted file.
    """
    if df is None:
        return _BOM
    try:
        csv_text = df.to_csv(index=False)
    except Exception:
        # Pathological cells (e.g. a column of un-stringable objects) —
        # degrade to header-only CSV constructed from the column names.
        try:
            csv_text = ",".join(str(c) for c in df.columns) + "\n"
        except Exception:
            return _BOM
    return _BOM + csv_text.encode("utf-8")


# ─── Public: list-of-dicts → CSV bytes ──────────────────────────────────────


def rows_to_csv_bytes(
    rows: list[dict],
    columns: list[str] | None = None,
) -> bytes:
    """Build a CSV (bytes) from a list of dict rows.

    Parameters
    ----------
    rows:
        Heterogenous list of dicts. Missing keys become empty cells.
        Non-string/numeric values are coerced via ``str()`` so a stray
        ``datetime`` / ``Decimal`` / dataclass instance can't blow up the
        download button.
    columns:
        Optional explicit column list. When provided the output is
        restricted to (and ordered by) these columns; missing keys in
        any row become empty strings. When ``None`` the columns are
        inferred from the union of keys in ``rows`` (insertion-ordered
        from the first occurrence — pandas ``DataFrame(list[dict])``
        does this for us).

    Returns
    -------
    bytes
        UTF-8-BOM CSV. Always at least the BOM + header row when columns
        are supplied; the BOM alone when both ``rows`` and ``columns``
        are empty.
    """
    # Empty rows → header-only CSV (or just the BOM if no columns hint).
    if not rows:
        if columns:
            header = ",".join(str(c) for c in columns) + "\n"
            return _BOM + header.encode("utf-8")
        return _BOM

    # Coerce every cell to a CSV-safe primitive. We do this in a fresh
    # list-of-dicts (vs. mutating the caller's) so the caller can keep
    # using the original ``rows`` for whatever else (e.g. UI rendering).
    coerced: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            # An unexpected row shape — coerce the whole thing to a single
            # "value" column so the download still works.
            coerced.append({"value": _coerce_cell(row)})
            continue
        coerced.append({k: _coerce_cell(v) for k, v in row.items()})

    try:
        df = pd.DataFrame(coerced)
    except Exception:
        # Last-ditch fallback — header-only.
        if columns:
            header = ",".join(str(c) for c in columns) + "\n"
            return _BOM + header.encode("utf-8")
        return _BOM

    if columns:
        # Restrict + order. Missing columns are added as empty strings.
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        df = df[list(columns)]

    return dataframe_to_csv_bytes(df)


# ─── Public: safe filename ──────────────────────────────────────────────────


def safe_filename(base: str, ext: str = "csv") -> str:
    """Build a filesystem-safe, timestamped filename for a download button.

    The base is lowercased, every non-``[a-z0-9_]`` run is collapsed to
    a single ``_``, leading/trailing underscores are stripped, and a
    timestamp suffix (``_YYYYMMDD_HHMMSS``) is appended before the
    extension. An empty (or all-symbol) ``base`` falls back to
    ``'export'`` so the file always has a usable name.

    Examples
    --------
    >>> safe_filename("Alert Breakdown")
    'alert_breakdown_20260523_140000.csv'
    >>> safe_filename("")
    'export_20260523_140000.csv'
    >>> safe_filename("LLM-Spend!!", ext="tsv")
    'llm_spend_20260523_140000.tsv'
    """
    raw = str(base or "")
    cleaned = re.sub(r"[^a-z0-9_]+", "_", raw.lower())
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "export"

    # Normalize the extension too — strip a leading dot and lowercase.
    ext_clean = str(ext or "csv").lower().lstrip(".")
    if not ext_clean:
        ext_clean = "csv"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{cleaned}_{stamp}.{ext_clean}"


# ─── Internal: cell coercion ────────────────────────────────────────────────


def _coerce_cell(value: Any) -> Any:
    """Coerce an arbitrary cell value into something pandas/CSV-safe.

    Strings, ints, floats, bools, and ``None`` pass through unchanged
    (pandas / to_csv handles those natively). Everything else — datetimes,
    Decimals, dataclasses, custom objects — gets ``str()``-coerced. Any
    coercion that itself raises falls back to an empty string so a bad
    ``__str__`` cannot crash the download.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        return str(value)
    except Exception:
        return ""


# ─── Convenience: BytesIO accessor (kept private — callers go through bytes)
def _csv_to_bytesio(payload: bytes) -> io.BytesIO:
    """Wrap CSV bytes in a BytesIO for callers that need a file-like.

    Kept here (vs. inlined) so a future caller doing zip-bundling or
    multipart upload has one obvious entry point. Not part of the public
    API contract — use ``dataframe_to_csv_bytes`` / ``rows_to_csv_bytes``
    directly when bytes are sufficient.
    """
    buf = io.BytesIO(payload)
    buf.seek(0)
    return buf
