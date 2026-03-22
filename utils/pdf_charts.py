"""
pdf_charts.py — Publication-quality matplotlib charts for investor PDF reports.

Visual quality target: Goldman Sachs Equity Research / JP Morgan Global Markets /
Bloomberg Intelligence. All charts return PNG bytes for PDF embedding.
"""

import matplotlib
matplotlib.use('Agg')  # non-interactive backend — must come before pyplot

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
import io
from typing import List, Optional

# ---------------------------------------------------------------------------
# Design System — Institutional Dark Theme
# ---------------------------------------------------------------------------

BG_DARK       = "#0D1B2A"   # deep navy — page/chart background
BG_MID        = "#132237"   # slightly lighter navy — chart area
BG_CARD       = "#1A2E45"   # card backgrounds
ACCENT_GOLD   = "#C9A84C"   # gold accent — primary data highlight
ACCENT_BLUE   = "#2E86C1"   # steel blue — secondary
ACCENT_TEAL   = "#1ABC9C"   # teal — positive/bullish
ACCENT_RED    = "#E74C3C"   # crimson — negative/bearish
ACCENT_AMBER  = "#F39C12"   # amber — neutral/caution
TEXT_PRIMARY  = "#ECF0F1"   # near-white text
TEXT_SECONDARY= "#95A5A6"   # muted text
GRID_LINE     = "#1E3A5F"   # subtle grid lines

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fig_to_bytes(fig) -> bytes:
    """Render figure to PNG bytes and close it."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=180, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    buf.seek(0)
    data = buf.read()
    plt.close(fig)
    return data


def _base_fig(w_in: float = 8, h_in: float = 4):
    """Return (fig, ax) with institutional dark style applied."""
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    fig.patch.set_facecolor(BG_DARK)
    ax.set_facecolor(BG_MID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
    ax.spines['bottom'].set_color(GRID_LINE)
    ax.spines['left'].set_color(GRID_LINE)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.xaxis.label.set_color(TEXT_SECONDARY)
    ax.yaxis.label.set_color(TEXT_SECONDARY)
    ax.grid(axis='y', color=GRID_LINE, linewidth=0.5, alpha=0.7)
    ax.set_title('', color=TEXT_PRIMARY, fontsize=10, fontweight='bold', pad=12)
    return fig, ax


def _placeholder(message: str = "Insufficient Data",
                 w_in: float = 6, h_in: float = 3) -> bytes:
    """Return a minimal dark placeholder chart with a centered message."""
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    fig.patch.set_facecolor(BG_DARK)
    ax.set_facecolor(BG_DARK)
    ax.set_axis_off()
    ax.text(0.5, 0.5, message,
            ha='center', va='center',
            color=TEXT_SECONDARY, fontsize=11,
            transform=ax.transAxes)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 1. Sentiment Gauge Chart
# ---------------------------------------------------------------------------

def sentiment_gauge_chart(score: float, label: str,
                          w_in: float = 4, h_in: float = 3) -> bytes:
    """
    Semicircle gauge chart showing sentiment from -1 (BEARISH) to +1 (BULLISH).
    Returns PNG bytes.
    """
    try:
        score = float(score)
        score = max(-1.0, min(1.0, score))  # clamp

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_DARK)
        ax.set_aspect('equal')
        ax.set_axis_off()

        # --- Arc zones (theta: 180° → 0° maps to -1 → +1) ---
        # Zone boundaries in data-space (-1 to +1) → angle (180 to 0 degrees)
        def score_to_deg(s):
            return 180.0 - (s + 1.0) / 2.0 * 180.0

        r_outer, r_inner = 1.0, 0.65
        arc_width = r_outer - r_inner

        def draw_arc_zone(start_score, end_score, color, alpha=0.85):
            theta1 = score_to_deg(end_score)    # matplotlib: CCW, so higher deg = left
            theta2 = score_to_deg(start_score)
            wedge = mpatches.Wedge(
                center=(0, 0),
                r=r_outer,
                theta1=theta1,
                theta2=theta2,
                width=arc_width,
                facecolor=color,
                edgecolor=BG_DARK,
                linewidth=1.5,
                alpha=alpha
            )
            ax.add_patch(wedge)

        draw_arc_zone(-1.0, -0.1, ACCENT_RED)
        draw_arc_zone(-0.1,  0.1, TEXT_SECONDARY)
        draw_arc_zone( 0.1,  1.0, ACCENT_TEAL)

        # --- Tick marks at -1, -0.5, 0, +0.5, +1 ---
        for tick_score in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            angle_rad = np.radians(score_to_deg(tick_score))
            x_out = r_outer * np.cos(angle_rad)
            y_out = r_outer * np.sin(angle_rad)
            x_in  = (r_outer + 0.07) * np.cos(angle_rad)
            y_in  = (r_outer + 0.07) * np.sin(angle_rad)
            ax.plot([x_out, x_in], [y_out, y_in],
                    color='white', linewidth=1.5, zorder=5)

        # --- Needle ---
        needle_angle_rad = np.radians(score_to_deg(score))
        needle_len = 0.82
        nx = needle_len * np.cos(needle_angle_rad)
        ny = needle_len * np.sin(needle_angle_rad)
        ax.annotate('',
                    xy=(nx, ny),
                    xytext=(0, 0),
                    arrowprops=dict(arrowstyle='->', color='white',
                                   lw=2.5, mutation_scale=14))

        # Center cap
        cap = plt.Circle((0, 0), 0.07, color=BG_CARD, zorder=10)
        ax.add_patch(cap)
        cap_inner = plt.Circle((0, 0), 0.04, color=TEXT_PRIMARY, zorder=11)
        ax.add_patch(cap_inner)

        # --- Zone labels ---
        ax.text(-1.15, 0.08, "BEARISH", ha='center', va='bottom',
                color=ACCENT_RED, fontsize=6.5, fontweight='bold', rotation=0)
        ax.text(0.0, 1.18, "NEUTRAL", ha='center', va='bottom',
                color=TEXT_SECONDARY, fontsize=6.5, fontweight='bold')
        ax.text(1.15, 0.08, "BULLISH", ha='center', va='bottom',
                color=ACCENT_TEAL, fontsize=6.5, fontweight='bold')

        # --- Score and label text ---
        score_color = ACCENT_TEAL if score > 0.1 else (ACCENT_RED if score < -0.1 else TEXT_SECONDARY)
        ax.text(0, -0.18, f"{score:+.2f}", ha='center', va='center',
                color=score_color, fontsize=16, fontweight='bold')
        ax.text(0, -0.38, label.upper(), ha='center', va='center',
                color=score_color, fontsize=8.5, fontweight='bold',
                fontstyle='italic')

        ax.set_xlim(-1.45, 1.45)
        ax.set_ylim(-0.55, 1.35)

        fig.tight_layout(pad=0.3)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 2. Sentiment Breakdown Chart
# ---------------------------------------------------------------------------

def sentiment_breakdown_chart(news: float, freight: float, macro: float, alpha: float,
                               w_in: float = 6, h_in: float = 3) -> bytes:
    """
    Horizontal bar chart showing 4 sentiment components, each -1 to +1.
    Returns PNG bytes.
    """
    try:
        components = [
            ("News Sentiment",    news),
            ("Freight Momentum",  freight),
            ("Macro Score",       macro),
            ("Alpha Score",       alpha),
        ]

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_MID)

        labels = [c[0] for c in components]
        values = [float(c[1]) if c[1] is not None else 0.0 for c in components]
        colors = [ACCENT_TEAL if v >= 0 else ACCENT_RED for v in values]

        y_pos = np.arange(len(labels))
        bars = ax.barh(y_pos, values, color=colors, height=0.45,
                       edgecolor=BG_DARK, linewidth=0.8, zorder=3)

        # Value labels at end of bars
        for bar, val in zip(bars, values):
            x_offset = 0.04 if val >= 0 else -0.04
            ha = 'left' if val >= 0 else 'right'
            ax.text(val + x_offset, bar.get_y() + bar.get_height() / 2,
                    f"{val:+.2f}", va='center', ha=ha,
                    color=TEXT_PRIMARY, fontsize=8.5, fontweight='bold')

        # Reference line at 0
        ax.axvline(0, color=TEXT_SECONDARY, linewidth=1.0, linestyle='--', alpha=0.6, zorder=4)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT_PRIMARY, fontsize=9)
        ax.set_xlim(-1.35, 1.35)
        ax.set_xlabel("Sentiment Score", color=TEXT_SECONDARY, fontsize=8)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax.spines['bottom'].set_color(GRID_LINE)
        ax.spines['left'].set_color(GRID_LINE)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='x', color=GRID_LINE, linewidth=0.5, alpha=0.6)
        ax.set_title("SENTIMENT COMPONENT BREAKDOWN",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.6)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 3. Freight Rates Chart
# ---------------------------------------------------------------------------

def freight_rates_chart(routes: list, w_in: float = 8, h_in: float = 5) -> bytes:
    """
    Horizontal bar chart of freight rate 30-day % changes by route.
    routes: list of dicts with keys route_id (str), change_pct (float), rate (float, optional)
    Returns PNG bytes.
    """
    try:
        if not routes:
            return _placeholder("No Route Data Available", w_in, h_in)

        # Sort by abs(change_pct) desc, take top 12
        valid = [r for r in routes if r.get('route_id') and r.get('change_pct') is not None]
        valid.sort(key=lambda r: abs(float(r['change_pct'])), reverse=True)
        valid = valid[:12]

        if not valid:
            return _placeholder("No Route Data Available", w_in, h_in)

        labels   = [str(r['route_id'])[:25] for r in valid]
        changes  = [float(r['change_pct']) for r in valid]
        rates    = [r.get('rate') for r in valid]
        colors   = [ACCENT_TEAL if c >= 0 else ACCENT_RED for c in changes]

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_MID)

        y_pos = np.arange(len(labels))
        bars = ax.barh(y_pos, changes, color=colors, height=0.55,
                       edgecolor=BG_DARK, linewidth=0.8, zorder=3)

        # Percentage labels at bar ends
        x_max = max(abs(c) for c in changes) if changes else 1.0
        for i, (bar, chg, rate) in enumerate(zip(bars, changes, rates)):
            x_off = x_max * 0.025
            ha = 'left' if chg >= 0 else 'right'
            sign_off = x_off if chg >= 0 else -x_off
            label_txt = f"{chg:+.1f}%"
            if rate is not None:
                label_txt += f"  ${float(rate):,.0f}"
            ax.text(chg + sign_off, bar.get_y() + bar.get_height() / 2,
                    label_txt, va='center', ha=ha,
                    color=TEXT_PRIMARY, fontsize=7.5, fontweight='bold')

        # Reference line at 0
        ax.axvline(0, color=TEXT_SECONDARY, linewidth=1.0, linestyle='--', alpha=0.6, zorder=4)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT_PRIMARY, fontsize=8)
        ax.set_xlabel("30-Day Change (%)", color=TEXT_SECONDARY, fontsize=8)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax.spines['bottom'].set_color(GRID_LINE)
        ax.spines['left'].set_color(GRID_LINE)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='x', color=GRID_LINE, linewidth=0.5, alpha=0.6)
        ax.set_title("FREIGHT RATE MOMENTUM (30-DAY CHANGE)",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 4. Alpha Signals Chart
# ---------------------------------------------------------------------------

def alpha_signals_chart(signals: list, w_in: float = 8, h_in: float = 5) -> bytes:
    """
    Bubble scatter chart of alpha signals.
    signals: list of dicts with keys: ticker, strength (0-1), expected_return_pct, direction
    direction: 'LONG' | 'SHORT' | 'NEUTRAL'
    Returns PNG bytes.
    """
    try:
        if not signals:
            return _placeholder("No Alpha Signals Available", w_in, h_in)

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_MID)

        dir_color = {
            'LONG':    ACCENT_TEAL,
            'SHORT':   ACCENT_RED,
            'NEUTRAL': ACCENT_AMBER,
        }
        plotted_dirs = set()

        for sig in signals:
            ticker = str(sig.get('ticker', '?'))
            strength = float(sig.get('strength', 0.5))
            ret_pct  = float(sig.get('expected_return_pct', 0.0))
            direction = str(sig.get('direction', 'NEUTRAL')).upper()
            color = dir_color.get(direction, ACCENT_AMBER)
            size  = max(50, min(400, abs(ret_pct) * 35 + 60))

            ax.scatter(strength, ret_pct, s=size, c=color, alpha=0.85,
                       edgecolors=BG_DARK, linewidths=0.8, zorder=4)
            ax.text(strength, ret_pct, ticker,
                    ha='center', va='center',
                    color=BG_DARK, fontsize=6.5, fontweight='bold', zorder=5)
            plotted_dirs.add(direction)

        # Quadrant reference lines
        ax.axvline(0.5, color=GRID_LINE, linewidth=0.8, linestyle='--', alpha=0.7, zorder=2)
        ax.axhline(0.0, color=GRID_LINE, linewidth=0.8, linestyle='--', alpha=0.7, zorder=2)

        # Legend
        legend_patches = []
        for d in ['LONG', 'SHORT', 'NEUTRAL']:
            if d in plotted_dirs:
                legend_patches.append(
                    mpatches.Patch(color=dir_color[d], label=d)
                )
        if legend_patches:
            leg = ax.legend(handles=legend_patches, loc='lower right',
                            framealpha=0.25, facecolor=BG_CARD,
                            edgecolor=GRID_LINE, labelcolor=TEXT_PRIMARY,
                            fontsize=8)

        ax.set_xlabel("Signal Strength", color=TEXT_SECONDARY, fontsize=8)
        ax.set_ylabel("Expected Return (%)", color=TEXT_SECONDARY, fontsize=8)
        ax.set_xlim(-0.05, 1.10)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax.spines['bottom'].set_color(GRID_LINE)
        ax.spines['left'].set_color(GRID_LINE)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='both', color=GRID_LINE, linewidth=0.4, alpha=0.5)
        ax.set_title("ALPHA SIGNAL LANDSCAPE",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 5. Stock Performance Chart
# ---------------------------------------------------------------------------

def stock_performance_chart(tickers: list, prices: dict, changes_30d: dict,
                             w_in: float = 7, h_in: float = 4) -> bytes:
    """
    Grouped bar chart: 30d return % (bars) + price ($) as right-axis line/diamonds.
    Returns PNG bytes.
    """
    try:
        if not tickers:
            return _placeholder("No Equity Data Available", w_in, h_in)

        valid_tickers = [t for t in tickers
                         if t in changes_30d and changes_30d[t] is not None]
        if not valid_tickers:
            return _placeholder("No Equity Data Available", w_in, h_in)

        returns = [float(changes_30d[t]) for t in valid_tickers]
        bar_colors = [ACCENT_TEAL if r >= 0 else ACCENT_RED for r in returns]
        x = np.arange(len(valid_tickers))

        fig, ax1 = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax1.set_facecolor(BG_MID)

        bars = ax1.bar(x, returns, color=bar_colors, width=0.45,
                       edgecolor=BG_DARK, linewidth=0.8, zorder=3)

        # Return labels on bars
        for bar, ret in zip(bars, returns):
            y_off = 0.15 if ret >= 0 else -0.15
            va    = 'bottom' if ret >= 0 else 'top'
            ax1.text(bar.get_x() + bar.get_width() / 2, ret + y_off,
                     f"{ret:+.1f}%", ha='center', va=va,
                     color=TEXT_PRIMARY, fontsize=7.5, fontweight='bold')

        ax1.axhline(0, color=TEXT_SECONDARY, linewidth=0.8, linestyle='--', alpha=0.5)
        ax1.set_ylabel("30D Return (%)", color=TEXT_SECONDARY, fontsize=8)
        ax1.set_xticks(x)
        ax1.set_xticklabels(valid_tickers, color=TEXT_PRIMARY, fontsize=9, fontweight='bold')
        ax1.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax1.spines['bottom'].set_color(GRID_LINE)
        ax1.spines['left'].set_color(GRID_LINE)
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_color(GRID_LINE)
        ax1.grid(axis='y', color=GRID_LINE, linewidth=0.5, alpha=0.6, zorder=0)

        # Right axis: prices
        price_vals = [float(prices[t]) if t in prices and prices[t] is not None else None
                      for t in valid_tickers]
        valid_price_idx = [i for i, p in enumerate(price_vals) if p is not None]

        if valid_price_idx:
            ax2 = ax1.twinx()
            ax2.set_facecolor('none')
            px = np.array(valid_price_idx)
            py = np.array([price_vals[i] for i in valid_price_idx])
            ax2.plot(px, py, color=ACCENT_GOLD, linewidth=1.5,
                     linestyle='--', alpha=0.85, zorder=4)
            ax2.scatter(px, py, color=ACCENT_GOLD, marker='D', s=45,
                        zorder=5, edgecolors=BG_DARK, linewidths=0.8)
            ax2.set_ylabel("Price (USD)", color=ACCENT_GOLD, fontsize=8)
            ax2.tick_params(colors=ACCENT_GOLD, labelsize=7.5)
            ax2.spines['right'].set_color(GRID_LINE)
            ax2.spines['top'].set_visible(False)
            ax2.spines['left'].set_visible(False)
            ax2.spines['bottom'].set_visible(False)

        ax1.set_title("SHIPPING EQUITY PERFORMANCE (30D)",
                      color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 6. Macro Snapshot Chart
# ---------------------------------------------------------------------------

def macro_snapshot_chart(bdi: float, bdi_chg: float, wti: float, wti_chg: float,
                          treasury: float, pmi: float,
                          w_in: float = 8, h_in: float = 3) -> bytes:
    """
    Six KPI boxes in a 2x3 grid using matplotlib text/patches.
    Returns PNG bytes.
    """
    try:
        fig = plt.figure(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)

        kpis = [
            {
                "label": "BALTIC DRY INDEX",
                "value": f"{float(bdi):,.0f}" if bdi is not None else "N/A",
                "change": bdi_chg,
                "change_fmt": f"{float(bdi_chg):+.1f}%" if bdi_chg is not None else "",
                "favorable": bdi_chg > 0 if bdi_chg is not None else None,
            },
            {
                "label": "WTI CRUDE ($/bbl)",
                "value": f"${float(wti):.2f}" if wti is not None else "N/A",
                "change": wti_chg,
                "change_fmt": f"{float(wti_chg):+.1f}%" if wti_chg is not None else "",
                # Lower oil = favorable for shipping costs
                "favorable": wti_chg < 0 if wti_chg is not None else None,
            },
            {
                "label": "10Y TREASURY YIELD",
                "value": f"{float(treasury):.2f}%" if treasury is not None else "N/A",
                "change": None,
                "change_fmt": "10Y UST",
                # Low yield = more favorable equity backdrop
                "favorable": float(treasury) < 4.5 if treasury is not None else None,
            },
            {
                "label": "PMI PROXY",
                "value": f"{float(pmi):.1f}" if pmi is not None else "N/A",
                "change": None,
                "change_fmt": "Global Mfg PMI",
                "favorable": float(pmi) >= 50.0 if pmi is not None else None,
            },
            {
                "label": "SUPPLY CHAIN",
                "value": "STRESS" if wti is not None and float(wti) > 80 else "NORMAL",
                "change": None,
                "change_fmt": "Composite",
                "favorable": not (wti is not None and float(wti) > 80),
            },
            {
                "label": "MARKET REGIME",
                "value": "RISK-ON" if pmi is not None and float(pmi) >= 50 else "RISK-OFF",
                "change": None,
                "change_fmt": "Current",
                "favorable": pmi is not None and float(pmi) >= 50,
            },
        ]

        # Layout: 2 rows x 3 cols, absolute positioning
        cols, rows = 3, 2
        box_w = 1.0 / cols
        box_h = 1.0 / rows
        pad_x, pad_y = 0.012, 0.022

        for i, kpi in enumerate(kpis):
            col = i % cols
            row = i // cols
            # (left, bottom, width, height) in figure coordinates
            left   = col * box_w + pad_x
            bottom = (1 - (row + 1) * box_h) + pad_y
            width  = box_w - 2 * pad_x
            height = box_h - 2 * pad_y

            # Background rectangle
            rect = mpatches.FancyBboxPatch(
                (left, bottom), width, height,
                boxstyle="round,pad=0.01",
                facecolor=BG_CARD,
                edgecolor=GRID_LINE,
                linewidth=0.8,
                transform=fig.transFigure,
                clip_on=False,
                zorder=2,
            )
            fig.add_artist(rect)

            cx = left + width / 2
            cy_label = bottom + height * 0.82
            cy_value = bottom + height * 0.47
            cy_change = bottom + height * 0.14

            fig.text(cx, cy_label, kpi["label"],
                     ha='center', va='center',
                     color=TEXT_SECONDARY, fontsize=6.5, fontweight='bold',
                     transform=fig.transFigure, zorder=3)

            fig.text(cx, cy_value, kpi["value"],
                     ha='center', va='center',
                     color=TEXT_PRIMARY, fontsize=14, fontweight='bold',
                     transform=fig.transFigure, zorder=3)

            chg_color = (ACCENT_TEAL if kpi["favorable"] is True
                         else ACCENT_RED if kpi["favorable"] is False
                         else TEXT_SECONDARY)
            arrow = ""
            if kpi["change"] is not None:
                arrow = " ▲" if float(kpi["change"]) > 0 else " ▼"
            change_text = (kpi["change_fmt"] or "") + arrow

            fig.text(cx, cy_change, change_text,
                     ha='center', va='center',
                     color=chg_color, fontsize=7, fontweight='bold',
                     transform=fig.transFigure, zorder=3)

        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 7. Portfolio Allocation Chart
# ---------------------------------------------------------------------------

def portfolio_allocation_chart(weights: dict, w_in: float = 5, h_in: float = 5) -> bytes:
    """
    Donut chart showing portfolio weights. Handles negative weights (shown as abs + SHORT label).
    Returns PNG bytes.
    """
    try:
        if not weights:
            return _placeholder("No Portfolio Data", w_in, h_in)

        TICKER_COLORS = {
            "ZIM":  ACCENT_GOLD,
            "MATX": ACCENT_BLUE,
            "SBLK": ACCENT_TEAL,
            "DAC":  ACCENT_RED,
            "CMRE": "#9B59B6",
        }
        fallback_colors = [
            "#5D6D7E", "#A569BD", "#45B39D", "#EC7063",
            "#F4D03F", "#85C1E9", "#82E0AA",
        ]

        labels, sizes, colors, display_labels = [], [], [], []
        fallback_idx = 0
        for i, (ticker, w) in enumerate(weights.items()):
            if w is None:
                continue
            w_val = float(w)
            ticker_up = str(ticker).upper()
            sizes.append(abs(w_val))
            labels.append(ticker_up)
            suffix = " (S)" if w_val < 0 else ""
            display_labels.append(f"{ticker_up}{suffix}")
            color = TICKER_COLORS.get(ticker_up,
                                      fallback_colors[fallback_idx % len(fallback_colors)])
            if ticker_up not in TICKER_COLORS:
                fallback_idx += 1
            colors.append(color)

        if not sizes or sum(sizes) == 0:
            return _placeholder("No Portfolio Data", w_in, h_in)

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_DARK)

        wedges, _ = ax.pie(
            sizes,
            labels=None,
            colors=colors,
            startangle=90,
            wedgeprops=dict(width=0.35, edgecolor=BG_DARK, linewidth=1.5),
            counterclock=False,
        )

        # Center text
        ax.text(0, 0.08, "PORTFOLIO", ha='center', va='center',
                color=TEXT_PRIMARY, fontsize=8.5, fontweight='bold')
        ax.text(0, -0.12, "ALLOCATION", ha='center', va='center',
                color=TEXT_SECONDARY, fontsize=7.5)

        # Legend
        total = sum(sizes)
        legend_handles = []
        for lbl, sz, col in zip(display_labels, sizes, colors):
            pct = sz / total * 100
            legend_handles.append(
                mpatches.Patch(color=col, label=f"{lbl}  {pct:.1f}%")
            )
        leg = ax.legend(handles=legend_handles, loc='lower center',
                        bbox_to_anchor=(0.5, -0.18),
                        ncol=min(3, len(legend_handles)),
                        framealpha=0.2, facecolor=BG_CARD,
                        edgecolor=GRID_LINE, labelcolor=TEXT_PRIMARY,
                        fontsize=8)

        ax.set_title("SIGNAL-WEIGHTED PORTFOLIO",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.5)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 8. Conviction Breakdown Chart
# ---------------------------------------------------------------------------

def conviction_breakdown_chart(by_conviction: dict, by_type: dict,
                                w_in: float = 8, h_in: float = 4) -> bytes:
    """
    Side-by-side horizontal bar charts: conviction level breakdown + signal type breakdown.
    Returns PNG bytes.
    """
    try:
        fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)

        for ax in (ax_left, ax_right):
            ax.set_facecolor(BG_MID)
            ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
            ax.spines['bottom'].set_color(GRID_LINE)
            ax.spines['left'].set_color(GRID_LINE)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.grid(axis='x', color=GRID_LINE, linewidth=0.5, alpha=0.6)

        # --- Left: conviction ---
        conviction_order = ["HIGH", "MEDIUM", "LOW"]
        conviction_colors = {"HIGH": ACCENT_TEAL, "MEDIUM": ACCENT_AMBER, "LOW": ACCENT_RED}

        conv_labels, conv_vals, conv_colors = [], [], []
        if by_conviction:
            for k in conviction_order:
                if k in by_conviction:
                    conv_labels.append(k)
                    conv_vals.append(int(by_conviction[k]))
                    conv_colors.append(conviction_colors.get(k, TEXT_SECONDARY))

        if not conv_labels:
            ax_left.text(0.5, 0.5, "No Data", ha='center', va='center',
                         color=TEXT_SECONDARY, transform=ax_left.transAxes)
        else:
            y = np.arange(len(conv_labels))
            bars = ax_left.barh(y, conv_vals, color=conv_colors, height=0.45,
                                edgecolor=BG_DARK, linewidth=0.8, zorder=3)
            for bar, val in zip(bars, conv_vals):
                ax_left.text(val + max(conv_vals) * 0.03,
                             bar.get_y() + bar.get_height() / 2,
                             str(val), va='center', ha='left',
                             color=TEXT_PRIMARY, fontsize=8.5, fontweight='bold')
            ax_left.set_yticks(y)
            ax_left.set_yticklabels(conv_labels, color=TEXT_PRIMARY, fontsize=9)
            ax_left.set_xlabel("Signal Count", color=TEXT_SECONDARY, fontsize=8)

        ax_left.set_title("BY CONVICTION", color=TEXT_PRIMARY, fontsize=8.5,
                           fontweight='bold', pad=8)

        # --- Right: signal type ---
        type_colors_list = [
            "#2E86C1",  # MOMENTUM — steel blue
            "#8E44AD",  # MEAN_REVERSION — purple
            "#1ABC9C",  # FUNDAMENTAL — teal
            "#C9A84C",  # MACRO — gold
            "#5D6D7E",  # TECHNICAL — slate
        ]
        type_labels, type_vals, type_clrs = [], [], []
        if by_type:
            for i, (k, v) in enumerate(by_type.items()):
                type_labels.append(str(k))
                type_vals.append(int(v))
                type_clrs.append(type_colors_list[i % len(type_colors_list)])

        if not type_labels:
            ax_right.text(0.5, 0.5, "No Data", ha='center', va='center',
                          color=TEXT_SECONDARY, transform=ax_right.transAxes)
        else:
            y = np.arange(len(type_labels))
            bars = ax_right.barh(y, type_vals, color=type_clrs, height=0.45,
                                  edgecolor=BG_DARK, linewidth=0.8, zorder=3)
            for bar, val in zip(bars, type_vals):
                ax_right.text(val + max(type_vals) * 0.03,
                              bar.get_y() + bar.get_height() / 2,
                              str(val), va='center', ha='left',
                              color=TEXT_PRIMARY, fontsize=8.5, fontweight='bold')
            ax_right.set_yticks(y)
            ax_right.set_yticklabels(type_labels, color=TEXT_PRIMARY, fontsize=8)
            ax_right.set_xlabel("Signal Count", color=TEXT_SECONDARY, fontsize=8)

        ax_right.set_title("BY SIGNAL TYPE", color=TEXT_PRIMARY, fontsize=8.5,
                            fontweight='bold', pad=8)

        fig.suptitle("ALPHA SIGNAL QUALITY BREAKDOWN",
                     color=TEXT_PRIMARY, fontsize=9.5, fontweight='bold', y=1.02)
        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 9. Risk Matrix Chart
# ---------------------------------------------------------------------------

def risk_matrix_chart(insights: list, w_in: float = 7, h_in: float = 5) -> bytes:
    """
    Scatter risk/opportunity matrix. Each insight plotted by score vs impact.
    insights: list of dicts with keys: title, score, category,
              ports_involved (list), routes_involved (list), stocks_potentially_affected (list)
    Returns PNG bytes.
    """
    try:
        if not insights:
            return _placeholder("No Insights Available", w_in, h_in)

        cat_colors = {
            "CONVERGENCE":  "#8E44AD",
            "ROUTE":        ACCENT_BLUE,
            "PORT_DEMAND":  ACCENT_TEAL,
            "MACRO":        ACCENT_GOLD,
        }
        fallback = TEXT_SECONDARY

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_MID)

        # Compute impact scores (raw count, then normalize)
        raw_impacts = []
        for ins in insights:
            ports   = len(ins.get('ports_involved', []) or [])
            routes  = len(ins.get('routes_involved', []) or [])
            stocks  = len(ins.get('stocks_potentially_affected', []) or [])
            raw_impacts.append(ports + routes + stocks)

        max_impact = max(raw_impacts) if max(raw_impacts) > 0 else 1

        plotted_cats = set()
        for ins, raw_imp in zip(insights, raw_impacts):
            score    = float(ins.get('score', 0.5))
            impact   = raw_imp / max_impact
            cat      = str(ins.get('category', '')).upper()
            color    = cat_colors.get(cat, fallback)
            title    = str(ins.get('title', ''))[:20]

            ax.scatter(score, impact, s=120, c=color, alpha=0.85,
                       edgecolors=BG_DARK, linewidths=0.8, zorder=4)
            ax.text(score, impact + 0.035, title,
                    ha='center', va='bottom',
                    color=TEXT_PRIMARY, fontsize=6.5, zorder=5)
            plotted_cats.add(cat)

        # Quadrant reference lines
        ax.axvline(0.5, color=GRID_LINE, linewidth=0.9, linestyle='--', alpha=0.8, zorder=2)
        ax.axhline(0.5, color=GRID_LINE, linewidth=0.9, linestyle='--', alpha=0.8, zorder=2)

        # Quadrant labels
        quad_kw = dict(color=TEXT_SECONDARY, fontsize=7, fontstyle='italic', alpha=0.7)
        ax.text(0.25, 0.95, "Monitor",      ha='center', va='top', transform=ax.transAxes, **quad_kw)
        ax.text(0.75, 0.95, "Prioritize",   ha='center', va='top', transform=ax.transAxes, **quad_kw)
        ax.text(0.25, 0.05, "Low Priority", ha='center', va='bottom', transform=ax.transAxes, **quad_kw)
        ax.text(0.75, 0.05, "Watch",        ha='center', va='bottom', transform=ax.transAxes, **quad_kw)

        # Legend
        legend_patches = []
        for cat, col in cat_colors.items():
            if cat in plotted_cats:
                legend_patches.append(mpatches.Patch(color=col, label=cat))
        if legend_patches:
            ax.legend(handles=legend_patches, loc='upper left',
                      framealpha=0.25, facecolor=BG_CARD,
                      edgecolor=GRID_LINE, labelcolor=TEXT_PRIMARY, fontsize=7.5)

        ax.set_xlabel("Opportunity Score", color=TEXT_SECONDARY, fontsize=8)
        ax.set_ylabel("Impact", color=TEXT_SECONDARY, fontsize=8)
        ax.set_xlim(-0.05, 1.10)
        ax.set_ylim(-0.05, 1.15)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax.spines['bottom'].set_color(GRID_LINE)
        ax.spines['left'].set_color(GRID_LINE)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(color=GRID_LINE, linewidth=0.4, alpha=0.5)
        ax.set_title("RISK / OPPORTUNITY MATRIX",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)


# ---------------------------------------------------------------------------
# 10. News Topic Chart
# ---------------------------------------------------------------------------

def news_topic_chart(topic_breakdown: dict, w_in: float = 6, h_in: float = 4) -> bytes:
    """
    Horizontal bar chart of news topics with sentiment-based coloring.
    topic_breakdown: dict mapping topic_name -> {"count": int, "avg_sentiment": float}
    Returns PNG bytes.
    """
    try:
        if not topic_breakdown:
            return _placeholder("No News Data Available", w_in, h_in)

        topics, counts, sentiments = [], [], []
        for topic, data in topic_breakdown.items():
            if data is None:
                continue
            if isinstance(data, dict):
                count = int(data.get('count', 0))
                avg_sent = float(data.get('avg_sentiment', 0.0))
            else:
                # bare number treated as count
                count = int(data)
                avg_sent = 0.0
            topics.append(str(topic))
            counts.append(count)
            sentiments.append(avg_sent)

        if not topics:
            return _placeholder("No News Data Available", w_in, h_in)

        # Sort by count descending
        order = sorted(range(len(topics)), key=lambda i: counts[i], reverse=True)
        topics    = [topics[i] for i in order]
        counts    = [counts[i] for i in order]
        sentiments= [sentiments[i] for i in order]

        colors = []
        for s in sentiments:
            if s > 0.1:
                colors.append(ACCENT_TEAL)
            elif s < -0.1:
                colors.append(ACCENT_RED)
            else:
                colors.append(TEXT_SECONDARY)

        fig, ax = plt.subplots(figsize=(w_in, h_in))
        fig.patch.set_facecolor(BG_DARK)
        ax.set_facecolor(BG_MID)

        y_pos = np.arange(len(topics))
        bars = ax.barh(y_pos, counts, color=colors, height=0.50,
                       edgecolor=BG_DARK, linewidth=0.8, zorder=3)

        max_count = max(counts) if counts else 1
        for bar, cnt, sent in zip(bars, counts, sentiments):
            label_txt = f"{cnt}  (sent: {sent:+.2f})"
            ax.text(cnt + max_count * 0.02,
                    bar.get_y() + bar.get_height() / 2,
                    label_txt, va='center', ha='left',
                    color=TEXT_PRIMARY, fontsize=7.5)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(topics, color=TEXT_PRIMARY, fontsize=8.5)
        ax.set_xlabel("Article Count", color=TEXT_SECONDARY, fontsize=8)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
        ax.spines['bottom'].set_color(GRID_LINE)
        ax.spines['left'].set_color(GRID_LINE)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='x', color=GRID_LINE, linewidth=0.5, alpha=0.6)
        ax.set_title("NEWS COVERAGE BY TOPIC",
                     color=TEXT_PRIMARY, fontsize=9, fontweight='bold', pad=10)

        fig.tight_layout(pad=0.7)
        return _fig_to_bytes(fig)

    except Exception:
        return _placeholder("Insufficient Data", w_in, h_in)
