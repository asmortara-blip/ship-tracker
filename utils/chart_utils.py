"""Plotly chart helper utilities for Ship Tracker.

All figures use the project's dark theme by default.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# ── Theme constants (mirrors ui/styles.py) ───────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"
_C_ACCENT  = "#3b82f6"
_C_HIGH    = "#10b981"
_C_MOD     = "#f59e0b"
_C_LOW     = "#ef4444"

_MA_COLORS = {
    7:  "#60a5fa",   # light blue
    30: "#f59e0b",   # amber
    90: "#8b5cf6",   # purple
}

_BB_FILL_COLOR = "rgba(59,130,246,0.08)"
_BB_LINE_COLOR = "rgba(59,130,246,0.45)"


def make_dark_figure(height: int = 400, title: str = "") -> go.Figure:
    """Return a new Plotly Figure pre-configured with the Ship Tracker dark theme.

    Parameters
    ----------
    height: Figure height in pixels (default 400).
    title:  Optional chart title string.

    Returns
    -------
    go.Figure with dark layout applied and no traces.
    """
    fig = go.Figure()

    margin = {"l": 20, "r": 20, "t": 44 if title else 20, "b": 20}

    layout_kwargs: dict = {
        "paper_bgcolor": _C_BG,
        "plot_bgcolor":  _C_SURFACE,
        "height":        height,
        "margin":        margin,
        "font": {
            "color":  _C_TEXT,
            "family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "size":   12,
        },
        "showlegend": True,
        "legend": {
            "bgcolor":     "rgba(0,0,0,0)",
            "bordercolor": "rgba(255,255,255,0.10)",
            "font":        {"color": _C_TEXT2, "size": 11},
            "orientation": "h",
            "yanchor":     "bottom",
            "y":           1.02,
            "xanchor":     "right",
            "x":           1,
        },
        "xaxis": {
            "gridcolor":      "rgba(255,255,255,0.05)",
            "zerolinecolor":  "rgba(255,255,255,0.10)",
            "tickfont":       {"color": _C_TEXT3, "size": 11},
            "linecolor":      "rgba(255,255,255,0.10)",
            "showgrid":       True,
        },
        "yaxis": {
            "gridcolor":      "rgba(255,255,255,0.05)",
            "zerolinecolor":  "rgba(255,255,255,0.10)",
            "tickfont":       {"color": _C_TEXT3, "size": 11},
            "linecolor":      "rgba(255,255,255,0.10)",
            "showgrid":       True,
        },
        "hoverlabel": {
            "bgcolor":     _C_CARD,
            "bordercolor": "rgba(255,255,255,0.15)",
            "font":        {"color": _C_TEXT, "size": 12},
        },
    }

    if title:
        layout_kwargs["title"] = {
            "text":  title,
            "font":  {"size": 14, "color": _C_TEXT},
            "x":     0.01,
            "xanchor": "left",
        }

    fig.update_layout(**layout_kwargs)
    return fig


def add_ma_lines(
    fig: go.Figure,
    series: pd.Series,
    windows: list[int] | None = None,
    row: int = 1,
) -> None:
    """Add moving-average lines to an existing figure in-place.

    Parameters
    ----------
    fig:     Target Plotly figure (modified in-place).
    series:  Pandas Series with a DatetimeIndex (or any ordered index).
    windows: List of MA window sizes in days (default [7, 30, 90]).
    row:     Subplot row number (1-indexed, default 1).
    """
    if windows is None:
        windows = [7, 30, 90]

    for w in windows:
        ma = series.rolling(window=w, min_periods=1).mean()
        color = _MA_COLORS.get(w, _C_TEXT2)
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=ma,
                mode="lines",
                name=f"MA{w}",
                line={"color": color, "width": 1.5, "dash": "dot"},
                hovertemplate=f"MA{w}: %{{y:.2f}}<extra></extra>",
            ),
            row=row,
            col=1,
        )


def add_bollinger_bands(
    fig: go.Figure,
    series: pd.Series,
    window: int = 20,
    std: float = 2,
    row: int = 1,
) -> None:
    """Add Bollinger Bands (upper, lower, mid) with a shaded fill to an existing figure.

    Adds four traces: upper band, lower band (with fill), and middle (SMA) line.

    Parameters
    ----------
    fig:    Target Plotly figure (modified in-place).
    series: Pandas Series with an ordered index.
    window: Rolling window size (default 20).
    std:    Number of standard deviations for band width (default 2).
    row:    Subplot row number (1-indexed, default 1).
    """
    sma   = series.rolling(window=window, min_periods=1).mean()
    sigma = series.rolling(window=window, min_periods=1).std(ddof=0).fillna(0)
    upper = sma + std * sigma
    lower = sma - std * sigma

    x_vals      = list(series.index)
    x_combined  = x_vals + x_vals[::-1]
    y_combined  = list(upper) + list(lower[::-1])

    # Shaded fill between bands
    fig.add_trace(
        go.Scatter(
            x=x_combined,
            y=y_combined,
            fill="toself",
            fillcolor=_BB_FILL_COLOR,
            line={"color": "rgba(0,0,0,0)", "width": 0},
            name=f"BB({window},{std}\u03c3) band",
            showlegend=True,
            hoverinfo="skip",
        ),
        row=row,
        col=1,
    )

    # Upper band line
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=upper,
            mode="lines",
            name=f"BB upper",
            line={"color": _BB_LINE_COLOR, "width": 1},
            hovertemplate="BB upper: %{y:.2f}<extra></extra>",
            showlegend=False,
        ),
        row=row,
        col=1,
    )

    # Lower band line
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=lower,
            mode="lines",
            name=f"BB lower",
            line={"color": _BB_LINE_COLOR, "width": 1},
            hovertemplate="BB lower: %{y:.2f}<extra></extra>",
            showlegend=False,
        ),
        row=row,
        col=1,
    )

    # Middle SMA line
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=sma,
            mode="lines",
            name=f"BB mid (SMA{window})",
            line={"color": _C_ACCENT, "width": 1.5, "dash": "dot"},
            hovertemplate=f"SMA{window}: %{{y:.2f}}<extra></extra>",
        ),
        row=row,
        col=1,
    )


def format_rate(value: float) -> str:
    """Format a numeric value as a USD freight rate string.

    Example: 1234.5 -> "$1,235"

    Parameters
    ----------
    value: Numeric rate value ($/FEU or similar).

    Returns
    -------
    str: Formatted string like "$1,234".
    """
    return f"${value:,.0f}"


def format_pct(value: float, show_sign: bool = True) -> str:
    """Format a numeric value as a percentage string.

    Example: 12.34 -> "+12.3%"  |  -5.6 -> "-5.6%"

    Parameters
    ----------
    value:     Numeric percentage (e.g. 12.3 means 12.3%).
    show_sign: Prefix positive values with "+" (default True).

    Returns
    -------
    str: Formatted percentage string.
    """
    sign = "+" if (show_sign and value > 0) else ""
    return f"{sign}{value:.1f}%"
