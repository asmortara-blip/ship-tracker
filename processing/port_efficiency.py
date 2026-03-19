"""
Port efficiency scoring for tracked container ports.

Produces composite efficiency scores based on throughput rank, digitalization,
dwell time, crane productivity, and berth utilization benchmarks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ports.port_registry import PORTS_BY_LOCODE


# ---------------------------------------------------------------------------
# Benchmark data — realistic estimates for the 25 tracked ports
# ---------------------------------------------------------------------------

PORT_BENCHMARKS: dict[str, dict] = {
    "CNSHA": {"throughput_rank": 1,  "digital_index": 0.82, "dwell_days": 3.5, "crane_mph": 32, "berth_util": 0.78},
    "SGSIN": {"throughput_rank": 2,  "digital_index": 0.95, "dwell_days": 2.8, "crane_mph": 35, "berth_util": 0.82},
    "CNNBO": {"throughput_rank": 3,  "digital_index": 0.78, "dwell_days": 3.8, "crane_mph": 30, "berth_util": 0.75},
    "CNSZN": {"throughput_rank": 4,  "digital_index": 0.80, "dwell_days": 3.2, "crane_mph": 31, "berth_util": 0.76},
    "CNTAO": {"throughput_rank": 5,  "digital_index": 0.75, "dwell_days": 4.0, "crane_mph": 28, "berth_util": 0.72},
    "KRPUS": {"throughput_rank": 6,  "digital_index": 0.88, "dwell_days": 3.0, "crane_mph": 33, "berth_util": 0.80},
    "CNTXG": {"throughput_rank": 7,  "digital_index": 0.72, "dwell_days": 4.2, "crane_mph": 27, "berth_util": 0.70},
    "HKHKG": {"throughput_rank": 8,  "digital_index": 0.90, "dwell_days": 2.5, "crane_mph": 36, "berth_util": 0.85},
    "MYPKG": {"throughput_rank": 9,  "digital_index": 0.78, "dwell_days": 3.5, "crane_mph": 29, "berth_util": 0.74},
    "NLRTM": {"throughput_rank": 10, "digital_index": 0.93, "dwell_days": 2.2, "crane_mph": 34, "berth_util": 0.79},
    "AEJEA": {"throughput_rank": 11, "digital_index": 0.92, "dwell_days": 2.0, "crane_mph": 37, "berth_util": 0.83},
    "BEANR": {"throughput_rank": 12, "digital_index": 0.90, "dwell_days": 2.5, "crane_mph": 33, "berth_util": 0.78},
    "MYTPP": {"throughput_rank": 13, "digital_index": 0.80, "dwell_days": 2.8, "crane_mph": 32, "berth_util": 0.76},
    "TWKHH": {"throughput_rank": 14, "digital_index": 0.85, "dwell_days": 3.0, "crane_mph": 31, "berth_util": 0.75},
    "USLAX": {"throughput_rank": 15, "digital_index": 0.75, "dwell_days": 5.5, "crane_mph": 25, "berth_util": 0.85},
    "USLGB": {"throughput_rank": 16, "digital_index": 0.74, "dwell_days": 5.8, "crane_mph": 24, "berth_util": 0.83},
    "DEHAM": {"throughput_rank": 17, "digital_index": 0.88, "dwell_days": 2.8, "crane_mph": 31, "berth_util": 0.76},
    "USNYC": {"throughput_rank": 18, "digital_index": 0.72, "dwell_days": 6.0, "crane_mph": 22, "berth_util": 0.88},
    "MATNM": {"throughput_rank": 19, "digital_index": 0.85, "dwell_days": 2.5, "crane_mph": 30, "berth_util": 0.72},
    "JPYOK": {"throughput_rank": 20, "digital_index": 0.82, "dwell_days": 3.2, "crane_mph": 28, "berth_util": 0.70},
    "LKCMB": {"throughput_rank": 21, "digital_index": 0.70, "dwell_days": 4.5, "crane_mph": 24, "berth_util": 0.68},
    "GRPIR": {"throughput_rank": 22, "digital_index": 0.78, "dwell_days": 3.8, "crane_mph": 26, "berth_util": 0.73},
    "USSAV": {"throughput_rank": 23, "digital_index": 0.76, "dwell_days": 4.2, "crane_mph": 26, "berth_util": 0.78},
    "GBFXT": {"throughput_rank": 24, "digital_index": 0.82, "dwell_days": 3.0, "crane_mph": 29, "berth_util": 0.74},
    "BRSAO": {"throughput_rank": 25, "digital_index": 0.60, "dwell_days": 7.5, "crane_mph": 18, "berth_util": 0.65},
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PortEfficiencyScore:
    port_locode: str
    port_name: str
    efficiency_score: float          # [0, 1] composite
    throughput_efficiency: float     # TEU per infrastructure unit (normalized)
    berth_utilization: float         # estimated 0-1
    dwell_time_days: float           # estimated avg container dwell time
    crane_productivity: float        # estimated moves per hour (normalized)
    digital_index: float             # port digitalization proxy [0, 1]
    connectivity_rank: int           # rank among all tracked ports (1 = best)
    bottleneck_risk: str             # "LOW" | "MODERATE" | "HIGH"
    efficiency_grade: str            # "A+" | "A" | "B+" | "B" | "C" | "D"
    key_strengths: list[str] = field(default_factory=list)
    key_weaknesses: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _normalize_metrics(bm: dict) -> dict[str, float]:
    """Convert raw benchmark values to [0, 1] normalized scores."""
    throughput_eff = 1.0 - (bm["throughput_rank"] - 1) / 24.0
    digital       = bm["digital_index"]
    dwell_score   = _clamp(1.0 - (bm["dwell_days"] - 2.0) / 6.0)
    crane_score   = _clamp((bm["crane_mph"] - 18) / 19.0)
    berth_score   = _clamp(1.0 - abs(bm["berth_util"] - 0.75) / 0.25)
    return {
        "throughput_eff": throughput_eff,
        "digital":        digital,
        "dwell_score":    dwell_score,
        "crane_score":    crane_score,
        "berth_score":    berth_score,
    }


def _composite_score(n: dict[str, float]) -> float:
    return (
        0.25 * n["throughput_eff"]
        + 0.20 * n["digital"]
        + 0.20 * n["dwell_score"]
        + 0.20 * n["crane_score"]
        + 0.15 * n["berth_score"]
    )


def _efficiency_grade(score: float) -> str:
    if score > 0.88:
        return "A+"
    if score > 0.78:
        return "A"
    if score > 0.68:
        return "B+"
    if score > 0.55:
        return "B"
    if score > 0.40:
        return "C"
    return "D"


def _bottleneck_risk(bm: dict) -> str:
    berth = bm["berth_util"]
    dwell = bm["dwell_days"]
    if berth > 0.85 or dwell > 5.5:
        return "HIGH"
    if berth > 0.78 or dwell > 4.0:
        return "MODERATE"
    return "LOW"


def _strengths_weaknesses(
    bm: dict,
    n: dict[str, float],
    score: float,
) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []

    # Throughput
    if n["throughput_eff"] >= 0.75:
        strengths.append("Top-tier global throughput volume")
    elif n["throughput_eff"] < 0.40:
        weaknesses.append("Lower throughput rank relative to peers")

    # Digital
    if bm["digital_index"] >= 0.88:
        strengths.append("High port digitalization and automation")
    elif bm["digital_index"] < 0.70:
        weaknesses.append("Low digitalization index — manual processes dominant")

    # Dwell
    if bm["dwell_days"] <= 2.5:
        strengths.append("Very short container dwell times")
    elif bm["dwell_days"] >= 5.0:
        weaknesses.append(f"Long average dwell time ({bm['dwell_days']:.1f} days)")

    # Crane productivity
    if bm["crane_mph"] >= 33:
        strengths.append("High crane productivity (moves per hour)")
    elif bm["crane_mph"] <= 22:
        weaknesses.append("Low crane productivity")

    # Berth utilization
    if 0.72 <= bm["berth_util"] <= 0.80:
        strengths.append("Optimal berth utilization rate")
    elif bm["berth_util"] > 0.85:
        weaknesses.append("High berth utilization — congestion risk")
    elif bm["berth_util"] < 0.68:
        weaknesses.append("Low berth utilization — underused infrastructure")

    return strengths, weaknesses


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_port_efficiency(
    locode: str,
    port_name: str,
    wb_data: dict | None = None,
) -> PortEfficiencyScore:
    """Compute an efficiency score for a single port.

    Parameters
    ----------
    locode:    UN/LOCODE of the port.
    port_name: Human-readable port name.
    wb_data:   Optional World Bank supplementary data (reserved for future use).

    Returns
    -------
    PortEfficiencyScore dataclass instance.
    """
    bm = PORT_BENCHMARKS[locode]
    n  = _normalize_metrics(bm)
    score = _clamp(_composite_score(n))
    grade = _efficiency_grade(score)
    risk  = _bottleneck_risk(bm)
    strengths, weaknesses = _strengths_weaknesses(bm, n, score)

    # connectivity_rank is determined after all ports are scored; placeholder = throughput_rank
    # score_all_ports() patches this value after sorting.
    return PortEfficiencyScore(
        port_locode=locode,
        port_name=port_name,
        efficiency_score=round(score, 4),
        throughput_efficiency=round(n["throughput_eff"], 4),
        berth_utilization=bm["berth_util"],
        dwell_time_days=bm["dwell_days"],
        crane_productivity=round(n["crane_score"], 4),
        digital_index=bm["digital_index"],
        connectivity_rank=bm["throughput_rank"],  # patched by score_all_ports
        bottleneck_risk=risk,
        efficiency_grade=grade,
        key_strengths=strengths,
        key_weaknesses=weaknesses,
    )


def score_all_ports(wb_data: dict | None = None) -> list[PortEfficiencyScore]:
    """Score all 25 tracked ports and return them sorted by efficiency_score descending.

    connectivity_rank is assigned after sorting (1 = most efficient).
    """
    results: list[PortEfficiencyScore] = []
    for locode, bm in PORT_BENCHMARKS.items():
        port = PORTS_BY_LOCODE.get(locode)
        port_name = port.name if port else locode
        results.append(score_port_efficiency(locode, port_name, wb_data=wb_data))

    results.sort(key=lambda r: r.efficiency_score, reverse=True)

    # Patch connectivity_rank to reflect efficiency ordering
    for rank, result in enumerate(results, start=1):
        object.__setattr__(result, "connectivity_rank", rank) if hasattr(result, "__dataclass_fields__") else None
        # PortEfficiencyScore is a regular (mutable) dataclass — direct assignment is fine
        result.connectivity_rank = rank

    return results
