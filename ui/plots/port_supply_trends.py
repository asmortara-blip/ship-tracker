"""ui/plots/port_supply_trends.py — pure plotly builders for the
"deficit trend over time" section of the Port Supply Lines tab.

Kept separate from ``ui/tab_port_supply_lines.py`` so:
  * the tab module stays under ~1100 lines and easy to scan
  * the figure builders are testable without streamlit imports
  * the severity-band shading + reference-line scaffolding is reused
    by both the per-port + regional rollup figures

Builders never call ``st.*``. Empty input returns an annotated empty
``Figure`` so the caller can render unconditionally.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import plotly.graph_objects as go

from processing.port_supply_trend import PortTrendPoint
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
)


__all__ = [
    "SeverityBand",
    "SEVERITY_BANDS",
    "build_port_trend_figure",
    "build_regional_trend_figure",
]


@dataclass(frozen=True)
class SeverityBand:
    """One severity-band background shading definition.

    Matches the band boundaries documented in
    ``processing.port_supply_lines`` (Critical Deficit < -10,
    Deficit < -3, Balanced [-3, 3], Surplus [3, 10],
    Heavy Surplus > 10) — kept here as a frozen tuple so the chart's
    visual language matches the data model's band naming.
    """

    label: str
    y_low: float        # inclusive lower bound (float('-inf') for the bottom band)
    y_high: float       # exclusive upper bound (float('inf') for the top band)
    fill_color: str     # rgba string (with low alpha for background shading)


# Order matters — bottom-up so the legend reads worst → best top-down
# when paint order matters. Each band is rendered as a rectangle shape
# in the figure's layout.shapes list.
SEVERITY_BANDS: tuple[SeverityBand, ...] = (
    SeverityBand(
        label="Critical Deficit",
        y_low=float("-inf"),
        y_high=-10.0,
        fill_color="rgba(192,57,43,0.12)",
    ),
    SeverityBand(
        label="Deficit",
        y_low=-10.0,
        y_high=-3.0,
        fill_color="rgba(201,150,43,0.10)",
    ),
    SeverityBand(
        label="Balanced",
        y_low=-3.0,
        y_high=3.0,
        fill_color="rgba(90,86,80,0.06)",
    ),
    SeverityBand(
        label="Surplus",
        y_low=3.0,
        y_high=10.0,
        fill_color="rgba(46,158,110,0.10)",
    ),
    SeverityBand(
        label="Heavy Surplus",
        y_low=10.0,
        y_high=float("inf"),
        fill_color="rgba(31,138,91,0.12)",
    ),
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _band_shapes(y_min: float, y_max: float) -> list[dict]:
    """Build the list of plotly layout shapes — one rect per severity band.

    Uses x-axis 'paper' coords so the shading spans the full chart
    horizontally regardless of the x-axis date range, and y-axis
    'y' coords so the bands track the data values. The ±inf bounds
    on the extreme bands are clamped to ``y_min`` / ``y_max`` of the
    visible plot range (with a buffer) so plotly accepts the values
    as numeric.
    """
    shapes: list[dict] = []
    # Buffer so the top/bottom bands extend past the visible plot.
    buffer = max(2.0, abs(y_max - y_min) * 0.5)
    safe_min = y_min - buffer
    safe_max = y_max + buffer
    for band in SEVERITY_BANDS:
        y0 = safe_min if band.y_low == float("-inf") else band.y_low
        y1 = safe_max if band.y_high == float("inf") else band.y_high
        # Clip the band to the visible y range so plotly doesn't extend
        # the y-axis to ±inf.
        y0 = max(y0, safe_min)
        y1 = min(y1, safe_max)
        if y1 <= y0:
            continue
        shapes.append({
            "type": "rect",
            "xref": "paper",
            "x0": 0.0,
            "x1": 1.0,
            "yref": "y",
            "y0": y0,
            "y1": y1,
            "fillcolor": band.fill_color,
            "line": {"width": 0},
            "layer": "below",
        })
    return shapes


def _severity_for(deficit_days: float) -> str:
    """Map a deficit-day value to its severity label.

    Mirrors the band thresholds in ``SEVERITY_BANDS``.
    """
    if math.isnan(deficit_days):
        return ""
    if deficit_days < -10.0:
        return "Critical Deficit"
    if deficit_days < -3.0:
        return "Deficit"
    if deficit_days <= 3.0:
        return "Balanced"
    if deficit_days <= 10.0:
        return "Surplus"
    return "Heavy Surplus"


def _y_range(values: list[float]) -> tuple[float, float]:
    """Return (y_min, y_max) for shape clipping. NaN-safe."""
    real = [v for v in values if not math.isnan(v)]
    if not real:
        return (-15.0, 15.0)
    lo = min(real)
    hi = max(real)
    # Ensure a sensible visible range so the reference line + bands
    # are always shown even when the data is tightly clustered.
    lo = min(lo, -15.0)
    hi = max(hi, 15.0)
    return (lo, hi)


def _empty_figure(title: str, message: str) -> go.Figure:
    """Build an annotated-empty figure (same shape every builder returns)."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font={"color": C_TEXT3, "size": 12},
    )
    apply_dark_layout(fig, title=title, height=320)
    return fig


# ── Per-port trend figure ──────────────────────────────────────────────────


def build_port_trend_figure(
    points: list[PortTrendPoint],
    locode: str,
    port_name: str,
) -> go.Figure:
    """Line chart of one port's deficit-day trend.

    * y = deficit_days (positive = surplus, negative = deficit)
    * x = ISO date
    * Severity bands shaded as colored backgrounds
    * Horizontal y=0 reference line marks the deficit/surplus crossover
    * Hover shows deficit + severity_label
    """
    if not points:
        return _empty_figure(
            title=f"Deficit trend — {port_name or locode}",
            message="No snapshot history yet",
        )

    dates = [p.date for p in points]
    deficits = [p.deficit_days for p in points]
    severities = [p.severity_label or _severity_for(p.deficit_days) for p in points]

    # Custom hovertext that handles NaN gracefully.
    hover_texts: list[str] = []
    for p, sev in zip(points, severities):
        if math.isnan(p.deficit_days):
            hover_texts.append(
                f"<b>{port_name or locode}</b><br>"
                f"{p.date}<br>"
                "(no snapshot)<extra></extra>"
            )
        else:
            hover_texts.append(
                f"<b>{port_name or locode}</b><br>"
                f"{p.date}<br>"
                f"Supply: {p.deficit_days:+.1f}d<br>"
                f"Severity: {sev or 'Unknown'}<extra></extra>"
            )

    y_min, y_max = _y_range(deficits)
    shapes = _band_shapes(y_min, y_max)

    fig = go.Figure()

    # Severity-band backgrounds first (rendered via layout shapes).
    # Reference line at y=0 (deficit/surplus crossover) — also a layout shape.
    shapes.append({
        "type": "line",
        "xref": "paper",
        "x0": 0.0,
        "x1": 1.0,
        "yref": "y",
        "y0": 0.0,
        "y1": 0.0,
        "line": {"color": C_TEXT, "width": 1.2, "dash": "dot"},
        "layer": "above",
    })

    # Main line trace.
    fig.add_trace(go.Scatter(
        x=dates,
        y=deficits,
        mode="lines+markers",
        name="Deficit days",
        line={"color": C_ACCENT, "width": 2.0, "shape": "linear"},
        marker={
            "size": 6,
            "color": C_ACCENT,
            "line": {"color": C_BG, "width": 1},
        },
        hovertext=hover_texts,
        hovertemplate="%{hovertext}",
        connectgaps=False,   # NaN slots break the line
        showlegend=False,
    ))

    apply_dark_layout(
        fig,
        title=f"Deficit trend — {port_name or locode} ({locode})",
        height=360,
    )
    fig.update_layout(
        shapes=shapes,
        xaxis={
            "title": None,
            "type": "category",
            "gridcolor": "rgba(255,255,255,0.04)",
            "tickfont": {"color": C_TEXT3, "size": 10},
        },
        yaxis={
            "title": "Deficit days (negative = stressed)",
            "gridcolor": "rgba(255,255,255,0.04)",
            "tickfont": {"color": C_TEXT2, "size": 11},
            "zeroline": False,
        },
        margin={"l": 8, "r": 8, "t": 44, "b": 32},
    )
    return fig


# ── Regional trend figure ──────────────────────────────────────────────────


def build_regional_trend_figure(
    series: list[tuple[str, float]],
    region: str,
) -> go.Figure:
    """Line chart of one region's average deficit-day trend.

    Same axes + shading scheme as ``build_port_trend_figure``. Input
    is the flat ``(date, avg_deficit)`` shape returned by
    ``processing.port_supply_trend.build_regional_trend_series``.
    """
    if not series:
        return _empty_figure(
            title=f"Regional trend — {region}",
            message="No snapshot history yet",
        )

    dates = [d for d, _ in series]
    deficits = [v for _, v in series]
    severities = [_severity_for(v) for v in deficits]

    hover_texts: list[str] = []
    for date_iso, avg, sev in zip(dates, deficits, severities):
        if math.isnan(avg):
            hover_texts.append(
                f"<b>{region}</b><br>{date_iso}<br>"
                "(no snapshot)<extra></extra>"
            )
        else:
            hover_texts.append(
                f"<b>{region}</b><br>{date_iso}<br>"
                f"Avg supply: {avg:+.2f}d<br>"
                f"Severity: {sev or 'Unknown'}<extra></extra>"
            )

    y_min, y_max = _y_range(deficits)
    shapes = _band_shapes(y_min, y_max)
    shapes.append({
        "type": "line",
        "xref": "paper",
        "x0": 0.0,
        "x1": 1.0,
        "yref": "y",
        "y0": 0.0,
        "y1": 0.0,
        "line": {"color": C_TEXT, "width": 1.2, "dash": "dot"},
        "layer": "above",
    })

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates,
        y=deficits,
        mode="lines+markers",
        name="Avg deficit days",
        line={"color": C_ACCENT, "width": 2.0, "shape": "linear"},
        marker={
            "size": 5,
            "color": C_ACCENT,
            "line": {"color": C_BG, "width": 1},
        },
        hovertext=hover_texts,
        hovertemplate="%{hovertext}",
        connectgaps=False,
        showlegend=False,
    ))

    apply_dark_layout(
        fig,
        title=f"Regional trend — {region}",
        height=300,
    )
    fig.update_layout(
        shapes=shapes,
        xaxis={
            "title": None,
            "type": "category",
            "gridcolor": "rgba(255,255,255,0.04)",
            "tickfont": {"color": C_TEXT3, "size": 9},
        },
        yaxis={
            "title": "Avg deficit days",
            "gridcolor": "rgba(255,255,255,0.04)",
            "tickfont": {"color": C_TEXT2, "size": 10},
            "zeroline": False,
        },
        margin={"l": 8, "r": 8, "t": 40, "b": 24},
    )
    return fig
