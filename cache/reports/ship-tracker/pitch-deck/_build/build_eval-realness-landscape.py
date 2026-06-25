"""Eval-realness landscape: 20 backtest validators, what's tested on REAL market data.

Left: the validator list as a horizontal categorical dot grid. Two validators run
on real market P&L (BRASS); the rest are deterministic walk-forward on synthetic /
structural inputs (STEEL). Every validator is currently HEALTHY (green check).
Right: a stat strip, the idea-panel funnel, and a "How it works" breakdown.
Facts only from the fact sheet.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _style as S

from matplotlib.patches import FancyBboxPatch

OUT = "/Users/aaronmortara/MC/Models/Ship/cache/reports/ship-tracker/eval-realness-landscape.pdf"

# ── The 20 validators (2 real on top, then 18 synthetic/structural) ────────────
# 18 enumerated deterministic / synthetic validators.
REAL = [
    "VaR Coverage — Kupiec POF",
    "ES Coverage — Acerbi-Szekely",
]
SYNTHETIC = [
    "SSI Component Predictiveness",
    "SCHI Dimension Predictiveness",
    "Disruption Forecast Accuracy",
    "Momentum Ranker Ladder",
    "Leading Indicators Calibration",
    "News Sentiment Calibration",
    "Vulnerability Scorer Monotonicity",
    "ETA Predictor Accuracy",
    "Port Supply Lines Stability",
    "Company Supply Risk Stability",
    "SSI Lag-Correlation Recovery",
    "Historical Event Replay",
    "Snapshot Diff Anomaly Recovery",
    "Cargo-Flow JSD Stability",
    "Capacity-Demand Persistence",
    "Spillover Graph Recall",
    "Graph Centrality Dominance",
    "Freight Volatility Backtest",
]
ROWS = [(lbl, True) for lbl in REAL] + [(lbl, False) for lbl in SYNTHETIC]
assert len(ROWS) == 20

fig, ax = S.new_page()

S.header(
    ax,
    "Ship Tracker",
    "The Eval Surface: 20 Validators",
    "What's tested on REAL market data — and what walks forward on synthetic / structural inputs",
)
S.footer(ax, right="Backtest validator suite — real-vs-synthetic honesty pass")

# ── Geometry ───────────────────────────────────────────────────────────────────
LIST_X = 4.0          # left edge of list card
LIST_W = 52.0         # list card width
RIGHT_X = 60.0        # left edge of right column
RIGHT_W = 36.0        # right column width

TOP = 82.0            # top of the rows
BOT = 12.5            # bottom of the rows
N = len(ROWS)
row_h = (TOP - BOT) / N

# list backing card
ax.add_patch(FancyBboxPatch(
    (LIST_X, BOT - 7.0), LIST_W, (TOP - BOT) + 10.3,
    boxstyle="round,pad=0.3,rounding_size=1.2",
    linewidth=0.9, edgecolor=S.LINE, facecolor=S.CARD, zorder=0.5))

# column headers
hdr_y = TOP + 2.4
ax.text(LIST_X + 6.6, hdr_y, "VALIDATOR", color=S.SLATE, fontsize=7.4,
        fontweight="bold", va="bottom", ha="left")
ax.text(LIST_X + LIST_W - 9.0, hdr_y, "DATA SOURCE", color=S.SLATE, fontsize=7.4,
        fontweight="bold", va="bottom", ha="right")
ax.text(LIST_X + LIST_W - 2.0, hdr_y, "OK", color=S.SLATE, fontsize=7.4,
        fontweight="bold", va="bottom", ha="right")

def green_check(cx, cy):
    ax.plot([cx - 0.65, cx - 0.2, cx + 0.85],
            [cy - 0.05, cy - 0.75, cy + 0.85],
            color=S.GREEN, lw=1.7, solid_capstyle="round",
            solid_joinstyle="round", zorder=6)

for i, (label, is_real) in enumerate(ROWS):
    yc = TOP - row_h * (i + 0.5)
    cat_color = S.BRASS if is_real else S.STEEL
    is_sibling = label.startswith("+")

    # faint zebra band on real rows so the two pop
    if is_real:
        ax.add_patch(FancyBboxPatch(
            (LIST_X + 1.2, yc - row_h / 2 + 0.25), LIST_W - 2.4, row_h - 0.5,
            boxstyle="round,pad=0.05,rounding_size=0.6",
            linewidth=0.0, facecolor=S.BRASS_LT, zorder=1.0, alpha=0.45))

    # category dot
    if not is_sibling:
        ax.add_patch(FancyBboxPatch(
            (LIST_X + 3.0, yc - 0.85), 1.7, 1.7,
            boxstyle="round,pad=0.05,rounding_size=0.6",
            linewidth=0.0, facecolor=cat_color, zorder=4))
    else:
        ax.add_patch(FancyBboxPatch(
            (LIST_X + 3.0, yc - 0.85), 1.7, 1.7,
            boxstyle="round,pad=0.05,rounding_size=0.6",
            linewidth=0.0, facecolor=S.STEEL_LT, zorder=4))

    # row index
    ax.text(LIST_X + 2.4, yc, f"{i+1:02d}", color=S.SLATE, fontsize=6.4,
            va="center", ha="right", zorder=4)

    # label
    weight = "bold" if is_real else ("normal")
    lab_color = S.INK if not is_sibling else S.SLATE
    style = "italic" if is_sibling else "normal"
    ax.text(LIST_X + 6.6, yc, label, color=lab_color, fontsize=7.9,
            va="center", ha="left", fontweight=weight, fontstyle=style, zorder=4)

    # data-source tag
    tag = "REAL" if is_real else "SYN"
    tag_color = S.BRASS if is_real else S.STEEL
    ax.text(LIST_X + LIST_W - 9.0, yc, tag, color=tag_color, fontsize=7.0,
            va="center", ha="right", fontweight="bold", zorder=4)

    # healthy green check
    green_check(LIST_X + LIST_W - 3.3, yc)

# legend strip under the list card
leg_y = BOT - 4.3
def swatch(x, color):
    ax.add_patch(FancyBboxPatch((x, leg_y - 0.75), 1.7, 1.7,
                boxstyle="round,pad=0.05,rounding_size=0.55", linewidth=0,
                facecolor=color, zorder=4))
swatch(LIST_X + 2.0, S.BRASS)
ax.text(LIST_X + 4.4, leg_y, "REAL market data", color=S.INK, fontsize=7.4,
        va="center", ha="left")
swatch(LIST_X + 21.5, S.STEEL)
ax.text(LIST_X + 23.9, leg_y, "Synthetic / structural", color=S.INK, fontsize=7.4,
        va="center", ha="left")
green_check(LIST_X + 45.5, leg_y)
ax.text(LIST_X + 47.4, leg_y, "Healthy", color=S.INK, fontsize=7.4,
        va="center", ha="left", fontweight="bold")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT COLUMN
# ══════════════════════════════════════════════════════════════════════════════

# ── Stat strip: 3 stacked tiles ────────────────────────────────────────────────
def stat_tile(y, h, big, big_color, label):
    ax.add_patch(FancyBboxPatch(
        (RIGHT_X, y), RIGHT_W, h, boxstyle="round,pad=0.3,rounding_size=1.0",
        linewidth=1.2, edgecolor=S.LINE, facecolor=S.CARD, zorder=2))
    ax.text(RIGHT_X + 3.5, y + h / 2, big, color=big_color, fontsize=18,
            fontweight="bold", va="center", ha="left", zorder=3)
    ax.text(RIGHT_X + 14.5, y + h / 2, label, color=S.SLATE, fontsize=7.6,
            va="center", ha="left", zorder=3, linespacing=1.4)

st_top = 82.0
st_h = 8.2
st_gap = 1.8
stat_tile(st_top - st_h, st_h, "2 / 20", S.BRASS,
          "validators run on\nREAL market data")
stat_tile(st_top - 2 * st_h - st_gap, st_h, "18", S.STEEL,
          "deterministic walk-forward\non synthetic / structural")
stat_tile(st_top - 3 * st_h - 2 * st_gap, st_h, "20 / 20", S.GREEN,
          "currently report\nHEALTHY")

# headline of the stat strip
ax.text(RIGHT_X, st_top + 2.4, "AT A GLANCE", color=S.BRASS, fontsize=8.4,
        fontweight="bold", va="bottom", ha="left")

# ── Suite line ─────────────────────────────────────────────────────────────────
suite_y = st_top - 3 * st_h - 2 * st_gap - 6.6
ax.add_patch(FancyBboxPatch(
    (RIGHT_X, suite_y), RIGHT_W, 5.0, boxstyle="round,pad=0.3,rounding_size=1.0",
    linewidth=1.2, edgecolor=S.GREEN, facecolor="#EAF3EE", zorder=2))
ax.text(RIGHT_X + 2.0, suite_y + 3.4, "Suite: 7,958 tests pass",
        color=S.GREEN, fontsize=9.2, fontweight="bold", va="center", ha="left", zorder=3)
ax.text(RIGHT_X + 2.0, suite_y + 1.3, "0 live-network leaks (sockets blocked)",
        color=S.SLATE, fontsize=7.6, va="center", ha="left", zorder=3)

# ── Idea-panel funnel (subtle inset) ───────────────────────────────────────────
fun_top = suite_y - 3.4
ax.text(RIGHT_X, fun_top, "IDEA-PANEL FUNNEL", color=S.BRASS, fontsize=8.0,
        fontweight="bold", va="top", ha="left")
funnel = [("60", "raw"), ("43", "unique"), ("14", "survivors"), ("13", "shipped")]
fb_y = fun_top - 6.6
seg_w = (RIGHT_W - 3 * 1.6) / 4
fx = RIGHT_X
for j, (num, lab) in enumerate(funnel):
    # taper the height to read as a funnel
    fh = 5.0 - j * 0.55
    fc = S.MIST if j < 3 else S.BRASS_LT
    ec = S.STEEL if j < 3 else S.BRASS
    nc = S.STEEL if j < 3 else S.BRASS
    ax.add_patch(FancyBboxPatch(
        (fx, fb_y + (5.0 - fh)), seg_w, fh,
        boxstyle="round,pad=0.15,rounding_size=0.7",
        linewidth=1.1, edgecolor=ec, facecolor=fc, zorder=3))
    ax.text(fx + seg_w / 2, fb_y + (5.0 - fh) + fh / 2 + 0.6, num, color=nc,
            fontsize=11, fontweight="bold", va="center", ha="center", zorder=4)
    ax.text(fx + seg_w / 2, fb_y - 1.5, lab, color=S.SLATE, fontsize=6.8,
            va="center", ha="center", zorder=4)
    if j < 3:
        ax.text(fx + seg_w + 0.8, fb_y + 2.5, "›", color=S.SLATE, fontsize=10,
                va="center", ha="center", zorder=4)
    fx += seg_w + 1.6

# ── Breakdown: honesty ethos ───────────────────────────────────────────────────
S.breakdown(
    ax, RIGHT_X, 9.5, RIGHT_W,
    "How to read this",
    [
        "\"Real\" = scored against realized",
        "market P&L (VaR & ES coverage).",
        "Synthetic = deterministic walk-",
        "forward on structural inputs —",
        "rigorous, just not market-real.",
        "Nothing synthetic is shown as real.",
    ],
    body_size=7.8,
)

print("rendering …")
S.save(fig, OUT)
import os
sz = os.path.getsize(OUT)
print(f"wrote {OUT} ({sz} bytes)")
