"""Slide 05 — The Data Moat. Breadth of real, point-in-time data.

4 domain panels (MARITIME / MARKET / MACRO / GEOPOLITICAL & TRADE), each a grid
of real-feed nodes; an ingest-discipline strip underneath emphasising TTL caching,
bitemporal point-in-time vintages, per-feed real-vs-modeled stamps and offline-safe
fetch. Reads as a credibility / moat slide.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/"
       "Pitch Deck (v2)/05-data-moat.pdf")

fig, ax = D.slide(
    5, "real inputs", "Breadth of real, point-in-time data",
    subtitle="Four feed domains, fused under one ingest discipline.",
)

# ── small feed-node helper (rounded chip with feed name + terse provenance) ────
def feed_node(x, y, w, h, name, prov, *, hero=False):
    fc = D.BRASS_LT if hero else D.CARD
    ec = D.BRASS if hero else D.STEEL
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle="round,pad=0.25,rounding_size=0.7",
                 facecolor=fc, edgecolor=ec, lw=1.15, zorder=3))
    ax.text(x + w / 2, y + h - 1.45, name, ha="center", va="top", color=D.INK,
            fontsize=8.6, fontweight="bold", zorder=4)
    ax.text(x + w / 2, y + h - 1.45 - 2.75, prov, ha="center", va="top",
            color=D.SLATE, fontsize=6.9, zorder=4, linespacing=1.18)


# ── domain panel: title + 2x2 (or 2x1) node grid inside ───────────────────────
def domain_panel(x, y, w, h, title, count_lbl, nodes):
    D.panel(ax, x, y, w, h, title=title)
    # count badge top-right inside the panel header band
    ax.text(x + w - 1.9, y + h - 2.0, count_lbl, ha="right", va="top",
            color=D.SLATE, fontsize=7.8, fontweight="bold", zorder=3)
    # node grid: 2 columns
    pad = 1.9
    gx = 1.7   # gap between cols
    gy = 1.7   # gap between rows
    inner_x = x + pad
    inner_top = y + h - 5.0           # below the panel heading
    inner_w = w - 2 * pad
    nw = (inner_w - gx) / 2.0
    rows = (len(nodes) + 1) // 2
    nh = ((inner_top - (y + pad)) - gy * (rows - 1)) / rows
    for i, (nm, pr, hero) in enumerate(nodes):
        r, c = divmod(i, 2)
        nx = inner_x + c * (nw + gx)
        ny = inner_top - nh - r * (nh + gy)
        feed_node(nx, ny, nw, nh, nm, pr, hero=hero)


# ── 2x2 grid of domain panels ─────────────────────────────────────────────────
GX, GW = 4.0, 92.0          # grid x-extent
col_gap = 3.0
PW = (GW - col_gap) / 2.0   # panel width
PLx = GX
PRx = GX + PW + col_gap

TOP_EDGE = 76.0            # top edge of the upper panel row (below subtitle)
row_gap = 2.6
PH = 21.0                   # panel height
TROW = TOP_EDGE - PH       # bottom-anchor of top row
BROW = TROW - row_gap - PH # bottom-anchor of bottom row

domain_panel(
    PLx, TROW, PW, PH, "Maritime", "4 feeds",
    [("IMF PortWatch", "daily chokepoint\nvessel transits", True),
     ("AIS / aisstream", "live vessel\npositions", False),
     ("Canal authorities", "Suez · Panama\ntransit scrapes", False),
     ("Open-Meteo", "marine weather\n& sea state", False)],
)
domain_panel(
    PRx, TROW, PW, PH, "Market", "2 feeds",
    [("yfinance", "5-yr equities ZIM MATX\nSBLK DAC CMRE STNG", True),
     ("Alpha Vantage", "company\nfundamentals", False)],
)
domain_panel(
    PLx, BROW, PW, PH, "Macro", "3 feeds",
    [("FRED", "BDI · Brent/WTI\nDXY · VIX", True),
     ("World Bank", "development\nindicators", False),
     ("OECD", "macro\nstatistics", False)],
)
domain_panel(
    PRx, BROW, PW, PH, "Geopolitical & Trade", "3 feeds",
    [("GDELT", "geo-located\nevent stream", True),
     ("UN Comtrade", "bilateral\ntrade flows", False),
     ("OFAC (SDN)", "sanctions\nscreening", False)],
)

# ── ingest-discipline strip (below the grid) ──────────────────────────────────
SH = 9.0
SY = BROW - 3.2 - SH       # strip bottom (3.2 gap below bottom panel row)
D.panel(ax, GX, SY, GW, SH, title="Ingest discipline — applied to every feed before analytics")

disc = [
    ("TTL cache_manager", "rate-safe, offline-resilient"),
    ("Bitemporal vintages", "point-in-time, no look-ahead"),
    ("Real-vs-modeled stamp", "per-feed data-quality flag"),
    ("Offline-safe fetch", "graceful synthetic fallback"),
]
n = len(disc)
seg = (GW - 3.2) / n
for i, (h1, h2) in enumerate(disc):
    cx = GX + 1.6 + seg * i + seg / 2.0
    ax.text(cx, SY + SH - 4.7, h1, ha="center", va="center", color=D.INK,
            fontsize=8.9, fontweight="bold", zorder=4)
    ax.text(cx, SY + SH - 7.0, h2, ha="center", va="center", color=D.SLATE,
            fontsize=7.6, zorder=4)
    if i > 0:
        dvx = GX + 1.6 + seg * i
        ax.add_line(Line2D([dvx, dvx], [SY + 1.3, SY + SH - 3.4],
                           color=D.BRASS, lw=1.0, alpha=0.65, zorder=3))

# ── moat / legend footnote line beneath the strip ─────────────────────────────
ax.add_patch(FancyBboxPatch((GX, SY - 5.4), 2.6, 2.6,
             boxstyle="round,pad=0.2,rounding_size=0.6",
             facecolor=D.BRASS_LT, edgecolor=D.BRASS, lw=1.0, zorder=3))
ax.text(GX + 3.4, SY - 4.1,
        "Brass = the hero feed per domain.  Twelve real sources across four "
        "domains, every one cached, vintaged and stamped — the breadth, not any "
        "one feed, is the moat.",
        ha="left", va="center", color=D.SLATE, fontsize=8.0, zorder=4)

# market-context honesty footnote
ax.text(GX, SY - 8.0,
        "Feeds, cache & stamps are product capability.  ~80% of world goods "
        "trade moves by sea (UNCTAD) — illustrative · public sources.",
        ha="left", va="center", color=D.SLATE, fontsize=7.2, zorder=4)

D.save(fig, OUT)
print("wrote", OUT)
