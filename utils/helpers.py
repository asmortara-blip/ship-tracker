from __future__ import annotations
import re
from datetime import datetime, timezone


def slugify(text: str) -> str:
    """Convert arbitrary string to filesystem-safe slug."""
    text = str(text).lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text


def format_usd(value: float, compact: bool = True) -> str:
    """Format a USD value for display."""
    if compact:
        if abs(value) >= 1e12:
            return f"${value/1e12:.2f}T"
        if abs(value) >= 1e9:
            return f"${value/1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"${value/1e6:.2f}M"
    return f"${value:,.0f}"


def score_to_label(score: float, high: float = 0.70, low: float = 0.35) -> str:
    """Convert [0,1] score to human-readable label."""
    if score >= high:
        return "High"
    if score <= low:
        return "Low"
    return "Moderate"


def trend_label(pct_change: float, threshold: float = 0.05) -> str:
    """Convert percentage change to trend label."""
    if pct_change > threshold:
        return "Rising"
    if pct_change < -threshold:
        return "Falling"
    return "Stable"


def delta_color(value: float, inverse: bool = False) -> str:
    """Return Streamlit delta color string."""
    if inverse:
        return "inverse"
    return "normal"


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def safe_normalize(series, min_val: float | None = None, max_val: float | None = None):
    """Min-max normalize a pandas Series to [0, 1]. Returns 0.5 if no variance."""
    import pandas as pd
    mn = series.min() if min_val is None else min_val
    mx = series.max() if max_val is None else max_val
    if mx == mn:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - mn) / (mx - mn)


def sigmoid(x: float) -> float:
    """Sigmoid function for converting z-scores to [0,1]."""
    import math
    return 1 / (1 + math.exp(-x))
