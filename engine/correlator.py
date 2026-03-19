from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from loguru import logger

try:
    from scipy.stats import pearsonr as _pearsonr
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False
    logger.warning("scipy not available; correlation analysis disabled")


@dataclass
class CorrelationResult:
    stock: str
    signal: str              # e.g. "BDIY", "FBX01_rate", "congestion_avg"
    lag_days: int            # 0 = contemporaneous, positive = signal leads stock
    pearson_r: float
    p_value: float
    direction: str           # "positive" | "negative"
    interpretation: str
    n_observations: int


class ShippingStockCorrelator:
    """Tests whether shipping signals have statistically significant
    correlations with stock prices. Only surfaces results that clear
    both magnitude (|r| >= min_abs_r) and statistical significance (p < 0.05).

    If nothing clears the thresholds, returns an empty list — the UI will
    display an informational message rather than forcing a connection.
    """

    def __init__(
        self,
        min_window: int = 60,
        min_abs_r: float = 0.40,
        lags_to_test: list[int] | None = None,
    ) -> None:
        self.min_window = min_window
        self.min_abs_r = min_abs_r
        self.lags_to_test = lags_to_test or [0, 7, 14, 21, 30]

    def analyze(
        self,
        stock_data: dict[str, pd.DataFrame],
        macro_data: dict[str, pd.DataFrame],
        freight_data: dict[str, pd.DataFrame] | None = None,
    ) -> list[CorrelationResult]:
        """Run correlation analysis across all (stock, signal, lag) combinations.

        Returns:
            List of CorrelationResult where |r| >= min_abs_r AND p < 0.05.
            Empty list if no significant correlations found.
        """
        if not _SCIPY_AVAILABLE:
            logger.warning("scipy missing — skipping correlation analysis")
            return []

        if not stock_data or not macro_data:
            return []

        # Build shipping signal DataFrame (date-indexed)
        signal_df = self._build_signal_df(macro_data, freight_data, stock_data)
        if signal_df.empty:
            logger.warning("No shipping signals available for correlation")
            return []

        results: list[CorrelationResult] = []

        for symbol, stock_df in stock_data.items():
            if stock_df.empty:
                continue
            stock_series = stock_df.set_index("date")["close"]

            for signal_col in signal_df.columns:
                signal_series = signal_df[signal_col]

                best = self._find_best_lag(symbol, signal_col, signal_series, stock_series)
                if best:
                    results.append(best)

        # Sort by |r| descending
        results.sort(key=lambda x: abs(x.pearson_r), reverse=True)

        if results:
            logger.info(f"Correlator: {len(results)} significant correlations found")
        else:
            logger.info("Correlator: no significant correlations found (|r| threshold not met)")

        return results

    def _find_best_lag(
        self,
        symbol: str,
        signal_col: str,
        signal_series: pd.Series,
        stock_series: pd.Series,
    ) -> CorrelationResult | None:
        """Test all lags and return the best (highest |r|) that clears thresholds."""
        best_r = 0.0
        best_result: CorrelationResult | None = None

        for lag in self.lags_to_test:
            r, p, n = self._compute_correlation(signal_series, stock_series, lag)

            if n < self.min_window:
                continue
            if abs(r) < self.min_abs_r:
                continue
            if p >= 0.05:
                continue

            if abs(r) > abs(best_r):
                best_r = r
                best_result = CorrelationResult(
                    stock=symbol,
                    signal=signal_col,
                    lag_days=lag,
                    pearson_r=r,
                    p_value=p,
                    direction="positive" if r > 0 else "negative",
                    interpretation=self._interpret(symbol, signal_col, r, lag),
                    n_observations=n,
                )

        return best_result

    def _compute_correlation(
        self,
        signal: pd.Series,
        stock: pd.Series,
        lag: int,
    ) -> tuple[float, float, int]:
        """Compute Pearson r with lag alignment. Returns (r, p_value, n)."""
        try:
            # Align on date index
            if lag > 0:
                # Signal leads stock: shift stock forward by lag
                stock_lagged = stock.shift(-lag)
            else:
                stock_lagged = stock

            combined = pd.concat([signal, stock_lagged], axis=1).dropna()
            combined.columns = ["signal", "stock"]

            if len(combined) < self.min_window:
                return 0.0, 1.0, len(combined)

            r, p = _pearsonr(combined["signal"], combined["stock"])
            return float(r), float(p), len(combined)

        except Exception as exc:
            logger.debug(f"Correlation computation failed ({signal.name}, {stock.name}, lag={lag}): {exc}")
            return 0.0, 1.0, 0

    def _build_signal_df(
        self,
        macro_data: dict[str, pd.DataFrame],
        freight_data: dict[str, pd.DataFrame] | None,
        stock_data: dict[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        """Build a date-indexed DataFrame of all shipping signals."""
        series_dict: dict[str, pd.Series] = {}

        # Baltic Dry Index
        bdi_df = macro_data.get("BDIY")
        if bdi_df is not None and not bdi_df.empty:
            series_dict["BDI"] = bdi_df.set_index("date")["value"]

        # US Imports
        imp_df = macro_data.get("XTIMVA01USM667S")
        if imp_df is not None and not imp_df.empty:
            series_dict["US_Imports"] = imp_df.set_index("date")["value"]

        # US Exports
        exp_df = macro_data.get("XTEXVA01USM667S")
        if exp_df is not None and not exp_df.empty:
            series_dict["US_Exports"] = exp_df.set_index("date")["value"]

        # Freight PPI
        ppi_df = macro_data.get("WPU101")
        if ppi_df is not None and not ppi_df.empty:
            series_dict["Freight_PPI"] = ppi_df.set_index("date")["value"]

        # Industrial Production
        ip_df = macro_data.get("IPMAN")
        if ip_df is not None and not ip_df.empty:
            series_dict["Industrial_Production"] = ip_df.set_index("date")["value"]

        # FBX01 Trans-Pacific freight rates
        if freight_data:
            tp_df = freight_data.get("transpacific_eb")
            if tp_df is not None and not tp_df.empty and tp_df["source"].iloc[-1] != "fallback":
                series_dict["FBX01_Rate"] = tp_df.set_index("date")["rate_usd_per_feu"]

        # Commodity ETF signals (from stock_data — they ARE price signals)
        commodity_etfs = ["DBA", "DBB", "USO", "XLB"]
        if stock_data:
            for etf in commodity_etfs:
                etf_df = stock_data.get(etf)
                if etf_df is not None and not etf_df.empty:
                    series_dict[f"Commodity_{etf}"] = etf_df.set_index("date")["close"]

        if not series_dict:
            return pd.DataFrame()

        df = pd.DataFrame(series_dict)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df

    def _interpret(self, stock: str, signal: str, r: float, lag: int) -> str:
        """Generate a human-readable interpretation of a correlation."""
        direction = "positively" if r > 0 else "inversely"
        strength = "strongly" if abs(r) > 0.60 else "moderately"

        signal_labels = {
            "BDI": "Baltic Dry Index",
            "US_Imports": "US import value",
            "US_Exports": "US export value",
            "Freight_PPI": "freight price index",
            "Industrial_Production": "industrial production",
            "FBX01_Rate": "Trans-Pacific freight rates",
            "Commodity_DBA": "agriculture commodity prices",
            "Commodity_DBB": "base metals prices",
            "Commodity_USO": "oil prices",
            "Commodity_XLB": "materials sector",
        }
        signal_label = signal_labels.get(signal, signal)

        if lag == 0:
            timing = "contemporaneously"
        elif lag > 0:
            timing = f"with {lag}-day lag (signal may lead stock)"
        else:
            timing = f"with {abs(lag)}-day lead (stock anticipates signal)"

        return f"{stock} is {strength} {direction} correlated with {signal_label} {timing} (r={r:.2f})"


def build_correlation_heatmap_data(
    results: list[CorrelationResult],
    all_stocks: list[str],
    all_signals: list[str],
) -> pd.DataFrame:
    """Build a pivot DataFrame suitable for a Plotly heatmap.

    Rows = signals, Columns = stocks, Values = pearson_r (best lag per pair).
    Returns DataFrame of zeros where no significant correlation exists.
    """
    import numpy as np

    matrix = pd.DataFrame(0.0, index=all_signals, columns=all_stocks)
    for result in results:
        if result.signal in matrix.index and result.stock in matrix.columns:
            matrix.loc[result.signal, result.stock] = result.pearson_r

    return matrix
