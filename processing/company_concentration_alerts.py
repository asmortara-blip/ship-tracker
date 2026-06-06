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

        # Two footprint shapes are accepted:
        #   * Live ``processing.port_supply_lines.CompanyPortFootprint`` —
        #     exposes ``port_exposures`` (each with ``port_locode`` +
        #     ``exposure_weight``). Weights must be renormalized to
        #     shares (they're absolute, not fractional).
        #   * Test fixture stub — exposes ``ports`` (each with ``locode`` +
        #     ``share_within_company`` already normalized).
        # Try live shape first, fall back to stub. Each per-port entry
        # is collected into (locode, raw_weight) then normalized.
        raw_pairs: list[tuple[str, float]] = []
        live_entries = list(getattr(fp, "port_exposures", []) or [])
        if live_entries:
            for p in live_entries:
                w = getattr(p, "exposure_weight", None)
                if w is None:
                    continue
                raw_pairs.append((
                    str(getattr(p, "port_locode", "") or ""),
                    float(w),
                ))
        else:
            stub_entries = list(getattr(fp, "ports", []) or [])
            for p in stub_entries:
                s = getattr(p, "share_within_company", None)
                if s is None:
                    s = getattr(p, "share", None)
                if s is None:
                    continue
                raw_pairs.append((
                    str(getattr(p, "locode", "") or ""),
                    float(s),
                ))

        if not raw_pairs:
            continue

        # Renormalize to shares so the HHI math holds regardless of
        # whether the input weights summed to 1.0 already (stub fixtures)
        # or to an arbitrary total (live exposure_weight values).
        total = sum(w for _loc, w in raw_pairs if w > 0)
        if total <= 0:
            continue
        port_share_pairs = [(loc, w / total) for loc, w in raw_pairs if w > 0]
        if not port_share_pairs:
            continue
        shares = [s for _loc, s in port_share_pairs]
        port_count = len(port_share_pairs)

        # Prefer the builder's HHI when present: the live footprint computes
        # it over the FULL set of ports the ticker touches, whereas
        # ``port_exposures`` here is capped to the top-N for display — squaring
        # only the capped shares overstates concentration (and couples it to
        # the unrelated render cap). Stub fixtures carry no precomputed value
        # (it defaults to 0.0, impossible for a real non-empty footprint), so
        # they fall back to the shares we just normalized.
        precomputed_hhi = float(getattr(fp, "concentration_hhi", 0.0) or 0.0)
        hhi = precomputed_hhi if precomputed_hhi > 0 else _hhi_from_shares(shares)
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


# ---------------------------------------------------------------------------
# Multi-axis concentration (rec R040) — route / chokepoint / commodity HHI.
#
# The port-footprint HHI above misses a real single-point-of-failure: a book
# can be diversified across PORTS yet 90% dependent on one CHOKEPOINT (Suez) or
# one COMMODITY. This computes HHI over three additional axes from the exposure
# matrix + chokepoint registry, so that concentration is caught wherever it
# actually lives. Same Herfindahl formula (``_hhi_from_shares``); the chokepoint
# share vector is left as fractions-OF-BOOK (it sums to <= 1, the
# chokepoint-exposed fraction), so a chokepoint-free book scores ~0 while a
# single-chokepoint book scores high — exactly the SPOF signal we want.
# ---------------------------------------------------------------------------

CONCENTRATION_AXES: tuple[str, ...] = ("commodity", "route", "chokepoint")


@dataclass
class AxisConcentrationAlert:
    """One per (ticker, axis) whose HHI crosses the fire threshold."""

    ticker: str
    axis: str                               # "commodity" | "route" | "chokepoint"
    hhi: float                              # in [0, 1]
    concentration_band: str
    severity: str                           # "HIGH" or "CRITICAL"
    n_buckets: int                          # # of distinct keys on this axis
    top_keys: list = field(default_factory=list)   # (key, share) desc
    summary: str = ""


def _normalize_shares(weights: dict) -> dict:
    """Rescale non-negative weights so they sum to 1.0 (empty/zero -> {})."""
    clean = {k: float(v) for k, v in (weights or {}).items() if float(v) > 0.0}
    total = sum(clean.values())
    if total <= 0.0:
        return {}
    return {k: v / total for k, v in clean.items()}


def _route_to_chokepoints() -> dict:
    """Invert the chokepoint registry: route_id -> [chokepoint keys it passes]."""
    out: dict[str, list] = {}
    try:
        from processing.chokepoint_analyzer import CHOKEPOINTS
    except Exception:  # pragma: no cover - defensive
        return out
    for key, cp in CHOKEPOINTS.items():
        for rid in getattr(cp, "affected_routes", []) or []:
            out.setdefault(str(rid), []).append(str(key))
    return out


def _route_shares(commodity_weights: dict) -> dict:
    """Distribute each commodity weight evenly across the routes that carry it.

    Returns route_id -> share (normalized to 1 over the routes touched).
    """
    from processing.exposure_matrix import routes_for_commodity
    route_share: dict[str, float] = {}
    for hs, w in (commodity_weights or {}).items():
        routes = routes_for_commodity(hs)
        if not routes:
            continue
        per = float(w) / len(routes)
        for rid in routes:
            route_share[str(rid)] = route_share.get(str(rid), 0.0) + per
    return _normalize_shares(route_share)


def chokepoint_shares(route_share: dict, *, route_to_cp: dict | None = None) -> dict:
    """Map per-route shares onto the chokepoints those routes pass through.

    A route through K chokepoints splits its share evenly across them; a route
    through NO chokepoint contributes nothing (so the result sums to the
    book's chokepoint-EXPOSED fraction, not 1). Left un-normalized on purpose:
    HHI over this vector is ~0 for a chokepoint-free book and high for a
    single-chokepoint book — the true single-point-of-failure signal.
    """
    r2c = _route_to_chokepoints() if route_to_cp is None else route_to_cp
    cp_share: dict[str, float] = {}
    for rid, w in (route_share or {}).items():
        cps = r2c.get(str(rid), [])
        if not cps:
            continue
        per = float(w) / len(cps)
        for cp in cps:
            cp_share[cp] = cp_share.get(cp, 0.0) + per
    return cp_share


def axis_shares(ticker: str, axis: str) -> dict:
    """The share vector for one concentration axis. Pure-ish (reads registries)."""
    from processing.exposure_matrix import company_commodity_weights
    comm = _normalize_shares(company_commodity_weights(ticker))
    if axis == "commodity":
        return comm
    route_share = _route_shares(comm)
    if axis == "route":
        return route_share
    if axis == "chokepoint":
        return chokepoint_shares(route_share)
    return {}


def compute_axis_concentration_alerts(
    tickers: list,
    *,
    axes: tuple = CONCENTRATION_AXES,
    fire_threshold_hhi: float = DEFAULT_FIRE_THRESHOLD_HHI,
    critical_threshold_hhi: float = DEFAULT_CRITICAL_THRESHOLD_HHI,
    top_in_body: int = 3,
) -> list:
    """Per (ticker, axis), emit an alert when the axis HHI crosses fire.

    Catches single-point-of-failure concentration the port-footprint HHI misses
    (one chokepoint / one commodity). Returns ``AxisConcentrationAlert`` sorted
    by HHI desc. Never raises — a bad ticker or registry hiccup is skipped.
    """
    out: list = []
    for ticker in tickers or []:
        t = str(ticker or "")
        if not t:
            continue
        for axis in axes:
            try:
                shares = axis_shares(t, axis)
            except Exception:  # pragma: no cover - defensive
                continue
            if not shares:
                continue
            hhi = _hhi_from_shares(list(shares.values()))
            if hhi < fire_threshold_hhi:
                continue
            top = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)[:top_in_body]
            top_str = ", ".join(f"{k} {s * 100:.0f}%" for k, s in top)
            sev = _severity_for(hhi, critical_threshold_hhi)
            out.append(AxisConcentrationAlert(
                ticker=t, axis=axis, hhi=round(hhi, 4),
                concentration_band=concentration_band(hhi), severity=sev,
                n_buckets=len(shares), top_keys=top,
                summary=(
                    f"{t}: {axis} concentration HHI {hhi:.2f} "
                    f"({concentration_band(hhi)}) — top: {top_str}. "
                    "Single-point-of-failure risk on this axis."
                ),
            ))
    out.sort(key=lambda a: a.hhi, reverse=True)
    return out
