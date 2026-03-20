"""
Port congestion historical database and advanced forecasting system.

Covers the major congestion events of 2019-2026:
  - 2020-04  COVID demand collapse
  - 2020-11 – 2021-09  US demand surge / LA-LB anchor crisis
  - 2021-03  Suez Canal Ever-Given blockage
  - 2021-06  Yantian closure
  - 2022-03  Shanghai lockdown
  - 2023     Global normalisation
  - 2024     Red Sea rerouting → European port spike
  - 2025     Semi-normalised with regional variation
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# CongestionRecord
# ---------------------------------------------------------------------------

@dataclass
class CongestionRecord:
    """One monthly observation of port congestion."""

    port_locode: str
    date: date                  # first of the month
    congestion_score: float     # [0, 1]
    vessel_count: int
    avg_wait_days: float
    incident_type: str          # "NORMAL" | "ELEVATED" | "SPIKE" | "CRISIS"
    driver: str                 # "WEATHER" | "LABOR" | "DEMAND_SURGE" |
                                # "EQUIPMENT" | "INFRASTRUCTURE" | "PANDEMIC"
    notes: str = ""


# ---------------------------------------------------------------------------
# CongestionForecast (enhanced)
# ---------------------------------------------------------------------------

@dataclass
class CongestionForecast:
    """Advanced port congestion forecast with uncertainty and component decomposition."""

    port_locode: str
    current_score: float
    forecast_7d: float
    forecast_30d: float
    forecast_90d: float
    confidence: float                       # [0, 1]
    trend: str                              # "WORSENING" | "STABLE" | "IMPROVING"
    seasonal_component: float               # additive seasonal term
    trend_component: float                  # additive trend term
    incident_probability: float             # P(spike in next 30 days)
    ci_lower_7d: float = 0.0
    ci_upper_7d: float = 0.0
    ci_lower_30d: float = 0.0
    ci_upper_30d: float = 0.0
    ci_lower_90d: float = 0.0
    ci_upper_90d: float = 0.0


# ---------------------------------------------------------------------------
# Historical database helpers
# ---------------------------------------------------------------------------

def _rec(
    locode: str,
    year: int,
    month: int,
    score: float,
    vessels: int,
    wait: float,
    incident: str,
    driver: str,
    notes: str = "",
) -> CongestionRecord:
    return CongestionRecord(
        port_locode=locode,
        date=date(year, month, 1),
        congestion_score=round(min(1.0, max(0.0, score)), 3),
        vessel_count=vessels,
        avg_wait_days=round(wait, 1),
        incident_type=incident,
        driver=driver,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CONGESTION_HISTORY
# ---------------------------------------------------------------------------

def _build_history() -> Dict[str, List[CongestionRecord]]:  # noqa: C901
    """Construct the full historical database from hard-coded event data."""

    db: Dict[str, List[CongestionRecord]] = {}

    # ── USLAX — Port of Los Angeles ─────────────────────────────────────────

    db["USLAX"] = [
        # 2019 baseline
        _rec("USLAX", 2019, 1,  0.38, 380, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 2,  0.35, 350, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 3,  0.36, 360, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 4,  0.37, 370, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 5,  0.39, 390, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 6,  0.40, 400, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2019, 7,  0.42, 415, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2019, 8,  0.45, 440, 1.8, "ELEVATED", "DEMAND_SURGE",
             "Pre-holiday peak season building"),
        _rec("USLAX", 2019, 9,  0.50, 470, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2019, 10, 0.52, 490, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2019, 11, 0.45, 430, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2019, 12, 0.38, 370, 1.4, "NORMAL",   "DEMAND_SURGE"),
        # 2020 – COVID collapse then early recovery
        _rec("USLAX", 2020, 1,  0.35, 340, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2020, 2,  0.30, 300, 1.0, "NORMAL",   "PANDEMIC",
             "COVID news suppressing bookings"),
        _rec("USLAX", 2020, 3,  0.22, 220, 0.7, "NORMAL",   "PANDEMIC",
             "Global lockdowns → volume crash"),
        _rec("USLAX", 2020, 4,  0.15, 150, 0.5, "NORMAL",   "PANDEMIC",
             "Historic demand collapse; near-empty vessels"),
        _rec("USLAX", 2020, 5,  0.18, 175, 0.6, "NORMAL",   "PANDEMIC"),
        _rec("USLAX", 2020, 6,  0.22, 210, 0.7, "NORMAL",   "PANDEMIC"),
        _rec("USLAX", 2020, 7,  0.32, 310, 1.1, "NORMAL",   "DEMAND_SURGE",
             "E-commerce surge begins; stimulus checks"),
        _rec("USLAX", 2020, 8,  0.42, 400, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2020, 9,  0.52, 490, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2020, 10, 0.62, 580, 2.8, "SPIKE",    "DEMAND_SURGE",
             "Container shortage spreading"),
        _rec("USLAX", 2020, 11, 0.72, 680, 3.8, "SPIKE",    "DEMAND_SURGE",
             "Ships beginning to queue outside port"),
        _rec("USLAX", 2020, 12, 0.78, 740, 4.5, "CRISIS",   "DEMAND_SURGE",
             "40+ ships at anchor — record levels"),
        # 2021 – LA/LB anchor crisis peak
        _rec("USLAX", 2021, 1,  0.82, 790, 5.2, "CRISIS",   "DEMAND_SURGE",
             "65+ ships anchored in San Pedro Bay"),
        _rec("USLAX", 2021, 2,  0.80, 775, 5.0, "CRISIS",   "DEMAND_SURGE"),
        _rec("USLAX", 2021, 3,  0.85, 830, 5.8, "CRISIS",   "DEMAND_SURGE",
             "Suez Canal blockage compounds global congestion"),
        _rec("USLAX", 2021, 4,  0.88, 870, 6.2, "CRISIS",   "DEMAND_SURGE"),
        _rec("USLAX", 2021, 5,  0.87, 860, 6.0, "CRISIS",   "DEMAND_SURGE"),
        _rec("USLAX", 2021, 6,  0.86, 845, 5.9, "CRISIS",   "DEMAND_SURGE",
             "Yantian closure delays add to queue"),
        _rec("USLAX", 2021, 7,  0.84, 820, 5.6, "CRISIS",   "DEMAND_SURGE"),
        _rec("USLAX", 2021, 8,  0.87, 855, 6.1, "CRISIS",   "DEMAND_SURGE",
             "Peak season crushes already overwhelmed terminal"),
        _rec("USLAX", 2021, 9,  0.89, 880, 6.5, "CRISIS",   "DEMAND_SURGE",
             "80+ ships at anchor — all-time record"),
        _rec("USLAX", 2021, 10, 0.86, 845, 6.0, "CRISIS",   "DEMAND_SURGE",
             "Night gates introduced; slow progress"),
        _rec("USLAX", 2021, 11, 0.80, 780, 5.2, "CRISIS",   "DEMAND_SURGE"),
        _rec("USLAX", 2021, 12, 0.72, 700, 4.2, "SPIKE",    "DEMAND_SURGE"),
        # 2022
        _rec("USLAX", 2022, 1,  0.65, 630, 3.5, "SPIKE",    "DEMAND_SURGE"),
        _rec("USLAX", 2022, 2,  0.60, 580, 3.1, "SPIKE",    "DEMAND_SURGE"),
        _rec("USLAX", 2022, 3,  0.55, 530, 2.7, "ELEVATED", "DEMAND_SURGE",
             "Shanghai lockdown creates new delays"),
        _rec("USLAX", 2022, 4,  0.58, 555, 2.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 5,  0.60, 575, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("USLAX", 2022, 6,  0.55, 530, 2.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 7,  0.50, 490, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 8,  0.48, 470, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 9,  0.45, 440, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 10, 0.40, 395, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2022, 11, 0.35, 345, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2022, 12, 0.32, 315, 1.1, "NORMAL",   "DEMAND_SURGE"),
        # 2023 – normalisation
        _rec("USLAX", 2023, 1,  0.30, 295, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 2,  0.28, 275, 0.9, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 3,  0.30, 295, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 4,  0.32, 310, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 5,  0.33, 320, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 6,  0.35, 340, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 7,  0.38, 370, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 8,  0.42, 405, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2023, 9,  0.44, 425, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2023, 10, 0.42, 405, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2023, 11, 0.38, 370, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2023, 12, 0.35, 340, 1.2, "NORMAL",   "DEMAND_SURGE"),
        # 2024 – moderate, Red Sea rerouting shifts some volume eastbound
        _rec("USLAX", 2024, 1,  0.33, 320, 1.1, "NORMAL",   "INFRASTRUCTURE",
             "Red Sea disruptions → some cargo rerouted to transpacific"),
        _rec("USLAX", 2024, 2,  0.35, 340, 1.3, "NORMAL",   "INFRASTRUCTURE"),
        _rec("USLAX", 2024, 3,  0.38, 370, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("USLAX", 2024, 4,  0.40, 390, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 5,  0.42, 410, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 6,  0.44, 430, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 7,  0.48, 465, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 8,  0.50, 485, 2.1, "ELEVATED", "DEMAND_SURGE",
             "Peak season; tariff front-loading"),
        _rec("USLAX", 2024, 9,  0.52, 500, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 10, 0.50, 485, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 11, 0.45, 440, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2024, 12, 0.40, 390, 1.5, "ELEVATED", "DEMAND_SURGE"),
        # 2025 – tariff uncertainty → front-loading spikes
        _rec("USLAX", 2025, 1,  0.42, 410, 1.6, "ELEVATED", "DEMAND_SURGE",
             "Tariff front-loading ahead of policy deadlines"),
        _rec("USLAX", 2025, 2,  0.45, 440, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 3,  0.50, 490, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 4,  0.55, 535, 2.5, "ELEVATED", "DEMAND_SURGE",
             "Major tariff announcement causes volume surge"),
        _rec("USLAX", 2025, 5,  0.52, 510, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 6,  0.48, 465, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 7,  0.46, 450, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 8,  0.48, 465, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 9,  0.50, 485, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 10, 0.48, 465, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 11, 0.44, 430, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2025, 12, 0.40, 390, 1.5, "ELEVATED", "DEMAND_SURGE"),
        # 2026 (partial)
        _rec("USLAX", 2026, 1,  0.42, 410, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2026, 2,  0.43, 420, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USLAX", 2026, 3,  0.44, 430, 1.8, "ELEVATED", "DEMAND_SURGE"),
    ]

    # ── CNSHA — Port of Shanghai ─────────────────────────────────────────────

    db["CNSHA"] = [
        # 2019 baseline
        _rec("CNSHA", 2019, 1,  0.40, 420, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 2,  0.25, 255, 0.8, "NORMAL",   "DEMAND_SURGE",
             "Chinese New Year factory shutdown"),
        _rec("CNSHA", 2019, 3,  0.38, 390, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 4,  0.42, 430, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 5,  0.44, 450, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 6,  0.45, 460, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 7,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 8,  0.52, 530, 2.1, "ELEVATED", "DEMAND_SURGE",
             "Peak season export surge"),
        _rec("CNSHA", 2019, 9,  0.54, 550, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 10, 0.52, 530, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 11, 0.45, 460, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2019, 12, 0.42, 430, 1.5, "NORMAL",   "DEMAND_SURGE"),
        # 2020
        _rec("CNSHA", 2020, 1,  0.38, 390, 1.4, "NORMAL",   "PANDEMIC"),
        _rec("CNSHA", 2020, 2,  0.12, 110, 0.3, "NORMAL",   "PANDEMIC",
             "COVID factory closures — near-zero throughput"),
        _rec("CNSHA", 2020, 3,  0.20, 195, 0.6, "NORMAL",   "PANDEMIC",
             "Gradual restart but global demand collapsed"),
        _rec("CNSHA", 2020, 4,  0.15, 145, 0.4, "NORMAL",   "PANDEMIC",
             "Global lockdowns hit export demand"),
        _rec("CNSHA", 2020, 5,  0.25, 250, 0.8, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 6,  0.35, 350, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 7,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 8,  0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE",
             "Western demand surge hits Chinese export capacity"),
        _rec("CNSHA", 2020, 9,  0.62, 630, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 10, 0.65, 665, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 11, 0.68, 695, 3.2, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2020, 12, 0.65, 665, 3.0, "SPIKE",    "DEMAND_SURGE"),
        # 2021
        _rec("CNSHA", 2021, 1,  0.62, 635, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 2,  0.28, 280, 0.9, "NORMAL",   "DEMAND_SURGE",
             "CNY shutdown"),
        _rec("CNSHA", 2021, 3,  0.68, 695, 3.2, "SPIKE",    "DEMAND_SURGE",
             "Suez Canal blockage ripples reach Shanghai"),
        _rec("CNSHA", 2021, 4,  0.72, 735, 3.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 5,  0.70, 715, 3.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 6,  0.74, 755, 3.8, "SPIKE",    "DEMAND_SURGE",
             "Yantian closure shifts cargo to Shanghai"),
        _rec("CNSHA", 2021, 7,  0.72, 735, 3.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 8,  0.75, 765, 3.9, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 9,  0.73, 745, 3.7, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 10, 0.68, 695, 3.2, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 11, 0.62, 635, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2021, 12, 0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE"),
        # 2022 – Shanghai lockdown (Apr-Jun)
        _rec("CNSHA", 2022, 1,  0.55, 560, 2.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2022, 2,  0.32, 320, 1.1, "NORMAL",   "DEMAND_SURGE",
             "CNY shutdown"),
        _rec("CNSHA", 2022, 3,  0.60, 610, 2.7, "SPIKE",    "PANDEMIC",
             "COVID lockdown begins late March"),
        _rec("CNSHA", 2022, 4,  0.08, 75,  0.2, "NORMAL",   "PANDEMIC",
             "Full city lockdown — port near standstill"),
        _rec("CNSHA", 2022, 5,  0.10, 95,  0.3, "NORMAL",   "PANDEMIC",
             "Lockdown continues; skeleton crew operations"),
        _rec("CNSHA", 2022, 6,  0.38, 385, 1.4, "NORMAL",   "PANDEMIC",
             "Lockdown lifts; massive backlog clearance"),
        _rec("CNSHA", 2022, 7,  0.72, 735, 3.6, "SPIKE",    "DEMAND_SURGE",
             "Backlog explosion as demand returns"),
        _rec("CNSHA", 2022, 8,  0.78, 795, 4.2, "CRISIS",   "DEMAND_SURGE",
             "Post-lockdown backlog peaks"),
        _rec("CNSHA", 2022, 9,  0.70, 715, 3.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSHA", 2022, 10, 0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2022, 11, 0.45, 460, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2022, 12, 0.38, 385, 1.4, "NORMAL",   "DEMAND_SURGE"),
        # 2023 – normalisation
        _rec("CNSHA", 2023, 1,  0.35, 355, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 2,  0.22, 215, 0.7, "NORMAL",   "DEMAND_SURGE",
             "CNY slowdown"),
        _rec("CNSHA", 2023, 3,  0.40, 405, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 4,  0.42, 425, 1.6, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 5,  0.44, 450, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 6,  0.46, 470, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 7,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 8,  0.50, 510, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 9,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 10, 0.45, 460, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 11, 0.40, 405, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2023, 12, 0.36, 360, 1.3, "NORMAL",   "DEMAND_SURGE"),
        # 2024 – Red Sea disruptions redirect some Europe cargo through Shanghai
        _rec("CNSHA", 2024, 1,  0.38, 385, 1.4, "NORMAL",   "INFRASTRUCTURE"),
        _rec("CNSHA", 2024, 2,  0.24, 240, 0.8, "NORMAL",   "DEMAND_SURGE",
             "CNY slowdown"),
        _rec("CNSHA", 2024, 3,  0.46, 470, 1.8, "ELEVATED", "INFRASTRUCTURE",
             "Rerouted Asia-Europe cargo adds volume"),
        _rec("CNSHA", 2024, 4,  0.50, 510, 2.1, "ELEVATED", "INFRASTRUCTURE"),
        _rec("CNSHA", 2024, 5,  0.54, 550, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 6,  0.56, 570, 2.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 7,  0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 8,  0.60, 610, 2.7, "SPIKE",    "DEMAND_SURGE",
             "Peak season + tariff front-loading"),
        _rec("CNSHA", 2024, 9,  0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 10, 0.54, 550, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 11, 0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2024, 12, 0.42, 425, 1.6, "ELEVATED", "DEMAND_SURGE"),
        # 2025
        _rec("CNSHA", 2025, 1,  0.44, 450, 1.7, "ELEVATED", "DEMAND_SURGE",
             "Tariff front-loading"),
        _rec("CNSHA", 2025, 2,  0.26, 260, 0.9, "NORMAL",   "DEMAND_SURGE",
             "CNY shutdown"),
        _rec("CNSHA", 2025, 3,  0.52, 530, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 4,  0.58, 590, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 5,  0.54, 550, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 6,  0.50, 510, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 7,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 8,  0.50, 510, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 9,  0.48, 490, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 10, 0.46, 470, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 11, 0.42, 425, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSHA", 2025, 12, 0.38, 385, 1.4, "NORMAL",   "DEMAND_SURGE"),
        # 2026
        _rec("CNSHA", 2026, 1,  0.40, 405, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSHA", 2026, 2,  0.25, 250, 0.8, "NORMAL",   "DEMAND_SURGE",
             "CNY slowdown"),
        _rec("CNSHA", 2026, 3,  0.46, 470, 1.8, "ELEVATED", "DEMAND_SURGE"),
    ]

    # ── NLRTM — Port of Rotterdam ─────────────────────────────────────────────

    db["NLRTM"] = [
        # 2019
        _rec("NLRTM", 2019, 1,  0.35, 280, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 2,  0.32, 255, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 3,  0.34, 270, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 4,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 5,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 6,  0.40, 320, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 7,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 8,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 9,  0.40, 320, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 10, 0.44, 350, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 11, 0.40, 320, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2019, 12, 0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        # 2020
        _rec("NLRTM", 2020, 1,  0.34, 270, 1.1, "NORMAL",   "PANDEMIC"),
        _rec("NLRTM", 2020, 2,  0.30, 240, 0.9, "NORMAL",   "PANDEMIC"),
        _rec("NLRTM", 2020, 3,  0.22, 175, 0.6, "NORMAL",   "PANDEMIC",
             "European lockdowns collapse import demand"),
        _rec("NLRTM", 2020, 4,  0.15, 115, 0.4, "NORMAL",   "PANDEMIC"),
        _rec("NLRTM", 2020, 5,  0.18, 140, 0.5, "NORMAL",   "PANDEMIC"),
        _rec("NLRTM", 2020, 6,  0.24, 190, 0.8, "NORMAL",   "PANDEMIC"),
        _rec("NLRTM", 2020, 7,  0.30, 240, 0.9, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2020, 8,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2020, 9,  0.44, 350, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2020, 10, 0.50, 400, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2020, 11, 0.56, 450, 2.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2020, 12, 0.60, 480, 2.6, "SPIKE",    "DEMAND_SURGE"),
        # 2021
        _rec("NLRTM", 2021, 1,  0.62, 495, 2.7, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 2,  0.60, 480, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 3,  0.70, 560, 3.4, "SPIKE",    "INFRASTRUCTURE",
             "Suez Canal Ever-Given blockage — vessels back up at Rotterdam"),
        _rec("NLRTM", 2021, 4,  0.72, 575, 3.6, "SPIKE",    "DEMAND_SURGE",
             "Post-Suez backlog clearance causes congestion"),
        _rec("NLRTM", 2021, 5,  0.68, 545, 3.2, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 6,  0.65, 520, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 7,  0.60, 480, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 8,  0.58, 465, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 9,  0.55, 440, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 10, 0.52, 415, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 11, 0.50, 400, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2021, 12, 0.48, 385, 1.9, "ELEVATED", "DEMAND_SURGE"),
        # 2022
        _rec("NLRTM", 2022, 1,  0.46, 370, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 2,  0.44, 350, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 3,  0.50, 400, 2.0, "ELEVATED", "DEMAND_SURGE",
             "Shanghai lockdown delays Asia-Europe sailings"),
        _rec("NLRTM", 2022, 4,  0.54, 430, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 5,  0.58, 465, 2.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 6,  0.62, 495, 2.7, "SPIKE",    "DEMAND_SURGE",
             "Post-Shanghai-lockdown cargo surge hits Rotterdam"),
        _rec("NLRTM", 2022, 7,  0.60, 480, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 8,  0.56, 450, 2.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 9,  0.50, 400, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 10, 0.44, 350, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 11, 0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2022, 12, 0.34, 270, 1.1, "NORMAL",   "DEMAND_SURGE"),
        # 2023
        _rec("NLRTM", 2023, 1,  0.32, 255, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 2,  0.30, 240, 0.9, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 3,  0.32, 255, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 4,  0.34, 270, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 5,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 6,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 7,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 8,  0.34, 270, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 9,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 10, 0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 11, 0.34, 270, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2023, 12, 0.30, 240, 0.9, "NORMAL",   "DEMAND_SURGE"),
        # 2024 – Red Sea → Cape of Good Hope rerouting adds 2-3 weeks → Rotterdam spike
        _rec("NLRTM", 2024, 1,  0.42, 335, 1.6, "ELEVATED", "INFRASTRUCTURE",
             "Red Sea crisis: Houthi attacks force Cape rerouting"),
        _rec("NLRTM", 2024, 2,  0.48, 385, 1.9, "ELEVATED", "INFRASTRUCTURE",
             "Longer sailings create vessel bunching"),
        _rec("NLRTM", 2024, 3,  0.56, 450, 2.4, "ELEVATED", "INFRASTRUCTURE",
             "Vessel bunching from Cape route delays hits Rotterdam"),
        _rec("NLRTM", 2024, 4,  0.62, 495, 2.7, "SPIKE",    "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 5,  0.66, 530, 3.0, "SPIKE",    "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 6,  0.70, 560, 3.4, "SPIKE",    "INFRASTRUCTURE",
             "Peak bunching from Red Sea detours"),
        _rec("NLRTM", 2024, 7,  0.72, 575, 3.6, "SPIKE",    "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 8,  0.68, 545, 3.2, "SPIKE",    "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 9,  0.62, 495, 2.7, "SPIKE",    "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 10, 0.56, 450, 2.4, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 11, 0.50, 400, 2.0, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2024, 12, 0.46, 370, 1.8, "ELEVATED", "INFRASTRUCTURE"),
        # 2025 – Red Sea partially persists; some normalisation
        _rec("NLRTM", 2025, 1,  0.50, 400, 2.0, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 2,  0.48, 385, 1.9, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 3,  0.46, 370, 1.8, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 4,  0.44, 350, 1.6, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 5,  0.42, 335, 1.6, "ELEVATED", "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 6,  0.40, 320, 1.4, "NORMAL",   "INFRASTRUCTURE"),
        _rec("NLRTM", 2025, 7,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2025, 8,  0.40, 320, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2025, 9,  0.42, 335, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2025, 10, 0.44, 350, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("NLRTM", 2025, 11, 0.40, 320, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2025, 12, 0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        # 2026
        _rec("NLRTM", 2026, 1,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2026, 2,  0.36, 285, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("NLRTM", 2026, 3,  0.38, 305, 1.3, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── SGSIN — Port of Singapore ─────────────────────────────────────────────

    db["SGSIN"] = [
        # 2019
        _rec("SGSIN", 2019, 1,  0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 2,  0.38, 545, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 3,  0.40, 570, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 4,  0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 5,  0.44, 630, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 6,  0.46, 660, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 7,  0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 8,  0.50, 715, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 9,  0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 10, 0.46, 660, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 11, 0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2019, 12, 0.40, 570, 1.4, "NORMAL",   "DEMAND_SURGE"),
        # 2020
        _rec("SGSIN", 2020, 1,  0.38, 545, 1.3, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 2,  0.30, 430, 1.0, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 3,  0.22, 315, 0.7, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 4,  0.18, 255, 0.6, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 5,  0.24, 345, 0.8, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 6,  0.32, 460, 1.1, "NORMAL",   "PANDEMIC"),
        _rec("SGSIN", 2020, 7,  0.44, 630, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2020, 8,  0.54, 770, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2020, 9,  0.58, 830, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2020, 10, 0.62, 885, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2020, 11, 0.66, 945, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2020, 12, 0.64, 915, 2.7, "SPIKE",    "DEMAND_SURGE"),
        # 2021
        _rec("SGSIN", 2021, 1,  0.62, 885, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 2,  0.60, 860, 2.5, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 3,  0.68, 970, 3.0, "SPIKE",    "INFRASTRUCTURE",
             "Suez ripple — transshipment delays increase"),
        _rec("SGSIN", 2021, 4,  0.70, 1000, 3.2, "SPIKE",   "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 5,  0.68, 970, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 6,  0.72, 1030, 3.4, "SPIKE",   "DEMAND_SURGE",
             "Yantian closure diverts cargo via Singapore"),
        _rec("SGSIN", 2021, 7,  0.74, 1055, 3.6, "SPIKE",   "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 8,  0.76, 1085, 3.8, "CRISIS",  "DEMAND_SURGE",
             "Singapore congestion reaches record — berth wait 5+ days"),
        _rec("SGSIN", 2021, 9,  0.74, 1055, 3.6, "SPIKE",   "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 10, 0.70, 1000, 3.2, "SPIKE",   "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 11, 0.64, 915, 2.7, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2021, 12, 0.58, 830, 2.3, "ELEVATED", "DEMAND_SURGE"),
        # 2022
        _rec("SGSIN", 2022, 1,  0.55, 785, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 2,  0.52, 745, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 3,  0.58, 830, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 4,  0.62, 885, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 5,  0.65, 930, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 6,  0.70, 1000, 3.2, "SPIKE",   "DEMAND_SURGE",
             "Post-Shanghai-lockdown cargo surge"),
        _rec("SGSIN", 2022, 7,  0.68, 970, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 8,  0.62, 885, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 9,  0.55, 785, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 10, 0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 11, 0.42, 600, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2022, 12, 0.38, 545, 1.3, "NORMAL",   "DEMAND_SURGE"),
        # 2023
        _rec("SGSIN", 2023, 1,  0.36, 515, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 2,  0.34, 490, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 3,  0.36, 515, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 4,  0.38, 545, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 5,  0.40, 570, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 6,  0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 7,  0.44, 630, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 8,  0.46, 660, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 9,  0.44, 630, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 10, 0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 11, 0.38, 545, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2023, 12, 0.35, 500, 1.2, "NORMAL",   "DEMAND_SURGE"),
        # 2024 – Red Sea → Singapore as key transshipment hub sees volume spike
        _rec("SGSIN", 2024, 1,  0.50, 715, 1.9, "ELEVATED", "INFRASTRUCTURE",
             "Red Sea rerouting increases Singapore transshipment volumes"),
        _rec("SGSIN", 2024, 2,  0.58, 830, 2.3, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 3,  0.65, 930, 2.8, "SPIKE",    "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 4,  0.72, 1030, 3.4, "SPIKE",   "INFRASTRUCTURE",
             "Record transshipment volumes; berth queues form"),
        _rec("SGSIN", 2024, 5,  0.76, 1085, 3.8, "CRISIS",  "INFRASTRUCTURE",
             "Singapore congestion surpasses 2021 highs"),
        _rec("SGSIN", 2024, 6,  0.78, 1115, 4.0, "CRISIS",  "INFRASTRUCTURE",
             "Worst Singapore congestion in 5 years"),
        _rec("SGSIN", 2024, 7,  0.74, 1055, 3.6, "SPIKE",   "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 8,  0.68, 970, 3.0, "SPIKE",    "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 9,  0.62, 885, 2.6, "SPIKE",    "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 10, 0.55, 785, 2.2, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 11, 0.48, 685, 1.8, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2024, 12, 0.44, 630, 1.6, "ELEVATED", "INFRASTRUCTURE"),
        # 2025
        _rec("SGSIN", 2025, 1,  0.46, 660, 1.7, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2025, 2,  0.44, 630, 1.6, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2025, 3,  0.46, 660, 1.7, "ELEVATED", "INFRASTRUCTURE"),
        _rec("SGSIN", 2025, 4,  0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 5,  0.50, 715, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 6,  0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 7,  0.46, 660, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 8,  0.48, 685, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 9,  0.46, 660, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 10, 0.44, 630, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 11, 0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2025, 12, 0.40, 570, 1.4, "NORMAL",   "DEMAND_SURGE"),
        # 2026
        _rec("SGSIN", 2026, 1,  0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2026, 2,  0.40, 570, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("SGSIN", 2026, 3,  0.42, 600, 1.5, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── KRPUS — Port of Busan ─────────────────────────────────────────────────

    db["KRPUS"] = [
        # 2019
        _rec("KRPUS", 2019, 1,  0.36, 310, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 2,  0.32, 275, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 3,  0.35, 300, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 4,  0.37, 320, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 5,  0.39, 335, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 6,  0.42, 360, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 7,  0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 8,  0.46, 395, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 9,  0.48, 415, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 10, 0.46, 395, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 11, 0.40, 345, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2019, 12, 0.36, 310, 1.2, "NORMAL",   "DEMAND_SURGE"),
        # 2020
        _rec("KRPUS", 2020, 1,  0.34, 293, 1.1, "NORMAL",   "PANDEMIC"),
        _rec("KRPUS", 2020, 2,  0.28, 240, 0.9, "NORMAL",   "PANDEMIC"),
        _rec("KRPUS", 2020, 3,  0.20, 172, 0.6, "NORMAL",   "PANDEMIC"),
        _rec("KRPUS", 2020, 4,  0.15, 129, 0.5, "NORMAL",   "PANDEMIC"),
        _rec("KRPUS", 2020, 5,  0.20, 172, 0.6, "NORMAL",   "PANDEMIC"),
        _rec("KRPUS", 2020, 6,  0.28, 240, 0.9, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 7,  0.38, 327, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 8,  0.48, 415, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 9,  0.55, 475, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 10, 0.60, 517, 2.3, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 11, 0.64, 552, 2.5, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2020, 12, 0.62, 534, 2.4, "SPIKE",    "DEMAND_SURGE"),
        # 2021
        _rec("KRPUS", 2021, 1,  0.60, 517, 2.3, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 2,  0.58, 500, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 3,  0.64, 552, 2.5, "SPIKE",    "INFRASTRUCTURE",
             "Suez blockage causes transshipment delays at Busan"),
        _rec("KRPUS", 2021, 4,  0.66, 569, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 5,  0.68, 586, 2.7, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 6,  0.70, 603, 2.9, "SPIKE",    "DEMAND_SURGE",
             "Yantian closure and US demand surge both hit Busan transshipment"),
        _rec("KRPUS", 2021, 7,  0.72, 621, 3.1, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 8,  0.74, 638, 3.2, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 9,  0.72, 621, 3.1, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 10, 0.68, 586, 2.7, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 11, 0.62, 534, 2.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2021, 12, 0.56, 483, 2.1, "ELEVATED", "DEMAND_SURGE"),
        # 2022
        _rec("KRPUS", 2022, 1,  0.52, 449, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 2,  0.48, 415, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 3,  0.54, 466, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 4,  0.58, 500, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 5,  0.62, 534, 2.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 6,  0.65, 561, 2.6, "SPIKE",    "DEMAND_SURGE",
             "Post-Shanghai backlog adds to Busan queue"),
        _rec("KRPUS", 2022, 7,  0.62, 534, 2.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 8,  0.56, 483, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 9,  0.50, 431, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 10, 0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 11, 0.38, 327, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2022, 12, 0.34, 293, 1.1, "NORMAL",   "DEMAND_SURGE"),
        # 2023
        _rec("KRPUS", 2023, 1,  0.32, 276, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 2,  0.30, 259, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 3,  0.32, 276, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 4,  0.35, 302, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 5,  0.38, 327, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 6,  0.40, 345, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 7,  0.42, 362, 1.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 8,  0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 9,  0.42, 362, 1.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 10, 0.40, 345, 1.4, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 11, 0.36, 310, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2023, 12, 0.32, 276, 1.0, "NORMAL",   "DEMAND_SURGE"),
        # 2024 – Red Sea ripple; Busan benefits from some rerouted cargo
        _rec("KRPUS", 2024, 1,  0.38, 327, 1.3, "NORMAL",   "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 2,  0.42, 362, 1.4, "ELEVATED", "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 3,  0.46, 397, 1.6, "ELEVATED", "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 4,  0.50, 431, 1.8, "ELEVATED", "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 5,  0.54, 466, 2.0, "ELEVATED", "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 6,  0.56, 483, 2.1, "ELEVATED", "INFRASTRUCTURE"),
        _rec("KRPUS", 2024, 7,  0.58, 500, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2024, 8,  0.60, 517, 2.3, "SPIKE",    "DEMAND_SURGE"),
        _rec("KRPUS", 2024, 9,  0.58, 500, 2.2, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2024, 10, 0.54, 466, 2.0, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2024, 11, 0.48, 415, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2024, 12, 0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        # 2025
        _rec("KRPUS", 2025, 1,  0.46, 397, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 2,  0.42, 362, 1.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 3,  0.48, 415, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 4,  0.52, 449, 1.9, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 5,  0.50, 431, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 6,  0.46, 397, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 7,  0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 8,  0.46, 397, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 9,  0.44, 380, 1.5, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 10, 0.42, 362, 1.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 11, 0.38, 327, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2025, 12, 0.35, 302, 1.1, "NORMAL",   "DEMAND_SURGE"),
        # 2026
        _rec("KRPUS", 2026, 1,  0.38, 327, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2026, 2,  0.36, 310, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("KRPUS", 2026, 3,  0.40, 345, 1.4, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── HKHKG — Port of Hong Kong ──────────────────────────────────────────────

    db["HKHKG"] = [
        # 2019
        _rec("HKHKG", 2019, 1,  0.38, 350, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("HKHKG", 2019, 6,  0.44, 405, 1.6, "ELEVATED", "LABOR",
             "Social unrest begins affecting port operations"),
        _rec("HKHKG", 2019, 7,  0.46, 425, 1.7, "ELEVATED", "LABOR"),
        _rec("HKHKG", 2019, 9,  0.48, 440, 1.8, "ELEVATED", "LABOR"),
        _rec("HKHKG", 2019, 12, 0.38, 350, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("HKHKG", 2020, 4,  0.12, 110, 0.3, "NORMAL",   "PANDEMIC"),
        _rec("HKHKG", 2020, 11, 0.58, 535, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("HKHKG", 2021, 3,  0.65, 600, 2.8, "SPIKE",    "DEMAND_SURGE"),
        _rec("HKHKG", 2021, 6,  0.72, 665, 3.4, "SPIKE",    "DEMAND_SURGE",
             "Yantian closure 50km away diverts cargo to Hong Kong"),
        _rec("HKHKG", 2021, 9,  0.68, 627, 3.1, "SPIKE",    "DEMAND_SURGE"),
        _rec("HKHKG", 2022, 3,  0.55, 508, 2.3, "ELEVATED", "PANDEMIC"),
        _rec("HKHKG", 2022, 8,  0.60, 554, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("HKHKG", 2023, 6,  0.38, 350, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("HKHKG", 2024, 6,  0.50, 462, 2.0, "ELEVATED", "INFRASTRUCTURE"),
        _rec("HKHKG", 2025, 3,  0.44, 406, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("HKHKG", 2026, 3,  0.40, 370, 1.5, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── CNSZN — Shenzhen / Yantian ────────────────────────────────────────────

    db["CNSZN"] = [
        _rec("CNSZN", 2019, 6,  0.45, 420, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSZN", 2020, 4,  0.12, 110, 0.3, "NORMAL",   "PANDEMIC"),
        _rec("CNSZN", 2020, 10, 0.62, 580, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSZN", 2021, 5,  0.68, 635, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSZN", 2021, 6,  0.88, 820, 5.5, "CRISIS",   "LABOR",
             "COVID cluster at Yantian terminal — partial closure weeks 1-4"),
        _rec("CNSZN", 2021, 7,  0.75, 700, 4.0, "CRISIS",   "LABOR",
             "Terminal slowly re-opening; massive backlog"),
        _rec("CNSZN", 2021, 8,  0.65, 608, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSZN", 2021, 9,  0.70, 654, 3.4, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSZN", 2022, 3,  0.60, 561, 2.6, "SPIKE",    "PANDEMIC"),
        _rec("CNSZN", 2022, 8,  0.65, 608, 3.0, "SPIKE",    "DEMAND_SURGE"),
        _rec("CNSZN", 2023, 6,  0.36, 336, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("CNSZN", 2024, 6,  0.52, 486, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSZN", 2025, 3,  0.46, 430, 1.8, "ELEVATED", "DEMAND_SURGE"),
        _rec("CNSZN", 2026, 3,  0.42, 393, 1.6, "ELEVATED", "DEMAND_SURGE"),
    ]

    # ── DEHAM — Hamburg ───────────────────────────────────────────────────────

    db["DEHAM"] = [
        _rec("DEHAM", 2019, 6,  0.36, 260, 1.2, "NORMAL",   "DEMAND_SURGE"),
        _rec("DEHAM", 2020, 4,  0.14, 100, 0.4, "NORMAL",   "PANDEMIC"),
        _rec("DEHAM", 2021, 3,  0.62, 445, 2.7, "SPIKE",    "INFRASTRUCTURE",
             "Suez ripple; labor industrial action"),
        _rec("DEHAM", 2021, 8,  0.68, 490, 3.2, "SPIKE",    "LABOR"),
        _rec("DEHAM", 2022, 6,  0.60, 432, 2.6, "SPIKE",    "LABOR",
             "Dockworker strike causes major delays"),
        _rec("DEHAM", 2023, 6,  0.32, 230, 1.0, "NORMAL",   "DEMAND_SURGE"),
        _rec("DEHAM", 2024, 4,  0.56, 404, 2.4, "ELEVATED", "INFRASTRUCTURE",
             "Red Sea rerouting congests Hamburg terminals"),
        _rec("DEHAM", 2024, 7,  0.65, 468, 3.0, "SPIKE",    "INFRASTRUCTURE"),
        _rec("DEHAM", 2025, 6,  0.42, 302, 1.6, "ELEVATED", "DEMAND_SURGE"),
        _rec("DEHAM", 2026, 3,  0.38, 274, 1.3, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── BEANR — Antwerp ───────────────────────────────────────────────────────

    db["BEANR"] = [
        _rec("BEANR", 2019, 6,  0.38, 285, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("BEANR", 2020, 4,  0.16, 120, 0.5, "NORMAL",   "PANDEMIC"),
        _rec("BEANR", 2021, 3,  0.65, 488, 2.9, "SPIKE",    "INFRASTRUCTURE",
             "Suez Canal blockage backs up Antwerp inbound vessels"),
        _rec("BEANR", 2021, 9,  0.62, 465, 2.6, "SPIKE",    "DEMAND_SURGE"),
        _rec("BEANR", 2022, 7,  0.58, 435, 2.4, "ELEVATED", "DEMAND_SURGE"),
        _rec("BEANR", 2023, 6,  0.34, 255, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("BEANR", 2024, 5,  0.62, 465, 2.7, "SPIKE",    "INFRASTRUCTURE",
             "Red Sea rerouting — Cape of Good Hope adds vessel bunching"),
        _rec("BEANR", 2024, 8,  0.68, 510, 3.1, "SPIKE",    "INFRASTRUCTURE"),
        _rec("BEANR", 2025, 6,  0.44, 330, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("BEANR", 2026, 3,  0.40, 300, 1.4, "NORMAL",   "DEMAND_SURGE"),
    ]

    # ── USNYC — New York / New Jersey ─────────────────────────────────────────

    db["USNYC"] = [
        _rec("USNYC", 2019, 6,  0.38, 295, 1.3, "NORMAL",   "DEMAND_SURGE"),
        _rec("USNYC", 2020, 4,  0.17, 130, 0.5, "NORMAL",   "PANDEMIC"),
        _rec("USNYC", 2020, 10, 0.54, 420, 2.1, "ELEVATED", "DEMAND_SURGE"),
        _rec("USNYC", 2021, 4,  0.72, 559, 3.5, "SPIKE",    "DEMAND_SURGE",
             "East Coast congestion as some cargo diverts from LA/LB"),
        _rec("USNYC", 2021, 9,  0.68, 527, 3.1, "SPIKE",    "DEMAND_SURGE"),
        _rec("USNYC", 2022, 3,  0.60, 466, 2.6, "SPIKE",    "LABOR",
             "ILA dockworker negotiations slow throughput"),
        _rec("USNYC", 2022, 8,  0.55, 427, 2.3, "ELEVATED", "DEMAND_SURGE"),
        _rec("USNYC", 2023, 6,  0.33, 256, 1.1, "NORMAL",   "DEMAND_SURGE"),
        _rec("USNYC", 2024, 9,  0.65, 505, 2.9, "SPIKE",    "LABOR",
             "ILA strike threat / brief strike action"),
        _rec("USNYC", 2025, 3,  0.44, 342, 1.7, "ELEVATED", "DEMAND_SURGE"),
        _rec("USNYC", 2026, 3,  0.40, 310, 1.4, "NORMAL",   "DEMAND_SURGE"),
    ]

    return db


# Build the module-level constant once
CONGESTION_HISTORY: Dict[str, List[CongestionRecord]] = _build_history()

logger.debug(
    "CONGESTION_HISTORY loaded: {} ports, {} total records",
    len(CONGESTION_HISTORY),
    sum(len(v) for v in CONGESTION_HISTORY.values()),
)


# ---------------------------------------------------------------------------
# Historical analytics helpers
# ---------------------------------------------------------------------------

def get_monthly_average(port_locode: str, month: int) -> float:
    """Return the mean congestion score for a given calendar month across all years."""
    records = CONGESTION_HISTORY.get(port_locode, [])
    monthly = [r.congestion_score for r in records if r.date.month == month]
    if not monthly:
        return 0.45
    return sum(monthly) / len(monthly)


def get_seasonal_component(port_locode: str, target_month: Optional[int] = None) -> float:
    """Return the seasonal deviation from the annual mean for a port/month.

    Positive = above-average season, negative = below-average.
    """
    records = CONGESTION_HISTORY.get(port_locode, [])
    if not records:
        return 0.0

    all_scores = [r.congestion_score for r in records]
    annual_mean = sum(all_scores) / len(all_scores)

    if target_month is None:
        target_month = date.today().month

    month_mean = get_monthly_average(port_locode, target_month)
    return round(month_mean - annual_mean, 4)


def get_last_major_incident(port_locode: str) -> Optional[CongestionRecord]:
    """Return the most recent CRISIS or SPIKE record for a port."""
    records = CONGESTION_HISTORY.get(port_locode, [])
    candidates = [
        r for r in records
        if r.incident_type in ("CRISIS", "SPIKE")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.date)


def get_incident_frequency(port_locode: str) -> float:
    """Return the fraction of historical months classified as SPIKE or CRISIS."""
    records = CONGESTION_HISTORY.get(port_locode, [])
    if not records:
        return 0.10
    spike_count = sum(
        1 for r in records if r.incident_type in ("CRISIS", "SPIKE")
    )
    return round(spike_count / len(records), 4)


def _port_scores_series(port_locode: str) -> List[float]:
    return [r.congestion_score for r in CONGESTION_HISTORY.get(port_locode, [])]


# ---------------------------------------------------------------------------
# Correlation matrix
# ---------------------------------------------------------------------------

def compute_congestion_correlation_matrix(ports: List[str]) -> pd.DataFrame:
    """Compute pairwise Pearson correlation between port congestion time-series.

    Uses only date-aligned observations (months present in both ports).

    Parameters
    ----------
    ports:
        List of UN/LOCODE strings, e.g. ["USLAX", "CNSHA", "NLRTM"].

    Returns
    -------
    pd.DataFrame with ports as both index and columns, values in [-1, 1].
    """
    logger.debug("Computing congestion correlation matrix for {} ports", len(ports))

    # Build a date-indexed dict of scores for each port
    series: Dict[str, Dict[date, float]] = {}
    for p in ports:
        records = CONGESTION_HISTORY.get(p, [])
        series[p] = {r.date: r.congestion_score for r in records}

    # Find common dates
    if not series:
        return pd.DataFrame()

    date_sets = [set(s.keys()) for s in series.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))

    if len(common_dates) < 3:
        logger.warning(
            "Fewer than 3 common observations for correlation; returning identity matrix"
        )
        n = len(ports)
        return pd.DataFrame(
            [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)],
            index=ports,
            columns=ports,
        )

    # Build aligned matrix
    data: Dict[str, List[float]] = {
        p: [series[p][d] for d in common_dates] for p in ports
    }
    df = pd.DataFrame(data, index=[str(d) for d in common_dates])
    corr = df.corr(method="pearson")
    corr.index = ports
    corr.columns = ports
    logger.debug(
        "Correlation matrix computed over {} common date points", len(common_dates)
    )
    return corr.round(3)


# ---------------------------------------------------------------------------
# Advanced forecasting
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _macro_pressure(macro_data: Optional[dict]) -> float:
    """Derive additive macro pressure from BDI trend and PMI."""
    if not macro_data:
        return 0.0
    pressure = 0.0

    bdi_rising = macro_data.get("BDI_rising", False)
    if bdi_rising:
        pressure += 0.04

    # BDI level signal
    bdi_series = macro_data.get("bdi") or macro_data.get("BDI")
    if isinstance(bdi_series, list) and len(bdi_series) >= 5:
        recent = bdi_series[-5:]
        slope = (recent[-1] - recent[0]) / max(1, len(recent) - 1)
        pressure += _clamp(slope / 500.0, -0.02, 0.04)

    pmi = macro_data.get("PMI", 50.0)
    if pmi > 54:
        pressure += 0.03
    elif pmi > 52:
        pressure += 0.015

    ism = macro_data.get("ISM", 50.0)
    if ism > 54:
        pressure += 0.02

    return _clamp(pressure, -0.05, 0.10)


def _run_monte_carlo(
    current: float,
    alpha: float,
    seasonal: float,
    pressure: float,
    horizon_days: int,
    n_paths: int = 300,
    rng_seed: int = 42,
) -> tuple:
    """Run simple Monte Carlo for confidence intervals.

    Returns (mean_forecast, ci_lower, ci_upper) at horizon_days.
    """
    rng = random.Random(rng_seed)
    sigma_daily = 0.008   # daily congestion volatility

    finals: List[float] = []
    for _ in range(n_paths):
        val = current
        for day in range(horizon_days):
            # Exponential smoothing step + noise
            target = val + pressure / 30.0 + seasonal / 30.0
            noise = rng.gauss(0, sigma_daily)
            val = _clamp(alpha * target + (1 - alpha) * val + noise)
        finals.append(val)

    finals.sort()
    n = len(finals)
    mean_val = sum(finals) / n
    ci_lower = finals[int(n * 0.10)]
    ci_upper = finals[int(n * 0.90)]
    return round(mean_val, 4), round(ci_lower, 4), round(ci_upper, 4)


def forecast_congestion_advanced(
    port_locode: str,
    current_ais_data: Optional[dict] = None,
    macro_data: Optional[dict] = None,
) -> CongestionForecast:
    """Produce an advanced congestion forecast with component decomposition and Monte Carlo CIs.

    Algorithm
    ---------
    1. Retrieve current score from AIS data (or fallback to last historical record).
    2. Apply simple exponential smoothing (alpha = 0.3) with seasonal and macro additive terms.
    3. Run Monte Carlo uncertainty bands (300 paths) for each horizon.
    4. Estimate spike probability from historical incident frequency adjusted by current level.

    Parameters
    ----------
    port_locode:      UN/LOCODE of the port.
    current_ais_data: Optional dict from AIS feed; may contain 'vessel_count',
                      'avg_wait_hours', 'congestion_score'.
    macro_data:       Optional macro dict with 'BDI_rising', 'PMI', 'ISM' keys.

    Returns
    -------
    CongestionForecast instance.
    """
    logger.debug("Advanced forecast requested for port={}", port_locode)

    # ── 1. Determine current score ─────────────────────────────────────────
    current = 0.45  # fallback
    if current_ais_data:
        ais_score = current_ais_data.get("congestion_score")
        if ais_score is not None:
            current = float(ais_score)
        elif "vessel_count" in current_ais_data:
            # Derive from vessel count vs historical baseline
            hist_max = max(
                (r.vessel_count for r in CONGESTION_HISTORY.get(port_locode, [])),
                default=500,
            )
            current = _clamp(current_ais_data["vessel_count"] / max(1, hist_max))

    # Fallback to most recent historical record
    if current == 0.45:
        records = CONGESTION_HISTORY.get(port_locode, [])
        if records:
            latest = max(records, key=lambda r: r.date)
            current = latest.congestion_score

    # ── 2. Component decomposition ─────────────────────────────────────────
    today = date.today()
    seasonal_comp = get_seasonal_component(port_locode, today.month)
    trend_comp = _macro_pressure(macro_data)

    # ── 3. Exponential smoothing forecasts ─────────────────────────────────
    alpha = 0.3
    mean_reversion_target = 0.45

    # 7-day
    level_7d = _clamp(
        alpha * (current + trend_comp + seasonal_comp * 0.5)
        + (1 - alpha) * current
    )

    # 30-day (mean reversion pulls in)
    level_30d = _clamp(
        alpha * (level_7d + trend_comp * 0.7)
        + (1 - alpha) * (level_7d * 0.85 + mean_reversion_target * 0.15)
    )

    # 90-day (stronger mean reversion)
    level_90d = _clamp(
        alpha * (level_30d + trend_comp * 0.4)
        + (1 - alpha) * (level_30d * 0.70 + mean_reversion_target * 0.30)
    )

    # ── 4. Monte Carlo confidence intervals ────────────────────────────────
    mc_7d  = _run_monte_carlo(current, alpha, seasonal_comp, trend_comp, 7)
    mc_30d = _run_monte_carlo(current, alpha, seasonal_comp, trend_comp, 30)
    mc_90d = _run_monte_carlo(current, alpha, seasonal_comp, trend_comp, 90)

    # ── 5. Trend label ─────────────────────────────────────────────────────
    if level_30d > current + 0.06:
        trend = "WORSENING"
    elif level_30d < current - 0.06:
        trend = "IMPROVING"
    else:
        trend = "STABLE"

    # ── 6. Confidence (degrades with longer horizon) ───────────────────────
    base_confidence = 0.85 if CONGESTION_HISTORY.get(port_locode) else 0.55
    ci_width_7d = mc_7d[2] - mc_7d[1]
    confidence = round(_clamp(base_confidence - ci_width_7d * 0.5), 3)

    # ── 7. Incident probability P(spike next 30d) ──────────────────────────
    base_freq = get_incident_frequency(port_locode)
    # Scale: high current congestion amplifies base probability
    if current >= 0.75:
        multiplier = 2.5
    elif current >= 0.55:
        multiplier = 1.6
    elif current >= 0.40:
        multiplier = 1.0
    else:
        multiplier = 0.5
    incident_prob = _clamp(base_freq * multiplier + trend_comp * 0.5)

    logger.debug(
        "Forecast {} | current={:.2f} 7d={:.2f} 30d={:.2f} 90d={:.2f} trend={} p_spike={:.0%}",
        port_locode, current, level_7d, level_30d, level_90d, trend, incident_prob,
    )

    return CongestionForecast(
        port_locode=port_locode,
        current_score=round(current, 4),
        forecast_7d=round(level_7d, 4),
        forecast_30d=round(level_30d, 4),
        forecast_90d=round(level_90d, 4),
        confidence=confidence,
        trend=trend,
        seasonal_component=round(seasonal_comp, 4),
        trend_component=round(trend_comp, 4),
        incident_probability=round(incident_prob, 4),
        ci_lower_7d=mc_7d[1],
        ci_upper_7d=mc_7d[2],
        ci_lower_30d=mc_30d[1],
        ci_upper_30d=mc_30d[2],
        ci_lower_90d=mc_90d[1],
        ci_upper_90d=mc_90d[2],
    )


def forecast_all_ports(
    port_results: list,
    macro_data: Optional[dict] = None,
) -> Dict[str, CongestionForecast]:
    """Produce advanced forecasts for every port in port_results.

    Parameters
    ----------
    port_results: List of objects/dicts with at minimum 'port_locode' and
                  optionally 'current_congestion'.
    macro_data:   Passed through to forecast_congestion_advanced.

    Returns
    -------
    Dict mapping port_locode -> CongestionForecast.
    """
    results: Dict[str, CongestionForecast] = {}
    for pr in port_results:
        if isinstance(pr, dict):
            locode = pr.get("port_locode", "")
            raw_score = pr.get("current_congestion") or pr.get("congestion_score")
        else:
            locode = getattr(pr, "port_locode", "") or getattr(pr, "locode", "")
            raw_score = getattr(pr, "current_congestion", None)

        if not locode:
            continue

        ais_data = {"congestion_score": raw_score} if raw_score is not None else None
        results[locode] = forecast_congestion_advanced(locode, ais_data, macro_data)

    return results
