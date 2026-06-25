# Build Prompt — Ship Tracker Marketing Site (Claude / Claude Design)

> Paste everything below the line into Claude (claude.ai → Artifacts, or Claude
> Design). It is written to be executed verbatim. It specifies the product, the
> art direction, the information architecture, every section's copy and layout,
> the data-visualizations, the motion, the tech, and the quality bar. Build the
> whole thing; do not summarize or ask clarifying questions — make confident,
> tasteful decisions where a detail is unspecified.

---

## ROLE & MISSION

You are a senior art director + front-end engineer building the public marketing
site for **Ship Tracker — Global Shipping Intelligence**, an institutional-grade
platform that turns real maritime and market data into coverage-tested risk and
alpha for shipping-exposed portfolios and supply chains.

Produce a **single, self-contained, production-grade React site** (one artifact)
that looks like it was made by a top studio for a serious quant/maritime firm —
think the visual gravity of *The Wall Street Journal* and *FT*, the data rigor of
Bloomberg/Palantir/Kpler, and the craft of Stripe — rendered in a **nautical,
institutional** language. It must feel **expensive, precise, and trustworthy**,
never like a generic SaaS template. Every screen should reward a second look.

The site is a **showpiece**: heavily visual, data-driven, with bespoke charts and
restrained, purposeful motion. Default to "more designed," but always in service
of clarity and authority — this is for allocators, risk officers, and trading
desks, not consumers.

---

## THE PRODUCT (what the site is selling — use these real facts)

Ship Tracker ingests **real data feeds** — IMF PortWatch chokepoint transits
(Suez, Panama, Bab-el-Mandeb, Hormuz, Malacca…), 5-year equity histories for
shipping names (ZIM, MATX, SBLK, DAC, CMRE, STNG), FRED macro (Baltic Dry
Index, Brent/WTI, DXY, VIX), GDELT geopolitical events, AIS vessel tracks, UN
Comtrade flows, marine weather, and OFAC sanctions — and turns them into:

- **The Shipping Stress Index (SSI)** — a 6-component, prominence-weighted gauge
  of global shipping stress; the chokepoint component is the single heaviest weight.
- **Coverage-tested risk** — Value-at-Risk and Expected-Shortfall that are
  *statistically validated against realized profit-and-loss*, not just asserted.
  Headline proof: on 1,292 trading days × 6 shipping equities, the standard
  Gaussian VaR is **statistically rejected at 99%** (it under-states the fat tail),
  while a **Student-t tail (ν = 6)** passes both 95% and 99%, and its
  Expected-Shortfall is confirmed **well-scaled** by the Acerbi-Szekely test.
- **Chokepoint intelligence** — the two highest-leverage canal nodes (Suez,
  Panama) driven by *real* PortWatch transit data with a strict precedence ladder.
- **An honesty discipline** — every figure is labelled real vs modeled; nothing
  synthetic is ever presented as real. 20 backtest validators run continuously;
  the suite is exhaustively tested.

Positioning line you may use: **"The risk you can actually back-test."**
Alternate: **"Real data. Coverage-tested. From the chokepoint to the close."**

---

## ART DIRECTION — "Nautical Institutional"

The mood board is a **brass-and-charcoal nautical chart room** crossed with a
**broadsheet financial paper**. Deep water, steel instruments, brass fittings,
warm chart paper, and a single disciplined accent.

### Palette (use these exact values; this is the spine of the design)

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#0B1A2B` | primary text on light; near-black navy |
| `--hull` | `#0E2235` | darkest sections / hero ground |
| `--navy` | `#13283D` | dark surfaces, footer |
| `--steel` | `#2E5A7E` | primary brand blue (lines, primary UI) |
| `--steel-300` | `#7FA3C0` | secondary blue, muted data series |
| `--mist` | `#DCE6EF` | pale steel wash, light cards on dark |
| `--brass` | `#B0892F` | THE accent — rules, highlights, key numbers |
| `--brass-200` | `#E7D9B4` | brass tint fills, chips |
| `--paper` | `#F7F5EF` | warm paper background (light sections) |
| `--paper-card` | `#FFFFFF` | cards on paper |
| `--rule` | `#C9D2DB` | hairlines on light |
| `--rule-dark` | `#2A3F54` | hairlines on dark |
| `--signal-pos` | `#2F7D5B` | healthy / pass / well-scaled |
| `--signal-neg` | `#B23A3A` | reject / breach / critical |
| `--signal-warn` | `#C98A21` | elevated / caution |

Rules: **one accent only (brass)** — use it sparingly, like gold leaf on an
instrument; never fill large areas with it. Backgrounds alternate between **deep
navy/hull** (authority) and **warm paper** (clarity). Pure white only inside
cards. Red/green strictly for risk semantics, never decoration.

### Typography

Pair a **refined display serif** with a **precise grotesque** and a **mono for
figures**. Use Google Fonts (always load):

- **Display / headlines:** `Fraunces` (opsz, slight contrast) — or `Newsreader`
  as fallback. Large, tight tracking, confident. This is the WSJ/FT voice.
- **Body / UI:** `Inter` (or `Libre Franklin`) — clean, neutral, legible.
- **Numerals / data / labels / kicker:** `IBM Plex Mono` — tabular, instrument-like.
  Use mono for all metrics, axis labels, timestamps, and small-caps kickers.

Scale (desktop): hero display 64–88px; section headlines 36–44px; lead 20–22px;
body 16–17px; data labels 12–13px mono uppercase with +0.12em tracking. Generous
line-height on body (1.6). Headlines tight (1.05–1.1).

### Motifs & texture (this is what makes it "nautical," not generic)

Weave these in subtly and tastefully — they are seasoning, not the meal:

- **Bathymetric contour lines** (nautical chart depth contours) as faint
  background line-art in dark sections; thin steel strokes, very low opacity.
- **A graticule** (latitude/longitude grid) behind the hero and the map.
- **Brass hairlines** as section dividers and to underline key numbers (a 2px
  brass rule is the signature accent).
- **Depth soundings**: scatter small mono numerals like a chart's depth marks in
  one ambient area.
- **A compass rose** — minimal, single-weight line — as a small recurring mark
  (e.g., near the logo, or as a section ornament). Not cartoonish.
- **Knot / rope** only if extremely subtle (e.g., a thin divider) — when in
  doubt, leave it out. Avoid clip-art anchors, wheels, sailboats, emoji.

Imagery: **no stock photos of cargo ships.** Use data-driven and illustrative
graphics only — maps, charts, contour art, instrument dials. If you need an
"image," generate it as SVG/canvas (a stylized chokepoint map, a depth chart).

### Layout system

12-column grid, ~1200–1280px max content width, wide gutters, lots of negative
space (institutional = confident whitespace). Strong baseline alignment. Use
**thin hairline rules** to structure content like a broadsheet. Cards have a
1px rule + soft shadow on paper, or a subtle inner border on dark. Corner radius
small (4–8px) — crisp, not bubbly.

---

## SIGNATURE VISUAL MOMENTS (build these as real, animated components)

1. **Hero — "Deep Water."** Full-viewport dark `--hull` ground with a faint
   animated graticule + bathymetric contours drifting slowly (CSS/canvas, tide-
   like). Overlaid: a **live-style data ticker rail** (mono) showing SSI level,
   chokepoint statuses, and a VaR figure, gently cycling. A minimal **world
   shipping graph** (nodes = ports/chokepoints, brass edges = lanes) animates in
   with a draw-on effect. Headline in Fraunces; one brass rule; a single primary
   CTA. It should feel like looking at a lit instrument panel at night.

2. **The Shipping Stress Index gauge.** A bespoke radial/arc gauge (SVG) with a
   brass needle, steel arc, and the 6 components as small segmented contributors
   beneath. Animate the needle on scroll-into-view. Mono readouts.

3. **Chokepoint map.** A stylized, schematic world map (SVG — not a real tile
   map) marking Suez, Panama, Bab-el-Mandeb, Hormuz, Malacca with status dots
   (green/amber/red). On hover, a card shows transit drop % and risk level. A
   small legend explains the precedence: real PortWatch > real canal scrape >
   baseline. Graticule behind it.

4. **The proof chart — VaR/ES calibration.** A clean editorial chart (recharts or
   hand-built SVG) showing breach rate vs nominal at 95% and 99% for Gaussian vs
   Student-t, with the **Gaussian-99% bar flagged red ("REJECTED, p=0.021")** and
   the **Student-t bars green ("PASS")**. A companion micro-chart shows realized
   tail loss vs ES forecast (well-scaled). This is the credibility centerpiece —
   make it beautiful and unambiguous, annotated like an FT graphic.

5. **The honesty ledger.** A "real vs modeled" visual — e.g., a tally/grid of the
   20 validators with 2 marked REAL (brass) and 18 deterministic (steel), all
   healthy. Editorial, scannable, quietly confident.

Motion across all: **restrained and weighty** — slow, eased reveals (300–600ms,
custom cubic-bezier that feels like settling water), scroll-triggered draw-ons for
charts/maps, parallax depth only in the hero. No bouncy springs, no confetti, no
gratuitous hover wiggles. Respect `prefers-reduced-motion`.

---

## INFORMATION ARCHITECTURE (sections, in order)

Build all of these as one long, scroll-driven page with a sticky minimal nav.

1. **Top nav** — left: a small compass-rose mark + "SHIP TRACKER" in mono small-
   caps with brass dot. Right: anchor links (Platform · Risk Engine · Chokepoints
   · Methodology) + a brass-outlined "Request access" button. Transparent over the
   hero, condenses to `--navy` with a hairline on scroll.

2. **Hero ("Deep Water")** — see signature moment #1.
   - Kicker (mono): `INSTITUTIONAL SHIPPING INTELLIGENCE`
   - Headline (Fraunces): **"The risk you can actually back-test."**
   - Sub: "Ship Tracker turns real chokepoint transits, shipping-equity tape, and
     macro feeds into a stress index and coverage-tested VaR & Expected-Shortfall —
     validated against realized P&L, labelled real vs modeled, end to end."
   - Primary CTA: "Request access" · Secondary: "See the methodology ↓"
   - Live ticker rail beneath.

3. **The thesis** — a broadsheet two-column statement. Headline: **"Shipping
   disruption is mispriced risk."** Body: a tight, confident paragraph on why
   chokepoint shocks and freight regimes move shipping equities and supply chains,
   and why most "risk numbers" are never tested against what actually happened.
   Pull-quote with a brass rule.

4. **The platform — capability grid.** Section headline: **"From the chokepoint to
   the close."** A 2×3 (or 3×2) grid of capability cards, each with a tiny bespoke
   line-icon (SVG, single weight, nautical/instrument flavor) + a mono kicker +
   headline + one line:
   - Shipping Stress Index · "One number for global shipping stress, decomposed."
   - Coverage-tested risk · "VaR & ES validated against realized P&L."
   - Chokepoint intelligence · "Suez & Panama, live from PortWatch transit."
   - Real-data alpha · "Signals frozen point-in-time, marked net of cost."
   - Disruption event studies · "What real events did to real prices."
   - Honesty by default · "Every figure labelled real vs modeled."

5. **The Shipping Stress Index** — signature moment #2 (the gauge), left; copy
   right explaining the 6 components and the chokepoint dominance. Mono component
   readouts.

6. **Chokepoint intelligence** — signature moment #3 (the map), full-bleed dark
   section with graticule. Side panel explains the precedence ladder and the
   escalate-only rule. A small inline diagram of `transit drop → risk level`
   (≥50% CRITICAL, ≥30% HIGH, ≥15% MODERATE).

7. **Proof — "Tested against what happened."** Signature moment #4 (the VaR/ES
   calibration chart) on warm paper, treated like a featured FT graphic with a
   headline, a one-sentence dek, the chart, and a 3-bullet "how to read it"
   caption in mono. This is the section that wins the skeptic.

8. **Methodology & honesty** — signature moment #5 (the validator ledger) +
   editorial copy: "We label real vs modeled. We back-test. We publish the
   verdict — even when it's negative." Mention: 20 validators, point-in-time
   ledger, full test suite. Quiet, credible, no hype.

9. **A numbers band** — a thin full-width strip on `--navy` with 3–4 big mono
   stats + brass rules between: e.g., "1,292 days back-tested", "6 shipping
   equities", "20 live validators", "0 synthetic figures shown as real". Animate
   counts on view.

10. **CTA — "Request access."** Centered, paper or hull ground, a confident
    headline, one short line, an access form (email + firm + role) styled as
    instrument inputs (mono labels, hairline underlines, brass focus). A
    reassuring institutional note beneath (e.g., "For professional / institutional
    use. Illustrative; not investment advice.").

11. **Footer** — `--navy`, bathymetric contour art faint behind. Compass-rose
    mark + wordmark, a tidy link grid, a thin brass rule, and a small-print
    disclaimer line: "Ship Tracker is a research & analytics platform. Figures are
    illustrative and labelled real vs modeled; nothing herein is investment
    advice." Latitude/longitude style coordinates as a playful mono ornament.

---

## DATA-VIZ SPEC (build real charts, not images)

Use `recharts` if available in the artifact env, else hand-build with SVG. Style
ALL charts editorially: thin axes, no chartjunk, mono tick labels, hairline grid,
direct labels over legends where possible, the brass accent for the "hero" series.

- **VaR breach chart:** grouped bars, x = {95%, 99%}, series = {Gaussian (steel),
  Student-t (brass)}; dashed nominal reference lines at 5% and 1%; values:
  G95=4.88, t95=5.65, G99=1.70, t99=1.16; annotate each with its Kupiec p
  (0.84 / 0.29 / 0.021 / 0.57); outline G99 in `--signal-neg` with a "REJECTED"
  tag and t99 with a green "PASS" tag.
- **ES calibration micro-chart:** paired bars realized vs forecast at 95% and 99%
  (4.39 vs 4.45; 6.06 vs 6.17), tagged "well-scaled" green.
- **SSI gauge:** radial arc 0–100 with brass needle; 6 component sub-bars.
- **Chokepoint statuses:** map dots colored by risk level with hover cards.
- **Numbers band counters:** animated integer count-ups.

All numbers above are the real platform figures — use them as the example data.

---

## COPY & TONE

Voice: **institutional broadsheet** — precise, declarative, numerate, quietly
confident. Short sentences. No exclamation marks, no emoji, no buzzwords
("revolutionary", "AI-powered", "seamless", "unlock"), no hype. Lead with facts
and numbers. Where you assert a capability, ground it in a real figure. Headlines
are short and editorial (Fraunces); decks are one sentence; captions are mono.
Write all real copy — never lorem ipsum.

---

## TECH & IMPLEMENTATION

- **One self-contained React artifact** (default export a single `App`), no
  external assets beyond Google Fonts and (if available) `recharts` / `framer-
  motion` / `lucide-react`. If a lib isn't available, hand-build with SVG/CSS.
- **Tailwind** for layout/spacing; define the palette as CSS variables and map
  Tailwind to them (or use arbitrary values with the hexes above).
- **Motion:** `framer-motion` if available, else CSS `@keyframes` +
  IntersectionObserver for scroll reveals. Honor `prefers-reduced-motion`.
- **Responsive:** flawless from 360px to 1440px+. The hero, map, and charts must
  reflow gracefully; stack grids to one column on mobile; keep type legible.
- **Accessibility:** WCAG AA contrast on every text/background pair (the palette
  is chosen for this — verify brass-on-dark and steel-on-paper); semantic
  landmarks (`header/nav/main/section/footer`), `alt`/`aria-label` on viz,
  visible focus states (brass ring), keyboard-navigable nav and form.
- **Performance:** no heavy images; SVG/canvas only; lazy-reveal below the fold;
  smooth 60fps motion.
- **Polish:** consistent 8px spacing rhythm, aligned baselines, no orphaned
  hairlines, no overflow, no layout shift. Cross-check that the brass accent
  appears intentionally (a handful of places), not scattered.

---

## QUALITY BAR & ANTI-PATTERNS

Aim for "a serious firm paid a great studio for this." Specifically AVOID:
- generic SaaS look: purple/indigo gradients, blob shapes, glassmorphism, big
  rounded bubbly cards, center-everything hero with a floating screenshot;
- emoji, clip-art nautical icons (anchors/wheels/sailboats), stock cargo photos;
- rainbow chart palettes, 3D charts, chartjunk, drop-shadowed text;
- hypey marketing copy, lorem ipsum, fake logos "as seen in";
- motion for motion's sake (bouncy springs, parallax everywhere, autoplay video).

Instead deliver: disciplined nautical-institutional design, broadsheet typographic
hierarchy, bespoke editorial data-viz, restrained brass accenting, weighty
tasteful motion, and copy that reads like it was written by a quant who can write.

**Build the complete site now as one artifact. Make every decision in service of
authority, clarity, and craft.**
