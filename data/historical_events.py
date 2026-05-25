"""Historical shipping-disruption events — the SSI's empirical ground truth.

The Shipping Stress Index (:mod:`processing.shipping_stress_index`) claims to
"detect disruption". For that claim to be operator-actionable it needs to be
measured against real-world events the platform's users already remember:
*the Suez blockage*, *the Panama drought*, *the Red Sea attacks*. This module
is the registry of those events — one row per named, dated, route-localised
disruption — so :mod:`processing.disruption_backtest` can replay each one and
score the SSI's response.

The list is deliberately small and conservative: every entry is an event the
container-shipping press covered in real time and that left a clear footprint
in the chokepoint / port / weather / rate signals the SSI consumes. Speculative
events are out — a backtester is only as honest as its ground truth, and a
fabricated event would silently inflate the hit rate.

This is a **pure data module**: no I/O, no Streamlit imports, no platform
dependencies. The :class:`HistoricalEvent` dataclass is frozen and hashable so
events can be used as dict keys / set members in test fixtures.

Route IDs cited in ``affected_routes`` SHOULD match
``routes.route_registry.ROUTES_BY_ID`` — when they do, the backtester can map
the event's disruption straight onto the SSI's per-route output. A few legacy
route names that operators use colloquially (``panama_eb``, ``la_long_beach``)
are tolerated as labels but will not score per-route stress because they are
not in the registry; those events still drive their affected chokepoints, so
the SSI's chokepoint component still fires. See
:func:`processing.disruption_backtest.synthesize_event_inputs` for the mapping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HistoricalEvent:
    """One named, dated, route-localised shipping disruption event.

    Every field is grounded in a real-world, well-documented occurrence; no
    speculative entries. Fields:

    event_id
        Stable short identifier (``suez_2021``, ``panama_drought_2023``).
        Tests use this as a dict key, so it must never change once shipped.
    name
        Human-readable headline for UI / CLI output.
    start_date, end_date
        ISO ``YYYY-MM-DD``. ``end_date`` may be the literal string
        ``"ongoing"`` for events still active at the platform's "today".
    severity
        One of ``"severe"`` / ``"major"`` / ``"moderate"`` — a coarse cue
        for the backtest's expected-band check.
    affected_routes
        Route IDs the event materially disrupted. Strings, may include
        labels that aren't in ``route_registry`` (see module docstring).
    affected_chokepoints
        Chokepoint registry keys (``"suez"``, ``"panama"``, …) the event
        directly disrupted. The backtester elevates each named chokepoint's
        ``current_disruption_type`` when synthesising event inputs.
    description
        One-sentence summary for the UI table cell. Keep it factual.
    expected_ssi_band
        Which SSI band the event SHOULD have driven the fleet into.
        ``"Severe"`` / ``"Stressed"`` / ``"Elevated"``.
    expected_lead_time_days
        Acceptable detection lead time — how many days before
        ``start_date`` we consider the SSI to have "detected early".
    """

    event_id: str
    name: str
    start_date: str                           # ISO YYYY-MM-DD
    end_date: str                             # ISO YYYY-MM-DD or "ongoing"
    severity: str                             # "severe" | "major" | "moderate"
    affected_routes: list[str] = field(default_factory=list)
    affected_chokepoints: list[str] = field(default_factory=list)
    description: str = ""
    expected_ssi_band: str = "Stressed"       # "Severe" | "Stressed" | "Elevated"
    expected_lead_time_days: int = 7

    def __hash__(self) -> int:                # noqa: D401 — hash by ID
        """Hash by event_id so events work as dict keys / set members."""
        return hash(self.event_id)


# ---------------------------------------------------------------------------
# EVENTS — the registry
# ---------------------------------------------------------------------------

#: The canonical historical-event list. Order is chronological so the UI table
#: reads naturally top-to-bottom. Every entry is anchored in a real disruption
#: that materially affected container shipping; see each ``description`` for
#: the one-line summary.
EVENTS: list[HistoricalEvent] = [
    HistoricalEvent(
        event_id="covid_2020",
        name="COVID-19 Demand Shock (Q2 crash + Q3 surge)",
        start_date="2020-03-15",
        end_date="2020-12-31",
        severity="severe",
        # COVID hit the entire global container network — both crash and the
        # subsequent restocking surge registered as system-wide stress.
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "asia_europe",
            "ningbo_europe",
            "transatlantic",
            "sea_transpacific_eb",
            "south_asia_to_europe",
            "intra_asia_china_sea",
            "longbeach_to_asia",
        ],
        affected_chokepoints=[],              # Demand shock, not a chokepoint event
        description=(
            "Pandemic demand crash in Q2 followed by a restocking surge in Q3 "
            "drove broad congestion, rate dislocation and lane disruption."
        ),
        expected_ssi_band="Stressed",
        expected_lead_time_days=14,
    ),
    HistoricalEvent(
        event_id="suez_2021",
        name="Suez Canal Blockage (Ever Given)",
        start_date="2021-03-23",
        end_date="2021-03-29",
        severity="severe",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
            "med_hub_to_asia",
        ],
        affected_chokepoints=["suez"],
        description=(
            "The Ever Given container ship grounded in the Suez Canal, "
            "blocking ~12% of global trade for six days."
        ),
        expected_ssi_band="Severe",
        expected_lead_time_days=3,
    ),
    HistoricalEvent(
        event_id="uswc_2014",
        name="US West Coast Port Labour Dispute (2014–2015)",
        start_date="2014-10-31",
        end_date="2015-02-21",
        severity="major",
        affected_routes=[
            "transpacific_eb",
            "sea_transpacific_eb",
            "longbeach_to_asia",
        ],
        affected_chokepoints=[],              # Port-level dispute, not a chokepoint
        description=(
            "ILWU/PMA contract dispute slowed LA/Long Beach throughput for "
            "months, choking the Trans-Pacific lane."
        ),
        expected_ssi_band="Stressed",
        expected_lead_time_days=14,
    ),
    HistoricalEvent(
        event_id="hanjin_2016",
        name="Hanjin Shipping Bankruptcy",
        start_date="2016-08-31",
        end_date="2016-11-30",
        severity="major",
        affected_routes=[
            "transpacific_eb",
            "asia_europe",
            "sea_transpacific_eb",
        ],
        affected_chokepoints=[],
        description=(
            "Korea's largest carrier filed for receivership, stranding "
            "~$14B of cargo and dislocating Trans-Pacific capacity."
        ),
        expected_ssi_band="Stressed",
        expected_lead_time_days=7,
    ),
    HistoricalEvent(
        event_id="felixstowe_2021",
        name="Felixstowe Port Congestion (UK)",
        start_date="2021-08-20",
        end_date="2021-10-31",
        severity="moderate",
        affected_routes=[
            "asia_europe",
            "south_asia_to_europe",
        ],
        affected_chokepoints=[],
        description=(
            "UK's largest container port saw severe trucker shortages and "
            "containers stacked seven-high through autumn 2021."
        ),
        expected_ssi_band="Elevated",
        expected_lead_time_days=14,
    ),
    HistoricalEvent(
        event_id="uswc_2023",
        name="US West Coast Port Labour Dispute (2023)",
        start_date="2023-04-01",
        end_date="2023-06-30",
        severity="major",
        affected_routes=[
            "transpacific_eb",
            "sea_transpacific_eb",
            "longbeach_to_asia",
        ],
        affected_chokepoints=[],
        description=(
            "ILWU/PMA contract gap caused intermittent slowdowns at LA/Long "
            "Beach through mid-2023 before a tentative deal."
        ),
        expected_ssi_band="Stressed",
        expected_lead_time_days=7,
    ),
    HistoricalEvent(
        event_id="panama_drought_2023",
        name="Panama Canal Drought Restrictions",
        start_date="2023-08-08",
        end_date="2024-06-30",
        severity="major",
        affected_routes=[
            "transpacific_eb",
            "us_east_south_america",
            "china_south_america",
        ],
        affected_chokepoints=["panama"],
        description=(
            "Severe drought cut Panama Canal daily transits from ~36 to ~24, "
            "forcing US-East-Coast cargo around Cape Horn or via Suez."
        ),
        expected_ssi_band="Stressed",
        expected_lead_time_days=14,
    ),
    HistoricalEvent(
        event_id="red_sea_2024",
        name="Red Sea / Houthi Attacks",
        start_date="2024-01-01",
        end_date="ongoing",
        severity="severe",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
            "north_africa_to_europe",
        ],
        affected_chokepoints=["suez", "bab_el_mandeb"],
        description=(
            "Houthi attacks on Red Sea shipping forced major carriers to "
            "reroute around the Cape of Good Hope, adding 7–10 transit days."
        ),
        expected_ssi_band="Severe",
        expected_lead_time_days=7,
    ),
]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

#: Dict keyed by ``event_id`` for O(1) lookup. Kept module-level (and built at
#: import) so tests don't pay a per-call rebuild cost.
EVENTS_BY_ID: dict[str, HistoricalEvent] = {e.event_id: e for e in EVENTS}


def get_event(event_id: str) -> HistoricalEvent | None:
    """Return the event with this ``event_id`` or ``None`` if missing.

    Lookup is exact-match against the canonical ID. The CLI accepts the same
    string — operator typos surface as ``None`` here, then become a single-
    line error from the CLI rather than a traceback.
    """
    if not event_id:
        return None
    return EVENTS_BY_ID.get(str(event_id))


def _parse_iso_date(s: str) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string to a :class:`date`, tolerantly.

    Returns ``None`` for the literal sentinel ``"ongoing"`` and for any value
    Python's :func:`datetime.fromisoformat` cannot accept — the caller treats
    ``None`` as "open-ended" rather than crashing.
    """
    if not s or str(s).strip().lower() == "ongoing":
        return None
    try:
        return datetime.fromisoformat(str(s).strip()[:10]).date()
    except (TypeError, ValueError):
        return None


def get_events_in_window(start_date: str, end_date: str) -> list[HistoricalEvent]:
    """Return events whose active period intersects ``[start_date, end_date]``.

    An event with ``end_date == "ongoing"`` intersects any window whose
    upper bound is on or after the event's ``start_date``. A malformed
    window date returns an empty list rather than raising — the backtester
    treats *no events in window* as a valid (if uninteresting) state.
    """
    win_start = _parse_iso_date(start_date)
    win_end = _parse_iso_date(end_date)
    if win_start is None or win_end is None:
        return []
    if win_start > win_end:
        return []

    out: list[HistoricalEvent] = []
    for event in EVENTS:
        ev_start = _parse_iso_date(event.start_date)
        ev_end = _parse_iso_date(event.end_date)
        if ev_start is None:
            continue
        # "ongoing" -> use the window's upper bound as the implicit end.
        ev_end_effective = ev_end if ev_end is not None else win_end
        # Half-open intersection: events overlap if start <= win_end and
        # end >= win_start. (Inclusive on both sides — calendar-day windows.)
        if ev_start <= win_end and ev_end_effective >= win_start:
            out.append(event)
    return out
