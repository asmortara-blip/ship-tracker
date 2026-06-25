"""Slide 10 / 11 — Rigor, Honesty & the Research Engine."""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.lines import Line2D

OUT = "/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/Pitch Deck (v2)/10-rigor.pdf"

fig, ax = D.slide(
    10, "how we know it's real", "Rigor, Honesty & the Research Engine",
    subtitle="Two validators scored on real market data; a test suite with no live-network "
             "leaks; an idea panel that compounds the platform.",
)

# ── ZONE 1: EVAL SURFACE (left column) ───────────────────────────────────────
z1x, z1w = 4.0, 44.0
D.panel(ax, z1x, 38.0, z1w, 39.0, title="Eval surface — 20 validators")

# Two big stat callouts: 2 real vs 18 structural
D.stat(ax, z1x + 4.0, 71.5, "2", "on REAL market data",
       sub="scored vs realized P&L", big=30, color=D.BRASS)
D.stat(ax, z1x + 25.0, 71.5, "18", "deterministic structural",
       sub="walk-forward / structural", big=30, color=D.STEEL)
ax.add_line(Line2D([z1x + 22.0, z1x + 22.0], [56.5, 70.0], color=D.BRASS,
                   lw=1.2, alpha=0.7, zorder=3))

# Name the two real ones — brass chips (stacked, full panel width)
ax.text(z1x + 4.0, 54.0, "THE TWO REAL VALIDATORS", color=D.BRASS, fontsize=8.8,
        fontweight="bold", va="top", ha="left", zorder=3)
D.chip(ax, z1x + 4.0, 47.6, "VaR Coverage · Kupiec POF", fc=D.BRASS_LT, ec=D.BRASS)
D.chip(ax, z1x + 4.0, 43.0, "ES Coverage · Acerbi–Szekely",
       fc=D.BRASS_LT, ec=D.BRASS)
ax.text(z1x + 4.0, 40.6, "Both PASS on the real 5-yr / 6-equity cache.",
        color=D.SLATE, fontsize=8.0, va="top", ha="left", zorder=3)

# ── ZONE 2: TEST DISCIPLINE (right column, top) ──────────────────────────────
z2x, z2w = 52.0, 44.0
D.panel(ax, z2x, 60.0, z2w, 17.0, title="Test discipline")
disc = [
    ("7,958", "tests pass"),
    ("0", "live-net leaks"),
    ("20/20", "healthy"),
]
seg = z2w / 3
for i, (val, lab) in enumerate(disc):
    cx = z2x + seg * i + seg / 2
    ax.text(cx, 71.5, val, color=D.BRASS, fontsize=21, fontweight="bold",
            va="top", ha="center", family="DejaVu Sans", zorder=3)
    ax.text(cx, 65.2, lab, color=D.INK, fontsize=8.8, fontweight="bold",
            va="top", ha="center", zorder=3)
    if i > 0:
        ax.add_line(Line2D([z2x + seg * i, z2x + seg * i], [62.0, 73.0],
                           color=D.BRASS, lw=1.1, alpha=0.65, zorder=3))
ax.text(z2x + z2w / 2, 62.3, "sockets blocked · CI no-cache → 2 real = "
        "“not evaluated”, never fabricated",
        color=D.SLATE, fontsize=7.6, va="top", ha="center", zorder=3)

# ── ZONE 3: RESEARCH ENGINE (right column, lower) ────────────────────────────
D.panel(ax, z2x, 38.0, z2w, 19.0, title="Research engine")
D.funnel(ax, z2x + 2.0, 41.5, z2w - 4.0,
         [("60", "raw ideas"), ("43", "unique"),
          ("14", "survivors"), ("13", "shipped")], h=10.0)
ax.text(z2x + z2w / 2, 40.3, "A 56-agent multi-persona idea panel that "
        "compounds the platform  →  recs R267–R279.",
        color=D.SLATE, fontsize=7.8, va="top", ha="center", zorder=3)

# ── HONESTY DISCIPLINE note panel (full-width, bottom) ───────────────────────
D.note_panel(
    ax, 4.0, 6.5, 92.0, 28.0, "The honesty discipline",
    [
        "“Real” means scored against realized P&L — VaR & ES coverage "
        "tested on the live 5-yr cache (1,292 days × 6 shipping equities).",
        "The other 18 validators walk forward on structural inputs: rigorous and "
        "deterministic, but not market-real — and we say so explicitly.",
        "Nothing synthetic is ever shown as real. Every feed carries a "
        "data-quality / realness stamp; modeled overlays are labelled, never "
        "passed off as live.",
        "A no-cache CI run reports the 2 real validators as “not evaluated” "
        "(vacuously healthy) rather than inventing a number.",
    ],
    size=9.8, gap=5.0,
)

D.save(fig, OUT)
print("wrote", OUT)
