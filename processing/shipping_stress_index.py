"""Shipping Stress Index — the fleet-wide composite disruption read (SSI).

The platform already tracks chokepoints, port congestion, weather risk, freight
rates and route vulnerability — but those datasets are siloed. The **Shipping
Stress Index** fuses them into a single per-route ``stress_score`` and a
fleet-wide ``overall_ssi``, answering one question: *how stressed is the
container-shipping system right now, and which lanes are driving it?*

For every canonical route in ``routes.route_registry`` the SSI blends five
components:

* **chokepoint** — active maritime-chokepoint disruptions touching the lane
  (``processing.chokepoint_analyzer``);
* **congestion** — modeled congestion at the route's destination port
  (``processing.congestion_predictor``);
* **weather** — current weather-risk index for the lane
  (``processing.weather_risk``);
* **rate** — how far the lane's freight rate has run from its 30-day base
  (a rate spike *or* crash both register as stress);
* **vulnerability** — the route's structural fragility
  (``processing.vulnerability_scorer``).

The blend weights live in :data:`COMPONENT_WEIGHTS` and are asserted to sum to
1.0. The overall SSI averages the per-route scores with extra weight on the two
most prominent global lanes (``transpacific_eb``, ``asia_europe``).

This is a **pure processing module**: no Streamlit imports, no ``st.`` calls.
Every public function tolerates empty ``freight_data`` / ``port_results`` /
``route_results`` and returns neutral defaults rather than raising — the
codebase convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from processing.chokepoint_analyzer import (
    compute_chokepoint_risk_score,
    get_current_active_disruptions,
)
from processing.congestion_predictor import predict_congestion
from processing.vulnerability_scorer import score_vulnerability
from processing.weather_risk import compute_route_weather_risk
from routes.route_registry import ROUTES, ROUTES_BY_ID


# ---------------------------------------------------------------------------
# Component weights — the SSI blend. Asserted to sum to 1.0 at import time.
# ---------------------------------------------------------------------------

COMPONENT_WEIGHTS: dict[str, float] = {
    "chokepoint":    0.29,
    "congestion":    0.20,
    "weather":       0.16,
    "rate":          0.16,
    "vulnerability": 0.09,
    "anomaly":       0.10,
}
if abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) >= 1e-9:
    # ValueError instead of assert so the invariant still fires under ``python -O``.
    raise ValueError("SSI COMPONENT_WEIGHTS must sum to 1.0")

# Routes given extra weight in the fleet-wide SSI roll-up — the two highest-
# volume global container lanes. Their stress matters disproportionately.
_PROMINENT_ROUTES: dict[str, float] = {
    "transpacific_eb": 2.0,
    "asia_europe":     2.0,
}
_DEFAULT_ROUTE_WEIGHT = 1.0

# Human-readable label for each component key — used for dominant-driver text.
_DRIVER_LABELS: dict[str, str] = {
    "chokepoint":    "Chokepoint disruption",
    "congestion":    "Port congestion",
    "weather":       "Weather risk",
    "rate":          "Freight-rate dislocation",
    "vulnerability": "Structural vulnerability",
    "anomaly":       "Anomalous drift",
}

# SSI band thresholds — (upper-bound-exclusive, label, hex colour).
# A score below the bound takes that band. The final band is the catch-all.
_SSI_BANDS: list[tuple[float, str, str]] = [
    (0.25, "Calm",     "#2e9e6e"),
    (0.45, "Elevated", "#c9962b"),
    (0.65, "Stressed", "#f97316"),
    (1.01, "Severe",   "#c0392b"),
]

# Voyage statuses that count as a delay for delayed_voyage_count.
_DELAYED_STATUSES: frozenset[str] = frozenset({"Minor Delay", "Major Delay"})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RouteStress:
    """Per-route stress breakdown — one row of the SSI."""

    route_id: str
    route_name: str
    stress_score: float                       # [0, 1] composite
    chokepoint_stress: float                   # [0, 1] component
    congestion_stress: float                   # [0, 1] component
    weather_stress: float                      # [0, 1] component
    rate_stress: float                         # [0, 1] component
    vulnerability: float                       # [0, 1] component
    dominant_driver: str                       # human-readable top component
    affected_chokepoints: list[str] = field(default_factory=list)
    delayed_voyage_count: int = 0              # delayed voyages on this lane
    anomaly_stress: float = 0.0                # [0, 1] component — drift detector


@dataclass
class ShippingStressReport:
    """Fleet-wide Shipping Stress Index report."""

    overall_ssi: float                         # [0, 1] composite
    ssi_label: str                             # "Calm"|"Elevated"|"Stressed"|"Severe"
    ssi_color: str                             # hex colour string for the label
    route_stress: list[RouteStress] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)
    top_disruptions: list[str] = field(default_factory=list)
    wow_change: float = 0.0                    # week-over-week SSI change (placeholder)
    data_timestamp: str = ""                   # ISO 8601 UTC generation time


# ---------------------------------------------------------------------------
# Internal numeric helper
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into [lo, hi]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Component scorers — one private helper per SSI component
# ---------------------------------------------------------------------------

def _route_chokepoint_stress(route_id: str) -> tuple[float, list[str]]:
    """Chokepoint-disruption stress for *route_id*.

    Returns ``(stress, affected_chokepoints)`` where ``stress`` is in [0, 1]
    and ``affected_chokepoints`` lists the names of currently-disrupted
    chokepoints that touch this lane.

    A chokepoint's ``affected_routes`` may contain route IDs that are *not* in
    ``route_registry`` (e.g. ``persian_gulf_lng``). We only act on this lane
    when ``route_id`` is itself a registry route, so unknown IDs are silently
    ignored. The stress is the maximum chokepoint risk score across all
    disrupted chokepoints touching the lane (the worst chokepoint dominates),
    with a small additive bump for each extra disrupted chokepoint to reflect
    compounding exposure.
    """
    if route_id not in ROUTES_BY_ID:
        return 0.0, []

    try:
        risk_scores = compute_chokepoint_risk_score()
    except Exception:  # pragma: no cover - defensive
        logger.exception("compute_chokepoint_risk_score failed")
        return 0.0, []

    try:
        active = get_current_active_disruptions()
    except Exception:  # pragma: no cover - defensive
        logger.exception("get_current_active_disruptions failed")
        return 0.0, []

    touching: list[tuple[str, float]] = []
    for cp in active:
        # Intersect affected_routes against the registry — ignore unknown IDs.
        if route_id in cp.affected_routes and route_id in ROUTES_BY_ID:
            key = next(
                (k for k, v in _iter_chokepoint_items() if v is cp),
                None,
            )
            score = risk_scores.get(key, 0.0) if key else 0.0
            touching.append((cp.name, score))

    if not touching:
        return 0.0, []

    worst = max(score for _, score in touching)
    # Each additional disrupted chokepoint compounds exposure modestly.
    compounding = 0.08 * (len(touching) - 1)
    stress = _clamp(worst + compounding)
    names = sorted(name for name, _ in touching)
    return round(stress, 4), names


def _iter_chokepoint_items():
    """Yield ``(key, Chokepoint)`` pairs from the chokepoint registry.

    Isolated so :func:`_route_chokepoint_stress` can map an active-disruption
    object back to its registry key (the key is what
    ``compute_chokepoint_risk_score`` is keyed by).
    """
    from processing.chokepoint_analyzer import CHOKEPOINTS

    yield from CHOKEPOINTS.items()


def _route_weather_stress(route_id: str) -> float:
    """Weather-risk stress for *route_id* in [0, 1].

    Thin wrapper over ``weather_risk.compute_route_weather_risk`` — its
    ``current_risk_score`` is already a normalised [0, 1] index, so it maps
    straight onto SSI stress. Unknown routes / model failure -> 0.0.
    """
    try:
        index = compute_route_weather_risk(route_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("compute_route_weather_risk failed for {}", route_id)
        return 0.0
    return round(_clamp(getattr(index, "current_risk_score", 0.0)), 4)


def _compute_anomaly_component(
    route_id: str, freight_data: dict | None
) -> float:
    """Anomaly drift stress for *route_id* in [0, 1].

    Catches *drift* — a route slowly degrading without any single chokepoint /
    congestion / weather / rate component crossing its own threshold. We hand
    the route's rate series to :func:`engine.anomaly_detect.detect_anomaly`
    with default config and map the result onto the SSI's [0, 1] axis:

      * not detected (or too few samples)  -> 0.0
      * detected at MEDIUM severity        -> 0.40
      * detected at HIGH severity          -> 0.70
      * detected at CRITICAL severity      -> 1.00

    NEVER raises. Any failure inside the detector — bad config, empty series,
    pandas dependency missing — short-circuits to 0.0 so the SSI still
    composes cleanly. Lazy import of :mod:`engine.anomaly_detect` avoids the
    circular-import risk at module-import time.
    """
    if not freight_data:
        return 0.0
    try:
        entry = freight_data.get(route_id)
        if entry is None:
            return 0.0
        rates = _extract_rate_series(entry)
        if rates is None or len(rates) < 14:
            return 0.0

        # Lazy import — keeps shipping_stress_index importable without
        # engine.anomaly_detect being available.
        import pandas as pd
        from engine.anomaly_detect import AnomalyConfig, detect_anomaly

        series = pd.Series(rates)
        result = detect_anomaly(
            series,
            AnomalyConfig(
                metric_id=f"route_rate_{route_id}",
                lookback_days=30,
                z_threshold=2.5,
                method="zscore",
                min_samples=14,
            ),
        )
        if not getattr(result, "detected", False):
            return 0.0
        severity = (getattr(result, "severity", "") or "").upper()
        if severity == "CRITICAL":
            return 1.0
        if severity == "HIGH":
            return 0.70
        if severity == "MEDIUM":
            return 0.40
        return 0.0
    except Exception:  # pragma: no cover - defensive
        logger.debug("_compute_anomaly_component failed for {}", route_id)
        return 0.0


def _route_rate_stress(route_id: str, freight_data: dict | None) -> float:
    """Freight-rate dislocation stress for *route_id* in [0, 1].

    A lane is "stressed" on the rate axis when its freight rate has run far
    from its recent base in *either* direction — a spike signals scarcity, a
    crash signals demand collapse; both are disruption. The score is the
    absolute 30-day percentage move, normalised so a 40% move ~= full stress.

    ``freight_data`` is the platform-standard dict keyed by ``route_id``. Each
    value is normally a pandas DataFrame with a ``rate_usd_per_feu`` column,
    but plain numbers, sequences and ``{"rate": ...}`` dicts are also accepted.
    An absent route, empty data, or anything unparseable -> neutral 0.0.
    """
    if not freight_data:
        return 0.0
    entry = freight_data.get(route_id)
    if entry is None:
        return 0.0

    rates = _extract_rate_series(entry)
    if rates is None or len(rates) < 2:
        return 0.0

    current = rates[-1]
    past = rates[-31] if len(rates) >= 31 else rates[0]
    if past == 0:
        return 0.0

    pct_move = abs((current - past) / past)
    # Normalise: a 40% move (up or down) maps to full stress.
    return round(_clamp(pct_move / 0.40), 4)


def _extract_rate_series(entry) -> list[float] | None:
    """Coerce one ``freight_data`` value into a list of floats (chronological).

    Accepts the platform's varied freight shapes: a pandas DataFrame with a
    ``rate_usd_per_feu`` column (sorted by ``date`` when present), a Series,
    a list/tuple of numbers or ``{"rate": ...}``-style row dicts, or a plain
    scalar. Returns ``None`` when nothing numeric can be extracted.
    """
    # pandas DataFrame / Series — detected by attribute to avoid a hard import.
    if hasattr(entry, "empty") and hasattr(entry, "columns"):
        try:
            if entry.empty or "rate_usd_per_feu" not in entry.columns:
                return None
            df = entry.sort_values("date") if "date" in entry.columns else entry
            series = df["rate_usd_per_feu"].dropna()
            vals = [float(v) for v in series.tolist()]
            return vals or None
        except Exception:  # pragma: no cover - defensive
            return None
    if hasattr(entry, "dropna") and not hasattr(entry, "columns"):  # Series
        try:
            vals = [float(v) for v in entry.dropna().tolist()]
            return vals or None
        except Exception:  # pragma: no cover - defensive
            return None

    # Plain scalar — a single observation, no move computable.
    if isinstance(entry, (int, float)):
        return [float(entry)]

    # Sequence of numbers or row dicts.
    if isinstance(entry, (list, tuple)) and entry:
        out: list[float] = []
        for item in entry:
            if isinstance(item, (int, float)):
                out.append(float(item))
            elif isinstance(item, dict):
                for key in ("rate_usd_per_feu", "rate", "latest_rate", "value", "price"):
                    if key in item:
                        try:
                            out.append(float(item[key]))
                        except (TypeError, ValueError):
                            pass
                        break
        return out or None

    # Dict with a named rate key — a single observation.
    if isinstance(entry, dict):
        for key in ("rate_usd_per_feu", "rate", "latest_rate", "value", "price"):
            if key in entry:
                try:
                    return [float(entry[key])]
                except (TypeError, ValueError):
                    return None
    return None


def _route_congestion_stress(route_id: str, port_results: list | dict | None) -> float:
    """Destination-port congestion stress for *route_id* in [0, 1].

    Looks up the route's destination LOCODE and pulls the modeled congestion
    forecast for that port from ``congestion_predictor.predict_all_ports``. The
    forecast's ``predicted_7d`` is used (near-term outlook) and clamped to
    [0, 1]. When no port data is available, or the route / port is unknown,
    returns a neutral 0.5 — congestion is genuinely *unknown*, not *absent*, so
    a mid value is the honest default rather than 0.0.
    """
    route = ROUTES_BY_ID.get(route_id)
    if route is None:
        return 0.5
    if not port_results:
        return 0.5

    # Locate the destination-port result. port_results may be a list of
    # PortDemandResult objects (attr: ``locode`` / ``congestion_index``) or a
    # dict keyed by locode, or plain dicts — handle all shapes. We deliberately
    # do not use ``predict_all_ports`` here: it assumes a ``port_locode``
    # attribute that PortDemandResult does not expose.
    results = (
        list(port_results.values()) if isinstance(port_results, dict)
        else list(port_results)
    )
    dest_result = None
    for result in results:
        if isinstance(result, dict):
            loc = result.get("locode") or result.get("port_locode")
        else:
            loc = getattr(result, "locode", None) or getattr(result, "port_locode", None)
        if loc == route.dest_locode:
            dest_result = result
            break
    if dest_result is None:
        return 0.5

    # Current congestion at the destination port (modeled, [0, 1]).
    if isinstance(dest_result, dict):
        # Use `is not None` (not `or`) so a legitimate 0.0 reading — the
        # calmest, genuinely-uncongested port — is honored instead of being
        # treated as missing and falling through to the neutral 0.5 default
        # (which would inflate the congestion component + headline SSI). This
        # mirrors the object branch below.
        current = next(
            (dest_result[k] for k in
             ("current_congestion", "congestion_index", "congestion_component")
             if dest_result.get(k) is not None),
            None,
        )
    else:
        current = (
            getattr(dest_result, "current_congestion", None)
            if getattr(dest_result, "current_congestion", None) is not None
            else getattr(dest_result, "congestion_index", None)
        )
    if current is None:
        current = 0.5

    try:
        forecast = predict_congestion(route.dest_locode, _clamp(float(current)), {})
    except Exception:  # pragma: no cover - defensive
        logger.exception("predict_congestion failed")
        return 0.5

    near_term = getattr(forecast, "predicted_7d", None)
    if near_term is None:
        near_term = getattr(forecast, "current_congestion", 0.5)
    return round(_clamp(float(near_term)), 4)


# ---------------------------------------------------------------------------
# SSI band classification
# ---------------------------------------------------------------------------

def _classify_ssi(score: float) -> tuple[str, str]:
    """Map an SSI score in [0, 1] to its ``(label, hex_color)`` band."""
    for bound, label, color in _SSI_BANDS:
        if score < bound:
            return label, color
    # Catch-all — the final band already covers up to 1.01.
    label, color = _SSI_BANDS[-1][1], _SSI_BANDS[-1][2]
    return label, color


# ---------------------------------------------------------------------------
# Voyage delay counting
# ---------------------------------------------------------------------------

def _delayed_counts_by_route(voyage_fleet) -> dict[str, int]:
    """Count delayed voyages per route from an optional voyage fleet.

    A voyage counts as delayed when its ``status`` is "Minor Delay" or
    "Major Delay" (see ``data.voyage_dataset.Voyage``). When ``voyage_fleet``
    is ``None`` or empty, an empty mapping is returned and every route reports
    a delayed-voyage count of 0.
    """
    counts: dict[str, int] = {}
    if not voyage_fleet:
        return counts
    for voyage in voyage_fleet:
        status = getattr(voyage, "status", "")
        if status in _DELAYED_STATUSES:
            route_id = getattr(voyage, "route_id", "")
            if route_id:
                counts[route_id] = counts.get(route_id, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# top_disruptions — drawn from the alert engine
# ---------------------------------------------------------------------------

def _build_top_disruptions(
    freight_data: dict | None,
    macro_data: dict | None,
    port_results: list | dict | None,
    route_results: list | dict | None,
    route_stress: list[RouteStress],
) -> list[str]:
    """Assemble the headline ``top_disruptions`` list.

    Primary source is ``engine.alert_engine.generate_alerts`` — CRITICAL and
    WARNING alerts are surfaced first, most-severe first. As a fallback (or
    supplement) the worst-stressed routes contribute a one-line disruption
    note each, so the list is never empty when stress genuinely exists.
    """
    disruptions: list[str] = []

    try:
        from engine.alert_engine import generate_alerts

        alerts = generate_alerts(
            port_results or [],
            route_results or [],
            freight_data or {},
            macro_data or {},
            [],
        )
        severity_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        alerts_sorted = sorted(
            alerts, key=lambda a: severity_rank.get(getattr(a, "severity", "INFO"), 3)
        )
        for alert in alerts_sorted:
            if getattr(alert, "severity", "INFO") in ("CRITICAL", "WARNING"):
                title = getattr(alert, "title", "")
                if title and title not in disruptions:
                    disruptions.append(title)
    except Exception:  # pragma: no cover - defensive
        logger.exception("alert_engine.generate_alerts failed for top_disruptions")

    # Supplement with the worst-stressed lanes so the list reflects the SSI
    # itself even when no rate/congestion alert thresholds were tripped.
    for rs in route_stress:
        if len(disruptions) >= 6:
            break
        if rs.stress_score >= 0.45:
            note = f"{rs.route_name}: {rs.dominant_driver.lower()} stress elevated"
            if note not in disruptions:
                disruptions.append(note)

    return disruptions[:6]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_shipping_stress(
    freight_data: dict,
    macro_data: dict,
    port_results: list,
    route_results: list,
    voyage_fleet=None,
) -> ShippingStressReport:
    """Compute the fleet-wide Shipping Stress Index.

    For every canonical route a :class:`RouteStress` is built by blending five
    components — chokepoint, congestion, weather, rate, vulnerability — using
    :data:`COMPONENT_WEIGHTS`. The overall SSI averages the per-route scores
    with extra weight on the prominent ``transpacific_eb`` and ``asia_europe``
    lanes (see :data:`_PROMINENT_ROUTES`).

    Parameters
    ----------
    freight_data:
        Platform-standard dict keyed by ``route_id`` -> rate data (normally a
        pandas DataFrame with a ``rate_usd_per_feu`` column). May be empty.
    macro_data:
        Macro-indicator dict. Forwarded to the alert engine for
        ``top_disruptions``. May be empty.
    port_results:
        Port analysis results — list of objects (or dict keyed by LOCODE) each
        exposing a congestion-like metric. May be empty.
    route_results:
        Route analysis results (e.g. ``RouteOpportunity`` objects). Forwarded
        to the alert engine. May be empty.
    voyage_fleet:
        Optional list of ``Voyage`` objects (``data.voyage_dataset``). When
        supplied, each :class:`RouteStress` reports how many of its voyages are
        delayed. When ``None``, every ``delayed_voyage_count`` is 0.

    Returns
    -------
    ShippingStressReport
        Always a valid report — empty inputs yield neutral defaults, never a
        crash or exception.
    """
    delayed_by_route = _delayed_counts_by_route(voyage_fleet)

    route_stress: list[RouteStress] = []
    # Running prominence-WEIGHTED per-component sums — each route's component
    # value times its route_weight — so the fleet-wide component_scores
    # decompose the prominence-weighted overall_ssi exactly
    # (Σ_k COMPONENT_WEIGHTS[k] · component_scores[k] == overall_ssi).
    component_totals: dict[str, float] = {k: 0.0 for k in COMPONENT_WEIGHTS}

    weighted_stress_sum = 0.0
    weight_sum = 0.0

    for route in ROUTES:
        route_id = route.id

        chokepoint_stress, affected_chokepoints = _route_chokepoint_stress(route_id)
        congestion_stress = _route_congestion_stress(route_id, port_results)
        weather_stress = _route_weather_stress(route_id)
        rate_stress = _route_rate_stress(route_id, freight_data)

        try:
            vuln_record = score_vulnerability(route_id, route.name)
            vulnerability = _clamp(
                float(getattr(vuln_record, "overall_vulnerability", 0.0))
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("score_vulnerability failed for {}", route_id)
            vulnerability = 0.0

        anomaly_stress = _compute_anomaly_component(route_id, freight_data)

        components = {
            "chokepoint":    chokepoint_stress,
            "congestion":    congestion_stress,
            "weather":       weather_stress,
            "rate":          rate_stress,
            "vulnerability": vulnerability,
            "anomaly":       anomaly_stress,
        }

        # Weighted blend -> per-route composite stress score.
        stress_score = _clamp(
            sum(components[k] * COMPONENT_WEIGHTS[k] for k in COMPONENT_WEIGHTS)
        )

        # Dominant driver: the component contributing the most weighted stress.
        dominant_key = max(
            COMPONENT_WEIGHTS,
            key=lambda k: components[k] * COMPONENT_WEIGHTS[k],
        )
        dominant_driver = _DRIVER_LABELS[dominant_key]

        route_stress.append(
            RouteStress(
                route_id=route_id,
                route_name=route.name,
                stress_score=round(stress_score, 4),
                chokepoint_stress=round(chokepoint_stress, 4),
                congestion_stress=round(congestion_stress, 4),
                weather_stress=round(weather_stress, 4),
                rate_stress=round(rate_stress, 4),
                vulnerability=round(vulnerability, 4),
                dominant_driver=dominant_driver,
                affected_chokepoints=affected_chokepoints,
                delayed_voyage_count=delayed_by_route.get(route_id, 0),
                anomaly_stress=round(anomaly_stress, 4),
            )
        )

        # Prominence-weight BOTH the overall SSI and the per-component
        # breakdown by the SAME route_weight, so the component_scores below
        # decompose the displayed overall_ssi rather than a separate equal-
        # weighted average that wouldn't reconcile.
        route_weight = _PROMINENT_ROUTES.get(route_id, _DEFAULT_ROUTE_WEIGHT)
        for key, value in components.items():
            component_totals[key] += value * route_weight
        weighted_stress_sum += stress_score * route_weight
        weight_sum += route_weight

    # Fleet-wide SSI — prominence-weighted average of per-route stress.
    overall_ssi = round(_clamp(weighted_stress_sum / weight_sum), 4) if weight_sum else 0.0

    n_routes = len(route_stress)
    # Prominence-weighted component averages (same weights as overall_ssi), so
    # Σ_k COMPONENT_WEIGHTS[k] · component_scores[k] reconciles to overall_ssi.
    component_scores = {
        key: round(total / weight_sum, 4) if weight_sum else 0.0
        for key, total in component_totals.items()
    }

    ssi_label, ssi_color = _classify_ssi(overall_ssi)

    # Sort routes worst-first so consumers (and top_disruptions) see the
    # highest-stress lanes at the head of the list.
    route_stress.sort(key=lambda rs: rs.stress_score, reverse=True)

    top_disruptions = _build_top_disruptions(
        freight_data, macro_data, port_results, route_results, route_stress
    )

    logger.debug(
        "compute_shipping_stress: SSI={:.3f} ({}), {} routes, {} disruptions",
        overall_ssi, ssi_label, n_routes, len(top_disruptions),
    )

    return ShippingStressReport(
        overall_ssi=overall_ssi,
        ssi_label=ssi_label,
        ssi_color=ssi_color,
        route_stress=route_stress,
        component_scores=component_scores,
        top_disruptions=top_disruptions,
        wow_change=0.0,
        data_timestamp=datetime.now(timezone.utc).isoformat(),
    )
