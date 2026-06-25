"""FLOWCHART — From Returns to a Coverage-Tested Risk Number.

Left-to-right pipeline: real returns -> EWMA vol -> two tail models ->
realized P&L -> three validation gates -> verdict badges.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _style as S

OUT = "/Users/aaronmortara/MC/Models/Ship/cache/reports/ship-tracker/risk-validation-pipeline.pdf"

fig, ax = S.new_page()

S.header(
    ax,
    "Ship Tracker",
    "From Returns to a Coverage-Tested Risk Number",
    "VaR / ES pipeline on the real cache: 1292 trading days x 6 shipping equities, 60-day EWMA window",
)
S.footer(
    ax,
    right="risk_lab — VaR Coverage (Kupiec POF) + ES Coverage (Acerbi-Szekely) — the 2 real-data validators",
)

# ── Column 1: source + EWMA vol ───────────────────────────────────────────────
# Real returns panel
S.box(ax, 3.0, 62.0, 19.0, 12.0,
      "Real 5-yr returns panel",
      body="1292 days x 6\nshipping equities",
      fc=S.BRASS_LT, ec=S.BRASS, tc=S.INK,
      title_size=10.5, body_size=8.6)

# EWMA vol forecast
S.box(ax, 3.0, 44.0, 19.0, 12.0,
      "EWMA volatility",
      body="RiskMetrics\nforecast (causal)",
      fc=S.MIST, ec=S.STEEL, tc=S.INK,
      title_size=10.5, body_size=8.6)

S.arrow(ax, (12.5, 62.0), (12.5, 56.0), color=S.STEEL, lw=1.8)

# ── Column 2: two parallel tail models ────────────────────────────────────────
S.band(ax, 26.0, 42.0, 21.0, 33.5, "Two tail models")

# Gaussian VaR (STEEL = synthetic/Gaussian)
S.box(ax, 28.0, 60.5, 17.0, 11.5,
      "Gaussian VaR",
      body="thin-tailed\nnormal quantile",
      fc=S.MIST, ec=S.STEEL, tc=S.INK,
      title_size=10.5, body_size=8.6)

# Student-t VaR (BRASS = real fix)
S.box(ax, 28.0, 45.0, 17.0, 11.5,
      "Student-t VaR  nu=6",
      body="fat-tailed\nkurtosis 3.0",
      fc=S.BRASS_LT, ec=S.BRASS, tc=S.INK,
      title_size=10.5, body_size=8.6)

# vol -> both tail models
S.arrow(ax, (22.0, 50.0), (28.0, 66.0), color=S.STEEL, lw=1.6,
        connection="arc3,rad=-0.18")
S.arrow(ax, (22.0, 50.0), (28.0, 50.5), color=S.BRASS, lw=1.6,
        connection="arc3,rad=0.0")

# ── Column 3: realized P&L ────────────────────────────────────────────────────
S.box(ax, 51.0, 50.5, 16.0, 14.5,
      "Realized\nnext-day P&L",
      fc=S.CARD, ec=S.NAVY, tc=S.INK,
      title_size=11)

# both tail models -> realized P&L (the breach test)
S.arrow(ax, (45.0, 66.0), (51.0, 60.0), color=S.STEEL, lw=1.6,
        connection="arc3,rad=0.12")
S.arrow(ax, (45.0, 50.5), (51.0, 56.0), color=S.BRASS, lw=1.6,
        connection="arc3,rad=-0.12")

# ── Column 4: three validation gates (stacked column) ─────────────────────────
S.band(ax, 70.0, 41.0, 27.0, 38.0, "Validation gates")

gx, gw = 72.0, 23.0
S.box(ax, gx, 66.5, gw, 8.5,
      "Kupiec POF",
      body="unconditional coverage",
      fc=S.CARD, ec=S.STEEL, tc=S.INK,
      title_size=10, body_size=8.2)

S.box(ax, gx, 56.0, gw, 8.5,
      "Christoffersen",
      body="conditional / independence",
      fc=S.CARD, ec=S.STEEL, tc=S.INK,
      title_size=10, body_size=8.2)

S.box(ax, gx, 45.5, gw, 8.5,
      "Acerbi-Szekely Test 2",
      body="ES tail-mean (R268)",
      fc=S.CARD, ec=S.STEEL, tc=S.INK,
      title_size=10, body_size=8.2)

# realized P&L -> each gate
S.arrow(ax, (67.0, 59.0), (72.0, 70.75), color=S.STEEL, lw=1.5,
        connection="arc3,rad=-0.15")
S.arrow(ax, (67.0, 58.0), (72.0, 60.25), color=S.STEEL, lw=1.5,
        connection="arc3,rad=0.0")
S.arrow(ax, (67.0, 57.0), (72.0, 49.75), color=S.STEEL, lw=1.5,
        connection="arc3,rad=0.15")

# ── Verdict badges (bottom-right block) ───────────────────────────────────────
vx = 72.0
ax.text(vx, 39.0, "VERDICTS (real cache)", color=S.BRASS, fontsize=9,
        fontweight="bold", va="top", ha="left")

# Gaussian 99% REJECTED (RED)
S.box(ax, vx, 31.0, 25.0, 6.0,
      "Gaussian 99%: REJECTED",
      body="Kupiec p = 0.021",
      fc="#F3E2E2", ec=S.RED, tc=S.RED,
      title_size=9.5, body_size=8.0, lw=1.6)

# Student-t 99% PASS (GREEN)
S.box(ax, vx, 23.6, 25.0, 6.0,
      "Student-t 99%: PASS",
      body="Kupiec p = 0.57",
      fc="#E2EFE8", ec=S.GREEN, tc=S.GREEN,
      title_size=9.5, body_size=8.0, lw=1.6)

# ES WELL-SCALED (GREEN)
S.box(ax, vx, 16.2, 25.0, 6.0,
      "ES: WELL-SCALED",
      body="Z2 within crit",
      fc="#E2EFE8", ec=S.GREEN, tc=S.GREEN,
      title_size=9.5, body_size=8.0, lw=1.6)

# gates -> verdicts
S.arrow(ax, (84.5, 45.5), (84.5, 37.4), color=S.SLATE, lw=1.8, style="-|>")

# ── Inline numeric chips under the realized-P&L stage (breach evidence) ────────
ax.text(51.0, 47.0, "95% breach  (nominal 5.0%)", color=S.SLATE, fontsize=7.8,
        fontweight="bold", va="top", ha="left")
S.chip(ax, 51.0, 42.6, "Gaussian 4.88%", fc=S.MIST, ec=S.STEEL, tc=S.INK, size=7.4)
S.chip(ax, 51.0, 38.4, "Student-t 5.65%", fc=S.BRASS_LT, ec=S.BRASS, tc=S.INK, size=7.4)

ax.text(51.0, 33.8, "99% breach  (nominal 1.0%)", color=S.SLATE, fontsize=7.8,
        fontweight="bold", va="top", ha="left")
S.chip(ax, 51.0, 29.4, "Gaussian 1.70%", fc="#F3E2E2", ec=S.RED, tc=S.RED, size=7.4)
S.chip(ax, 51.0, 25.2, "Student-t 1.16%", fc="#E2EFE8", ec=S.GREEN, tc=S.GREEN, size=7.4)

# ── How-it-works breakdown panel (bottom-left) ────────────────────────────────
S.breakdown(
    ax, 3.0, 6.5, 44.0,
    "How it works",
    [
        "Kupiec POF: are there too many VaR breaches overall?",
        "Christoffersen: are breaches independent, not clustered?",
        "Acerbi-Szekely Test 2: is the ES tail-mean big enough?",
        "Takeaway: the Student-t tail fixes the 99% VaR (p 0.021",
        "   -> 0.57) AND the ES is honest (Z2 = -0.13 / -0.15).",
    ],
    body_size=8.2,
)

S.save(fig, OUT)
print("wrote", OUT)
