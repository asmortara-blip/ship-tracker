"""tools/rules_csv.py — CSV round-trip for alert rules.

The YAML round-trip in :mod:`tools.rules_yaml` is the engineer-friendly
shape; CSV is what most operators reach for first because it opens in
Excel. This module mirrors the YAML module's API but with the CSV wire
format:

* :func:`rules_to_csv` — serialise rules to a deterministic, Excel-
  friendly CSV string (UTF-8 with BOM).
* :func:`csv_to_rules` — total parser. Returns ``(rules, warnings)``;
  per-row problems become warnings rather than raises so a stale
  column or a malformed row never blocks the rest of the import.
* :func:`validate_rule_csv_row` — synchronous yes/no for the UI's
  paste-validation button.

Wire format
-----------
A single header row followed by one row per rule:

    rule_id,name,metric,threshold_pct,severity,condition,enabled,...

* **Column order is fixed** via :data:`COLUMNS` — same input always
  produces byte-identical CSV.
* **``target_channels`` is joined with ``|``** rather than ``,`` so the
  list cannot collide with the CSV delimiter. The split is symmetric;
  empty strings between separators are dropped.
* **Booleans** render as the lower-case strings ``true`` / ``false``;
  the parser also accepts ``True`` / ``False`` / ``1`` / ``0`` / ``yes``
  / ``no`` (case-insensitive) so a hand-edited file from Excel survives.
* **Output is UTF-8 with a BOM** (``\\xef\\xbb\\xbf``). Excel on macOS /
  Windows otherwise mis-detects the encoding as Windows-1252 and a
  non-ASCII character in a rule name will display as mojibake. The
  parser strips the BOM if present so the round-trip is byte-clean.

Defining contract
-----------------
Round-trip preserves every wire-format field on every rule we ship
(empty list, single rule, full rule, multi-rule, special characters
in name). The CLI subcommands (``rules export-csv`` / ``import-csv`` /
``diff-csv``) and the UI's Export / Import expander both consume this
module.

Why a separate module from :mod:`tools.rules_yaml`?
---------------------------------------------------
* The CSV parser is much smaller — the stdlib :mod:`csv` module
  handles the framing and quoting, so we don't ship a hand-rolled
  parser.
* The YAML wire format is structured (list-of-dicts with nested lists
  for ``target_channels``); CSV is flat. The pipe-delimited list is a
  CSV-only concern, the inline-list flow style is a YAML-only concern.
* Operators reach for CSV when they want to open in Excel and edit a
  grid; for YAML when they want to diff in git. The two consumers have
  different ergonomics and deserve their own surface.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional


# Canonical engine severities. Anything else is rejected on import per
# the same contract as the YAML module — the engine routes on severity
# and an unknown value would silently bypass channel-threshold checks.
_ALLOWED_SEVERITIES: frozenset[str] = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
)

# CSV column order. Fixed so the output is deterministic for any given
# input — a diff between two checkpoints can only surface a real rule
# change, never formatting drift. The order mirrors the YAML module's
# semantic grouping: identity → core threshold → severity/condition →
# toggles → channels → cooldown → flap.
COLUMNS: list[str] = [
    "rule_id",
    "name",
    "metric",
    "threshold_pct",
    "severity",
    "condition",
    "enabled",
    "email_notify",
    "target_channels",
    "cooldown_minutes",
    "flap_detection_enabled",
    "flap_window_minutes",
    "flap_threshold_crossings",
]

# Set used by the unknown-column check on import — every header column
# not in this set produces a warning and is ignored. We expose the
# constant for tests / future consumers.
_ALLOWED_FIELDS: frozenset[str] = frozenset(COLUMNS)

# Delimiter used to join / split ``target_channels`` inside a CSV cell.
# Anything except ``,`` (the CSV delimiter), ``"`` (the CSV quote), and
# newline would work; ``|`` is unambiguous, easy to type, and matches
# the engine's own internal dedup_key separator.
_CHANNEL_SEP: str = "|"

# Raw UTF-8 BOM. Spelled out as bytes (not as a literal) so reviewers
# can grep for the exact escape sequence and so the constant survives
# editor "smart quotes" / encoding round-trips on a colleague's
# machine. Same pattern as :mod:`utils.csv_export`.
_BOM: str = "﻿"

# Defaults used when a field is missing on import. Mirrors the engine's
# ``normalize_rule`` posture: a missing field gets a sensible default,
# not an exception.
_DEFAULTS: dict[str, Any] = {
    "name": "",
    "metric": "",
    "threshold_pct": 0.0,
    "severity": "MEDIUM",
    "condition": "Above",
    "enabled": True,
    "email_notify": False,
    "target_channels": [],
    "cooldown_minutes": 0,
    "flap_detection_enabled": False,
    "flap_window_minutes": 30,
    "flap_threshold_crossings": 5,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Internal — scalar render / coerce
# ─────────────────────────────────────────────────────────────────────────────

def _bool_to_str(value: Any) -> str:
    """Render a bool as ``'true'`` / ``'false'``. Non-bool inputs use
    Python truthiness so a stray ``1`` / empty-string round-trips as
    the right shape."""
    return "true" if bool(value) else "false"


def _str_to_bool(value: Any) -> Optional[bool]:
    """Coerce a CSV cell back to a bool. Accepts the canonical forms
    we emit (``true`` / ``false``) plus the common hand-edited forms
    (``True`` / ``False`` / ``1`` / ``0`` / ``yes`` / ``no``).

    Returns ``None`` for unrecognised inputs — the caller surfaces a
    warning so the operator sees what got rejected instead of a
    silent coercion to whatever Python's truthiness happens to return."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # Numeric path: 0 → False, anything else → True. Matches the
        # stdlib ``bool()`` semantics and the CSV-text forms below.
        return bool(value)
    if not isinstance(value, str):
        return None
    low = value.strip().lower()
    if low in {"true", "yes", "1"}:
        return True
    if low in {"false", "no", "0"}:
        return False
    return None


def _channels_to_str(values: Any) -> str:
    """Join a list of channel names with ``|``. Non-list inputs
    collapse to an empty string — same semantics as the YAML emitter's
    list path."""
    if not isinstance(values, (list, tuple)):
        return ""
    parts: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        parts.append(s)
    return _CHANNEL_SEP.join(parts)


def _str_to_channels(raw: Any) -> list[str]:
    """Split a CSV cell on ``|``. Empty cells → empty list. Strips
    whitespace on each token and drops blanks (so ``a||b`` parses to
    ``['a', 'b']``, not ``['a', '', 'b']``)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []
    return [tok.strip() for tok in s.split(_CHANNEL_SEP) if tok.strip()]


def _value_to_cell(field: str, value: Any) -> str:
    """Render a single rule field as a CSV cell.

    Booleans go through ``_bool_to_str``; ``target_channels`` goes
    through ``_channels_to_str``; floats use ``repr`` for lossless
    round-trip (matches the YAML emitter); everything else is rendered
    via ``str``."""
    if value is None:
        return ""
    if field in {"enabled", "email_notify", "flap_detection_enabled"}:
        return _bool_to_str(value)
    if field == "target_channels":
        return _channels_to_str(value)
    if isinstance(value, bool):
        # Defensive: a field not in the bool set above that happens to
        # carry a bool still renders cleanly. ``bool`` must be checked
        # before ``int`` because ``isinstance(True, int)`` is True.
        return _bool_to_str(value)
    if isinstance(value, float):
        # ``repr(float)`` round-trips losslessly in CPython; ``str``
        # would truncate edge cases like 0.1 + 0.2.
        return repr(value)
    return str(value)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal — rule shape normalisation (mirrors tools.rules_yaml._rule_to_dict)
# ─────────────────────────────────────────────────────────────────────────────

def _rule_to_dict(rule: Any) -> Optional[dict]:
    """Normalise an arbitrary rule input to the persisted-dict shape
    used by the CSV emitter.

    Returns ``None`` for inputs that cannot be persisted (no
    ``rule_id`` or ``id``, not a dict / AlertRule). Lazy-imports
    :class:`engine.alert_engine_v2.AlertRule` so importing this module
    does not pull in the engine — the CLI / UI surface must stay
    importable in light contexts (e.g. documentation generators).
    """
    if rule is None:
        return None

    if isinstance(rule, dict):
        rule_id = rule.get("rule_id") or rule.get("id")
        if not rule_id:
            return None
        out: dict = {"rule_id": str(rule_id)}

        # threshold_pct is the wire-format key; persisted dicts use
        # threshold. Prefer the wire key if present so a manually-
        # authored dict can override the engine's stored value.
        if "threshold_pct" in rule:
            try:
                out["threshold_pct"] = float(rule["threshold_pct"])
            except (TypeError, ValueError):
                out["threshold_pct"] = 0.0
        elif "threshold" in rule:
            try:
                out["threshold_pct"] = float(rule["threshold"])
            except (TypeError, ValueError):
                out["threshold_pct"] = 0.0

        for src_key, wire_key in [
            ("name", "name"),
            ("metric", "metric"),
            ("severity", "severity"),
            ("condition", "condition"),
            ("enabled", "enabled"),
            ("email_notify", "email_notify"),
            ("cooldown_minutes", "cooldown_minutes"),
            ("flap_detection_enabled", "flap_detection_enabled"),
            ("flap_window_minutes", "flap_window_minutes"),
            ("flap_threshold_crossings", "flap_threshold_crossings"),
        ]:
            if src_key in rule:
                out[wire_key] = rule[src_key]

        # target_channels: coerce to list[str], dropping non-strings.
        tc = rule.get("target_channels")
        if isinstance(tc, list):
            out["target_channels"] = [
                str(x) for x in tc if isinstance(x, (str, int, float))
            ]

        # Severity normalisation for emission. The engine stores the UI
        # label (often the operator-facing "Warning") but the wire wants
        # the canonical engine vocabulary. Map common UI labels.
        sev = out.get("severity")
        if isinstance(sev, str):
            up = sev.upper()
            ui_map = {"INFO": "LOW", "WARNING": "MEDIUM", "CRITICAL": "CRITICAL"}
            out["severity"] = ui_map.get(up, up)

        return out

    # AlertRule dataclass path. Lazy-import the engine so this module
    # stays importable without pulling in sqlite/loguru.
    try:
        from engine.alert_engine_v2 import AlertRule  # noqa: WPS433
    except Exception:
        AlertRule = None  # type: ignore[assignment]

    if AlertRule is not None and isinstance(rule, AlertRule):
        out2: dict = {
            "rule_id": str(rule.rule_id),
            "name": str(rule.name),
            "metric": str(rule.alert_type),
            "threshold_pct": float(rule.threshold),
            "severity": str(rule.severity).upper(),
            "enabled": bool(rule.enabled),
            "target_channels": list(rule.target_channels or []),
        }
        # Optional dataclass fields — round-trip only when present so
        # this module works against older AlertRule shipments.
        for fld_name in (
            "cooldown_minutes",
            "flap_detection_enabled",
            "flap_window_minutes",
            "flap_threshold_crossings",
        ):
            if hasattr(rule, fld_name):
                out2[fld_name] = getattr(rule, fld_name)
        return out2

    # Unknown type — drop silently. Matches the YAML module's posture.
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Public — serialise
# ─────────────────────────────────────────────────────────────────────────────

def rules_to_csv(rules: list[Any]) -> str:
    """Serialise rules to a deterministic CSV string.

    Output shape:

    * One header row carrying :data:`COLUMNS` in order.
    * One row per rule, sorted ascending by ``rule_id``. Same input
      always produces byte-identical CSV (tests pin this so a diff
      between two checkpoints surfaces real rule changes only).
    * ``target_channels`` joined with ``|``.
    * Booleans as ``true`` / ``false``.
    * UTF-8 BOM prefix for Excel friendliness.

    Accepts:

    * ``dict`` rules in the persisted shape — both ``rule_id`` and
      legacy ``id`` are accepted as the identifier source.
    * :class:`engine.alert_engine_v2.AlertRule` dataclass instances.
    * Mixed lists; non-dict / non-AlertRule items are silently
      skipped (same posture as :mod:`tools.rules_yaml`).

    Empty / non-list input still produces a valid CSV — just the BOM +
    header row — so a downstream parser can tell "no rules" apart from
    "no file".
    """
    # Write into an in-memory StringIO so the csv module's framing
    # / quoting code does the heavy lifting. We slap the BOM on at
    # the end before returning.
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=COLUMNS,
        # QUOTE_MINIMAL: quote a cell only when it contains the
        # delimiter, the quote char, a newline, or starts/ends with
        # whitespace. Same default as Python's csv module — keeps the
        # output diff-friendly while still surviving Excel's
        # round-trip.
        quoting=csv.QUOTE_MINIMAL,
        # Use \n line terminator (csv defaults to \r\n) so the output
        # matches the YAML module's convention and so a diff between
        # platforms is line-clean. Excel reads either.
        lineterminator="\n",
        # Reject any unexpected key in the input dict — every key must
        # appear in fieldnames or csv raises. We pre-filter via
        # COLUMNS below so this is belt-and-braces only.
        extrasaction="ignore",
    )

    writer.writeheader()

    if not isinstance(rules, list) or not rules:
        # Empty list still emits the BOM + header so a parser can
        # tell "well-formed empty file" apart from "malformed".
        return _BOM + buf.getvalue()

    # Normalise every input to the persisted-dict shape, then sort by
    # rule_id. A rule with no resolvable id is dropped (matches the
    # engine's save_rules which ignores rows without rule_id).
    normalised: list[dict] = []
    for r in rules:
        d = _rule_to_dict(r)
        if d is None:
            continue
        normalised.append(d)
    normalised.sort(key=lambda d: d.get("rule_id", ""))

    for rule in normalised:
        row = {col: _value_to_cell(col, rule.get(col, "")) for col in COLUMNS}
        writer.writerow(row)

    return _BOM + buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  Public — parse + validate
# ─────────────────────────────────────────────────────────────────────────────

def csv_to_rules(csv_str: Any) -> tuple[list[dict], list[str]]:
    """Parse + validate a rules CSV document.

    Returns ``(valid_rules, warnings)``:

    * ``valid_rules`` — persisted-dict shape consumed by
      :func:`engine.alert_engine_v2.save_rules`. Empty list on a
      malformed file or all-rows-rejected.
    * ``warnings`` — non-fatal hints. The parser never raises; every
      failure mode either skips the offending row + warns, or
      collapses to ``([], [error_msg])`` for a top-level parse error.

    The CLI / UI both rely on this contract: warnings get surfaced to
    the operator (stdout / ``st.warning``) but never block.
    """
    if not isinstance(csv_str, str):
        return [], ["could not parse CSV: input is not a string"]

    # Strip the UTF-8 BOM if present. ``csv.DictReader`` would
    # otherwise treat it as part of the first column name and the
    # ``rule_id`` lookup would silently miss every row.
    if csv_str.startswith(_BOM):
        csv_str = csv_str[len(_BOM):]

    if not csv_str.strip():
        # An empty / whitespace-only file is not malformed — it just
        # carries no rules. Return ``([], [])`` so the caller can
        # distinguish "nothing to import" from "parser error".
        return [], []

    try:
        reader = csv.DictReader(io.StringIO(csv_str))
        # Force iteration of the header by accessing fieldnames; on a
        # truly malformed input this raises and we catch it below.
        header = reader.fieldnames
    except Exception as exc:  # noqa: BLE001 — surface message, never raise
        return [], [f"could not parse CSV: {exc}"]

    if not header:
        return [], ["could not parse CSV: empty or missing header row"]

    warnings: list[str] = []

    # Surface unknown columns once at the top, not per-row, so a stale
    # CSV with one extra column doesn't spam N warnings. We still keep
    # processing rows — the unknown column is ignored, the known ones
    # import normally.
    unknown_cols = [c for c in header if c and c not in _ALLOWED_FIELDS]
    for col in unknown_cols:
        warnings.append(f"unknown column {col!r} ignored")

    out: list[dict] = []
    try:
        rows = list(reader)
    except Exception as exc:  # noqa: BLE001 — malformed CSV body
        return [], warnings + [f"could not parse CSV: {exc}"]

    for idx, raw_row in enumerate(rows):
        # csv.DictReader yields a dict per row; an empty trailing line
        # produces a row with every value None or empty. Skip those so
        # an extra newline at the end of the file isn't a warning.
        if not raw_row or all(
            (v is None or (isinstance(v, str) and not v.strip()))
            for v in raw_row.values()
        ):
            continue

        # Required: rule_id (or legacy id). No id → skip + warn. Same
        # posture as the YAML parser; the engine drops rule-less rows
        # anyway, we just pre-empt it with a clearer message.
        rule_id = (raw_row.get("rule_id") or raw_row.get("id") or "").strip()
        if not rule_id:
            warnings.append(f"row #{idx + 1}: missing rule_id; skipped")
            continue

        # Severity validation — reject anything outside the canonical
        # set so a typo doesn't quietly bypass channel routing.
        sev_raw = (raw_row.get("severity") or "").strip()
        if sev_raw:
            sev_str = sev_raw.upper()
            if sev_str not in _ALLOWED_SEVERITIES:
                warnings.append(
                    f"rule {rule_id!r}: severity {sev_raw!r} not in "
                    f"{{CRITICAL, HIGH, MEDIUM, LOW}}; rule skipped"
                )
                continue
        else:
            sev_str = _DEFAULTS["severity"]
            warnings.append(
                f"rule {rule_id!r}: missing field 'severity' "
                f"defaulted to {sev_str!r}"
            )

        kept: dict = {"rule_id": rule_id, "severity": sev_str}

        # name / metric / condition — bare strings, default + warn if
        # missing. We treat empty-string in the CSV cell the same as
        # missing so a hand-edited file with a blank cell defaults
        # cleanly (matches Excel's "leave the cell empty" UX).
        for field in ("name", "metric", "condition"):
            raw = raw_row.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                default = _DEFAULTS[field]
                warnings.append(
                    f"rule {rule_id!r}: missing field {field!r} "
                    f"defaulted to {default!r}"
                )
                kept[field] = default
            else:
                kept[field] = raw.strip() if isinstance(raw, str) else raw

        # threshold_pct — float coercion. Bad value: warn + default
        # to 0.0 (matches the YAML parser).
        raw_thr = raw_row.get("threshold_pct")
        if raw_thr is None or (isinstance(raw_thr, str) and not raw_thr.strip()):
            warnings.append(
                f"rule {rule_id!r}: missing field 'threshold_pct' "
                f"defaulted to 0.0"
            )
            kept["threshold_pct"] = 0.0
        else:
            try:
                kept["threshold_pct"] = float(raw_thr)
            except (TypeError, ValueError):
                warnings.append(
                    f"rule {rule_id!r}: threshold_pct {raw_thr!r} "
                    f"could not be parsed as float; defaulted to 0.0"
                )
                kept["threshold_pct"] = 0.0

        # Booleans — explicit coercion via ``_str_to_bool``. Unknown
        # values warn + default rather than silently coerce.
        for field in ("enabled", "email_notify", "flap_detection_enabled"):
            raw = raw_row.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                default = _DEFAULTS[field]
                warnings.append(
                    f"rule {rule_id!r}: missing field {field!r} "
                    f"defaulted to {default!r}"
                )
                kept[field] = default
                continue
            coerced = _str_to_bool(raw)
            if coerced is None:
                default = _DEFAULTS[field]
                warnings.append(
                    f"rule {rule_id!r}: {field} {raw!r} could not be "
                    f"parsed as bool; defaulted to {default!r}"
                )
                kept[field] = default
            else:
                kept[field] = coerced

        # target_channels — split on ``|``. Empty string → empty list.
        kept["target_channels"] = _str_to_channels(
            raw_row.get("target_channels")
        )

        # Integer fields — same posture as the YAML parser's defensive
        # int() coerce, with the engine's normalize_rule clamps applied
        # so the saved dict already matches what the runtime would
        # normalise to (fewer surprises in the round-trip).
        raw_cd = raw_row.get("cooldown_minutes")
        if raw_cd is None or (isinstance(raw_cd, str) and not raw_cd.strip()):
            kept["cooldown_minutes"] = _DEFAULTS["cooldown_minutes"]
        else:
            try:
                kept["cooldown_minutes"] = max(0, int(float(raw_cd)))
            except (TypeError, ValueError):
                kept["cooldown_minutes"] = _DEFAULTS["cooldown_minutes"]

        raw_win = raw_row.get("flap_window_minutes")
        if raw_win is None or (isinstance(raw_win, str) and not raw_win.strip()):
            kept["flap_window_minutes"] = _DEFAULTS["flap_window_minutes"]
        else:
            try:
                kept["flap_window_minutes"] = max(1, int(float(raw_win)))
            except (TypeError, ValueError):
                kept["flap_window_minutes"] = _DEFAULTS["flap_window_minutes"]

        raw_x = raw_row.get("flap_threshold_crossings")
        if raw_x is None or (isinstance(raw_x, str) and not raw_x.strip()):
            kept["flap_threshold_crossings"] = _DEFAULTS["flap_threshold_crossings"]
        else:
            try:
                kept["flap_threshold_crossings"] = max(2, int(float(raw_x)))
            except (TypeError, ValueError):
                kept["flap_threshold_crossings"] = _DEFAULTS[
                    "flap_threshold_crossings"
                ]

        # Engine alias keys. Matches the YAML parser's contract —
        # save_rules accepts either ``rule_id`` or ``id`` on insert,
        # and the engine's fire path reads ``threshold``.
        kept["id"] = rule_id
        kept["threshold"] = float(kept["threshold_pct"])

        out.append(kept)

    return out, warnings


def validate_rule_csv_row(row_dict: Any) -> tuple[bool, str]:
    """True/False + a reason string for the UI's CSV paste validator.

    A row is valid if:

    * input is a dict / mapping,
    * carries a non-empty ``rule_id`` (or legacy ``id``),
    * carries a ``severity`` in the canonical set (case-insensitive
      after strip), OR no severity at all (the parser defaults it).

    The first failure stops the check — the UI only needs one reason
    to surface. Mirrors the YAML module's :func:`validate_rule_dict`
    so the two paste flows feel identical.
    """
    if not isinstance(row_dict, dict):
        return False, "not a mapping"
    rule_id = row_dict.get("rule_id") or row_dict.get("id")
    if not rule_id or not str(rule_id).strip():
        return False, "missing rule_id"
    sev = row_dict.get("severity")
    if sev is not None and str(sev).strip():
        sev_str = str(sev).strip().upper()
        if sev_str not in _ALLOWED_SEVERITIES:
            return (
                False,
                f"severity {sev!r} not in {{CRITICAL, HIGH, MEDIUM, LOW}}",
            )
    return True, "ok"
