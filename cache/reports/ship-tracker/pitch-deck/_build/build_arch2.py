import sys; sys.path.insert(0, "/tmp/viz_ship")
import _style as S
from matplotlib.lines import Line2D

OUT = "/Users/aaronmortara/MC/Models/Ship/cache/reports/ship-tracker/system-architecture.pdf"
fig, ax = S.new_page()

S.header(ax, "Ship Tracker", "End-to-End Architecture",
         "Real-data feeds flow down through ingest, analytics, and the engine to delivery — labelled real vs modeled at every step.")
ax.text(6, 84.2, "Brass nodes = the two validators scored on REAL market P&L  (VaR Coverage · Kupiec;  ES Coverage · Acerbi-Szekely).",
        color=S.BRASS, fontsize=8.6, fontweight="bold", ha="left", va="center")

LX, RX = 6.0, 96.0
BW = RX - LX
band_h = 11.2
gap = 3.0
top = 79.5
NODE_H = 6.0

def layer(i, label):
    y = top - i * (band_h + gap)
    S.band(ax, LX, y, BW, band_h, "", fc=S.MIST, ec=S.STEEL)
    ax.text(LX + 1.4, y + band_h - 1.3, label.upper(), color=S.STEEL,
            fontsize=8.5, fontweight="bold", va="top", ha="left", zorder=3)
    return y

def nodes(y, items, *, real_idx=()):
    n = len(items); pad = 2.2
    usable = BW - 2 * pad
    w = (usable - (n - 1) * 1.8) / n
    ny = y + 1.2
    for k, (t, b) in enumerate(items):
        x = LX + pad + k * (w + 1.8)
        ec = S.BRASS if k in real_idx else S.STEEL
        fc = S.BRASS_LT if k in real_idx else S.CARD
        S.box(ax, x, ny, w, NODE_H, t, body=b, ec=ec, fc=fc,
              title_size=8.4, body_size=6.9, round_pad=0.4, lw=1.2)

y1 = layer(1, "1 · Real data feeds")
nodes(y1, [("IMF PortWatch","chokepoint transit"), ("yfinance","5-yr equities"),
           ("FRED","macro series"), ("GDELT","geo-events"),
           ("Canal auth.","Suez / Panama"), ("AIS","vessel tracks"),
           ("Comtrade","trade flows"), ("Marine wx","Open-Meteo"),
           ("OFAC","sanctions")])
y2 = layer(2, "2 · Ingest & cache")
nodes(y2, [("cache_manager","TTL caches"), ("Bitemporal vintages","point-in-time"),
           ("Quality / realness","real-vs-modeled stamps"), ("Offline-safe fetch","graceful degrade")])
y3 = layer(3, "3 · Analytics")
nodes(y3, [("Shipping Stress Index","6 components"), ("risk_lab","VaR / ES"),
           ("20 backtest validators","real + synthetic"), ("alpha_engine","signals"),
           ("Event studies","prices x events"), ("DCF / MC","valuation")],
      real_idx=(1, 2))
y4 = layer(4, "4 · Engine")
nodes(y4, [("Scheduler jobs","cron cadence"), ("LLM narration","briefing / TLDR"),
           ("Alert delivery","6 channels"), ("Signal ledger","point-in-time, net of cost")])
y5 = layer(5, "5 · Delivery")
nodes(y5, [("Streamlit UI","8 sections / 50+ tabs"), ("Daily briefing","+ TLDR"),
           ("Investor report","HTML / PDF"), ("Multi-channel alerts","email / webhook"),
           ("JSON API","programmatic")])

for xc in (LX + BW * 0.18, LX + BW * 0.5, LX + BW * 0.82):
    for i in range(1, 5):
        ytop = top - i * (band_h + gap)
        ybot = top - (i + 1) * (band_h + gap) + band_h
        S.arrow(ax, (xc, ytop + 0.2), (xc, ybot - 0.2), color=S.STEEL_LT, lw=1.4, mut=11)

S.footer(ax, right="System architecture · 5-layer stack")
S.save(fig, OUT)
print("wrote", OUT)
