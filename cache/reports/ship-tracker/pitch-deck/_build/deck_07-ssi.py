"""Slide 07 / 11 — The Shipping Stress Index.

Composite gauge (illustrative) + six prominence-weighted component weights +
a "How to read it" note panel and a REAL+MODELED chip. Built on the verified
smoke layout, polished for an institutional pitch deck.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/"
       "Pitch Deck (v2)/07-ssi.pdf")

fig, ax = D.slide(
    7, "composite signal", "The Shipping Stress Index",
    "One gauge of global shipping stress, decomposed into six "
    "prominence-weighted components.",
)

# ── LEFT: the SSI gauge (illustrative value) ─────────────────────────────────
# Equal-aspect inset; 180° gauge with the default GREEN/AMBER/RED zones.
D.radial_gauge(fig, [0.055, 0.345, 0.335, 0.345], 63,
               label="SSI — illustrative")

# Framing card behind the gauge region for visual weight + a reading band.
D.panel(ax, 4.0, 25.0, 38.0, 47.0, title="Composite reading")
# zone legend below the gauge
zy = 29.5
for i, (lab, col) in enumerate([("Low 0–40", D.GREEN),
                                ("Elevated 40–70", D.AMBER),
                                ("Critical 70–100", D.RED)]):
    cx = 7.0 + i * 11.6
    ax.add_patch(D.FancyBboxPatch((cx, zy), 2.0, 2.0,
                 boxstyle="round,pad=0.1,rounding_size=0.4",
                 facecolor=col, edgecolor="none", zorder=3))
    ax.text(cx + 2.7, zy + 1.0, lab, color=D.SLATE, fontsize=7.6,
            va="center", ha="left", zorder=3)

# ── RIGHT: component weights (chokepoint heaviest, in BRASS) ──────────────────
D.hbars(
    fig, [0.555, 0.345, 0.345, 0.345],
    ["Chokepoint", "Congestion", "Rate", "Weather", "Anomaly", "Vulnerability"],
    [29, 18, 16, 15, 12, 10],
    colors=[D.BRASS, D.STEEL, D.STEEL, D.STEEL, D.STEEL, D.STEEL],
    fmt="{:.0f}%", title="Component weights",
)
# caption under the hbars
ax.text(56.0, 31.5,
        "Chokepoint is the #1 weight (~0.29). Six weights sum to 100%.",
        color=D.SLATE, fontsize=8.0, va="top", ha="left")

# ── BOTTOM-LEFT: how to read it ──────────────────────────────────────────────
D.note_panel(
    ax, 4.0, 6.0, 50.0, 16.5, "How to read it",
    ["0–100 composite; higher = more shipping stress.",
     "Chokepoint transit risk is the #1 weight (~0.29).",
     "Six components reconcile EXACTLY to the headline SSI."],
)

# ── BOTTOM-RIGHT: real+modeled inputs + provenance note ──────────────────────
D.panel(ax, 57.0, 6.0, 39.0, 16.5)
D.chip(ax, 59.0, 17.5, "REAL + MODELED INPUTS")
ax.text(59.0, 13.6,
        "Real: IMF PortWatch chokepoint transits,\n"
        "FRED macro (BDI / Brent / VIX), equity tape.\n"
        "Modeled: congestion, weather, anomaly,\n"
        "vulnerability — each labelled real vs modeled.",
        color=D.INK, fontsize=8.0, va="top", ha="left", linespacing=1.42)

# provenance footnote inside the gauge card (gauge value is illustrative)
ax.text(23.0, 26.8,
        "Gauge value illustrative · public sources",
        color=D.SLATE, fontsize=7.2, va="bottom", ha="center", style="italic")

D.save(fig, OUT)
print("wrote", OUT)
