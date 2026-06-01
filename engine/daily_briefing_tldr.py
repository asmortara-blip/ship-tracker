"""engine/daily_briefing_tldr.py — one-paragraph TLDR over the daily narration.

The structured ``DailyNarration`` from ``engine.narration_engine`` is
a list of bullet sections — comprehensive but slow to scan in a
crowded inbox. This module wraps it in a single Claude API call that
distills the headline + top 2-3 facts into a 2-3 sentence summary
suitable for SMS, the top of an email, or a Slack DM.

Output contract:
  * ``TldrSummary.text`` — 50-120 word paragraph, no lists, no bullets
  * ``TldrSummary.source`` — "claude" | "template"
  * ``TldrSummary.tokens_in / tokens_out`` — populated on the claude path
  * ``TldrSummary.model`` — the model identifier used

Defensive:
  * No ``ANTHROPIC_API_KEY`` (explicit arg ▶ ``st.secrets`` ▶ env) →
    falls back to template summary
  * Any Claude call failure → falls back to template summary
  * Empty / no-content narration → returns a "no signal" placeholder

Caching — mirrors ``narration_engine``:
  The narration itself is cached per UTC day, so the UI reads it cheaply
  on every render. The TLDR derives from that narration and is cached
  the same way: one file per UTC day, invalidated by a content
  fingerprint so a force-refreshed narration yields a fresh TLDR. The
  first viewer of each day pays one cheap Haiku call; everyone else hits
  the cache. Only Claude-backed TLDRs are cached — the template fallback
  is not, so the cache slot stays open for the LLM once a key is set.

Every *successful* Claude call is recorded via
``engine.llm_telemetry.record_call`` (source ``"daily_briefing_tldr"``)
so the cost panel + the operator digest pick it up; they group by the
``source`` column with no allowlist, so the new source flows through
automatically. Failed calls produce no billable usage and aren't
recorded — matching ``narration_engine``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


__all__ = [
    "TldrSummary",
    "generate_tldr",
    "render_template_tldr",
]


# Pin a small, cheap model — TLDRs are short, no need for Opus.
DEFAULT_TLDR_MODEL: str = "claude-haiku-4-5-20251001"

# One cached TLDR file per UTC day (gitignored: see cache/ in .gitignore).
TLDR_CACHE_DIR: Path = Path(__file__).resolve().parent.parent / "cache" / "tldr"

_NO_SIGNAL: str = "No material shipping-stress signals today."
_SYSTEM_PROMPT: str = (
    "You are a shipping operations analyst. Given a structured daily "
    "briefing for a containerized-freight fleet, write a tight 2-3 "
    "sentence TLDR (under 120 words). Lead with the single most "
    "important fact (a deficit, a route, a ticker, or a macro shift). "
    "Plain prose only — NO bullet points, NO markdown, NO headers, "
    "NO numbered lists. Write as if briefing a portfolio manager who "
    "has 10 seconds. If the input is empty or contains no signal, "
    "respond with exactly: '" + _NO_SIGNAL + "'"
)


@dataclass(frozen=True)
class TldrSummary:
    """One-paragraph TLDR over a DailyNarration."""

    text: str
    source: str = "template"     # "claude" | "template"
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    generated_at: str = ""


# ---------------------------------------------------------------------------
# Template fallback — used when Claude is unavailable or key missing
# ---------------------------------------------------------------------------


def render_template_tldr(narration) -> str:
    """Build a serviceable TLDR from the narration without LLM help.

    Heuristic: headline + first section's first bullet (usually the
    highest-priority fact). When there is neither, fall back to the
    body's first sentence — consistent with ``_has_content``, which
    counts a body-only narration as signal — and only then to a
    no-signal placeholder.
    """
    headline = str(getattr(narration, "headline", "") or "").strip()
    sections = list(getattr(narration, "sections", []) or [])
    first_bullet = ""
    for section in sections:
        bullets = list(getattr(section, "bullets", []) or [])
        if bullets:
            first_bullet = str(bullets[0]).strip()
            break

    if headline and first_bullet:
        return f"{headline}. {first_bullet}".strip()
    if headline or first_bullet:
        return headline or first_bullet

    # No headline, no bullets — fall back to the body's first sentence
    # rather than misreport real content as "no signal".
    body = str(getattr(narration, "body", "") or "").strip()
    if body:
        first_sentence = body.split(". ", 1)[0].strip()
        return first_sentence or body
    return _NO_SIGNAL


# ---------------------------------------------------------------------------
# Narration introspection
# ---------------------------------------------------------------------------


def _has_content(narration) -> bool:
    """True when the narration carries any headline/body/bullet signal.

    Lets ``generate_tldr`` short-circuit an empty narration to the
    no-signal placeholder without burning a Claude call.
    """
    if str(getattr(narration, "headline", "") or "").strip():
        return True
    if str(getattr(narration, "body", "") or "").strip():
        return True
    for section in (getattr(narration, "sections", []) or []):
        if list(getattr(section, "bullets", []) or []):
            return True
    return False


def _narration_to_prompt(narration) -> str:
    """Serialize a DailyNarration into a prompt-friendly text blob.

    Headline + body + every section's bullets in plain text. Skips
    sections that are empty (no bullets) so we don't pad the prompt.
    """
    parts: list[str] = []
    headline = str(getattr(narration, "headline", "") or "").strip()
    body = str(getattr(narration, "body", "") or "").strip()
    if headline:
        parts.append(f"HEADLINE: {headline}")
    if body:
        parts.append(f"OVERVIEW: {body}")
    sections = list(getattr(narration, "sections", []) or [])
    for section in sections:
        title = str(getattr(section, "title", "") or "").strip()
        bullets = list(getattr(section, "bullets", []) or [])
        if not bullets:
            continue
        bullet_lines = "\n".join(f"- {str(b).strip()}" for b in bullets)
        parts.append(f"{title}:\n{bullet_lines}")
    return "\n\n".join(parts) or "(no narration content)"


# ---------------------------------------------------------------------------
# Day cache — mirrors engine.narration_engine's per-UTC-day file cache
# ---------------------------------------------------------------------------


def _narration_fingerprint(narration, model: str) -> str:
    """Stable short hash of the narration content + model.

    Two narrations that would produce the same TLDR share a fingerprint,
    so the day-cache serves a hit; a force-refreshed narration (new
    content) or a model change yields a new fingerprint and a fresh TLDR.
    """
    basis = f"{model}\n{_narration_to_prompt(narration)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _tldr_cache_path(date_str: str, cache_dir: Path) -> Path:
    return cache_dir / f"{date_str}.json"


def _read_tldr_cache(path: Path, fingerprint: str) -> TldrSummary | None:
    """Return the cached TLDR iff it exists and matches ``fingerprint``.

    A fingerprint mismatch means the day's narration was regenerated
    since this TLDR was cached, so we treat it as a miss and let the
    caller produce a fresh one.
    """
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if str(data.get("narration_fingerprint", "")) != fingerprint:
            return None
        return TldrSummary(
            text=str(data.get("text", "")),
            source=str(data.get("source", "template")),
            model=str(data.get("model", "")),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            generated_at=str(data.get("generated_at", "")),
        )
    except Exception:
        # A corrupt/partial cache file should never break the briefing.
        return None


def _write_tldr_cache(path: Path, summary: TldrSummary, fingerprint: str) -> None:
    """Best-effort write of a Claude-backed TLDR to the day cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(summary)
        payload["narration_fingerprint"] = fingerprint
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except Exception:
        # Cache-write failure must NEVER break the briefing.
        return


# ---------------------------------------------------------------------------
# Claude-backed summarizer
# ---------------------------------------------------------------------------


def _record_telemetry(
    *, source: str, model: str, tokens_in: int, tokens_out: int,
) -> None:
    """Best-effort: record the successful Claude call in llm_telemetry.

    Mirrors ``engine.narration_engine`` — only the success path is
    recorded, using the real token usage. ``source`` distinguishes the
    caller in the cost panel + operator digest (which group by source
    with no allowlist): ``"daily_briefing_tldr"`` for the shipping
    briefing, ``"investor_report_tldr"`` for the investor-report lede.
    Never raises; a telemetry write failure must not break the briefing.
    """
    try:
        from engine.llm_telemetry import record_call
        record_call(
            source=source,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    except Exception:
        return


def generate_tldr(
    narration,
    *,
    model: str = DEFAULT_TLDR_MODEL,
    api_key: str | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    source: str = "daily_briefing_tldr",
) -> TldrSummary:
    """Distill a DailyNarration to a one-paragraph TLDR.

    Decision flow (mirrors ``generate_daily_narration``):

    1. ``None`` / no-content narration → no-signal placeholder (no call,
       not cached).
    2. If ``use_cache`` and a cached TLDR for this narration's date
       exists *and* its content fingerprint matches → return it.
    3. Resolve the Anthropic key (explicit arg ▶ ``st.secrets`` ▶ env).
       Absent → template summary (not cached, so the slot stays open for
       the LLM once a key is configured).
    4. Call Claude (Haiku by default), record telemetry, write the cache,
       return.
    5. On any Claude failure → template summary (not cached).

    ALWAYS returns a valid ``TldrSummary`` — never raises. Even a
    duck-typed narration whose attribute access throws (a lazy/IO-backed
    property) degrades to a placeholder rather than escaping.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        # ── Empty / no-content short-circuit ─────────────────────────
        if narration is None or not _has_content(narration):
            return TldrSummary(
                text=_NO_SIGNAL, source="template", generated_at=now_iso,
            )

        # ── Cache read (before key check — a key may have lapsed since
        #    the TLDR was cached; a valid cached claude TLDR is still good)
        date_str = str(getattr(narration, "date", "") or "").strip() \
            or datetime.now(timezone.utc).date().isoformat()
        fingerprint = _narration_fingerprint(narration, model)
        cache_dir = cache_dir or TLDR_CACHE_DIR
        cache_file = _tldr_cache_path(date_str, cache_dir)
        if use_cache:
            cached = _read_tldr_cache(cache_file, fingerprint)
            if cached is not None:
                return cached

        # ── API-key resolution (shared with narration_engine) ────────
        try:
            from engine.narration_engine import _get_anthropic_key
            key = _get_anthropic_key(api_key)
        except Exception:
            key = api_key or ""
        if not key:
            return TldrSummary(
                text=render_template_tldr(narration),
                source="template", generated_at=now_iso,
            )

        # ── Claude call ──────────────────────────────────────────────
        # _call_claude imports anthropic internally; not pre-importing it
        # here keeps the monkeypatched call SDK-free in tests and lets a
        # missing package fall to the outer template fallback.
        from engine.narration_engine import _call_claude
        text, tokens_in, tokens_out = _call_claude(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_narration_to_prompt(narration),
            model=model, api_key=key,
        )
        # The call is billable whether or not the text is usable (input
        # tokens + cached system prompt), so record it before judging the
        # text — otherwise an empty response would under-count cost.
        if tokens_in or tokens_out:
            _record_telemetry(
                source=source, model=model,
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        text = text.strip()
        if not text:
            # Billed but unusable text → template fallback (not cached).
            return TldrSummary(
                text=render_template_tldr(narration),
                source="template", generated_at=now_iso,
            )
        summary = TldrSummary(
            text=text, source="claude", model=model,
            tokens_in=tokens_in, tokens_out=tokens_out,
            generated_at=now_iso,
        )
        _write_tldr_cache(cache_file, summary, fingerprint)
        return summary
    except Exception:
        # Absolute no-raise guarantee: a Claude failure, or a duck-typed
        # narration whose attribute access raises, degrades to the safest
        # summary we can build. render_template_tldr can itself touch a
        # raising attribute, so guard it too.
        try:
            return TldrSummary(
                text=render_template_tldr(narration),
                source="template", generated_at=now_iso,
            )
        except Exception:
            return TldrSummary(
                text=_NO_SIGNAL, source="template", generated_at=now_iso,
            )
