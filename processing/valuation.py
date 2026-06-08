"""valuation.py — illustrative equity-valuation models for shipping companies.

Four classic valuation models — multi-stage DCF, scenario analysis,
Monte-Carlo simulation and a sensitivity/tornado decomposition — plus a
disruption-linkage layer that ties the worst-case scenario to the platform's
modeled disruption severity. The math is **pure and deterministic**: every
fundamental input arrives as an argument, so the whole module runs fully
offline (no network, no ``yfinance``) and every test is a known-answer test.

ILLUSTRATIVE ONLY — assumed inputs, not measured
------------------------------------------------
A provenance audit of this platform established that **no real company
fundamentals reach any analytics module** — there is no free-cash-flow, capex,
debt, revenue or shares-outstanding time series wired in (the one real-
fundamentals feed, Alpha Vantage, is key-gated and consumed only by a
health/display tab). A DCF built here is therefore **illustrative**: its
fundamental inputs are **assumed defaults, not measured values**.

This module is honest about that by construction:

* every fundamental input is an *argument* with a clearly-labelled illustrative
  default (see :class:`ValuationInputs`);
* every result object carries a per-input provenance flag (``"real"`` vs
  ``"assumed"``) and a non-empty ``disclaimer`` string;
* the disclaimer is repeated in the module docstring, the result objects and
  the human-readable summaries.

**Illustrative valuation — assumed inputs, not investment advice. See
docs/DATA_PROVENANCE.md.** This mirrors the discipline already in
``engine.alpha_engine`` (which appends a ``DISCLAIMER`` to every signal) and
``processing.disruption_cascade`` ("Modeled idea, not investment advice"). The
per-share numbers produced here are **never** a real price target.

Pure processing module — no Streamlit imports, no ``st.`` calls, no live-data
adapters (those are the orchestrator's job). stdlib + numpy + pandas only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Disclaimer — travels with every result object and every summary string.
# ---------------------------------------------------------------------------

DISCLAIMER: str = (
    "Illustrative valuation — assumed inputs, not investment advice. "
    "See docs/DATA_PROVENANCE.md."
)

# The fundamental inputs a valuation depends on. Used to default the per-input
# provenance map to "assumed" — the honest default given the audit.
_FUNDAMENTAL_INPUTS: tuple[str, ...] = (
    "fcf_0",
    "fcf_growth",
    "discount_rate",
    "terminal_growth",
    "shares_outstanding",
    "net_debt",
)

# When discount_rate <= terminal_growth the Gordon terminal value diverges (or
# goes negative). We clamp the terminal growth to sit a fixed margin *below*
# the discount rate so the closed form stays finite and positive, and flag it.
_MIN_DISCOUNT_TERMINAL_SPREAD: float = 0.005  # 50 bps floor on (r - g_terminal)


# ---------------------------------------------------------------------------
# Numeric guards
# ---------------------------------------------------------------------------

def _finite(value: float, default: float = 0.0) -> float:
    """Return ``float(value)`` if finite, else *default*.

    Guards every public entry point against NaN / inf propagating out of the
    valuation math (e.g. a divide-by-near-zero or a degenerate Monte-Carlo
    draw).
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return v


def _coerce_growth(fcf_growth, horizon: int) -> list[float]:
    """Coerce a scalar-or-sequence growth spec into a per-year list of length *horizon*.

    A scalar applies the same growth every year. A sequence is used as-is and
    padded with its last element (or 0.0 when empty) to reach *horizon*; an
    over-long sequence is truncated. Non-finite entries fall back to 0.0.
    """
    horizon = max(0, int(horizon))
    if isinstance(fcf_growth, (int, float, np.floating, np.integer)):
        return [_finite(fcf_growth, 0.0)] * horizon
    if isinstance(fcf_growth, (Sequence, np.ndarray)):
        vals = [_finite(g, 0.0) for g in list(fcf_growth)]
        if not vals:
            return [0.0] * horizon
        if len(vals) >= horizon:
            return vals[:horizon]
        return vals + [vals[-1]] * (horizon - len(vals))
    # Unknown type — treat as zero growth.
    return [0.0] * horizon


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ValuationInputs:
    """Fundamental inputs for a single-company DCF.

    **Every default is illustrative, not measured.** The platform wires no real
    fundamentals into analytics (see module docstring + docs/DATA_PROVENANCE.md),
    so a caller that does not override a field is using an assumption. The
    ``input_provenance`` map records exactly which fields are ``"real"`` (the
    caller supplied a measured value and said so) vs ``"assumed"`` (the default
    or an unlabelled override). It defaults every fundamental to ``"assumed"``.

    Fields
    ------
    fcf_0:
        Latest annual free cash flow (currency units, e.g. USD millions). The
        base from which the explicit-horizon FCF is projected.
    fcf_growth:
        Either a scalar annual growth rate applied every projection year, or a
        per-year sequence (e.g. ``[0.10, 0.06, 0.03]``). Sequences shorter than
        the horizon are padded with their last value.
    discount_rate:
        WACC / required return used to discount projected FCF and the terminal
        value. Must exceed ``terminal_growth`` for a finite Gordon terminal
        value — see :func:`dcf_valuation` for the clamp/flag behaviour.
    terminal_growth:
        Perpetual FCF growth beyond the explicit horizon (Gordon growth).
    shares_outstanding:
        Diluted share count. Guarded against zero / negative.
    net_debt:
        Net debt (total debt minus cash). Subtracted from enterprise value to
        reach equity value. Default 0.0 (debt-free assumption).
    input_provenance:
        ``{field_name: "real" | "assumed"}``. Defaults every fundamental to
        ``"assumed"``. Callers with a measured value should pass ``"real"`` for
        that field — but the audit means that is rarely truthful here.
    """

    fcf_0: float = 500.0                 # illustrative: USD ~500M annual FCF
    fcf_growth: object = 0.03            # illustrative: 3% steady-state growth
    discount_rate: float = 0.10          # illustrative: 10% WACC
    terminal_growth: float = 0.02        # illustrative: 2% perpetual growth
    shares_outstanding: float = 120.0    # illustrative: 120M diluted shares
    net_debt: float = 0.0                # illustrative: debt-free
    input_provenance: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Default every fundamental's provenance to "assumed" — the honest
        # default. An explicit map is respected but back-filled for any field
        # the caller left out, so the provenance is always complete.
        prov = dict(self.input_provenance or {})
        for name in _FUNDAMENTAL_INPUTS:
            prov.setdefault(name, "assumed")
        self.input_provenance = prov

    def assumptions(self) -> dict:
        """Echo the inputs as a plain dict (for ``ValuationResult.assumptions``)."""
        return {
            "fcf_0": _finite(self.fcf_0),
            "fcf_growth": self.fcf_growth,
            "discount_rate": _finite(self.discount_rate),
            "terminal_growth": _finite(self.terminal_growth),
            "shares_outstanding": _finite(self.shares_outstanding),
            "net_debt": _finite(self.net_debt),
        }


@dataclass
class ValuationResult:
    """The output of a single DCF run.

    Carries the headline per-share value plus the full intermediate chain
    (enterprise value, equity value, PV of explicit FCF, PV of terminal value)
    so the number is decomposable — and always a ``disclaimer``, an
    ``assumptions`` echo and the ``input_provenance`` map so the result can
    never be mistaken for a measured price target.
    """

    per_share_value: float
    enterprise_value: float
    equity_value: float
    pv_explicit_fcf: float
    pv_terminal_value: float
    horizon: int
    terminal_growth_clamped: bool       # True if r<=g forced a terminal-growth clamp
    assumptions: dict = field(default_factory=dict)
    input_provenance: dict = field(default_factory=dict)
    disclaimer: str = DISCLAIMER
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. DCF valuation
# ---------------------------------------------------------------------------

def dcf_valuation(inputs: ValuationInputs, *, horizon: int = 5) -> ValuationResult:
    """Multi-stage discounted-cash-flow valuation → per-share equity value.

    The model, stated once:

    1. Project FCF over ``horizon`` years from ``fcf_0`` using ``fcf_growth``
       (a constant rate or a per-year sequence). Year *t* FCF is
       ``fcf_0 * Π(1 + g_i)`` for ``i = 1..t``.
    2. Discount each year's FCF at ``discount_rate`` → PV of explicit FCF.
    3. A Gordon-growth terminal value on the final-year FCF:
       ``TV = FCF_H * (1 + g_term) / (r - g_term)``, discounted ``horizon``
       years → PV of terminal value. **Guard:** when ``r <= g_term`` the
       closed form diverges / goes negative, so ``g_term`` is clamped to
       ``r - _MIN_DISCOUNT_TERMINAL_SPREAD`` and the result is flagged
       (``terminal_growth_clamped=True``) with an explanatory note.
    4. Enterprise value = PV(explicit) + PV(terminal). Equity value =
       EV − ``net_debt``. Per-share = equity value / ``shares_outstanding``,
       guarded against zero / negative share counts (→ 0.0 with a note).

    Parameters
    ----------
    inputs:
        A :class:`ValuationInputs`. All fundamentals are assumed unless the
        caller labelled them ``"real"`` in ``input_provenance`` — and the audit
        means they are illustrative here.
    horizon:
        Number of explicit projection years (>= 1; values < 1 are treated as 1).

    Returns
    -------
    ValuationResult
        Per-share value plus the full PV decomposition, the assumptions echo,
        the provenance map and the standing disclaimer. Never raises.
    """
    horizon = max(1, int(horizon))
    notes: list[str] = []

    fcf_0 = _finite(inputs.fcf_0, 0.0)
    r = _finite(inputs.discount_rate, 0.0)
    g_term = _finite(inputs.terminal_growth, 0.0)
    net_debt = _finite(inputs.net_debt, 0.0)
    shares = _finite(inputs.shares_outstanding, 0.0)

    growth = _coerce_growth(inputs.fcf_growth, horizon)

    # ---- 1. project + 2. discount explicit FCF --------------------------
    pv_explicit = 0.0
    fcf_t = fcf_0
    last_fcf = fcf_0
    # A non-positive discount rate makes discounting meaningless; floor the
    # per-year discount factor denominator at a tiny positive so we never
    # divide by zero or amplify by a negative base.
    one_plus_r = 1.0 + r
    if one_plus_r <= 0.0:
        one_plus_r = 1e-9
        notes.append(
            "Discount rate <= -100% is non-economic; discount factor floored "
            "to avoid division blow-up."
        )

    for t in range(1, horizon + 1):
        fcf_t = fcf_t * (1.0 + growth[t - 1])
        pv_explicit += fcf_t / (one_plus_r ** t)
        last_fcf = fcf_t

    pv_explicit = _finite(pv_explicit, 0.0)

    # ---- 3. Gordon terminal value with the r > g guard ------------------
    terminal_growth_clamped = False
    g_eff = g_term
    spread = r - g_term
    if spread < _MIN_DISCOUNT_TERMINAL_SPREAD:
        # r - g_term sits below the floor (or r <= g_term outright): the Gordon
        # perpetuity either diverges/inverts OR blows up to a near-singular but
        # FINITE value — which slips past the isfinite guard and wrecks the
        # documented monotonicity (a discount rate landing just above terminal
        # growth produced per-share ~1e18). Clamp g_term to exactly the floor
        # below r so the closed form stays finite AND bounded, and flag it.
        g_eff = r - _MIN_DISCOUNT_TERMINAL_SPREAD
        terminal_growth_clamped = True
        notes.append(
            "Discount rate − terminal growth (" + format(spread, ".4f")
            + ") below the " + format(_MIN_DISCOUNT_TERMINAL_SPREAD, ".4f")
            + " floor; clamped terminal growth to " + format(g_eff, ".4f")
            + " to keep the Gordon terminal value finite and bounded."
        )

    spread_eff = r - g_eff
    if spread_eff <= 0.0:
        # Defensive: only reachable if r itself is <= the spread floor (e.g. a
        # zero or negative discount rate). Fall back to no terminal value.
        terminal_value = 0.0
        notes.append(
            "Discount rate too low to support any Gordon terminal value; "
            "terminal value set to 0.0."
        )
    else:
        terminal_value = last_fcf * (1.0 + g_eff) / spread_eff

    pv_terminal = _finite(terminal_value / (one_plus_r ** horizon), 0.0)

    # ---- 4. EV → equity → per-share -------------------------------------
    enterprise_value = _finite(pv_explicit + pv_terminal, 0.0)
    equity_value = _finite(enterprise_value - net_debt, 0.0)

    if shares <= 0.0:
        per_share = 0.0
        notes.append(
            "Shares outstanding <= 0; per-share value undefined and reported "
            "as 0.0."
        )
    else:
        per_share = _finite(equity_value / shares, 0.0)

    return ValuationResult(
        per_share_value=round(per_share, 2),
        enterprise_value=round(enterprise_value, 2),
        equity_value=round(equity_value, 2),
        pv_explicit_fcf=round(pv_explicit, 2),
        pv_terminal_value=round(pv_terminal, 2),
        horizon=horizon,
        terminal_growth_clamped=terminal_growth_clamped,
        assumptions=inputs.assumptions(),
        input_provenance=dict(inputs.input_provenance),
        disclaimer=DISCLAIMER,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Scenario overrides — shared by scenario_valuation + the disruption layer
# ---------------------------------------------------------------------------

# Override keys map to ValuationInputs fields. A "margin" override is sugar: it
# scales fcf_0 multiplicatively (a margin expansion/compression proxy) without
# the caller needing to recompute fcf_0 by hand.
_DIRECT_OVERRIDE_FIELDS: frozenset[str] = frozenset({
    "fcf_0", "fcf_growth", "discount_rate", "terminal_growth",
    "shares_outstanding", "net_debt",
})


def _apply_overrides(base: ValuationInputs, overrides: dict) -> ValuationInputs:
    """Return a copy of *base* with *overrides* applied.

    Recognised keys: the six :class:`ValuationInputs` fundamentals, plus the
    convenience key ``"margin"`` (a multiplicative scale on ``fcf_0``, modelling
    a margin expansion/compression). Unknown keys are ignored. Provenance is
    carried over from *base* (an overridden field stays ``"assumed"`` unless the
    caller separately marks it ``"real"`` — overrides are scenario knobs, not
    measurements).
    """
    overrides = overrides or {}
    kwargs: dict = {}
    for key, value in overrides.items():
        if key in _DIRECT_OVERRIDE_FIELDS:
            kwargs[key] = value

    new_inputs = replace(base, **kwargs) if kwargs else replace(base)

    # "margin" sugar: scale fcf_0 (post any direct fcf_0 override).
    if "margin" in overrides:
        margin_scale = _finite(overrides["margin"], 1.0)
        new_inputs = replace(new_inputs, fcf_0=_finite(new_inputs.fcf_0, 0.0) * margin_scale)

    # Preserve the base provenance map on the copy.
    new_inputs.input_provenance = dict(base.input_provenance)
    return new_inputs


# ---------------------------------------------------------------------------
# 2. Scenario valuation
# ---------------------------------------------------------------------------

def scenario_valuation(
    base_inputs: ValuationInputs,
    *,
    scenarios: dict,
    horizon: int = 5,
) -> dict:
    """Run a DCF per named scenario → ``{name: ValuationResult}``.

    Each scenario is a dict of overrides on ``base_inputs`` (see
    :func:`_apply_overrides`): e.g. ``{"best": {"fcf_growth": 0.12,
    "discount_rate": 0.09}, "worst": {"fcf_growth": -0.05,
    "discount_rate": 0.13}}``. When the scenarios are ordered worst → base →
    best in their *economics* (lower growth + higher discount rate = worse),
    the resulting per-share values satisfy the natural invariant
    ``worst <= base <= best`` — the module's scenario tests assert exactly this.

    Parameters
    ----------
    base_inputs:
        The base-case :class:`ValuationInputs`.
    scenarios:
        ``{scenario_name: overrides_dict}``. An empty / ``None`` mapping yields
        an empty result.

    Returns
    -------
    dict[str, ValuationResult]
        One result per scenario name, each carrying the disclaimer + provenance.
    """
    scenarios = scenarios or {}
    out: dict[str, ValuationResult] = {}
    for name, overrides in scenarios.items():
        scen_inputs = _apply_overrides(base_inputs, overrides if isinstance(overrides, dict) else {})
        # Uniform horizon across scenarios: horizon is a modeling choice, not a
        # per-scenario economic, so varying it would break worst<=base<=best.
        result = dcf_valuation(scen_inputs, horizon=horizon)
        result.notes = list(result.notes) + [f"Scenario: {name}."]
        out[str(name)] = result
    return out


# ---------------------------------------------------------------------------
# 3. Monte-Carlo valuation
# ---------------------------------------------------------------------------

def _sample_distribution(rng: np.random.Generator, kind: str, params, n: int) -> np.ndarray:
    """Draw *n* samples of one input from a named distribution.

    Supported kinds:
      * ``"normal"``      — params ``(mu, sigma)``
      * ``"triangular"``  — params ``(low, mode, high)``
      * ``"uniform"``     — params ``(low, high)``
      * ``"lognormal"``   — params ``(mean, sigma)`` of the underlying normal
      * ``"fixed"``       — params ``(value,)`` (a constant column; useful to
                            pin one input while varying others)

    An unknown kind, malformed params, or a degenerate spread degrades to a
    constant column at the first finite param (or 0.0), never raising.
    """
    kind = str(kind).lower().strip()
    p = [float(x) for x in (params if isinstance(params, (list, tuple, np.ndarray)) else [params])]

    try:
        if kind == "normal" and len(p) >= 2:
            return rng.normal(p[0], max(0.0, p[1]), n)
        if kind == "triangular" and len(p) >= 3:
            lo, mode, hi = sorted([p[0], p[1], p[2]])
            # numpy requires lo <= mode <= hi and lo < hi.
            if hi <= lo:
                return np.full(n, lo)
            mode = min(max(mode, lo), hi)
            return rng.triangular(lo, mode, hi, n)
        if kind == "uniform" and len(p) >= 2:
            lo, hi = sorted([p[0], p[1]])
            if hi <= lo:
                return np.full(n, lo)
            return rng.uniform(lo, hi, n)
        if kind == "lognormal" and len(p) >= 2:
            return rng.lognormal(p[0], max(0.0, p[1]), n)
        if kind == "fixed" and len(p) >= 1:
            return np.full(n, p[0])
    except Exception:  # pragma: no cover - defensive
        pass
    # Fallback: a constant column at the first finite param, else zeros.
    base = next((x for x in p if np.isfinite(x)), 0.0)
    return np.full(n, base)


def monte_carlo_valuation(
    base_inputs: ValuationInputs,
    *,
    distributions: dict,
    n: int = 10000,
    seed: int = 0,
    horizon: int = 5,
) -> dict:
    """Monte-Carlo the DCF over uncertain inputs → a percentile summary.

    Each draw samples the inputs named in ``distributions``, overrides them on
    ``base_inputs``, runs :func:`dcf_valuation`, and collects the per-share
    value. The simulation is **deterministic given** ``seed`` — it uses
    ``numpy.random.default_rng(seed)`` — so the same seed reproduces the same
    summary exactly, and a different seed produces different draws.

    Degenerate draws are handled explicitly: a draw whose sampled
    ``discount_rate <= terminal_growth`` would force the DCF's terminal-growth
    clamp. Such draws are **skipped** (not silently clamped into the
    distribution), and the count is reported as ``skipped`` so the caller can
    see how much of the sampled space was non-economic. Non-finite per-share
    results are likewise skipped.

    Parameters
    ----------
    base_inputs:
        Base-case :class:`ValuationInputs`; any input not in ``distributions``
        is held at its base value across all draws.
    distributions:
        ``{input_name: (kind, params)}`` — see :func:`_sample_distribution` for
        the supported kinds. Only the six fundamentals are sampleable; unknown
        names are ignored.
    n:
        Number of draws (>= 1).
    seed:
        Seed for ``numpy.random.default_rng`` — fixes the whole simulation.
    horizon:
        Explicit DCF horizon used for every draw.

    Returns
    -------
    dict
        ``mean, median, std, p5, p25, p50, p75, p95, min, max, n_requested,
        n_valid, skipped, percentiles`` (a ready-to-plot ``{label: value}``
        map), plus ``assumptions``, ``input_provenance`` and ``disclaimer``.
        When every draw is degenerate the numeric fields are 0.0 and ``skipped``
        equals ``n``.
    """
    n = max(1, int(n))
    horizon = max(1, int(horizon))
    distributions = distributions or {}
    rng = np.random.default_rng(seed)

    # Pre-sample each distributed input into an (n,) column. Sampling order is
    # sorted by name so the draw stream is deterministic regardless of dict
    # insertion order.
    sampled: dict[str, np.ndarray] = {}
    for name in sorted(distributions.keys()):
        if name not in _DIRECT_OVERRIDE_FIELDS:
            continue
        spec = distributions[name]
        if not (isinstance(spec, (list, tuple)) and len(spec) >= 2):
            continue
        kind, params = spec[0], spec[1]
        sampled[name] = _sample_distribution(rng, kind, params, n)

    per_share = np.empty(n, dtype=float)
    valid = np.zeros(n, dtype=bool)
    skipped = 0

    base_term_growth = _finite(base_inputs.terminal_growth, 0.0)

    for i in range(n):
        overrides: dict = {}
        for name, col in sampled.items():
            overrides[name] = float(col[i])

        # Degenerate-draw guard: a sampled discount_rate within the terminal-
        # spread floor of (or below) the terminal growth is non-economic — the
        # Gordon term gets clamped to the floor, pulling a giant (bounded) value
        # into the distribution and biasing mean/max. Skip it (and count it),
        # mirroring the DCF clamp threshold, rather than letting it through.
        draw_r = overrides.get("discount_rate", _finite(base_inputs.discount_rate, 0.0))
        draw_g = overrides.get("terminal_growth", base_term_growth)
        if draw_r - draw_g < _MIN_DISCOUNT_TERMINAL_SPREAD:
            skipped += 1
            continue

        scen_inputs = _apply_overrides(base_inputs, overrides)
        res = dcf_valuation(scen_inputs, horizon=horizon)
        val = res.per_share_value
        if not np.isfinite(val):
            skipped += 1
            continue
        per_share[i] = val
        valid[i] = True

    vals = per_share[valid]
    n_valid = int(vals.size)

    if n_valid == 0:
        summary_vals = {
            "mean": 0.0, "median": 0.0, "std": 0.0,
            "p5": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0,
            "min": 0.0, "max": 0.0,
        }
    else:
        p5, p25, p50, p75, p95 = (
            float(np.percentile(vals, q)) for q in (5, 25, 50, 75, 95)
        )
        summary_vals = {
            "mean": round(float(np.mean(vals)), 2),
            "median": round(p50, 2),
            "std": round(float(np.std(vals)), 4),
            "p5": round(p5, 2),
            "p25": round(p25, 2),
            "p50": round(p50, 2),
            "p75": round(p75, 2),
            "p95": round(p95, 2),
            "min": round(float(np.min(vals)), 2),
            "max": round(float(np.max(vals)), 2),
        }

    out = dict(summary_vals)
    out["n_requested"] = n
    out["n_valid"] = n_valid
    out["skipped"] = skipped
    # A plot-friendly percentile map (the shape a fan/percentile chart wants).
    out["percentiles"] = {
        "p5": summary_vals["p5"],
        "p25": summary_vals["p25"],
        "p50": summary_vals["p50"],
        "p75": summary_vals["p75"],
        "p95": summary_vals["p95"],
    }
    out["assumptions"] = base_inputs.assumptions()
    out["input_provenance"] = dict(base_inputs.input_provenance)
    out["disclaimer"] = DISCLAIMER
    return out


# ---------------------------------------------------------------------------
# 4. Sensitivity analysis / tornado
# ---------------------------------------------------------------------------

def sensitivity_analysis(
    base_inputs: ValuationInputs,
    *,
    ranges: dict,
) -> list:
    """One-at-a-time sensitivity → tornado-chart rows, sorted by swing desc.

    For each input named in ``ranges`` (mapping ``name -> (low, high)``) the
    per-share value is recomputed at the *low* and *high* end while every other
    input is held at its base value. The ``swing`` is ``abs(high_value -
    low_value)`` — the width of that input's bar on a tornado chart. The rows
    are returned sorted by ``swing`` descending, so the dominant driver is the
    first element.

    Parameters
    ----------
    base_inputs:
        Base-case :class:`ValuationInputs`.
    ranges:
        ``{input_name: (low, high)}``. Only the six fundamentals are supported;
        unknown names are skipped. ``None`` / empty yields an empty list.

    Returns
    -------
    list[dict]
        Each row: ``{"input", "low", "high", "low_value", "high_value",
        "swing", "base_value"}`` where ``low_value`` / ``high_value`` are the
        per-share values at the range endpoints. Sorted by ``swing`` desc.
        Carries no per-row disclaimer (it is structural chart data), but every
        underlying per-share value comes from a disclaimer-bearing
        :class:`ValuationResult`.
    """
    ranges = ranges or {}
    base_value = dcf_valuation(base_inputs).per_share_value

    rows: list[dict] = []
    for name, bounds in ranges.items():
        if name not in _DIRECT_OVERRIDE_FIELDS:
            continue
        if not (isinstance(bounds, (list, tuple)) and len(bounds) >= 2):
            continue
        low, high = bounds[0], bounds[1]

        low_value = dcf_valuation(_apply_overrides(base_inputs, {name: low})).per_share_value
        high_value = dcf_valuation(_apply_overrides(base_inputs, {name: high})).per_share_value
        swing = abs(_finite(high_value) - _finite(low_value))

        rows.append({
            "input": name,
            "low": low,
            "high": high,
            "low_value": round(_finite(low_value), 2),
            "high_value": round(_finite(high_value), 2),
            "swing": round(swing, 2),
            "base_value": round(_finite(base_value), 2),
        })

    rows.sort(key=lambda r: r["swing"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# 5. Disruption linkage — PURE (severity is just a float)
# ---------------------------------------------------------------------------
#
# The actual disruption SOURCE is kept out of this math: the only coupling is a
# scalar ``severity`` in [0, 1]. A separate adapter (the orchestrator's job)
# derives that float from processing.shipping_stress_index (the modeled SSI) or
# world_graph_criticality. The mapping below — severity → a worse fundamental
# picture — is documented and explicit, never learned.

# How severity haircuts near-term growth and lifts the discount rate. At
# severity = 1.0 the near-term growth rate is cut by the full
# _MAX_GROWTH_HAIRCUT (absolute, in growth-rate points) and the discount rate
# is raised by the full _MAX_DISCOUNT_PREMIUM (a risk premium). Both scale
# linearly with severity, so severity is weakly monotonic in "worse": higher
# severity never raises the per-share value.
_MAX_GROWTH_HAIRCUT: float = 0.08      # up to -8 growth-rate points at severity 1.0
_MAX_DISCOUNT_PREMIUM: float = 0.05    # up to +5.0 discount-rate points at severity 1.0


def disruption_adjusted_inputs(
    base_inputs: ValuationInputs,
    severity: float,
) -> ValuationInputs:
    """Return a copy of *base_inputs* worsened in proportion to *severity*.

    The disruption→fundamentals mapping (explicit, documented, not learned):

    * **Near-term FCF growth is haircut.** Every projection-year growth rate is
      reduced by ``severity * _MAX_GROWTH_HAIRCUT`` (absolute growth-rate
      points). A scalar growth becomes a single reduced scalar; a per-year
      sequence has the haircut applied to each year. So a 6% growth at
      severity 0.5 becomes ``0.06 - 0.5*0.08 = 0.02``.
    * **The discount rate is raised as a risk premium.** ``discount_rate`` is
      increased by ``severity * _MAX_DISCOUNT_PREMIUM``. So a 10% WACC at
      severity 0.5 becomes ``0.10 + 0.5*0.05 = 0.125``.

    Both effects push value *down*, so the composition guarantees the property
    asserted in the tests: **higher severity → weakly lower
    :func:`dcf_valuation` per-share value** (monotone non-increasing). Severity
    is clamped to ``[0, 1]``.

    NOTE: ``severity`` is the *only* coupling to the disruption model — this
    function takes a float, never a stress report, so the math stays pure and
    fully offline-testable. The orchestrator's adapter is responsible for
    deriving the float from the (modeled) SSI or world-graph criticality.
    """
    sev = max(0.0, min(1.0, _finite(severity, 0.0)))
    growth_haircut = sev * _MAX_GROWTH_HAIRCUT
    discount_premium = sev * _MAX_DISCOUNT_PREMIUM

    # Haircut the growth — scalar stays scalar, sequence stays sequence.
    g = base_inputs.fcf_growth
    if isinstance(g, (int, float, np.floating, np.integer)):
        new_growth: object = _finite(g, 0.0) - growth_haircut
    elif isinstance(g, (Sequence, np.ndarray)):
        new_growth = [_finite(x, 0.0) - growth_haircut for x in list(g)]
    else:
        new_growth = -growth_haircut

    new_discount = _finite(base_inputs.discount_rate, 0.0) + discount_premium

    adjusted = replace(
        base_inputs,
        fcf_growth=new_growth,
        discount_rate=new_discount,
    )
    adjusted.input_provenance = dict(base_inputs.input_provenance)
    return adjusted


def build_disruption_scenarios(
    base_inputs: ValuationInputs,
    severity: float,
    *,
    best_growth_uplift: float = 0.04,
    best_discount_relief: float = 0.01,
) -> dict:
    """Build a worst/base/best scenarios dict for :func:`scenario_valuation`.

    * ``"worst"`` is driven by the disruption layer: its overrides come from
      :func:`disruption_adjusted_inputs` at the given ``severity`` (haircut
      growth + risk-premium discount rate).
    * ``"base"`` is the unmodified base case (empty overrides).
    * ``"best"`` is a symmetric upside: growth lifted by ``best_growth_uplift``
      and the discount rate relieved by ``best_discount_relief``.

    Because the worst case only ever *worsens* fundamentals and the best case
    only ever *improves* them relative to base, feeding this dict to
    :func:`scenario_valuation` preserves the ``worst <= base <= best``
    per-share invariant.

    Returns a plain ``{name: overrides}`` dict — the exact shape #2 expects.
    """
    adj = disruption_adjusted_inputs(base_inputs, severity)

    worst_overrides = {
        "fcf_growth": adj.fcf_growth,
        "discount_rate": adj.discount_rate,
    }

    base_growth = base_inputs.fcf_growth
    if isinstance(base_growth, (int, float, np.floating, np.integer)):
        best_growth: object = _finite(base_growth, 0.0) + best_growth_uplift
    elif isinstance(base_growth, (Sequence, np.ndarray)):
        best_growth = [_finite(x, 0.0) + best_growth_uplift for x in list(base_growth)]
    else:
        best_growth = best_growth_uplift

    best_overrides = {
        "fcf_growth": best_growth,
        "discount_rate": max(0.0, _finite(base_inputs.discount_rate, 0.0) - best_discount_relief),
    }

    return {
        "worst": worst_overrides,
        "base": {},
        "best": best_overrides,
    }


def severity_from_ssi(ssi_value: float, *, ssi_max: float = 100.0) -> float:
    """Normalise a stress reading to a ``[0, 1]`` disruption severity.

    A thin, network-free convenience: ``severity = clamp(ssi_value / ssi_max,
    0, 1)``. Pass ``ssi_max=1.0`` for an SSI already on a 0–1 axis (as
    ``processing.shipping_stress_index`` emits its ``overall_ssi``), or the
    natural maximum of whatever stress scale you hold.

    NOTE: the SSI itself is a **modeled** index (see docs/DATA_PROVENANCE.md) —
    this helper only rescales a number; it does not make the input real. It is
    deliberately the *only* bridge between the disruption model and this pure
    valuation math, and even it takes a plain float, not a stress report.
    """
    ssi_max = _finite(ssi_max, 100.0)
    if ssi_max <= 0.0:
        return 0.0
    return max(0.0, min(1.0, _finite(ssi_value, 0.0) / ssi_max))


# ---------------------------------------------------------------------------
# Convenience: an illustrative input set + a human-readable summary
# ---------------------------------------------------------------------------

def illustrative_inputs(**overrides) -> ValuationInputs:
    """Return a :class:`ValuationInputs` of illustrative defaults.

    Pure convenience for callers/tests that want the standard assumed-default
    set without restating it. Any keyword overrides a default — but the
    provenance stays ``"assumed"`` (these are not measured values). This exists
    precisely so the *assumed* nature is centralised and obvious.
    """
    base = ValuationInputs()
    if overrides:
        # Only forward recognised fields; ignore the rest.
        kwargs = {k: v for k, v in overrides.items() if k in _DIRECT_OVERRIDE_FIELDS}
        base = replace(base, **kwargs)
        base.input_provenance = {name: "assumed" for name in _FUNDAMENTAL_INPUTS}
    return base


# ---------------------------------------------------------------------------
# R047 — bridge REAL Alpha Vantage fundamentals into ValuationInputs.
#
# The valuation math stays pure + network-free (see the module docstring): this
# bridge takes an already-fetched ``av_data`` dict as an ARGUMENT and never
# touches the network or cache. The cache-only read that produces ``av_data``
# lives in the orchestration layer (``processing.company_profiler`` /
# ``data.alphavantage_feed``), so a hot-path render can NEVER trigger a live AV
# fetch and blow the 25/day free-tier quota.
#
# Provenance rule (honest by construction):
#   * A ValuationInputs field is stamped ``"real"`` ONLY when it is populated
#     from a genuinely-present AV measurement (a non-zero / non-sentinel value
#     that the feed actually returned). AV's ``_safe_float`` coalesces missing
#     values to ``0.0``, so we treat a 0.0 as "not covered" and LEAVE the field
#     at its assumed default — never fabricating a "real" flag off a sentinel.
#   * Every field the feed does NOT cover stays the existing assumed default
#     with provenance ``"assumed"`` — i.e. a fully-dark feed reproduces today's
#     all-assumed behaviour byte-for-byte.
#
# AV → ValuationInputs mapping (each documented as direct-measure vs proxy):
#   fcf_0          ← EBITDA proxy (CompanyIncome.ebitda). A real, measured AV
#                    figure used as a first-order FCF stand-in. Flagged "real"
#                    (the underlying EBITDA is measured) but it is a PROXY for
#                    free cash flow — see the note appended to the result.
#   fcf_growth     ← revenue_growth_yoy_pct / 100 (measured YoY growth). "real".
#   discount_rate  ← left ASSUMED. AV gives beta, but a CAPM discount rate needs
#                    an assumed risk-free + equity-risk-premium, so the result
#                    would be modeled, not measured — we do NOT stamp it "real".
#   shares_outstanding ← market_cap (USD bn) / price-proxy. Only set when both a
#                    real market cap AND a real per-share figure are present;
#                    otherwise left assumed. "real" when set.
#   terminal_growth, net_debt ← left ASSUMED (AV OVERVIEW exposes neither a
#                    perpetual-growth nor a net-debt figure honestly).
# ---------------------------------------------------------------------------

# AV value keys this bridge understands. All are optional; a missing or
# zero/sentinel value leaves the corresponding ValuationInputs field assumed.
_AV_VALUATION_KEYS: tuple[str, ...] = (
    "ebitda",                  # annual EBITDA (FCF proxy) — currency units
    "revenue_growth_yoy_pct",  # measured YoY revenue/earnings growth, percent
    "market_cap_bn",           # market capitalisation, USD billions
    "price",                   # per-share price (to derive a share count)
)


def _av_real(value: object) -> float | None:
    """Return a positive finite float from an AV value, else ``None``.

    AV's parser coalesces every missing field to ``0.0``, so a 0.0 is
    indistinguishable from "not reported" — we treat it as NOT covered and
    return ``None`` so the caller leaves that ValuationInputs field assumed.
    Negative values (e.g. a loss-making EBITDA) are also treated as not-usable
    for the FCF/share-count proxies and return ``None``.
    """
    v = _finite(value, default=float("nan"))
    if not np.isfinite(v) or v <= 0.0:
        return None
    return v


def fundamentals_to_valuation_inputs(
    ticker: str = "",
    *,
    av_data: dict | None = None,
) -> ValuationInputs:
    """Build :class:`ValuationInputs` from REAL Alpha Vantage fundamentals.

    Pure + offline: ``av_data`` is an already-fetched dict (see
    ``_AV_VALUATION_KEYS``); this function never fetches. When a value is
    genuinely present (positive + finite) the corresponding ValuationInputs
    field is populated from it and stamped ``"real"``; every field the feed does
    not cover stays the standard assumed default with provenance ``"assumed"``.

    A ``None`` / empty / fully-sentinel ``av_data`` therefore returns the exact
    ``ValuationInputs()`` all-assumed default — today's behaviour, unchanged.

    Never raises on a malformed payload: unknown keys are ignored and any value
    that does not parse to a usable positive float is treated as not-covered.

    Parameters
    ----------
    ticker:
        Cosmetic only (kept for symmetry with the orchestration call site /
        logging); the mapping does not depend on it.
    av_data:
        Optional dict with any of ``_AV_VALUATION_KEYS``. See the mapping in the
        module-level comment above.
    """
    base = ValuationInputs()  # all-assumed defaults
    data = dict(av_data) if isinstance(av_data, dict) else {}
    if not data:
        return base  # dark feed → byte-for-byte the assumed default

    kwargs: dict = {}
    real_fields: list[str] = []
    notes: list[str] = []

    # fcf_0 ← EBITDA proxy (real, measured — but a proxy for FCF).
    ebitda = _av_real(data.get("ebitda"))
    if ebitda is not None:
        kwargs["fcf_0"] = ebitda
        real_fields.append("fcf_0")
        notes.append("fcf_0 set from real AV EBITDA as an FCF proxy.")

    # fcf_growth ← measured YoY growth (percent → fraction).
    growth_pct = data.get("revenue_growth_yoy_pct")
    growth_val = _finite(growth_pct, default=float("nan"))
    # Growth can legitimately be negative or zero; only require it be finite AND
    # actually supplied (None / sentinel strings → not covered).
    if growth_pct not in (None, "", "None", "N/A", "-") and np.isfinite(growth_val):
        kwargs["fcf_growth"] = growth_val / 100.0
        real_fields.append("fcf_growth")

    # shares_outstanding ← market_cap (USD bn) / price. Needs BOTH real.
    mcap_bn = _av_real(data.get("market_cap_bn"))
    price = _av_real(data.get("price"))
    if mcap_bn is not None and price is not None:
        # market cap in USD billions / price-per-share → shares in billions →
        # convert to the millions unit ValuationInputs uses for shares.
        shares_millions = (mcap_bn * 1e9 / price) / 1e6
        if np.isfinite(shares_millions) and shares_millions > 0.0:
            kwargs["shares_outstanding"] = shares_millions
            real_fields.append("shares_outstanding")

    if not real_fields:
        return base  # nothing usable in the payload → assumed default

    inputs = replace(base, **kwargs)
    # Stamp provenance: real for the fields we populated, assumed for the rest.
    prov = {name: "assumed" for name in _FUNDAMENTAL_INPUTS}
    for name in real_fields:
        prov[name] = "real"
    inputs.input_provenance = prov
    return inputs


def summarize_valuation(result: ValuationResult, *, ticker: str = "") -> str:
    """One-line human-readable summary of a :class:`ValuationResult`.

    Always leads with the per-share value and always ends with the disclaimer,
    and flags when any input is assumed (which, per the audit, is the norm
    here). Never presents the number as a price target.
    """
    tick = (str(ticker).strip() + " ") if ticker else ""
    assumed = [k for k, v in result.input_provenance.items() if v != "real"]
    prov_note = (
        f" Inputs assumed (not measured): {', '.join(sorted(assumed))}."
        if assumed else " All inputs flagged real."
    )
    clamp_note = " [terminal growth clamped: r<=g]" if result.terminal_growth_clamped else ""
    return (
        f"{tick}illustrative DCF: ~{result.per_share_value:.2f}/share "
        f"(equity value {result.equity_value:.0f}, "
        f"EV {result.enterprise_value:.0f}, {result.horizon}y horizon)"
        f"{clamp_note}.{prov_note} {result.disclaimer}"
    )
