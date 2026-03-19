from __future__ import annotations

import copy

from processing.regime_detector import MacroRegime, get_regime_multipliers


# ---------------------------------------------------------------------------
# Score adjustment
# ---------------------------------------------------------------------------

def apply_regime_adjustment(route_results: list, regime: MacroRegime) -> list:
    """Return a new list of route results with regime-adjusted opportunity scores.

    Original objects are never mutated — deep copies are made and their
    ``opportunity_score`` field is replaced with the regime-adjusted value
    clamped to [0, 1].

    Args:
        route_results: list of RouteOpportunity (or compatible) objects that
                       expose ``route_id`` and ``opportunity_score``.
        regime:        Current MacroRegime from classify_macro_regime().

    Returns:
        New list of modified copies, preserving original ordering.
    """
    multipliers = get_regime_multipliers(regime)
    default_mult = multipliers.get("_default", 1.0)

    adjusted: list = []
    for route in route_results:
        route_copy = copy.copy(route)
        mult = multipliers.get(route.route_id, default_mult)
        raw_score = route.opportunity_score * mult
        route_copy.opportunity_score = max(0.0, min(1.0, raw_score))
        adjusted.append(route_copy)

    return adjusted


# ---------------------------------------------------------------------------
# Regime banner HTML
# ---------------------------------------------------------------------------

def render_regime_banner(regime: MacroRegime) -> str:
    """Return an HTML string for a full-width regime banner.

    The banner uses a dark glassmorphism card style with a left border in the
    regime's color.  It is intended to be injected at the top of dashboard tabs
    via ``st.markdown(..., unsafe_allow_html=True)``.

    Includes:
    - Regime label in large colored text
    - Regime name badge and confidence bar
    - Best routes this regime
    - Watch stocks list
    - Short regime description
    """
    color = regime.regime_color
    label = regime.shipping_regime_label
    regime_name = regime.regime
    confidence_pct = int(regime.confidence * 100)
    description = regime.regime_description

    best_routes_str = (
        ", ".join(regime.best_routes_in_regime)
        if regime.best_routes_in_regime
        else "No strong route recommendations"
    )
    best_stocks_str = (
        ", ".join(regime.best_stocks_in_regime)
        if regime.best_stocks_in_regime
        else "Avoid shipping names"
    )

    # Confidence bar fill color: mirror the regime color at lower opacity
    bar_html = (
        f'<div style="background:#1e293b;border-radius:4px;height:6px;width:100%;margin-top:4px;">'
        f'<div style="background:{color};border-radius:4px;height:6px;width:{confidence_pct}%;"></div>'
        f'</div>'
    )

    html = f"""
<div style="
    background: rgba(15,23,42,0.85);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.08);
    border-left: 4px solid {color};
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 16px;
    font-family: 'Inter', sans-serif;
">
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <span style="
        font-size:1.5rem;
        font-weight:700;
        color:{color};
        letter-spacing:-0.5px;
    ">{label}</span>
    <span style="
        background:rgba(255,255,255,0.07);
        color:#94a3b8;
        font-size:0.72rem;
        font-weight:600;
        letter-spacing:0.08em;
        text-transform:uppercase;
        padding:2px 8px;
        border-radius:4px;
    ">{regime_name}</span>
  </div>

  <div style="margin-top:6px;color:#64748b;font-size:0.78rem;">
    Confidence: <span style="color:#cbd5e1;font-weight:600;">{confidence_pct}%</span>
    {bar_html}
  </div>

  <div style="
      display:flex;
      gap:24px;
      margin-top:12px;
      flex-wrap:wrap;
  ">
    <div>
      <span style="color:#64748b;font-size:0.73rem;text-transform:uppercase;letter-spacing:0.06em;">
        Best routes this regime
      </span><br>
      <span style="color:#e2e8f0;font-size:0.88rem;font-weight:500;">{best_routes_str}</span>
    </div>
    <div>
      <span style="color:#64748b;font-size:0.73rem;text-transform:uppercase;letter-spacing:0.06em;">
        Watch
      </span><br>
      <span style="color:#e2e8f0;font-size:0.88rem;font-weight:500;">{best_stocks_str}</span>
    </div>
  </div>

  <p style="
      color:#94a3b8;
      font-size:0.82rem;
      margin-top:12px;
      margin-bottom:0;
      line-height:1.55;
      border-top:1px solid rgba(255,255,255,0.05);
      padding-top:10px;
  ">{description}</p>
</div>
""".strip()

    return html
