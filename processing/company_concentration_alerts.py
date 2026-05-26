"""processing/company_concentration_alerts.py — single-point-of-failure detector.

A ticker's port footprint can be diversified (low HHI — many ports,
no single one dominates) or concentrated (high HHI — one or two ports
carry most of the exposure). High concentration is a single-port
failure mode: when a hurricane closes Shanghai, a ticker with 80% of
its container flow through Shanghai has a much bigger problem than
one with 15%.

This module derives a per-ticker concentration risk from existing
``CompanyPortFootprint`` data, classifies it into bands, and emits
operator-facing alert records (``CompanyConcentrationAlert``) for
tickers that cross thresholds. The alert engine consumes these via
the same path the PORT_DEFICIT alerts use.

Concentration bands (lower-bound inclusive):
  0.00 → 0.25  →  "Diversified"
  0.25 → 0.45  →  "Moderate"
  0.45 → 0.65  →  "Concentrated"
  0.65 → 0.85  →  "Highly Concentrated"
  0.85 → 1.00  →  "Single-Port Risk"

Alerts fire at "Concentrated" (HHI >= 0.45) by default, with
configurable thresholds.

Pure function — no I/O. Tests inject synthetic footprints to verify
both the math and the band/severity mapping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


__all__ = [
    "CONCENTRATION_BANDS",
    "CompanyConcentrationAlert",
    "concentration_band",
    "compute_concentration_alerts",
]


# Lower-bound inclusive bands. Tuple of (lower_bound, band_label).
CONCENTRATION_BANDS: list[tuple[float, str]] = [
    (0.00, "Diversified"),
    (0.25, "Moderate"),
    (0.45, "Concentrated"),
    (0.65, "Highly Concentrated"),
    (0.85, "Single-Port Risk"),
]

# Default thresholds — wired into the alert engine. Operators can
# override via the alert rule template params.
DEFAULT_FIRE_THRESHOLD_HHI: float = 0.45
DEFAULT_CRITICAL_THRESHOLD_HHI: float = 0.85


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CompanyConcentrationAlert:
    """One per ticker that crossed the fire threshold.

    Carries enough information for the alert engine to format a
    meaningful body without re-querying — locodes + shares for the
    top-3 ports are baked in.
    """

    ticker: str
    hhi: float                                       # in [0, 1]
    concentration_band: str                          # see CONCENTRATION_BANDS
    severity: str                                    # "HIGH" or "CRITICAL"
    port_count: int                                  # # ports in the footprint
    top_ports: list[tuple[str, float]] = field(default_factory=list)
                                                    # (locode, share)
    summary: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def concentration_band(hhi: float) -> str:
    """Map an HHI value in [0, 1] to its operator-facing band label.

    Inputs outside the band domain degrade defensively to the nearest
    band (HHI < 0 → "Diversified"; HHI > 1 → "Single-Port Risk").
    """
    label = CONCENTRATION_BANDS[0][1]
    for lower, candidate in CONCENTRATION_BANDS:
        if hhi >= lower:
            label = candidate
        else:
            break
    return label


def _severity_for(hhi: float, critical_threshold: float) -> str:
    """Pick the alert severity for the given HHI.

    HHI at or above ``critical_threshold`` → CRITICAL; otherwise HIGH
    (the fire-threshold contract means only ≥ fire_threshold reaches
    this helper at all).
    """
    return "CRITICAL" if hhi >= critical_threshold else "HIGH"


def _hhi_from_shares(shares: list[float]) -> float:
    """Herfindahl-Hirschman Index of a list of shares already in [0, 1].

    Returns 0.0 when the list is empty. Does NOT re-normalize — caller
    has already ensured the shares sum to 1.0 (or close to it).
    """
    return sum(s * s for s in shares if s is not None)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_concentration_alerts(
    footprints: list,
    *,
    fire_threshold_hhi: float = DEFAULT_FIRE_THRESHOLD_HHI,
    critical_threshold_hhi: float = DEFAULT_CRITICAL_THRESHOLD_HHI,
    top_ports_in_body: int = 3,
) -> list[CompanyConcentrationAlert]:
    """Scan footprints + emit one alert per ticker that crosses threshold.

    ``footprints`` is a list of ``CompanyPortFootprint`` (from
    ``processing.port_supply_lines.build_company_port_footprints``).
    Each footprint must expose ``ticker``, ``port_count``, and a list
    of port-share tuples — see fixture in test_company_concentration_alerts.

    Parameters
    ----------
    fire_threshold_hhi:
        Minimum HHI to emit any alert. Defaults to 0.45 (start of the
        "Concentrated" band). Below this, the ticker is healthy
        enough not to spam operators.
    critical_threshold_hhi:
        At-or-above triggers CRITICAL severity. Defaults to 0.85
        (start of the "Single-Port Risk" band).
    top_ports_in_body:
        Cap on the (locode, share) tuples baked into each alert.
        Default 3 keeps the body scannable in a Slack DM.

    Returns
    -------
    list[CompanyConcentrationAlert]
        Sorted by HHI DESC so the worst single-point-of-failure
        candidate appears first. Empty when no ticker crosses
        ``fire_threshold_hhi``.

    Defensive against:
      * Empty input list
      * Footprints whose port_count == 0 (skipped)
      * Footprints missing the port-share list (treated as HHI=0,
        therefore skipped)
    """
    out: list[CompanyConcentrationAlert] = []
    for fp in footprints:
        ticker = str(getattr(fp, "ticker", "") or "")
        if not ticker:
            continue
        port_count = int(getattr(fp, "port_count", 0) or 0)
        if port_count <= 0:
            continue

        # ``ports`` is the per-port breakdown — each entry exposes
        # ``locode`` and ``share_within_company`` (or ``share`` for
        # legacy fixtures). Be tolerant of both names.
        ports = list(getattr(fp, "ports", []) or [])
        shares: list[float] = []
        port_share_pairs: list[tuple[str, float]] = []
        for p in ports:
            s = getattr(p, "share_within_company", None)
            if s is None:
                s = getattr(p, "share", None)
            if s is None:
                continue
            shares.append(float(s))
            port_share_pairs.append(
                (str(getattr(p, "locode", "") or ""), float(s)),
            )
        if not shares:
            continue

        hhi = _hhi_from_shares(shares)
        if hhi < fire_threshold_hhi:
            continue

        # Top-N ports for the body — sorted by share DESC. Bake the
        # whole list into the alert so the engine doesn't have to
        # re-query the footprint at render time.
        port_share_pairs.sort(key=lambda kv: kv[1], reverse=True)
        top_ports = port_share_pairs[:max(1, int(top_ports_in_body))]
        band = concentration_band(hhi)
        sev = _severity_for(hhi, critical_threshold_hhi)
        top_locodes_label = ", ".join(
            f"{locode} ({share * 100:.0f}%)" for locode, share in top_ports
        )
        summary = (
            f"{ticker} port footprint HHI={hhi:.2f} ({band}); "
            f"top: {top_locodes_label}"
        )
        out.append(CompanyConcentrationAlert(
            ticker=ticker,
            hhi=hhi,
            concentration_band=band,
            severity=sev,
            port_count=port_count,
            top_ports=top_ports,
            summary=summary,
        ))

    out.sort(key=lambda a: a.hhi, reverse=True)
    return out
