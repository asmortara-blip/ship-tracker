"""Slide 03 — The Solution: real data → coverage-tested intelligence.

Three columns (INPUTS → ENGINE → OUTPUTS) connected left-to-right by arrows.
The coverage-tested risk node is highlighted in BRASS (the differentiator).
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/"
       "Pitch Deck (v2)/03-solution.pdf")

fig, ax = D.slide(
    3, "what it is",
    "Real data → coverage-tested intelligence",
    subtitle="One pipeline: real feeds in, a stress index and back-tested "
             "VaR & ES out — every layer labelled real vs modeled.",
)

# ── column geometry ──────────────────────────────────────────────────────────
# Three columns across the body. Body lives below ~78, above the footer (3.4).
COL_W = 26.0
X1 = 5.0          # INPUTS column left edge
X2 = 37.0         # ENGINE column left edge
X3 = 69.0         # OUTPUTS column left edge
CXM = X1 + COL_W / 2.0   # input col centre
CEM = X2 + COL_W / 2.0   # engine col centre
COM = X3 + COL_W / 2.0   # output col centre

# Column header band (chip-style) for each stage
HDR_Y = 71.0          # band spans 71.0 .. 75.6
HDR_H = 4.6
ROW_TOP = 68.5        # top edge of the first node row (clears the band)
def col_header(cx, text, num):
    w = COL_W
    x = cx - w / 2.0
    ax.add_patch(FancyBboxPatch((x, HDR_Y), w, HDR_H,
                 boxstyle="round,pad=0.2,rounding_size=1.0",
                 facecolor=D.NAVY, edgecolor=D.BRASS, lw=1.3, zorder=3))
    ax.text(x + 2.0, HDR_Y + HDR_H / 2.0, num, ha="center", va="center",
            color=D.BRASS, fontsize=11, fontweight="bold", zorder=4)
    ax.text(cx + 1.4, HDR_Y + HDR_H / 2.0, text, ha="center", va="center",
            color=D.PAPER_TX, fontsize=11.5, fontweight="bold", zorder=4)

col_header(CXM, "REAL FEEDS", "1")
col_header(CEM, "ENGINE", "2")
col_header(COM, "INTELLIGENCE", "3")

# ── INPUTS column — stacked feed nodes ───────────────────────────────────────
# Two-up compact node rows so six feeds fit airily.
feeds = [
    ("PortWatch", "chokepoint transits"),
    ("yfinance", "shipping-equity tape"),
    ("FRED", "BDI · Brent · VIX · DXY"),
    ("GDELT", "geo-events"),
    ("AIS", "vessel positions"),
    ("OFAC", "sanctions (SDN)"),
]
in_h = 6.6
in_gap = 1.7
in_top = ROW_TOP - in_h     # bottom-y of the first (top) node
nx = X1
nw = COL_W
for i, (nm, sub) in enumerate(feeds):
    ny = in_top - i * (in_h + in_gap)
    D.node(ax, nx, ny, nw, in_h, nm, body=sub, ec=D.STEEL, ts=10.2, bs=7.9)

# realness stamp note under the inputs (kept clear of the value strip below)
stamp_y = in_top - (len(feeds) - 1) * (in_h + in_gap) - 1.6
ax.text(CXM, stamp_y, "every feed carries a real-vs-modeled stamp",
        ha="center", va="top", color=D.SLATE, fontsize=8.0, style="italic")

# ── ENGINE column — processing nodes (brass = differentiator) ────────────────
eng = [
    ("Shipping Stress Index", "6 weighted components", D.STEEL, D.CARD, False),
    ("risk_lab  VaR / ES", "coverage-tested  ·  the moat",
     D.BRASS, D.BRASS_LT, True),
    ("20 validators", "2 on real market data", D.STEEL, D.CARD, False),
    ("Signal ledger", "point-in-time · net of cost", D.STEEL, D.CARD, False),
]
en_h = 10.5
en_gap = 3.0
en_top = ROW_TOP - en_h
for i, (nm, sub, ec, fc, hero) in enumerate(eng):
    ny = en_top - i * (en_h + en_gap)
    lw = 2.2 if hero else 1.4
    D.node(ax, X2, ny, COL_W, en_h, nm, body=sub, ec=ec, fc=fc,
           ts=11.5, bs=8.4, lw=lw)
    if hero:
        # a small brass tag marking the differentiator
        ax.text(X2 + COL_W - 1.6, ny + en_h - 1.4, "★",
                ha="right", va="top", color=D.BRASS, fontsize=11,
                fontweight="bold", zorder=4)

# ── OUTPUTS column — delivered intelligence nodes ────────────────────────────
outs = [
    ("Stress gauge", "live SSI dial"),
    ("Tested VaR & ES", "Kupiec · Christoffersen · A-S"),
    ("Chokepoint risk", "Suez · Panama precedence"),
    ("Briefing / alerts / API", "daily + 6 channels + JSON"),
]
ou_h = 10.5
ou_gap = 3.0
ou_top = ROW_TOP - ou_h
for i, (nm, sub) in enumerate(outs):
    ny = ou_top - i * (ou_h + ou_gap)
    # brass edge on the Tested VaR & ES output (it's the differentiated deliverable)
    ec = D.BRASS if i == 1 else D.STEEL
    fc = D.BRASS_LT if i == 1 else D.CARD
    lw = 2.0 if i == 1 else 1.4
    D.node(ax, X3, ny, COL_W, ou_h, nm, body=sub, ec=ec, fc=fc,
           ts=11.5, bs=8.4, lw=lw)

# ── connector arrows between columns ─────────────────────────────────────────
# big stage arrows centred between columns, in the vertical middle of the stack
mid_y = 38.0
D.arrow(ax, (X1 + COL_W + 0.6, mid_y), (X2 - 0.6, mid_y),
        color=D.BRASS, lw=2.4, mut=20)
D.arrow(ax, (X2 + COL_W + 0.6, mid_y), (X3 - 0.6, mid_y),
        color=D.BRASS, lw=2.4, mut=20)

# ── one-line value proposition strip ─────────────────────────────────────────
vp_y = 8.6
vp_h = 6.0
vp_cy = vp_y + vp_h / 2.0
ax.add_patch(FancyBboxPatch((5.0, vp_y), 90.0, vp_h,
             boxstyle="round,pad=0.3,rounding_size=1.2",
             facecolor=D.NAVY, edgecolor=D.BRASS, lw=1.4, zorder=2))
ax.text(50.0, vp_cy + 1.4,
        "The only platform that fuses maritime-disruption signals with "
        "portfolio risk back-tested against realized P&L —",
        ha="center", va="center", color=D.PAPER_TX, fontsize=10.8,
        fontweight="bold", zorder=3)
ax.text(50.0, vp_cy - 1.5,
        "under one real-vs-modeled honesty discipline.",
        ha="center", va="center", color=D.BRASS_LT, fontsize=10.8,
        fontweight="bold", zorder=3)

D.save(fig, OUT)
print("wrote", OUT)
