"""Shared design system for the Ship Tracker report visuals.

Institutional / nautical aesthetic: WSJ steel-blue identity + brass accent on warm
paper. Every visual imports these helpers so the five PDFs are visually consistent.
Vector PDF output (crisp when printed). DejaVu fonts (always present in matplotlib).
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

# ── Palette ──────────────────────────────────────────────────────────────────
INK      = "#0B1A2B"   # near-black navy ink (titles, text)
NAVY     = "#13283D"   # deep hull navy
STEEL    = "#2E5A7E"   # WSJ steel blue (primary)
STEEL_LT = "#7FA3C0"   # lighter steel (secondary fills)
MIST     = "#DCE6EF"   # pale steel wash (light box fill)
SLATE    = "#52606D"   # muted slate (sublabels)
BRASS    = "#B0892F"   # nautical brass / gold accent
BRASS_LT = "#E7D9B4"
PAPER    = "#F7F5EF"   # warm chart paper
CARD     = "#FFFFFF"
LINE     = "#C9D2DB"   # hairline rules
GREEN    = "#2F7D5B"   # pass / healthy
RED      = "#B23A3A"   # reject / fail
AMBER    = "#C98A21"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9.5,
    "axes.edgecolor": LINE,
    "axes.linewidth": 0.8,
    "figure.dpi": 150,
    "pdf.fonttype": 42,         # embed TrueType so text stays selectable/crisp
    "savefig.facecolor": PAPER,
})

LETTER_LANDSCAPE = (11.0, 8.5)
LETTER_PORTRAIT  = (8.5, 11.0)


def new_page(figsize=LETTER_LANDSCAPE):
    """Blank page: paper background, coordinate space 0..100 x, 0..100 y, no axes."""
    fig = plt.figure(figsize=figsize)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def header(ax, kicker, title, subtitle=None, *, y=95.5):
    """Top masthead: brass kicker rule + product kicker, bold title, subtitle."""
    ax.add_line(Line2D([4, 96], [y + 1.0, y + 1.0], color=BRASS, lw=2.4))
    ax.text(4, y - 0.2, kicker.upper(), color=BRASS, fontsize=9,
            fontweight="bold", va="top", ha="left", family="DejaVu Sans")
    ax.text(4, y - 2.6, title, color=INK, fontsize=19, fontweight="bold",
            va="top", ha="left")
    if subtitle:
        ax.text(4, y - 6.6, subtitle, color=SLATE, fontsize=10.5, va="top", ha="left")


def footer(ax, left="Ship Tracker — Global Shipping Intelligence", right=""):
    ax.add_line(Line2D([4, 96], [3.6, 3.6], color=LINE, lw=0.8))
    ax.text(4, 2.4, left, color=SLATE, fontsize=7.5, va="center", ha="left")
    if right:
        ax.text(96, 2.4, right, color=SLATE, fontsize=7.5, va="center", ha="right")


def box(ax, x, y, w, h, title, *, body=None, fc=CARD, ec=STEEL, tc=INK,
        title_size=10, body_size=8.2, lw=1.4, round_pad=0.55, title_weight="bold",
        align="center"):
    """Rounded node box with a bold title and optional body lines."""
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad={round_pad},rounding_size=1.2",
                       linewidth=lw, edgecolor=ec, facecolor=fc, zorder=2)
    ax.add_patch(p)
    ha = {"center": "center", "left": "left"}[align]
    tx = x + w / 2 if align == "center" else x + 1.6
    if body:
        ax.text(tx, y + h - 2.2, title, color=tc, fontsize=title_size,
                fontweight=title_weight, ha=ha, va="top", zorder=3)
        ax.text(tx, y + h - 2.2 - title_size * 0.34, body, color=SLATE,
                fontsize=body_size, ha=ha, va="top", zorder=3, linespacing=1.35)
    else:
        ax.text(tx, y + h / 2, title, color=tc, fontsize=title_size,
                fontweight=title_weight, ha=ha, va="center", zorder=3,
                linespacing=1.3)
    return (x + w / 2, y + h / 2)


def band(ax, x, y, w, h, label, *, fc=MIST, ec=STEEL, label_color=STEEL):
    """A wide background band that groups a layer/row, with a left-edge label."""
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=0.8",
                       linewidth=1.0, edgecolor=ec, facecolor=fc, zorder=1, alpha=0.55)
    ax.add_patch(p)
    ax.text(x + 0.9, y + h - 1.2, label.upper(), color=label_color, fontsize=8,
            fontweight="bold", rotation=0, va="top", ha="left", zorder=2)


def arrow(ax, start, end, *, color=STEEL, lw=1.6, style="-|>", mut=14, alpha=1.0,
          connection="arc3,rad=0.0"):
    a = FancyArrowPatch(start, end, arrowstyle=style, mutation_scale=mut,
                        color=color, lw=lw, zorder=2, alpha=alpha,
                        connectionstyle=connection)
    ax.add_patch(a)


def chip(ax, x, y, text, *, fc=BRASS_LT, ec=BRASS, tc=INK, size=8):
    """A small pill/badge."""
    w = 1.05 * len(text) + 3
    p = FancyBboxPatch((x, y), w, 3.0, boxstyle="round,pad=0.2,rounding_size=1.5",
                       linewidth=1.0, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + 1.5, text, color=tc, fontsize=size, fontweight="bold",
            ha="center", va="center", zorder=4)
    return w


def breakdown(ax, x, y, w, heading, lines, *, fc="#F0EFE8", ec=BRASS, h=None,
              body_size=8.4):
    """A 'How it works' explainer panel: heading + bulleted lines."""
    if h is None:
        h = 4.5 + 2.5 * len(lines)
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.0",
                       linewidth=1.2, edgecolor=ec, facecolor=fc, zorder=2)
    ax.add_patch(p)
    ax.text(x + 1.6, y + h - 1.8, heading.upper(), color=BRASS, fontsize=9,
            fontweight="bold", va="top", ha="left", zorder=3)
    ty = y + h - 5.0
    for ln in lines:
        ax.text(x + 1.9, ty, "▸", color=STEEL, fontsize=8.2, va="top", ha="left", zorder=3)
        ax.text(x + 3.6, ty, ln, color=INK, fontsize=body_size, va="top", ha="left",
                zorder=3, wrap=True, linespacing=1.3)
        ty -= 2.5
    return h


def save(fig, path):
    fig.savefig(path, format="pdf", facecolor=PAPER, bbox_inches=None)
    plt.close(fig)
    return path
