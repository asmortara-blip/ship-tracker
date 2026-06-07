"""data/contracts.py — per-feed data-quality SLAs + schema contracts (R116).

This module is a PURE-OBSERVABILITY gate. It describes what a healthy feed
frame should look like (``FeedContract``), checks a frame against that
description (``check_contract``), and — at the wiring point — downgrades the
provenance label + records a violation when a feed disappoints
(``apply_contract`` / ``observe_contract``).

CRITICAL SAFETY CONTRACT
------------------------
Everything here sits in the hot path of every feed via
``data/normalizer.py``. None of these functions may:

  * drop / filter / re-order rows,
  * mutate the DataFrame,
  * block a feed, or
  * raise.

``check_contract`` reads the frame but never touches it. ``apply_contract``
returns a *copy* of the ``DataSource`` with a downgraded ``quality`` label —
it never touches the frame. Both are wrapped so that ANY internal failure
degrades gracefully to "pass / unchanged" rather than propagating. A bug in a
contract must never be able to break data loading.

What a contract checks
----------------------
* ``required_cols``   — these columns must be present.
* ``non_null_cols``   — these columns must contain no nulls.
* ``date_col``        — if set, the column must parse to dates and be
                        monotone non-decreasing (normalizers already sort).
* ``min_rows``        — at least this many rows (0 = no minimum; an EMPTY
                        frame is always exempt — feeds legitimately return
                        empty frames when a source is down, and that case is
                        already labelled ``demo``/``unknown`` upstream).
* ``value_ranges``    — per-column ``(min, max)`` inclusive bounds; either
                        bound may be ``None`` for "unbounded on that side".
* ``max_staleness_hours`` — newest ``date_col`` value vs ``now`` must be
                        within this many hours.

The registry ``CONTRACTS`` holds conservative expectations for the known
feeds, keyed by the lowercased ``source`` name a feed stamps on its frame
(``comtrade``, ``fred``, ``freight_scraper``, ``aishub``, …) AND by the
canonical short feed id used elsewhere (``fred``, ``yfinance``, …). Unknown
feeds map to no contract → pass (a pure no-op). Bounds are intentionally
loose so REAL data passes; the point is to catch a schema/empty/stale
regression, not to second-guess legitimate values.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Result + contract dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContractResult:
    """Outcome of a single ``check_contract`` call.

    ``passed`` is ``True`` iff ``violations`` is empty. ``violations`` is a
    list of short human-readable strings, each naming one failed expectation.
    """
    passed: bool
    violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedContract:
    """Per-feed expectations. All fields optional → an empty contract passes.

    Attributes
    ----------
    name:
        Human label for the feed (used in violation strings + log lines).
    required_cols:
        Columns that must exist in the frame.
    non_null_cols:
        Columns that must contain zero nulls (implies required).
    date_col:
        If set, this column must parse to datetimes and be monotone
        non-decreasing. Also the column used for staleness.
    min_rows:
        Minimum acceptable row count. ``0`` disables the check. An empty
        frame is always exempt (feeds return empty on outage by design).
    value_ranges:
        Mapping ``col → (min, max)``; either bound may be ``None``.
    max_staleness_hours:
        Max age (hours) of the newest ``date_col`` value relative to now.
        Requires ``date_col``; ``None`` disables.
    """
    name: str
    required_cols: tuple[str, ...] = ()
    non_null_cols: tuple[str, ...] = ()
    date_col: Optional[str] = None
    min_rows: int = 0
    value_ranges: Mapping[str, tuple[Optional[float], Optional[float]]] = field(
        default_factory=dict
    )
    max_staleness_hours: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Pure check — NEVER raises, NEVER touches the frame
# ─────────────────────────────────────────────────────────────────────────────

def check_contract(df: Any, contract: FeedContract, *,
                   now: Optional[datetime] = None) -> ContractResult:
    """Check ``df`` against ``contract``. Pure + total: never raises.

    Returns a ``ContractResult``. On ANY internal error, returns a passing
    result (fail-open) — an observability check must never punish real data
    for a bug in the checker. The frame is only read, never modified.
    """
    try:
        if contract is None:
            return ContractResult(passed=True, violations=())

        # An empty frame is exempt from every row-level check: feeds return
        # empty frames on outage by design and that is already labelled
        # demo/unknown upstream. We only verify column presence so a
        # genuinely malformed empty frame is still visible.
        raw_cols = getattr(df, "columns", None)
        if raw_cols is None:
            # Not a DataFrame-like object — nothing meaningful to check.
            # Fail open: a non-frame slipping in is a wiring error, not a
            # data violation, and must never downgrade real data.
            return ContractResult(passed=True, violations=())
        cols = list(raw_cols)
        try:
            n_rows = len(df) if df is not None else 0
        except Exception:
            n_rows = 0
        is_empty = bool(getattr(df, "empty", n_rows == 0))

        violations: list[str] = []

        # required columns -----------------------------------------------------
        for col in contract.required_cols:
            if col not in cols:
                violations.append(f"missing required column '{col}'")

        if is_empty:
            # Nothing else is meaningful on an empty frame.
            return ContractResult(passed=not violations, violations=tuple(violations))

        # non-null columns -----------------------------------------------------
        for col in contract.non_null_cols:
            if col not in cols:
                violations.append(f"missing required column '{col}'")
                continue
            try:
                n_null = int(df[col].isna().sum())
            except Exception:
                n_null = 0
            if n_null > 0:
                violations.append(f"column '{col}' has {n_null} null(s)")

        # date column: parseable + monotone non-decreasing --------------------
        if contract.date_col is not None:
            dc = contract.date_col
            if dc not in cols:
                violations.append(f"missing date column '{dc}'")
            else:
                import pandas as pd  # local import keeps module import cheap
                parsed = pd.to_datetime(df[dc], errors="coerce")
                n_unparseable = int(parsed.isna().sum())
                if n_unparseable > 0:
                    violations.append(
                        f"date column '{dc}' has {n_unparseable} unparseable value(s)"
                    )
                valid = parsed.dropna()
                if len(valid) >= 2 and not valid.is_monotonic_increasing:
                    violations.append(f"date column '{dc}' is not monotone non-decreasing")

                # staleness --------------------------------------------------
                if contract.max_staleness_hours is not None and len(valid) > 0:
                    ref = now or datetime.now(timezone.utc)
                    newest = valid.max()
                    try:
                        newest_ts = pd.Timestamp(newest)
                        ref_ts = pd.Timestamp(ref)
                        # Normalise tz so the subtraction is well-defined.
                        if newest_ts.tzinfo is None and ref_ts.tzinfo is not None:
                            ref_ts = ref_ts.tz_localize(None)
                        elif newest_ts.tzinfo is not None and ref_ts.tzinfo is None:
                            ref_ts = ref_ts.tz_localize(timezone.utc)
                        age_hours = (ref_ts - newest_ts).total_seconds() / 3600.0
                        if age_hours > contract.max_staleness_hours:
                            violations.append(
                                f"stale: newest '{dc}' is {age_hours:.1f}h old "
                                f"> SLA {contract.max_staleness_hours:.1f}h"
                            )
                    except Exception:
                        # Staleness is best-effort; a tz/parse quirk must not
                        # turn into a false violation.
                        pass

        # value ranges --------------------------------------------------------
        for col, bounds in (contract.value_ranges or {}).items():
            if col not in cols:
                violations.append(f"missing ranged column '{col}'")
                continue
            try:
                lo, hi = bounds
            except Exception:
                continue
            try:
                import pandas as pd
                series = pd.to_numeric(df[col], errors="coerce").dropna()
            except Exception:
                continue
            if series.empty:
                continue
            if lo is not None:
                n_below = int((series < lo).sum())
                if n_below > 0:
                    violations.append(
                        f"column '{col}' has {n_below} value(s) below min {lo}"
                    )
            if hi is not None:
                n_above = int((series > hi).sum())
                if n_above > 0:
                    violations.append(
                        f"column '{col}' has {n_above} value(s) above max {hi}"
                    )

        # min rows ------------------------------------------------------------
        if contract.min_rows and n_rows < contract.min_rows:
            violations.append(f"too few rows: {n_rows} < min {contract.min_rows}")

        return ContractResult(passed=not violations, violations=tuple(violations))

    except Exception as exc:  # pragma: no cover — defence in depth
        # Fail open: a checker bug must never report a false violation that
        # would downgrade real data.
        logger.debug(f"contracts.check_contract failed (treating as pass): {exc}")
        return ContractResult(passed=True, violations=())


# ─────────────────────────────────────────────────────────────────────────────
# Registry — conservative expectations so REAL data passes
# ─────────────────────────────────────────────────────────────────────────────
#
# Keyed by the lowercased ``source`` string a normalizer stamps on its frame
# AND by the canonical short feed id. Bounds are deliberately loose: the goal
# is to catch a schema break / empty-where-data-expected / a clearly-bad value
# (e.g. negative price), not to second-guess legitimate observations.
#
# No ``max_staleness_hours`` is set by default — staleness SLAs are owned by
# the per-feed DataSource.sla_hours + the source_health probes, and a frame's
# newest date can legitimately lag (Comtrade is monthly, throughput annual).
# Staleness is supported by the checker for callers that want it, but the
# registry stays conservative to avoid false STALE downgrades on real data.

CONTRACTS: dict[str, FeedContract] = {
    # ── Equities (yfinance OHLCV) ──────────────────────────────────────────
    "stock": FeedContract(
        name="Equities (OHLCV)",
        required_cols=("date", "symbol", "open", "high", "low", "close", "volume"),
        non_null_cols=("date", "close"),
        date_col="date",
        min_rows=0,
        value_ranges={
            "close": (0.0, None),
            "open": (0.0, None),
            "high": (0.0, None),
            "low": (0.0, None),
            "volume": (0.0, None),
        },
    ),

    # ── Macro (FRED / World Bank long form) ────────────────────────────────
    "fred": FeedContract(
        name="Macro series (FRED)",
        required_cols=("date", "series_id", "series_name", "value", "source"),
        non_null_cols=("date", "value"),
        date_col="date",
        min_rows=0,
    ),

    # ── Freight rates ──────────────────────────────────────────────────────
    "freight_scraper": FeedContract(
        name="Freight rates",
        required_cols=("date", "route_id", "rate_usd_per_feu", "index_name", "source"),
        non_null_cols=("date", "rate_usd_per_feu"),
        date_col="date",
        min_rows=0,
        value_ranges={"rate_usd_per_feu": (0.0, None)},
    ),

    # ── Port throughput (World Bank) ───────────────────────────────────────
    "worldbank": FeedContract(
        name="Port throughput",
        required_cols=("year", "port_locode", "country_iso3", "teu_millions",
                       "connectivity_index", "source"),
        non_null_cols=("year",),
        min_rows=0,
        value_ranges={
            "year": (1900, 2100),
            "teu_millions": (0.0, None),
        },
    ),

    # ── AIS vessel congestion ──────────────────────────────────────────────
    "aishub": FeedContract(
        name="AIS congestion",
        required_cols=("date", "port_locode", "vessel_count", "vessel_type", "source"),
        non_null_cols=("date", "vessel_count"),
        date_col="date",
        min_rows=0,
        value_ranges={"vessel_count": (0.0, None)},
    ),

    # ── Trade (UN Comtrade) ────────────────────────────────────────────────
    "comtrade": FeedContract(
        name="Trade flows (Comtrade)",
        required_cols=("date", "port_locode", "country_iso3", "hs_code", "flow",
                       "value_usd", "net_weight_kg", "source"),
        non_null_cols=("date",),
        date_col="date",
        min_rows=0,
        value_ranges={
            "value_usd": (0.0, None),
            "net_weight_kg": (0.0, None),
        },
    ),
}

# Aliases so a lookup by canonical short feed id or a DataSource.name finds the
# right contract. Maps an alias → a registry key above.
_ALIASES: dict[str, str] = {
    "yahoo finance":  "stock",
    "yfinance":       "stock",
    "stocks":         "stock",
    "equities":       "stock",
    "macro":          "fred",
    "fred series":    "fred",
    "freight":        "freight_scraper",
    "ais":            "aishub",
    "throughput":     "worldbank",
    "port throughput": "worldbank",
    "trade":          "comtrade",
    "un comtrade":    "comtrade",
}


def get_contract(source_name: Optional[str]) -> Optional[FeedContract]:
    """Look up a contract by ``source`` string / feed id. ``None`` → no match.

    Never raises. Matching is case-insensitive and tries the alias map.
    Unknown source → ``None`` (the caller treats this as a pure no-op).
    """
    try:
        if not source_name:
            return None
        key = str(source_name).strip().lower()
        if key in CONTRACTS:
            return CONTRACTS[key]
        alias = _ALIASES.get(key)
        if alias and alias in CONTRACTS:
            return CONTRACTS[alias]
        return None
    except Exception:  # pragma: no cover — defence in depth
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Violation recording — best-effort, never raises
# ─────────────────────────────────────────────────────────────────────────────

def _record_violation(source_name: str, violations: Sequence[str]) -> None:
    """Best-effort record of a quality violation to ``kv_state``.

    Key is ``data_quality_violation:<source>:<YYYY-MM-DD>`` and the value is a
    running count for that source on that UTC day (TEXT, like the other
    operator counters). On ANY failure we log at debug and move on — recording
    a violation must never break the feed.
    """
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"data_quality_violation:{str(source_name or 'unknown').strip().lower()}:{day}"
        now_iso = datetime.now(timezone.utc).isoformat()

        from state.db import get_connection

        conn = get_connection()
        # Atomic increment — mirrors the kv_state counter pattern used by the
        # alert engines. CAST(value AS INTEGER) yields 0 for a missing/corrupt
        # value so a fresh key starts the count at 1.
        conn.execute(
            "INSERT INTO kv_state (key, value, updated_at) VALUES (?, '1', ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = CAST(value AS INTEGER) + 1, "
            "updated_at = excluded.updated_at",
            (key, now_iso),
        )
        logger.debug(
            "data_quality: {} violation(s) for source={!r}: {}",
            len(violations), source_name, "; ".join(violations)[:300],
        )
    except Exception as exc:
        logger.debug(f"contracts._record_violation failed (ignored): {exc}")


def get_violation_count(source_name: str, *, day: Optional[str] = None) -> int:
    """Return today's (UTC) recorded violation count for ``source_name``.

    ``day`` overrides the date (``YYYY-MM-DD``) for tests. Returns 0 when no
    row exists. Never raises.
    """
    try:
        d = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"data_quality_violation:{str(source_name or 'unknown').strip().lower()}:{d}"
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0
    except Exception as exc:
        logger.debug(f"contracts.get_violation_count failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Apply — downgrade the DataSource label on failure; NEVER touch the frame
# ─────────────────────────────────────────────────────────────────────────────

def apply_contract(df: Any, source: Any, *, now: Optional[datetime] = None,
                   record: bool = True) -> Any:
    """Return a (possibly downgraded) copy of ``source`` for ``df``.

    Looks up the contract by ``source.name``. If the contract FAILS, returns a
    COPY of ``source`` with ``quality`` downgraded to ``DataQuality.DEMO`` and
    a note appended; otherwise returns ``source`` unchanged. The DataFrame is
    NEVER touched and NEVER returned mutated.

    On ANY exception (no contract module, bad source, checker bug) returns
    ``source`` unchanged. This is the public helper feeds/UIs can call when
    they hold a real ``DataSource``.
    """
    try:
        if source is None:
            return source
        name = getattr(source, "name", None)
        contract = get_contract(name)
        if contract is None:
            return source  # unknown feed → pure no-op

        result = check_contract(df, contract, now=now)
        if result.passed:
            return source

        if record:
            _record_violation(str(name), result.violations)

        # Downgrade the label only — copy, never mutate the original or df.
        from data.quality import DataQuality

        note = "; ".join(result.violations)[:200]
        existing = getattr(source, "notes", "") or ""
        new_notes = (existing + " | " if existing else "") + f"contract: {note}"
        try:
            return replace(source, quality=DataQuality.DEMO, notes=new_notes)
        except Exception:
            # ``source`` is not a frozen dataclass we can ``replace`` — fall
            # back to returning it unchanged rather than risk a mutation.
            return source
    except Exception as exc:  # pragma: no cover — defence in depth
        logger.debug(f"contracts.apply_contract failed (returning source unchanged): {exc}")
        return source


def observe_contract(df: Any, source_name: Optional[str], *,
                     now: Optional[datetime] = None) -> ContractResult:
    """Frame-only observability hook for the normalizer chokepoint.

    The normalizers carry a ``source`` *string*, not a ``DataSource`` object,
    so this variant takes the name directly. It runs the contract check and
    records any violations, then returns the ``ContractResult`` for callers
    that want it. It NEVER touches ``df`` and NEVER raises.

    Unknown source → passing no-op (no contract, nothing recorded).
    """
    try:
        contract = get_contract(source_name)
        if contract is None:
            return ContractResult(passed=True, violations=())
        result = check_contract(df, contract, now=now)
        if not result.passed:
            _record_violation(str(source_name), result.violations)
        return result
    except Exception as exc:  # pragma: no cover — defence in depth
        logger.debug(f"contracts.observe_contract failed (treating as pass): {exc}")
        return ContractResult(passed=True, violations=())


__all__ = [
    "ContractResult",
    "FeedContract",
    "CONTRACTS",
    "check_contract",
    "get_contract",
    "apply_contract",
    "observe_contract",
    "get_violation_count",
]
