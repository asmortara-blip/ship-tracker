"""narration_engine.py — AI-style insight narration system.

Generates Bloomberg/Goldman Sachs-quality written market commentary from raw
shipping data signals. Entirely rule-based — no external API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from engine.insight import Insight
    from ports.demand_analyzer import PortDemandResult
    from routes.optimizer import RouteOpportunity


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pct(val: float) -> str:
    """Format a decimal fraction as a percentage string, e.g. 0.123 -> '+12.3%'."""
    sign = "+" if val >= 0 else ""
    return sign + "{:.1f}%".format(val * 100)


def _rate(val: float) -> str:
    """Format a rate as $X,XXX/FEU."""
    return "${:,.0f}/FEU".format(val)


def _usd_billions(val: float) -> str:
    """Format USD value in billions."""
    return "${:.1f}B".format(val / 1e9)


def _now_date() -> str:
    """Return today as 'Month DD, YYYY' string."""
    return datetime.now(timezone.utc).strftime("%B %d, %Y")


def _bdi_current_and_change(macro_data: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """Return (current_bdi_value, pct_change_30d) or (0.0, 0.0) if unavailable."""
    bdi_df = macro_data.get("BDI") or macro_data.get("bdi")
    if bdi_df is None or bdi_df.empty:
        return 0.0, 0.0
    try:
        col = "value" if "value" in bdi_df.columns else bdi_df.columns[-1]
        vals = bdi_df.sort_values(
            "date" if "date" in bdi_df.columns else bdi_df.columns[0]
        )[col].dropna()
        if len(vals) < 2:
            current_only = float(vals.iloc[-1]) if len(vals) == 1 else 0.0
            return current_only, 0.0
        current = float(vals.iloc[-1])
        past = float(vals.iloc[-31]) if len(vals) >= 31 else float(vals.iloc[0])
        pct = (current - past) / past if past != 0 else 0.0
        return current, pct
    except Exception:
        return 0.0, 0.0


def _wti_current(macro_data: dict[str, pd.DataFrame]) -> float:
    """Return latest WTI price, or 0.0 if unavailable."""
    wti_df = macro_data.get("DCOILWTICO")
    if wti_df is None or wti_df.empty:
        return 0.0
    try:
        vals = wti_df["value"].dropna()
        return float(vals.iloc[-1]) if not vals.empty else 0.0
    except Exception:
        return 0.0


def _ipman_pct_vs_avg(macro_data: dict[str, pd.DataFrame]) -> float:
    """Return industrial production vs 90-day average as a fraction, or 0.0."""
    df = macro_data.get("IPMAN")
    if df is None or df.empty:
        return 0.0
    try:
        vals = df["value"].dropna()
        current = float(vals.iloc[-1])
        avg = float(vals.tail(90).mean())
        return (current - avg) / avg if avg != 0 else 0.0
    except Exception:
        return 0.0


def _rate_vs_90d_avg(route_result, freight_data: dict[str, pd.DataFrame]) -> float:
    """Return (current_rate / 90d_avg - 1) for a route, or 0.0."""
    route_id = getattr(route_result, "route_id", None)
    if route_id is None:
        return 0.0
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return 0.0
    try:
        vals = df.sort_values("date")["rate_usd_per_feu"].dropna()
        if len(vals) < 2:
            return 0.0
        current = float(vals.iloc[-1])
        avg = float(vals.tail(90).mean())
        return (current - avg) / avg if avg != 0 else 0.0
    except Exception:
        return 0.0


def _score_to_sentiment(score: float) -> str:
    """Map a [0,1] composite score to BULLISH/BEARISH/NEUTRAL/MIXED."""
    if score >= 0.65:
        return "BULLISH"
    if score <= 0.35:
        return "BEARISH"
    return "NEUTRAL"


def _score_to_sentiment_float(score: float) -> float:
    """Map a [0,1] score to [-1, 1] float."""
    return (score - 0.5) * 2.0


# ---------------------------------------------------------------------------
# NarrationEngine
# ---------------------------------------------------------------------------

class NarrationEngine:
    """Generates Bloomberg-quality written commentary from shipping data signals.

    All methods are rule-based string construction — no external API calls.
    """

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate_market_brief(
        self,
        port_results: list,
        route_results: list,
        insights: list,
        macro_data: dict[str, pd.DataFrame],
        freight_data: dict[str, pd.DataFrame],
    ) -> str:
        """Generate a 3-5 paragraph executive summary.

        Styled in the voice of a Goldman Sachs shipping analyst. Uses real
        data values to produce specific, quantitative commentary.

        Args:
            port_results:  List of PortDemandResult objects.
            route_results: List of RouteOpportunity objects.
            insights:      List of Insight objects from the decision engine.
            macro_data:    Dict of FRED series DataFrames.
            freight_data:  Dict of route_id -> freight rate DataFrames.

        Returns:
            Multi-paragraph narrative string.
        """
        logger.info("NarrationEngine: generating market brief")

        paragraphs: list[str] = []

        # --- Paragraph 1: Macro backdrop ---
        paragraphs.append(self._macro_paragraph(macro_data))

        # --- Paragraph 2: Top route commentary ---
        if route_results:
            paragraphs.append(self._routes_paragraph(route_results, freight_data))

        # --- Paragraph 3: Port demand landscape ---
        if port_results:
            paragraphs.append(self._ports_paragraph(port_results))

        # --- Paragraph 4: Insight synthesis / convergence ---
        synth = self._insights_paragraph(insights, route_results, freight_data)
        if synth:
            paragraphs.append(synth)

        # --- Paragraph 5: Forward-looking conclusion ---
        paragraphs.append(self._outlook_paragraph(port_results, route_results, macro_data))

        brief = "\n\n".join(paragraphs)
        logger.debug("Market brief generated ({} paragraphs)".format(len(paragraphs)))
        return brief

    # -----------------------------------------------------------------------

    def generate_route_narrative(
        self,
        route_result,
        freight_data: dict[str, pd.DataFrame],
    ) -> str:
        """Generate a 2-3 sentence narrative for a single route.

        Covers specific rate levels, trend direction, and a trading implication.

        Args:
            route_result:  RouteOpportunity dataclass instance.
            freight_data:  Dict of rate DataFrames by route_id.

        Returns:
            Narrative string (2-3 sentences).
        """
        route_name = getattr(route_result, "route_name", "Unknown Route")
        current_rate = getattr(route_result, "current_rate_usd_feu", 0.0)
        rate_trend = getattr(route_result, "rate_trend", "Stable")
        pct_30d = getattr(route_result, "rate_pct_change_30d", 0.0)
        opp_score = getattr(route_result, "opportunity_score", 0.5)
        origin_congestion = getattr(route_result, "origin_congestion", 0.5)
        dest_demand = getattr(route_result, "dest_demand_score", 0.5)
        origin_locode = getattr(route_result, "origin_locode", "")
        dest_locode = getattr(route_result, "dest_locode", "")
        transit_days = getattr(route_result, "transit_days", 0)

        vs_90d = _rate_vs_90d_avg(route_result, freight_data)

        # Sentence 1: rate level and trend
        if current_rate > 0:
            if abs(vs_90d) > 0.03:
                vs_label = "{} {}% above" if vs_90d > 0 else "{} {}% below"
                vs_str = ("{:.0f}% above".format(vs_90d * 100) if vs_90d > 0
                          else "{:.0f}% below".format(abs(vs_90d) * 100))
                s1 = (
                    "{} freight rates are currently at {}, {} the trailing 90-day average, "
                    "with 30-day momentum {}.".format(
                        route_name,
                        _rate(current_rate),
                        vs_str,
                        rate_trend.lower(),
                    )
                )
            else:
                s1 = (
                    "{} freight rates are holding near the 90-day average at {} "
                    "({} over the past 30 days).".format(
                        route_name, _rate(current_rate), _pct(pct_30d)
                    )
                )
        else:
            s1 = (
                "{} rate data is currently unavailable; the route remains under active monitoring.".format(
                    route_name
                )
            )

        # Sentence 2: demand / congestion context
        if dest_demand >= 0.70:
            demand_phrase = "strong destination demand ({:.0f}% demand score at {})".format(
                dest_demand * 100, dest_locode
            )
        elif dest_demand <= 0.35:
            demand_phrase = "soft destination demand ({:.0f}% demand score at {})".format(
                dest_demand * 100, dest_locode
            )
        else:
            demand_phrase = "moderate demand conditions at {}".format(dest_locode)

        if origin_congestion > 0.65:
            cong_phrase = "high loading-port congestion at {} ({:.0f}% congestion index)".format(
                origin_locode, origin_congestion * 100
            )
        elif origin_congestion < 0.35:
            cong_phrase = "clear loading conditions at {}".format(origin_locode)
        else:
            cong_phrase = "manageable congestion at {}".format(origin_locode)

        s2 = "The lane is characterised by {} and {}, with a typical {}-day transit.".format(
            demand_phrase, cong_phrase, transit_days
        )

        # Sentence 3: trading implication
        if opp_score >= 0.70:
            s3 = (
                "Given the elevated opportunity score of {:.0f}%, shippers should consider "
                "securing capacity on this lane in the near term; carriers positioned here "
                "are likely to benefit from continued rate strength.".format(opp_score * 100)
            )
        elif opp_score >= 0.50:
            s3 = (
                "With a moderate opportunity score of {:.0f}%, this route warrants monitoring "
                "for a more decisive rate signal before committing forward capacity.".format(
                    opp_score * 100
                )
            )
        else:
            s3 = (
                "The route's opportunity score of {:.0f}% suggests caution; shippers may find "
                "better value on alternative lanes while conditions remain weak.".format(
                    opp_score * 100
                )
            )

        narrative = " ".join([s1, s2, s3])
        logger.debug("Route narrative generated for {}".format(route_name))
        return narrative

    # -----------------------------------------------------------------------

    def generate_port_narrative(self, port_result) -> str:
        """Generate a 1-2 sentence narrative per port.

        Covers demand tier and key drivers.

        Args:
            port_result: PortDemandResult dataclass instance.

        Returns:
            Narrative string (1-2 sentences).
        """
        port_name = getattr(port_result, "port_name", "Unknown Port")
        locode = getattr(port_result, "locode", "")
        demand_score = getattr(port_result, "demand_score", 0.5)
        demand_label = getattr(port_result, "demand_label", "Moderate")
        demand_trend = getattr(port_result, "demand_trend", "Stable")
        congestion_index = getattr(port_result, "congestion_index", 0.5)
        import_value = getattr(port_result, "import_value_usd", 0.0)
        throughput_teu_m = getattr(port_result, "throughput_teu_m", 0.0)
        top_products = getattr(port_result, "top_products", [])
        vessel_count = getattr(port_result, "vessel_count", 0)

        # Demand tier phrase
        if demand_score >= 0.70:
            tier = "elevated demand ({:.0f}% composite score)".format(demand_score * 100)
        elif demand_score <= 0.35:
            tier = "depressed demand ({:.0f}% composite score)".format(demand_score * 100)
        else:
            tier = "moderate demand ({:.0f}% composite score)".format(demand_score * 100)

        # Key product driver
        if top_products:
            top_cat = top_products[0].get("category", "mixed cargo")
            top_val = top_products[0].get("value_usd", 0.0)
            product_phrase = "{} ({} in imports)".format(top_cat, _usd_billions(top_val)) if top_val > 1e8 else top_cat
        else:
            product_phrase = "mixed cargo"

        # Sentence 1: headline demand statement
        s1 = (
            "{} ({}) is registering {} driven by {}, with a {} trade trend.".format(
                port_name, locode, tier, product_phrase, demand_trend.lower()
            )
        )

        # Sentence 2: operational colour
        parts: list[str] = []
        if throughput_teu_m > 0:
            parts.append("annual throughput of {:.1f}M TEU".format(throughput_teu_m))
        if vessel_count > 0:
            parts.append("{} cargo vessels currently in zone".format(vessel_count))
        if congestion_index > 0.65:
            parts.append(
                "congestion running at {:.0f}% of baseline, creating potential 3-5 day delays".format(
                    congestion_index * 100
                )
            )
        elif congestion_index > 0.45:
            parts.append("moderate congestion ({:.0f}% index)".format(congestion_index * 100))

        if import_value > 1e9:
            parts.append("import flow of {}".format(_usd_billions(import_value)))

        if parts:
            s2 = "Key operational metrics include: {}.".format(", ".join(parts))
            narrative = " ".join([s1, s2])
        else:
            narrative = s1

        logger.debug("Port narrative generated for {}".format(port_name))
        return narrative

    # -----------------------------------------------------------------------

    def generate_scenario_commentary(
        self,
        scenario_name: str,
        scenario_result: dict,
    ) -> str:
        """Generate a plain-English 'what this means for you' explanation.

        Args:
            scenario_name:   Human-readable scenario label.
            scenario_result: Dict with scenario outcome metrics. Expected keys
                             (all optional): rate_change, demand_impact,
                             congestion_impact, affected_routes, revenue_impact,
                             risk_level, time_horizon, recommendation.

        Returns:
            Plain-English explanation string.
        """
        logger.debug("Generating scenario commentary for: {}".format(scenario_name))

        rate_change = scenario_result.get("rate_change", 0.0)
        demand_impact = scenario_result.get("demand_impact", 0.0)
        congestion_impact = scenario_result.get("congestion_impact", 0.0)
        affected_routes = scenario_result.get("affected_routes", [])
        revenue_impact = scenario_result.get("revenue_impact", 0.0)
        risk_level = scenario_result.get("risk_level", "MEDIUM")
        time_horizon = scenario_result.get("time_horizon", "near-term")
        recommendation = scenario_result.get("recommendation", "")

        parts: list[str] = []

        # Opening — what is this scenario
        parts.append(
            "Under the '{}' scenario, the following dynamics are projected over the {}:".format(
                scenario_name, time_horizon
            )
        )

        # Rate impact
        if abs(rate_change) > 0.01:
            direction = "rise" if rate_change > 0 else "fall"
            parts.append(
                "Freight rates are expected to {} by approximately {}, "
                "which {} shippers who have booked spot capacity.".format(
                    direction,
                    _pct(abs(rate_change)),
                    "disadvantages" if rate_change > 0 else "benefits",
                )
            )

        # Demand impact
        if abs(demand_impact) > 0.01:
            if demand_impact > 0:
                parts.append(
                    "Port demand is forecast to increase by {}, suggesting higher vessel utilisation "
                    "and tighter slot availability across key lanes.".format(_pct(demand_impact))
                )
            else:
                parts.append(
                    "Port demand is expected to contract by {}, which may free up capacity "
                    "and put downward pressure on rates.".format(_pct(abs(demand_impact)))
                )

        # Congestion impact
        if congestion_impact > 0.05:
            parts.append(
                "Port congestion is projected to worsen by roughly {}, "
                "adding an estimated {}-{} days to typical transit schedules.".format(
                    _pct(congestion_impact),
                    max(1, int(congestion_impact * 7)),
                    max(2, int(congestion_impact * 14)),
                )
            )
        elif congestion_impact < -0.05:
            parts.append(
                "Congestion relief of approximately {} is expected, which should "
                "reduce dwell times and improve port turnaround.".format(_pct(abs(congestion_impact)))
            )

        # Affected routes
        if affected_routes:
            route_list = ", ".join(str(r) for r in affected_routes[:4])
            suffix = " and others" if len(affected_routes) > 4 else ""
            parts.append(
                "The lanes most directly impacted are: {}{}.".format(route_list, suffix)
            )

        # Revenue / P&L impact
        if abs(revenue_impact) > 0.01:
            rev_dir = "upside" if revenue_impact > 0 else "downside"
            parts.append(
                "For carriers, this implies {} revenue exposure of {}, "
                "assuming current booking volumes hold.".format(rev_dir, _pct(abs(revenue_impact)))
            )

        # Risk level colour
        risk_map = {
            "LOW": "This is considered a low-risk scenario with limited tail outcomes.",
            "MEDIUM": "Risk is rated medium — outcomes could diverge meaningfully from the base case.",
            "HIGH": "This is a high-risk scenario; material deviations from consensus are possible.",
            "CRITICAL": (
                "Risk is rated critical. Operators should stress-test portfolios against "
                "this scenario immediately."
            ),
        }
        risk_sentence = risk_map.get(str(risk_level).upper(), "")
        if risk_sentence:
            parts.append(risk_sentence)

        # Recommendation
        if recommendation:
            parts.append("Recommended action: {}.".format(recommendation.rstrip(".")))

        commentary = " ".join(parts)
        return commentary

    # -----------------------------------------------------------------------

    def build_weekly_digest(
        self,
        port_results: list,
        route_results: list,
        insights: list,
        macro_data: dict[str, pd.DataFrame],
        freight_data: dict[str, pd.DataFrame],
    ) -> dict:
        """Build a structured weekly market digest dict.

        Returns:
            Dict with keys:
              headline         (str)   — one punchy sentence
              executive_summary (str)  — 3 paragraphs
              top_3_trades     (list)  — list of dicts: route, action, rationale, target_rate
              key_risks        (list)  — 3 risk strings
              sentiment        (str)   — BULLISH / BEARISH / NEUTRAL / MIXED
              sentiment_score  (float) — [-1, 1]
        """
        logger.info("NarrationEngine: building weekly digest")

        bdi_val, bdi_chg = _bdi_current_and_change(macro_data)
        wti = _wti_current(macro_data)
        ipman_vs_avg = _ipman_pct_vs_avg(macro_data)

        # --- Compute composite sentiment score ---
        sentiment_score = self._compute_sentiment_score(
            port_results, route_results, macro_data, bdi_chg, ipman_vs_avg
        )
        if sentiment_score >= 0.25:
            sentiment = "BULLISH"
        elif sentiment_score <= -0.25:
            sentiment = "BEARISH"
        elif abs(sentiment_score) < 0.10:
            sentiment = "NEUTRAL"
        else:
            sentiment = "MIXED"

        # --- Headline ---
        headline = self._build_headline(
            sentiment, bdi_val, bdi_chg, route_results, freight_data, wti
        )

        # --- Executive summary (3 paragraphs) ---
        exec_para1 = self._macro_paragraph(macro_data)
        exec_para2 = self._routes_paragraph(route_results, freight_data) if route_results else ""
        exec_para3 = self._ports_paragraph(port_results) if port_results else ""
        executive_summary = "\n\n".join(p for p in [exec_para1, exec_para2, exec_para3] if p)

        # --- Top 3 trades ---
        top_3_trades = self._derive_top_trades(route_results, freight_data, port_results)

        # --- Key risks ---
        key_risks = self._derive_key_risks(
            port_results, route_results, macro_data, bdi_chg, wti
        )

        digest = {
            "headline": headline,
            "executive_summary": executive_summary,
            "top_3_trades": top_3_trades,
            "key_risks": key_risks,
            "sentiment": sentiment,
            "sentiment_score": round(sentiment_score, 4),
        }

        logger.info(
            "Weekly digest built — sentiment: {} ({:.3f})".format(sentiment, sentiment_score)
        )
        return digest

    # -----------------------------------------------------------------------
    # Private paragraph builders
    # -----------------------------------------------------------------------

    def _macro_paragraph(self, macro_data: dict[str, pd.DataFrame]) -> str:
        """Construct macro backdrop paragraph."""
        bdi_val, bdi_chg = _bdi_current_and_change(macro_data)
        wti = _wti_current(macro_data)
        ipman_vs_avg = _ipman_pct_vs_avg(macro_data)
        date_str = _now_date()

        sentences: list[str] = []

        # BDI
        if bdi_val > 0:
            if bdi_chg > 0.10:
                sentences.append(
                    "The Baltic Dry Index surged {pct} over the past month to {val:,.0f} points "
                    "as of {date}, signalling broad-based demand acceleration across dry bulk "
                    "and container segments.".format(
                        pct=_pct(bdi_chg), val=bdi_val, date=date_str
                    )
                )
            elif bdi_chg > 0.03:
                sentences.append(
                    "The Baltic Dry Index edged higher by {pct} over the past month "
                    "to {val:,.0f}, reflecting a modest improvement in global cargo demand.".format(
                        pct=_pct(bdi_chg), val=bdi_val
                    )
                )
            elif bdi_chg < -0.10:
                sentences.append(
                    "The Baltic Dry Index declined {pct} over the past 30 days to {val:,.0f}, "
                    "flagging a deterioration in dry bulk and broader shipping demand that "
                    "warrants close monitoring.".format(
                        pct=_pct(abs(bdi_chg)), val=bdi_val
                    )
                )
            elif bdi_chg < -0.03:
                sentences.append(
                    "The Baltic Dry Index softened {pct} over the past month to {val:,.0f}, "
                    "suggesting mild demand headwinds in the dry bulk complex.".format(
                        pct=_pct(abs(bdi_chg)), val=bdi_val
                    )
                )
            else:
                sentences.append(
                    "The Baltic Dry Index is broadly stable at {val:,.0f} points, "
                    "with a 30-day change of just {pct}, consistent with a balanced supply/demand "
                    "environment.".format(val=bdi_val, pct=_pct(bdi_chg))
                )
        else:
            sentences.append(
                "Baltic Dry Index data is currently unavailable; macro assessment "
                "relies on industrial production and fuel cost proxies."
            )

        # Industrial production
        if abs(ipman_vs_avg) > 0.01:
            if ipman_vs_avg > 0.03:
                sentences.append(
                    "Manufacturing output is running {pct} above its 90-day average, "
                    "consistent with an inventory restocking cycle that historically "
                    "drives incremental container demand.".format(pct=_pct(ipman_vs_avg))
                )
            elif ipman_vs_avg < -0.03:
                sentences.append(
                    "Manufacturing output has slipped {pct} below its 90-day trend, "
                    "pointing to potential demand softness in the months ahead.".format(
                        pct=_pct(abs(ipman_vs_avg))
                    )
                )

        # WTI / fuel
        if wti > 0:
            if wti > 90:
                sentences.append(
                    "Crude oil at ${:.0f}/bbl represents a meaningful headwind to vessel "
                    "operating margins; bunker-intensive routes face the highest cost "
                    "exposure.".format(wti)
                )
            elif wti < 60:
                sentences.append(
                    "WTI crude at ${:.0f}/bbl provides a significant fuel-cost tailwind, "
                    "underpinning shipping margins across all major lanes.".format(wti)
                )
            else:
                sentences.append(
                    "Brent/WTI crude hovering near ${:.0f}/bbl presents a broadly neutral "
                    "impact on bunker costs.".format(wti)
                )

        return " ".join(sentences)

    def _routes_paragraph(
        self,
        route_results: list,
        freight_data: dict[str, pd.DataFrame],
    ) -> str:
        """Construct route rate commentary paragraph."""
        if not route_results:
            return ""

        top_routes = sorted(
            route_results, key=lambda r: getattr(r, "opportunity_score", 0.0), reverse=True
        )[:3]

        sentences: list[str] = [
            "Turning to individual trade lanes, the following dynamics are most material:"
        ]

        for route in top_routes:
            route_name = getattr(route, "route_name", "Unknown")
            current_rate = getattr(route, "current_rate_usd_feu", 0.0)
            pct_30d = getattr(route, "rate_pct_change_30d", 0.0)
            rate_trend = getattr(route, "rate_trend", "Stable")
            opp_score = getattr(route, "opportunity_score", 0.5)
            vs_90d = _rate_vs_90d_avg(route, freight_data)

            if current_rate > 0:
                rate_str = _rate(current_rate)
                if abs(vs_90d) > 0.05:
                    cmp = "{:.0f}% above".format(vs_90d * 100) if vs_90d > 0 else "{:.0f}% below".format(abs(vs_90d) * 100)
                    cmp_str = " ({} the 90-day average)".format(cmp)
                else:
                    cmp_str = " (near 90-day average)"
                sentences.append(
                    "{} rates stand at {}{}, with 30-day momentum at {} — "
                    "our composite opportunity score of {:.0f}% places this lane in "
                    "the '{}' tier.".format(
                        route_name,
                        rate_str,
                        cmp_str,
                        rate_trend.lower(),
                        opp_score * 100,
                        getattr(route, "opportunity_label", "Moderate"),
                    )
                )
            else:
                sentences.append(
                    "{} rate data is pending; the lane carries an opportunity "
                    "score of {:.0f}%.".format(route_name, opp_score * 100)
                )

        return " ".join(sentences)

    def _ports_paragraph(self, port_results: list) -> str:
        """Construct port demand overview paragraph."""
        if not port_results:
            return ""

        high_demand = [p for p in port_results if getattr(p, "demand_score", 0) >= 0.70]
        low_demand = [p for p in port_results if getattr(p, "demand_score", 0) <= 0.35]
        congested = [
            p for p in port_results
            if getattr(p, "congestion_index", 0) > 0.70
        ]

        sentences: list[str] = []

        if high_demand:
            names = ", ".join(
                "{} ({:.0f}%)".format(
                    getattr(p, "port_name", "?"), getattr(p, "demand_score", 0) * 100
                )
                for p in high_demand[:3]
            )
            sentences.append(
                "On the port demand front, {port_count} port{s} are registering "
                "high-demand conditions: {names}; these locations are likely to "
                "see elevated vessel queuing and booking pressure in the near "
                "term.".format(
                    port_count=len(high_demand),
                    s="s" if len(high_demand) != 1 else "",
                    names=names,
                )
            )

        if low_demand:
            names_low = ", ".join(
                getattr(p, "port_name", "?") for p in low_demand[:3]
            )
            sentences.append(
                "Conversely, {port_count} port{s} — including {names} — "
                "are exhibiting below-threshold demand, offering potential "
                "backhaul or redeployment opportunities.".format(
                    port_count=len(low_demand),
                    s="s" if len(low_demand) != 1 else "",
                    names=names_low,
                )
            )

        if congested:
            cong_names = ", ".join(
                "{} ({:.0f}% congestion index)".format(
                    getattr(p, "port_name", "?"), getattr(p, "congestion_index", 0) * 100
                )
                for p in congested[:2]
            )
            sentences.append(
                "Elevated congestion is flagged at {}: operators should build "
                "3-5 day schedule buffers and consider alternative call "
                "sequencing.".format(cong_names)
            )

        if not sentences:
            scored = sorted(
                port_results, key=lambda p: getattr(p, "demand_score", 0), reverse=True
            )
            if scored:
                top = scored[0]
                sentences.append(
                    "Port demand is broadly balanced; {} leads with a {:.0f}% demand "
                    "score.".format(
                        getattr(top, "port_name", "the top port"),
                        getattr(top, "demand_score", 0.5) * 100,
                    )
                )

        return " ".join(sentences)

    def _insights_paragraph(
        self,
        insights: list,
        route_results: list,
        freight_data: dict[str, pd.DataFrame],
    ) -> str:
        """Synthesise top insights into a narrative paragraph."""
        if not insights:
            return ""

        convergence = [
            i for i in insights
            if getattr(i, "category", "") == "CONVERGENCE"
        ]
        top_insight = max(insights, key=lambda i: getattr(i, "score", 0.0))
        top_score = getattr(top_insight, "score", 0.5)

        sentences: list[str] = []

        if convergence:
            c = convergence[0]
            sentences.append(
                "Notably, the decision engine has flagged a high-confidence CONVERGENCE signal "
                "(score {:.0f}%): {}.".format(
                    getattr(c, "score", 0.0) * 100,
                    getattr(c, "detail", "multiple bullish signals are aligned"),
                )
            )

        if top_score >= 0.70 and (not convergence or convergence[0] is not top_insight):
            sentences.append(
                "The highest-ranked individual insight — '{}' — carries a {:.0f}% confidence "
                "score; {} is the recommended action.".format(
                    getattr(top_insight, "title", "unnamed"),
                    top_score * 100,
                    getattr(top_insight, "action", "Monitor"),
                )
            )

        macro_insights = [
            i for i in insights if getattr(i, "category", "") == "MACRO"
        ]
        if macro_insights:
            mi = macro_insights[0]
            sentences.append(
                "Macro signals are characterised as: {}.".format(
                    getattr(mi, "detail", "neutral environment")
                )
            )

        return " ".join(sentences) if sentences else ""

    def _outlook_paragraph(
        self,
        port_results: list,
        route_results: list,
        macro_data: dict[str, pd.DataFrame],
    ) -> str:
        """Construct a forward-looking concluding paragraph."""
        bdi_val, bdi_chg = _bdi_current_and_change(macro_data)
        wti = _wti_current(macro_data)

        high_demand_count = sum(
            1 for p in port_results if getattr(p, "demand_score", 0) >= 0.65
        )
        strong_route_count = sum(
            1 for r in route_results if getattr(r, "opportunity_score", 0) >= 0.65
        )

        if high_demand_count >= 3 and strong_route_count >= 2:
            tone = (
                "The overall constellation of signals points to a constructive near-term "
                "freight environment. With {hd} ports in high-demand territory and {sr} routes "
                "scoring strong opportunity, shippers face increasing competition for available "
                "slots — forward bookings at current rate levels represent defensible value.".format(
                    hd=high_demand_count, sr=strong_route_count
                )
            )
        elif high_demand_count == 0 and strong_route_count == 0:
            tone = (
                "The aggregate signal picture is cautious. Demand across tracked ports and routes "
                "remains below threshold, suggesting that the spot market may continue to soften. "
                "Shippers are advised to leverage spot exposure opportunistically while deferring "
                "longer-term freight commitments."
            )
        else:
            tone = (
                "The market presents a mixed picture with selective pockets of strength. "
                "Shippers with flexible capacity should prioritise the highest-scoring lanes "
                "while maintaining optionality on routes where signals remain inconclusive."
            )

        # Risk tail
        risk_parts: list[str] = []
        if wti > 90:
            risk_parts.append("elevated energy costs")
        if bdi_chg < -0.15:
            risk_parts.append("accelerating BDI decline")
        if any(getattr(p, "congestion_index", 0) > 0.80 for p in port_results):
            risk_parts.append("severe port congestion at key nodes")

        if risk_parts:
            risk_str = ", ".join(risk_parts)
            tone += (
                " Primary downside risks to monitor: {}.".format(risk_str)
            )

        return tone

    # -----------------------------------------------------------------------
    # Digest helpers
    # -----------------------------------------------------------------------

    def _compute_sentiment_score(
        self,
        port_results: list,
        route_results: list,
        macro_data: dict[str, pd.DataFrame],
        bdi_chg: float,
        ipman_vs_avg: float,
    ) -> float:
        """Compute overall sentiment score in [-1, 1]."""
        scores: list[float] = []

        # BDI component
        if bdi_chg != 0.0:
            bdi_contrib = max(-1.0, min(1.0, bdi_chg * 5.0))
            scores.append(bdi_contrib)

        # Industrial production component
        if ipman_vs_avg != 0.0:
            ip_contrib = max(-1.0, min(1.0, ipman_vs_avg * 10.0))
            scores.append(ip_contrib)

        # Port demand average (centre on 0.5, scale to [-1,1])
        if port_results:
            avg_demand = sum(getattr(p, "demand_score", 0.5) for p in port_results) / len(port_results)
            scores.append((avg_demand - 0.5) * 2.0)

        # Route opportunity average
        if route_results:
            avg_opp = sum(getattr(r, "opportunity_score", 0.5) for r in route_results) / len(route_results)
            scores.append((avg_opp - 0.5) * 2.0)

        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _build_headline(
        self,
        sentiment: str,
        bdi_val: float,
        bdi_chg: float,
        route_results: list,
        freight_data: dict[str, pd.DataFrame],
        wti: float,
    ) -> str:
        """Build a punchy single-sentence headline."""
        # Leading data point for the headline
        top_route = None
        if route_results:
            top_route = max(route_results, key=lambda r: getattr(r, "opportunity_score", 0.0))

        if sentiment == "BULLISH":
            if bdi_val > 0 and bdi_chg > 0.05:
                return (
                    "Freight markets firm as BDI surges {pct} — "
                    "Trans-Pacific and Asia-Europe lanes tighten.".format(pct=_pct(bdi_chg))
                )
            if top_route and getattr(top_route, "current_rate_usd_feu", 0) > 3000:
                return (
                    "{route} rates hold above {rate} amid strengthening demand — "
                    "capacity constraints building.".format(
                        route=getattr(top_route, "route_name", "Key route"),
                        rate=_rate(getattr(top_route, "current_rate_usd_feu", 0)),
                    )
                )
            return "Global shipping demand accelerating — shippers face tighter slot availability ahead."

        if sentiment == "BEARISH":
            if bdi_val > 0 and bdi_chg < -0.05:
                return (
                    "BDI slides {pct} as demand softens — "
                    "freight rates under pressure across major lanes.".format(pct=_pct(abs(bdi_chg)))
                )
            if wti > 90:
                return (
                    "Shipping margins squeezed by ${:.0f}/bbl crude — "
                    "carriers face earnings headwinds.".format(wti)
                )
            return "Container market softens — spot rates retreating as demand signals weaken."

        if sentiment == "NEUTRAL":
            return (
                "Shipping markets broadly balanced — BDI flat, port demand mixed, "
                "no decisive directional signal."
            )

        # MIXED
        if top_route:
            return (
                "Mixed signals across freight markets — "
                "{route} leads on opportunity while broader demand remains uneven.".format(
                    route=getattr(top_route, "route_name", "top lane")
                )
            )
        return "Freight markets present mixed signals — selective lane strength amid macro uncertainty."

    def _derive_top_trades(
        self,
        route_results: list,
        freight_data: dict[str, pd.DataFrame],
        port_results: list,
    ) -> list[dict]:
        """Derive the top 3 actionable trade ideas."""
        if not route_results:
            return []

        sorted_routes = sorted(
            route_results, key=lambda r: getattr(r, "opportunity_score", 0.0), reverse=True
        )

        trades: list[dict] = []
        for route in sorted_routes[:3]:
            route_id = getattr(route, "route_id", "")
            route_name = getattr(route, "route_name", "Unknown")
            opp_score = getattr(route, "opportunity_score", 0.5)
            current_rate = getattr(route, "current_rate_usd_feu", 0.0)
            pct_30d = getattr(route, "rate_pct_change_30d", 0.0)
            rate_trend = getattr(route, "rate_trend", "Stable")
            dest_demand = getattr(route, "dest_demand_score", 0.5)

            # Action
            if opp_score >= 0.70:
                action = "BUY / Prioritize"
            elif opp_score >= 0.50:
                action = "MONITOR / Watch"
            else:
                action = "AVOID / Caution"

            # Target rate: current ± projected move based on trend
            if rate_trend == "Rising" and current_rate > 0:
                target_rate = current_rate * 1.08
            elif rate_trend == "Falling" and current_rate > 0:
                target_rate = current_rate * 0.93
            else:
                target_rate = current_rate

            # Rationale
            rationale_parts: list[str] = []
            if pct_30d > 0.05:
                rationale_parts.append("rates up {pct} over 30 days".format(pct=_pct(pct_30d)))
            elif pct_30d < -0.05:
                rationale_parts.append("rates down {pct} over 30 days".format(pct=_pct(abs(pct_30d))))
            if dest_demand >= 0.65:
                rationale_parts.append("strong destination demand ({:.0f}%)".format(dest_demand * 100))
            elif dest_demand <= 0.35:
                rationale_parts.append("weak destination demand ({:.0f}%)".format(dest_demand * 100))
            rationale_parts.append("opportunity score {:.0f}%".format(opp_score * 100))
            rationale = "; ".join(rationale_parts).capitalize() + "."

            trades.append({
                "route": route_name,
                "route_id": route_id,
                "action": action,
                "rationale": rationale,
                "target_rate": round(target_rate, 0),
                "current_rate": round(current_rate, 0),
                "opportunity_score": round(opp_score, 4),
            })

        return trades

    def _derive_key_risks(
        self,
        port_results: list,
        route_results: list,
        macro_data: dict[str, pd.DataFrame],
        bdi_chg: float,
        wti: float,
    ) -> list[str]:
        """Derive exactly 3 key risk strings."""
        risks: list[str] = []

        # Risk 1: Macro / BDI
        if bdi_chg < -0.10:
            risks.append(
                "BDI deterioration ({pct} over 30 days) signals potential demand recession in "
                "dry bulk; contagion into container rates is a tail risk.".format(
                    pct=_pct(abs(bdi_chg))
                )
            )
        elif wti > 90:
            risks.append(
                "Elevated crude oil at ${:.0f}/bbl could compress carrier margins and trigger "
                "bunker surcharge escalation across all major lanes.".format(wti)
            )
        else:
            risks.append(
                "Macro data divergence — if manufacturing output continues to soften, container "
                "demand could undershoot current rate forecasts by 5-10%."
            )

        # Risk 2: Port congestion
        heavily_congested = [
            p for p in port_results if getattr(p, "congestion_index", 0) > 0.75
        ]
        if heavily_congested:
            names = ", ".join(getattr(p, "port_name", "?") for p in heavily_congested[:2])
            risks.append(
                "Severe congestion at {} presents schedule integrity risk — "
                "3-7 day delays are plausible and could cascade into downstream "
                "port disruptions.".format(names)
            )
        else:
            risks.append(
                "Geopolitical disruption risk: any escalation affecting key chokepoints "
                "(Suez Canal, Strait of Malacca, Panama Canal) could divert significant "
                "capacity and cause abrupt rate spikes on alternative lanes."
            )

        # Risk 3: Route-level rate risk
        falling_routes = [
            r for r in route_results
            if getattr(r, "rate_trend", "") == "Falling"
            and getattr(r, "current_rate_usd_feu", 0) > 0
        ]
        if falling_routes:
            worst = min(falling_routes, key=lambda r: getattr(r, "rate_pct_change_30d", 0.0))
            risks.append(
                "{route} is showing the steepest rate decline ({pct} over 30 days); "
                "if this trend extends, carriers may need to blank sailings to "
                "rebalance supply.".format(
                    route=getattr(worst, "route_name", "The weakest route"),
                    pct=_pct(getattr(worst, "rate_pct_change_30d", 0.0)),
                )
            )
        else:
            risks.append(
                "Overcapacity risk: new vessel deliveries scheduled for the current year "
                "could exceed demand growth, placing structural downward pressure on "
                "spot rates through the back half of the year."
            )

        # Always return exactly 3
        return risks[:3]


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def generate_route_narrative(route_result, freight_data: dict) -> str:
    """Module-level convenience wrapper around NarrationEngine.generate_route_narrative."""
    return NarrationEngine().generate_route_narrative(route_result, freight_data)


def generate_port_narrative(port_result) -> str:
    """Module-level convenience wrapper around NarrationEngine.generate_port_narrative."""
    return NarrationEngine().generate_port_narrative(port_result)


def generate_scenario_commentary(scenario_name: str, scenario_result: dict) -> str:
    """Module-level convenience wrapper around NarrationEngine.generate_scenario_commentary."""
    return NarrationEngine().generate_scenario_commentary(scenario_name, scenario_result)
