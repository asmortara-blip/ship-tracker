"""processing/port_supply_trend.py — daily-snapshot trend extraction.

Pure-function helpers that walk the on-disk snapshot tree
(``cache/port_supply_snapshots/<YYYY-MM-DD>/``) and assemble
chronological deficit-day series for a single port or for a region's
average. The output is a flat list of ``PortTrendPoint`` records
(dates as ISO strings, deficits as floats) that the UI layer can hand
straight to plotly — no streamlit / matplotlib dependency in this
module.

The series is intentionally a list rather than a pandas DataFrame so
the tests can pin exact behaviour at the point-level without dragging
in pandas equality semantics.

Missing-port handling
---------------------
When a locode is absent from a given snapshot (e.g. the operator
captured only 40FT_REEFER that day, or the port list expanded
between snapshots) the corresponding ``PortTrendPoint`` is emitted
with ``deficit_days=float('nan')`` and ``severity_label=""``. Holding
the slot rather than filtering it preserves chronology so plotly's
line trace breaks visually on the gap day instead of silently
collapsing it.

Regional rollup
---------------
``build_regional_trend_series`` averages deficit_days across every
port in the requested region per date. NaN values are skipped from
the average (so a single missing port doesn't drop the whole region
to NaN). If every port in the region is missing on a given day, that
date's average is NaN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from processing.port_supply_history import list_snapshot_dates, load_snapshot


__all__ = [
    "PortTrendPoint",
    "build_port_trend_series",
    "build_regional_trend_series",
]


@dataclass
class PortTrendPoint:
    """One observation on a port's deficit-trend timeline.

    ``date`` is ISO YYYY-MM-DD so the UI can hand it straight to
    plotly's date axis without timezone conversions. ``deficit_days``
    can be NaN when the locode is missing from that day's snapshot —
    callers must guard hover-text formatting accordingly.
    """

    date: str
    locode: str
    deficit_days: float
    severity_label: str


def build_port_trend_series(
    locode: str,
    container_type: str = "40FT_DRY",
    *,
    root: Path | None = None,
    max_days: int = 90,
) -> list[PortTrendPoint]:
    """Return a chronological per-day deficit-day series for one port.

    Walks every date dir under ``root`` (defaults to
    ``SNAPSHOT_ROOT``), loads the matching container-type snapshot,
    and extracts the row matching ``locode``. The result is sorted
    oldest-first so downstream chart code can plot it directly.

    ``max_days`` caps the series at the most-recent N snapshots —
    keeps the UI light when the snapshot tree has years of history.

    Defensive: returns ``[]`` if ``root`` has zero snapshots (rather
    than raising), and tolerates per-day load failures by emitting a
    NaN point for that date.
    """
    if max_days <= 0:
        return []
    all_dates = list_snapshot_dates(root=root)
    if not all_dates:
        return []
    # Keep the most-recent N (list_snapshot_dates returns oldest-first).
    selected = all_dates[-max_days:] if len(all_dates) > max_days else all_dates

    out: list[PortTrendPoint] = []
    target = locode.upper()
    for d in selected:
        try:
            rows = load_snapshot(
                d, container_type=container_type, root=root,
            )
        except FileNotFoundError:
            # That day's dir exists but doesn't have the container-type
            # snapshot we need — emit a NaN slot so chronology is preserved.
            out.append(PortTrendPoint(
                date=d.isoformat(),
                locode=target,
                deficit_days=float("nan"),
                severity_label="",
            ))
            continue
        except Exception:
            # Defensive — any other load failure emits a NaN slot.
            out.append(PortTrendPoint(
                date=d.isoformat(),
                locode=target,
                deficit_days=float("nan"),
                severity_label="",
            ))
            continue

        match = next((r for r in rows if r.locode == target), None)
        if match is None:
            out.append(PortTrendPoint(
                date=d.isoformat(),
                locode=target,
                deficit_days=float("nan"),
                severity_label="",
            ))
            continue

        out.append(PortTrendPoint(
            date=d.isoformat(),
            locode=target,
            deficit_days=float(match.supply_deficit_days),
            severity_label=match.severity_label or "",
        ))
    return out


def build_regional_trend_series(
    region: str,
    container_type: str = "40FT_DRY",
    *,
    root: Path | None = None,
    max_days: int = 90,
) -> list[tuple[str, float]]:
    """Return per-day average deficit-days for ports in a region.

    Walks the snapshot tree and, for each date, averages
    ``supply_deficit_days`` across every port whose ``region`` matches.

    NaN handling: rows with NaN deficit are skipped from the average
    (so a single missing port doesn't tank the regional series). If
    every port in the region is missing on a given day, that day's
    average is NaN — preserves chronology for plotly.

    Returns a list of ``(date_iso, avg_deficit_days)`` tuples sorted
    oldest-first.

    Defensive: returns ``[]`` if ``root`` has zero snapshots.
    """
    if max_days <= 0:
        return []
    all_dates = list_snapshot_dates(root=root)
    if not all_dates:
        return []
    selected = all_dates[-max_days:] if len(all_dates) > max_days else all_dates

    out: list[tuple[str, float]] = []
    for d in selected:
        try:
            rows = load_snapshot(
                d, container_type=container_type, root=root,
            )
        except FileNotFoundError:
            out.append((d.isoformat(), float("nan")))
            continue
        except Exception:
            out.append((d.isoformat(), float("nan")))
            continue

        regional_rows = [r for r in rows if (r.region or "") == region]
        if not regional_rows:
            out.append((d.isoformat(), float("nan")))
            continue
        # Skip NaN deficits — a single missing port doesn't kill the avg.
        deficits = [
            float(r.supply_deficit_days)
            for r in regional_rows
            if not math.isnan(float(r.supply_deficit_days))
        ]
        if not deficits:
            out.append((d.isoformat(), float("nan")))
            continue
        out.append((d.isoformat(), sum(deficits) / len(deficits)))
    return out
