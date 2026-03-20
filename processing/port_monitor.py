"""Port Operations Monitor — live-feeling operational status for 25 global container ports.

Tracks berth occupancy, crane productivity, throughput, gate utilisation, dwell times,
and operational anomalies for the world's most significant container terminals.

Data: 2025/2026 realistic baselines synthesised from port authority reports,
      UNCTAD maritime statistics, JOC, and Drewry port benchmarking.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from loguru import logger


# ── Status colour palette ─────────────────────────────────────────────────────

STATUS_COLORS = {
    "NORMAL":    "#10b981",
    "DEGRADED":  "#f59e0b",
    "DISRUPTED": "#ef4444",
}


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PortOperationalStatus:
    port_locode: str
    port_name: str
    country_flag: str                    # emoji flag
    region: str

    berths_total: int
    berths_occupied: int
    berths_available: int
    crane_count: int
    crane_operational: int
    throughput_today_teu: int            # synthetic live value
    avg_dwell_time_days: float
    gate_utilization_pct: float          # 0-100

    rail_connection: bool
    deepwater_berths_count: int
    max_vessel_draft_m: float
    max_vessel_teu: int

    last_incident_date: str              # ISO date string or ""
    incident_description: str
    operational_status: str              # "NORMAL" | "DEGRADED" | "DISRUPTED"
    peak_hours: list = field(default_factory=list)

    # Throughput baseline for anomaly detection (annual M TEU → daily TEU)
    annual_teu_m: float = 0.0
    throughput_baseline_daily: int = 0
    throughput_std_daily: int = 0


@dataclass
class PortPerformanceMetric:
    port_locode: str
    metric_date: str                     # ISO date string
    ship_turn_hours: float               # avg vessel time in port (berth to departure)
    crane_productivity_moves_per_hour: float
    gate_truck_wait_minutes: float
    rail_dwell_days: float
    annual_teu_volume_m: float


# ── PORT_OPERATIONAL_DATA ─────────────────────────────────────────────────────
# All 25 ports. Throughput baseline = annual_teu_m * 1_000_000 / 365.
# throughput_today_teu is initialised to baseline; simulate_live_throughput()
# adds noise.

PORT_OPERATIONAL_DATA: list[PortOperationalStatus] = [

    # 1 — Shanghai (CNSHA) ─────────────────────────────────────────────────────
    # World's #1 port. Yangshan deepwater + Waigaoqiao river terminals.
    # 2023 throughput: 49.2 M TEU.
    PortOperationalStatus(
        port_locode="CNSHA",
        port_name="Shanghai",
        country_flag="\U0001f1e8\U0001f1f3",  # CN
        region="Asia East",
        berths_total=130,
        berths_occupied=104,
        berths_available=26,
        crane_count=420,
        crane_operational=398,
        throughput_today_teu=134795,
        avg_dwell_time_days=2.8,
        gate_utilization_pct=82.0,
        rail_connection=True,
        deepwater_berths_count=62,
        max_vessel_draft_m=17.5,
        max_vessel_teu=24000,
        last_incident_date="2025-01-14",
        incident_description="Temporary berth congestion from fog closure at Yangshan; cleared within 18 h.",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00", "20:00-23:00"],
        annual_teu_m=49.2,
        throughput_baseline_daily=134795,
        throughput_std_daily=8500,
    ),

    # 2 — Singapore (SGSIN) ───────────────────────────────────────────────────
    # Tuas Mega Port Phase 1 opened 2022; full 65 M TEU capacity by 2040.
    # 2023 throughput: 38.8 M TEU.
    PortOperationalStatus(
        port_locode="SGSIN",
        port_name="Singapore",
        country_flag="\U0001f1f8\U0001f1ec",  # SG
        region="Southeast Asia",
        berths_total=95,
        berths_occupied=74,
        berths_available=21,
        crane_count=280,
        crane_operational=271,
        throughput_today_teu=106301,
        avg_dwell_time_days=1.4,
        gate_utilization_pct=78.0,
        rail_connection=False,
        deepwater_berths_count=52,
        max_vessel_draft_m=18.0,
        max_vessel_teu=24000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "15:00-20:00"],
        annual_teu_m=38.8,
        throughput_baseline_daily=106301,
        throughput_std_daily=6200,
    ),

    # 3 — Ningbo-Zhoushan (CNNBO) ─────────────────────────────────────────────
    # 2023: 35.3 M TEU. World's busiest bulk-plus-container combo.
    PortOperationalStatus(
        port_locode="CNNBO",
        port_name="Ningbo-Zhoushan",
        country_flag="\U0001f1e8\U0001f1f3",  # CN
        region="Asia East",
        berths_total=112,
        berths_occupied=88,
        berths_available=24,
        crane_count=310,
        crane_operational=294,
        throughput_today_teu=96712,
        avg_dwell_time_days=2.5,
        gate_utilization_pct=79.0,
        rail_connection=True,
        deepwater_berths_count=48,
        max_vessel_draft_m=17.0,
        max_vessel_teu=23000,
        last_incident_date="2025-02-03",
        incident_description="Minor crane maintenance outage; 6 cranes offline for 4 h.",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=35.3,
        throughput_baseline_daily=96712,
        throughput_std_daily=6000,
    ),

    # 4 — Shenzhen (CNSZN) ────────────────────────────────────────────────────
    # Yantian + Shekou + Chiwan terminals. 2023: 29.1 M TEU.
    PortOperationalStatus(
        port_locode="CNSZN",
        port_name="Shenzhen",
        country_flag="\U0001f1e8\U0001f1f3",  # CN
        region="Asia East",
        berths_total=96,
        berths_occupied=75,
        berths_available=21,
        crane_count=245,
        crane_operational=232,
        throughput_today_teu=79726,
        avg_dwell_time_days=2.6,
        gate_utilization_pct=77.0,
        rail_connection=True,
        deepwater_berths_count=38,
        max_vessel_draft_m=16.5,
        max_vessel_teu=22000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "15:00-19:00"],
        annual_teu_m=29.1,
        throughput_baseline_daily=79726,
        throughput_std_daily=5100,
    ),

    # 5 — Qingdao (CNTAO) ─────────────────────────────────────────────────────
    # 2023: 27.9 M TEU. Key north China gateway.
    PortOperationalStatus(
        port_locode="CNTAO",
        port_name="Qingdao",
        country_flag="\U0001f1e8\U0001f1f3",  # CN
        region="Asia East",
        berths_total=82,
        berths_occupied=64,
        berths_available=18,
        crane_count=198,
        crane_operational=186,
        throughput_today_teu=76438,
        avg_dwell_time_days=2.7,
        gate_utilization_pct=74.0,
        rail_connection=True,
        deepwater_berths_count=32,
        max_vessel_draft_m=16.0,
        max_vessel_teu=21000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "13:00-17:00"],
        annual_teu_m=27.9,
        throughput_baseline_daily=76438,
        throughput_std_daily=4900,
    ),

    # 6 — Busan (KRPUS) ───────────────────────────────────────────────────────
    # 2023: 22.1 M TEU. Northeast Asia hub.
    PortOperationalStatus(
        port_locode="KRPUS",
        port_name="Busan",
        country_flag="\U0001f1f0\U0001f1f7",  # KR
        region="Asia East",
        berths_total=74,
        berths_occupied=56,
        berths_available=18,
        crane_count=175,
        crane_operational=168,
        throughput_today_teu=60548,
        avg_dwell_time_days=2.2,
        gate_utilization_pct=71.0,
        rail_connection=True,
        deepwater_berths_count=28,
        max_vessel_draft_m=17.0,
        max_vessel_teu=22000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "14:00-18:00"],
        annual_teu_m=22.1,
        throughput_baseline_daily=60548,
        throughput_std_daily=3900,
    ),

    # 7 — Tianjin (CNTXG) ─────────────────────────────────────────────────────
    # 2023: 21.7 M TEU.
    PortOperationalStatus(
        port_locode="CNTXG",
        port_name="Tianjin",
        country_flag="\U0001f1e8\U0001f1f3",  # CN
        region="Asia East",
        berths_total=78,
        berths_occupied=61,
        berths_available=17,
        crane_count=185,
        crane_operational=176,
        throughput_today_teu=59452,
        avg_dwell_time_days=2.9,
        gate_utilization_pct=72.0,
        rail_connection=True,
        deepwater_berths_count=25,
        max_vessel_draft_m=15.5,
        max_vessel_teu=20000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-09:00", "13:00-17:00"],
        annual_teu_m=21.7,
        throughput_baseline_daily=59452,
        throughput_std_daily=3800,
    ),

    # 8 — Hong Kong (HKHKG) ───────────────────────────────────────────────────
    # Declining due to Pearl River Delta competition. 2023: 14.3 M TEU.
    PortOperationalStatus(
        port_locode="HKHKG",
        port_name="Hong Kong",
        country_flag="\U0001f1ed\U0001f1f0",  # HK
        region="Asia East",
        berths_total=48,
        berths_occupied=31,
        berths_available=17,
        crane_count=102,
        crane_operational=96,
        throughput_today_teu=39178,
        avg_dwell_time_days=2.0,
        gate_utilization_pct=62.0,
        rail_connection=False,
        deepwater_berths_count=22,
        max_vessel_draft_m=15.5,
        max_vessel_teu=20000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["08:00-12:00", "15:00-19:00"],
        annual_teu_m=14.3,
        throughput_baseline_daily=39178,
        throughput_std_daily=2600,
    ),

    # 9 — Port Klang (MYPKG) ──────────────────────────────────────────────────
    # 2023: 14.0 M TEU. Malaysia's main gateway.
    PortOperationalStatus(
        port_locode="MYPKG",
        port_name="Port Klang",
        country_flag="\U0001f1f2\U0001f1fe",  # MY
        region="Southeast Asia",
        berths_total=52,
        berths_occupied=39,
        berths_available=13,
        crane_count=118,
        crane_operational=112,
        throughput_today_teu=38356,
        avg_dwell_time_days=2.5,
        gate_utilization_pct=73.0,
        rail_connection=True,
        deepwater_berths_count=18,
        max_vessel_draft_m=15.0,
        max_vessel_teu=18000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "14:00-18:00"],
        annual_teu_m=14.0,
        throughput_baseline_daily=38356,
        throughput_std_daily=2500,
    ),

    # 10 — Rotterdam (NLRTM) ──────────────────────────────────────────────────
    # Europe's largest. Maasvlakte II deepwater. 2023: 14.0 M TEU.
    PortOperationalStatus(
        port_locode="NLRTM",
        port_name="Rotterdam",
        country_flag="\U0001f1f3\U0001f1f1",  # NL
        region="Europe",
        berths_total=74,
        berths_occupied=55,
        berths_available=19,
        crane_count=195,
        crane_operational=188,
        throughput_today_teu=38356,
        avg_dwell_time_days=1.9,
        gate_utilization_pct=68.0,
        rail_connection=True,
        deepwater_berths_count=36,
        max_vessel_draft_m=19.0,
        max_vessel_teu=24000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=14.0,
        throughput_baseline_daily=38356,
        throughput_std_daily=2400,
    ),

    # 11 — Jebel Ali / Dubai (AEJEA) ──────────────────────────────────────────
    # Middle East hub. DP World. 2023: 14.0 M TEU.
    PortOperationalStatus(
        port_locode="AEJEA",
        port_name="Jebel Ali",
        country_flag="\U0001f1e6\U0001f1ea",  # AE
        region="Middle East",
        berths_total=67,
        berths_occupied=50,
        berths_available=17,
        crane_count=148,
        crane_operational=141,
        throughput_today_teu=38356,
        avg_dwell_time_days=2.3,
        gate_utilization_pct=76.0,
        rail_connection=False,
        deepwater_berths_count=26,
        max_vessel_draft_m=17.0,
        max_vessel_teu=22000,
        last_incident_date="2025-01-28",
        incident_description="Red Sea diversions driving 18% throughput surge vs prior year; yard density elevated.",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "15:00-20:00"],
        annual_teu_m=14.0,
        throughput_baseline_daily=38356,
        throughput_std_daily=3100,
    ),

    # 12 — Antwerp-Bruges (BEANR) ─────────────────────────────────────────────
    # 2023: 12.0 M TEU. Europe's #2 container port.
    PortOperationalStatus(
        port_locode="BEANR",
        port_name="Antwerp-Bruges",
        country_flag="\U0001f1e7\U0001f1ea",  # BE
        region="Europe",
        berths_total=62,
        berths_occupied=44,
        berths_available=18,
        crane_count=152,
        crane_operational=146,
        throughput_today_teu=32877,
        avg_dwell_time_days=2.1,
        gate_utilization_pct=66.0,
        rail_connection=True,
        deepwater_berths_count=22,
        max_vessel_draft_m=16.5,
        max_vessel_teu=22000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=12.0,
        throughput_baseline_daily=32877,
        throughput_std_daily=2100,
    ),

    # 13 — Tanjung Pelepas (MYTPP) ────────────────────────────────────────────
    # 2023: 10.5 M TEU. Maersk hub gateway.
    PortOperationalStatus(
        port_locode="MYTPP",
        port_name="Tanjung Pelepas",
        country_flag="\U0001f1f2\U0001f1fe",  # MY
        region="Southeast Asia",
        berths_total=42,
        berths_occupied=31,
        berths_available=11,
        crane_count=98,
        crane_operational=93,
        throughput_today_teu=28767,
        avg_dwell_time_days=1.8,
        gate_utilization_pct=70.0,
        rail_connection=False,
        deepwater_berths_count=16,
        max_vessel_draft_m=17.5,
        max_vessel_teu=22000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "15:00-19:00"],
        annual_teu_m=10.5,
        throughput_baseline_daily=28767,
        throughput_std_daily=1900,
    ),

    # 14 — Kaohsiung (TWKHH) ──────────────────────────────────────────────────
    # 2023: 9.4 M TEU. Key Taiwan gateway.
    PortOperationalStatus(
        port_locode="TWKHH",
        port_name="Kaohsiung",
        country_flag="\U0001f1f9\U0001f1fc",  # TW
        region="Asia East",
        berths_total=48,
        berths_occupied=34,
        berths_available=14,
        crane_count=112,
        crane_operational=107,
        throughput_today_teu=25753,
        avg_dwell_time_days=2.3,
        gate_utilization_pct=67.0,
        rail_connection=True,
        deepwater_berths_count=14,
        max_vessel_draft_m=16.0,
        max_vessel_teu=20000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "14:00-18:00"],
        annual_teu_m=9.4,
        throughput_baseline_daily=25753,
        throughput_std_daily=1700,
    ),

    # 15 — Los Angeles (USLAX) ────────────────────────────────────────────────
    # 2023: 9.2 M TEU. Chronic congestion; inland empire chassis shortages.
    PortOperationalStatus(
        port_locode="USLAX",
        port_name="Los Angeles",
        country_flag="\U0001f1fa\U0001f1f8",  # US
        region="North America West",
        berths_total=67,
        berths_occupied=48,
        berths_available=19,
        crane_count=72,
        crane_operational=64,
        throughput_today_teu=25205,
        avg_dwell_time_days=4.8,
        gate_utilization_pct=88.0,
        rail_connection=True,
        deepwater_berths_count=30,
        max_vessel_draft_m=15.8,
        max_vessel_teu=20000,
        last_incident_date="2025-03-01",
        incident_description="Labor slowdown at Yusen Terminal; 6-hr gate backlog. Truck wait times exceeding 3 h.",
        operational_status="DEGRADED",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=9.2,
        throughput_baseline_daily=25205,
        throughput_std_daily=3200,
    ),

    # 16 — Long Beach (USLGB) ─────────────────────────────────────────────────
    # 2023: 9.1 M TEU. Partner to LA; together form San Pedro Bay complex.
    PortOperationalStatus(
        port_locode="USLGB",
        port_name="Long Beach",
        country_flag="\U0001f1fa\U0001f1f8",  # US
        region="North America West",
        berths_total=65,
        berths_occupied=46,
        berths_available=19,
        crane_count=68,
        crane_operational=61,
        throughput_today_teu=24932,
        avg_dwell_time_days=4.6,
        gate_utilization_pct=85.0,
        rail_connection=True,
        deepwater_berths_count=28,
        max_vessel_draft_m=15.5,
        max_vessel_teu=18000,
        last_incident_date="2025-03-01",
        incident_description="Elevated vessel queues from Lunar New Year peak; average anchorage wait 28 h.",
        operational_status="DEGRADED",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=9.1,
        throughput_baseline_daily=24932,
        throughput_std_daily=3100,
    ),

    # 17 — Hamburg (DEHAM) ────────────────────────────────────────────────────
    # 2023: 8.3 M TEU. Elbe depth limit restricts ULCV access.
    PortOperationalStatus(
        port_locode="DEHAM",
        port_name="Hamburg",
        country_flag="\U0001f1e9\U0001f1ea",  # DE
        region="Europe",
        berths_total=56,
        berths_occupied=38,
        berths_available=18,
        crane_count=132,
        crane_operational=126,
        throughput_today_teu=22740,
        avg_dwell_time_days=2.3,
        gate_utilization_pct=63.0,
        rail_connection=True,
        deepwater_berths_count=12,
        max_vessel_draft_m=13.5,
        max_vessel_teu=16000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=8.3,
        throughput_baseline_daily=22740,
        throughput_std_daily=1600,
    ),

    # 18 — New York / New Jersey (USNYC) ──────────────────────────────────────
    # 2023: 7.8 M TEU. Bayonne Bridge clearance now 65.5 m.
    PortOperationalStatus(
        port_locode="USNYC",
        port_name="New York / New Jersey",
        country_flag="\U0001f1fa\U0001f1f8",  # US
        region="North America East",
        berths_total=54,
        berths_occupied=39,
        berths_available=15,
        crane_count=86,
        crane_operational=80,
        throughput_today_teu=21370,
        avg_dwell_time_days=3.8,
        gate_utilization_pct=80.0,
        rail_connection=True,
        deepwater_berths_count=16,
        max_vessel_draft_m=15.2,
        max_vessel_teu=18000,
        last_incident_date="2025-02-20",
        incident_description="ILA work-to-rule action; gate productivity 15% below normal.",
        operational_status="DEGRADED",
        peak_hours=["07:00-11:00", "15:00-19:00"],
        annual_teu_m=7.8,
        throughput_baseline_daily=21370,
        throughput_std_daily=2400,
    ),

    # 19 — Tanger Med (MATNM) ─────────────────────────────────────────────────
    # 2023: 7.2 M TEU. Africa's #1. Booming transshipment hub.
    PortOperationalStatus(
        port_locode="MATNM",
        port_name="Tanger Med",
        country_flag="\U0001f1f2\U0001f1e6",  # MA
        region="Africa",
        berths_total=46,
        berths_occupied=33,
        berths_available=13,
        crane_count=108,
        crane_operational=104,
        throughput_today_teu=19726,
        avg_dwell_time_days=1.7,
        gate_utilization_pct=71.0,
        rail_connection=True,
        deepwater_berths_count=20,
        max_vessel_draft_m=17.0,
        max_vessel_teu=22000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["08:00-12:00", "16:00-20:00"],
        annual_teu_m=7.2,
        throughput_baseline_daily=19726,
        throughput_std_daily=1600,
    ),

    # 20 — Yokohama (JPYOK) ───────────────────────────────────────────────────
    # 2023: 3.1 M TEU. Japan's #1 container port.
    PortOperationalStatus(
        port_locode="JPYOK",
        port_name="Yokohama",
        country_flag="\U0001f1ef\U0001f1f5",  # JP
        region="Asia East",
        berths_total=38,
        berths_occupied=26,
        berths_available=12,
        crane_count=82,
        crane_operational=79,
        throughput_today_teu=8493,
        avg_dwell_time_days=1.9,
        gate_utilization_pct=60.0,
        rail_connection=True,
        deepwater_berths_count=12,
        max_vessel_draft_m=15.0,
        max_vessel_teu=18000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["08:00-12:00", "14:00-18:00"],
        annual_teu_m=3.1,
        throughput_baseline_daily=8493,
        throughput_std_daily=700,
    ),

    # 21 — Colombo (LKCMB) ────────────────────────────────────────────────────
    # 2023: 7.2 M TEU. Fastest-growing South Asian hub.
    PortOperationalStatus(
        port_locode="LKCMB",
        port_name="Colombo",
        country_flag="\U0001f1f1\U0001f1f0",  # LK
        region="South Asia",
        berths_total=40,
        berths_occupied=28,
        berths_available=12,
        crane_count=88,
        crane_operational=84,
        throughput_today_teu=19726,
        avg_dwell_time_days=2.1,
        gate_utilization_pct=72.0,
        rail_connection=False,
        deepwater_berths_count=10,
        max_vessel_draft_m=18.0,
        max_vessel_teu=24000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "15:00-19:00"],
        annual_teu_m=7.2,
        throughput_baseline_daily=19726,
        throughput_std_daily=1600,
    ),

    # 22 — Piraeus (GRPIR) ────────────────────────────────────────────────────
    # COSCO-operated. 2023: 5.5 M TEU. SE Europe gateway.
    PortOperationalStatus(
        port_locode="GRPIR",
        port_name="Piraeus",
        country_flag="\U0001f1ec\U0001f1f7",  # GR
        region="Europe",
        berths_total=36,
        berths_occupied=24,
        berths_available=12,
        crane_count=78,
        crane_operational=73,
        throughput_today_teu=15068,
        avg_dwell_time_days=2.0,
        gate_utilization_pct=64.0,
        rail_connection=True,
        deepwater_berths_count=14,
        max_vessel_draft_m=16.5,
        max_vessel_teu=20000,
        last_incident_date="2025-03-05",
        incident_description="Port authority customs IT system upgrade caused 6-h processing delays for empties.",
        operational_status="NORMAL",
        peak_hours=["08:00-12:00", "15:00-19:00"],
        annual_teu_m=5.5,
        throughput_baseline_daily=15068,
        throughput_std_daily=1200,
    ),

    # 23 — Savannah (USSAV) ───────────────────────────────────────────────────
    # 2023: 5.8 M TEU. Fastest-growing US East Coast port.
    PortOperationalStatus(
        port_locode="USSAV",
        port_name="Savannah",
        country_flag="\U0001f1fa\U0001f1f8",  # US
        region="North America East",
        berths_total=34,
        berths_occupied=25,
        berths_available=9,
        crane_count=58,
        crane_operational=54,
        throughput_today_teu=15890,
        avg_dwell_time_days=3.2,
        gate_utilization_pct=83.0,
        rail_connection=True,
        deepwater_berths_count=10,
        max_vessel_draft_m=14.0,
        max_vessel_teu=16000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["06:00-10:00", "14:00-18:00"],
        annual_teu_m=5.8,
        throughput_baseline_daily=15890,
        throughput_std_daily=1800,
    ),

    # 24 — Felixstowe (GBFXT) ─────────────────────────────────────────────────
    # 2023: 3.5 M TEU. UK's largest container port.
    PortOperationalStatus(
        port_locode="GBFXT",
        port_name="Felixstowe",
        country_flag="\U0001f1ec\U0001f1e7",  # GB
        region="Europe",
        berths_total=30,
        berths_occupied=19,
        berths_available=11,
        crane_count=62,
        crane_operational=58,
        throughput_today_teu=9589,
        avg_dwell_time_days=2.5,
        gate_utilization_pct=66.0,
        rail_connection=True,
        deepwater_berths_count=8,
        max_vessel_draft_m=14.5,
        max_vessel_teu=16000,
        last_incident_date="",
        incident_description="",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "14:00-18:00"],
        annual_teu_m=3.5,
        throughput_baseline_daily=9589,
        throughput_std_daily=800,
    ),

    # 25 — Santos (BRSAO) ─────────────────────────────────────────────────────
    # 2023: 4.5 M TEU. South America's #1.
    PortOperationalStatus(
        port_locode="BRSAO",
        port_name="Santos",
        country_flag="\U0001f1e7\U0001f1f7",  # BR
        region="South America",
        berths_total=38,
        berths_occupied=28,
        berths_available=10,
        crane_count=72,
        crane_operational=66,
        throughput_today_teu=12329,
        avg_dwell_time_days=3.8,
        gate_utilization_pct=81.0,
        rail_connection=True,
        deepwater_berths_count=8,
        max_vessel_draft_m=13.5,
        max_vessel_teu=14000,
        last_incident_date="2025-02-14",
        incident_description="Customs strike reduced gate processing capacity 40% for 3 days.",
        operational_status="NORMAL",
        peak_hours=["07:00-11:00", "14:00-18:00"],
        annual_teu_m=4.5,
        throughput_baseline_daily=12329,
        throughput_std_daily=1500,
    ),
]

PORT_OPERATIONAL_BY_LOCODE: dict[str, PortOperationalStatus] = {
    p.port_locode: p for p in PORT_OPERATIONAL_DATA
}


# ── PORT_PERFORMANCE_BENCHMARKS ───────────────────────────────────────────────

PORT_PERFORMANCE_BENCHMARKS: list[PortPerformanceMetric] = [
    # Ship turn time = vessel berth arrival to departure (hours)
    # Crane productivity = container moves per crane hour (gross)
    PortPerformanceMetric(
        port_locode="SGSIN", metric_date="2025-01-01",
        ship_turn_hours=18.0, crane_productivity_moves_per_hour=32.0,
        gate_truck_wait_minutes=22.0, rail_dwell_days=0.0,
        annual_teu_volume_m=38.8,
    ),
    PortPerformanceMetric(
        port_locode="CNSHA", metric_date="2025-01-01",
        ship_turn_hours=22.0, crane_productivity_moves_per_hour=31.0,
        gate_truck_wait_minutes=35.0, rail_dwell_days=1.4,
        annual_teu_volume_m=49.2,
    ),
    PortPerformanceMetric(
        port_locode="CNNBO", metric_date="2025-01-01",
        ship_turn_hours=23.0, crane_productivity_moves_per_hour=30.0,
        gate_truck_wait_minutes=38.0, rail_dwell_days=1.6,
        annual_teu_volume_m=35.3,
    ),
    PortPerformanceMetric(
        port_locode="CNSZN", metric_date="2025-01-01",
        ship_turn_hours=24.0, crane_productivity_moves_per_hour=30.5,
        gate_truck_wait_minutes=36.0, rail_dwell_days=1.5,
        annual_teu_volume_m=29.1,
    ),
    PortPerformanceMetric(
        port_locode="CNTAO", metric_date="2025-01-01",
        ship_turn_hours=25.0, crane_productivity_moves_per_hour=29.5,
        gate_truck_wait_minutes=40.0, rail_dwell_days=1.8,
        annual_teu_volume_m=27.9,
    ),
    PortPerformanceMetric(
        port_locode="KRPUS", metric_date="2025-01-01",
        ship_turn_hours=20.0, crane_productivity_moves_per_hour=30.0,
        gate_truck_wait_minutes=28.0, rail_dwell_days=1.2,
        annual_teu_volume_m=22.1,
    ),
    PortPerformanceMetric(
        port_locode="CNTXG", metric_date="2025-01-01",
        ship_turn_hours=26.0, crane_productivity_moves_per_hour=29.0,
        gate_truck_wait_minutes=42.0, rail_dwell_days=1.9,
        annual_teu_volume_m=21.7,
    ),
    PortPerformanceMetric(
        port_locode="HKHKG", metric_date="2025-01-01",
        ship_turn_hours=21.0, crane_productivity_moves_per_hour=30.0,
        gate_truck_wait_minutes=30.0, rail_dwell_days=0.0,
        annual_teu_volume_m=14.3,
    ),
    PortPerformanceMetric(
        port_locode="MYPKG", metric_date="2025-01-01",
        ship_turn_hours=24.0, crane_productivity_moves_per_hour=28.0,
        gate_truck_wait_minutes=38.0, rail_dwell_days=1.5,
        annual_teu_volume_m=14.0,
    ),
    PortPerformanceMetric(
        port_locode="NLRTM", metric_date="2025-01-01",
        ship_turn_hours=24.0, crane_productivity_moves_per_hour=29.0,
        gate_truck_wait_minutes=32.0, rail_dwell_days=1.2,
        annual_teu_volume_m=14.0,
    ),
    PortPerformanceMetric(
        port_locode="AEJEA", metric_date="2025-01-01",
        ship_turn_hours=28.0, crane_productivity_moves_per_hour=28.0,
        gate_truck_wait_minutes=48.0, rail_dwell_days=0.0,
        annual_teu_volume_m=14.0,
    ),
    PortPerformanceMetric(
        port_locode="BEANR", metric_date="2025-01-01",
        ship_turn_hours=26.0, crane_productivity_moves_per_hour=27.5,
        gate_truck_wait_minutes=35.0, rail_dwell_days=1.4,
        annual_teu_volume_m=12.0,
    ),
    PortPerformanceMetric(
        port_locode="MYTPP", metric_date="2025-01-01",
        ship_turn_hours=20.0, crane_productivity_moves_per_hour=31.0,
        gate_truck_wait_minutes=25.0, rail_dwell_days=0.0,
        annual_teu_volume_m=10.5,
    ),
    PortPerformanceMetric(
        port_locode="TWKHH", metric_date="2025-01-01",
        ship_turn_hours=22.0, crane_productivity_moves_per_hour=29.5,
        gate_truck_wait_minutes=33.0, rail_dwell_days=1.3,
        annual_teu_volume_m=9.4,
    ),
    PortPerformanceMetric(
        port_locode="USLAX", metric_date="2025-01-01",
        ship_turn_hours=48.0, crane_productivity_moves_per_hour=25.0,
        gate_truck_wait_minutes=185.0, rail_dwell_days=4.2,
        annual_teu_volume_m=9.2,
    ),
    PortPerformanceMetric(
        port_locode="USLGB", metric_date="2025-01-01",
        ship_turn_hours=46.0, crane_productivity_moves_per_hour=25.5,
        gate_truck_wait_minutes=172.0, rail_dwell_days=4.0,
        annual_teu_volume_m=9.1,
    ),
    PortPerformanceMetric(
        port_locode="DEHAM", metric_date="2025-01-01",
        ship_turn_hours=28.0, crane_productivity_moves_per_hour=27.0,
        gate_truck_wait_minutes=40.0, rail_dwell_days=1.8,
        annual_teu_volume_m=8.3,
    ),
    PortPerformanceMetric(
        port_locode="USNYC", metric_date="2025-01-01",
        ship_turn_hours=36.0, crane_productivity_moves_per_hour=26.0,
        gate_truck_wait_minutes=120.0, rail_dwell_days=3.2,
        annual_teu_volume_m=7.8,
    ),
    PortPerformanceMetric(
        port_locode="MATNM", metric_date="2025-01-01",
        ship_turn_hours=19.0, crane_productivity_moves_per_hour=30.5,
        gate_truck_wait_minutes=28.0, rail_dwell_days=1.1,
        annual_teu_volume_m=7.2,
    ),
    PortPerformanceMetric(
        port_locode="JPYOK", metric_date="2025-01-01",
        ship_turn_hours=22.0, crane_productivity_moves_per_hour=28.5,
        gate_truck_wait_minutes=30.0, rail_dwell_days=1.3,
        annual_teu_volume_m=3.1,
    ),
    PortPerformanceMetric(
        port_locode="LKCMB", metric_date="2025-01-01",
        ship_turn_hours=22.0, crane_productivity_moves_per_hour=29.0,
        gate_truck_wait_minutes=35.0, rail_dwell_days=0.0,
        annual_teu_volume_m=7.2,
    ),
    PortPerformanceMetric(
        port_locode="GRPIR", metric_date="2025-01-01",
        ship_turn_hours=30.0, crane_productivity_moves_per_hour=27.0,
        gate_truck_wait_minutes=50.0, rail_dwell_days=1.5,
        annual_teu_volume_m=5.5,
    ),
    PortPerformanceMetric(
        port_locode="USSAV", metric_date="2025-01-01",
        ship_turn_hours=32.0, crane_productivity_moves_per_hour=26.5,
        gate_truck_wait_minutes=95.0, rail_dwell_days=2.8,
        annual_teu_volume_m=5.8,
    ),
    PortPerformanceMetric(
        port_locode="GBFXT", metric_date="2025-01-01",
        ship_turn_hours=30.0, crane_productivity_moves_per_hour=27.5,
        gate_truck_wait_minutes=45.0, rail_dwell_days=1.6,
        annual_teu_volume_m=3.5,
    ),
    PortPerformanceMetric(
        port_locode="BRSAO", metric_date="2025-01-01",
        ship_turn_hours=42.0, crane_productivity_moves_per_hour=23.5,
        gate_truck_wait_minutes=140.0, rail_dwell_days=3.5,
        annual_teu_volume_m=4.5,
    ),
]

PERF_BY_LOCODE: dict[str, PortPerformanceMetric] = {
    m.port_locode: m for m in PORT_PERFORMANCE_BENCHMARKS
}


# ── Live simulation helpers ───────────────────────────────────────────────────

def _hour_of_day_factor(port: PortOperationalStatus) -> float:
    """Return a multiplier (0.6-1.15) based on whether current UTC hour overlaps a peak window."""
    now_h = datetime.now(timezone.utc).hour
    for window in port.peak_hours:
        parts = window.split("-")
        if len(parts) == 2:
            try:
                start_h = int(parts[0].split(":")[0])
                end_h   = int(parts[1].split(":")[0])
                if start_h <= now_h < end_h:
                    return 1.12
            except ValueError:
                pass
    return 0.88


def simulate_live_throughput(port_locode: str) -> dict:
    """Return a dict of synthetic live operational statistics for a port.

    Adds Gaussian noise around each port's daily baseline, further modulated
    by time-of-day (peak hours +12%, off-peak -12%) and port operational status.

    Returns
    -------
    dict with keys:
        throughput_teu, berth_occupancy_pct, crane_utilization_pct,
        gate_wait_minutes, vessel_queue, avg_dwell_time_days, timestamp_utc
    """
    port = PORT_OPERATIONAL_BY_LOCODE.get(port_locode)
    if port is None:
        logger.warning("simulate_live_throughput: unknown locode {}", port_locode)
        return {}

    # Seed from locode + current minute for stable-ish values within each minute
    seed_minute = datetime.now(timezone.utc).minute
    rng = random.Random(hash(port_locode + str(seed_minute)))

    tod_factor = _hour_of_day_factor(port)

    # Status-based disruption factor
    status_factor = {"NORMAL": 1.0, "DEGRADED": 0.78, "DISRUPTED": 0.52}.get(
        port.operational_status, 1.0
    )

    raw_teu = port.throughput_baseline_daily * tod_factor * status_factor
    noise   = rng.gauss(0, port.throughput_std_daily * 0.15)
    teu     = max(0, int(raw_teu + noise))

    # Berth occupancy: add small noise around occupied count
    occ_noise   = rng.randint(-2, 2)
    occ         = max(0, min(port.berths_total, port.berths_occupied + occ_noise))
    berth_pct   = round(occ / max(port.berths_total, 1) * 100, 1)

    # Crane utilisation
    crane_noise = rng.randint(-3, 3)
    crane_op    = max(0, min(port.crane_count, port.crane_operational + crane_noise))
    crane_pct   = round(crane_op / max(port.crane_count, 1) * 100, 1)

    # Gate wait: baseline from perf benchmark + status scaling
    perf     = PERF_BY_LOCODE.get(port_locode)
    base_wait = perf.gate_truck_wait_minutes if perf else 45.0
    wait_mult = {"NORMAL": 1.0, "DEGRADED": 1.55, "DISRUPTED": 2.4}.get(
        port.operational_status, 1.0
    )
    gate_wait = round(base_wait * wait_mult * tod_factor + rng.gauss(0, 5), 0)
    gate_wait = max(5.0, gate_wait)

    # Vessel queue: 0 at well-run ports, elevated at congested ones
    base_queue = max(0, int((berth_pct - 70) / 8))
    vessel_queue = max(0, base_queue + rng.randint(-1, 3))

    # Dwell time
    dwell_noise = rng.gauss(0, 0.2)
    dwell = round(port.avg_dwell_time_days * status_factor + dwell_noise, 2)
    dwell = max(0.5, dwell)

    ts = (
        str(datetime.now(timezone.utc).year) + "-"
        + str(datetime.now(timezone.utc).month).zfill(2) + "-"
        + str(datetime.now(timezone.utc).day).zfill(2) + "T"
        + str(datetime.now(timezone.utc).hour).zfill(2) + ":"
        + str(datetime.now(timezone.utc).minute).zfill(2) + "Z"
    )

    return {
        "throughput_teu": teu,
        "berth_occupancy_pct": berth_pct,
        "crane_utilization_pct": crane_pct,
        "gate_wait_minutes": gate_wait,
        "vessel_queue": vessel_queue,
        "avg_dwell_time_days": dwell,
        "timestamp_utc": ts,
    }


def detect_port_anomalies(port_locode: str, current_stats: dict) -> list:
    """Flag anomalies in current_stats relative to the port's baseline.

    Checks performed
    ----------------
    1. Throughput deviation: flag if |actual - baseline| > 2 * std.
    2. Dwell time: flag if actual > 1.5x the baseline dwell.
    3. Gate wait: flag if actual > 2x the benchmark gate wait.
    4. Vessel queue: flag if queue >= 5.
    5. Operational status: DEGRADED/DISRUPTED always raises a flag.
    6. Crane utilisation: flag if below 60% (idle assets) or above 98% (no headroom).

    Returns
    -------
    list of str — human-readable anomaly descriptions.
    """
    port = PORT_OPERATIONAL_BY_LOCODE.get(port_locode)
    if port is None or not current_stats:
        return []

    flags: list = []

    # 1 — throughput deviation
    teu      = current_stats.get("throughput_teu", port.throughput_baseline_daily)
    baseline = port.throughput_baseline_daily
    std      = port.throughput_std_daily or max(1, baseline * 0.06)
    deviation = abs(teu - baseline)
    if deviation > 2.0 * std:
        direction = "above" if teu > baseline else "below"
        pct_dev   = round((teu - baseline) / max(baseline, 1) * 100, 1)
        flags.append(
            port_locode + " — Throughput " + str(abs(pct_dev)) + "% " + direction
            + " baseline (" + str(teu) + " vs " + str(baseline) + " TEU/day)"
            + " — >2\u03c3 deviation"
        )

    # 2 — dwell time
    dwell = current_stats.get("avg_dwell_time_days", port.avg_dwell_time_days)
    if dwell > port.avg_dwell_time_days * 1.5:
        flags.append(
            port_locode + " — Avg dwell " + str(dwell) + " days vs baseline "
            + str(port.avg_dwell_time_days) + " days — yard congestion suspected"
        )

    # 3 — gate wait
    perf       = PERF_BY_LOCODE.get(port_locode)
    gate_wait  = current_stats.get("gate_wait_minutes", 0.0)
    bench_wait = (perf.gate_truck_wait_minutes if perf else 60.0)
    if gate_wait > bench_wait * 2.0:
        flags.append(
            port_locode + " — Gate truck wait " + str(int(gate_wait)) + " min"
            + " (benchmark " + str(int(bench_wait)) + " min) — gate bottleneck"
        )

    # 4 — vessel queue
    queue = current_stats.get("vessel_queue", 0)
    if queue >= 5:
        flags.append(
            port_locode + " — " + str(queue) + " vessels at anchor — elevated anchorage queue"
        )

    # 5 — operational status
    if port.operational_status == "DISRUPTED":
        flags.append(
            port_locode + " [DISRUPTED] — " + (port.incident_description or "Service severely impacted")
        )
    elif port.operational_status == "DEGRADED":
        flags.append(
            port_locode + " [DEGRADED] — " + (port.incident_description or "Reduced operational capacity")
        )

    # 6 — crane utilisation extremes
    crane_pct = current_stats.get("crane_utilization_pct", 80.0)
    if crane_pct < 60.0:
        flags.append(
            port_locode + " — Crane utilisation low at "
            + str(crane_pct) + "% — possible demand shortfall or maintenance"
        )
    elif crane_pct > 98.0:
        flags.append(
            port_locode + " — Crane utilisation critical at "
            + str(crane_pct) + "% — zero headroom for surges"
        )

    if flags:
        logger.debug("detect_port_anomalies: {} flags for {}", len(flags), port_locode)

    return flags


def get_all_live_stats() -> dict:
    """Return simulate_live_throughput() for every port. O(25)."""
    return {p.port_locode: simulate_live_throughput(p.port_locode) for p in PORT_OPERATIONAL_DATA}


def get_all_anomalies() -> list:
    """Run detect_port_anomalies for every port against its live stats.

    Returns a flat list of anomaly strings across all 25 ports.
    """
    all_flags: list = []
    for port in PORT_OPERATIONAL_DATA:
        stats = simulate_live_throughput(port.port_locode)
        flags = detect_port_anomalies(port.port_locode, stats)
        all_flags.extend(flags)
    return all_flags
