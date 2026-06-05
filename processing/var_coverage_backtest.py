"""VaR-coverage backtest — the first risk number checked against realized P&L (R070).

Every one of the platform's 18 existing backtest adapters validates an analytic
against ANOTHER analytic (synthetic histories, sign-agreement between models);
none has ever checked a risk number against realized returns (DATA_PROVENANCE).
This is the minimum honesty gate before quoting a VaR: does the VaR band
actually cover realized P&L at its stated confidence?

It runs the textbook **Kupiec proportion-of-failures (POF)** coverage test:

1. Build the book's realized return series from REAL cached closes
   (``book_pnl.returns_panel`` — the same panel the Risk-Lab VaR + the coherent
   Stress-VaR use, now that the date-as-column cache shape is honoured).
2. Roll a 1-day VaR at the stated confidence over a trailing window and count
   how often the realized next-day return breached it.
3. Kupiec POF likelihood-ratio test: is the observed breach rate statistically
   consistent with the nominal ``1 - confidence``? A correctly-calibrated VaR
   breaches ~``1-confidence`` of the time; far fewer (too conservative) or far
   more (dangerously loose) both reject.

Honesty scope: this validates the VaR DISPERSION model — the loss-distribution
width on realized returns. The coherent Stress-VaR's forward cascade *shock* is
an illustrative scenario tilt and is NOT coverage-tested here (there is no
realized "the-model's-shock-happened" return to score it against). When no real
cached history is available (e.g. a headless CI box with an empty cache) the
backtest reports ``basis="insufficient"`` and does NOT fabricate a verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Default shipping universe to coverage-test (matches the Risk-Lab book).
_DEFAULT_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE", "STNG"]

# chi-square(1) critical value at 95% — Kupiec rejects above this.
_CHI2_1DOF_95 = 3.841458820694124


@dataclass
class VaRCoverageScorecard:
    """One VaR-coverage (Kupiec POF) run, normalised for the backtest report."""

    confidence: float
    method: str
    window: int
    n_observations: int          # out-of-sample days actually scored
    n_breaches: int
    breach_rate: float           # observed
    nominal_rate: float          # 1 - confidence
    kupiec_lr: float             # POF likelihood-ratio statistic
    kupiec_pvalue: float
    rejected: bool               # model rejected at 95% (LR > chi2_1dof_95)?
    well_calibrated: bool        # not rejected AND ran on real data
    basis: str                   # "real" | "insufficient"
    summary: str = ""
    tickers: list = field(default_factory=list)


def kupiec_pof(n_observations: int, n_breaches: int, nominal_rate: float) -> dict:
    """Kupiec proportion-of-failures likelihood-ratio test.

    Returns ``{"lr", "pvalue", "rejected"}``. Under H0 the VaR is correctly
    calibrated (breaches ~ Binomial(N, p)); a large LR rejects at 95%.
    The x=0 and x=N corners are taken at their analytic limits.
    """
    n = int(n_observations)
    x = int(n_breaches)
    p = float(nominal_rate)
    if n <= 0 or not (0.0 < p < 1.0) or x < 0 or x > n:
        return {"lr": 0.0, "pvalue": 1.0, "rejected": False}

    # log-likelihood under H0 (rate = p) and unrestricted (rate = x/N).
    ll_h0 = (n - x) * math.log(1.0 - p) + (x * math.log(p) if x > 0 else 0.0)
    pi = x / n
    if x == 0:
        ll_un = n * math.log(1.0 - pi) if pi < 1.0 else 0.0
    elif x == n:
        ll_un = x * math.log(pi)
    else:
        ll_un = (n - x) * math.log(1.0 - pi) + x * math.log(pi)

    lr = -2.0 * (ll_h0 - ll_un)
    lr = max(0.0, float(lr))   # numerical guard; LR is non-negative
    # chi-square(1) survival function = erfc(sqrt(lr/2)).
    pvalue = math.erfc(math.sqrt(lr / 2.0)) if lr > 0 else 1.0
    return {"lr": lr, "pvalue": float(pvalue), "rejected": lr > _CHI2_1DOF_95}


def _rolling_var_breaches(
    port_returns: pd.Series, *, confidence: float, window: int, method: str,
) -> tuple:
    """Roll a 1-day VaR over a trailing window; count realized breaches.

    Returns ``(n_observations, n_breaches)`` over the out-of-sample span
    (every day after the first full window). A breach is a realized return
    strictly below that day's VaR estimate (VaR is a negative return).
    """
    r = pd.to_numeric(port_returns, errors="coerce").dropna().to_numpy()
    n = len(r)
    if n <= window + 1:
        return 0, 0
    q = (1.0 - confidence) * 100.0
    z = None
    if method == "parametric":
        try:
            from scipy.stats import norm
            z = float(norm.ppf(1.0 - confidence))
        except Exception:
            z = -1.6448536269514722 if confidence >= 0.95 else -1.2815515594

    obs = breaches = 0
    for t in range(window, n):
        win = r[t - window:t]
        if method == "parametric" and z is not None:
            var_t = float(win.mean() + z * win.std(ddof=1))
        else:
            var_t = float(np.percentile(win, q))
        obs += 1
        if r[t] < var_t:
            breaches += 1
    return obs, breaches


def backtest_var_coverage(
    returns_panel: pd.DataFrame,
    weights: Optional[dict] = None,
    *,
    confidence: float = 0.95,
    window: int = 60,
    method: str = "historical",
    min_oos: int = 25,
) -> VaRCoverageScorecard:
    """Kupiec POF coverage of a rolling VaR over an injected returns panel.

    ``returns_panel`` is a per-ticker daily-returns frame (e.g. from
    ``book_pnl.returns_panel``). ``weights`` defaults to equal-weight over the
    panel's columns. Returns ``basis="insufficient"`` (no verdict) when the
    panel can't yield at least ``min_oos`` out-of-sample observations.

    The 60-day VaR window + 25-day OOS minimum are PRACTICAL defaults sized to
    the platform's ~180-day fetch (Basel's 250 would never evaluate on real
    caches). A low-power gate that actually runs beats a textbook one that
    never does; the Kupiec p-value still reflects the small sample.
    """
    nominal = 1.0 - float(confidence)
    if returns_panel is None or returns_panel.empty:
        return VaRCoverageScorecard(
            confidence=confidence, method=method, window=window,
            n_observations=0, n_breaches=0, breach_rate=0.0,
            nominal_rate=nominal, kupiec_lr=0.0, kupiec_pvalue=1.0,
            rejected=False, well_calibrated=False, basis="insufficient",
            summary="No real return history available — coverage not evaluated.",
        )

    cols = list(returns_panel.columns)
    if weights:
        cols = [c for c in cols if c in weights]
        w = np.array([float(weights[c]) for c in cols], dtype=float)
    else:
        w = np.full(len(cols), 1.0 / max(1, len(cols)))
    if not cols:
        return VaRCoverageScorecard(
            confidence=confidence, method=method, window=window,
            n_observations=0, n_breaches=0, breach_rate=0.0,
            nominal_rate=nominal, kupiec_lr=0.0, kupiec_pvalue=1.0,
            rejected=False, well_calibrated=False, basis="insufficient",
            summary="Weights did not overlap the panel — coverage not evaluated.",
        )

    sub = returns_panel[cols].dropna(how="any")
    port = pd.Series(sub.to_numpy() @ w, index=sub.index)
    n_obs, n_breaches = _rolling_var_breaches(
        port, confidence=confidence, window=window, method=method
    )
    if n_obs < min_oos:
        # Report the honest computed rate so the scorecard is self-consistent
        # (n_breaches and breach_rate must not disagree), even though no Kupiec
        # verdict is derived from a sub-min_oos sample.
        return VaRCoverageScorecard(
            confidence=confidence, method=method, window=window,
            n_observations=n_obs, n_breaches=n_breaches,
            breach_rate=(n_breaches / n_obs if n_obs else 0.0),
            nominal_rate=nominal, kupiec_lr=0.0, kupiec_pvalue=1.0,
            rejected=False, well_calibrated=False, basis="insufficient",
            summary=(f"Only {n_obs} out-of-sample day(s) (need >= {min_oos}) — "
                     "coverage not evaluated."),
            tickers=cols,
        )

    breach_rate = n_breaches / n_obs
    k = kupiec_pof(n_obs, n_breaches, nominal)
    well = not k["rejected"]
    verdict = (
        "well-calibrated" if well else
        ("too conservative" if breach_rate < nominal else "too loose")
    )
    return VaRCoverageScorecard(
        confidence=confidence, method=method, window=window,
        n_observations=n_obs, n_breaches=n_breaches,
        breach_rate=breach_rate, nominal_rate=nominal,
        kupiec_lr=k["lr"], kupiec_pvalue=k["pvalue"], rejected=k["rejected"],
        well_calibrated=well, basis="real",
        summary=(
            f"{confidence*100:.0f}% VaR breached {n_breaches}/{n_obs} days "
            f"({breach_rate*100:.1f}% vs {nominal*100:.1f}% nominal) over "
            f"{len(cols)} names; Kupiec POF LR={k['lr']:.2f} "
            f"(p={k['pvalue']:.3f}) — {verdict}."
        ),
        tickers=cols,
    )


def _load_cached_stock_data(cache_dir: str = "cache") -> dict:
    """Best-effort, NETWORK-FREE read of cached stock frames -> {ticker: frame}.

    Reads ``cache/stocks/*.parquet`` directly (no yfinance, no Streamlit) so the
    backtest can run headless. Frames keep their canonical date-as-column shape;
    ``book_pnl.returns_panel`` now indexes them correctly. Returns {} when the
    cache is empty/absent.
    """
    out: dict = {}
    try:
        stocks_dir = Path(cache_dir) / "stocks"
        if not stocks_dir.exists():
            return out
        for f in sorted(stocks_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(f)
            except Exception:
                continue
            if df is None or df.empty:
                continue
            sym = (str(df["symbol"].iloc[0]) if "symbol" in df.columns
                   else f.stem.split("_")[0].upper())
            out[sym] = df
    except Exception:
        return {}
    return out


def run_var_coverage_backtest(
    *, confidence: float = 0.95, window: int = 60, method: str = "historical",
    tickers: Optional[list] = None,
) -> VaRCoverageScorecard:
    """Live entry: load REAL cached closes, build the panel, run the coverage
    test. Honest ``basis="insufficient"`` when no usable cached history exists.
    """
    from processing.book_pnl import returns_panel

    stock_data = _load_cached_stock_data()
    panel = returns_panel(stock_data, tickers or _DEFAULT_TICKERS)
    return backtest_var_coverage(
        panel, confidence=confidence, window=window, method=method
    )
