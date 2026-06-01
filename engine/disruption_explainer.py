"""Disruption explainer — template-based, one-paragraph English rationales.

The platform already computes :class:`processing.shipping_stress_index.RouteStress`
(six component scores + a dominant-driver label) and
:class:`data.voyage_dataset.Voyage` (delay attribution). What the UI lacks is
the *English* — a one-paragraph "why this route is stressed" or "why this
voyage is late" that an operator can read in under five seconds.

This module is **template-based**, not LLM-driven, by design:

* **Deterministic.** Same inputs → same output. The UI can render explanations
  during a normal Streamlit reload with no network call.
* **Auditable.** Every phrase comes from a module-level constant
  (:data:`_ROUTE_HEADLINE_TEMPLATES`, :data:`_COMPONENT_PHRASES`,
  :data:`_VOYAGE_PHRASES`); to re-translate the platform an operator edits
  strings, not logic.
* **Cheap.** A 5-route Disruption Radar page that prints five explanations
  costs zero LLM tokens.

Every public function NEVER raises — bad input degrades to a safe default
rather than propagating an exception into the UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RouteExplanation:
    """One paragraph of English-rationale prose about a single route."""

    route_id: str
    route_name: str
    severity_band: str                      # "Calm" | "Elevated" | "Stressed" | "Severe"
    stress_score: float
    headline: str
    why: list[str] = field(default_factory=list)   # 2-4 bullets
    affected_chokepoints: list[str] = field(default_factory=list)
    delayed_voyages: int = 0
    recommended_focus: str = ""             # "monitor"|"investigate"|"escalate"|""


@dataclass
class VoyageExplanation:
    """One paragraph of English-rationale prose about a single voyage."""

    voyage_id: str
    vessel_name: str
    route_id: str
    status: str
    delay_days: float
    headline: str
    why: list[str] = field(default_factory=list)
    primary_cause: str = "unknown"          # 'weather'|'congestion'|'chokepoint'|'unknown'|'none'


# ---------------------------------------------------------------------------
# Template constants — module-level so operators can re-translate by editing
# strings, not by editing logic. Each headline template takes a fixed set of
# named placeholders so the .format() call cannot silently drop a substitution.
# ---------------------------------------------------------------------------

_ROUTE_HEADLINE_TEMPLATES: dict[str, str] = {
    "Severe":   "{name} is severely disrupted — {top} crisis driving stress to {score:.2f}.",
    "Stressed": "{name} is under stress (score {score:.2f}); {top} is the dominant driver.",
    "Elevated": "{name} shows elevated stress (score {score:.2f}) — early warning on {top}.",
    "Calm":     "{name} is operating normally (score {score:.2f}).",
}

# Per-component bullet phrases. Each takes the same keyword bag so the
# format() call is uniform; missing keys default through .format_map / dict.
_COMPONENT_PHRASES: dict[str, str] = {
    "chokepoint":    "active chokepoint disruption ({affected})",
    "congestion":    "high port congestion ({delayed_voyages} voyages affected)",
    "weather":       "weather delays in the route corridor",
    "rate":          "rate volatility relative to the historical baseline",
    "vulnerability": "structural vulnerability (single-route dependency)",
    "anomaly":       "anomalous drift in route inputs",
}

# Human-readable single-word labels per component, used inside headlines.
_COMPONENT_LABELS: dict[str, str] = {
    "chokepoint":    "chokepoint disruption",
    "congestion":    "port congestion",
    "weather":       "weather",
    "rate":          "freight-rate dislocation",
    "vulnerability": "structural vulnerability",
    "anomaly":       "anomalous drift",
}

# Voyage-level phrases keyed by the primary cause we attribute to the delay.
_VOYAGE_HEADLINE_TEMPLATES: dict[str, str] = {
    "weather":    "{vessel} ({voyage_id}) is delayed {delay:.1f} days — weather is the primary driver.",
    "congestion": "{vessel} ({voyage_id}) is delayed {delay:.1f} days — destination-port congestion is the primary driver.",
    "chokepoint": "{vessel} ({voyage_id}) is delayed {delay:.1f} days — chokepoint disruption on the route is the primary driver.",
    "unknown":    "{vessel} ({voyage_id}) is delayed {delay:.1f} days — cause is not clearly attributable to any single factor.",
    "none":       "{vessel} ({voyage_id}) is running on schedule.",
}

_VOYAGE_PHRASES: dict[str, str] = {
    "weather":    "Weather-attributed delay: {weather:.1f} days of the total.",
    "congestion": "Destination port congestion sits at {congestion:.2f} on the 0-1 scale.",
    "chokepoint": "Route currently transits an active chokepoint: {chokepoints}.",
    "delay":      "Total delay so far: {delay:.1f} days vs nominal transit.",
}

# Bullet-count cap so a route explanation stays readable.
_MAX_ROUTE_BULLETS = 4
_MIN_ROUTE_BULLETS = 2

# Recommended-focus thresholds — pinned in one place so the UI can document
# them in tooltips without re-deriving from the component weights.
_FOCUS_ESCALATE = 0.7
_FOCUS_INVESTIGATE = 0.5
_FOCUS_MONITOR = 0.3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(x, default: float = 0.0) -> float:
    """Coerce ``x`` to float; return ``default`` on failure. NEVER raises."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _safe_int(x, default: int = 0) -> int:
    """Coerce ``x`` to int; return ``default`` on failure. NEVER raises."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _route_components(stress) -> dict[str, float]:
    """Pull the six component values off a RouteStress (or compatible object).

    Missing attributes are treated as 0.0. Returns a fresh dict so the caller
    can sort / mutate it without affecting the original.
    """
    return {
        "chokepoint":    _safe_float(getattr(stress, "chokepoint_stress", 0.0)),
        "congestion":    _safe_float(getattr(stress, "congestion_stress", 0.0)),
        "weather":       _safe_float(getattr(stress, "weather_stress", 0.0)),
        "rate":          _safe_float(getattr(stress, "rate_stress", 0.0)),
        "vulnerability": _safe_float(getattr(stress, "vulnerability", 0.0)),
        "anomaly":       _safe_float(getattr(stress, "anomaly_stress", 0.0)),
    }


def _recommended_focus(score: float) -> str:
    """Map a stress score to a focus recommendation.

    >0.7 -> 'escalate'   0.5-0.7 -> 'investigate'
    0.3-0.5 -> 'monitor' <0.3 -> '' (Calm — no action)
    """
    if score > _FOCUS_ESCALATE:
        return "escalate"
    if score > _FOCUS_INVESTIGATE:
        return "investigate"
    if score > _FOCUS_MONITOR:
        return "monitor"
    return ""


def _format_component_bullet(key: str, ctx: dict) -> str:
    """Render the component bullet for *key* with the route context substituted.

    Falls back gracefully when ctx is missing fields the phrase template
    references — the platform's UI may render an explanation for a route with
    no affected chokepoints or no delayed voyages.
    """
    template = _COMPONENT_PHRASES.get(key, "{key} contribution")
    # Build a defaulting dict so .format_map cannot raise KeyError.
    payload = {
        "key":              key,
        "affected":         ", ".join(ctx.get("affected_chokepoints", []) or []) or "active",
        "delayed_voyages":  _safe_int(ctx.get("delayed_voyages", 0)),
        "score":            _safe_float(ctx.get("stress_score", 0.0)),
    }
    try:
        return template.format_map(payload)
    except Exception:  # pragma: no cover - defensive
        return template


# ---------------------------------------------------------------------------
# Public API — explain_route
# ---------------------------------------------------------------------------

def explain_route(stress) -> RouteExplanation:
    """Produce a one-paragraph English explanation for a single RouteStress.

    Pulls the dominant component from the per-component breakdown (value *
    weight, mirroring the SSI's own dominant_driver) and uses it as the
    headline subject. The ``why`` bullets enumerate every component above a
    small contribution threshold, capped at :data:`_MAX_ROUTE_BULLETS`.

    NEVER raises. ``stress=None`` or a malformed object returns a valid
    "unknown route" explanation.
    """
    if stress is None:
        return RouteExplanation(
            route_id="",
            route_name="",
            severity_band="Calm",
            stress_score=0.0,
            headline="No route data supplied.",
            why=[],
            affected_chokepoints=[],
            delayed_voyages=0,
            recommended_focus="",
        )

    try:
        route_id = str(getattr(stress, "route_id", "") or "")
        route_name = str(getattr(stress, "route_name", route_id) or route_id)
        score = _safe_float(getattr(stress, "stress_score", 0.0))
        affected = list(getattr(stress, "affected_chokepoints", []) or [])
        delayed = _safe_int(getattr(stress, "delayed_voyage_count", 0))

        components = _route_components(stress)

        # Sort components by raw value; ties broken by SSI-style weighting
        # would require importing the weights dict — keep it simple and use
        # raw value for ordering.
        sorted_components = sorted(
            components.items(), key=lambda kv: kv[1], reverse=True
        )

        # Determine band — same thresholds as shipping_stress_index._classify_ssi.
        if score < 0.25:
            band = "Calm"
        elif score < 0.45:
            band = "Elevated"
        elif score < 0.65:
            band = "Stressed"
        else:
            band = "Severe"

        # Headline — substitute the dominant component label into the template.
        # On Calm routes the top component is largely cosmetic; the Calm
        # template intentionally omits {top}.
        top_key = sorted_components[0][0] if sorted_components else "chokepoint"
        top_label = _COMPONENT_LABELS.get(top_key, top_key)
        template = _ROUTE_HEADLINE_TEMPLATES.get(band, _ROUTE_HEADLINE_TEMPLATES["Calm"])
        try:
            headline = template.format(name=route_name, top=top_label, score=score)
        except Exception:  # pragma: no cover - defensive
            headline = f"{route_name} stress {score:.2f}"

        # Bullets — every component with a contribution above the floor, in
        # descending order. The Calm route still gets at least the top bullet
        # so the UI never renders an empty bullet list.
        bullet_ctx = {
            "affected_chokepoints": affected,
            "delayed_voyages":      delayed,
            "stress_score":         score,
        }
        threshold = 0.10
        bullets: list[str] = []
        for key, value in sorted_components:
            if value < threshold and len(bullets) >= _MIN_ROUTE_BULLETS:
                break
            bullets.append(_format_component_bullet(key, bullet_ctx))
            if len(bullets) >= _MAX_ROUTE_BULLETS:
                break

        # Calm route with all-zero components — guarantee a minimum bullet
        # count so the UI shape stays consistent.
        while len(bullets) < _MIN_ROUTE_BULLETS and sorted_components:
            for key, _value in sorted_components:
                phrase = _format_component_bullet(key, bullet_ctx)
                if phrase not in bullets:
                    bullets.append(phrase)
                if len(bullets) >= _MIN_ROUTE_BULLETS:
                    break
            break  # safety net — sorted_components is bounded

        focus = _recommended_focus(score)

        return RouteExplanation(
            route_id=route_id,
            route_name=route_name,
            severity_band=band,
            stress_score=round(score, 4),
            headline=headline,
            why=bullets,
            affected_chokepoints=affected,
            delayed_voyages=delayed,
            recommended_focus=focus,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("explain_route failed")
        return RouteExplanation(
            route_id=str(getattr(stress, "route_id", "") or ""),
            route_name=str(getattr(stress, "route_name", "") or ""),
            severity_band="Calm",
            stress_score=0.0,
            headline="Explanation unavailable — input could not be parsed.",
            why=[],
            affected_chokepoints=[],
            delayed_voyages=0,
            recommended_focus="",
        )


# ---------------------------------------------------------------------------
# Public API — explain_voyage
# ---------------------------------------------------------------------------

def _voyage_primary_cause(voyage) -> str:
    """Pick the primary cause of *voyage*'s delay.

    Order of attribution:
      * voyage on schedule (delay <= 1 day) -> 'none'
      * has weather_delay_days > 0           -> 'weather'
      * congestion_at_dest > 0.7             -> 'congestion'
      * has chokepoints_on_route             -> 'chokepoint'
      * otherwise                            -> 'unknown'

    The thresholds are deliberately conservative — a voyage with a small
    weather component but a major chokepoint diversion will still be flagged
    as 'weather' because that's what the per-voyage attribution field carries.
    Operators looking deeper should consult the route-level explanation.
    """
    delay = _safe_float(getattr(voyage, "delay_days", 0.0))
    if delay <= 1.0 and (getattr(voyage, "status", "") or "") in ("On Schedule", "Arrived"):
        return "none"

    weather_delay = _safe_float(getattr(voyage, "weather_delay_days", 0.0))
    if weather_delay > 0.0:
        return "weather"

    congestion = _safe_float(getattr(voyage, "congestion_at_dest", 0.0))
    if congestion > 0.7:
        return "congestion"

    chokepoints = list(getattr(voyage, "chokepoints_on_route", []) or [])
    if chokepoints:
        return "chokepoint"

    return "unknown"


def explain_voyage(voyage) -> VoyageExplanation:
    """Produce a one-paragraph English explanation for a single Voyage.

    Picks a primary cause via :func:`_voyage_primary_cause` and substitutes
    the matching headline template. The ``why`` bullets surface the numeric
    detail behind the headline (weather days, congestion fraction, the
    specific chokepoint touched, total delay) so the operator can verify the
    attribution without leaving the row.

    NEVER raises.
    """
    if voyage is None:
        return VoyageExplanation(
            voyage_id="",
            vessel_name="",
            route_id="",
            status="",
            delay_days=0.0,
            headline="No voyage data supplied.",
            why=[],
            primary_cause="unknown",
        )

    try:
        voyage_id = str(getattr(voyage, "voyage_id", "") or "")
        vessel_name = str(getattr(voyage, "vessel_name", "") or "")
        route_id = str(getattr(voyage, "route_id", "") or "")
        status = str(getattr(voyage, "status", "") or "")
        delay = _safe_float(getattr(voyage, "delay_days", 0.0))
        weather_delay = _safe_float(getattr(voyage, "weather_delay_days", 0.0))
        congestion = _safe_float(getattr(voyage, "congestion_at_dest", 0.0))
        chokepoints = list(getattr(voyage, "chokepoints_on_route", []) or [])

        primary = _voyage_primary_cause(voyage)
        template = _VOYAGE_HEADLINE_TEMPLATES.get(primary, _VOYAGE_HEADLINE_TEMPLATES["unknown"])

        try:
            headline = template.format(
                vessel=vessel_name or "Vessel",
                voyage_id=voyage_id or "?",
                delay=max(0.0, delay),
            )
        except Exception:  # pragma: no cover - defensive
            headline = f"{vessel_name} ({voyage_id}) — explanation unavailable."

        bullets: list[str] = []
        if primary == "none":
            # On schedule — single bullet noting the on-time status.
            bullets.append("Voyage is on schedule; no material delay reported.")
        else:
            # Always lead with the total-delay bullet — anchors the magnitude.
            if delay > 0.0:
                bullets.append(_VOYAGE_PHRASES["delay"].format(delay=delay))
            if primary == "weather" and weather_delay > 0.0:
                bullets.append(_VOYAGE_PHRASES["weather"].format(weather=weather_delay))
            if primary == "congestion":
                bullets.append(_VOYAGE_PHRASES["congestion"].format(congestion=congestion))
            if primary == "chokepoint" and chokepoints:
                bullets.append(
                    _VOYAGE_PHRASES["chokepoint"].format(
                        chokepoints=", ".join(chokepoints)
                    )
                )
            # If we still have only the delay bullet, fold in whichever
            # secondary signal exists so the bullet list isn't a single item.
            if len(bullets) < 2:
                if weather_delay > 0.0 and primary != "weather":
                    bullets.append(_VOYAGE_PHRASES["weather"].format(weather=weather_delay))
                elif congestion > 0.5 and primary != "congestion":
                    bullets.append(_VOYAGE_PHRASES["congestion"].format(congestion=congestion))
                elif chokepoints and primary != "chokepoint":
                    bullets.append(
                        _VOYAGE_PHRASES["chokepoint"].format(
                            chokepoints=", ".join(chokepoints)
                        )
                    )

        return VoyageExplanation(
            voyage_id=voyage_id,
            vessel_name=vessel_name,
            route_id=route_id,
            status=status,
            delay_days=round(delay, 2),
            headline=headline,
            why=bullets,
            primary_cause=primary,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("explain_voyage failed")
        return VoyageExplanation(
            voyage_id=str(getattr(voyage, "voyage_id", "") or ""),
            vessel_name=str(getattr(voyage, "vessel_name", "") or ""),
            route_id=str(getattr(voyage, "route_id", "") or ""),
            status=str(getattr(voyage, "status", "") or ""),
            delay_days=0.0,
            headline="Explanation unavailable — input could not be parsed.",
            why=[],
            primary_cause="unknown",
        )


# ---------------------------------------------------------------------------
# Top-N convenience wrappers
# ---------------------------------------------------------------------------

def explain_top_disruptions(stresses, *, top_n: int = 5) -> list[RouteExplanation]:
    """Explain the top-N most-stressed routes from a RouteStress collection.

    Filters out Calm routes (they aren't disruptions), sorts the remainder by
    stress_score descending, and caps at ``top_n``. ``stresses=None`` or an
    empty input yields an empty list — never raises.
    """
    if not stresses:
        return []
    try:
        # Filter Calm routes (score < 0.25), then sort & cap.
        candidates = [
            s for s in stresses
            if _safe_float(getattr(s, "stress_score", 0.0)) >= 0.25
        ]
        candidates.sort(
            key=lambda s: _safe_float(getattr(s, "stress_score", 0.0)),
            reverse=True,
        )
        n = max(0, _safe_int(top_n, default=5))
        return [explain_route(s) for s in candidates[:n]]
    except Exception:  # pragma: no cover - defensive
        logger.exception("explain_top_disruptions failed")
        return []


def explain_delayed_voyages(voyages, *, top_n: int = 10) -> list[VoyageExplanation]:
    """Explain the top-N most-delayed voyages from a voyage list.

    Filters voyages with delay_days > 1.0 (matches the SSI's _DELAYED_STATUSES
    convention — Minor Delay starts above 1 day) and sorts by delay_days
    descending. ``voyages=None`` -> empty list. NEVER raises.
    """
    if not voyages:
        return []
    try:
        candidates = [
            v for v in voyages
            if _safe_float(getattr(v, "delay_days", 0.0)) > 1.0
        ]
        candidates.sort(
            key=lambda v: _safe_float(getattr(v, "delay_days", 0.0)),
            reverse=True,
        )
        n = max(0, _safe_int(top_n, default=10))
        return [explain_voyage(v) for v in candidates[:n]]
    except Exception:  # pragma: no cover - defensive
        logger.exception("explain_delayed_voyages failed")
        return []
