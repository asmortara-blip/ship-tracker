"""tab_commentary.py — per-tab LLM-narrated editorial commentary.

Companion to ``engine.narration_engine``. Where that module produces ONE
structured daily briefing per UTC day (cached on disk by date), this module
produces a SHORT 1-2 paragraph editorial commentary on the *current data
context of a single tab* — caller-defined, free-form. Cached in SQLite's
``kv_state`` table keyed by ``commentary:{tab_name}:{stable_hash(context)}``
with a 1-hour TTL so a refresh of the same dataset reuses the prior call.

Decision flow
-------------
1. Compute a process-stable hash of the (tab_name, context) pair. The hash
   uses ``utils.helpers.stable_hash`` so it survives a process restart
   (Python's built-in ``hash()`` is salted per process and would defeat
   cross-process cache hits).
2. Look up ``commentary:{tab}:{hash}`` in the ``kv_state`` table. If the
   row is younger than the TTL, return the cached ``TabCommentary``.
3. Otherwise, resolve the Anthropic API key (explicit ▶ ``st.secrets`` ▶
   ``os.environ``). If absent, return the template-fallback commentary
   synthesized deterministically from the context dict. Template-path
   output is NOT cached so the slot stays open for the LLM once a key
   is configured.
4. Call Claude with the system prompt wrapped in ``cache_control:
   ephemeral`` so the editorial-style block is reused across calls within
   Anthropic's 5-minute cache window. Parse the JSON output, persist to
   ``kv_state``, return the ``TabCommentary``.
5. On any failure (network, JSON parse, schema mismatch) → log and fall
   back to the template (not cached).

Cost shape
----------
Default model: ``claude-haiku-4-5-20251001`` (Haiku 4.5). System prompt
~600 tokens, user prompt ~150-300 tokens depending on context size,
``max_tokens=400`` on output. Within Haiku 4.5's cache window the cached
system prompt drops to a fraction of full price, keeping repeated tab
visits near-free.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from utils.helpers import stable_hash


# ── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_LLM_MODEL: str = "claude-haiku-4-5-20251001"
"""Haiku 4.5 — fast + cheap + good enough for 1-2 paragraph commentary.
Tunable via the ``model=`` argument to ``build_commentary``."""

DEFAULT_MAX_TOKENS: int = 400
"""Output ceiling. 1-2 paragraph commentary tops out around 250-350 tokens;
400 leaves headroom without inviting Haiku to ramble."""

CACHE_TTL_SECONDS: int = 3_600
"""1 hour. Long enough that hopping between tabs doesn't re-trigger the LLM;
short enough that a meaningful data refresh produces fresh commentary even
when the input dict is structurally identical."""


# ── System prompt — sent to Anthropic with cache_control: ephemeral ─────────

_COMMENTARY_SYSTEM_PROMPT: str = """You are the editorial voice for an
institutional shipping-and-freight research dashboard. Your job: write
ONE crisp 1-2 paragraph commentary on the current state of the tab the
user is looking at.

Voice and constraints:
- WSJ-style: tight, declarative, data-grounded. No hedge-fund hype.
- No "should consider" recommendations, no price targets, no exclamation marks.
- Cite the metrics the user has supplied — exactly. Never invent numbers.
- If a metric is missing or zero, omit it rather than guess.
- Total length: 60 to 180 words. Two paragraphs max; one is fine.
- The HEADLINE is a single sentence, ≤ 120 characters, no trailing period
  required, no quotation marks.
- The BODY is plain text. Use blank lines between paragraphs. No markdown,
  no bullet lists, no section headers.

Output ONLY a single JSON object (no markdown fences, no preamble, no
commentary about your output), matching this exact schema:

{
  "headline": "<one sentence, ≤120 chars>",
  "body": "<1-2 short paragraphs of plain text, separated by a blank line>"
}"""


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TabCommentary:
    """One tab's editorial commentary blob.

    Attributes
    ----------
    headline:
        Single-sentence editorial verdict (≤120 chars). The tab UI typically
        renders this as a bolded headline above ``body``.
    body:
        1-2 paragraph plain-text commentary; paragraphs separated by ``\\n\\n``.
    source:
        ``"llm"`` when produced by Anthropic, ``"template"`` when the API key
        was absent or the call failed. UI may show a small badge keyed on
        this field.
    model:
        Model ID that produced ``body``. Empty string for template fallback.
    tokens_in, tokens_out:
        Input / output token counts from the Anthropic response usage block.
        Both 0 for template fallback.
    generated_at:
        ISO 8601 UTC timestamp at the moment the commentary was returned —
        not the moment a cached entry was last written.
    """
    headline: str
    body: str
    source: str         # "llm" | "template"
    model: str
    tokens_in: int
    tokens_out: int
    generated_at: str


# ── API key resolution — mirrors engine.narration_engine ───────────────────

def _get_anthropic_key(explicit: Optional[str]) -> str:
    """Resolve the API key: explicit arg ▶ st.secrets ▶ os.environ.

    Streamlit is optional — the engine module must remain importable from
    contexts that have no streamlit runtime (tests, CLI).
    """
    if explicit:
        return explicit
    try:
        import streamlit as st
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key:
            return str(key)
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


# ── Cache key & context fingerprinting ──────────────────────────────────────

def _serialize_context(context: dict) -> str:
    """Render the context dict as a stable JSON string for hashing.

    ``sort_keys=True`` guarantees that two dicts with identical contents but
    different insertion order hash to the same key. ``default=str`` lets
    non-JSON-serializable types (Decimal, datetime, dataclasses) flow through
    without raising; we never deserialize this — only hash it.
    """
    try:
        return json.dumps(context or {}, sort_keys=True, default=str)
    except Exception:
        # Last-ditch — repr() is technically process-stable for primitives
        # and dataclasses; the stable_hash on top handles the rest.
        return repr(context)


def _cache_key(tab_name: str, context: dict) -> str:
    """Build the ``kv_state`` lookup key for a (tab, context) pair."""
    digest = stable_hash(f"{tab_name}|{_serialize_context(context)}")
    return f"commentary:{tab_name}:{digest:08x}"


# ── SQLite cache I/O ────────────────────────────────────────────────────────

def _read_cache(key: str) -> Optional[TabCommentary]:
    """Return a cached ``TabCommentary`` if the row exists and is within TTL.

    Any I/O / parse error returns ``None`` and lets the caller fall through
    to the LLM path. Cache misses are silent (debug log only).
    """
    try:
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value, updated_at FROM kv_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        updated = _parse_iso(row["updated_at"])
        if updated is None:
            return None
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
        data = json.loads(row["value"])
        return TabCommentary(
            headline=str(data.get("headline", "")),
            body=str(data.get("body", "")),
            source=str(data.get("source", "llm")),
            model=str(data.get("model", "")),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            generated_at=str(data.get("generated_at", "")),
        )
    except Exception as exc:
        logger.debug(f"tab_commentary: cache read failed for {key}: {exc}")
        return None


def _write_cache(key: str, commentary: TabCommentary) -> None:
    """Persist a commentary to ``kv_state``. Best-effort — never raises."""
    try:
        from state.db import get_connection
        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(asdict(commentary), default=str), now_iso),
        )
    except Exception as exc:
        logger.debug(f"tab_commentary: cache write failed for {key}: {exc}")


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; return ``None`` on failure."""
    try:
        dt = datetime.fromisoformat(s)
        # Treat naive strings as UTC — every writer in this module stamps
        # tz-aware ISO, but legacy rows could be naive.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


# ── Template fallback ───────────────────────────────────────────────────────

def _summarize_context_for_template(context: dict) -> list[str]:
    """Turn the free-form context dict into a short list of human-readable
    fragments suitable for the deterministic template body.

    Keys are surfaced in their natural order. Values are formatted by type:
    floats round to 2 dp; ints render as-is; sequences render as joined
    strings (first 3 items only); strings are passed through. Falsy values
    are skipped so callers can pass a half-populated dict without producing
    "None"/"0" noise.
    """
    fragments: list[str] = []
    for key, val in (context or {}).items():
        if val in (None, "", [], {}):
            continue
        label = str(key).replace("_", " ").strip()
        if isinstance(val, bool):
            fragments.append(f"{label}: {'yes' if val else 'no'}")
        elif isinstance(val, float):
            fragments.append(f"{label}: {val:.2f}")
        elif isinstance(val, int):
            fragments.append(f"{label}: {val}")
        elif isinstance(val, (list, tuple)):
            preview = ", ".join(str(x) for x in list(val)[:3])
            if preview:
                fragments.append(f"{label}: {preview}")
        elif isinstance(val, dict):
            preview = ", ".join(f"{k}={v}" for k, v in list(val.items())[:3])
            if preview:
                fragments.append(f"{label}: {preview}")
        else:
            text = str(val).strip()
            if text:
                fragments.append(f"{label}: {text[:120]}")
    return fragments


def _template_commentary(tab_name: str, context: dict) -> TabCommentary:
    """Deterministic 1-2 sentence commentary from the context dict alone.

    Used when no API key is configured or the API call fails. Output is
    pure-function of (tab_name, context) so two callers with the same
    inputs always get the same string back. NOT cached.
    """
    fragments = _summarize_context_for_template(context)
    if not fragments:
        headline = f"{tab_name.title()} commentary unavailable"
        body = (
            "No editorial commentary is available because no contextual "
            "metrics were supplied for this tab and the Claude API key is "
            "not configured. Set ANTHROPIC_API_KEY to enable LLM commentary."
        )
    else:
        primary = fragments[0]
        rest = fragments[1:4]
        headline = (
            f"{tab_name.title()} snapshot: {primary}"
        )[:120]
        if rest:
            secondary = "; ".join(rest)
            body = (
                f"{primary.capitalize()}. "
                f"Supporting reads: {secondary}. "
                "Template fallback — configure ANTHROPIC_API_KEY for LLM commentary."
            )
        else:
            body = (
                f"{primary.capitalize()}. "
                "Template fallback — configure ANTHROPIC_API_KEY for LLM commentary."
            )
    return TabCommentary(
        headline=headline,
        body=body,
        source="template",
        model="",
        tokens_in=0,
        tokens_out=0,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Anthropic call ──────────────────────────────────────────────────────────

def _build_user_prompt(tab_name: str, context: dict) -> str:
    """Render the (tab, context) pair as a compact prompt for Claude.

    JSON-encoded context lets Claude consume any free-form payload the tab
    cares to send — top metrics, signal direction, latest macro reads —
    without us having to negotiate a schema with every caller.
    """
    try:
        ctx_json = json.dumps(context or {}, indent=2, default=str, sort_keys=True)
    except Exception:
        ctx_json = repr(context)
    return (
        f"Tab: {tab_name}\n\n"
        "Current data context (JSON):\n\n"
        f"{ctx_json}\n\n"
        "Write the editorial commentary as JSON per the system instructions."
    )


def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    max_tokens: int,
    api_key: str,
) -> tuple[str, int, int]:
    """Single-shot Anthropic call. Returns ``(text, tokens_in, tokens_out)``.

    The system block carries ``cache_control: ephemeral`` so repeated calls
    inside the 5-minute cache window reuse the system tokens at a fraction
    of full price — exactly the cost-saving move the daily narrator uses.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    text_parts = [
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ]
    text = "".join(text_parts).strip()
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0)) if usage else 0
    tokens_out = int(getattr(usage, "output_tokens", 0)) if usage else 0
    return text, tokens_in, tokens_out


def _parse_claude_json(
    raw: str, *, model: str, tokens_in: int, tokens_out: int,
) -> Optional[TabCommentary]:
    """Parse Claude's JSON output into a ``TabCommentary``.

    Returns ``None`` on any parsing / schema failure so the caller can
    fall back to the template. Tolerates markdown fences even though the
    system prompt asks for raw JSON — Haiku sometimes adds them anyway.
    """
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            text = text.strip("`").strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        headline = str(data.get("headline", "")).strip()
        body = str(data.get("body", "")).strip()
        if not headline or not body:
            return None
        return TabCommentary(
            headline=headline,
            body=body,
            source="llm",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        logger.debug(f"tab_commentary: Claude JSON parse failed: {exc}")
        return None


# ── Public entry point ──────────────────────────────────────────────────────

def build_commentary(
    tab_name: str,
    context: dict,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: str = DEFAULT_LLM_MODEL,
    api_key: Optional[str] = None,
    use_cache: bool = True,
) -> TabCommentary:
    """Return editorial commentary for ``tab_name`` given the current ``context``.

    Cache-first; LLM if needed; template fallback. See module docstring for
    the full decision flow.

    Parameters
    ----------
    tab_name:
        Short tab identifier (``"overview"``, ``"portfolio"``, etc.). Becomes
        part of the SQLite cache key AND is surfaced in the user prompt so
        Claude can tune its phrasing to the tab.
    context:
        Free-form dict of "what's on screen right now" — top metrics, signal
        direction, latest macro reads. Used verbatim for hashing and is
        JSON-encoded into the user prompt. Order-independent (the hash sorts
        keys before hashing).
    max_tokens:
        Output ceiling for the Anthropic response. Default 400.
    model:
        Anthropic model ID. Default is a Haiku-class model for low cost.
    api_key:
        Explicit override. If ``None``, falls back to ``st.secrets`` then
        ``os.environ['ANTHROPIC_API_KEY']``.
    use_cache:
        If False, bypass the SQLite cache lookup AND skip writing the result
        back. Useful in tests or for forcing a regen.

    Returns
    -------
    TabCommentary
        Always returns a populated object; ``source`` indicates whether the
        commentary came from the LLM, a fresh template fallback, or a
        previously-cached LLM result.
    """
    tab_name = (tab_name or "untitled").strip().lower()
    context = context or {}

    cache_key = _cache_key(tab_name, context)
    if use_cache:
        cached = _read_cache(cache_key)
        if cached is not None:
            return cached

    api_key_resolved = _get_anthropic_key(api_key)
    if not api_key_resolved:
        logger.debug(f"tab_commentary: no API key for tab={tab_name}, using template")
        return _template_commentary(tab_name, context)

    user_prompt = _build_user_prompt(tab_name, context)
    try:
        raw, tokens_in, tokens_out = _call_anthropic(
            _COMMENTARY_SYSTEM_PROMPT,
            user_prompt,
            model=model,
            max_tokens=max_tokens,
            api_key=api_key_resolved,
        )
        commentary = _parse_claude_json(
            raw, model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        )
    except Exception as exc:
        logger.warning(f"tab_commentary: Anthropic call failed for {tab_name}: {exc}")
        commentary = None

    if commentary is not None:
        if use_cache:
            _write_cache(cache_key, commentary)
        return commentary
    return _template_commentary(tab_name, context)
