"""company_supply_risk.py — daily ticker-level supply-line risk roll-up.

Single scalar per tracked shipping equity that answers: *how exposed is
this company today to port-side container-supply problems?* Built on top
of ``processing.port_supply_lines.build_port_supply_chains`` +
``build_company_port_footprints`` — every input is published, every
aggregate is pure arithmetic, no fitted model.

Three component terms blended into one 0-100 score:

  * ``weighted_deficit_days``       — Σ over ports of
                                       ``share_within_port × min(deficit, 0)``.
                                       Captures "how much of this ticker's port
                                       footprint sits in deficit, and how badly."
  * ``port_concentration_penalty``  — HHI of share_within_port across the
                                       company's ports. High = vulnerable to a
                                       single-port shock.
  * ``critical_port_count``         — count of linked ports whose
                                       ``severity_label`` starts with "Critical".

Final score: ``min(100, max(0, -weighted_deficit_days * 2.0 +
critical_port_count * 5.0 + port_concentration_penalty * 20.0))``.

Bands (the operator-facing label):

  * 0-20   Low
  * 20-40  Moderate
  * 40-60  Elevated
  * 60-80  High
  * 80+    Critical

Pure processing module — no Streamlit, no I/O. Defaults call the live
registry builders; tests + backtests can inject precomputed chains /
footprints to keep runs hermetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data.quality import DataSource


__all__ = [
    "CompanySupplyRiskScore",
    "RISK_BANDS",
    "compute_company_supply_risk",
    "COMPANY_SUPPLY_RISK_SOURCE",
]


# Bands the operator-facing label uses. Each entry: (lower_inclusive, label).
# Final band is open-ended at the top.
RISK_BANDS: tuple[tuple[float, str], ...] = (
    (0.0,  "Low"),
    (20.0, "Moderate"),
    (40.0, "Elevated"),
    (60.0, "High"),
    (80.0, "Critical"),
)


COMPANY_SUPPLY_RISK_SOURCE = DataSource.modeled(
    "Company Supply Risk",
    notes=(
        "Per-ticker supply-side risk roll-up. Blends share-within-port weighted "
        "deficit days + port-concentration HHI + critical-port count into a "
        "single 0-100 scalar per tracked equity, computed from the same "
        "port_supply_lines join the world map renders."
    ),
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CompanySupplyRiskScore:
    """One ticker's blended port-side supply risk for the trading day."""

    ticker: str
    total_risk_score: float                                  # 0-100 normalised
    port_count: int                                          # # ports in the footprint
    weighted_deficit_days: float                             # signed; ≤ 0
    top_problem_ports: list[tuple[str, float, float]] = field(default_factory=list)
                                                             # (locode, share, deficit)
    critical_port_count: int = 0                             # # ports in Critical Deficit
    risk_band: str = "Low"                                   # one of RISK_BANDS labels


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _risk_band(score: float) -> str:
    """Map a 0-100 score to its operator-facing band label.

    Lower-bound inclusive at each break. Negative inputs degrade to "Low";
    > 100 inputs degrade to "Critical". The blender already clips to
    ``[0, 100]`` so this is purely defensive.
    """
    label = RISK_BANDS[0][1]
    for lower, candidate in RISK_BANDS:
        if score >= lower:
            label = candidate
        else:
            break
    return label


def _hhi(weights: list[float]) -> float:
    """Herfindahl-Hirschman Index over a list of non-negative weights.

    Normalised so each weight is a share of the total, then HHI = Σ
    share². Range: ``1/n`` (perfectly diversified) → ``1.0`` (single-port
    concentration). Returns 0.0 for an empty list — vacuously
    undefined; 0.0 keeps the additive blend well-defined.
    """
    total = sum(weights)
    if total <= 0 or not weights:
        return 0.0
    shares = [w / total for w in weights]
    return sum(s * s for s in shares)


def _share_within_port_index(chains: list) -> dict[tuple[str, str], float]:
    """Build a {(locode, ticker): share_within_port} lookup from chains.

    ``share_within_port`` is the fraction of one port's total exposure
    weight that a given ticker holds. The CSV exporter computes the
    same thing inline — this just lifts it out so the risk roll-up can
    reuse exactly the same denominator the operator sees on the per-port
    drill-down.
    """
    index: dict[tuple[str, str], float] = {}
    for chain in chains:
        companies = list(chain.exposed_companies or [])
        total = sum(float(ce.exposure_weight) for ce in companies)
        if total <= 0:
            continue
        for ce in companies:
            share = float(ce.exposure_weight) / total
            index[(chain.port.locode, ce.ticker)] = share
    return index


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_company_supply_risk(
    *,
    container_type: str = "40FT_DRY",
    chains: list | None = None,
    footprints: list | None = None,
) -> list[CompanySupplyRiskScore]:
    """Return one ``CompanySupplyRiskScore`` per ticker with ≥ 1 port link.

    Parameters
    ----------
    container_type:
        Container-type slice passed through to the live builders. Ignored
        when ``chains`` / ``footprints`` are injected.
    chains:
        Optional precomputed ``list[PortExposureChain]``. Defaults to
        ``build_port_supply_chains(container_type=container_type,
        top_n_companies=999)`` so every company shows up in the per-port
        denominator (matches the footprint builder's convention).
    footprints:
        Optional precomputed ``list[CompanyPortFootprint]``. Defaults to
        ``build_company_port_footprints(container_type=container_type,
        top_n_ports=999)`` so every port in the footprint is scored.

    Returns
    -------
    list[CompanySupplyRiskScore]
        One entry per ticker. Sorted by ``total_risk_score`` desc — most
        at-risk first.
    """
    if chains is None:
        from processing.port_supply_lines import build_port_supply_chains
        chains = build_port_supply_chains(
            container_type=container_type,
            top_n_companies=999,
        )
    if footprints is None:
        from processing.port_supply_lines import build_company_port_footprints
        footprints = build_company_port_footprints(
            container_type=container_type,
            top_n_ports=999,
        )

    # Lift share_within_port out of the chains so the blend uses the same
    # denominator the per-port drill-down shows.
    share_index = _share_within_port_index(list(chains))

    scores: list[CompanySupplyRiskScore] = []
    for fp in footprints:
        exposures = list(fp.port_exposures or [])
        if not exposures:
            continue

        # share_within_port × min(deficit, 0) per port; sum yields a ≤ 0 number.
        weighted_deficit = 0.0
        # Per-port contribution magnitude for the top-3 list.
        per_port_contrib: list[tuple[str, float, float, float]] = []
        # Shares used for the HHI concentration penalty.
        shares: list[float] = []
        critical_count = 0
        for pe in exposures:
            share = float(share_index.get((pe.port_locode, fp.ticker), 0.0))
            deficit = float(pe.supply_deficit_days)
            neg_deficit = min(deficit, 0.0)
            contrib = share * neg_deficit
            weighted_deficit += contrib
            shares.append(share)
            if str(pe.severity_label).startswith("Critical"):
                critical_count += 1
            per_port_contrib.append((pe.port_locode, share, deficit, abs(contrib)))

        # Top 3 ports by absolute contribution to the weighted-deficit term —
        # answers "which ports are dragging this score?"
        per_port_contrib.sort(key=lambda t: t[3], reverse=True)
        top_problem = [
            (locode, round(share, 6), round(deficit, 4))
            for locode, share, deficit, _ in per_port_contrib[:3]
        ]

        # Concentration penalty: HHI across share_within_port; if shares are
        # all zero (degenerate registry state) the penalty collapses to 0.
        concentration = _hhi(shares)

        # Blend — pure arithmetic; clip only at the end so component
        # contributions stay transparent.
        raw_score = (
            -weighted_deficit * 2.0
            + critical_count * 5.0
            + concentration * 20.0
        )
        total = max(0.0, min(100.0, raw_score))

        scores.append(CompanySupplyRiskScore(
            ticker=fp.ticker,
            total_risk_score=round(total, 4),
            port_count=len(exposures),
            weighted_deficit_days=round(weighted_deficit, 6),
            top_problem_ports=top_problem,
            critical_port_count=critical_count,
            risk_band=_risk_band(total),
        ))

    scores.sort(key=lambda s: s.total_risk_score, reverse=True)
    return scores
