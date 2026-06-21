"""tariff_engine.py — Tariff pass-through & trade-diversion engine (R025).

Computes the trade value-at-risk from a tariff regime, the elasticity-weighted
DIVERSION of that flow to alternative origins, and rolls the result up to a
per-shipping-equity exposure via the dense commodity↔company matrix in
``processing.exposure_matrix``.

This is the engine that DE-MOCKS the Trade War tab: it replaces a frozen facade
(a hardcoded $244B burden / 214 rerouted-ships constant) with a COMPUTED burden
derived from a curated, dated tariff table × real per-country trade-flow shares
× a documented base trade value.

────────────────────────────────────────────────────────────────────────────
HONESTY / PROVENANCE
────────────────────────────────────────────────────────────────────────────
* TARIFF RATES are public policy. The :data:`US_CHINA_TARIFF_RATES_2025` table
  below is a *curated, dated* snapshot of the announced US Section-301 + IEEPA
  "reciprocal" tariff regime on Chinese goods (effective May 2025). They are
  real-world policy values, documented inline and stamped via
  :data:`TARIFF_POLICY_SOURCE` as "curated policy data (as of …)" — NOT
  fabricated. Tariff schedules move fast; treat the date as the as-of.

* TRADE-FLOW SHARES come from ``data.comtrade_feed._COUNTRY_CATEGORY_SHARES``
  (China's per-HS-category export mix). Those are hand-tuned-but-realistic
  shares; the feed itself labels them modeled.

* BASE TRADE VALUE (:data:`US_IMPORTS_FROM_CHINA_GOODS_BN`) is a single
  documented scalar — total US goods imports from China — used to turn shares
  into dollar exposure. Documented + dated; see the constant.

* ELASTICITY (:data:`DIVERSION_ELASTICITY`) is a single documented, conservative
  diversion sensitivity. It is an ASSUMED structural parameter, not a fitted or
  measured one — labeled as such.

Everything downstream is a pure, deterministic arithmetic roll-up of those
inputs. No randomness, no network, no Streamlit. Empty inputs → all-zero result
(never raises).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from data.quality import DataSource

# ---------------------------------------------------------------------------
# Curated, DATED tariff policy table
# ---------------------------------------------------------------------------
# Effective-as-of date for the curated US→China tariff schedule below. The
# regime is volatile; this stamps the snapshot so the UI can show provenance.
TARIFF_POLICY_AS_OF = datetime(2025, 5, 12, tzinfo=timezone.utc)

# Curated US tariff rate on Chinese goods, by HS cargo category (the same
# category keys used across the platform — see cargo_analyzer.HS_CATEGORIES).
#
# These are the announced, stacked ad-valorem rates on most Chinese-origin
# goods under the 2025 regime: the prior Section-301 lists + the IEEPA
# "reciprocal" tariff, after the mid-May 2025 US–China 90-day de-escalation
# that cut the reciprocal component from 125% to 10% (combined headline ≈ 30%
# baseline, higher where Section-301/232 stack). Rates are expressed as a
# fraction (0.30 = 30%). Curated from the USTR Section-301 schedule + the May
# 2025 Geneva joint-statement tariff levels — NOT fabricated. Per-category
# variation reflects stacked Section-232 (steel/aluminium) and the heavier
# legacy Section-301 lists on electronics/machinery.
#
# DATED CURATED POLICY DATA — see TARIFF_POLICY_AS_OF.
US_CHINA_TARIFF_RATES_2025: dict[str, float] = {
    "electronics": 0.45,   # Section-301 List-3/4 + reciprocal; semis stacked
    "machinery":   0.40,   # Section-301 List-3 capital goods + reciprocal
    "automotive":  0.55,   # Section-232 autos/parts stack on top of reciprocal
    "apparel":     0.40,   # List-4A consumer goods + reciprocal
    "chemicals":   0.35,   # List-3 intermediate chemicals + reciprocal
    "agriculture": 0.30,   # baseline reciprocal (less Section-301 history)
    "metals":      0.55,   # Section-232 steel/aluminium stack on reciprocal
}

# Single documented base trade value: total US goods imports from China.
# US Census 2024 goods imports from China were ≈ $438.9B. Rounded to a
# documented round figure for the share→dollar conversion. As-of 2024 full year.
# Source: US Census Bureau, "Trade in Goods with China" (2024 annual).
US_IMPORTS_FROM_CHINA_GOODS_BN: float = 439.0

# Diversion elasticity: the fraction of the directly-tariffed flow that is
# ASSUMED to re-route to alternative low/zero-tariff origins (Vietnam, Mexico,
# India, …) per unit of effective tariff, rather than disappearing. A tariff of
# rate ``r`` diverts ``min(1, r * DIVERSION_ELASTICITY)`` of the exposed flow.
#
# ASSUMED structural parameter — conservative, documented, NOT fitted/measured.
# At r=0.45 this diverts ~54% of the flow; the rest is demand destruction /
# price pass-through that stays bearish for the direct lane. This bounds
# diversion at 100% of the exposed flow so it can never exceed the value at risk.
DIVERSION_ELASTICITY: float = 1.2

# Alternative origins that absorb diverted flow — the bullish diversion lanes.
# Documentation only; the per-ticker bullish/bearish split is computed from the
# diverted-vs-direct dollar magnitudes, not from this list.
DIVERSION_ORIGINS: tuple[str, ...] = ("Vietnam", "Mexico", "India", "Bangladesh", "Indonesia")


TARIFF_POLICY_SOURCE = DataSource(
    name="USTR Section-301 + May-2025 US–China tariff schedule",
    kind="manual",
    quality="modeled",
    as_of=TARIFF_POLICY_AS_OF,
    notes=(
        "Curated policy data (as of 2025-05-12). Ad-valorem rates are real "
        "announced US tariffs on Chinese goods; trade-value-at-risk, diversion "
        "and per-ticker exposure are MODELED from those rates × China export "
        "shares × a documented base trade value. Not investment advice."
    ),
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommodityTariffImpact:
    """Per-commodity tariff impact — the dollar value at risk and its split
    between flow that diverts to alternative origins vs. flow exposed directly.
    """

    hs_category: str            # "electronics", "machinery", ...
    category_label: str         # "Electronics", "Machinery", ...
    tariff_rate: float          # ad-valorem fraction applied (0.45 = 45%)
    trade_share: float          # this category's share of the base flow [0,1]
    trade_value_bn: float       # dollar flow in this category (share × base)
    value_at_risk_bn: float     # tariff_rate × trade_value_bn
    diverted_bn: float          # part of VaR that re-routes to alt origins
    direct_exposure_bn: float   # part of VaR that stays bearish on the lane


@dataclass(frozen=True)
class TickerTariffExposure:
    """Per-shipping-equity tariff exposure, rolled up from commodity VaR via the
    company↔commodity weight matrix.
    """

    ticker: str
    direct_exposure_bn: float   # weighted bearish (direct-lane) exposure
    diversion_upside_bn: float  # weighted bullish (diversion-lane) exposure
    net_bn: float               # diversion_upside − direct_exposure
    direction: str              # "Bullish" | "Bearish" | "Neutral"


@dataclass(frozen=True)
class TariffImpact:
    """Full output of :func:`compute_tariff_impact`."""

    commodities: list[CommodityTariffImpact] = field(default_factory=list)
    tickers: list[TickerTariffExposure] = field(default_factory=list)
    total_trade_value_bn: float = 0.0     # COMPUTED total exposed flow
    total_burden_bn: float = 0.0          # COMPUTED Σ value-at-risk (the de-mock)
    total_diverted_bn: float = 0.0        # COMPUTED Σ diverted
    total_direct_bn: float = 0.0          # COMPUTED Σ direct-exposure
    source: DataSource = field(default=TARIFF_POLICY_SOURCE)

    @property
    def is_empty(self) -> bool:
        return not self.commodities


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _direction(net_bn: float, dead_band_bn: float) -> str:
    """Classify a net per-ticker exposure. ``dead_band_bn`` keeps tiny nets
    Neutral so a near-zero rounding residue does not flip a sign."""
    if net_bn > dead_band_bn:
        return "Bullish"
    if net_bn < -dead_band_bn:
        return "Bearish"
    return "Neutral"


def compute_tariff_impact(
    tariff_rates: dict[str, float],
    country_category_shares: dict[str, float],
    exposure_map: dict[str, dict[str, float]],
    *,
    base_trade_value_bn: float = US_IMPORTS_FROM_CHINA_GOODS_BN,
    diversion_elasticity: float = DIVERSION_ELASTICITY,
    category_labels: dict[str, str] | None = None,
    source: DataSource = TARIFF_POLICY_SOURCE,
) -> TariffImpact:
    """Compute the tariff pass-through + trade-diversion impact.

    Parameters
    ----------
    tariff_rates:
        ``{hs_category: ad-valorem fraction}`` — e.g. ``{"electronics": 0.45}``.
        Use :data:`US_CHINA_TARIFF_RATES_2025` for the curated regime.
    country_category_shares:
        ``{hs_category: share}`` of the base flow carried by each category, e.g.
        China's export mix from ``comtrade_feed._COUNTRY_CATEGORY_SHARES["CN"]``.
        Shares are normalised internally so they need only be proportional.
    exposure_map:
        ``{ticker: {hs_category: weight}}`` — the dense company↔commodity matrix
        from ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE`` (each ticker's vector
        sums to ~1.0). Used to roll commodity VaR up to per-ticker exposure.
    base_trade_value_bn:
        Documented total trade flow ($bn) the shares apply to. Default is the
        curated US-imports-from-China figure.
    diversion_elasticity:
        Documented diversion sensitivity (see :data:`DIVERSION_ELASTICITY`).
    category_labels:
        Optional ``{hs_category: pretty label}``; defaults to title-case.
    source:
        Provenance stamp carried on the result.

    Returns
    -------
    TariffImpact
        Per-commodity VaR, per-ticker exposure, and COMPUTED totals. Empty or
        all-zero inputs yield an all-zero result (never raises).

    VaR FORMULA (per commodity)
    ---------------------------
        trade_value_bn   = base_trade_value_bn × normalised_share
        value_at_risk_bn = tariff_rate × trade_value_bn
        diverted_bn      = value_at_risk_bn × min(1, rate × elasticity)
        direct_bn        = value_at_risk_bn − diverted_bn

    The total burden is the COMPUTED Σ value_at_risk_bn — not a constant.
    """
    tariff_rates = tariff_rates or {}
    raw_shares = country_category_shares or {}
    exposure_map = exposure_map or {}
    labels = category_labels or {}

    # Categories we actually have a tariff rate for AND a trade share for.
    cats = [c for c in tariff_rates if c in raw_shares]

    # Normalise the shares over the categories in play so trade_value sums to
    # the base value across the tariffed categories (proportional input → exact
    # dollar split). Guard against an all-zero / empty share set.
    share_total = sum(max(0.0, float(raw_shares.get(c, 0.0))) for c in cats)

    commodities: list[CommodityTariffImpact] = []
    if cats and share_total > 0 and base_trade_value_bn > 0:
        for cat in cats:
            rate = max(0.0, float(tariff_rates.get(cat, 0.0)))
            share = max(0.0, float(raw_shares.get(cat, 0.0))) / share_total
            trade_value = base_trade_value_bn * share
            var = rate * trade_value
            # Diversion fraction is bounded at 1.0 so diverted ≤ VaR.
            divert_frac = min(1.0, max(0.0, rate * diversion_elasticity))
            diverted = var * divert_frac
            direct = var - diverted
            commodities.append(
                CommodityTariffImpact(
                    hs_category=cat,
                    category_label=labels.get(cat, cat.title()),
                    tariff_rate=rate,
                    trade_share=share,
                    trade_value_bn=trade_value,
                    value_at_risk_bn=var,
                    diverted_bn=diverted,
                    direct_exposure_bn=direct,
                )
            )

    # Sort commodities by burden, biggest first — stable presentation order.
    commodities.sort(key=lambda c: c.value_at_risk_bn, reverse=True)

    total_trade_value = sum(c.trade_value_bn for c in commodities)
    total_burden = sum(c.value_at_risk_bn for c in commodities)
    total_diverted = sum(c.diverted_bn for c in commodities)
    total_direct = sum(c.direct_exposure_bn for c in commodities)

    # ── Per-ticker roll-up ──────────────────────────────────────────────────
    # A company's exposure to the regime is the weight-blend of every
    # commodity's split: weight × direct = bearish (its lanes carry tariffed
    # cargo that is being suppressed); weight × diverted = bullish (the same
    # cargo re-routes onto lanes it can also serve). The net decides direction.
    by_cat_direct = {c.hs_category: c.direct_exposure_bn for c in commodities}
    by_cat_diverted = {c.hs_category: c.diverted_bn for c in commodities}

    # Dead-band for the direction call: 0.5% of the total burden, so only
    # negligible residues land Neutral. Falls back to a tiny absolute floor.
    dead_band = max(0.005 * total_burden, 1e-9)

    tickers: list[TickerTariffExposure] = []
    for ticker, weights in exposure_map.items():
        if not weights:
            continue
        direct = sum(
            float(weights.get(cat, 0.0)) * by_cat_direct.get(cat, 0.0)
            for cat in by_cat_direct
        )
        diverted = sum(
            float(weights.get(cat, 0.0)) * by_cat_diverted.get(cat, 0.0)
            for cat in by_cat_diverted
        )
        net = diverted - direct
        if direct == 0.0 and diverted == 0.0:
            continue
        tickers.append(
            TickerTariffExposure(
                ticker=ticker,
                direct_exposure_bn=direct,
                diversion_upside_bn=diverted,
                net_bn=net,
                direction=_direction(net, dead_band),
            )
        )

    # Most net-positive (diversion winners) first; ties by magnitude of swing.
    tickers.sort(key=lambda t: t.net_bn, reverse=True)

    return TariffImpact(
        commodities=commodities,
        tickers=tickers,
        total_trade_value_bn=total_trade_value,
        total_burden_bn=total_burden,
        total_diverted_bn=total_diverted,
        total_direct_bn=total_direct,
        source=source,
    )


def compute_default_tariff_impact() -> TariffImpact:
    """Convenience: run :func:`compute_tariff_impact` on the curated US→China
    regime, China's export mix, and the platform's company exposure matrix.

    Imports the data dependencies lazily so importing this module stays cheap
    and side-effect-free (exposure_matrix builds its matrix at import).
    """
    from data.comtrade_feed import _COUNTRY_CATEGORY_SHARES, _category_shares_for_country
    from processing.cargo_analyzer import HS_CATEGORIES
    from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE

    # China's normalised export-category mix is the diverting flow's composition.
    cn_shares = (
        _category_shares_for_country("CN")
        if "CN" in _COUNTRY_CATEGORY_SHARES
        else dict(_COUNTRY_CATEGORY_SHARES.get("CN", {}))
    )
    labels = {k: v.get("label", k.title()) for k, v in HS_CATEGORIES.items()}

    return compute_tariff_impact(
        US_CHINA_TARIFF_RATES_2025,
        cn_shares,
        COMPANY_COMMODITY_EXPOSURE,
        category_labels=labels,
    )
