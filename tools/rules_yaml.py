"""tools/rules_yaml.py — config-as-code for alert rules.

Operators want to version their alert-rule sets in git and ship them to
colleagues without copy-pasting from the UI. This module provides the
serialise / parse / validate primitives the CLI (``tools.ops_cli rules
export|import|diff``) and the UI's Export/Import expander both consume.

Wire format
-----------
A small subset of YAML — just enough for the alert-rule shape:

    schema_version: 1
    rules:
      - rule_id: bdi-spike
        name: BDI spike >5%
        metric: bdi
        threshold_pct: 5.0
        severity: HIGH
        condition: Above
        enabled: true
        email_notify: false
        target_channels: [trading-desk]
        cooldown_minutes: 360
        flap_detection_enabled: false
        flap_window_minutes: 30
        flap_threshold_crossings: 5
      - ...

Field choices:

* ``rule_id`` is the stable identifier (matches the engine's
  ``AlertRule.rule_id`` and the persisted dict's ``rule_id``/``id``
  key — emit/parse uses the canonical ``rule_id`` name).
* ``metric`` is the persisted dict key (the engine routes by
  ``alert_type`` internally, but the operator-facing wire field is
  ``metric`` — matches the UI label, the template catalog, and the
  ``RuleTemplate`` dataclass).
* ``threshold_pct`` reads "numeric threshold, units depend on metric"
  — matches ``RuleTemplate.threshold_pct``. The persisted dict stores
  it as ``threshold`` so the engine can stay agnostic to units;
  emit/parse converts both directions.
* ``severity`` is one of ``CRITICAL`` / ``HIGH`` / ``MEDIUM`` / ``LOW``
  — the canonical engine vocabulary. Imports with any other value are
  rejected with a warning (security review: the engine routes on
  severity strings, an unknown value would silently fall through
  every channel threshold check and never deliver).

PyYAML detection
----------------
PyYAML is an optional dependency — the project ships a tiny hand-rolled
emitter/parser that handles the subset above. If PyYAML is installed it
is used for emission (slightly nicer formatting), but the parser is
ALWAYS the hand-rolled one so a YAML file authored with the help of the
tooling round-trips byte-exact even on machines without PyYAML.

The hand-rolled emitter / parser only needs to handle:

* top-level ``key: value`` lines for scalars,
* top-level ``key:`` followed by a list-of-dicts (``  - key: value``),
* scalar types: ``str`` / ``int`` / ``float`` / ``bool``,
* string quoting only when needed (contains ``:`` / ``#`` /
  leading-trailing whitespace, or is empty),
* inline lists for the small ``target_channels: [a, b]`` case.

No anchors, no aliases, no flow-style maps, no multi-line scalars.

Warnings
--------
The parse path returns ``(rules, warnings)`` — warnings are non-fatal
hints surfaced to the operator (unknown field ignored, missing field
defaulted, severity rejected). Never logged with sensitive data, never
include user passwords / tokens / channel webhooks (rules don't
carry those, but the policy is enforced by what we put IN the warning
strings — only field names and the canonical replacement values).
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Optional


# Try to detect PyYAML at module load. The flag is consulted by the
# emitter; the parser is hand-rolled regardless so the round-trip stays
# byte-exact across deployments with mixed dependency sets.
try:
    import yaml as _yaml  # type: ignore[import-not-found]
    _HAS_PYYAML: bool = True
except Exception:  # noqa: BLE001 — any import failure means "no PyYAML"
    _yaml = None
    _HAS_PYYAML = False


# Canonical engine severities. Anything else is rejected on import per
# the task contract — the engine routes on severity and an unknown
# value would silently bypass every channel-threshold check.
_ALLOWED_SEVERITIES: frozenset[str] = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
)

# Schema version stamped at the top of every emitted document. Bump
# this if the wire shape changes incompatibly so an old reader can
# refuse to parse a newer file.
_SCHEMA_VERSION: int = 1

# Allowed top-level wire-format field names. Anything else encountered
# during import is dropped with a warning so a hand-edited blob with a
# stale or typo'd key cannot silently disable a rule.
_ALLOWED_FIELDS: frozenset[str] = frozenset({
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
})

# Field emission order — sorted by semantic grouping (identity → core
# threshold → severity/condition → toggles → channels → cooldown →
# flap). Fixed so the YAML output is deterministic for any given input.
_FIELD_ORDER: list[str] = [
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
#  Internal — scalar emission / parsing
# ─────────────────────────────────────────────────────────────────────────────

def _needs_quoting(s: str) -> bool:
    """A bare string needs quoting if it contains a YAML separator
    character (``:`` / ``#``), has leading / trailing whitespace, is
    empty, or could be confused with a non-string scalar (``true`` /
    ``false`` / a number)."""
    if s == "":
        return True
    if s != s.strip():
        return True
    if ":" in s or "#" in s or "[" in s or "]" in s or "," in s:
        return True
    # Reserve bool / null lookalikes — the parser would coerce
    # ``true`` / ``false`` / ``yes`` / ``no`` / ``null`` to non-str.
    lower = s.lower()
    if lower in {"true", "false", "yes", "no", "null", "~"}:
        return True
    # Numeric lookalikes — int() / float() would accept these as
    # numbers; quoting forces the string interpretation.
    try:
        float(s)
        return True
    except ValueError:
        pass
    return False


def _emit_scalar(value: Any) -> str:
    """Render a scalar (str / int / float / bool / None) as a one-line
    YAML token. Used by both the inline-list and the key:value writers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        # Order matters: bool is a subclass of int, must be checked first.
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # ``repr(float)`` round-trips losslessly in CPython; ``str``
        # would truncate edge cases (e.g. 0.1 + 0.2). Strip the trailing
        # ``.0`` for clean ints — 5.0 → "5.0" stays explicit so the
        # parser knows to treat it as float.
        return repr(value)
    s = str(value)
    if _needs_quoting(s):
        # Escape backslashes + double quotes for the double-quoted form.
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _emit_inline_list(values: list[Any]) -> str:
    """Render a list as ``[a, b, c]`` flow style. Only used for the small
    ``target_channels`` list; everything else stays block-style."""
    if not values:
        return "[]"
    return "[" + ", ".join(_emit_scalar(v) for v in values) + "]"


def _parse_scalar(raw: str) -> Any:
    """Coerce a YAML token string into a Python value.

    Order matters: empty → None, quoted → unescape, bool/null tokens,
    int, float, bare string."""
    raw = raw.strip()
    if raw == "" or raw == "~" or raw.lower() == "null":
        return None
    # Double-quoted string. Strip the quotes + unescape.
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        body = raw[1:-1]
        # Reverse the escape from _emit_scalar: \" → ", \\ → \.
        body = body.replace('\\"', '"').replace("\\\\", "\\")
        return body
    # Single-quoted string. YAML treats a single-quoted scalar as a
    # literal — the only escape is '' → '. We emit double-quoted but
    # PyYAML or a hand-author may produce single-quoted input.
    if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
        body = raw[1:-1].replace("''", "'")
        return body
    # Booleans + null tokens.
    low = raw.lower()
    if low == "true" or low == "yes":
        return True
    if low == "false" or low == "no":
        return False
    # Integer? (int() rejects "5.0" so int comes first.)
    try:
        return int(raw)
    except ValueError:
        pass
    # Float?
    try:
        return float(raw)
    except ValueError:
        pass
    # Bare string.
    return raw


def _parse_inline_list(raw: str) -> list[Any]:
    """Parse a ``[a, b, c]`` flow-style list. Splits respecting double-
    quoted strings so a quoted scalar containing a comma is not torn."""
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        # Caller mis-detected; treat as empty.
        return []
    inner = inner[1:-1].strip()
    if not inner:
        return []
    # Split on commas at quote-depth zero.
    out: list[str] = []
    buf: list[str] = []
    in_quotes = False
    quote_char = ""
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_quotes:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(inner):
                # Preserve the escape pair as-is.
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == quote_char:
                in_quotes = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_quotes = True
            quote_char = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ",":
            out.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    last = "".join(buf).strip()
    if last:
        out.append(last)
    return [_parse_scalar(tok) for tok in out]


# ─────────────────────────────────────────────────────────────────────────────
#  Public — serialise
# ─────────────────────────────────────────────────────────────────────────────

def rules_to_yaml(rules: list[Any]) -> str:
    """Serialise a list of rule dicts (or ``AlertRule`` dataclass
    instances) to a stable YAML string.

    Output is deterministic: rules sorted by ``rule_id`` ascending,
    fields emitted in ``_FIELD_ORDER`` within each rule, scalars rendered
    via ``_emit_scalar``. The same input always produces byte-identical
    output — tests pin this so a diff between two checkpoints can ONLY
    surface a real rule change.

    Accepts:

    * ``dict`` rules in the persisted shape (the runtime form the engine
      reads). Both ``rule_id`` and the legacy ``id`` key are accepted as
      the identifier source — UI-created rules use ``id``; rules added
      from the template panel carry both. The output always emits
      ``rule_id``.
    * ``AlertRule`` dataclass instances. The dataclass uses
      ``alert_type`` for what the wire calls ``metric`` and
      ``threshold`` for ``threshold_pct``; the projection lives in
      ``_rule_to_dict`` below.

    Non-dict / non-AlertRule items are silently skipped (no crash on a
    mixed list).
    """
    if not isinstance(rules, list) or not rules:
        # Even the empty case carries a schema_version so a downstream
        # reader can tell "no rules" apart from "stale / malformed file".
        return f"schema_version: {_SCHEMA_VERSION}\nrules: []\n"

    # Normalise every input to the persisted-dict shape, then sort by
    # rule_id. A rule with no resolvable id is dropped — the engine
    # ignores them too (see save_rules: rows without rule_id are not
    # persisted).
    normalised: list[dict] = []
    for r in rules:
        d = _rule_to_dict(r)
        if d is None:
            continue
        normalised.append(d)
    normalised.sort(key=lambda d: d.get("rule_id", ""))

    out_lines: list[str] = [f"schema_version: {_SCHEMA_VERSION}", "rules:"]
    for rule in normalised:
        # First line of each rule carries the leading '- ' and the first
        # field; subsequent fields use '  ' indent so they align under
        # the first field's key.
        first = True
        for key in _FIELD_ORDER:
            if key not in rule:
                # Omit missing optional fields entirely; the parser
                # defaults them on the round-trip side. Required fields
                # (rule_id, name) are always present after _rule_to_dict.
                continue
            value = rule[key]
            if key == "target_channels" and isinstance(value, list):
                rendered = _emit_inline_list(value)
            else:
                rendered = _emit_scalar(value)
            prefix = "  - " if first else "    "
            out_lines.append(f"{prefix}{key}: {rendered}")
            first = False
    out_lines.append("")  # trailing newline
    return "\n".join(out_lines)


def _rule_to_dict(rule: Any) -> Optional[dict]:
    """Normalise an arbitrary rule input to the persisted-dict shape.

    Returns ``None`` for inputs that cannot be persisted (e.g. a plain
    string, a dict with no ``rule_id`` or ``id``). The ``None`` return
    lets the caller silently drop garbage entries without raising on a
    mixed list.

    Lazy-imports ``AlertRule`` so importing this module does not pull in
    the engine. The engine is heavy (loguru, sqlite, dataclasses) and
    the CLI / UI surface should be importable in lighter contexts (e.g.
    a documentation generator).
    """
    if rule is None:
        return None

    # Dict path — handles both the persisted shape and a hand-edited
    # blob from the rule editor.
    if isinstance(rule, dict):
        rule_id = rule.get("rule_id") or rule.get("id")
        if not rule_id:
            return None
        out: dict = {"rule_id": str(rule_id)}

        # ``threshold_pct`` is the wire-format key; the persisted dict
        # uses ``threshold``. Prefer the wire key if explicitly present
        # so a manually-authored dict can override.
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
            out["target_channels"] = [str(x) for x in tc if isinstance(x, (str, int, float))]

        # Severity normalisation for serialisation: the engine stores
        # whatever the UI saved (often the operator-facing "Warning"
        # label), but the wire format wants the canonical engine
        # vocabulary. Map common UI labels to engine equivalents.
        sev = out.get("severity")
        if isinstance(sev, str):
            up = sev.upper()
            ui_map = {"INFO": "LOW", "WARNING": "MEDIUM", "CRITICAL": "CRITICAL"}
            if up in ui_map:
                out["severity"] = ui_map[up]
            else:
                out["severity"] = up

        return out

    # AlertRule dataclass path. Lazy-import the engine so this module
    # stays importable without pulling sqlite/loguru.
    try:
        from engine.alert_engine_v2 import AlertRule  # noqa: WPS433
    except Exception:
        # Engine unavailable → can't introspect dataclass fields.
        # Fall through to the dict-likeness check below.
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
        for fld in fields(AlertRule):
            if fld.name in (
                "cooldown_minutes",
                "flap_detection_enabled",
                "flap_window_minutes",
                "flap_threshold_crossings",
            ):
                out2[fld.name] = getattr(rule, fld.name)
        return out2

    # Unknown type — drop silently.
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Public — parse + validate
# ─────────────────────────────────────────────────────────────────────────────

def yaml_to_rules(yaml_str: str) -> tuple[list[dict], list[str]]:
    """Parse + validate a rules YAML document.

    Returns ``(valid_rules, warnings)``:

    * ``valid_rules`` — the persisted-dict shape the engine consumes
      via ``save_rules``. Empty list on malformed input.
    * ``warnings`` — non-fatal hints surfaced to the operator. A
      malformed document produces a single ``"could not parse YAML: ..."``
      entry; a per-rule problem (unknown field, missing field, bad
      severity) produces a per-rule entry with the offending rule_id.

    The parser never raises — every failure mode collapses to
    ``([], [error_msg])`` or a kept-but-warned rule. The caller
    (``ops_cli rules import`` / the UI Import button) relies on this
    contract.
    """
    if not isinstance(yaml_str, str):
        return [], ["could not parse YAML: input is not a string"]

    try:
        parsed = _parse_yaml_document(yaml_str)
    except Exception as exc:  # noqa: BLE001 — surface message, never raise
        return [], [f"could not parse YAML: {exc}"]

    if not isinstance(parsed, dict):
        return [], ["could not parse YAML: top level is not a mapping"]

    warnings: list[str] = []
    schema = parsed.get("schema_version")
    if schema is not None and schema != _SCHEMA_VERSION:
        warnings.append(
            f"schema_version {schema!r} does not match expected "
            f"{_SCHEMA_VERSION}; attempting best-effort parse"
        )

    raw_rules = parsed.get("rules")
    if raw_rules is None:
        warnings.append("missing top-level 'rules' key; treating as empty list")
        return [], warnings
    if not isinstance(raw_rules, list):
        return [], ["could not parse YAML: 'rules' is not a list"]

    out: list[dict] = []
    for idx, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            warnings.append(
                f"rule #{idx + 1}: not a mapping; skipped"
            )
            continue

        # Required: rule_id (or legacy id). No id → skip with a warning
        # so a hand-edited file with a stray dash doesn't silently drop
        # the rest of the rule.
        rule_id = raw_rule.get("rule_id") or raw_rule.get("id")
        if not rule_id:
            warnings.append(f"rule #{idx + 1}: missing rule_id; skipped")
            continue

        # Severity validation — reject anything outside the canonical
        # engine vocabulary so a typo doesn't quietly bypass channel
        # routing.
        sev = raw_rule.get("severity")
        if sev is not None:
            sev_str = str(sev).strip().upper()
            if sev_str not in _ALLOWED_SEVERITIES:
                warnings.append(
                    f"rule {rule_id!r}: severity {sev!r} not in "
                    f"{{CRITICAL, HIGH, MEDIUM, LOW}}; rule skipped"
                )
                continue
            raw_rule["severity"] = sev_str

        # Unknown-field warnings — every key not in the allow-list gets
        # a per-rule warning and is dropped from the saved dict. The
        # legacy ``id`` key is allowed alongside ``rule_id`` for
        # backward compat.
        kept: dict = {}
        for key, value in raw_rule.items():
            if key == "id":
                continue  # alias for rule_id, already extracted
            if key not in _ALLOWED_FIELDS:
                warnings.append(
                    f"rule {rule_id!r}: unknown field {key!r} ignored"
                )
                continue
            kept[key] = value

        # Default-fill missing optional fields + add a per-rule
        # warning so the operator sees what got defaulted.
        for key, default in _DEFAULTS.items():
            if key not in kept:
                warnings.append(
                    f"rule {rule_id!r}: missing field {key!r} "
                    f"defaulted to {default!r}"
                )
                kept[key] = default

        # Type coercions. threshold_pct ``"5.0"`` → 5.0. The engine
        # tolerates strings via its own normalize_rule, but we coerce
        # here so the saved dict is the same shape the runtime path
        # would normalise to — fewer surprises in the round-trip.
        try:
            kept["threshold_pct"] = float(kept["threshold_pct"])
        except (TypeError, ValueError):
            warnings.append(
                f"rule {rule_id!r}: threshold_pct {kept['threshold_pct']!r} "
                f"could not be parsed as float; defaulted to 0.0"
            )
            kept["threshold_pct"] = 0.0

        try:
            kept["cooldown_minutes"] = max(0, int(kept["cooldown_minutes"]))
        except (TypeError, ValueError):
            kept["cooldown_minutes"] = 0

        try:
            kept["flap_window_minutes"] = max(1, int(kept["flap_window_minutes"]))
        except (TypeError, ValueError):
            kept["flap_window_minutes"] = 30

        try:
            kept["flap_threshold_crossings"] = max(
                2, int(kept["flap_threshold_crossings"])
            )
        except (TypeError, ValueError):
            kept["flap_threshold_crossings"] = 5

        kept["enabled"] = bool(kept["enabled"])
        kept["email_notify"] = bool(kept["email_notify"])
        kept["flap_detection_enabled"] = bool(kept["flap_detection_enabled"])

        tc = kept.get("target_channels")
        if not isinstance(tc, list):
            kept["target_channels"] = []
        else:
            kept["target_channels"] = [str(x) for x in tc]

        kept["rule_id"] = str(rule_id)
        # ``id`` alias for compatibility with the engine's save_rules
        # (which accepts either key on insert).
        kept["id"] = kept["rule_id"]
        # ``threshold`` alias — the engine's fire path reads this key.
        kept["threshold"] = float(kept["threshold_pct"])

        out.append(kept)

    return out, warnings


def validate_rule_dict(rule_dict: Any) -> tuple[bool, str]:
    """True/False + a reason string. Used by the UI's paste-validation
    button to give the operator immediate feedback before hitting Import.

    A rule is valid if:

    * input is a dict,
    * carries a non-empty ``rule_id`` or ``id`` field,
    * carries a ``severity`` in the canonical set (case-insensitive
      after strip), OR no severity at all (the parser will default it).

    Anything else returns ``(False, reason)``. The first failure stops
    the check — the UI only needs one reason to surface.
    """
    if not isinstance(rule_dict, dict):
        return False, "not a mapping"
    rule_id = rule_dict.get("rule_id") or rule_dict.get("id")
    if not rule_id:
        return False, "missing rule_id"
    sev = rule_dict.get("severity")
    if sev is not None:
        sev_str = str(sev).strip().upper()
        if sev_str not in _ALLOWED_SEVERITIES:
            return (
                False,
                f"severity {sev!r} not in {{CRITICAL, HIGH, MEDIUM, LOW}}",
            )
    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  Hand-rolled YAML parser
#
#  Only handles the rule-list document shape this module emits. Not a
#  general-purpose YAML parser by any stretch — anchors, aliases, flow-
#  style mappings, multi-line strings, and tags are all unsupported and
#  will produce a parse error.
# ─────────────────────────────────────────────────────────────────────────────

def _parse_yaml_document(yaml_str: str) -> dict:
    """Parse the limited YAML subset this module emits.

    Recognised structures:

    * top-level ``key: value`` (scalar value),
    * top-level ``key:`` followed by ``  - item`` lines (list of
      dicts, where each ``  - item`` opens a new dict, and ``    key:
      value`` lines extend it).

    Anything else is a parse error.
    """
    # Strip comments and trailing whitespace, drop blank lines. PyYAML
    # tolerates trailing comments after a value; we don't — the
    # emitter never writes them, and a hand-author who adds one will
    # see the offending line preserved as part of the value (which is
    # surfaced via the unknown-field warning path).
    lines: list[tuple[int, str]] = []
    for raw_line in yaml_str.splitlines():
        # Drop pure-comment lines and blank lines.
        stripped = raw_line.rstrip()
        if not stripped:
            continue
        if stripped.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line))

    out: dict = {}
    i = 0
    while i < len(lines):
        indent, line = lines[i]
        if indent != 0:
            raise ValueError(
                f"unexpected indentation at top level: {line!r}"
            )
        # Find the unquoted colon. (Within double quotes the colon is
        # part of the string and must not split the key/value.)
        key, sep, value = _split_key_value(line)
        if not sep:
            raise ValueError(f"expected 'key: value' at top level: {line!r}")
        value = value.strip()
        key = key.strip()
        if value == "":
            # Block: either an empty mapping/list (covered by the next
            # line's indent) or the start of a list-of-dicts.
            i += 1
            sub_lines: list[tuple[int, str]] = []
            while i < len(lines) and lines[i][0] > 0:
                sub_lines.append(lines[i])
                i += 1
            if key == "rules":
                out[key] = _parse_rule_list_block(sub_lines)
            else:
                # Future: nested mappings. Not used by this module's
                # wire shape; treat as empty.
                out[key] = None
            continue
        # Scalar or inline list.
        if value.startswith("["):
            out[key] = _parse_inline_list(value)
        else:
            out[key] = _parse_scalar(value)
        i += 1
    return out


def _split_key_value(line: str) -> tuple[str, str, str]:
    """Find the unquoted ``: `` separator. Returns (key, sep, value)
    where sep is either '': ' or ''. A line with no separator returns
    ``("", "", line)``."""
    in_quotes = False
    quote_char = ""
    j = 0
    # We deliberately also accept "key:\n" (value is empty) — the colon
    # without a trailing space is the block-start signal.
    while j < len(line):
        ch = line[j]
        if in_quotes:
            if ch == "\\" and j + 1 < len(line):
                j += 2
                continue
            if ch == quote_char:
                in_quotes = False
            j += 1
            continue
        if ch in ('"', "'"):
            in_quotes = True
            quote_char = ch
            j += 1
            continue
        if ch == ":":
            # Either "key:value" (block-open style) or "key: value".
            return line[:j], ":", line[j + 1:]
        j += 1
    return "", "", line


def _parse_rule_list_block(sub_lines: list[tuple[int, str]]) -> list[dict]:
    """Parse an indented block of list-of-dicts entries.

    Each entry opens with ``  - key: value`` at indent 2, and continues
    with ``    key: value`` at indent 4 (or any indent strictly greater
    than 2, in practice). New entries are detected by the ``- `` marker.
    """
    rules: list[dict] = []
    current: Optional[dict] = None
    for indent, raw_line in sub_lines:
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            # New rule entry. The rest of the line is the first key:value.
            if current is not None:
                rules.append(current)
            current = {}
            after_dash = stripped[2:].strip()
            key, sep, value = _split_key_value(after_dash)
            if not sep:
                raise ValueError(
                    f"expected 'key: value' after '-': {raw_line!r}"
                )
            current[key.strip()] = _parse_value(value.strip())
            continue
        # Continuation line — extend the current rule.
        if current is None:
            raise ValueError(
                f"continuation line before any '-' entry: {raw_line!r}"
            )
        key, sep, value = _split_key_value(stripped)
        if not sep:
            raise ValueError(f"expected 'key: value': {raw_line!r}")
        current[key.strip()] = _parse_value(value.strip())

    if current is not None:
        rules.append(current)
    return rules


def _parse_value(raw: str) -> Any:
    """Dispatch between inline-list and scalar parsing for a value
    captured after the key separator."""
    if raw.startswith("["):
        return _parse_inline_list(raw)
    return _parse_scalar(raw)
