"""VaR & ES Calibration on Real Data — two-panel data graph for Ship Tracker."""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _style as S
import numpy as np

OUT = "/Users/aaronmortara/MC/Models/Ship/cache/reports/ship-tracker/var-es-calibration.pdf"

fig, ax = S.new_page()

S.header(
    ax,
    "Ship Tracker",
    "VaR & ES Calibration on Real Data",
    "1,292 trading days x 6 shipping equities (5y) - 60-day EWMA window - "
    "Gaussian vs Student-t (nu=6)",
)

# (Color legends live on each chart panel; the redundant top chips were removed
#  to keep the masthead clean.)

# ══════════════════════════════════════════════════════════════════════════════
# LEFT PANEL — VaR breach rate (%) : 95% and 99% groups, Gaussian vs Student-t
# ══════════════════════════════════════════════════════════════════════════════
axL = fig.add_axes([0.065, 0.330, 0.40, 0.385])  # [left, bottom, w, h] fig frac
axL.set_facecolor(S.CARD)
for sp in ("top", "right"):
    axL.spines[sp].set_visible(False)
for sp in ("left", "bottom"):
    axL.spines[sp].set_color(S.LINE)

# data
grp_centers = np.array([0.0, 1.0])          # 95% group, 99% group
bw = 0.34
gauss = np.array([4.88, 1.70])
stud  = np.array([5.65, 1.16])
nominal = np.array([5.0, 1.0])

bG = axL.bar(grp_centers - bw / 2, gauss, width=bw, color=S.STEEL,
             edgecolor=S.NAVY, linewidth=0.8, zorder=3, label="Gaussian EWMA")
bT = axL.bar(grp_centers + bw / 2, stud, width=bw, color=S.BRASS,
             edgecolor="#7A5E1E", linewidth=0.8, zorder=3,
             label="Student-t EWMA (nu=6)")

# highlight the REJECTED bar (Gaussian 99%) in RED
bG[1].set_color(S.RED)
bG[1].set_edgecolor("#7A2424")
bG[1].set_linewidth(1.6)

# dashed nominal reference lines, one per group (label placed below the line so
# it never collides with the bar value annotations sitting above each bar)
for c, nom in zip(grp_centers, nominal):
    axL.plot([c - bw - 0.12, c + bw + 0.30], [nom, nom], ls=(0, (4, 3)),
             color=S.SLATE, lw=1.4, zorder=4)
    axL.text(c + bw + 0.33, nom, f"nominal\n{nom:.1f}%", color=S.SLATE,
             fontsize=7.0, va="center", ha="left", style="italic",
             linespacing=1.1)

# bar annotations: value + Kupiec p verdict
def annot(bar, val, ptxt, verdict, vcolor):
    bx = bar.get_x() + bar.get_width() / 2
    axL.text(bx, val + 0.10, f"{val:.2f}%", ha="center", va="bottom",
             fontsize=8.6, fontweight="bold", color=S.INK, zorder=5)
    axL.text(bx, val + 0.46, ptxt, ha="center", va="bottom", fontsize=6.8,
             color=S.SLATE, zorder=5)
    axL.text(bx, val + 0.78, verdict, ha="center", va="bottom", fontsize=7.0,
             fontweight="bold", color=vcolor, zorder=5)

annot(bG[0], 4.88, "Kupiec p=0.84", "OK", S.GREEN)
annot(bT[0], 5.65, "Kupiec p=0.29", "OK", S.GREEN)
annot(bG[1], 1.70, "Kupiec p=0.021", "REJECTED", S.RED)
annot(bT[1], 1.16, "Kupiec p=0.57", "PASS", S.GREEN)

axL.set_xticks(grp_centers)
axL.set_xticklabels(["95% confidence", "99% confidence"], fontsize=9)
axL.set_ylabel("VaR breach rate (% of days)", fontsize=8.6, color=S.INK)
axL.set_ylim(0, 7.4)
axL.set_xlim(-0.62, 1.62)
axL.tick_params(axis="both", labelsize=8, colors=S.SLATE, length=3)
axL.set_yticks([0, 1, 2, 3, 4, 5, 6, 7])
axL.grid(axis="y", color=S.LINE, lw=0.5, alpha=0.6, zorder=0)
axL.set_axisbelow(True)
axL.legend(loc="upper right", fontsize=7.4, frameon=False, ncol=1,
           handlelength=1.2, bbox_to_anchor=(1.0, 1.0))

# panel title on the 0..100 ax (sits in the header gutter, clear of the chart)
ax.text(6.5, 82.4, "VaR coverage — breach rate vs nominal",
        color=S.INK, fontsize=11.5, fontweight="bold", ha="left", va="top")
ax.text(6.5, 79.8, "Gaussian under-states the 99% tail (red); Student-t keeps "
        "both levels un-rejected",
        color=S.SLATE, fontsize=8.2, ha="left", va="top")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT PANEL — ES coverage : realized tail loss vs ES forecast, 95% & 99%
# ══════════════════════════════════════════════════════════════════════════════
axR = fig.add_axes([0.565, 0.330, 0.385, 0.385])
axR.set_facecolor(S.CARD)
for sp in ("top", "right"):
    axR.spines[sp].set_visible(False)
for sp in ("left", "bottom"):
    axR.spines[sp].set_color(S.LINE)

es_centers = np.array([0.0, 1.0])
ebw = 0.34
realized = np.array([4.39, 6.06])   # realized tail loss
forecast = np.array([4.45, 6.17])   # ES forecast

rb = axR.bar(es_centers - ebw / 2, realized, width=ebw, color=S.STEEL_LT,
             edgecolor=S.STEEL, linewidth=0.9, zorder=3,
             label="Realized tail loss")
fb = axR.bar(es_centers + ebw / 2, forecast, width=ebw, color=S.BRASS,
             edgecolor="#7A5E1E", linewidth=0.9, zorder=3,
             label="ES forecast (ewma_t)")

def annot_es(bar, val):
    bx = bar.get_x() + bar.get_width() / 2
    axR.text(bx, val + 0.10, f"{val:.2f}%", ha="center", va="bottom",
             fontsize=8.4, fontweight="bold", color=S.INK, zorder=5)

for b, v in zip(rb, realized):
    annot_es(b, v)
for b, v in zip(fb, forecast):
    annot_es(b, v)

# Z2 verdict callouts above each group
z2_info = [
    (0.0, 6.20, "Z2 = -0.13", "crit -0.21", "WELL-SCALED"),
    (1.0, 7.10, "Z2 = -0.15", "crit -0.48", "WELL-SCALED"),
]
for c, ytop, z, crit, verdict in z2_info:
    axR.text(c, ytop, z, ha="center", va="bottom", fontsize=7.8,
             fontweight="bold", color=S.INK, zorder=5)
    axR.text(c, ytop - 0.42, crit, ha="center", va="bottom", fontsize=6.8,
             color=S.SLATE, zorder=5)
    axR.text(c, ytop + 0.46, verdict, ha="center", va="bottom", fontsize=7.2,
             fontweight="bold", color=S.GREEN, zorder=5)

axR.set_xticks(es_centers)
axR.set_xticklabels(["95% confidence", "99% confidence"], fontsize=9)
axR.set_ylabel("Tail loss / ES (% of position)", fontsize=8.6, color=S.INK)
axR.set_ylim(0, 8.6)
axR.set_xlim(-0.62, 1.62)
axR.tick_params(axis="both", labelsize=8, colors=S.SLATE, length=3)
axR.set_yticks([0, 2, 4, 6, 8])
axR.grid(axis="y", color=S.LINE, lw=0.5, alpha=0.6, zorder=0)
axR.set_axisbelow(True)
axR.legend(loc="upper left", fontsize=7.4, frameon=False, ncol=1,
           handlelength=1.2, bbox_to_anchor=(0.0, 1.0))

ax.text(57.5, 82.4, "ES coverage — realized vs forecast (Acerbi-Szekely)",
        color=S.INK, fontsize=11.5, fontweight="bold", ha="left", va="top")
ax.text(57.5, 79.8, "Realized tail loss tracks the Student-t ES at both levels — "
        "the ES is honest",
        color=S.SLATE, fontsize=8.2, ha="left", va="top")

# ══════════════════════════════════════════════════════════════════════════════
# BREAKDOWN — "How to read this"
# ══════════════════════════════════════════════════════════════════════════════
S.breakdown(
    ax, 4.0, 6.2, 92.0,
    "How to read this",
    [
        "BREACH RATE = share of days the realized loss exceeded the VaR; it "
        "should sit at the nominal level (5.0% at 95%, 1.0% at 99%).",
        "KUPIEC POF test: p < 0.05 = breach count too far from nominal to be "
        "chance -> VaR REJECTED. Gaussian 99% fails (p=0.021); Student-t passes.",
        "ACERBI-SZEKELY Z2 grades the Expected Shortfall on breach days: ~0 = "
        "well-scaled; negative past the critical value = ES under-states the tail.",
        "Both ES levels clear (Z2 above crit), so the Expected Shortfall is "
        "honest, not just the VaR quantile.",
        "TAKEAWAY: the Gaussian tail under-states the 99% fat tail; Student-t "
        "(nu=6) fixes the VaR AND keeps the ES well-scaled at both confidences.",
        "Two of the 20 backtest validators run on this real market cache; the "
        "other 18 are deterministic walk-forward / structural tests.",
    ],
    h=18.5,
    body_size=8.2,
)

S.footer(
    ax,
    right="risk_lab - VaR Coverage (Kupiec POF) + ES Coverage (Acerbi-Szekely) - "
          "real-cache validators",
)

S.save(fig, OUT)
print("wrote", OUT)
