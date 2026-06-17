# Session 2026‑06‑17 — How it was put together & how it works

A plain‑language, diagram‑first walkthrough of everything shipped in this session,
so a new reader can follow the *why*, the *flow*, and *where the code lives*
without reading every file. Diagrams are [Mermaid](https://mermaid.js.org) — they
render on GitHub and in most Markdown viewers.

The one rule that governs every box below: **real data or an honest empty — never
a fabricated number; every modeled layer is stamped `modeled`.**

---

## 1. The big picture — what happened, in order

```mermaid
flowchart TD
    A["Resumed: working tree had 3 half-built features<br/>(R125, R034, R043)"] --> B{Finish the in-flight work}
    B --> B1["R125 — Persisted-Book Risk<br/>(had module+test+UI → verified)"]
    B --> B2["R034 — Escalation Ladder<br/>(added defining-property tests)"]
    B --> B3["R043 — Self-Tuning Thresholds<br/>(added tests + the 'apply' UI panel)"]
    B1 & B2 & B3 --> C["Full suite green → 3 commits + mark SHIPPED → push"]
    C --> D["Launch 4 research agents<br/>(risk · maritime · alerting · data-integrity)"]
    D --> E["15 verified, code-grounded improvements found"]
    E --> F{Implement the cleanly-shippable ones}
    F --> F1["IMP-1 Cascade Stress-VaR on the real book"]
    F --> F2["IMP-2 Price R034 tail into the SSI + early-warning"]
    F --> F3["IMP-3 Not-advice disclosure on every export"]
    F --> G["IMP-4 Alert auto-resolve — DEFERRED<br/>(no production scan loop exists yet)"]
    F1 & F2 & F3 --> H["Tests green → commits → push"]
    E --> I["All 15 findings logged to recommendations.csv<br/>(R252–R266) so none are lost"]
    G --> I
```

**Reading it:** the left spine is *do the work that was already started*, the right
spine is *find and add more*. Anything that couldn't be shipped *honestly and
completely* this session was written down as a tracked backlog item instead of
half-built (see §6 on IMP‑4).

---

## 2. The three features that were already in flight

### R125 — Persisted‑Book Risk *(risk a desk actually runs each morning)*

```mermaid
flowchart LR
    P["state.positions<br/>(your DURABLE saved book)"] --> W["book_weights_detail<br/>REAL marked weights"]
    P --> M["mark_book → priced market value"]
    SD["stock_data<br/>(real prices)"] --> RP["returns_panel<br/>look-ahead-free daily returns"]
    W --> VAR["risk_lab.portfolio_var<br/>VaR / CVaR sized in $"]
    RP --> VAR
    W --> SC["risk_lab.stress_test_all_scenarios<br/>every BUILTIN scenario on the live book"]
    VAR & SC --> UI["tab_portfolio → Persisted-Book Risk section"]
    EMPTY["empty book / dark prices"] -.->|"honest empty (var=None, [])"| UI
```

*The point:* the risk cards run on the user's **actual** book and **real** returns,
not a hardcoded universe and not an `rng.normal` simulation.
Module: `processing/persisted_book_risk.py` · Tests: `tests/test_persisted_book_risk.py`.

### R034 — Escalation Ladder *(price the probability path, not a binary state)*

```mermaid
flowchart LR
    CP["Chokepoint current_risk_level<br/>+ disruption_type"] --> ST["map to a ladder rung"]
    ST --> LAD["Markov ladder<br/>DE_ESCALATING → TENSION → INCIDENT → PARTIAL → CLOSURE"]
    LAD -->|"roll forward N steps"| EXP["expected_score<br/>= Σ P(state) × severity(state)"]
    EXP --> BLEND["compute_chokepoint_risk_score<br/>max(deterministic, ladder)"]
    BLEND --> NOTE["stamped provenance = 'modeled'"]
```

*The point:* a passage at TENSION with a real road to CLOSURE scores **above** its
current deterministic level — the model prices the tail. The blend is `max(...)`, so
the ladder can only *raise* risk, never lower the deterministic floor; and it is
**opt‑in** (default off → byte‑for‑byte the old numbers).
Module: `processing/escalation_ladder.py` · Tests: `tests/test_escalation_ladder.py`.

### R043 — Self‑Tuning Thresholds *(close the loop: recommend + apply)*

```mermaid
flowchart LR
    H["historical alert fires<br/>(SQLite)"] --> SC["score each once<br/>(real backtest scoring)"]
    SC --> SW["sweep_thresholds<br/>re-filter fires across a candidate grid"]
    SW --> REC["recommend_threshold<br/>max hit-rate s.t. min-fire-count floor<br/>(tie → lower bar)"]
    REC -->|"no candidate clears the floor"| NONE["honest: recommend NOTHING"]
    REC -->|"winner"| APPLY["apply_recommended_threshold"]
    APPLY --> STORE["route_thresholds.set_threshold_for_key<br/>(reversible: carries prior value)"]
    STORE --> RATE["the rate detector reads the same store"]
```

*The point:* the operator no longer hand‑edits SQLite — the panel re‑scores each
alert type, recommends the bar that maximizes hit‑rate (refusing to trust a
2‑fire 100% fluke), and applies it reversibly.
Modules: `engine/alert_backtest.py`, `engine/route_thresholds.py` ·
UI: `ui/tab_alerts.py` (Self‑Tuning Thresholds panel) ·
Tests: `tests/test_threshold_tuner.py`.

---

## 3. The four research‑driven improvements

### IMP‑1 — Cascade Stress‑VaR on the REAL book *(the two halves that never met)*

```mermaid
flowchart LR
    subgraph Before["Before — disconnected"]
      E1["monte_carlo_book_es<br/>(best engine)"] -.->|"only ever saw"| U1["a hardcoded<br/>equal-weight universe"]
      B1["your real book"] -.->|"only ever saw"| C1["constant scenario<br/>multipliers"]
    end
    subgraph After["After — bridged (IMP-1)"]
      BK["persisted book<br/>REAL marked weights"] --> MC["monte_carlo_book_es"]
      ID["live cascade ideas<br/>(already on the tab)"] --> MC
      MC --> OUT["Stress-VaR/ES + per-name<br/>Euler attribution"]
      OUT --> T["tab_portfolio sub-panel:<br/>'which live disruption owns your tail'"]
    end
```

The user's real book is finally stressed by the **live disruption** the cascade
already scored, and the exact per‑name Euler split exposes a hidden shared bet
("3 of your names are secretly one Suez trade").
Added to `processing/persisted_book_risk.py` (`persisted_book_stress_var`).

### IMP‑2 — Price the R034 tail into the flagship index + an early warning

```mermaid
flowchart TD
    LAD["escalation_ladder (R034)"] --> SEAM["compute_shipping_stress(escalation_horizon=N)<br/>opt-in, default-off"]
    SEAM --> COMP["SSI chokepoint component<br/>(its single largest weight, 0.29)"]
    COMP --> SSI["overall Shipping Stress Index<br/>now moves on rising tension"]
    LAD --> SIG["escalation_alert_signals<br/>gate: elevated + forward floor + delta>0"]
    SIG --> CALL["tab_chokepoints early-warning banner<br/>(read-on-demand, NOT a pager)"]
    SIG -.->|"calm passage / already-hot"| SKIP["never fires"]
```

Before this, the ladder touched exactly one UI table and the headline index ran on
the bare deterministic score. *Why a banner and not a paging alert?* The chokepoint
registry carries **standing** state, so a registry‑driven pager would fire forever
from static config — see §5 (honesty/anti‑noise).
Touched: `processing/shipping_stress_index.py`, `processing/escalation_ladder.py`,
`ui/tab_chokepoints.py`.

### IMP‑3 — Not‑advice disclosure on every export

```mermaid
flowchart LR
    RD["report data (modeled signals,<br/>synthetic series)"] --> MD["markdown export"]
    RD --> XL["Excel export (every sheet)"]
    MD --> GATE["assert_disclosed(text)"]
    XL --> FOOT["_add_footer stamps MODELED_NOTICE"]
    GATE -->|"no marker → raises"| FAIL["DisclosureError<br/>(fails loudly, never ships)"]
    GATE -->|"marker present"| OK["artifact leaves the app disclosed"]
    FOOT --> OK
    CSV["CSV"] -.->|"out of scope: a row<br/>would corrupt the data grid"| X[ ]
```

The PDF/HTML reports were already disclosed (R005); the markdown + Excel files —
the ones that travel to Slack/Notion/email — were not. Now they carry a
`not investment advice` marker and a gate enforces it.
New: `utils/disclosure.py` · Touched: `utils/markdown_export.py`, `utils/excel_export.py`.

---

## 4. How the research was done (and why nothing was lost)

```mermaid
flowchart TD
    Q["4 parallel research agents,<br/>one per subsystem"] --> R["each: read code → verify the gap is real<br/>(file:line) → check it's not already built/proposed"]
    R --> S["15 verified, non-duplicate improvements"]
    S --> T1["3 shipped this session"]
    S --> T2["12 runner-ups"]
    T1 & T2 --> CSV["recommendations.csv rows R252–R266<br/>(SHIPPED ones logged with commit; rest carried)"]
```

Every agent was required to *prove* a gap with `file:line` evidence and confirm it
wasn't a duplicate of the 251 existing recs — so the backlog stays honest.

---

## 5. The honesty / anti‑noise model (the invariant behind every decision)

```mermaid
flowchart TD
    IN["any input"] --> Q{"real data available?"}
    Q -->|yes| REAL["compute on real data → label 'live'"]
    Q -->|"modeled overlay"| MOD["compute → label 'modeled' + carry a provenance note"]
    Q -->|"empty / dark / unknown"| EMPTY["return honest empty / None — NEVER fabricate"]
    REAL & MOD & EMPTY --> OUT["surface, labelled"]
    NOISE{"would it fire/ship constantly<br/>from static config?"} -->|yes| DEMOTE["demote to read-on-demand UI,<br/>not a pager (see IMP-2)"]
    NOISE -->|no| KEEP["keep"]
```

Two corollaries that shaped real decisions this session:
- **No fabrication:** every new risk path returns an honest empty on dark prices
  instead of an `rng.normal` stand‑in.
- **No dead/noisy surface:** the R034 escalation signal became a UI banner (not a
  pager) precisely because a registry‑driven pager would be constant noise; and
  IMP‑4 was *deferred* rather than half‑wired (next section).

---

## 6. What was deliberately NOT shipped — IMP‑4 (alert auto‑resolve)

The highest‑value finding (recovered CRITICAL alerts keep paging up the chain
forever, because escalation gates only on `acknowledged = 0`) was **deferred**, not
forced. Why:

```mermaid
flowchart LR
    NEED["auto-resolve needs to compare<br/>'what's firing now' vs 'what's open'"] --> LOOP{"is there a recurring<br/>alert-scan loop?"}
    LOOP -->|"the agent assumed yes"| ASSUME["run_all_checks()"]
    ASSUME --> REAL["…but run_all_checks has NO production caller<br/>(db_check_cli calls a same-named DB-health fn)"]
    REAL --> DECIDE["so a schema migration + escalation gate<br/>would be INERT until a scan loop exists"]
    DECIDE --> DEFER["logged as R260 with the prerequisite,<br/>rather than shipping dead surface"]
```

Building the scan loop first is the right next step — captured as **R260** in
`recommendations.csv`.

---

## 7. Module index (file → role → tests)

| File | Role | Tests |
|------|------|-------|
| `processing/persisted_book_risk.py` | Persisted‑book VaR + scenario stress (R125) **and** cascade Stress‑VaR (IMP‑1) | `tests/test_persisted_book_risk.py` |
| `processing/escalation_ladder.py` | Markov escalation ladder (R034) + early‑warning signal gate (IMP‑2) | `tests/test_escalation_ladder.py` |
| `processing/shipping_stress_index.py` | SSI; gained the opt‑in `escalation_horizon` seam (IMP‑2) | `tests/test_shipping_stress_index.py` |
| `engine/alert_backtest.py` | Backtest + sweep/recommend/apply self‑tuning loop (R043) | `tests/test_threshold_tuner.py` |
| `engine/route_thresholds.py` | Override store; gained reversible `set_threshold_for_key` (R043) | `tests/test_threshold_tuner.py` |
| `utils/disclosure.py` | Single source of the export not‑advice notice + `assert_disclosed` gate (IMP‑3) | `tests/test_export_disclosure.py` |
| `ui/tab_portfolio.py` · `ui/tab_chokepoints.py` · `ui/tab_alerts.py` | The UI surfaces for the above | `tests/test_tab_smoke.py` |
| `recommendations.csv` | The improvement backlog (R252–R266 added this session) | — |

> Pending integration as this doc was written: three more pure modules from
> background agents — `closure_scenario.py` (R258), `sized_book_risk.py` (R255),
> `signal_path_stats.py` (R256). They follow the same pattern and will appear in
> this table once merged.
