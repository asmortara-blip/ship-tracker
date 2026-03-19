from __future__ import annotations

import pandas as pd
from loguru import logger

from engine.signals import SignalComponent, direction_from_score
from engine.insight import Insight, make_insight


# Stock suggestions per insight category
_PORT_STOCKS = {
    "Asia East": ["ZIM", "MATX", "SBLK"],
    "North America West": ["MATX", "ZIM"],
    "North America East": ["ZIM", "CMRE"],
    "Europe": ["CMRE", "DAC"],
    "Southeast Asia": ["SBLK", "DAC"],
}

_ROUTE_STOCKS = {
    "transpacific_eb": ["MATX", "ZIM"],
    "transpacific_wb": ["MATX", "ZIM"],
    "asia_europe": ["ZIM", "CMRE", "DAC"],
    "transatlantic": ["CMRE", "DAC"],
}


class InsightScorer:
    """Core decision engine: generates and ranks Insight objects.

    Usage:
        scorer = InsightScorer(cfg)
        insights = scorer.score_all(port_results, route_results, macro_data, stock_data)
    """

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        engine_cfg = self.cfg.get("engine", {})
        self.high_threshold = engine_cfg.get("high_demand_threshold", 0.70)
        self.low_threshold = engine_cfg.get("low_demand_threshold", 0.35)
        self.min_score = engine_cfg.get("insight_min_score", 0.55)

    def score_all(
        self,
        port_results: list,
        route_results: list,
        macro_data: dict[str, pd.DataFrame],
        stock_data: dict[str, pd.DataFrame] | None = None,
    ) -> list[Insight]:
        """Generate all insights, sorted by score descending.

        Args:
            port_results: list[PortDemandResult] from demand_analyzer
            route_results: list[RouteOpportunity] from optimizer
            macro_data: dict series_id -> DataFrame from fred_feed
            stock_data: optional, used to flag relevant stocks

        Returns:
            list[Insight] sorted by score descending, max ~20 insights.
        """
        insights: list[Insight] = []

        # 1. Port demand insights
        for port in port_results:
            if port.demand_score >= self.high_threshold:
                insight = self._port_insight(port, "high")
                if insight:
                    insights.append(insight)
            elif port.demand_score <= self.low_threshold:
                insight = self._port_insight(port, "low")
                if insight:
                    insights.append(insight)

        # 2. Route insights
        for route in route_results:
            if route.opportunity_score >= self.min_score:
                insight = self._route_insight(route)
                if insight:
                    insights.append(insight)

        # 3. Macro insight
        macro_insight = self._macro_insight(macro_data)
        if macro_insight:
            insights.append(macro_insight)

        # 4. Convergence insights (highest value — multiple signals agree)
        convergence = self._detect_convergence(port_results, route_results, macro_data)
        insights.extend(convergence)

        # Deduplicate by title (convergence may overlap with individual insights)
        seen_titles: set[str] = set()
        deduped: list[Insight] = []
        for ins in insights:
            if ins.title not in seen_titles:
                seen_titles.add(ins.title)
                deduped.append(ins)

        deduped.sort(key=lambda x: x.score, reverse=True)
        result = deduped[:20]  # Cap at 20 insights

        logger.info(f"Decision engine: {len(result)} insights generated (top score: {result[0].score:.3f} if any)")
        return result

    # ------------------------------------------------------------------
    # Port insights
    # ------------------------------------------------------------------

    def _port_insight(self, port, direction: str) -> Insight | None:
        if not port.has_real_data:
            return None

        signals = [
            SignalComponent(
                name="Trade Flow",
                value=port.trade_flow_component,
                weight=0.40,
                label=f"${port.import_value_usd/1e9:.1f}B imports" if port.import_value_usd > 0 else "No import data",
                direction=direction_from_score(port.trade_flow_component),
            ),
            SignalComponent(
                name="Port Congestion",
                value=port.congestion_component,
                weight=0.35,
                label=f"{port.vessel_count} cargo vessels in zone",
                direction=direction_from_score(port.congestion_component),
            ),
            SignalComponent(
                name="Throughput",
                value=port.throughput_component,
                weight=0.25,
                label=f"{port.throughput_teu_m:.1f}M TEU/yr" if port.throughput_teu_m > 0 else "Throughput data unavailable",
                direction=direction_from_score(port.throughput_component),
            ),
        ]

        top_product = port.top_products[0]["category"] if port.top_products else "mixed cargo"

        if direction == "high":
            title = f"{port.port_name}: High demand — {top_product} dominant"
            detail = (
                f"{port.port_name} ({port.locode}) is showing elevated demand "
                f"(score {port.demand_score:.0%}), driven by {top_product} flows. "
                f"Trend is {port.demand_trend.lower()}. Consider routing inbound capacity here."
            )
            stocks = _PORT_STOCKS.get(port.region, ["ZIM", "MATX"])
        else:
            title = f"{port.port_name}: Low demand — potential slack capacity"
            detail = (
                f"{port.port_name} ({port.locode}) demand score is {port.demand_score:.0%}, "
                f"below the {self.low_threshold:.0%} threshold. "
                f"Trend is {port.demand_trend.lower()}. Lower rates or excess capacity likely."
            )
            stocks = []

        return make_insight(
            title=title,
            category="PORT_DEMAND",
            score=port.demand_score if direction == "high" else (1.0 - port.demand_score),
            detail=detail,
            signals=signals,
            ports=[port.locode],
            stocks=stocks,
        )

    # ------------------------------------------------------------------
    # Route insights
    # ------------------------------------------------------------------

    def _route_insight(self, route) -> Insight | None:
        signals = [
            SignalComponent(
                name="Rate Momentum",
                value=route.rate_momentum_component,
                weight=0.35,
                label=f"${route.current_rate_usd_feu:,.0f}/FEU ({route.rate_pct_change_30d*100:+.1f}% 30d)",
                direction=direction_from_score(route.rate_momentum_component),
            ),
            SignalComponent(
                name="Demand Imbalance",
                value=route.demand_imbalance_component,
                weight=0.30,
                label=f"Dest demand {route.dest_demand_score:.0%} vs origin congestion {route.origin_congestion:.0%}",
                direction=direction_from_score(route.demand_imbalance_component),
            ),
            SignalComponent(
                name="Origin Congestion",
                value=route.congestion_clearance_component,
                weight=0.20,
                label=f"{'Clear' if route.origin_congestion < 0.4 else 'Moderate' if route.origin_congestion < 0.65 else 'Congested'} departure port",
                direction=direction_from_score(route.congestion_clearance_component),
            ),
            SignalComponent(
                name="Macro Tailwind",
                value=route.macro_tailwind_component,
                weight=0.15,
                label="PMI / BDI environment",
                direction=direction_from_score(route.macro_tailwind_component),
            ),
        ]

        stocks = _ROUTE_STOCKS.get(route.route_id, ["ZIM"])

        return make_insight(
            title=f"{route.route_name}: {route.opportunity_label} opportunity",
            category="ROUTE",
            score=route.opportunity_score,
            detail=route.rationale,
            signals=signals,
            ports=[route.origin_locode, route.dest_locode],
            routes=[route.route_id],
            stocks=stocks,
        )

    # ------------------------------------------------------------------
    # Macro insight
    # ------------------------------------------------------------------

    def _macro_insight(self, macro_data: dict[str, pd.DataFrame]) -> Insight | None:
        from data.fred_feed import compute_bdi_score, get_latest_value

        bdi_score = compute_bdi_score(macro_data)

        # Industrial production proxy for PMI
        ipman_df = macro_data.get("IPMAN")
        if ipman_df is not None and not ipman_df.empty:
            vals = ipman_df["value"].dropna()
            current = vals.iloc[-1]
            avg = vals.tail(90).mean()
            pmi_proxy = min(1.0, max(0.0, (current / avg - 0.9) / 0.2)) if avg > 0 else 0.5
        else:
            pmi_proxy = 0.5

        wti_df = macro_data.get("DCOILWTICO")
        if wti_df is not None and not wti_df.empty:
            wti_val = float(wti_df["value"].dropna().iloc[-1])
            wti_norm = max(0.0, min(1.0, (wti_val - 40) / 80))
            fuel_inverse = 1.0 - wti_norm
            wti_label = f"WTI ${wti_val:.0f}/bbl"
        else:
            fuel_inverse = 0.5
            wti_label = "WTI unavailable"

        # Inventory cycle signal (most powerful leading indicator)
        from processing.inventory_analyzer import get_inventory_score_for_engine
        inventory_score = get_inventory_score_for_engine(macro_data)

        # Revised macro score with inventory cycle incorporated
        macro_score = 0.30 * pmi_proxy + 0.28 * bdi_score + 0.22 * fuel_inverse + 0.20 * inventory_score

        signals = [
            SignalComponent(
                name="Industrial Production",
                value=pmi_proxy,
                weight=0.30,
                label="Manufacturing activity vs 90d avg",
                direction=direction_from_score(pmi_proxy),
            ),
            SignalComponent(
                name="Baltic Dry Index",
                value=bdi_score,
                weight=0.28,
                label="BDI vs 90d rolling average",
                direction=direction_from_score(bdi_score),
            ),
            SignalComponent(
                name="Fuel Cost",
                value=fuel_inverse,
                weight=0.22,
                label=wti_label,
                direction=direction_from_score(fuel_inverse),
            ),
            SignalComponent(
                name="Inventory Cycle",
                value=inventory_score,
                weight=0.20,
                label=f"I:S ratio phase — inventory restocking signal",
                direction=direction_from_score(inventory_score),
            ),
        ]

        if macro_score < 0.40:
            title = "Macro: Headwinds for shipping — weak industrial demand"
            detail = "BDI and industrial production signals suggest below-average shipping demand environment. Freight margins may compress."
        elif macro_score > 0.65:
            title = "Macro: Tailwinds for shipping — strong industrial activity"
            detail = "BDI above average with positive industrial production trend. Supportive environment for freight rates and shipping stocks."
        else:
            title = "Macro: Neutral shipping environment"
            detail = "Mixed macro signals — BDI and industrial data near average. No strong directional bias."

        if macro_score < 0.40 or macro_score > 0.55:
            return make_insight(
                title=title,
                category="MACRO",
                score=macro_score,
                detail=detail,
                signals=signals,
                stocks=["SBLK", "DAC", "ZIM"] if macro_score > 0.55 else [],
            )
        return None  # Near-neutral macro: not worth surfacing

    # ------------------------------------------------------------------
    # Convergence detection
    # ------------------------------------------------------------------

    def _detect_convergence(
        self,
        port_results: list,
        route_results: list,
        macro_data: dict[str, pd.DataFrame],
    ) -> list[Insight]:
        """Detect when port demand + route opportunity + macro all agree.

        These are the highest-value insights: multiple independent signals
        pointing the same direction significantly raises confidence.
        """
        from data.fred_feed import compute_bdi_score

        macro_score = 0.5  # neutral default
        bdi = compute_bdi_score(macro_data)
        # Simple macro proxy: if BDI is strong, macro is supportive
        macro_score = bdi

        convergence_insights: list[Insight] = []

        for route in route_results:
            # Find destination port demand
            dest_port = next(
                (p for p in port_results if p.locode == route.dest_locode),
                None
            )
            if dest_port is None:
                continue

            dest_demand = dest_port.demand_score
            route_score = route.opportunity_score

            # All three must agree: dest demand high + route opportunity + macro supportive
            if (dest_demand >= 0.65
                    and route_score >= 0.55
                    and macro_score >= 0.50):

                # Convergence score = weighted avg with +0.10 multi-signal boost
                raw_score = (0.40 * dest_demand + 0.35 * route_score + 0.25 * macro_score)
                convergence_score = min(1.0, raw_score + 0.10)

                signals = [
                    SignalComponent(
                        name=f"{dest_port.port_name} Demand",
                        value=dest_demand,
                        weight=0.40,
                        label=f"Destination demand {dest_demand:.0%}",
                        direction=direction_from_score(dest_demand),
                    ),
                    SignalComponent(
                        name=f"{route.route_name} Opportunity",
                        value=route_score,
                        weight=0.35,
                        label=route.rationale[:80] + "..." if len(route.rationale) > 80 else route.rationale,
                        direction=direction_from_score(route_score),
                    ),
                    SignalComponent(
                        name="Macro Environment",
                        value=macro_score,
                        weight=0.25,
                        label="BDI and industrial production supportive",
                        direction=direction_from_score(macro_score),
                    ),
                ]

                stocks = _ROUTE_STOCKS.get(route.route_id, ["ZIM", "MATX"])

                detail = (
                    f"CONVERGENCE: {route.route_name} shows aligned bullish signals. "
                    f"{dest_port.port_name} demand is {dest_demand:.0%}, "
                    f"route opportunity score is {route_score:.0%}, "
                    f"and macro environment is supportive (BDI {macro_score:.0%} vs avg). "
                    f"Multi-signal confirmation raises confidence significantly."
                )

                convergence_insights.append(make_insight(
                    title=f"CONVERGENCE: Load at {route.origin_locode} -> {route.dest_locode} now",
                    category="CONVERGENCE",
                    score=convergence_score,
                    detail=detail,
                    signals=signals,
                    ports=[route.origin_locode, route.dest_locode],
                    routes=[route.route_id],
                    stocks=stocks,
                ))
                logger.info(f"Convergence detected: {route.route_id} (score={convergence_score:.3f})")

        return convergence_insights
