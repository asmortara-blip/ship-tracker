"""
Fundamentals Tab — Goldman Sachs equity-research quality shipping stock analysis.

Sections
--------
1. Coverage Universe Header  — title, stock count badge, sector rating
2. Stock Screening Table     — full universe table with color-coded returns & ratings
3. Valuation Matrix          — P/E bar chart + EV/EBITDA vs P/NAV scatter
4. Stock Deep-Dive Expanders — income statement, ratios, consensus, news
5. Dividend & Yield Tracker  — yield, payout, coverage, history, stability
6. Relative Value Heatmap    — stocks × metrics cheapness/expensiveness grid
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Static universe data ────────────────────────────────────────────────────────
# Columns: ticker, company, price, day_pct, wtd_pct, mtd_pct, ytd_pct,
#          pe, ev_ebitda, p_nav, yield_pct, rating, target, mktcap_b, subsector
UNIVERSE: list[dict] = [
    {"ticker":"ZIM",  "company":"ZIM Integrated Shipping", "price":16.42, "day":-1.2, "wtd":-3.1, "mtd":-5.8, "ytd":-18.4, "pe":4.1,  "ev_ebitda":2.8,  "p_nav":0.52, "div":14.2, "rating":"BUY",  "target":22.00, "mktcap":1.97,  "sub":"container"},
    {"ticker":"MATX", "company":"Matson Inc.",             "price":118.30,"day": 0.4, "wtd": 1.2, "mtd": 2.1, "ytd":  6.3, "pe":11.2, "ev_ebitda":6.4,  "p_nav":1.80, "div": 1.8, "rating":"HOLD", "target":122.00,"mktcap":3.81,  "sub":"container"},
    {"ticker":"DAC",  "company":"Danaos Corp.",            "price":68.15, "day": 0.7, "wtd": 1.8, "mtd": 3.4, "ytd":  4.1, "pe":4.8,  "ev_ebitda":4.2,  "p_nav":0.61, "div": 5.9, "rating":"BUY",  "target":82.00, "mktcap":1.36,  "sub":"container"},
    {"ticker":"GSL",  "company":"Global Ship Lease",       "price":19.88, "day": 0.3, "wtd": 0.9, "mtd":-1.2, "ytd": -3.8, "pe":3.9,  "ev_ebitda":5.1,  "p_nav":0.55, "div": 9.6, "rating":"BUY",  "target":26.00, "mktcap":0.59,  "sub":"container"},
    {"ticker":"CMRE", "company":"Costamare Inc.",          "price":10.14, "day":-0.5, "wtd":-1.1, "mtd":-2.3, "ytd": -7.2, "pe":5.6,  "ev_ebitda":6.8,  "p_nav":0.48, "div": 6.3, "rating":"BUY",  "target":13.50, "mktcap":1.06,  "sub":"container"},
    {"ticker":"SBLK", "company":"Star Bulk Carriers",      "price":14.62, "day":-0.8, "wtd":-2.4, "mtd":-4.1, "ytd":-12.3, "pe":8.1,  "ev_ebitda":5.6,  "p_nav":0.68, "div":11.4, "rating":"HOLD", "target":15.50, "mktcap":1.47,  "sub":"bulker"},
    {"ticker":"GOGL", "company":"Golden Ocean Group",      "price":8.24,  "day":-1.4, "wtd":-3.2, "mtd":-5.9, "ytd":-16.1, "pe":9.3,  "ev_ebitda":6.1,  "p_nav":0.71, "div":12.8, "rating":"HOLD", "target": 9.00, "mktcap":1.67,  "sub":"bulker"},
    {"ticker":"NMM",  "company":"Navios Maritime Partners", "price":33.40, "day": 0.2, "wtd": 0.6, "mtd": 1.4, "ytd":  2.8, "pe":5.2,  "ev_ebitda":4.9,  "p_nav":0.59, "div": 7.2, "rating":"BUY",  "target":42.00, "mktcap":1.24,  "sub":"bulker"},
    {"ticker":"STNG", "company":"Scorpio Tankers",         "price":48.31, "day": 1.1, "wtd": 2.8, "mtd": 4.2, "ytd":  8.7, "pe":6.4,  "ev_ebitda":5.3,  "p_nav":0.84, "div": 4.8, "rating":"BUY",  "target":62.00, "mktcap":1.97,  "sub":"tanker"},
    {"ticker":"HAFNI","company":"Hafnia Ltd.",             "price":6.38,  "day": 0.6, "wtd": 1.4, "mtd": 2.8, "ytd":  5.2, "pe":7.2,  "ev_ebitda":4.8,  "p_nav":0.92, "div":13.6, "rating":"BUY",  "target": 8.20, "mktcap":2.78,  "sub":"tanker"},
    {"ticker":"DHT",  "company":"DHT Holdings",            "price":10.82, "day":-0.3, "wtd":-0.8, "mtd":-1.6, "ytd": -4.2, "pe":8.8,  "ev_ebitda":5.9,  "p_nav":0.89, "div":11.2, "rating":"HOLD", "target":11.50, "mktcap":1.58,  "sub":"tanker"},
    {"ticker":"FRO",  "company":"Frontline PLC",           "price":17.64, "day": 0.9, "wtd": 2.1, "mtd": 3.8, "ytd":  7.4, "pe":7.6,  "ev_ebitda":6.2,  "p_nav":0.95, "div": 8.9, "rating":"BUY",  "target":22.50, "mktcap":3.36,  "sub":"tanker"},
    {"ticker":"EURN", "company":"Euronav NV",              "price":13.28, "day":-0.6, "wtd":-1.4, "mtd":-2.8, "ytd": -8.6, "pe":9.1,  "ev_ebitda":6.8,  "p_nav":0.82, "div": 9.4, "rating":"HOLD", "target":14.00, "mktcap":2.24,  "sub":"tanker"},
    {"ticker":"TEN",  "company":"Tsakos Energy Navigation","price":18.50, "day":-0.2, "wtd":-0.5, "mtd":-1.1, "ytd": -3.4, "pe":6.9,  "ev_ebitda":5.7,  "p_nav":0.74, "div": 5.4, "rating":"HOLD", "target":20.00, "mktcap":0.88,  "sub":"tanker"},
    {"ticker":"INSW", "company":"Intl Seaways Inc.",       "price":34.82, "day": 0.5, "wtd": 1.2, "mtd": 2.4, "ytd":  4.8, "pe":5.8,  "ev_ebitda":4.6,  "p_nav":0.78, "div": 6.2, "rating":"BUY",  "target":44.00, "mktcap":1.24,  "sub":"tanker"},
    {"ticker":"TNK",  "company":"Teekay Tankers",          "price":28.14, "day": 0.8, "wtd": 1.9, "mtd": 3.6, "ytd":  6.9, "pe":5.1,  "ev_ebitda":3.9,  "p_nav":0.81, "div": 7.8, "rating":"BUY",  "target":36.00, "mktcap":1.32,  "sub":"tanker"},
    {"ticker":"NAT",  "company":"Nordic American Tankers", "price":3.82,  "day":-0.5, "wtd":-1.2, "mtd":-2.6, "ytd": -8.4, "pe":12.4, "ev_ebitda":7.8,  "p_nav":0.64, "div":10.8, "rating":"HOLD", "target": 4.20, "mktcap":0.58,  "sub":"tanker"},
    {"ticker":"ASC",  "company":"Ardmore Shipping",        "price":12.64, "day": 0.3, "wtd": 0.7, "mtd": 1.4, "ytd":  2.8, "pe":6.2,  "ev_ebitda":4.4,  "p_nav":0.88, "div": 8.6, "rating":"BUY",  "target":16.00, "mktcap":0.47,  "sub":"tanker"},
    {"ticker":"CPLP", "company":"Capital Product Partners","price":15.92, "day": 0.1, "wtd": 0.3, "mtd": 0.6, "ytd":  1.2, "pe":7.8,  "ev_ebitda":7.2,  "p_nav":0.92, "div": 7.4, "rating":"HOLD", "target":17.00, "mktcap":0.54,  "sub":"container"},
    {"ticker":"MRC",  "company":"Marco Polo Seatrade",     "price":5.14,  "day":-1.1, "wtd":-2.6, "mtd":-4.8, "ytd":-14.2, "pe":None, "ev_ebitda":8.4,  "p_nav":0.41, "div": 0.0, "rating":"SELL", "target": 4.00, "mktcap":0.12,  "sub":"bulker"},
]

QUARTERLY: dict[str, list] = {
    "ZIM":  [("Q3'25",1840,620,380),("Q2'25",2140,780,490),("Q1'25",1920,640,390),("Q4'24",2380,920,580)],
    "MATX": [("Q3'25",382,102,68),  ("Q2'25",396,108,72),  ("Q1'25",364,94,62),   ("Q4'24",418,118,78)],
    "DAC":  [("Q3'25",198,142,94),  ("Q2'25",204,148,98),  ("Q1'25",192,138,90),  ("Q4'24",210,154,102)],
    "GSL":  [("Q3'25",162,108,68),  ("Q2'25",168,112,72),  ("Q1'25",154,104,64),  ("Q4'24",174,116,76)],
    "CMRE": [("Q3'25",218,148,82),  ("Q2'25",226,154,88),  ("Q1'25",208,142,78),  ("Q4'24",234,162,94)],
    "SBLK": [("Q3'25",348,162,84),  ("Q2'25",362,172,92),  ("Q1'25",332,152,76),  ("Q4'24",378,184,98)],
    "GOGL": [("Q3'25",284,128,62),  ("Q2'25",296,136,68),  ("Q1'25",268,118,56),  ("Q4'24",312,144,74)],
    "NMM":  [("Q3'25",214,124,72),  ("Q2'25",222,130,78),  ("Q1'25",204,118,66),  ("Q4'24",232,136,84)],
    "STNG": [("Q3'25",412,224,114), ("Q2'25",428,234,122), ("Q1'25",394,212,106), ("Q4'24",446,246,132)],
    "HAFNI":[("Q3'25",364,196,104), ("Q2'25",382,208,112), ("Q1'25",344,182,96),  ("Q4'24",402,222,122)],
    "DHT":  [("Q3'25",168,86,44),   ("Q2'25",176,92,48),   ("Q1'25",158,80,40),   ("Q4'24",184,98,52)],
    "FRO":  [("Q3'25",482,262,136), ("Q2'25",504,278,148), ("Q1'25",456,244,124), ("Q4'24",528,294,162)],
    "EURN": [("Q3'25",386,196,98),  ("Q2'25",402,208,106), ("Q1'25",364,182,90),  ("Q4'24",422,218,114)],
    "TEN":  [("Q3'25",212,102,48),  ("Q2'25",222,108,52),  ("Q1'25",200,96,44),   ("Q4'24",234,114,58)],
    "INSW": [("Q3'25",198,112,58),  ("Q2'25",208,118,62),  ("Q1'25",188,106,54),  ("Q4'24",218,126,68)],
    "TNK":  [("Q3'25",286,148,74),  ("Q2'25",298,156,80),  ("Q1'25",272,140,68),  ("Q4'24",312,164,88)],
    "NAT":  [("Q3'25",84,38,16),    ("Q2'25",88,40,18),    ("Q1'25",78,34,14),    ("Q4'24",92,44,20)],
    "ASC":  [("Q3'25",98,54,28),    ("Q2'25",102,56,30),   ("Q1'25",92,50,26),    ("Q4'24",108,60,34)],
    "CPLP": [("Q3'25",112,68,32),   ("Q2'25",116,72,34),   ("Q1'25",106,64,30),   ("Q4'24",122,76,38)],
    "MRC":  [("Q3'25",28,6,-4),     ("Q2'25",24,4,-6),     ("Q1'25",32,8,-2),     ("Q4'24",22,2,-8)],
}

CONSENSUS: dict[str, list] = {
    "ZIM":  [("Goldman Sachs",22.00,"Buy"),("JPMorgan",20.00,"Neutral"),("Morgan Stanley",24.00,"Overweight"),("Citi",21.50,"Buy"),("BofA",19.00,"Neutral")],
    "MATX": [("Goldman Sachs",122.00,"Neutral"),("JPMorgan",128.00,"Overweight"),("Stifel",115.00,"Hold"),("Baird",120.00,"Neutral")],
    "DAC":  [("Goldman Sachs",82.00,"Buy"),("Jefferies",78.00,"Buy"),("Deutsche Bank",80.00,"Buy"),("Pareto",85.00,"Buy")],
    "GSL":  [("Goldman Sachs",26.00,"Buy"),("Jefferies",24.00,"Buy"),("Deutsche Bank",25.00,"Buy"),("Clarksons",27.00,"Buy")],
    "CMRE": [("Goldman Sachs",13.50,"Buy"),("JPMorgan",12.00,"Neutral"),("Jefferies",14.00,"Buy")],
    "SBLK": [("Goldman Sachs",15.50,"Neutral"),("JPMorgan",16.00,"Overweight"),("BofA",14.00,"Underperform"),("Pareto",17.00,"Buy")],
    "GOGL": [("Goldman Sachs",9.00,"Neutral"),("JPMorgan",10.00,"Overweight"),("ABN AMRO",8.50,"Hold"),("Arctic",9.50,"Buy")],
    "NMM":  [("Goldman Sachs",42.00,"Buy"),("Jefferies",40.00,"Buy"),("Deutsche Bank",38.00,"Buy"),("Stifel",44.00,"Buy")],
    "STNG": [("Goldman Sachs",62.00,"Buy"),("JPMorgan",65.00,"Overweight"),("Clarksons",58.00,"Buy"),("Pareto",64.00,"Buy")],
    "HAFNI":[("Goldman Sachs",8.20,"Buy"),("JPMorgan",8.00,"Overweight"),("ABN AMRO",7.80,"Buy"),("Arctic",8.50,"Buy")],
    "DHT":  [("Goldman Sachs",11.50,"Neutral"),("JPMorgan",12.00,"Neutral"),("Pareto",11.00,"Hold"),("Arctic",12.50,"Buy")],
    "FRO":  [("Goldman Sachs",22.50,"Buy"),("JPMorgan",24.00,"Overweight"),("BofA",21.00,"Neutral"),("Clarksons",23.00,"Buy")],
    "EURN": [("Goldman Sachs",14.00,"Neutral"),("JPMorgan",15.00,"Neutral"),("BofA",13.00,"Underperform"),("ABN AMRO",14.50,"Hold")],
    "TEN":  [("Goldman Sachs",20.00,"Neutral"),("Jefferies",21.00,"Hold"),("Pareto",19.00,"Hold")],
    "INSW": [("Goldman Sachs",44.00,"Buy"),("JPMorgan",46.00,"Overweight"),("BofA",42.00,"Neutral"),("Jefferies",45.00,"Buy")],
    "TNK":  [("Goldman Sachs",36.00,"Buy"),("JPMorgan",38.00,"Overweight"),("Pareto",34.00,"Buy"),("Arctic",37.00,"Buy")],
    "NAT":  [("Goldman Sachs",4.20,"Neutral"),("JPMorgan",4.00,"Neutral"),("ABN AMRO",4.50,"Hold")],
    "ASC":  [("Goldman Sachs",16.00,"Buy"),("Jefferies",15.00,"Buy"),("Pareto",17.00,"Buy")],
    "CPLP": [("Goldman Sachs",17.00,"Neutral"),("Jefferies",18.00,"Hold"),("Deutsche Bank",16.00,"Hold")],
    "MRC":  [("Goldman Sachs",4.00,"Sell"),("Pareto",3.50,"Sell"),("Arctic",4.50,"Hold")],
}

DIV_HIST: dict[str, list] = {
    "ZIM":  [2.10,1.85,1.60,0.72],
    "MATX": [0.35,0.35,0.32,0.32],
    "DAC":  [0.80,0.80,0.75,0.75],
    "GSL":  [0.375,0.375,0.35,0.35],
    "CMRE": [0.115,0.115,0.115,0.115],
    "SBLK": [0.35,0.40,0.45,0.50],
    "GOGL": [0.25,0.30,0.35,0.40],
    "NMM":  [0.60,0.60,0.55,0.55],
    "STNG": [0.40,0.40,0.35,0.35],
    "HAFNI":[0.17,0.19,0.22,0.24],
    "DHT":  [0.24,0.22,0.28,0.30],
    "FRO":  [0.30,0.32,0.35,0.42],
    "EURN": [0.24,0.28,0.30,0.36],
    "TEN":  [0.25,0.22,0.20,0.20],
    "INSW": [0.48,0.48,0.44,0.44],
    "TNK":  [0.55,0.55,0.50,0.50],
    "NAT":  [0.10,0.10,0.12,0.12],
    "ASC":  [0.28,0.25,0.22,0.22],
    "CPLP": [0.295,0.295,0.295,0.295],
    "MRC":  [0.00,0.00,0.00,0.00],
}

NEWS: dict[str, list] = {
    "ZIM":  ["Q4 earnings beat: EPS $1.42 vs $1.18E","New Asia-US West Coast capacity added","Red Sea normalization pressures spot rates"],
    "MATX": ["Premium Hawaii service pricing power intact","Guam military contract renewed through 2027","Operating ratio improves 180bps YoY"],
    "DAC":  ["Long-term charter coverage 94% through 2026","Scrubber retrofit program 70% complete","Dividend increase signals confidence"],
    "GSL":  ["Fleet average charter duration 3.2 years","Newbuild orderbook zero — pure spot upside","Share buyback $50M authorized"],
    "CMRE": ["Diversified fleet reduces single-segment risk","Charter re-pricing cycle beginning 2026","Credit facility refinanced at tighter spread"],
    "SBLK": ["BDI recovery supports Q2 guidance","Fleet eco-retrofits cut OPEX $800/day/vessel","EnBW partnership for methanol dual-fuel"],
    "GOGL": ["Capesize rates firming on Brazil iron ore demand","Merger synergies with Stelmar on track","Debt reduction ahead of schedule"],
    "NMM": ["Dropdown pipeline from Navios Holdings","Container charter coverage protects earnings","Leverage falling — 3.2x Net Debt/EBITDA"],
    "STNG": ["Product tanker demand supported by refinery dislocation","Fleet renewal complete — youngest in peer group","Scrubber spread economics favorable"],
    "HAFNI":["Product tanker rate environment improving","Merger with TORM creates scale advantages","ESG initiatives ahead of IMO 2030 targets"],
    "DHT": ["VLCC spot rates stabilizing above breakeven","Balance sheet strongest in company history","Opportunistic drydock schedule minimizes off-hire"],
    "FRO": ["VLCC fleet expansion — 4 newbuilds on order","Spot rate leverage highest in peer group","Tanker demand supported by long-haul crude flows"],
    "EURN": ["VLCC fleet repositioning to Atlantic trades","IMO 2026 compliance spending headwind","Scrubber adoption rate 45% of fleet"],
    "TEN": ["Diversified tanker fleet reduces volatility","Gas tanker segment outperforming","Dividend maintained despite rate softness"],
    "INSW":["Product + crude tanker mix reduces cycle risk","Fleet renewal funded from operating cash flow","ESG score upgrade from Sustainalytics"],
    "TNK": ["MR tanker fleet benefits from US export growth","Spot rate momentum positive into summer","Interest rate hedge locks in low fixed cost"],
    "NAT": ["Simplest story: pure VLCC spot leverage","High yield requires sustained rate environment","Balance sheet leverage limits flexibility"],
    "ASC": ["Chemical tanker specialization commands premium","Long-term contracts with major chemical producers","IMO 2 fleet positioned for tightening regulation"],
    "CPLP":["Stable MLP-like distribution track record","Container charter revenue highly visible","Leverage falling toward investment-grade metrics"],
    "MRC": ["Operational challenges persist in dry bulk","Management transition underway","Asset sales being explored — potential catalyst"],
}

_SECTOR_PE_AVG = 7.2


# ── Helper functions ────────────────────────────────────────────────────────────

def _pct_color(v: float | None) -> str:
    if v is None:
        return C_TEXT3
    return C_HIGH if v >= 0 else C_LOW


def _fmt_pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _consensus_badge(rating: str) -> str:
    rl = rating.lower()
    if any(x in rl for x in ["buy", "overweight", "outperform", "strong"]):
        color = C_HIGH
    elif any(x in rl for x in ["sell", "underperform", "reduce"]):
        color = C_LOW
    else:
        color = C_MOD
    return badge(rating, color=color)


def _sub_color(sub: str) -> str:
    return {"container": C_ACCENT, "bulker": C_MOD, "tanker": C_HIGH}.get(sub, C_TEXT3)


# ── Section 1: Coverage Universe Header ────────────────────────────────────────

def _render_header(df: pd.DataFrame) -> None:
    try:
        n = len(df)
        n_buy  = int((df["rating"] == "BUY").sum())
        n_hold = int((df["rating"] == "HOLD").sum())
        n_sell = int((df["rating"] == "SELL").sum())
        avg_upside = ((df["target"] - df["price"]) / df["price"] * 100).mean()
        sector_view = "OVERWEIGHT" if n_buy > n_hold else "NEUTRAL"
        sv_color = C_HIGH if sector_view == "OVERWEIGHT" else C_MOD

        metric_card_row([
            {
                "label":    "Stocks Covered",
                "value":    f"{n}",
                "sublabel": "Global shipping universe",
                "accent":   C_ACCENT,
            },
            {
                "label":    "Sector View",
                "value":    sector_view,
                "delta":    "Bias vs market-weight",
                "delta_color": sv_color,
                "accent":   sv_color,
            },
            {
                "label":    "Avg Upside to Target",
                "value":    f"+{avg_upside:.1f}%",
                "delta":    "12-month consensus",
                "delta_color": C_HIGH,
                "accent":   C_HIGH,
            },
            {
                "label":    "BUY / HOLD / SELL",
                "value":    f"{n_buy} / {n_hold} / {n_sell}",
                "delta":    f"{n_buy / max(n,1) * 100:.0f}% BUY",
                "delta_color": C_HIGH,
                "accent":   C_MOD,
            },
        ], columns=4)
    except Exception:
        logger.exception("header render failed")


# ── Section 2: Stock Screening Table ───────────────────────────────────────────

def _render_screening_table(df: pd.DataFrame) -> None:
    try:
        section_header("Stock Screening — Coverage Universe",
                       "Full universe with returns, multiples, yield, rating, and upside")

        headers = [
            "Ticker", "Company", "Price",
            "Day %", "WTD %", "MTD %", "YTD %",
            "P/E", "EV/EBITDA", "P/NAV", "Yield %",
            "Rating", "Target", "Upside %",
        ]
        table_rows = []
        for _, r in df.iterrows():
            upside = (r["target"] - r["price"]) / r["price"] * 100
            pe_disp = f'{r["pe"]:.1f}x' if r["pe"] else "—"
            rating_color = {"BUY": C_HIGH, "HOLD": C_MOD, "SELL": C_LOW}.get(
                str(r["rating"]).upper(), C_TEXT3
            )
            table_rows.append([
                _sans(str(r["ticker"]), color=C_ACCENT, weight=700),
                _sans(str(r["company"]), color=C_TEXT2),
                _mono(f"${r['price']:.2f}", color=C_TEXT, weight=700),
                _mono(_fmt_pct(r["day"]), color=_pct_color(r["day"]), weight=600),
                _mono(_fmt_pct(r["wtd"]), color=_pct_color(r["wtd"]), weight=600),
                _mono(_fmt_pct(r["mtd"]), color=_pct_color(r["mtd"]), weight=600),
                _mono(_fmt_pct(r["ytd"]), color=_pct_color(r["ytd"]), weight=600),
                _mono(pe_disp, color=C_TEXT2),
                _mono(f"{r['ev_ebitda']:.1f}x", color=C_TEXT2),
                _mono(f"{r['p_nav']:.2f}x", color=C_TEXT2),
                _mono(f"{r['div']:.1f}%", color=C_HIGH, weight=600),
                badge(str(r["rating"]), color=rating_color),
                _mono(f"${r['target']:.2f}", color=C_TEXT),
                _mono(_fmt_pct(upside),
                      color=C_HIGH if upside >= 0 else C_LOW, weight=700),
            ])
        wsj_market_table(headers, table_rows)
        st.markdown(
            source_footer([
                {"name": "Static coverage universe", "kind": "modeled", "quality": "demo"},
                {"name": "Sell-side consensus ratings", "kind": "modeled", "quality": "demo"},
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("screening table render failed")


# ── Section 3: Valuation Matrix ────────────────────────────────────────────────

def _render_valuation_matrix(df: pd.DataFrame) -> None:
    try:
        section_header("Valuation Matrix",
                       "P/E relative to sector and EV/EBITDA vs P/NAV scatter")
        col1, col2 = st.columns(2)

        with col1:
            try:
                pe_df = df[df["pe"].notna()].copy().sort_values("pe")
                colors = [C_HIGH if v <= _SECTOR_PE_AVG else C_MOD for v in pe_df["pe"]]
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=pe_df["ticker"], y=pe_df["pe"],
                    marker_color=colors, name="P/E",
                    text=[f'{v:.1f}x' for v in pe_df["pe"]],
                    textposition="outside", textfont=dict(color=C_TEXT2, size=10),
                ))
                fig.add_hline(
                    y=_SECTOR_PE_AVG,
                    line=dict(color=C_ACCENT, width=2, dash="dash"),
                    annotation_text=f"Sector Avg {_SECTOR_PE_AVG}x",
                    annotation_font_color=C_ACCENT,
                    annotation_font_size=10,
                )
                apply_dark_layout(
                    fig,
                    title="P/E vs Sector Average",
                    height=320,
                    showlegend=False,
                    yaxis=dict(ticksuffix="x"),
                )
                st.plotly_chart(fig, use_container_width=True, key="val_pe_bar")
            except Exception:
                logger.exception("P/E bar chart failed")
                st.info("P/E chart unavailable")

        with col2:
            try:
                sub_colors = {"container": C_ACCENT, "bulker": C_MOD, "tanker": C_HIGH}
                fig2 = go.Figure()
                for sub in ["container", "bulker", "tanker"]:
                    sub_df = df[df["sub"] == sub]
                    if sub_df.empty:
                        continue
                    fig2.add_trace(go.Scatter(
                        x=sub_df["p_nav"], y=sub_df["ev_ebitda"],
                        mode="markers+text",
                        marker=dict(
                            size=sub_df["mktcap"] * 6,
                            color=sub_colors[sub],
                            opacity=0.8,
                            line=dict(color=C_BORDER, width=1),
                        ),
                        text=sub_df["ticker"], textposition="top center",
                        textfont=dict(color=C_TEXT, size=9),
                        name=sub.capitalize(),
                        hovertemplate=(
                            "<b>%{text}</b><br>"
                            "P/NAV: %{x:.2f}x<br>"
                            "EV/EBITDA: %{y:.1f}x<br>"
                            "<extra></extra>"
                        ),
                    ))
                apply_dark_layout(
                    fig2,
                    title="EV/EBITDA vs P/NAV (size = market cap)",
                    height=320,
                    xaxis=dict(title="P/NAV"),
                    yaxis=dict(title="EV/EBITDA"),
                )
                st.plotly_chart(fig2, use_container_width=True, key="val_scatter")
            except Exception:
                logger.exception("scatter chart failed")
                st.info("Scatter chart unavailable")

        st.markdown(
            source_footer([
                {"name": "Static valuation multiples", "kind": "modeled", "quality": "demo"},
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("valuation matrix render failed")


# ── Section 4: Deep-Dive Expanders ─────────────────────────────────────────────

def _render_deep_dive(df: pd.DataFrame) -> None:
    try:
        section_header("Stock Deep-Dive",
                       "Per-ticker income statement, ratios, consensus, and recent newsflow")
        for _, r in df.iterrows():
            tk = r["ticker"]
            upside = (r["target"] - r["price"]) / r["price"] * 100
            label = f'{tk}  •  ${r["price"]:.2f}  •  {_fmt_pct(upside)} to target  •  {r["rating"]}'
            with st.expander(label, expanded=False):
                try:
                    c1, c2 = st.columns([3, 2])

                    with c1:
                        quarters = QUARTERLY.get(tk, [])
                        if quarters:
                            st.markdown(
                                '<div class="sub-section-header">'
                                'Quarterly Income Statement ($M)</div>',
                                unsafe_allow_html=True,
                            )
                            q_headers = ["Quarter", "Revenue", "EBITDA", "Net Income"]
                            q_rows = []
                            for q_lbl, rev, ebitda, ni in quarters:
                                ni_col = C_HIGH if ni >= 0 else C_LOW
                                q_rows.append([
                                    _sans(q_lbl, color=C_TEXT2),
                                    _mono(f"${rev:,}", color=C_TEXT, weight=600),
                                    _mono(f"${ebitda:,}", color=C_MOD, weight=600),
                                    _mono(f"${ni:,}", color=ni_col, weight=600),
                                ])
                            wsj_market_table(q_headers, q_rows)

                    with c2:
                        st.markdown(
                            '<div class="sub-section-header">Key Ratios</div>',
                            unsafe_allow_html=True,
                        )
                        pe_str = f'{r["pe"]:.1f}x' if r["pe"] else "—"
                        kr_rows = [
                            [_sans("P/E", color=C_TEXT3),
                             _mono(pe_str, color=C_TEXT, weight=600)],
                            [_sans("EV/EBITDA", color=C_TEXT3),
                             _mono(f'{r["ev_ebitda"]:.1f}x', color=C_TEXT, weight=600)],
                            [_sans("P/NAV", color=C_TEXT3),
                             _mono(f'{r["p_nav"]:.2f}x', color=C_TEXT, weight=600)],
                            [_sans("Div Yield", color=C_TEXT3),
                             _mono(f'{r["div"]:.1f}%', color=C_HIGH, weight=600)],
                            [_sans("Mkt Cap", color=C_TEXT3),
                             _mono(f'${r["mktcap"]:.2f}B', color=C_TEXT, weight=600)],
                            [_sans("Segment", color=C_TEXT3),
                             _sans(str(r["sub"]).upper(),
                                   color=_sub_color(r["sub"]), weight=600)],
                        ]
                        wsj_market_table(["Ratio", "Value"], kr_rows)

                    consensus = CONSENSUS.get(tk, [])
                    if consensus:
                        st.markdown(
                            '<div class="sub-section-header">Analyst Consensus</div>',
                            unsafe_allow_html=True,
                        )
                        c_headers = ["Bank", "Target", "Upside", "Rating"]
                        c_rows_tbl = []
                        for bank, tgt, rat in consensus:
                            up = (tgt - r["price"]) / r["price"] * 100
                            c_rows_tbl.append([
                                _sans(bank, color=C_TEXT2),
                                _mono(f"${tgt:.2f}", color=C_TEXT, weight=600),
                                _mono(_fmt_pct(up), color=C_HIGH if up >= 0 else C_LOW),
                                _consensus_badge(rat),
                            ])
                        wsj_market_table(c_headers, c_rows_tbl)

                    news_items = NEWS.get(tk, [])
                    if news_items:
                        st.markdown(
                            '<div class="sub-section-header">Recent Newsflow</div>',
                            unsafe_allow_html=True,
                        )
                        news_html = "".join(
                            f'<div class="wsj-news-item">'
                            f'<span class="wsj-news-bullet"></span>'
                            f'<span class="wsj-news-text">{item}</span>'
                            f'</div>'
                            for item in news_items
                        )
                        st.markdown(news_html, unsafe_allow_html=True)

                    st.markdown(
                        source_footer([
                            {"name": f"{tk} company filings", "kind": "modeled", "quality": "demo"},
                            {"name": "Sell-side consensus aggregator", "kind": "modeled", "quality": "demo"},
                        ]),
                        unsafe_allow_html=True,
                    )
                except Exception:
                    logger.exception(f"deep-dive for {tk} failed")
                    st.info(f"Deep-dive data unavailable for {tk}")
    except Exception:
        logger.exception("deep dive section failed")


# ── Section 5: Dividend & Yield Tracker ────────────────────────────────────────

def _render_dividend_tracker(df: pd.DataFrame) -> None:
    try:
        section_header("Dividend & Yield Tracker",
                       "Yield, payout ratio, coverage, 4-quarter history, and stability")

        headers = ["Ticker", "Div Yield", "Payout %", "Cov Ratio",
                   "History (Q1-Q4)", "Stability"]
        table_rows = []
        for _, r in df.iterrows():
            tk = r["ticker"]
            hist = DIV_HIST.get(tk, [0, 0, 0, 0])
            total_ttm = sum(hist)
            payout_cov = round(total_ttm / (r["price"] * 0.08), 2) if total_ttm > 0 else 0.0
            if len(hist) > 1 and np.mean(hist) > 0:
                stab = max(0, 1 - np.std(hist) / np.mean(hist))
                stab_pct = int(round(stab * 100))
            else:
                stab_pct = 0
            stab_color = C_HIGH if stab_pct >= 80 else (C_MOD if stab_pct >= 50 else C_LOW)
            hist_str = " / ".join(f"${v:.2f}" for v in hist)
            payout_disp = min(total_ttm / r["price"] * 100, 99)
            stability_cell = (
                f'<div class="progress-bar-custom">'
                f'<div class="progress-bar-fill"'
                f' style="width:{stab_pct}%;background:{stab_color};"></div>'
                f'</div>'
                f'<span style="font-family:var(--mono);color:{stab_color};">'
                f'{stab_pct}%</span>'
            )
            table_rows.append([
                _sans(tk, color=C_ACCENT, weight=700),
                _mono(f"{r['div']:.1f}%", color=C_HIGH, weight=700),
                _mono(f"{payout_disp:.0f}%", color=C_TEXT2),
                _mono(f"{payout_cov:.1f}x", color=C_TEXT2),
                _mono(hist_str, color=C_TEXT3),
                stability_cell,
            ])
        wsj_market_table(headers, table_rows)
        st.markdown(
            source_footer([
                {"name": "Static dividend history", "kind": "modeled", "quality": "demo"},
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("dividend tracker render failed")


# ── Section 6: Relative Value Heatmap ──────────────────────────────────────────

def _render_relative_value(df: pd.DataFrame) -> None:
    try:
        section_header("Relative Value Heatmap",
                       "Cross-stock cheapness / expensiveness across valuation multiples and yield")

        metrics = ["p_nav", "ev_ebitda", "pe", "div"]
        metric_labels = ["P/NAV", "EV/EBITDA", "P/E", "Yield%"]
        invert = {"p_nav": True, "ev_ebitda": True, "pe": True, "div": False}

        tickers = df["ticker"].tolist()
        z_matrix = []
        text_matrix = []

        for m in metrics:
            row_z = []
            row_t = []
            vals = df[m].values.astype(float)
            valid = vals[~np.isnan(vals)]
            vmin, vmax = (valid.min(), valid.max()) if len(valid) > 1 else (0, 1)
            rng = vmax - vmin if vmax != vmin else 1.0
            for v in vals:
                if np.isnan(v):
                    row_z.append(0.5)
                    row_t.append("N/A")
                else:
                    norm = (v - vmin) / rng
                    score = (1.0 - norm) if invert[m] else norm
                    row_z.append(score)
                    if m == "div":
                        row_t.append(f"{v:.1f}%")
                    elif m == "pe":
                        row_t.append("—" if v == 0 else f"{v:.1f}x")
                    else:
                        row_t.append(f"{v:.2f}x")
            z_matrix.append(row_z)
            text_matrix.append(row_t)

        fig = go.Figure(data=go.Heatmap(
            z=z_matrix,
            x=tickers,
            y=metric_labels,
            text=text_matrix,
            texttemplate="%{text}",
            textfont=dict(size=9, color="white"),
            colorscale=[[0.0, C_LOW], [0.5, C_MOD], [1.0, C_HIGH]],
            showscale=True,
            colorbar=dict(
                title="Relative Value",
                ticktext=["Expensive", "Neutral", "Cheap"],
                tickvals=[0.1, 0.5, 0.9],
                tickfont=dict(color=C_TEXT2, size=10),
            ),
            hovertemplate="<b>%{x}</b><br>%{y}: %{text}<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=260,
            margin=dict(l=80, r=20, t=20, b=60),
            xaxis=dict(side="bottom"),
        )
        st.plotly_chart(fig, use_container_width=True, key="rel_val_heatmap")

        st.caption(
            "Green = relatively cheap vs peers | Red = relatively expensive vs peers. "
            "For yield, green = high yield. For valuation multiples, green = low multiple."
        )
        st.markdown(
            source_footer([
                {"name": "Cross-sectional valuation z-scores", "kind": "modeled", "quality": "demo"},
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("relative value heatmap failed")


# ── Main entry point ────────────────────────────────────────────────────────────

def render(stock_data: Any = None, insights: Any = None) -> None:
    """Render the Goldman Sachs equity research quality fundamentals tab."""
    try:
        df = pd.DataFrame(UNIVERSE).sort_values("mktcap", ascending=False).reset_index(drop=True)

        if stock_data is not None:
            try:
                if isinstance(stock_data, dict):
                    for i, row in df.iterrows():
                        tk = row["ticker"]
                        if tk in stock_data:
                            sd = stock_data[tk]
                            if isinstance(sd, dict) and "price" in sd:
                                df.at[i, "price"] = float(sd["price"])
                            elif hasattr(sd, "price"):
                                df.at[i, "price"] = float(sd.price)
            except Exception:
                logger.warning("Could not overlay live prices from stock_data")

        page_header(
            title="Shipping Equity Coverage",
            subtitle="Goldman Sachs-style equity research universe — 20 global shipping names",
            badge_text="FUNDAMENTALS",
            badge_color=C_ACCENT,
        )

        _render_header(df)
        _render_screening_table(df)
        _render_valuation_matrix(df)
        _render_deep_dive(df)
        _render_dividend_tracker(df)
        _render_relative_value(df)

    except Exception:
        logger.exception("tab_fundamentals render failed")
        st.error("Fundamentals tab encountered an error. Check logs.")
