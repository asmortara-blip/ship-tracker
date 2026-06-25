"""Slide 02 — The Problem. Shipping disruption is mispriced risk.
Infographic: LEFT chokepoint trade-share hbars; RIGHT-TOP recent real disruption
stats; RIGHT-BOTTOM the gap note panel.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/"
       "Pitch Deck (v2)/02-problem.pdf")

fig, ax = D.slide(
    2, "the opportunity", "Shipping disruption is mispriced risk",
    "Seaborne chokepoints move equities and supply chains — yet the risk is "
    "asserted, not tested.",
)

# ── LEFT: chokepoint trade-share horizontal bars ─────────────────────────────
# Representative integers parsed from MARKET CONTEXT (illustrative · public).
# Share of global trade-by-volume (Hormuz/Malacca quoted on seaborne-oil flows).
labels = [
    "Strait of Malacca",
    "Strait of Hormuz",
    "Bab-el-Mandeb",
    "Suez Canal",
    "Panama Canal",
]
values = [25, 20, 13, 12, 6]   # %  (Malacca ~1/4 goods; Hormuz ~oil; etc.)
# BRASS for the top bar (the largest share / hero), STEEL for the rest.
colors = [D.BRASS, D.STEEL, D.STEEL, D.STEEL, D.STEEL]

# Panel backing the chart for visual containment.
D.panel(ax, 4.0, 8.0, 47.0, 67.0, fc=D.CARD, ec=D.LINE)
ax.text(6.0, 72.4, "CHOKEPOINT SHARE OF GLOBAL TRADE",
        color=D.BRASS, fontsize=9.6, fontweight="bold", va="top", ha="left")
ax.text(6.0, 69.6, "(illustrative · public sources)",
        color=D.SLATE, fontsize=8.0, va="top", ha="left")

iax = D.hbars(
    fig, [0.105, 0.16, 0.345, 0.42],
    labels, values, colors=colors, vmax=30, fmt="{:.0f}%",
    label_size=9.4, val_size=9.6,
)

# Sub-note under the bars (metric clarification + provenance).
ax.text(6.0, 13.2,
        "Share of world trade / seaborne-oil flows by volume.",
        color=D.SLATE, fontsize=7.8, va="top", ha="left")
ax.text(6.0, 10.6,
        "EIA · IMF · World Bank · UNCTAD — illustrative · public sources.",
        color=D.SLATE, fontsize=7.4, va="top", ha="left", style="italic")

# ── RIGHT TOP: recent real disruption stat callouts ──────────────────────────
ax.text(54.5, 75.5, "RECENT REAL DISRUPTIONS",
        color=D.BRASS, fontsize=9.6, fontweight="bold", va="top", ha="left")

# Three stat callouts in a row, each in its own light card.
cards = [
    (54.5, "~90%", "Suez container", "traffic drop, 2024", "Red Sea / Houthi crisis"),
    (69.0, "38 → 18", "Panama daily", "transits, drought", "2023-24 low Gatun Lake"),
    (83.5, "6 days", "Ever Given", "canal blockage", "~400 ships queued, 2021"),
]
CW, CH, CY = 13.2, 19.5, 51.0
for cx, val, l1, l2, sub in cards:
    D.panel(ax, cx, CY, CW, CH, fc="#FBF8EF", ec=D.BRASS, lw=1.1)
    ax.text(cx + CW / 2, CY + CH - 2.4, val, color=D.BRASS, fontsize=17,
            fontweight="bold", ha="center", va="top", family="DejaVu Sans",
            zorder=3)
    ax.text(cx + CW / 2, CY + CH - 8.6, l1, color=D.INK, fontsize=8.6,
            fontweight="bold", ha="center", va="top", zorder=3)
    ax.text(cx + CW / 2, CY + CH - 11.2, l2, color=D.INK, fontsize=8.6,
            fontweight="bold", ha="center", va="top", zorder=3)
    ax.text(cx + CW / 2, CY + 2.6, sub, color=D.SLATE, fontsize=7.0,
            ha="center", va="center", zorder=3, wrap=True)
ax.text(54.5, 49.4, "illustrative · public sources (IMF / World Bank / gCaptain)",
        color=D.SLATE, fontsize=7.2, va="top", ha="left", style="italic")

# ── RIGHT BOTTOM: "the gap" note panel ───────────────────────────────────────
D.note_panel(
    ax, 54.5, 8.0, 42.2, 37.0,
    "the gap",
    [
        "Chokepoint shocks move shipping\n  equities and physical supply chains —\n  a load-bearing macro input.",
        "Yet most ‘risk numbers’ are asserted,\n  never tested against realized P&L.",
        "Ship Tracker closes that gap: fuses\n  real disruption signals with coverage-\n  tested VaR / ES on realized P&L.",
    ],
    size=9.6, gap=9.6,
)

D.save(fig, OUT)

import os
sz = os.path.getsize(OUT)
print("wrote", OUT, sz, "bytes")
