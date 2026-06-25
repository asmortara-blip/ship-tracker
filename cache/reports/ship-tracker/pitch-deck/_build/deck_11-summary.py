"""Slide 11 / 11 — closing summary divider: "Why Ship Tracker".

Dark section-divider. KPI band (4 hero stats) + the thesis takeaways on the left,
the self-compounding research engine (funnel) on the right, and the closing
tagline in brass.
"""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D
from matplotlib.lines import Line2D

OUT = ("/Users/aaronmortara/MC/Models/reports/final/"
       "Ship Tracker/Pitch Deck (v2)/11-summary.pdf")

fig, ax = D.divider(11, "summary", "Why Ship Tracker")

# Subtitle line under the title (title area is the top; keep body below ~78).
ax.text(4, 81.2,
        "Maritime disruption fused with coverage-tested portfolio risk — under an "
        "explicit real-vs-modeled discipline.",
        color=D.MIST, fontsize=11.5, va="top", ha="left")

# ── KPI band: 4 hero stats ───────────────────────────────────────────────────
D.kpi_band(ax, 73.5,
           [("1,292", "days back-tested", "real cache"),
            ("6", "shipping equities", "5-yr daily tape"),
            ("20", "live validators", "2 on real P&L"),
            ("0", "synthetic shown as real", "honesty discipline")],
           dark=True)

# ── Left: the thesis takeaways ───────────────────────────────────────────────
takeaways = [
    "Real data end-to-end — IMF PortWatch\ntransits, equity tape & macro feeds.",
    "The only shipping-risk platform whose\nVaR/ES is coverage-tested vs realized P&L.",
    "Chokepoints driven by real transit:\nSuez & Panama by live precedence (R267).",
    "Honesty discipline — every feed stamped\nreal vs modeled; nothing faked as real.",
    "A self-compounding research engine —\n56-agent panel → vetted, shipped recs.",
]
D.note_panel(ax, 4, 26.5, 52, 28.0, "The thesis", takeaways,
             dark=True, size=9.6, gap=5.0)

# ── Right: the self-compounding research engine ──────────────────────────────
D.panel(ax, 60, 26.5, 36, 28.0, title="Research engine", dark=True)
ax.text(62.0, 49.6,
        "A multi-persona idea panel compounds the\n"
        "backlog: every cycle adds net-new, real-data-\n"
        "testable ideas — then dedups, scores, ships.",
        color=D.MIST, fontsize=8.8, va="top", ha="left", linespacing=1.4)

# Funnel inside the panel.
D.funnel(ax, 62.0, 31.5, 32.0, [("60", "raw"), ("43", "unique"),
                                ("14", "survivors"), ("13", "shipped")], h=9.5)

# ── Closing tagline (brass, confident) ───────────────────────────────────────
ax.add_line(Line2D([4, 96], [21.5, 21.5], color="#2A3F54", lw=0.8, zorder=3))
ax.text(50, 14.0, "The risk you can actually back-test.",
        color=D.BRASS, fontsize=27, fontweight="bold", style="italic",
        va="center", ha="center", zorder=4)
ax.text(50, 7.2,
        "Real chokepoint transits · coverage-tested VaR & Expected-Shortfall · "
        "validated against realized P&L.",
        color=D.MIST, fontsize=9.5, va="center", ha="center", zorder=4)

# Provenance note for the market-context framing (small, honest).
ax.text(96, 23.6, "Stats are Ship Tracker product output, not market estimates.",
        color=D.SLATE, fontsize=6.8, va="bottom", ha="right", zorder=4)

D.save(fig, OUT)
import os
sz = os.path.getsize(OUT)
print(f"wrote {OUT} ({sz} bytes)")
