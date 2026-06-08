from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from loguru import logger

from processing.carbon_calculator import ROUTE_DISTANCES, _FUEL_CONSUMPTION_MT_PER_DAY
from routes.route_registry import ROUTES, ROUTES_BY_ID
from utils.helpers import trend_label


# ════════════════════════════════════════════════════════════════════════════
# R050 — Bunker-adjusted net freight (TCE proxy) anchored on REAL crude
# ════════════════════════════════════════════════════════════════════════════
#
# Gross spot rates (rate_usd_per_feu) tell you the headline price of a slot, but
# fuel is 40-60% of a voyage's running cost. When crude rallies, a rising gross
# rate can hide a *falling* margin: the rally is eaten by bunkers. This block
# computes a net-of-fuel freight figure per FEU, anchored on REAL WTI/Brent
# crude (FRED), and flags the 'gross up, net down' divergence.
#
# ── Fuel-leg formula ─────────────────────────────────────────────────────────
#   total_voyage_fuel_cost_usd = distance_nm × consumption_mt_per_nm × bunker_usd_per_mt
#   fuel_leg_usd_per_feu       = total_voyage_fuel_cost_usd / loaded_feu
#   net_freight_usd_per_feu    = gross_rate_usd_per_feu − fuel_leg_usd_per_feu
#
#   where:
#     distance_nm            = ROUTE_DISTANCES[route_id]   (nautical miles)
#     consumption_mt_per_nm  = 85 MT/day ÷ (20 kn × 24 h) ≈ 0.1771 MT/nm
#         • 85 MT/day is carbon_calculator._FUEL_CONSUMPTION_MT_PER_DAY (large
#           container vessel at sea speed) — reused so the two modules agree.
#         • 20 knots is the assumed sea speed for a large container vessel,
#           giving 480 nm/day. Documented constant _SEA_SPEED_KNOTS below.
#     loaded_feu             = 8000 TEU × 0.85 load ÷ 2 (TEU→FEU) = 3400 FEU
#         • mirrors carbon_calculator's _TEU_CAPACITY (8000) and _LOAD_FACTOR
#           (0.85); a FEU is 2 TEU, so loaded FEU = loaded TEU / 2.
#
#   Unit reconciliation: gross_rate is $/FEU, so the fuel leg MUST also be
#   $/FEU. We compute the WHOLE-VESSEL voyage fuel bill ($) and divide by the
#   loaded FEU count to get $/FEU. Net = gross − fuel_leg, both in $/FEU.
#
# ── Crude → bunker (VLSFO) heuristic ─────────────────────────────────────────
#   bunker_vlsfo_usd_per_mt ≈ crude_usd_per_bbl × 6.35 + 95
#     • 6.35 bbl/MT is the standard barrels-per-tonne conversion for heavy
#       fuel oil / VLSFO (~6.3-6.5; IMO/EIA use ~6.35 for residual fuel).
#     • +$95/MT is a documented refining + bunkering markup that maps recent
#       WTI (~$70-80/bbl) to observed VLSFO hub prices (~$540-600/MT;
#       Ship & Bunker global avg ≈ $628). This is a LINEAR back-of-envelope
#       proxy, NOT a Platts assessment — it is labelled `modeled` wherever it
#       drives a number, regardless of whether the crude input was real.
#
#   The REALNESS we track is the *crude* input: a live FRED WTI print → the
#   crude leg is real (provenance REAL/CACHED); a missing crude print → a
#   documented modeled fallback ($72/bbl ≈ a recent WTI level) and provenance
#   MODELED. We never fabricate a crude print as real.
# ─────────────────────────────────────────────────────────────────────────────

_SEA_SPEED_KNOTS: float = 20.0                 # assumed large-container sea speed
_NM_PER_DAY: float = _SEA_SPEED_KNOTS * 24.0   # 480 nm/day
# MT fuel burned per nautical mile (reuses carbon_calculator's 85 MT/day).
_FUEL_CONSUMPTION_MT_PER_NM: float = _FUEL_CONSUMPTION_MT_PER_DAY / _NM_PER_DAY  # ≈ 0.1771

_TEU_CAPACITY: int = 8_000      # mirrors carbon_calculator._TEU_CAPACITY
_LOAD_FACTOR: float = 0.85      # mirrors carbon_calculator._LOAD_FACTOR
_TEU_PER_FEU: float = 2.0       # an FEU (40ft) is two TEU (20ft)
_LOADED_FEU: float = (_TEU_CAPACITY * _LOAD_FACTOR) / _TEU_PER_FEU  # 3400 FEU

# Crude → VLSFO conversion (documented heuristic above).
_BBL_PER_MT_FUEL: float = 6.35          # barrels per metric tonne of VLSFO/HFO
_VLSFO_MARKUP_USD_PER_MT: float = 95.0  # refining + bunkering markup

# Offline-safe modeled crude level when FRED is dark (≈ a recent WTI print).
_MODELED_CRUDE_USD_PER_BBL: float = 72.0


@dataclass(frozen=True)
class NetFreight:
    """Bunker-adjusted net freight for one route (all $ figures per FEU)."""
    route_id: str
    gross_rate_usd_per_feu: float
    fuel_leg_usd_per_feu: float
    net_freight_usd_per_feu: float
    crude_usd_per_bbl: float          # the crude price actually used
    bunker_usd_per_mt: float          # derived VLSFO price
    distance_nm: float
    crude_provenance: str             # "real" | "modeled"
    net_margin_pct: float             # net / gross, 0.0 if gross<=0


def crude_to_bunker_usd_per_mt(crude_usd_per_bbl: float) -> float:
    """Convert a crude ($/bbl) print to a modeled VLSFO bunker price ($/MT).

    Heuristic (documented, LABELLED MODELED regardless of crude realness):
        bunker ≈ crude × 6.35 bbl/MT + $95/MT markup
    """
    return max(0.0, float(crude_usd_per_bbl) * _BBL_PER_MT_FUEL + _VLSFO_MARKUP_USD_PER_MT)


def compute_net_freight(
    route_id: str,
    gross_rate: float,
    *,
    crude_usd_per_bbl: float | None = None,
    loaded_feu: float | None = None,
) -> NetFreight:
    """Compute bunker-adjusted net freight ($/FEU) for a route.

    Net freight = gross spot ($/FEU) − the fuel leg ($/FEU), where the fuel
    leg is the whole-vessel voyage bunker bill divided by the loaded FEU count.
    See the module-level block for the full formula + the crude→bunker
    heuristic + unit reconciliation.

    Parameters
    ----------
    route_id:
        Identifier matching ``ROUTE_DISTANCES`` keys.
    gross_rate:
        Gross spot rate in $/FEU (``rate_usd_per_feu``).
    crude_usd_per_bbl:
        REAL crude print (WTI/Brent, $/bbl). Injectable for tests. When
        ``None`` (or non-positive), a documented modeled fallback is used and
        provenance is stamped ``"modeled"`` — never fabricated as real.
    loaded_feu:
        Override the loaded-FEU divisor (defaults to 3400). Kept injectable
        for tests / other vessel classes.

    Returns
    -------
    NetFreight
        ``net_freight_usd_per_feu`` can be NEGATIVE when fuel exceeds the
        gross rate (a fuel-eaten route) — that is surfaced honestly, not
        clamped.

    Never raises: an unknown route (distance 0) yields a fuel leg of 0 and
    net == gross, which is the honest 'no distance data' outcome.
    """
    if crude_usd_per_bbl is not None and float(crude_usd_per_bbl) > 0:
        crude = float(crude_usd_per_bbl)
        provenance = "real"
    else:
        crude = _MODELED_CRUDE_USD_PER_BBL
        provenance = "modeled"

    bunker_usd_per_mt = crude_to_bunker_usd_per_mt(crude)
    distance_nm = float(ROUTE_DISTANCES.get(route_id, 0.0))
    feu = float(loaded_feu) if (loaded_feu and loaded_feu > 0) else _LOADED_FEU

    total_voyage_fuel_usd = distance_nm * _FUEL_CONSUMPTION_MT_PER_NM * bunker_usd_per_mt
    fuel_leg_usd_per_feu = total_voyage_fuel_usd / feu if feu > 0 else 0.0

    gross = float(gross_rate)
    net = gross - fuel_leg_usd_per_feu
    margin_pct = (net / gross) if gross > 0 else 0.0

    return NetFreight(
        route_id=route_id,
        gross_rate_usd_per_feu=gross,
        fuel_leg_usd_per_feu=fuel_leg_usd_per_feu,
        net_freight_usd_per_feu=net,
        crude_usd_per_bbl=crude,
        bunker_usd_per_mt=bunker_usd_per_mt,
        distance_nm=distance_nm,
        crude_provenance=provenance,
        net_margin_pct=margin_pct,
    )


@dataclass(frozen=True)
class NetFreightDivergence:
    """Result of a 'gross up, net down' divergence check over a window."""
    route_id: str
    diverged: bool                 # True = gross rose but net flat/falling
    gross_change_usd_per_feu: float
    net_change_usd_per_feu: float
    crude_provenance: str


def net_freight_divergence(
    route_id: str,
    gross_start: float,
    gross_end: float,
    *,
    crude_start_usd_per_bbl: float | None = None,
    crude_end_usd_per_bbl: float | None = None,
    gross_rise_threshold: float = 50.0,
    net_flat_threshold: float = 0.0,
    loaded_feu: float | None = None,
) -> NetFreightDivergence:
    """Flag 'gross up, net down': gross rate rallies but NET is flat/falling.

    The classic fuel-eaten-rally signal. Computes net freight at the start and
    end of a window (each net-of-its-own-crude) and flags a divergence when:

        gross_end − gross_start  >  gross_rise_threshold   (a real rally)
      AND
        net_end   − net_start   <= net_flat_threshold      (net flat/falling)

    Parameters
    ----------
    gross_start, gross_end:
        Gross $/FEU at the window endpoints.
    crude_start_usd_per_bbl, crude_end_usd_per_bbl:
        REAL crude ($/bbl) at each endpoint. ``None`` → modeled fallback at
        that endpoint (provenance reflects whichever endpoint was modeled).
    gross_rise_threshold:
        Minimum $/FEU gross increase to count as a 'rally' (default $50/FEU,
        ~1-3% of a typical headline rate — filters noise).
    net_flat_threshold:
        Net change at/below this ($/FEU) counts as 'flat/falling' (default 0.0,
        i.e. net did not improve).

    Window: the caller picks the two endpoints (e.g. 30 trading days apart).
    Never raises.
    """
    start = compute_net_freight(
        route_id, gross_start,
        crude_usd_per_bbl=crude_start_usd_per_bbl, loaded_feu=loaded_feu,
    )
    end = compute_net_freight(
        route_id, gross_end,
        crude_usd_per_bbl=crude_end_usd_per_bbl, loaded_feu=loaded_feu,
    )
    gross_change = end.gross_rate_usd_per_feu - start.gross_rate_usd_per_feu
    net_change = end.net_freight_usd_per_feu - start.net_freight_usd_per_feu

    diverged = (gross_change > gross_rise_threshold) and (net_change <= net_flat_threshold)

    # Provenance: "real" only if BOTH endpoints used a real crude print.
    provenance = "real" if (start.crude_provenance == "real" and end.crude_provenance == "real") else "modeled"

    return NetFreightDivergence(
        route_id=route_id,
        diverged=diverged,
        gross_change_usd_per_feu=gross_change,
        net_change_usd_per_feu=net_change,
        crude_provenance=provenance,
    )


def compute_rate_momentum(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
) -> float:
    """Compute rate momentum score [0, 1] for a route.

    Score > 0.5: current rate above rolling average (bullish)
    Score < 0.5: current rate below rolling average (bearish)
    Score = 0.5: at average or no data
    """
    df = freight_data.get(route_id)
    if df is None or df.empty or len(df) < 2:
        return 0.5

    df = df.sort_values("date")
    if "rate_usd_per_feu" not in df.columns:
        return 0.5
    rates = df["rate_usd_per_feu"].dropna()

    if len(rates) < 2:
        return 0.5

    current_rate = float(rates.iloc[-1])
    rolling_avg = float(rates.tail(lookback_days).mean())

    if rolling_avg == 0:
        return 0.5

    # Map ratio [0.5, 1.5] → [0, 1]
    ratio = current_rate / rolling_avg
    score = (ratio - 0.5) / 1.0
    return max(0.0, min(1.0, score))


def compute_rate_pct_change(
    route_id: str,
    freight_data: dict[str, pd.DataFrame],
    days: int = 30,
) -> float:
    """Return percentage rate change over the last N days."""
    df = freight_data.get(route_id)
    if df is None or len(df) < 2:
        return 0.0

    df = df.sort_values("date")
    recent = df.tail(days + 1)

    if len(recent) < 2:
        return 0.0
    if "rate_usd_per_feu" not in recent.columns:
        return 0.0

    start = recent["rate_usd_per_feu"].iloc[0]
    end = recent["rate_usd_per_feu"].iloc[-1]

    if start == 0:
        return 0.0

    return (end - start) / start


def get_all_route_rates(
    freight_data: dict[str, pd.DataFrame],
) -> dict[str, dict]:
    """Return a summary dict for all routes: {route_id: {rate, pct_30d, trend, momentum}}."""
    summary = {}
    for route in ROUTES:
        df = freight_data.get(route.id)
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns:
            _rates = df["rate_usd_per_feu"].dropna()
            current_rate = float(_rates.iloc[-1]) if not _rates.empty else 0.0
        else:
            current_rate = 0.0
        pct_30d = compute_rate_pct_change(route.id, freight_data, 30)
        momentum = compute_rate_momentum(route.id, freight_data)
        summary[route.id] = {
            "current_rate": current_rate,
            "pct_30d": pct_30d,
            "trend": trend_label(pct_30d),
            "momentum_score": momentum,
        }
    return summary
