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
    "PortTEUTrend",
    "export_share_for_country",
    "build_port_teu_map",
    "build_port_teu_trends",
    "rank_ports",
    "rank_by_growth",
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
    connectivity: float = 0.0    # real WB Liner Shipping Connectivity Index (country-level)
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
        from data.worldbank_feed import (
            get_connectivity_for_country,
            get_teu_for_country,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"port_teu_map: WB accessors unavailable: {exc}")
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
        try:  # real WB Liner Shipping Connectivity Index (country-level)
            conn = float(get_connectivity_for_country(iso3, wb_data or {}))
        except Exception:
            conn = 0.0
        if conn != conn or conn < 0:  # NaN / negative guard
            conn = 0.0
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
            connectivity=round(conn, 1),
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


# ---------------------------------------------------------------------------
# Throughput trends over time (REAL World Bank multi-year container traffic)
# ---------------------------------------------------------------------------


@dataclass
class PortTEUTrend:
    """A port's container-throughput trajectory over the WB data window.

    ``teu_by_year`` is per-port (real country TEU per year × the modeled port
    weight), aligned to ``years``. ``cagr_pct`` is the compound annual growth
    rate across the window; ``yoy_latest_pct`` the most-recent year-over-year
    change. From REAL World Bank annual data (the per-port split is modeled).
    """

    locode: str
    name: str
    region: str
    years: list[int]
    teu_by_year: list[float]
    cagr_pct: float
    yoy_latest_pct: float
    n_years: int


def _port_weight(country_iso3: str, locode: str) -> float:
    """The modeled per-port allocation weight (1.0 when the country has a single
    tracked port / no entry). Mirrors ``get_teu_for_country``'s weighting."""
    try:
        from ports.port_registry import PORT_TRAFFIC_WEIGHTS
    except Exception:  # pragma: no cover - registry should import
        return 1.0
    if locode and country_iso3 in PORT_TRAFFIC_WEIGHTS:
        try:
            return float(PORT_TRAFFIC_WEIGHTS[country_iso3].get(locode, 1.0))
        except (TypeError, ValueError):
            return 1.0
    return 1.0


def _country_teu_by_year(df, country_iso3: str) -> tuple[list[int], list[float]]:
    """``(years, raw_TEU_values)`` for a country from the IS.SHP.GOOD.TU frame,
    ascending by year, finite + positive only. ``([], [])`` if none."""
    if df is None or getattr(df, "empty", True):
        return [], []
    try:
        sub = df[df["country_iso3"] == country_iso3].sort_values("year")
    except (KeyError, TypeError):
        return [], []
    years: list[int] = []
    vals: list[float] = []
    for _, row in sub.iterrows():
        try:
            y = int(row["year"]); v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if v == v and v > 0:  # finite + positive
            years.append(y)
            vals.append(v)
    return years, vals


def build_port_teu_trends(
    wb_data: Optional[dict], *, ports=None,
) -> list[PortTEUTrend]:
    """Per-port TEU trajectory + CAGR / latest-YoY over the WB window.

    Pure over ``wb_data``. Per-port TEU each year = real country TEU that year ×
    the modeled port weight (in millions). Never raises; a port with < 2 data
    years gets its available points with 0% growth.
    """
    if ports is None:
        try:
            from ports.port_registry import PORTS
            ports = list(PORTS)
        except Exception:  # pragma: no cover
            return []
    df = (wb_data or {}).get(_TEU_INDICATOR)
    out: list[PortTEUTrend] = []
    for p in ports:
        iso3 = getattr(p, "country_iso3", "") or ""
        locode = getattr(p, "locode", "") or ""
        years, cvals = _country_teu_by_year(df, iso3)
        w = _port_weight(iso3, locode)
        teu_year = [round((v / 1_000_000) * w, 4) for v in cvals]  # millions
        cagr = 0.0
        yoy = 0.0
        if len(teu_year) >= 2 and teu_year[0] > 0:
            n = len(teu_year) - 1
            cagr = round(((teu_year[-1] / teu_year[0]) ** (1.0 / n) - 1.0) * 100.0, 2)
            if teu_year[-2] > 0:
                yoy = round((teu_year[-1] / teu_year[-2] - 1.0) * 100.0, 2)
        out.append(PortTEUTrend(
            locode=locode,
            name=getattr(p, "name", locode) or locode,
            region=getattr(p, "region", "") or "",
            years=years,
            teu_by_year=teu_year,
            cagr_pct=cagr,
            yoy_latest_pct=yoy,
            n_years=len(teu_year),
        ))
    return out


def rank_by_growth(
    trends: list[PortTEUTrend], *, top_n: Optional[int] = None,
    descending: bool = True,
) -> list[PortTEUTrend]:
    """Ports ranked by CAGR (only those with >= 2 data years; id tie-break)."""
    have = [t for t in trends if t.n_years >= 2]
    ranked = sorted(have, key=lambda t: (t.cagr_pct, t.locode), reverse=descending)
    return ranked[:top_n] if top_n is not None else ranked
