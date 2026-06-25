"""Deck design system for the Ship Tracker capability/pitch deck (v2).

Builds on _style.py. Adds slide chrome (running mark + slide number + brass rule),
a dark-slide variant for visual rhythm, nautical motifs (graticule, compass rose,
depth contours), and infographic primitives (big-stat, KPI band, radial gauge,
funnel, hbar, panel). Letter-landscape vector PDF, one slide per page.

Circles/arcs (compass, gauge) are drawn on their OWN equal-aspect inset Axes so
they render perfectly round regardless of the page's x/y unit ratio.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/tmp/viz_ship")
import math
import _style as S
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Wedge, Circle, Polygon
from matplotlib.lines import Line2D

DECK_TOTAL = 11

# Re-export palette for convenience
INK, NAVY, HULL = S.INK, S.NAVY, "#0E2235"
STEEL, STEEL_LT, MIST = S.STEEL, S.STEEL_LT, S.MIST
SLATE, BRASS, BRASS_LT = S.SLATE, S.BRASS, S.BRASS_LT
PAPER, CARD, LINE = S.PAPER, S.CARD, S.LINE
GREEN, RED, AMBER = S.GREEN, S.RED, S.AMBER
PAPER_TX = "#EFEBE0"   # paper text-on-dark tint

LETTER = S.LETTER_LANDSCAPE


# ── motifs ───────────────────────────────────────────────────────────────────
def graticule(ax, *, color="#22506F", alpha=0.18, step=8.0, lw=0.6):
    """Faint lat/long grid across the whole 0..100 page (nautical chart feel)."""
    for x in np.arange(0, 100.01, step):
        ax.add_line(Line2D([x, x], [0, 100], color=color, lw=lw, alpha=alpha, zorder=0))
    for y in np.arange(0, 100.01, step):
        ax.add_line(Line2D([0, 100], [y, y], color=color, lw=lw, alpha=alpha, zorder=0))


def contours(ax, *, color="#2C5A7C", alpha=0.16, n=5, lw=1.0, y0=8, y1=58):
    """Smooth bathymetric depth-contour curves drifting across the lower page."""
    xs = np.linspace(0, 100, 240)
    for i in range(n):
        base = y0 + (y1 - y0) * i / max(1, n - 1)
        amp = 3.2 + 1.1 * i
        ys = base + amp * np.sin(xs / (13 + 2 * i) + i * 0.9)
        ax.plot(xs, ys, color=color, alpha=alpha, lw=lw, zorder=0)


def compass(fig, cx, cy, r, *, ring=STEEL, star=BRASS, ink=PAPER_TX, lw=1.4):
    """Minimal single-weight compass rose on an equal-aspect inset (fig fraction)."""
    iax = fig.add_axes([cx - r, cy - r, 2 * r, 2 * r]); iax.set_aspect("equal")
    iax.set_xlim(-1, 1); iax.set_ylim(-1, 1); iax.axis("off")
    iax.add_patch(Circle((0, 0), 0.96, fill=False, ec=ring, lw=lw, alpha=0.9))
    iax.add_patch(Circle((0, 0), 0.70, fill=False, ec=ring, lw=0.8, alpha=0.5))
    for ang, lng in [(90, 0.92), (0, 0.62), (270, 0.92), (180, 0.62),
                     (45, 0.5), (135, 0.5), (225, 0.5), (315, 0.5)]:
        a = math.radians(ang)
        x2, y2 = lng * math.cos(a), lng * math.sin(a)
        # diamond point
        w = 0.085
        px, py = -math.sin(a) * w, math.cos(a) * w
        col = star if ang in (90, 0, 270, 180) else ring
        iax.add_patch(Polygon([(0, 0), (x2 + px, y2 + py), (x2, y2),
                               (x2 - px, y2 - py)], closed=True,
                              facecolor=col, edgecolor=col, lw=0, alpha=0.92))
    iax.text(0, 1.02, "N", ha="center", va="bottom", color=star,
             fontsize=8.5, fontweight="bold")
    return iax


# ── slide chrome ─────────────────────────────────────────────────────────────
def _page(dark):
    fig = plt.figure(figsize=LETTER)
    bg = HULL if dark else PAPER
    fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    ax.add_patch(FancyBboxPatch((-2, -2), 104, 104, boxstyle="square,pad=0",
                                facecolor=bg, edgecolor="none", zorder=-5))
    return fig, ax


def chrome(ax, n, *, dark=False):
    """Running mark (top-left), slide number (top-right), brass top rule."""
    tx = PAPER_TX if dark else SLATE
    ax.add_line(Line2D([4, 96], [96.2, 96.2], color=BRASS, lw=2.2, zorder=4))
    ax.text(4, 97.4, "SHIP TRACKER", color=BRASS, fontsize=8.5, fontweight="bold",
            va="bottom", ha="left", zorder=5)
    ax.text(96, 97.4, f"{n:02d} / {DECK_TOTAL:02d}", color=tx, fontsize=8.5,
            fontweight="bold", va="bottom", ha="right", family="DejaVu Sans", zorder=5)
    ax.add_line(Line2D([4, 96], [3.4, 3.4], color=(LINE if not dark else "#2A3F54"),
                       lw=0.8, zorder=4))
    ax.text(4, 2.1, "Ship Tracker — Global Shipping Intelligence", color=tx,
            fontsize=7.2, va="center", ha="left", zorder=5)


def slide(n, kicker, title, subtitle=None, *, dark=False, title_size=27):
    """Standard content slide: chrome + kicker + big title + subtitle. Returns fig, ax."""
    fig, ax = _page(dark)
    chrome(ax, n, dark=dark)
    tk = BRASS
    tc = (PAPER_TX if dark else INK)
    sc = (MIST if dark else SLATE)
    ax.text(4, 91.5, kicker.upper(), color=tk, fontsize=10, fontweight="bold",
            va="top", ha="left")
    ax.text(4, 88.6, title, color=tc, fontsize=title_size, fontweight="bold",
            va="top", ha="left")
    if subtitle:
        ax.text(4, 80.5, subtitle, color=sc, fontsize=11.5, va="top", ha="left")
    return fig, ax


def cover(fig=None):
    """The deck cover — dark hull ground, graticule + contours + compass."""
    fig, ax = _page(dark=True)
    graticule(ax, alpha=0.15)
    contours(ax, alpha=0.13, n=6, y0=6, y1=46)
    ax.add_line(Line2D([8, 40], [70, 70], color=BRASS, lw=2.6))
    ax.text(8, 73.5, "INSTITUTIONAL SHIPPING INTELLIGENCE", color=BRASS,
            fontsize=12, fontweight="bold", va="bottom", ha="left")
    ax.text(8, 64, "Ship Tracker", color=PAPER_TX, fontsize=58, fontweight="bold",
            va="top", ha="left")
    ax.text(8, 47, "The risk you can actually back-test.", color=MIST,
            fontsize=22, va="top", ha="left", style="italic")
    ax.text(8, 38,
            "Real chokepoint transits, shipping-equity tape, and macro feeds →\n"
            "a stress index and coverage-tested VaR & Expected-Shortfall,\n"
            "validated against realized P&L and labelled real vs modeled.",
            color="#B9C8D6", fontsize=12.5, va="top", ha="left", linespacing=1.5)
    compass(fig, 0.80, 0.36, 0.11)
    ax.add_line(Line2D([8, 92], [10, 10], color="#2A3F54", lw=0.8))
    ax.text(8, 7.4, "Capability & Methodology Deck", color=MIST, fontsize=10,
            va="center", ha="left", fontweight="bold")
    ax.text(92, 7.4, f"{DECK_TOTAL} slides · vector PDF", color=SLATE, fontsize=8.5,
            va="center", ha="right")
    return fig, ax


def divider(n, kicker, title):
    """A dark section-divider style slide (used for the closing summary)."""
    fig, ax = slide(n, kicker, title, dark=True, title_size=30)
    contours(ax, alpha=0.12, n=5, y0=6, y1=40)
    return fig, ax


# ── infographic primitives ───────────────────────────────────────────────────
def panel(ax, x, y, w, h, *, title=None, fc=CARD, ec=LINE, lw=1.2, title_color=INK,
          dark=False, rounding=1.2):
    fc = fc if not dark else "#15293C"
    ec = ec if not dark else "#2E4860"
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle=f"round,pad=0.3,rounding_size={rounding}",
                 facecolor=fc, edgecolor=ec, lw=lw, zorder=2))
    if title:
        ax.text(x + 1.8, y + h - 2.0, title.upper(),
                color=(BRASS if not dark else BRASS), fontsize=9.2,
                fontweight="bold", va="top", ha="left", zorder=3)
    return (x, y, w, h)


def bullets(ax, x, y, lines, *, w=40, size=9.6, color=INK, gap=3.1, mark=STEEL,
            lh=1.3):
    ty = y
    for ln in lines:
        ax.text(x, ty, "▸", color=mark, fontsize=size * 0.9, va="top", ha="left", zorder=3)
        ax.text(x + 1.9, ty, ln, color=color, fontsize=size, va="top", ha="left",
                zorder=3, linespacing=lh, wrap=True)
        ty -= gap
    return ty


def note_panel(ax, x, y, w, h, title, lines, *, dark=False, size=9.4, gap=3.0):
    """Panel + heading + correctly-spaced bullets (avoids title/text overlap).
    Use this instead of panel()+bullets() for any 'How to read it' style box."""
    panel(ax, x, y, w, h, title=title, dark=dark)
    bullets(ax, x + 2.0, y + h - 4.6, lines, w=w - 4,
            color=(MIST if dark else INK), size=size, gap=gap)


def stat(ax, x, y, value, label, *, sub=None, big=26, color=None, lab_color=None,
         dark=False, ha="left"):
    color = color or (BRASS)
    lab_color = lab_color or (MIST if dark else SLATE)
    ax.text(x, y, value, color=color, fontsize=big, fontweight="bold",
            va="top", ha=ha, family="DejaVu Sans", zorder=3)
    ax.text(x, y - big * 0.40, label, color=(PAPER_TX if dark else INK),
            fontsize=9.5, fontweight="bold", va="top", ha=ha, zorder=3)
    if sub:
        ax.text(x, y - big * 0.40 - 3.0, sub, color=lab_color, fontsize=8.2,
                va="top", ha=ha, zorder=3)


def kpi_band(ax, y, items, *, x0=6, x1=94, dark=False, h=13):
    """A row of big-stat callouts with brass dividers between."""
    n = len(items)
    seg = (x1 - x0) / n
    for i, it in enumerate(items):
        cx = x0 + seg * i + 2.0
        stat(ax, cx, y, it[0], it[1], sub=it[2] if len(it) > 2 else None,
             big=22, dark=dark)
        if i > 0:
            ax.add_line(Line2D([x0 + seg * i, x0 + seg * i],
                               [y - h + 2, y + 1.5], color=BRASS, lw=1.2, alpha=0.7))


def hbars(fig, rect, labels, values, *, colors=None, vmax=None, fmt="{:.0f}",
          title=None, label_size=8.6, val_size=8.6, base=STEEL):
    """Horizontal bar chart on an inset Axes (rect = [l,b,w,h] fig fraction)."""
    iax = fig.add_axes(rect)
    n = len(values)
    ypos = np.arange(n)[::-1]
    colors = colors or [base] * n
    vmax = vmax or (max(values) * 1.18)
    iax.barh(ypos, values, color=colors, height=0.66, zorder=3)
    iax.set_xlim(0, vmax); iax.set_ylim(-0.6, n - 0.4)
    iax.set_yticks(ypos); iax.set_yticklabels(labels, fontsize=label_size, color=INK)
    iax.set_xticks([])
    for s in ("top", "right", "bottom"):
        iax.spines[s].set_visible(False)
    iax.spines["left"].set_color(LINE)
    iax.tick_params(length=0)
    for yp, v in zip(ypos, values):
        iax.text(v + vmax * 0.012, yp, fmt.format(v), va="center", ha="left",
                 fontsize=val_size, color=INK, fontweight="bold")
    if title:
        iax.set_title(title, fontsize=9.5, color=INK, loc="left", pad=6)
    return iax


def radial_gauge(fig, rect, value, *, vmin=0, vmax=100, label="", arc=STEEL,
                 needle=BRASS, zones=None):
    """A 180° radial gauge on an equal-aspect inset. value in [vmin,vmax]."""
    iax = fig.add_axes(rect); iax.set_aspect("equal")
    iax.set_xlim(-1.15, 1.15); iax.set_ylim(-0.25, 1.2); iax.axis("off")
    # zone arcs
    zones = zones or [(0, 40, GREEN), (40, 70, AMBER), (70, 100, RED)]
    for z0, z1, col in zones:
        a0 = 180 * (1 - (z0 - vmin) / (vmax - vmin))
        a1 = 180 * (1 - (z1 - vmin) / (vmax - vmin))
        iax.add_patch(Wedge((0, 0), 1.0, min(a0, a1), max(a0, a1), width=0.22,
                            facecolor=col, edgecolor="none", alpha=0.85))
    # needle
    frac = (value - vmin) / (vmax - vmin)
    ang = math.radians(180 * (1 - frac))
    iax.add_line(Line2D([0, 0.82 * math.cos(ang)], [0, 0.82 * math.sin(ang)],
                        color=needle, lw=3.2, solid_capstyle="round", zorder=5))
    iax.add_patch(Circle((0, 0), 0.06, facecolor=INK, edgecolor="none", zorder=6))
    iax.text(0, -0.16, f"{value:.0f}", ha="center", va="top", color=INK,
             fontsize=22, fontweight="bold")
    if label:
        iax.text(0, 1.12, label, ha="center", va="bottom", color=SLATE, fontsize=9)
    return iax


def funnel(ax, x, y, w, stages, *, h=11):
    """A left-to-right funnel: list of (value, label) with brass chevrons between."""
    n = len(stages)
    bw = (w - (n - 1) * 3.5) / n
    cx = x
    for i, (val, lab) in enumerate(stages):
        shade = 1 - 0.12 * i
        fc = BRASS_LT if i == n - 1 else MIST
        ec = BRASS if i == n - 1 else STEEL
        ax.add_patch(FancyBboxPatch((cx, y), bw, h, boxstyle="round,pad=0.2,rounding_size=0.8",
                     facecolor=fc, edgecolor=ec, lw=1.3, zorder=2))
        ax.text(cx + bw / 2, y + h - 2.5, str(val), ha="center", va="top",
                color=INK, fontsize=18, fontweight="bold", zorder=3)
        ax.text(cx + bw / 2, y + 2.3, lab, ha="center", va="center", color=SLATE,
                fontsize=8.2, zorder=3)
        if i < n - 1:
            mx = cx + bw + 1.75
            ax.text(mx, y + h / 2, "›", ha="center", va="center", color=BRASS,
                    fontsize=18, fontweight="bold", zorder=3)
        cx += bw + 3.5


def arrow(ax, p0, p1, *, color=STEEL, lw=1.7, mut=14, style="-|>", rad=0.0):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=mut,
                 color=color, lw=lw, zorder=2,
                 connectionstyle=f"arc3,rad={rad}"))


def chip(ax, x, y, text, *, fc=BRASS_LT, ec=BRASS, tc=INK, size=8.2, h=3.2):
    w = 1.05 * len(text) + 3
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1.6",
                 facecolor=fc, edgecolor=ec, lw=1.0, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc,
            fontsize=size, fontweight="bold", zorder=4)
    return w


def node(ax, x, y, w, h, title, *, body=None, fc=CARD, ec=STEEL, tc=INK,
         ts=10, bs=8.0, lw=1.4, dark=False):
    fc = fc if not dark else "#15293C"
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.45,rounding_size=1.1",
                 facecolor=fc, edgecolor=ec, lw=lw, zorder=2))
    if body:
        ax.text(x + w / 2, y + h - 1.9, title, ha="center", va="top", color=tc,
                fontsize=ts, fontweight="bold", zorder=3)
        ax.text(x + w / 2, y + h - 1.9 - ts * 0.34, body, ha="center", va="top",
                color=(MIST if dark else SLATE), fontsize=bs, zorder=3, linespacing=1.3)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", color=tc,
                fontsize=ts, fontweight="bold", zorder=3, linespacing=1.25)
    return (x + w / 2, y + h / 2)


def save(fig, path):
    fig.savefig(path, format="pdf", facecolor=fig.patch.get_facecolor())
    plt.close(fig)
    return path
