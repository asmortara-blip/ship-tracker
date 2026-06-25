"""Slide 06 — How It Works: From Returns to a Tested Risk Number.

Left-to-right pipeline on the D.slide() chrome:
  real 5-yr returns -> EWMA vol -> two tail models (Gaussian / Student-t nu=6)
  -> realized next-day P&L -> three validation gates (Kupiec, Christoffersen,
  Acerbi-Szekely Test 2) -> verdict badges (Gaussian 99% REJECTED red;
  Student-t 99% PASS green; ES WELL-SCALED green).

Colour semantics: STEEL = Gaussian/synthetic, BRASS = Student-t/real hero,
GREEN = pass, RED = reject.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D

OUT = "/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/Pitch Deck (v2)/06-pipeline.pdf"

REDFILL = "#F3E2E2"
GRNFILL = "#E2EFE8"

fig, ax = D.slide(
    6, "method",
    "How It Works — From Returns to a Tested Risk Number",
    subtitle="Real cache: 1,292 trading days x 6 shipping equities, 5y, 60-day EWMA window. "
             "VaR / ES coverage-tested against realized P&L.",
    title_size=23,
)

# ── Stage band labels (top of pipeline) ──────────────────────────────────────
band_y = 72.5
for lx, txt in [
    (3.0,  "1 · INPUT"),
    (24.0, "2 · TAIL MODELS"),
    (47.0, "3 · REALIZED P&L"),
    (66.5, "4 · VALIDATION GATES"),
]:
    ax.text(lx, band_y, txt, color=D.BRASS, fontsize=8.6, fontweight="bold",
            va="bottom", ha="left", zorder=5)
ax.add_line(D.Line2D([3.0, 89.5], [band_y - 0.8, band_y - 0.8],
                     color=D.LINE, lw=0.8, zorder=1))

# ── Stage 1: real returns -> EWMA vol (left column) ───────────────────────────
D.node(ax, 3.0, 58.5, 18.5, 10.5,
       "Real 5-yr returns",
       body="1,292 days x 6\nshipping equities\n(ZIM MATX SBLK …)",
       fc=D.BRASS_LT, ec=D.BRASS, tc=D.INK, ts=10.5, bs=7.8)

D.node(ax, 3.0, 44.0, 18.5, 10.5,
       "EWMA volatility",
       body="RiskMetrics\ncausal forecast\n60-day window",
       fc=D.MIST, ec=D.STEEL, tc=D.INK, ts=10.5, bs=7.8)

D.arrow(ax, (12.25, 58.5), (12.25, 54.5), color=D.STEEL, lw=2.0)

# ── Stage 2: two parallel tail models ─────────────────────────────────────────
# faint grouping band
ax.add_patch(D.FancyBboxPatch((23.5, 42.0), 18.5, 27.0,
             boxstyle="round,pad=0.3,rounding_size=1.0",
             facecolor="#F0EFE8", edgecolor=D.LINE, lw=0.9, zorder=1))

D.node(ax, 24.5, 57.5, 16.5, 10.0,
       "Gaussian VaR",
       body="thin-tailed\nnormal quantile",
       fc=D.MIST, ec=D.STEEL, tc=D.INK, ts=10.5, bs=8.0)

D.node(ax, 24.5, 44.5, 16.5, 10.0,
       "Student-t  nu=6",
       body="fat-tailed\nexcess kurt 3.0",
       fc=D.BRASS_LT, ec=D.BRASS, tc=D.INK, ts=10.5, bs=8.0)

# EWMA vol -> both models
D.arrow(ax, (21.5, 50.5), (24.5, 62.5), color=D.STEEL, lw=1.6, rad=-0.16)
D.arrow(ax, (21.5, 48.0), (24.5, 49.5), color=D.BRASS, lw=1.6, rad=0.08)

# ── Stage 3: realized next-day P&L (centre) ───────────────────────────────────
D.node(ax, 47.0, 50.0, 16.5, 13.0,
       "Realized\nnext-day P&L",
       body="did the loss\nbreach VaR?",
       fc=D.CARD, ec=D.NAVY, tc=D.INK, ts=11, bs=8.0)

# both models -> realized P&L (breach test)
D.arrow(ax, (41.0, 62.5), (47.0, 60.0), color=D.STEEL, lw=1.6, rad=0.12)
D.arrow(ax, (41.0, 49.5), (47.0, 54.0), color=D.BRASS, lw=1.6, rad=-0.12)

# ── Stage 4: three validation gates (stacked) ─────────────────────────────────
gx, gw = 66.5, 23.0
D.node(ax, gx, 60.5, gw, 7.4,
       "Kupiec POF",
       body="unconditional coverage",
       fc=D.CARD, ec=D.STEEL, tc=D.INK, ts=10, bs=7.8)
D.node(ax, gx, 51.5, gw, 7.4,
       "Christoffersen",
       body="conditional / independence",
       fc=D.CARD, ec=D.STEEL, tc=D.INK, ts=10, bs=7.8)
D.node(ax, gx, 42.5, gw, 7.4,
       "Acerbi-Szekely T2",
       body="ES tail-mean (Z2)",
       fc=D.CARD, ec=D.STEEL, tc=D.INK, ts=10, bs=7.8)

# realized P&L -> each gate
D.arrow(ax, (63.5, 59.0), (66.5, 64.2), color=D.SLATE, lw=1.4, rad=-0.12)
D.arrow(ax, (63.5, 57.0), (66.5, 55.2), color=D.SLATE, lw=1.4, rad=0.0)
D.arrow(ax, (63.5, 55.0), (66.5, 46.2), color=D.SLATE, lw=1.4, rad=0.12)

# ── Breach-evidence chips (under realized P&L) ────────────────────────────────
ax.text(47.0, 47.0, "95% breach   nominal 5.0%", color=D.SLATE, fontsize=7.8,
        fontweight="bold", va="top", ha="left")
D.chip(ax, 47.0, 41.8, "Gaussian 4.88%", fc=D.MIST, ec=D.STEEL, tc=D.INK, size=7.4)
D.chip(ax, 47.0, 37.6, "Student-t 5.65%", fc=D.BRASS_LT, ec=D.BRASS, tc=D.INK, size=7.4)

ax.text(47.0, 33.0, "99% breach   nominal 1.0%", color=D.SLATE, fontsize=7.8,
        fontweight="bold", va="top", ha="left")
D.chip(ax, 47.0, 27.8, "Gaussian 1.70%", fc=REDFILL, ec=D.RED, tc=D.RED, size=7.4)
D.chip(ax, 47.0, 23.6, "Student-t 1.16%", fc=GRNFILL, ec=D.GREEN, tc=D.GREEN, size=7.4)

# ── Verdict badges (right column, below the gates) ────────────────────────────
ax.text(gx, 38.6, "VERDICTS  ·  REAL CACHE", color=D.BRASS, fontsize=9,
        fontweight="bold", va="top", ha="left")

# gates -> verdicts connector
D.arrow(ax, (gx + gw / 2, 42.5), (gx + gw / 2, 37.4), color=D.SLATE, lw=2.0)

D.node(ax, gx, 30.0, gw, 6.2,
       "Gaussian 99%:  REJECTED",
       body="Kupiec p = 0.021  ·  Christoffersen p = 0.048",
       fc=REDFILL, ec=D.RED, tc=D.RED, ts=9.5, bs=7.2, lw=1.6)
D.node(ax, gx, 22.4, gw, 6.2,
       "Student-t 99%:  PASS",
       body="Kupiec p = 0.57  ·  Christoffersen p = 0.32",
       fc=GRNFILL, ec=D.GREEN, tc=D.GREEN, ts=9.5, bs=7.2, lw=1.6)
D.node(ax, gx, 14.8, gw, 6.2,
       "ES:  WELL-SCALED",
       body="Z2 = -0.13 / -0.15  within critical",
       fc=GRNFILL, ec=D.GREEN, tc=D.GREEN, ts=9.5, bs=7.2, lw=1.6)

# ── How-it-works explainer panel (bottom-left, under the pipeline) ────────────
D.note_panel(
    ax, 3.0, 7.0, 41.0, 28.5,
    "How to read it",
    [
        "Kupiec POF — too many VaR breaches overall?",
        "Christoffersen — are breaches clustered, not independent?",
        "Acerbi-Szekely T2 — is the ES tail-mean big enough?",
        "The Gaussian under-states the 99% fat tail and is rejected.",
        "Student-t (nu=6) fixes 99% (p 0.021 -> 0.57), keeps 95%,",
        "and the ES reads honest (Z2 ~ 0). Tested, not asserted.",
    ],
    size=8.8, gap=3.6,
)

# illustrative / sources note
ax.text(89.5, 7.0,
        "Coverage tests run nightly · risk_lab — the 2 real-data validators",
        color=D.SLATE, fontsize=7.0, va="bottom", ha="right")

D.save(fig, OUT)
print("wrote", OUT)
