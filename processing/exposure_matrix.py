"""exposure_matrix.py — Dense company↔commodity exposure matrix.

Replaces the sparse 4-entry ``COMMODITY_SHIPPING_MAP`` in
``processing/commodity_shipping.py`` with a *dense* linkage layer:

  * every HS cargo category (``cargo_analyzer.HS_CATEGORIES``) is mapped to a
    representative, tracked equity ETF (``COMMODITY_ETF_MAP``);
  * every tracked shipping company (``company_profiler.COMPANY_PROFILES``) is
    given a weight vector over those same HS categories
    (``COMPANY_COMMODITY_EXPOSURE``), derived at import time from the company's
    trade routes averaged through ``cargo_analyzer.get_route_cargo_mix()``.

This is the "Link" stage of the Disruption Alpha chain: it lets the cascade
scorer walk *commodity move → exposed companies* without a black box.

Pure processing module — no Streamlit, no I/O. Everything is derived
deterministically at import from the registry/profile constants, so the result
is stable within and across sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from processing import cargo_analyzer
from processing import company_profiler
from routes.route_registry import ROUTES, ROUTES_BY_ID

# ---------------------------------------------------------------------------
# Commodity → ETF mapping
# ---------------------------------------------------------------------------
# Each HS category maps to one representative ETF whose 30-day move is used as
# a tradable proxy for that commodity's demand signal.
#
# Tracked ETF universe (per the platform's stock-data loader / plan):
#   XRT — SPDR S&P Retail            (consumer-goods demand: electronics,
#                                     automotive, apparel)
#   XLI — Industrial Select Sector   (capital-goods demand: machinery)
#   XLB — Materials Select Sector    (materials / chemicals)
#   DBA — Invesco DB Agriculture     (agricultural commodities)
#   DBB — Invesco DB Base Metals     (base metals & steel)
#   USO — United States Oil Fund     (fuel-cost overlay — NOT used as a
#                                     category proxy here; see _FUEL_OVERLAY_ETF)
#
# Note: only XRT and XLI are guaranteed to be in the default stock-data feed;
# XLB/DBA/DBB may be absent. ``build_exposure_matrix`` degrades gracefully when
# a mapped ETF is missing (price change 0.0, direction "Neutral").
COMMODITY_ETF_MAP: dict[str, str] = {
    "electronics": "XRT",   # consumer-electronics demand
    "machinery": "XLI",     # industrial / capital goods
    "automotive": "XRT",    # consumer durables
    "apparel": "XRT",       # consumer soft goods
    "chemicals": "XLB",     # materials sector
    "agriculture": "DBA",   # agricultural commodities
    "metals": "DBB",        # base metals & steel
}

# ETF representing bunker-fuel cost. Tracked separately as a *cost* overlay
# rather than a per-category demand proxy: a rise in USO is bearish for carrier
# margins regardless of which cargo a ship carries. Exposed here for the
# cascade scorer; not part of COMMODITY_ETF_MAP.
_FUEL_OVERLAY_ETF: str = "USO"

# ---------------------------------------------------------------------------
# Company route / sector resolution
# ---------------------------------------------------------------------------
# ``COMPANY_PROFILES`` stores ``key_routes`` as human-readable strings
# ("Trans-Pacific", "Capesize iron ore", "Global charter ...") — never the
# route_ids used by route_registry. Resolution is therefore keyword-based:
# each known phrase fragment maps to one or more registry route_ids. Anything
# unresolved falls back to a sector-based default weighting.

# Lower-cased substring fragment → list of route_ids it implies.
_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "trans-pacific":  ["transpacific_eb", "transpacific_wb"],
    "transpacific":   ["transpacific_eb", "transpacific_wb"],
    "asia-usec":      ["asia_europe", "transpacific_eb"],
    "asia-europe":    ["asia_europe", "ningbo_europe"],
    "intra-asia":     ["intra_asia_china_sea", "intra_asia_china_japan"],
    "china-uswc":     ["transpacific_eb"],
    "clx":            ["transpacific_eb"],
    "hawaii":         ["transpacific_eb"],          # US WC ↔ Pacific proxy
    "alaska":         ["transpacific_eb"],
    "guam":           ["transpacific_eb", "intra_asia_china_sea"],
    "capesize":       ["china_south_america", "middle_east_to_asia"],
    "panamax":        ["us_east_south_america", "china_south_america"],
    "kamsarmax":      ["us_east_south_america", "china_south_america"],
    "supramax":       ["north_africa_to_europe", "intra_asia_china_sea"],
    "iron ore":       ["china_south_america", "middle_east_to_asia"],
    "grain":          ["us_east_south_america", "china_south_america"],
    "coal":           ["north_africa_to_europe", "middle_east_to_asia"],
    "transatlantic":  ["transatlantic"],
}

# Sector → default HS-category weight vector, used when a company's routes
# cannot be resolved at all (e.g. pure charter lessors) or as the blend target
# for dry-bulk vs container tilting. Weights are illustrative and normalised.
_SECTOR_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    # Container liners / lessors carry finished manufactured goods.
    "container": {
        "electronics": 0.30,
        "machinery": 0.24,
        "apparel": 0.20,
        "automotive": 0.12,
        "chemicals": 0.08,
        "metals": 0.04,
        "agriculture": 0.02,
    },
    # Dry-bulk carriers move raw materials — metals/agriculture dominate.
    "dry bulk": {
        "metals": 0.42,
        "agriculture": 0.38,
        "chemicals": 0.12,
        "machinery": 0.05,
        "electronics": 0.01,
        "apparel": 0.01,
        "automotive": 0.00,
    },
}

# Tickers explicitly treated as dry-bulk carriers (tilt toward metals /
# agriculture) vs. container names (tilt toward electronics / machinery /
# apparel). Used both for the route-mix tilt and the sector fallback.
_DRY_BULK_TICKERS: frozenset[str] = frozenset({"SBLK", "GOGL", "DSX"})
_CONTAINER_TICKERS: frozenset[str] = frozenset({"ZIM", "MATX", "DAC", "CMRE"})

# How hard to pull a company's raw route-mix toward its sector default.
# 0.0 = pure route mix, 1.0 = pure sector default. A moderate tilt keeps the
# route-derived signal dominant while ensuring dry-bulk names never end up
# electronics-heavy purely from a generic fallback.
_SECTOR_TILT: float = 0.35

# Minimum real per-route cargo share for a route to count as "carrying" a
# commodity in ``routes_for_commodity``. The region-dominance heuristic
# (``_REGION_DOMINANT_CARGO``) is coarse: it can flag a route whose *actual*
# cargo mix (``cargo_analyzer.get_route_cargo_mix``) carries little or none of
# the commodity — e.g. a route flagged for "apparel" by origin region that the
# mix shows carrying 0%. Cross-checking the region heuristic against the real
# mix removes that inconsistency, so this module and the cascade scorer agree
# on which lanes carry a commodity. 0.05 is low enough to keep every genuine
# exposure while dropping only negligible / zero-share routes. Documented and
# explicit — a published threshold, not a fitted cut-off.
_ROUTE_COMMODITY_MIN_SHARE: float = 0.05

# ---------------------------------------------------------------------------
# Hand-tuned overrides
# ---------------------------------------------------------------------------
# Small corrections applied *after* the derived weights, for credibility where
# the route-averaging heuristic is too blunt. Each ticker's override dict is
# merged onto the derived vector, then the whole vector is re-normalised to 1.0.
# Kept deliberately small — the matrix should stay mostly data-derived.
_EXPOSURE_OVERRIDES: dict[str, dict[str, float]] = {
    # ZIM's cross-Pacific niche is consumer-electronics heavy.
    "ZIM": {"electronics": 0.34},
    # Matson's China express (CLX) is a premium e-commerce / electronics lane.
    "MATX": {"electronics": 0.32, "apparel": 0.18},
    # Star Bulk is the canonical iron-ore Capesize / grain Panamax play.
    "SBLK": {"metals": 0.50, "agriculture": 0.34},
    # Golden Ocean is even more iron-ore / steel concentrated.
    "GOGL": {"metals": 0.50, "agriculture": 0.34},
    # Diana Shipping leans grain (Panamax/Kamsarmax) over metals.
    "DSX": {"agriculture": 0.44, "metals": 0.34},
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CommodityExposure:
    """One HS cargo category, its ETF proxy, and the companies exposed to it."""

    hs_category: str                # "electronics", "machinery", ...
    category_label: str             # "Electronics", "Machinery", ...
    etf_ticker: str                 # representative tracked ETF
    etf_price_change_30d: float     # 30-day % move of the ETF (0.0 if missing)
    direction: str                  # "Bullish" | "Bearish" | "Neutral"
    affected_routes: list[str] = field(default_factory=list)     # route_ids
    bullish_companies: list[str] = field(default_factory=list)   # tickers
    bearish_companies: list[str] = field(default_factory=list)   # tickers
    exposure_note: str = ""         # 1-2 sentence plain-language summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Return ``weights`` rescaled so the values sum to 1.0.

    Negative values are clamped to 0. An empty / all-zero input yields an even
    split across every HS category.
    """
    clean = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        n = len(cargo_analyzer.HS_CATEGORIES)
        return {k: 1.0 / n for k in cargo_analyzer.HS_CATEGORIES}
    return {k: v / total for k, v in clean.items()}


def _resolve_company_routes(profile: dict) -> list[str]:
    """Map a company profile's human-readable ``key_routes`` to route_ids.

    Returns the de-duplicated list of registry route_ids implied by keyword
    matches against the profile's route strings. Returns ``[]`` when nothing
    matches (caller then falls back to a sector default).
    """
    resolved: list[str] = []
    for raw in profile.get("key_routes", []) or []:
        text = str(raw).lower()
        for fragment, route_ids in _ROUTE_KEYWORDS.items():
            if fragment in text:
                for rid in route_ids:
                    if rid in ROUTES_BY_ID and rid not in resolved:
                        resolved.append(rid)
    return resolved


def _is_dry_bulk(ticker: str, profile: dict) -> bool:
    """Decide whether a company should be treated as a dry-bulk carrier.

    The explicit ``_DRY_BULK_TICKERS`` / ``_CONTAINER_TICKERS`` sets are
    authoritative: a known container name is never reclassified as dry-bulk
    even though its free-text ``sector`` may contain "Dry Bulk" (e.g. Costamare
    is ``"Container/Dry Bulk"`` but is run as a container lessor here). Only
    tickers in neither set fall back to a substring read of ``sector``.
    """
    if ticker in _DRY_BULK_TICKERS:
        return True
    if ticker in _CONTAINER_TICKERS:
        return False
    return "dry bulk" in str(profile.get("sector", "")).lower()


def _sector_default(ticker: str, profile: dict) -> dict[str, float]:
    """Return the sector-default HS weight vector for a company.

    Dry-bulk carriers get the dry-bulk vector, everything else the container
    vector — robust even if the free-text ``sector`` field is unusual.
    """
    key = "dry bulk" if _is_dry_bulk(ticker, profile) else "container"
    return dict(_SECTOR_DEFAULT_WEIGHTS[key])


def _derive_company_weights(ticker: str, profile: dict) -> dict[str, float]:
    """Derive one company's HS-category weight vector.

    Pipeline:
      1. Resolve the company's routes; average ``get_route_cargo_mix`` across
         them (illustrative fallback mix is used by cargo_analyzer when no
         trade data is supplied — we pass ``{}`` deliberately).
      2. If no routes resolve, start from the sector-default vector instead.
      3. Tilt the result toward the sector default by ``_SECTOR_TILT`` so
         dry-bulk names stay raw-materials-weighted and container names stay
         finished-goods-weighted.
      4. Merge any ``_EXPOSURE_OVERRIDES`` for the ticker.
      5. Normalise to sum to 1.0.
    """
    cats = list(cargo_analyzer.HS_CATEGORIES)
    route_ids = _resolve_company_routes(profile)

    if route_ids:
        # Average the per-route cargo mixes. cargo_analyzer.get_route_cargo_mix
        # returns illustrative, normalised weights when trade_data is empty.
        summed: dict[str, float] = {k: 0.0 for k in cats}
        for rid in route_ids:
            mix = cargo_analyzer.get_route_cargo_mix(rid, {})
            for k in cats:
                summed[k] += float(mix.get(k, 0.0))
        base = {k: summed[k] / len(route_ids) for k in cats}
    else:
        # No resolvable routes (pure charter lessor) — start from sector default.
        base = {k: 0.0 for k in cats}
        for k, v in _sector_default(ticker, profile).items():
            base[k] = v

    base = _normalise(base)

    # Tilt toward the sector default vector.
    sector_vec = _normalise(_sector_default(ticker, profile))
    tilted = {
        k: (1.0 - _SECTOR_TILT) * base.get(k, 0.0)
        + _SECTOR_TILT * sector_vec.get(k, 0.0)
        for k in cats
    }

    # Apply hand-tuned overrides, then re-normalise.
    for k, v in _EXPOSURE_OVERRIDES.get(ticker, {}).items():
        if k in tilted:
            tilted[k] = float(v)

    return _normalise(tilted)


def _build_company_commodity_exposure() -> dict[str, dict[str, float]]:
    """Build the full ``ticker → {hs_category: weight}`` matrix at import."""
    matrix: dict[str, dict[str, float]] = {}
    for ticker, profile in company_profiler.COMPANY_PROFILES.items():
        matrix[ticker] = _derive_company_weights(ticker, profile)
    return matrix


# ---------------------------------------------------------------------------
# Module-level derived constant
# ---------------------------------------------------------------------------
# ticker → {hs_category: weight in [0, 1]}; each company's vector sums to 1.0.
COMPANY_COMMODITY_EXPOSURE: dict[str, dict[str, float]] = (
    _build_company_commodity_exposure()
)


# ---------------------------------------------------------------------------
# ETF price-move helper
# ---------------------------------------------------------------------------

def _etf_change_30d(etf: str, stock_data: dict) -> float:
    """Return an ETF's 30-day fractional price change from ``stock_data``.

    ``stock_data`` maps ticker → DataFrame[date, symbol, open, high, low,
    close, volume]. Returns ``0.0`` when the ETF is absent, the frame is
    empty/malformed, or there is too little history — never raises.
    """
    if not stock_data:
        return 0.0
    df = stock_data.get(etf)
    if df is None:
        return 0.0
    try:
        if getattr(df, "empty", True) or "close" not in df.columns:
            return 0.0
        close = df["close"].dropna()
        if len(close) < 2:
            return 0.0
        current = float(close.iloc[-1])
        prior = float(close.iloc[-31]) if len(close) >= 31 else float(close.iloc[0])
        if prior == 0:
            return 0.0
        return (current - prior) / abs(prior)
    except Exception:
        # Degrade gracefully on any unexpected frame shape.
        return 0.0


def _direction_from_change(change_30d: float, threshold: float = 0.03) -> str:
    """Classify a 30-day move as Bullish / Bearish / Neutral.

    A rising commodity-demand ETF is read as bullish for the cargo it proxies
    (more goods to move). ``threshold`` is the dead-band that keeps small noise
    moves Neutral — matches the 3% band used by ``commodity_shipping``.
    """
    if change_30d > threshold:
        return "Bullish"
    if change_30d < -threshold:
        return "Bearish"
    return "Neutral"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def routes_for_commodity(hs_category: str) -> list[str]:
    """Return registry route_ids that genuinely carry ``hs_category`` cargo.

    A route counts when **both** explicit, documented conditions hold:

    1. the category is dominant for the route's origin region — the coarse
       region heuristic in ``cargo_analyzer._REGION_DOMINANT_CARGO`` (the same
       logic ``cargo_analyzer.analyze_cargo_flows`` uses); and
    2. the route's **actual** cargo mix
       (``cargo_analyzer.get_route_cargo_mix``) carries at least
       :data:`_ROUTE_COMMODITY_MIN_SHARE` of that commodity.

    Condition 2 was added so the result agrees with the real per-route cargo
    mix the cascade scorer uses: without it the region heuristic can list a
    route that in fact carries 0% of the commodity. The check is a transparent
    threshold comparison — no model, no hidden weighting.

    When ``get_route_cargo_mix`` cannot be consulted for a route (it raises),
    that route is kept on the region heuristic alone — a conservative degrade
    that never *adds* a spurious route, only tolerates a missing cross-check.
    Unknown categories return ``[]``.
    """
    routes: list[str] = []
    for route in ROUTES:
        dominant = cargo_analyzer._REGION_DOMINANT_CARGO.get(
            route.origin_region, []
        )
        if hs_category not in dominant:
            continue
        # Cross-check the coarse region flag against the route's real mix.
        try:
            mix = cargo_analyzer.get_route_cargo_mix(route.id, {})
        except Exception:
            # Mix unavailable — fall back to the region heuristic alone.
            routes.append(route.id)
            continue
        if not mix:
            routes.append(route.id)
            continue
        if float(mix.get(hs_category, 0.0)) >= _ROUTE_COMMODITY_MIN_SHARE:
            routes.append(route.id)
    return routes


def company_commodity_weights(ticker: str) -> dict[str, float]:
    """Return a company's HS-category weight vector (sums to 1.0).

    Unknown tickers return an even split across every HS category so callers
    never receive an empty dict.
    """
    weights = COMPANY_COMMODITY_EXPOSURE.get(ticker)
    if weights:
        return dict(weights)
    n = len(cargo_analyzer.HS_CATEGORIES)
    return {k: 1.0 / n for k in cargo_analyzer.HS_CATEGORIES}


def build_exposure_matrix(stock_data: dict) -> list[CommodityExposure]:
    """Build one ``CommodityExposure`` per HS cargo category.

    Parameters
    ----------
    stock_data:
        Mapping ``ticker -> DataFrame[date, symbol, open, high, low, close,
        volume]``. May be empty or missing some commodity ETFs — when an ETF
        is unavailable that category degrades to ``etf_price_change_30d=0.0``
        and ``direction="Neutral"`` rather than raising.

    Returns
    -------
    list[CommodityExposure]
        One entry per key of ``cargo_analyzer.HS_CATEGORIES`` (≈7), in the
        category order defined there.
    """
    stock_data = stock_data or {}
    matrix: list[CommodityExposure] = []

    # Companies whose weight on a category exceeds this share are considered
    # meaningfully "exposed" to that commodity's signal.
    exposure_threshold = 0.12

    for hs_category, meta in cargo_analyzer.HS_CATEGORIES.items():
        etf = COMMODITY_ETF_MAP.get(hs_category, "")
        change_30d = _etf_change_30d(etf, stock_data) if etf else 0.0
        direction = _direction_from_change(change_30d)

        affected_routes = routes_for_commodity(hs_category)

        # Companies exposed to this category, ranked by exposure weight.
        exposed = sorted(
            (
                (ticker, weights.get(hs_category, 0.0))
                for ticker, weights in COMPANY_COMMODITY_EXPOSURE.items()
                if weights.get(hs_category, 0.0) >= exposure_threshold
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        exposed_tickers = [t for t, _ in exposed]

        # A rising demand ETF is bullish for the exposed carriers; a falling
        # one is bearish. Neutral moves leave both lists empty.
        if direction == "Bullish":
            bullish_companies, bearish_companies = exposed_tickers, []
        elif direction == "Bearish":
            bullish_companies, bearish_companies = [], exposed_tickers
        else:
            bullish_companies, bearish_companies = [], []

        matrix.append(
            CommodityExposure(
                hs_category=hs_category,
                category_label=meta.get("label", hs_category.title()),
                etf_ticker=etf,
                etf_price_change_30d=change_30d,
                direction=direction,
                affected_routes=affected_routes,
                bullish_companies=bullish_companies,
                bearish_companies=bearish_companies,
                exposure_note=_exposure_note(
                    meta.get("label", hs_category.title()),
                    etf,
                    change_30d,
                    direction,
                    exposed_tickers,
                ),
            )
        )

    return matrix


def _exposure_note(
    label: str,
    etf: str,
    change_30d: float,
    direction: str,
    exposed_tickers: list[str],
) -> str:
    """Compose a short plain-language summary for a ``CommodityExposure``."""
    if not etf:
        return f"{label} cargo has no mapped ETF proxy; signal unavailable."

    names = ", ".join(exposed_tickers) if exposed_tickers else "no tracked carriers"

    if direction == "Bullish":
        return (
            f"{etf} (proxy for {label.lower()} demand) is up {change_30d:+.1%} "
            f"over 30 days — supportive of cargo volumes. Most exposed: {names}."
        )
    if direction == "Bearish":
        return (
            f"{etf} (proxy for {label.lower()} demand) is down {abs(change_30d):.1%} "
            f"over 30 days — a drag on cargo volumes. Most exposed: {names}."
        )
    return (
        f"{etf} (proxy for {label.lower()} demand) is roughly flat "
        f"({change_30d:+.1%} / 30d) — no clear signal. Most exposed: {names}."
    )
