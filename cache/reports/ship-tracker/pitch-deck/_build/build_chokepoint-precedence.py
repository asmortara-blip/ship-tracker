"""R267 — Lighting up Suez & Panama from real data. A precedence-ladder flowchart."""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _style as S

OUT = "/Users/aaronmortara/MC/Models/Ship/cache/reports/ship-tracker/chokepoint-precedence.pdf"

fig, ax = S.new_page()

S.header(
    ax,
    "Ship Tracker",
    "Lighting Up Suez & Panama from Real Data (R267)",
    "Precedence resolver for a canal chokepoint node: real PortWatch transit  >  real canal scrape  >  hardcoded baseline.",
)
S.footer(ax, right="R267 · apply_live_canal_nodes · escalate-only, ratchet-free")

# ── Geometry ───────────────────────────────────────────────────────────────
# Flow runs top -> bottom on the left 2/3 of the page; reference column on the right.
CX = 26.0          # centre x of the main flow column
BW = 36.0          # box width for the main flow nodes
LX = CX - BW / 2   # left edge of main column

# ── START node ──────────────────────────────────────────────────────────────
S.box(ax, LX, 78.0, BW, 8.6,
      "Canal node: Suez / Panama",
      body="#1 SSI chokepoint weight (~0.29)\nresolve today's risk level",
      fc=S.MIST, ec=S.NAVY, tc=S.INK, title_size=10.5, body_size=8.0)

# ── DECISION 1: PortWatch ─────────────────────────────────────────────────────
S.box(ax, LX, 66.5, BW, 7.2,
      "Real IMF PortWatch transit available?",
      fc=S.CARD, ec=S.STEEL, tc=S.INK, title_size=9.8)
S.arrow(ax, (CX, 78.0), (CX, 73.7 + 1.6), color=S.NAVY, lw=1.8)

# ── DECISION 1 -> YES branch (PortWatch drive) ────────────────────────────────
PW_X = 60.0  # YES boxes shift right
S.box(ax, PW_X, 66.8, 33.0, 8.6,
      "Drive from PortWatch",
      body="transit_drop_ratio -> risk level\nvalidator-grade, escalate-only",
      fc=S.CARD, ec=S.BRASS, tc=S.INK, title_size=10, body_size=8.0)
S.arrow(ax, (LX + BW, 70.1), (PW_X, 70.1), color=S.GREEN, lw=1.8)
ax.text((LX + BW + PW_X) / 2, 71.4, "YES", color=S.GREEN, fontsize=8.4,
        fontweight="bold", ha="center", va="bottom")
S.arrow(ax, (PW_X + 8, 66.8), (PW_X + 8, 63.7), color=S.GREEN, lw=1.5)
S.chip(ax, PW_X + 1.5, 60.8, "REAL (PortWatch)", fc="#DCEBE2", ec=S.GREEN, tc=S.GREEN, size=8)

# ── DECISION 1 -> NO -> DECISION 2: canal scrape ─────────────────────────────
S.box(ax, LX, 53.0, BW, 7.2,
      "Real canal scrape (not synthetic)?",
      fc=S.CARD, ec=S.STEEL, tc=S.INK, title_size=9.8)
S.arrow(ax, (CX, 66.5), (CX, 60.2 + 1.6), color=S.STEEL, lw=1.8)
ax.text(CX + 1.5, 64.0, "NO", color=S.SLATE, fontsize=8.4, fontweight="bold",
        ha="left", va="center")

# ── DECISION 2 -> YES branch (canal scrape) ──────────────────────────────────
S.box(ax, PW_X, 51.0, 33.0, 8.6,
      "Use canal scrape",
      body="Suez / Panama authority feed\nescalate-only",
      fc=S.CARD, ec=S.BRASS, tc=S.INK, title_size=10, body_size=8.0)
S.arrow(ax, (LX + BW, 56.6), (PW_X, 56.6), color=S.GREEN, lw=1.8)
ax.text((LX + BW + PW_X) / 2, 57.9, "YES", color=S.GREEN, fontsize=8.4,
        fontweight="bold", ha="center", va="bottom")
S.arrow(ax, (PW_X + 8, 51.0), (PW_X + 8, 48.0), color=S.GREEN, lw=1.5)
S.chip(ax, PW_X + 1.5, 45.0, "REAL (canal)", fc="#DCEBE2", ec=S.GREEN, tc=S.GREEN, size=8)

# ── DECISION 2 -> NO -> hardcoded baseline ───────────────────────────────────
S.box(ax, LX, 39.5, BW, 8.2,
      "Hold hardcoded baseline",
      body="no real overlay this cycle\nnever downgrades the node",
      fc="#ECECEC", ec=S.SLATE, tc=S.INK, title_size=10, body_size=8.0)
S.arrow(ax, (CX, 53.0), (CX, 47.7 + 0.0), color=S.STEEL, lw=1.8)
ax.text(CX + 1.5, 50.4, "NO", color=S.SLATE, fontsize=8.4, fontweight="bold",
        ha="left", va="center")
S.chip(ax, LX + 1.5, 35.0, "MODELED (baseline)", fc="#E6E6E6", ec=S.SLATE, tc=S.SLATE, size=8)
S.arrow(ax, (LX + 9, 39.5), (LX + 9, 38.0), color=S.SLATE, lw=1.5)

# ── RIGHT COLUMN: transit_drop -> level mapping ──────────────────────────────
MAP_X, MAP_Y, MAP_W, MAP_H = 70.5, 22.0, 26.0, 21.0
S.box(ax, MAP_X, MAP_Y, MAP_W, MAP_H, "", fc=S.CARD, ec=S.BRASS, lw=1.3)
ax.text(MAP_X + MAP_W / 2, MAP_Y + MAP_H - 2.3, "transit_drop_ratio → risk level",
        color=S.INK, fontsize=9.6, fontweight="bold", ha="center", va="top")
ax.text(MAP_X + MAP_W / 2, MAP_Y + MAP_H - 5.0, "(PortWatch drive path)",
        color=S.SLATE, fontsize=7.8, ha="center", va="top")

map_rows = [
    ("≥ 0.50", "CRITICAL", S.RED),
    ("≥ 0.30", "HIGH",     S.AMBER),
    ("≥ 0.15", "MODERATE", S.STEEL),
    ("else",   "LOW",      S.GREEN),
]
ry = MAP_Y + MAP_H - 8.6
for cond, lvl, col in map_rows:
    ax.text(MAP_X + 2.4, ry, cond, color=S.INK, fontsize=9.0, fontweight="bold",
            ha="left", va="center")
    S.chip(ax, MAP_X + 11.5, ry - 1.5, lvl, fc=S.CARD, ec=col, tc=col, size=8)
    ry -= 3.6
ax.text(MAP_X + 2.4, ry - 0.2, "None / short history → LOW",
        color=S.SLATE, fontsize=7.6, ha="left", va="center")

# ── BREAKDOWN: how it works / key rules ──────────────────────────────────────
S.breakdown(
    ax, 4.0, 6.0, 62.0,
    "How it works — precedence resolver rules",
    [
        "Precedence: real PortWatch transit  >  real canal scrape  >  baseline.",
        "Escalate-only & ratchet-free: a normal/cleared reading never",
        "   downgrades a node below its pristine baseline.",
        "A present real signal is never overridden by the synthetic feed;",
        "   reassigned via dataclasses.replace so identity lookups stay stable.",
        "Effect: the two highest-leverage SSI chokepoints — once dark behind",
        "   a near-always-synthetic scrape — now run on real data.",
    ],
    h=23.5,
)

S.save(fig, OUT)
print("wrote", OUT)
