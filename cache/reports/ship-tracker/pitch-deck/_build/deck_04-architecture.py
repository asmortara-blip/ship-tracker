"""Slide 04 — Platform Architecture: the 5-layer stacked-band stack.

Real Data Feeds -> Ingest & Cache -> Analytics -> Engine -> Delivery.
BRASS marks the 2 validators scored on REAL market P&L in the Analytics layer.
Adapted from build_arch2.py onto the D.slide() deck chrome.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/"
       "Pitch Deck (v2)/04-architecture.pdf")

fig, ax = D.slide(
    4, "the stack", "End-to-end, real to delivery",
)

# ── caption: BRASS legend just under the title -------------------------------
ax.text(4, 81.4,
        "Real-data feeds flow down through ingest, analytics and the engine to "
        "delivery — labelled real vs modeled at every step.",
        color=D.SLATE, fontsize=10.6, va="top", ha="left")

# legend swatch + caption, placed on its own line clear of the body text
ax.add_patch(FancyBboxPatch((4.0, 76.0), 2.2, 2.0,
             boxstyle="round,pad=0.1,rounding_size=0.5",
             facecolor=D.BRASS_LT, edgecolor=D.BRASS, lw=1.4, zorder=3))
ax.text(7.0, 77.0, "BRASS = the 2 validators scored on REAL market P&L "
        "(VaR Coverage · Kupiec;  ES Coverage · Acerbi-Szekely)",
        color=D.BRASS, fontsize=8.4, fontweight="bold", va="center", ha="left",
        zorder=3)

# ── layout geometry ----------------------------------------------------------
LX, RX = 5.0, 95.0
BW = RX - LX
band_h = 11.2
gap = 2.6
top = 73.4          # top edge of band 1
NODE_H = 6.6

bands_y = []        # store (y, band_h) per layer for arrow routing


def layer(i, num, label):
    """Draw a light grouping band behind a layer; return its bottom-left y."""
    y = top - (i - 1) * (band_h + gap) - band_h
    # light band
    ax.add_patch(FancyBboxPatch((LX, y), BW, band_h,
                 boxstyle="round,pad=0.2,rounding_size=0.9",
                 facecolor=D.MIST, edgecolor=D.STEEL, lw=1.0, alpha=0.45, zorder=1))
    # layer index badge + label, vertically centred on the band's left rail
    ax.text(LX + 1.4, y + band_h / 2 + 1.6, num, color=D.BRASS, fontsize=15,
            fontweight="bold", va="center", ha="left", zorder=3,
            family="DejaVu Sans")
    ax.text(LX + 1.4, y + band_h / 2 - 2.1, label.upper(), color=D.STEEL,
            fontsize=6.6, fontweight="bold", va="center", ha="left", zorder=3,
            linespacing=1.15)
    bands_y.append((y, band_h))
    return y


def nodes(y, items, *, real_idx=(), left_inset=9.6, ts=8.4, bs=6.7):
    """Lay node boxes across a band; brass-highlight indices in real_idx."""
    n = len(items)
    pad_r = 2.4
    x0 = LX + left_inset
    usable = (RX - x0) - pad_r
    inner_gap = 1.7
    w = (usable - (n - 1) * inner_gap) / n
    ny = y + (band_h - NODE_H) / 2
    centers = []
    for k, (t, b) in enumerate(items):
        x = x0 + k * (w + inner_gap)
        real = k in real_idx
        ec = D.BRASS if real else D.STEEL
        fc = D.BRASS_LT if real else D.CARD
        D.node(ax, x, ny, w, NODE_H, t, body=b, ec=ec, fc=fc,
               ts=ts, bs=bs, lw=(1.6 if real else 1.2))
        centers.append(x + w / 2)
    return centers, ny, NODE_H


# ── Layer 1 — Real data feeds ------------------------------------------------
y1 = layer(1, "1", "Real\ndata feeds")
c1, _, _ = nodes(y1, [
    ("IMF PortWatch", "transits"),
    ("yfinance", "equities"),
    ("FRED", "macro"),
    ("GDELT", "geo-events"),
    ("Canal auth.", "Suez / Pan."),
    ("AIS", "vessels"),
    ("Comtrade", "trade flows"),
    ("Marine wx", "Open-Meteo"),
    ("OFAC", "sanctions"),
], left_inset=9.6, ts=7.4, bs=6.4)

# ── Layer 2 — Ingest & cache -------------------------------------------------
y2 = layer(2, "2", "Ingest\n& cache")
c2, _, _ = nodes(y2, [
    ("cache_manager", "TTL caches"),
    ("Bitemporal vintages", "point-in-time"),
    ("Quality / realness", "real-vs-modeled stamps"),
    ("Offline-safe fetch", "graceful degrade"),
])

# ── Layer 3 — Analytics (brass on the 2 real validators) ---------------------
y3 = layer(3, "3", "Analytics")
c3, _, _ = nodes(y3, [
    ("Stress Index", "SSI · 6 components"),
    ("risk_lab VaR/ES", "4 methods"),
    ("VaR Coverage", "Kupiec · real P&L"),
    ("ES Coverage", "Acerbi-Szekely"),
    ("alpha_engine", "net-of-cost signals"),
    ("Event studies", "prices × events"),
    ("DCF / MC", "valuation"),
], real_idx=(2, 3))

# ── Layer 4 — Engine ---------------------------------------------------------
y4 = layer(4, "4", "Engine")
c4, _, _ = nodes(y4, [
    ("Scheduler jobs", "cron cadence"),
    ("LLM narration", "briefing / TLDR"),
    ("Alert delivery", "6 channels"),
    ("Signal ledger", "point-in-time"),
])

# ── Layer 5 — Delivery -------------------------------------------------------
y5 = layer(5, "5", "Delivery")
c5, _, _ = nodes(y5, [
    ("Streamlit UI", "8 sections / 50+ tabs"),
    ("Daily briefing", "+ TLDR"),
    ("Investor report", "HTML / PDF"),
    ("Multi-channel alerts", "email / webhook"),
    ("JSON API", "programmatic"),
])

# ── vertical flow arrows between consecutive bands ---------------------------
# three rails: left / centre / right, routed through the band gaps
rails = [LX + BW * 0.30, LX + BW * 0.55, LX + BW * 0.80]
for i in range(len(bands_y) - 1):
    y_lower_top = bands_y[i][0]                       # bottom of upper band region
    y_upper_bot = bands_y[i + 1][0] + bands_y[i + 1][1]   # top of next band
    for xc in rails:
        D.arrow(ax, (xc, y_lower_top - 0.1), (xc, y_upper_bot + 0.1),
                color=D.STEEL_LT, lw=1.5, mut=11)

# ── footnote: illustrative / source labelling --------------------------------
ax.text(4, 5.2,
        "20 backtest validators in all; the 2 brass nodes are scored on the real "
        "cache (1,292 trading days × 6 shipping equities). The other 18 are "
        "deterministic walk-forward / structural tests.",
        color=D.SLATE, fontsize=7.6, va="top", ha="left")

D.save(fig, OUT)
print("wrote", OUT)
