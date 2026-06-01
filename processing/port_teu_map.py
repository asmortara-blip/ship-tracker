"""processing/port_teu_map.py — per-port container throughput (TEU) for the
global map, categorized into total / import / export / net.

Data provenance (be honest about it)
-------------------------------------
* **Total per-port TEU** — REAL World Bank country *Container Port Traffic*
  (``IS.SHP.GOOD.TU``) split across that country's tracked ports by a MODELED
  ``PORT_TRAFFIC_WEIGHTS`` allocation. The COUNTRY totals are measured; the
  per-port split is modeled.
* **Import / export split** — MODELED. Each port's TEU is split by its
  COUNTRY's REAL merchandise export/import VALUE ratio
  (``TX.VAL.MRCH.CD.WT`` / ``TM.VAL.MRCH.CD.WT``). Trade *value* is only a
  proxy for container *volume*, so treat the split as indicative, not measured.
* **Net** = export − import (TEU-equivalent), i.e. the modeled trade balance.

So: a real-data backbone (WB container traffic + WB merchandise trade values)
with a modeled per-port allocation + a modeled value→volume import/export split.
Nothing here is a forecast or trading advice.

Pure over the World Bank data dict (the shape returned by
``data.worldbank_feed.fetch_port_throughput``): no I/O of its own beyond reading
the ``PORT_TRAFFIC_WEIGHTS`` constant. Never raises — a port with no data
yields zeros.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "PortTEU",
    "export_share_for_country",
    "build_port_teu_map",
    "rank_ports",
    "aggregate_by_region",
    "summarize",
]

# The four ways a port's throughput can be sliced on the map.
CATEGORIES: tuple[str, ...] = ("total", "import", "export", "net")
CATEGORY_LABELS: dict[str, str] = {
    "total": "Total throughput",
    "import": "Imports (modeled)",
    "export": "Exports (modeled)",
    "net": "Net balance (exports − imports)",
}

# WB indicator ids consumed here (kept local so the module documents its inputs).
_TEU_INDICATOR = "IS.SHP.GOOD.TU"
_EXPORTS_INDICATOR = "TX.VAL.MRCH.CD.WT"
_IMPORTS_INDICATOR = "TM.VAL.MRCH.CD.WT"


@dataclass
class PortTEU:
    """One port's container throughput, sliced by category. TEU in MILLIONS/yr.

    ``teu_total`` is real WB country TEU × the modeled per-port weight;
    ``teu_import``/``teu_export`` are that total split by the country's real
    export/import value ratio (modeled); ``teu_net`` = export − import.
    """

    locode: str
    name: str
    region: str
    country_iso3: str
    lat: Optional[float]
    lon: Optional[float]
    teu_total: float
    teu_import: float
    teu_export: float
    teu_net: float
    export_share: float          # [0, 1] — from the country's real trade values
    provenance: str = "real WB country TEU × modeled port split; import/export modeled from real trade-value ratio"

    def value_for(self, category: str) -> float:
        """The TEU figure for a category. Unknown category → total."""
        return {
            "total": self.teu_total,
            "import": self.teu_import,
            "export": self.teu_export,
            "net": self.teu_net,
        }.get(category, self.teu_total)


def _latest_value(df, country_iso3: str) -> float:
    """Most-recent finite numeric value for a country in a WB-normalized
    DataFrame (columns ``country_iso3`` / ``year`` / ``value``), else 0.0."""
    if df is None or getattr(df, "empty", True):
        return 0.0
    try:
        sub = df[df["country_iso3"] == country_iso3]
        if sub.empty:
            return 0.0
        latest = sub.sort_values("year").iloc[-1]
        v = float(latest["value"])
    except (KeyError, TypeError, ValueError, IndexError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):
        return 0.0
    return v


def export_share_for_country(
    country_iso3: str, wb_data: Optional[dict], *, default: float = 0.5,
) -> float:
    """Real merchandise export share = exports / (exports + imports), in [0, 1].

    Uses the country's REAL WB merchandise export/import values. Falls back to
    ``default`` (0.5 = balanced) when the trade data is missing/zero — so a port
    in a country with no trade data is shown as an even split rather than
    skewed. Never raises.
    """
    wb_data = wb_data or {}
    exp = _latest_value(wb_data.get(_EXPORTS_INDICATOR), country_iso3)
    imp = _latest_value(wb_data.get(_IMPORTS_INDICATOR), country_iso3)
    total = exp + imp
    if total <= 0:
        return default
    return max(0.0, min(1.0, exp / total))


def build_port_teu_map(wb_data: Optional[dict], *, ports=None) -> list[PortTEU]:
    """Assemble per-port TEU + the modeled import/export split.

    Pure over ``wb_data`` (the ``fetch_port_throughput`` dict). ``ports``
    defaults to the full tracked registry. Never raises — a port with no WB
    data gets zeros (and a balanced 0.5 split).
    """
    if ports is None:
        try:
            from ports.port_registry import PORTS
            ports = list(PORTS)
        except Exception as exc:  # pragma: no cover - registry should import
            logger.debug(f"port_teu_map: port registry unavailable: {exc}")
            return []

    try:
        from data.worldbank_feed import get_teu_for_country
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"port_teu_map: get_teu_for_country unavailable: {exc}")
        return []

    out: list[PortTEU] = []
    for p in ports:
        iso3 = getattr(p, "country_iso3", "") or ""
        locode = getattr(p, "locode", "") or ""
        try:
            total = float(get_teu_for_country(iso3, wb_data or {}, locode))
        except Exception:
            total = 0.0
        if total != total or total < 0:  # NaN / negative guard
            total = 0.0
        share = export_share_for_country(iso3, wb_data)
        exp = round(total * share, 4)
        imp = round(total * (1.0 - share), 4)
        out.append(PortTEU(
            locode=locode,
            name=getattr(p, "name", locode) or locode,
            region=getattr(p, "region", "") or "",
            country_iso3=iso3,
            lat=getattr(p, "lat", None),
            lon=getattr(p, "lon", None),
            teu_total=round(total, 4),
            teu_import=imp,
            teu_export=exp,
            teu_net=round(exp - imp, 4),
            export_share=round(share, 4),
        ))
    return out


def rank_ports(
    port_teus: list[PortTEU], category: str = "total", *,
    top_n: Optional[int] = None, descending: bool = True,
) -> list[PortTEU]:
    """Ports sorted by the category value (signed for 'net'), id as tie-break."""
    ranked = sorted(
        port_teus,
        key=lambda pt: (pt.value_for(category), pt.locode),
        reverse=descending,
    )
    return ranked[:top_n] if top_n is not None else ranked


def aggregate_by_region(
    port_teus: list[PortTEU], category: str = "total",
) -> dict[str, float]:
    """Sum the category value by region (sorted desc by magnitude)."""
    agg: dict[str, float] = {}
    for pt in port_teus:
        agg[pt.region] = agg.get(pt.region, 0.0) + pt.value_for(category)
    return {
        k: round(v, 4)
        for k, v in sorted(agg.items(), key=lambda kv: -abs(kv[1]))
    }


def summarize(port_teus: list[PortTEU]) -> dict:
    """Platform-wide totals for the UI header strip (millions TEU/yr)."""
    return {
        "n_ports": len(port_teus),
        "n_with_data": sum(1 for pt in port_teus if pt.teu_total > 0),
        "teu_total": round(sum(pt.teu_total for pt in port_teus), 2),
        "teu_export": round(sum(pt.teu_export for pt in port_teus), 2),
        "teu_import": round(sum(pt.teu_import for pt in port_teus), 2),
        "teu_net": round(sum(pt.teu_net for pt in port_teus), 2),
    }
