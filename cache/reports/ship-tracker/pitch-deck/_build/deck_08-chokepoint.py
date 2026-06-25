"""Slide 08 — Chokepoint Intelligence (R267): lighting up Suez & Panama from real PortWatch.

Adapts build_chokepoint-precedence.py (the precedence-ladder decision flow) onto the
D.slide() deck chrome. Coordinate space 0..100 / 0..100, Letter-landscape.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D

OUT = "/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/Pitch Deck (v2)/08-chokepoint.pdf"

fig, ax = D.slide(
    8,
    "real-data grounding",
    "Lighting up Suez & Panama from real PortWatch",
    subtitle="Precedence resolver for a canal node:  real PortWatch transit  >  real canal scrape  >  hardcoded baseline.",
)

# ── Section label for the decision flow (left 60%) ───────────────────────────
ax.text(4, 76.2, "PRECEDENCE-LADDER DECISION FLOW", color=D.SLATE, fontsize=9,
        fontweight="bold", va="top", ha="left")
ax.add_line(Line2D([4, 33], [74.4, 74.4], color=D.LINE, lw=0.8))

# ── Geometry for the main flow column (left side) ────────────────────────────
CX = 17.5           # centre x of the main flow column
BW = 27.0           # box width for the main flow nodes
LX = CX - BW / 2    # left edge of main column = 4.0

# YES-branch column (real-data outcomes), to the right of the flow column
YX = 39.0
YW = 22.5

# ── START node ───────────────────────────────────────────────────────────────
D.node(ax, LX, 65.0, BW, 7.4,
       "Canal node:  Suez / Panama",
       body="#1 SSI chokepoint weight (~0.29)",
       fc=D.MIST, ec=D.NAVY, tc=D.INK, ts=10, bs=7.8)

# ── DECISION 1: PortWatch ─────────────────────────────────────────────────────
D.node(ax, LX, 55.4, BW, 6.4,
       "Real IMF PortWatch\ntransit available?",
       fc=D.CARD, ec=D.STEEL, tc=D.INK, ts=9.6)
D.arrow(ax, (CX, 65.0), (CX, 61.8), color=D.NAVY, lw=1.8)

# YES -> Drive from PortWatch
D.node(ax, YX, 55.0, YW, 7.4,
       "Drive from PortWatch",
       body="transit_drop -> risk level\nvalidator-grade, escalate-only",
       fc=D.CARD, ec=D.BRASS, tc=D.INK, ts=9.6, bs=7.4)
D.arrow(ax, (LX + BW, 58.6), (YX, 58.6), color=D.GREEN, lw=1.8)
ax.text((LX + BW + YX) / 2, 59.8, "YES", color=D.GREEN, fontsize=8.2,
        fontweight="bold", ha="center", va="bottom")
D.chip(ax, YX + YW - 17.0, 51.0, "REAL · PortWatch",
       fc="#DCEBE2", ec=D.GREEN, tc=D.GREEN, size=7.8)

# ── DECISION 1 -> NO -> DECISION 2: canal scrape ─────────────────────────────
D.node(ax, LX, 44.2, BW, 6.4,
       "Real canal scrape\n(not synthetic)?",
       fc=D.CARD, ec=D.STEEL, tc=D.INK, ts=9.6)
D.arrow(ax, (CX, 55.4), (CX, 50.6), color=D.STEEL, lw=1.8)
ax.text(CX + 1.2, 52.9, "NO", color=D.SLATE, fontsize=8.2, fontweight="bold",
        ha="left", va="center")

# YES -> Use canal scrape
D.node(ax, YX, 43.8, YW, 7.4,
       "Use canal scrape",
       body="Suez / Panama authority feed\nescalate-only",
       fc=D.CARD, ec=D.BRASS, tc=D.INK, ts=9.6, bs=7.4)
D.arrow(ax, (LX + BW, 47.4), (YX, 47.4), color=D.GREEN, lw=1.8)
ax.text((LX + BW + YX) / 2, 48.6, "YES", color=D.GREEN, fontsize=8.2,
        fontweight="bold", ha="center", va="bottom")
D.chip(ax, YX + YW - 13.5, 39.8, "REAL · canal",
       fc="#DCEBE2", ec=D.GREEN, tc=D.GREEN, size=7.8)

# ── DECISION 2 -> NO -> hardcoded baseline ───────────────────────────────────
D.node(ax, LX, 33.2, BW, 7.0,
       "Hold hardcoded baseline",
       body="no real overlay this cycle",
       fc="#ECECEC", ec=D.SLATE, tc=D.INK, ts=9.6, bs=7.4)
D.arrow(ax, (CX, 44.2), (CX, 40.2), color=D.STEEL, lw=1.8)
ax.text(CX + 1.2, 42.4, "NO", color=D.SLATE, fontsize=8.2, fontweight="bold",
        ha="left", va="center")
D.chip(ax, LX + 2.0, 29.4, "MODELED · baseline",
       fc="#E6E6E6", ec=D.SLATE, tc=D.SLATE, size=7.8)

# ── RIGHT-TOP: transit_drop_ratio -> risk-level mapping table ─────────────────
MAP_X, MAP_Y, MAP_W, MAP_H = 64.0, 45.5, 32.0, 27.0
D.panel(ax, MAP_X, MAP_Y, MAP_W, MAP_H, title="transit_drop_ratio → risk level")
ax.text(MAP_X + 1.8, MAP_Y + MAP_H - 5.0, "applied on the PortWatch drive path",
        color=D.SLATE, fontsize=7.8, ha="left", va="top")

map_rows = [
    ("≥ 0.50", "CRITICAL", D.RED,   "transits collapse"),
    ("≥ 0.30", "HIGH",     D.AMBER, "severe rerouting"),
    ("≥ 0.15", "MODERATE", D.STEEL, "draft / queue strain"),
    ("else",   "LOW",      D.GREEN, "normal flow"),
]
ry = MAP_Y + MAP_H - 9.6
for cond, lvl, col, desc in map_rows:
    ax.text(MAP_X + 2.2, ry, cond, color=D.INK, fontsize=10.5, fontweight="bold",
            ha="left", va="center", family="DejaVu Sans")
    # level pill
    pw = 14.0
    px = MAP_X + 9.0
    ax.add_patch(FancyBboxPatch((px, ry - 1.7), pw, 3.4,
                 boxstyle="round,pad=0.2,rounding_size=1.6",
                 facecolor=col, edgecolor=col, lw=0, alpha=0.16, zorder=3))
    ax.text(px + pw / 2, ry, lvl, color=col, fontsize=8.6, fontweight="bold",
            ha="center", va="center", zorder=4)
    ax.text(MAP_X + 24.0, ry, desc, color=D.SLATE, fontsize=7.4,
            ha="left", va="center", zorder=4)
    ry -= 4.4
ax.text(MAP_X + 2.2, ry + 0.4, "None / short history → LOW",
        color=D.SLATE, fontsize=7.6, ha="left", va="center")

# ── BOTTOM: note panel of the rules (full width below the flow) ───────────────
D.note_panel(
    ax, 4.0, 5.0, 60.0, 21.5,
    "Precedence resolver — apply_live_canal_nodes (R267)",
    [
        "Precedence: real PortWatch transit > real canal scrape > baseline.",
        "Escalate-only & ratchet-free: a cleared reading never downgrades\n   a node below its pristine baseline.",
        "A present real signal is never overridden by the synthetic feed.",
        "Effect: the two highest-leverage SSI chokepoints — once dark behind\n   a near-always-synthetic scrape — now run on real data.",
    ],
    size=9.0, gap=4.0,
)

# ── BOTTOM-RIGHT: outcome / impact callout ───────────────────────────────────
IMP_X, IMP_Y, IMP_W, IMP_H = 66.5, 5.0, 29.5, 38.5
D.panel(ax, IMP_X, IMP_Y, IMP_W, IMP_H, title="Why it matters")
# big stat (value + tight label only — no sub to avoid crowding the bullets)
ax.text(IMP_X + 2.4, IMP_Y + IMP_H - 4.6, "2", color=D.BRASS, fontsize=30,
        fontweight="bold", va="top", ha="left", family="DejaVu Sans", zorder=3)
ax.text(IMP_X + 9.6, IMP_Y + IMP_H - 6.4, "highest-leverage\nSSI nodes lit up",
        color=D.INK, fontsize=9.0, fontweight="bold", va="top", ha="left",
        zorder=3, linespacing=1.25)
ax.add_line(Line2D([IMP_X + 2.4, IMP_X + IMP_W - 2.4],
                   [IMP_Y + IMP_H - 14.2, IMP_Y + IMP_H - 14.2],
                   color=D.LINE, lw=0.8))
D.bullets(
    ax, IMP_X + 2.4, IMP_Y + IMP_H - 16.4,
    [
        "Canal scrape almost always returns\n  is_synthetic=True — nodes were dark.",
        "Resolver overlays real IMF PortWatch\n  transit counts by precedence.",
        "Suez ~12% of global trade · ~30% of\n  container traffic; Panama ~5–6%.",
    ],
    size=8.0, gap=5.0, color=D.INK,
)
ax.text(IMP_X + 2.4, IMP_Y + 2.4, "Market context: illustrative · public sources",
        color=D.SLATE, fontsize=6.8, ha="left", va="center", style="italic")

D.save(fig, OUT)
import os
print("wrote", OUT, os.path.getsize(OUT), "bytes")
