"""Self-contained SVG chart generators for the investor HTML report.

All functions return complete <svg> (or wrapping <div>) strings with no
external dependencies, no JavaScript, and no CDN references.  Safe to embed
directly inside an HTML string.

Dark theme palette mirrors html_report.py / ui/styles.py.
"""
from __future__ import annotations

import math
from typing import Optional

# ── Palette ──────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_ACCENT_CYCLE = [C_ACCENT, C_HIGH, C_MOD, C_CONV, C_MACRO, "#f472b6", "#fb923c"]


# ── Private helpers ───────────────────────────────────────────────────────────

def _normalize(values: list) -> list:
    """Normalize a list of numbers to the [0, 1] range."""
    floats = [_safe_float(v) for v in values]
    lo, hi = min(floats, default=0.0), max(floats, default=0.0)
    if hi == lo:
        return [0.5] * len(floats)
    return [(v - lo) / (hi - lo) for v in floats]


def _safe_float(v, default: float = 0.0) -> float:
    """Safely convert *v* to float, returning *default* on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* characters, appending ellipsis if needed."""
    if not isinstance(text, str):
        text = str(text)
    return text if len(text) <= max_len else text[: max_len - 1] + "\u2026"


def _arc_path(cx: float, cy: float, r: float,
               start_deg: float, end_deg: float) -> str:
    """Return the SVG *d* attribute string for a circular arc.

    Angles are measured clockwise from the positive x-axis (standard SVG).
    """
    s = math.radians(start_deg)
    e = math.radians(end_deg)
    x1, y1 = cx + r * math.cos(s), cy + r * math.sin(s)
    x2, y2 = cx + r * math.cos(e), cy + r * math.sin(e)
    large = 1 if (end_deg - start_deg) % 360 > 180 else 0
    return f"M {x1:.3f} {y1:.3f} A {r:.3f} {r:.3f} 0 {large} 1 {x2:.3f} {y2:.3f}"


def _esc(text: str) -> str:
    """Minimal XML escaping for SVG text content."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _placeholder(width: int, height: int, msg: str = "No data") -> str:
    """Return a minimal placeholder SVG for edge cases."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="4"/>'
        f'<text x="{width//2}" y="{height//2}" fill="{C_TEXT3}" '
        f'font-size="11" font-family="sans-serif" text-anchor="middle" '
        f'dominant-baseline="middle">{_esc(msg)}</text>'
        f'</svg>'
    )


# ── Chart functions ───────────────────────────────────────────────────────────

def sentiment_gauge(score: float, width: int = 300, height: int = 180) -> str:
    """Semicircle gauge from -1.0 (bearish) to +1.0 (bullish).

    Returns a complete <svg> string.
    """
    score = max(-1.0, min(1.0, _safe_float(score)))

    cx = width / 2
    cy = height - 30          # pivot sits near bottom
    r_outer = min(cx - 20, cy - 10)
    r_inner = r_outer * 0.62
    r_needle_tip = r_outer - 4
    r_needle_hub = 6

    # Arc segments: left=180°, right=0° in SVG coords (y-down).
    # We split the 180° semicircle into three equal bands.
    segments = [
        # (start_deg, end_deg, color)
        (180, 240, C_LOW),      # bearish  — leftmost 60°
        (240, 300, C_MOD),      # neutral  — middle 60°
        (300, 360, C_HIGH),     # bullish  — rightmost 60°
    ]

    def _donut_slice(start_d: float, end_d: float, color: str) -> str:
        outer = _arc_path(cx, cy, r_outer, start_d, end_d)
        # inner arc goes end→start (reverse)
        s_i = math.radians(end_d)
        e_i = math.radians(start_d)
        ix1 = cx + r_inner * math.cos(s_i)
        iy1 = cy + r_inner * math.sin(s_i)
        ix2 = cx + r_inner * math.cos(e_i)
        iy2 = cy + r_inner * math.sin(e_i)
        large = 1 if (end_d - start_d) > 180 else 0
        d = (f"{outer} L {ix1:.3f} {iy1:.3f} "
             f"A {r_inner:.3f} {r_inner:.3f} 0 {large} 0 {ix2:.3f} {iy2:.3f} Z")
        return f'<path d="{d}" fill="{color}" opacity="0.85"/>'

    arcs = "".join(_donut_slice(s, e, c) for s, e, c in segments)

    # Needle: score -1→angle 180°, score +1→angle 0°  (SVG x-right)
    needle_deg = 180 - (score + 1) / 2 * 180   # 180° at score=-1, 0° at score=+1
    needle_rad = math.radians(needle_deg)
    nx = cx + r_needle_tip * math.cos(needle_rad)
    ny = cy + r_needle_tip * math.sin(needle_rad)

    needle = (
        f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{nx:.2f}" y2="{ny:.2f}" '
        f'stroke="{C_TEXT}" stroke-width="2.5" stroke-linecap="round"/>'
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_needle_hub}" '
        f'fill="{C_TEXT}" />'
    )

    # Sentiment label
    if score > 0.1:
        label_text, label_color = "BULLISH", C_HIGH
    elif score < -0.1:
        label_text, label_color = "BEARISH", C_LOW
    else:
        label_text, label_color = "NEUTRAL", C_MOD

    score_str = f"{score:+.2f}"

    labels = (
        f'<text x="18" y="{cy + 18}" fill="{C_LOW}" font-size="9" '
        f'font-family="sans-serif" font-weight="600">BEARISH</text>'
        f'<text x="{cx:.0f}" y="{cy - r_outer - 8}" fill="{C_TEXT2}" font-size="9" '
        f'font-family="sans-serif" text-anchor="middle">NEUTRAL</text>'
        f'<text x="{width - 18}" y="{cy + 18}" fill="{C_HIGH}" font-size="9" '
        f'font-family="sans-serif" text-anchor="end" font-weight="600">BULLISH</text>'
        # Score value
        f'<text x="{cx:.0f}" y="{cy + 10}" fill="{C_TEXT}" font-size="20" '
        f'font-family="sans-serif" text-anchor="middle" font-weight="700">{_esc(score_str)}</text>'
        # Label badge
        f'<text x="{cx:.0f}" y="{cy + 26}" fill="{label_color}" font-size="10" '
        f'font-family="sans-serif" text-anchor="middle" font-weight="600">{_esc(label_text)}</text>'
    )

    bg = (f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="6"/>'
          f'<defs>'
          f'<filter id="sglow"><feGaussianBlur stdDeviation="2" result="b"/>'
          f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
          f'</filter></defs>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'{bg}{arcs}{needle}{labels}'
        f'</svg>'
    )


def sentiment_bar(bullish: int, bearish: int, neutral: int,
                  width: int = 400, height: int = 40) -> str:
    """Horizontal stacked bar: green=bullish, gray=neutral, red=bearish.

    Returns a complete <svg> string.
    """
    b, n, r = max(0, int(bullish)), max(0, int(neutral)), max(0, int(bearish))
    total = b + n + r
    if total == 0:
        return _placeholder(width, height, "No sentiment data")

    pad_x, pad_y = 4, 4
    bar_h = height - pad_y * 2
    bar_w = width - pad_x * 2
    bar_y = pad_y

    bp = b / total
    np_ = n / total
    rp = r / total

    bw = bar_w * bp
    nw = bar_w * np_
    rw = bar_w * rp

    def _seg(x: float, w: float, color: str, pct: float, label: str) -> str:
        if w < 1:
            return ""
        rx_left = "4" if x == pad_x else "0"
        rx_right = "4" if x + w >= pad_x + bar_w - 0.5 else "0"
        rect = (f'<rect x="{x:.2f}" y="{bar_y}" width="{w:.2f}" height="{bar_h}" '
                f'fill="{color}" rx="0"/>')
        parts = [rect]
        if w > 28:
            pct_str = f"{pct*100:.0f}%"
            parts.append(
                f'<text x="{x + w/2:.2f}" y="{bar_y + bar_h/2:.2f}" '
                f'fill="{C_TEXT}" font-size="9" font-family="sans-serif" '
                f'text-anchor="middle" dominant-baseline="middle">'
                f'{_esc(pct_str)}</text>'
            )
        return "".join(parts)

    bx = pad_x
    nx_ = bx + bw
    rx_ = nx_ + nw

    segs = (
        _seg(bx, bw, C_HIGH,   bp,  "Bullish") +
        _seg(nx_, nw, C_TEXT3, np_, "Neutral") +
        _seg(rx_, rw, C_LOW,   rp,  "Bearish")
    )

    # Rounded overall container
    bg = (f'<rect x="{pad_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" '
          f'fill="{C_SURFACE}" rx="4"/>')

    # Clip path so corners are rounded on the filled bars
    clip = (f'<defs><clipPath id="sbc">'
            f'<rect x="{pad_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="4"/>'
            f'</clipPath></defs>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'{clip}{bg}'
        f'<g clip-path="url(#sbc)">{segs}</g>'
        f'</svg>'
    )


def score_bar(score: float, max_score: float = 1.0,
              width: int = 200, height: int = 10,
              color: str = C_HIGH) -> str:
    """Simple horizontal progress bar with rounded corners.

    Returns a complete <svg> string.
    """
    score = _safe_float(score)
    max_score = _safe_float(max_score, 1.0) or 1.0
    frac = max(0.0, min(1.0, score / max_score))

    track_w = width
    fill_w = max(frac * track_w, 0)

    clip = (f'<defs><clipPath id="sbc_{id(score)}">'
            f'<rect width="{track_w}" height="{height}" rx="{height//2}"/>'
            f'</clipPath></defs>')

    track = (f'<rect width="{track_w}" height="{height}" '
             f'fill="{C_SURFACE}" rx="{height//2}"/>')
    fill = (f'<rect width="{fill_w:.2f}" height="{height}" '
            f'fill="{color}" rx="{height//2}" clip-path="url(#sbc_{id(score)})"/>'
            if fill_w > 0 else "")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'{clip}{track}{fill}'
        f'</svg>'
    )


def sparkline(values: list, width: int = 120, height: int = 40,
              color: str = C_ACCENT, fill: bool = True) -> str:
    """Mini line chart — no axes, no labels, just the trend shape.

    Returns a complete <svg> string.
    """
    floats = [_safe_float(v) for v in (values or [])]
    floats = [v for v in floats if v == v]  # drop NaN
    if len(floats) < 2:
        return _placeholder(width, height, "—")

    lo, hi = min(floats), max(floats)
    pad = 4
    w_inner = width - pad * 2
    h_inner = height - pad * 2

    def _pt(i: int, v: float):
        x = pad + (i / (len(floats) - 1)) * w_inner
        y = pad + (1 - (v - lo) / (hi - lo if hi != lo else 1)) * h_inner
        return x, y

    points = [_pt(i, v) for i, v in enumerate(floats)]
    poly = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    elements = []
    if fill:
        last_x, _ = points[-1]
        first_x, _ = points[0]
        bottom = height - pad
        area_d = (f"M {points[0][0]:.2f},{points[0][1]:.2f} "
                  + " ".join(f"L {x:.2f},{y:.2f}" for x, y in points[1:])
                  + f" L {last_x:.2f},{bottom:.2f} L {first_x:.2f},{bottom:.2f} Z")
        # Convert hex color to rgba for fill
        fill_color = color
        elements.append(
            f'<defs><linearGradient id="spfill" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
            f'</linearGradient></defs>'
            f'<path d="{area_d}" fill="url(#spfill)"/>'
        )

    elements.append(
        f'<polyline points="{poly}" fill="none" stroke="{color}" '
        f'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # Dot at last point
    lx, ly = points[-1]
    elements.append(
        f'<circle cx="{lx:.2f}" cy="{ly:.2f}" r="2.5" fill="{color}"/>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elements)
        + '</svg>'
    )


def bar_chart(labels: list, values: list, colors: list = None,
              width: int = 400, height: int = 200, title: str = "") -> str:
    """Vertical bar chart with optional per-bar colors and title.

    Returns a complete <svg> string.
    """
    if not labels or not values:
        return _placeholder(width, height, "No data")

    floats = [_safe_float(v) for v in values]
    n = min(len(labels), len(floats))
    labels = [_truncate(str(l), 14) for l in labels[:n]]
    floats = floats[:n]

    if colors is None:
        colors = [C_ACCENT] * n
    else:
        colors = (list(colors) + [C_ACCENT] * n)[:n]

    title_h = 20 if title else 0
    pad_top = 14 + title_h
    pad_bot = 40 if n > 6 else 28
    pad_left = 10
    pad_right = 10

    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bot

    max_val = max(floats, default=1.0) or 1.0
    bar_w = chart_w / n * 0.6
    gap = chart_w / n

    elems = []

    # Background
    elems.append(f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="6"/>')

    # Title
    if title:
        elems.append(
            f'<text x="{width//2}" y="14" fill="{C_TEXT}" font-size="11" '
            f'font-family="sans-serif" text-anchor="middle" font-weight="600">'
            f'{_esc(title)}</text>'
        )

    # Baseline
    base_y = pad_top + chart_h
    elems.append(
        f'<line x1="{pad_left}" y1="{base_y}" x2="{pad_left + chart_w}" y2="{base_y}" '
        f'stroke="rgba(255,255,255,0.1)" stroke-width="1"/>'
    )

    rotate = n > 6

    for i, (lbl, val, col) in enumerate(zip(labels, floats, colors)):
        bx = pad_left + i * gap + gap / 2 - bar_w / 2
        bh = (val / max_val) * chart_h
        by = base_y - bh

        # Bar
        elems.append(
            f'<rect x="{bx:.2f}" y="{by:.2f}" width="{bar_w:.2f}" height="{bh:.2f}" '
            f'fill="{col}" rx="2"/>'
        )

        # Value label on top
        val_str = f"{val:.1f}" if val != int(val) else str(int(val))
        elems.append(
            f'<text x="{bx + bar_w/2:.2f}" y="{by - 3:.2f}" fill="{C_TEXT}" '
            f'font-size="9" font-family="sans-serif" text-anchor="middle">'
            f'{_esc(val_str)}</text>'
        )

        # X label
        lx = bx + bar_w / 2
        if rotate:
            elems.append(
                f'<text x="{lx:.2f}" y="{base_y + 4}" fill="{C_TEXT2}" '
                f'font-size="9" font-family="sans-serif" text-anchor="end" '
                f'transform="rotate(-40,{lx:.2f},{base_y + 4})">'
                f'{_esc(lbl)}</text>'
            )
        else:
            elems.append(
                f'<text x="{lx:.2f}" y="{base_y + 14}" fill="{C_TEXT2}" '
                f'font-size="9" font-family="sans-serif" text-anchor="middle">'
                f'{_esc(lbl)}</text>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elems)
        + '</svg>'
    )


def horizontal_bar_chart(labels: list, values: list, max_val: float = None,
                          colors: list = None, width: int = 400,
                          height: int = None, title: str = "") -> str:
    """Horizontal bar chart — ideal for ranking and comparison.

    Height is auto-computed if not provided (30px per row + padding).
    Returns a complete <svg> string.
    """
    if not labels or not values:
        return _placeholder(width, height or 100, "No data")

    floats = [_safe_float(v) for v in values]
    n = min(len(labels), len(floats))
    labels = [_truncate(str(l), 20) for l in labels[:n]]
    floats = floats[:n]

    if colors is None:
        colors = [C_ACCENT] * n
    else:
        colors = (list(colors) + [C_ACCENT] * n)[:n]

    row_h = 28
    title_h = 20 if title else 0
    pad_top = 8 + title_h
    pad_bot = 8
    label_w = 110
    val_w = 40
    bar_area = width - label_w - val_w - 8

    if height is None:
        height = pad_top + n * row_h + pad_bot

    effective_max = max_val or max(floats, default=1.0) or 1.0

    elems = [f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="6"/>']

    if title:
        elems.append(
            f'<text x="{width//2}" y="{8 + title_h - 4}" fill="{C_TEXT}" '
            f'font-size="11" font-family="sans-serif" text-anchor="middle" '
            f'font-weight="600">{_esc(title)}</text>'
        )

    for i, (lbl, val, col) in enumerate(zip(labels, floats, colors)):
        row_y = pad_top + i * row_h

        # Alternating row background
        if i % 2 == 0:
            elems.append(
                f'<rect x="0" y="{row_y}" width="{width}" height="{row_h}" '
                f'fill="rgba(255,255,255,0.03)" rx="0"/>'
            )

        # Row center y
        cy = row_y + row_h / 2

        # Label
        elems.append(
            f'<text x="{label_w - 6}" y="{cy:.1f}" fill="{C_TEXT2}" '
            f'font-size="10" font-family="sans-serif" text-anchor="end" '
            f'dominant-baseline="middle">{_esc(lbl)}</text>'
        )

        # Bar track
        bar_x = label_w
        bar_h_px = row_h * 0.45
        bar_y = cy - bar_h_px / 2
        elems.append(
            f'<rect x="{bar_x}" y="{bar_y:.2f}" width="{bar_area}" height="{bar_h_px:.2f}" '
            f'fill="{C_SURFACE}" rx="2"/>'
        )

        # Bar fill
        fill_w = max(0, val / effective_max) * bar_area
        if fill_w > 0:
            elems.append(
                f'<rect x="{bar_x}" y="{bar_y:.2f}" width="{fill_w:.2f}" '
                f'height="{bar_h_px:.2f}" fill="{col}" rx="2"/>'
            )

        # Value label
        val_str = f"{val:.2f}" if isinstance(val, float) and val != int(val) else str(int(val))
        elems.append(
            f'<text x="{bar_x + bar_area + 6}" y="{cy:.1f}" fill="{C_TEXT}" '
            f'font-size="10" font-family="sans-serif" dominant-baseline="middle">'
            f'{_esc(val_str)}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elems)
        + '</svg>'
    )


def signal_strength_chart(signals: list, width: int = 400, height: int = 200) -> str:
    """Scatter chart: x=strength (0-1), y=conviction (LOW/MEDIUM/HIGH).

    Each signal dict should have keys: name, direction, conviction, strength,
    and optionally expected_return.

    Direction colors: LONG=green, SHORT=red, NEUTRAL=gray.
    Returns a complete <svg> string.
    """
    if not signals:
        return _placeholder(width, height, "No signals")

    pad_l, pad_r = 60, 20
    pad_t, pad_b = 20, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    CONV_LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    DIR_COLOR = {"LONG": C_HIGH, "SHORT": C_LOW, "NEUTRAL": C_TEXT3}

    elems = [f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="6"/>']

    # Conviction band backgrounds
    band_h = plot_h / 3
    band_colors = ["rgba(239,68,68,0.05)", "rgba(245,158,11,0.05)", "rgba(16,185,129,0.05)"]
    band_labels = ["LOW", "MEDIUM", "HIGH"]
    for bi in range(3):
        by = pad_t + (2 - bi) * band_h
        elems.append(
            f'<rect x="{pad_l}" y="{by:.2f}" width="{plot_w}" height="{band_h:.2f}" '
            f'fill="{band_colors[bi]}" rx="0"/>'
        )
        elems.append(
            f'<text x="{pad_l - 4}" y="{by + band_h/2:.2f}" fill="{C_TEXT3}" '
            f'font-size="8" font-family="sans-serif" text-anchor="end" '
            f'dominant-baseline="middle">{band_labels[bi]}</text>'
        )

    # Baseline grid lines
    for bi in range(4):
        gy = pad_t + bi * band_h
        elems.append(
            f'<line x1="{pad_l}" y1="{gy:.2f}" x2="{pad_l + plot_w}" y2="{gy:.2f}" '
            f'stroke="rgba(255,255,255,0.07)" stroke-width="1"/>'
        )

    # X-axis ticks
    for xi, xv in enumerate([0, 0.25, 0.5, 0.75, 1.0]):
        gx = pad_l + xv * plot_w
        elems.append(
            f'<line x1="{gx:.2f}" y1="{pad_t}" x2="{gx:.2f}" y2="{pad_t + plot_h}" '
            f'stroke="rgba(255,255,255,0.05)" stroke-width="1"/>'
        )
        elems.append(
            f'<text x="{gx:.2f}" y="{pad_t + plot_h + 12}" fill="{C_TEXT3}" '
            f'font-size="8" font-family="sans-serif" text-anchor="middle">'
            f'{xv:.0%}</text>'
        )

    # Plot signals
    labeled = 0
    for sig in signals:
        strength = max(0.0, min(1.0, _safe_float(sig.get("strength", 0.5))))
        conv_str = str(sig.get("conviction", "MEDIUM")).upper()
        conv_idx = CONV_LEVELS.get(conv_str, 1)
        direction = str(sig.get("direction", "NEUTRAL")).upper()
        color = DIR_COLOR.get(direction, C_TEXT3)
        ret = _safe_float(sig.get("expected_return", 0.1))

        cx = pad_l + strength * plot_w
        # Center of conviction band
        cy = pad_t + (2 - conv_idx) * band_h + band_h / 2

        radius = max(4, min(10, 4 + abs(ret) * 30))

        elems.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.1f}" '
            f'fill="{color}" opacity="0.75" stroke="{color}" stroke-width="1"/>'
        )

        # Label top signals
        name = _truncate(str(sig.get("name", "")), 10)
        if name and labeled < 5:
            elems.append(
                f'<text x="{cx:.2f}" y="{cy - radius - 3:.2f}" fill="{C_TEXT2}" '
                f'font-size="8" font-family="sans-serif" text-anchor="middle">'
                f'{_esc(name)}</text>'
            )
            labeled += 1

    # X-axis label
    elems.append(
        f'<text x="{pad_l + plot_w/2:.0f}" y="{height - 4}" fill="{C_TEXT3}" '
        f'font-size="9" font-family="sans-serif" text-anchor="middle">Strength</text>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elems)
        + '</svg>'
    )


def keyword_cloud(keywords: list, width: int = 400, height: int = 120) -> str:
    """Simple flowing keyword cloud — largest first, colored by accent cycle.

    Returns a complete <svg> string.
    """
    if not keywords:
        return _placeholder(width, height, "No keywords")

    words = [str(k) for k in keywords if k][:24]

    elems = [f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="6"/>']

    # Font sizes: first=22, decay toward 10
    min_fs, max_fs = 10, 22
    n = len(words)

    # Simple flowing layout: place words left-to-right, wrap on overflow
    x, y = 10, 28
    line_h = 0

    for i, word in enumerate(words):
        fs = max(min_fs, int(max_fs - (max_fs - min_fs) * (i / max(n - 1, 1))))
        color = _ACCENT_CYCLE[i % len(_ACCENT_CYCLE)]
        # Approximate text width: fs * 0.6 per char
        tw = int(len(word) * fs * 0.62)

        if x + tw > width - 10 and x > 10:
            x = 10
            y += line_h + 6
            line_h = 0

        if y > height - 8:
            break

        line_h = max(line_h, fs)
        elems.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="{fs}" '
            f'font-family="sans-serif" font-weight="{"700" if i == 0 else "400"}">'
            f'{_esc(word)}</text>'
        )
        x += tw + 8

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elems)
        + '</svg>'
    )


def mini_donut(value: float, max_val: float = 1.0, color: str = C_HIGH,
               width: int = 80, height: int = 80, label: str = "") -> str:
    """Small donut chart showing a single proportion.

    Returns a complete <svg> string.
    """
    value = _safe_float(value)
    max_val = _safe_float(max_val, 1.0) or 1.0
    frac = max(0.0, min(1.0, value / max_val))

    label_h = 14 if label else 0
    cx = width / 2
    cy = (height - label_h) / 2
    r_outer = min(cx, cy) - 4
    r_inner = r_outer * 0.60

    # Full background ring
    bg_ring = (
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_outer:.1f}" '
        f'fill="none" stroke="{C_SURFACE}" stroke-width="{r_outer - r_inner:.1f}"/>'
    )

    elems = [
        f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="4"/>',
        bg_ring,
    ]

    if frac > 0.001:
        # Arc: starts at top (-90°) and sweeps clockwise
        sweep_deg = frac * 360
        # Circumference-based stroke-dasharray approach is cleaner for donut arcs
        stroke_w = r_outer - r_inner
        r_mid = (r_outer + r_inner) / 2
        circ = 2 * math.pi * r_mid
        dash = frac * circ
        gap = circ - dash

        # Start at top: rotate -90 degrees
        elems.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_mid:.2f}" '
            f'fill="none" stroke="{color}" stroke-width="{stroke_w:.1f}" '
            f'stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="{circ/4:.2f}" '
            f'stroke-linecap="round"/>'
        )

    # Center percentage text
    pct_str = f"{frac*100:.0f}%"
    elems.append(
        f'<text x="{cx:.1f}" y="{cy:.1f}" fill="{C_TEXT}" '
        f'font-size="{max(8, int(r_inner * 0.55))}" font-family="sans-serif" '
        f'text-anchor="middle" dominant-baseline="middle" font-weight="700">'
        f'{_esc(pct_str)}</text>'
    )

    if label:
        elems.append(
            f'<text x="{cx:.1f}" y="{height - 4}" fill="{C_TEXT2}" '
            f'font-size="8" font-family="sans-serif" text-anchor="middle">'
            f'{_esc(_truncate(label, 14))}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        + "".join(elems)
        + '</svg>'
    )


def freight_momentum_arrow(pct_change: float, width: int = 60, height: int = 60) -> str:
    """Up/down arrow SVG colored by direction of pct_change.

    Returns a complete <svg> string.
    """
    pct = _safe_float(pct_change)
    up = pct >= 0
    color = C_HIGH if up else C_LOW

    cx = width / 2
    label_h = 16
    arrow_h = height - label_h - 4
    arrow_w = width * 0.55

    # Arrow polygon
    shaft_w = arrow_w * 0.30
    head_w = arrow_w
    head_h = arrow_h * 0.45
    shaft_h = arrow_h - head_h

    if up:
        # Arrow pointing up
        # Top center → right of head → right of shaft → bottom-right → bottom-left → left of shaft → left of head
        top_y = 4
        mid_y = top_y + head_h
        bot_y = top_y + arrow_h
        lx = cx - arrow_w / 2
        rx = cx + arrow_w / 2
        slx = cx - shaft_w / 2
        srx = cx + shaft_w / 2
        pts = (
            f"{cx:.1f},{top_y:.1f} "
            f"{rx:.1f},{mid_y:.1f} "
            f"{srx:.1f},{mid_y:.1f} "
            f"{srx:.1f},{bot_y:.1f} "
            f"{slx:.1f},{bot_y:.1f} "
            f"{slx:.1f},{mid_y:.1f} "
            f"{lx:.1f},{mid_y:.1f}"
        )
    else:
        # Arrow pointing down
        top_y = 4
        mid_y = top_y + arrow_h - head_h
        bot_y = top_y + arrow_h
        lx = cx - arrow_w / 2
        rx = cx + arrow_w / 2
        slx = cx - shaft_w / 2
        srx = cx + shaft_w / 2
        pts = (
            f"{cx:.1f},{bot_y:.1f} "
            f"{rx:.1f},{mid_y:.1f} "
            f"{srx:.1f},{mid_y:.1f} "
            f"{srx:.1f},{top_y:.1f} "
            f"{slx:.1f},{top_y:.1f} "
            f"{slx:.1f},{mid_y:.1f} "
            f"{lx:.1f},{mid_y:.1f}"
        )

    arrow = f'<polygon points="{pts}" fill="{color}" opacity="0.9"/>'

    # Percentage label below
    sign = "+" if pct >= 0 else ""
    pct_str = f"{sign}{pct:.1f}%"
    text = (
        f'<text x="{cx:.1f}" y="{height - 2}" fill="{color}" '
        f'font-size="10" font-family="sans-serif" font-weight="700" '
        f'text-anchor="middle">{_esc(pct_str)}</text>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="{C_CARD}" rx="4"/>'
        f'{arrow}{text}'
        f'</svg>'
    )
