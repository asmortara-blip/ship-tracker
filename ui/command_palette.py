"""Global command-palette / search for the shipping-analytics app.

Tames the 72-tab, 10-section navigation by letting the user type a query and
jump. Everything here is split into two layers:

* **Pure helpers** (``build_search_index`` / ``search_index``) — no ``st.*``,
  no imports of app state, no I/O. These are unit-tested offline.
* **A render layer** (``render_command_palette``) — draws a single
  ``st.text_input`` + a short list of result buttons. Clicking a result sets
  ``st.session_state["nav_section"]`` to the target section key and reruns.

Routing contract
----------------
Navigation lives entirely in ``st.session_state["nav_section"]`` (a section
key string). Streamlit's inner ``st.tabs()`` is client-side and cannot be
driven programmatically, so **every result routes to its parent SECTION** —
the user then clicks the inner tab. This mirrors the app's existing pinned-tab
behaviour. We never attempt to select an inner tab.

This module deliberately does **not** import ``app.py`` (that would be
circular). The catalog — ``SECTIONS`` and ``SECTION_TABS`` — is passed in by
the caller (the sidebar wiring in ``app.py``).
"""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = [
    "build_search_index",
    "search_index",
    "render_command_palette",
    "default_entities",
]


# ── Pure helpers (no Streamlit, no I/O) ────────────────────────────────────
def build_search_index(
    sections,
    section_tabs,
    *,
    entities: Optional[Iterable] = None,
) -> list[dict]:
    """Build a flat, searchable catalog of navigable targets.

    Produces one record per navigable target:

    * One ``{"kind": "section", ...}`` record per entry in ``sections``.
    * One ``{"kind": "tab", ...}`` record per tab in ``section_tabs``.
    * One record per optional ``entity`` (companies / ports / routes …).

    Every record carries a ``section`` key (the routing target) plus the
    section's display ``section_label`` and ``icon`` for breadcrumbs.

    Parameters
    ----------
    sections:
        Iterable of ``(key, icon, label, desc)`` tuples (extra fields ignored,
        short/missing fields tolerated). Matches ``app.SECTIONS``.
    section_tabs:
        Mapping ``{section_key: [(tab_label, module_path), ...]}``. Matches
        ``app.SECTION_TABS``. Tabs whose section is unknown still index, with a
        best-effort breadcrumb.
    entities:
        Optional iterable of ``(name, kind, section_key)`` tuples. Each becomes
        a ``{"kind": kind, "label": name, "section": section_key, ...}`` record.

    Robust to ``None`` inputs and to short/ragged tuples — it never raises.
    """
    # Map section_key -> (icon, label) for breadcrumb enrichment. Built first
    # so tab/entity records can resolve their section's display metadata.
    meta: dict[str, tuple[str, str]] = {}
    section_records: list[dict] = []

    for row in sections or []:
        # Tolerate short tuples: (key,), (key, icon), (key, icon, label), …
        if isinstance(row, str):
            seq: list = [row]
        else:
            try:
                seq = list(row)
            except TypeError:
                continue
        if not seq:
            continue
        key = _as_str(seq[0])
        if not key:
            continue
        icon = _as_str(seq[1]) if len(seq) > 1 else ""
        label = _as_str(seq[2]) if len(seq) > 2 else key
        meta[key] = (icon, label or key)
        section_records.append(
            {
                "kind": "section",
                "label": label or key,
                "section": key,
                "section_label": label or key,
                "icon": icon,
                "module": None,
            }
        )

    index: list[dict] = list(section_records)

    # Tabs: one record each, routing to the parent section.
    if section_tabs:
        try:
            items = section_tabs.items()
        except AttributeError:
            items = []
        for sec_key, tabs in items:
            sec_key = _as_str(sec_key)
            icon, sec_label = meta.get(sec_key, ("", sec_key))
            for tab in tabs or []:
                if isinstance(tab, str):
                    # A bare string is the whole tab label (don't iterate it
                    # into characters).
                    tseq: list = [tab]
                else:
                    try:
                        tseq = list(tab)
                    except TypeError:
                        tseq = [tab] if tab is not None else []
                if not tseq:
                    continue
                tab_label = _as_str(tseq[0])
                if not tab_label:
                    continue
                module = _as_str(tseq[1]) if len(tseq) > 1 else None
                index.append(
                    {
                        "kind": "tab",
                        "label": tab_label,
                        "section": sec_key,
                        "section_label": sec_label,
                        "icon": icon,
                        "module": module or None,
                    }
                )

    # Optional entities (companies, ports, routes, …).
    for ent in entities or []:
        if isinstance(ent, str):
            eseq: list = [ent]
        else:
            try:
                eseq = list(ent)
            except TypeError:
                continue
        if not eseq:
            continue
        name = _as_str(eseq[0])
        if not name:
            continue
        kind = (_as_str(eseq[1]) if len(eseq) > 1 else "") or "entity"
        sec_key = _as_str(eseq[2]) if len(eseq) > 2 else ""
        icon, sec_label = meta.get(sec_key, ("", sec_key))
        index.append(
            {
                "kind": kind,
                "label": name,
                "section": sec_key,
                "section_label": sec_label,
                "icon": icon,
                "module": None,
            }
        )

    return index


def search_index(index, query, *, limit: int = 12) -> list[dict]:
    """Case-insensitive ranked search over a built index.

    Ranking tiers (best first), matched against the record ``label`` and, as a
    weaker fallback, the ``section_label``:

    0. **exact** — label equals the query
    1. **prefix** — label starts with the query
    2. **word-boundary** — query starts a word inside the label
    3. **substring** — query appears anywhere in the label
    4. **section-label substring** — query appears in the breadcrumb only

    Ties break stably by case-insensitive label, then by original index order.
    A blank/empty query returns a sensible default: the first ``limit`` section
    records (so the palette opens onto the section list). Never raises.
    """
    try:
        records = list(index or [])
    except TypeError:
        return []

    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 12
    if lim <= 0:
        return []

    q = _as_str(query).strip().lower()

    if not q:
        # Default view: the section records, in their original order.
        defaults = [r for r in records if _rec_kind(r) == "section"]
        return defaults[:lim]

    scored: list[tuple[int, str, int, dict]] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        label = _as_str(rec.get("label"))
        sec_label = _as_str(rec.get("section_label"))
        rank = _rank(label.lower(), sec_label.lower(), q)
        if rank is None:
            continue
        scored.append((rank, label.lower(), i, rec))

    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [rec for _, _, _, rec in scored[:lim]]


def _rank(label_l: str, sec_label_l: str, q: str) -> Optional[int]:
    """Return the rank tier for a single record, or ``None`` if no match."""
    if not q:
        return None
    if label_l == q:
        return 0
    if label_l.startswith(q):
        return 1
    if _word_boundary_match(label_l, q):
        return 2
    if q in label_l:
        return 3
    if q in sec_label_l:
        return 4
    return None


def _word_boundary_match(label_l: str, q: str) -> bool:
    """True if ``q`` begins a word in ``label_l`` (after a space/sep char).

    Treats common separators in tab labels (space, ``-``, ``/``, ``&``,
    ``_``) as word boundaries. The leading-position case is already covered by
    the prefix tier, so this only looks at interior words.
    """
    seps = " -/&_"
    start = 0
    while True:
        idx = label_l.find(q, start)
        if idx <= 0:  # not found, or at position 0 (prefix tier handles it)
            return False
        if label_l[idx - 1] in seps:
            return True
        start = idx + 1


def _as_str(v) -> str:
    """Coerce to a stripped string; ``None`` -> ``""``. Never raises."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:
        return ""


def _rec_kind(rec) -> str:
    return _as_str(rec.get("kind")) if isinstance(rec, dict) else ""


# ── Best-effort real entities (imports live here, NOT in pure helpers) ──────
def default_entities() -> list[tuple[str, str, str]]:
    """Best-effort pull of a few real entities for the index.

    Returns ``(name, kind, section_key)`` tuples sourced from:

    * carriers — ``processing.company_profiler.COMPANY_PROFILES`` (→ carriers)
    * ports    — ``ports.port_registry.PORTS`` (→ ports_routes)
    * routes   — ``routes.route_registry.ROUTES`` (→ ports_routes)

    Each source is wrapped in its own try/except so a single import failure
    never sinks the rest. Always returns a list (possibly empty).
    """
    out: list[tuple[str, str, str]] = []

    # Carriers / companies.
    try:
        from processing.company_profiler import COMPANY_PROFILES

        for ticker, prof in COMPANY_PROFILES.items():
            name = ""
            if isinstance(prof, dict):
                name = _as_str(prof.get("name"))
            name = name or _as_str(ticker)
            if name:
                out.append((name, "company", "carriers"))
    except Exception:
        pass

    # Ports.
    try:
        from ports.port_registry import PORTS

        for p in PORTS:
            name = _as_str(getattr(p, "name", None)) or (
                _as_str(p.get("name")) if isinstance(p, dict) else ""
            )
            if name:
                out.append((name, "port", "ports_routes"))
    except Exception:
        pass

    # Routes.
    try:
        from routes.route_registry import ROUTES

        for r in ROUTES:
            name = _as_str(getattr(r, "name", None)) or (
                _as_str(r.get("name")) if isinstance(r, dict) else ""
            )
            if name:
                out.append((name, "route", "ports_routes"))
    except Exception:
        pass

    return out


# ── Render layer (Streamlit) ───────────────────────────────────────────────
def render_command_palette(
    sections,
    section_tabs,
    *,
    entities: Optional[Iterable] = None,
    key: str = "cmd_palette",
) -> None:
    """Render the command palette into the current (sidebar) container.

    Draws a single text input and up to ~10 result buttons. Clicking a result
    sets ``st.session_state["nav_section"]`` to the target section and reruns.

    The whole body is wrapped in try/except so a bug here can never crash the
    sidebar — at worst the palette silently renders nothing.
    """
    try:
        import streamlit as st
        from ui.styles import C_TEXT2, C_TEXT3, badge

        index = build_search_index(sections, section_tabs, entities=entities)

        query = st.text_input(
            "Command palette",
            key=f"{key}_query",
            placeholder="🔎 Jump to… (tab, section, company, port)",
            label_visibility="collapsed",
        )

        results = search_index(index, query, limit=10)

        if not results:
            if _as_str(query).strip():
                st.caption("No matches.")
            return

        # Kind → muted, accessible label that screen readers can read off the
        # badge. Tabs don't need a kind chip (they're the common case).
        kind_chip = {
            "section": "Section",
            "company": "Company",
            "port": "Port",
            "route": "Route",
        }

        for pos, rec in enumerate(results):
            label = _as_str(rec.get("label")) or "(unnamed)"
            icon = _as_str(rec.get("icon"))
            sec_label = _as_str(rec.get("section_label"))
            kind = _rec_kind(rec)

            btn_text = f"{icon}  {label}".strip()
            if st.button(
                btn_text,
                key=f"{key}_r{pos}",
                use_container_width=True,
            ):
                target = _as_str(rec.get("section"))
                if target:
                    st.session_state["nav_section"] = target
                    st.rerun()

            # Dim breadcrumb under the button. The kind chip uses the
            # sanctioned ``badge()`` helper (its own markup); the "in
            # <section>" breadcrumb uses ``st.caption`` so this module
            # introduces zero raw inline-style markup of its own.
            chip = kind_chip.get(kind)
            crumb_color = C_TEXT3 if kind == "tab" else C_TEXT2
            if chip:
                st.markdown(
                    badge(chip, color=crumb_color), unsafe_allow_html=True
                )
            if sec_label and kind != "section":
                st.caption(f"in {sec_label}")

    except Exception:
        # Never let the palette take down the sidebar.
        return
