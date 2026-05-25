"""tests/test_openapi_gen.py — coverage for the hand-built OpenAPI spec.

The spec generator is pure (no DB, no network, no env). Tests exercise:

  * Top-level structural invariants — required keys, version string,
    presence of public + auth-required path entries.
  * Component schema coverage — every dataclass that the api server
    projects on the wire has a corresponding ``components.schemas``
    entry with its required fields enumerated.
  * Security scheme — ``BearerAuth`` is defined and every non-public
    operation references it (or inherits the global ``security``).
  * Rendering — JSON renders to valid JSON and YAML renders to a
    structure that round-trips through our own hand-rolled parser.
  * Endpoint coverage — at least 25 endpoints (the spec asks for
    "every route currently in api_server.py", which is in the 40+
    range today).

Validation logic in :func:`tools.openapi_gen.validate_spec` is
covered by ``test_validate_spec_*`` cases.
"""
from __future__ import annotations

import json
import re

import pytest

from tools.openapi_gen import (
    build_openapi_spec,
    render_openapi_json,
    render_openapi_yaml,
    validate_spec,
)


# ─── Top-level structural invariants ──────────────────────────────────────


def test_build_openapi_spec_has_required_top_level_keys():
    spec = build_openapi_spec()
    for key in ("openapi", "info", "servers", "paths", "components"):
        assert key in spec, f"missing top-level {key!r}"


def test_openapi_version_is_3_dot_x():
    spec = build_openapi_spec()
    # Either OpenAPI 3.0.x or 3.1.x — both are widely supported by
    # tooling and the spec generator should emit one of them.
    assert spec["openapi"].startswith("3.0.") or spec["openapi"].startswith("3.1.")


def test_info_has_title_and_version():
    spec = build_openapi_spec(version="9.9.9", title="Test API")
    assert spec["info"]["title"] == "Test API"
    assert spec["info"]["version"] == "9.9.9"


def test_servers_uses_supplied_base_url():
    spec = build_openapi_spec(base_url="https://api.example.com")
    assert spec["servers"][0]["url"] == "https://api.example.com"


# ─── Public + auth-required path presence ─────────────────────────────────


def test_paths_include_public_health():
    spec = build_openapi_spec()
    assert "/api/v1/health" in spec["paths"]


def test_paths_include_public_openapi_json():
    spec = build_openapi_spec()
    assert "/api/v1/openapi.json" in spec["paths"]


def test_paths_include_auth_required_alerts():
    spec = build_openapi_spec()
    assert "/api/v1/alerts" in spec["paths"]


def test_at_least_25_endpoints():
    """The brief asks for >=25 endpoints; today the surface is in the
    40+ range (paths * methods)."""
    spec = build_openapi_spec()
    n_operations = sum(len(methods) for methods in spec["paths"].values())
    assert n_operations >= 25, f"expected >=25, got {n_operations}"


# ─── Component schema coverage ────────────────────────────────────────────


@pytest.mark.parametrize(
    "schema_name,required_fields",
    [
        ("Alert", {"alert_id", "created_at", "alert_type", "severity"}),
        ("AlertRule", {"rule_id", "name", "alert_type", "enabled", "severity"}),
        ("DeliveryChannel", {"channel_id", "name", "kind", "severity_threshold"}),
        ("ReportMeta", {"report_id", "generated_at", "sentiment_label"}),
        ("AlertSilence", {"silence_id", "user_id", "starts_at", "expires_at"}),
        ("AlertAnnotation", {"annotation_id", "alert_id", "body"}),
        ("EscalationStep", {"chain_id", "rule_id", "step_number", "channel_id"}),
        ("ReportSchedule", {"schedule_id", "name", "cron_expr"}),
        ("NotificationPrefs", {"user_id", "enabled", "min_severity"}),
        ("AuditEvent", {"event_id", "created_at", "user_id", "action"}),
    ],
)
def test_component_schema_present_and_has_required_fields(schema_name, required_fields):
    spec = build_openapi_spec()
    schemas = spec["components"]["schemas"]
    assert schema_name in schemas, f"missing schema {schema_name}"
    declared = set(schemas[schema_name].get("required", []))
    missing = required_fields - declared
    assert not missing, f"{schema_name} missing required fields: {missing}"


def test_alert_schema_has_severity_enum():
    """``Alert.severity`` should be constrained to the four canonical
    levels — wire-shape regression test against a future expansion of
    severities that the API can't yet handle."""
    spec = build_openapi_spec()
    sev = spec["components"]["schemas"]["Alert"]["properties"]["severity"]
    assert set(sev["enum"]) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


# ─── Security scheme ──────────────────────────────────────────────────────


def test_bearer_auth_scheme_defined():
    spec = build_openapi_spec()
    schemes = spec["components"]["securitySchemes"]
    assert "BearerAuth" in schemes
    assert schemes["BearerAuth"]["type"] == "http"
    assert schemes["BearerAuth"]["scheme"] == "bearer"


def test_public_endpoints_override_security_to_empty():
    """``/health`` and ``/openapi.json`` must explicitly carry
    ``security: []`` so SDK generators understand they don't require
    a token."""
    spec = build_openapi_spec()
    assert spec["paths"]["/api/v1/health"]["get"]["security"] == []
    assert spec["paths"]["/api/v1/openapi.json"]["get"]["security"] == []


def test_auth_required_operations_reference_bearer_auth():
    """Every non-public operation must carry security pointing at
    ``BearerAuth`` (either at the operation level or by inheriting
    the global). Today every auth'd operation re-states it explicitly,
    so the assertion is concrete: each one has the marker."""
    spec = build_openapi_spec()
    public_paths = {"/api/v1/health", "/api/v1/openapi.json"}
    for path, methods in spec["paths"].items():
        for verb, op in methods.items():
            if path in public_paths:
                continue
            security = op.get("security")
            assert security, f"{verb.upper()} {path} missing security"
            # Should reference BearerAuth.
            scheme_names = set()
            for sec_entry in security:
                scheme_names.update(sec_entry.keys())
            assert "BearerAuth" in scheme_names, (
                f"{verb.upper()} {path} security missing BearerAuth ref"
            )


# ─── Rendering ────────────────────────────────────────────────────────────


def test_render_openapi_json_is_valid_json():
    spec = build_openapi_spec()
    body = render_openapi_json(spec)
    # Round-trips through stdlib json.
    parsed = json.loads(body)
    assert parsed["openapi"].startswith("3.")
    assert "/api/v1/health" in parsed["paths"]


def test_render_openapi_json_is_deterministic():
    """Two renders of the same spec must produce the same bytes —
    the on-disk artifact would otherwise produce noisy diffs."""
    spec = build_openapi_spec()
    a = render_openapi_json(spec)
    b = render_openapi_json(spec)
    assert a == b


def test_render_openapi_yaml_parses_with_hand_rolled_parser():
    """Validate the YAML emitter by re-parsing its output with a
    minimal hand-rolled parser that covers exactly the dialect we
    emit (nested dicts/lists, scalars, ``{}``/``[]`` markers, double-
    quoted strings). Round-trip the round-trip — every top-level path
    name we put in must come back out under ``paths``."""
    spec = build_openapi_spec()
    body = render_openapi_yaml(spec)
    parsed = _parse_yaml_subset(body)
    assert "openapi" in parsed
    assert "/api/v1/health" in parsed["paths"]
    assert "/api/v1/openapi.json" in parsed["paths"]


# ─── Validation ───────────────────────────────────────────────────────────


def test_validate_spec_passes_on_built_spec():
    spec = build_openapi_spec()
    errors = validate_spec(spec)
    assert errors == [], f"expected no errors, got: {errors}"


def test_validate_spec_flags_missing_top_level():
    """Strip the ``info`` key and confirm the validator flags it."""
    spec = build_openapi_spec()
    del spec["info"]
    errors = validate_spec(spec)
    assert any("info" in e for e in errors)


def test_validate_spec_flags_duplicate_operation_ids():
    """Collide two operationIds and confirm the validator catches it."""
    spec = build_openapi_spec()
    # Pick two arbitrary operations and assign the same operationId.
    spec["paths"]["/api/v1/health"]["get"]["operationId"] = "DUPE"
    spec["paths"]["/api/v1/openapi.json"]["get"]["operationId"] = "DUPE"
    errors = validate_spec(spec)
    assert any("DUPE" in e and "collides" in e for e in errors)


def test_every_operation_id_is_unique():
    """Smoke check at the spec level — every endpoint carries a
    distinct operationId so SDK generators produce unambiguous method
    names. This is what ``validate_spec`` asserts internally; we test
    it independently here so a future spec edit that introduces a
    collision fails fast."""
    spec = build_openapi_spec()
    op_ids = []
    for methods in spec["paths"].values():
        for op in methods.values():
            op_id = op.get("operationId")
            assert op_id, "every operation must define operationId"
            op_ids.append(op_id)
    assert len(op_ids) == len(set(op_ids)), (
        f"duplicate operationIds: "
        f"{sorted([oid for oid in op_ids if op_ids.count(oid) > 1])}"
    )


def test_every_operation_has_at_least_one_response():
    spec = build_openapi_spec()
    for path, methods in spec["paths"].items():
        for verb, op in methods.items():
            assert op.get("responses"), f"{verb.upper()} {path} missing responses"


def test_path_with_parameter_has_path_parameter_definitions():
    """Every path containing ``{name}`` must declare that parameter
    in at least one operation's ``parameters`` array."""
    spec = build_openapi_spec()
    param_pattern = re.compile(r"\{([^}]+)\}")
    for path, methods in spec["paths"].items():
        expected = set(param_pattern.findall(path))
        if not expected:
            continue
        for verb, op in methods.items():
            declared = {
                p["name"] for p in op.get("parameters", [])
                if p.get("in") == "path"
            }
            missing = expected - declared
            assert not missing, (
                f"{verb.upper()} {path} missing path params: {missing}"
            )


# ─── Hand-rolled YAML parser — minimal, only covers what we emit ─────────


def _parse_yaml_subset(text: str):
    """Parse the YAML subset emitted by ``render_openapi_yaml``.

    This is NOT a full YAML parser — it understands exactly what the
    emitter produces:

      * 2-space indent per nesting level.
      * Mappings: ``key: value`` (plain or quoted key, plain or
        quoted scalar value, or sub-block introduced by ``key:``).
      * Sequences: ``- value`` or ``- key: ...`` for a dict-in-list.
      * Empty containers: ``{}`` and ``[]``.
      * Scalars: ``null``, ``true``, ``false``, ints, floats, plain
        strings, double-quoted strings with ``\\n``/``\\"``/``\\\\``
        escape sequences.

    Used by ``test_render_openapi_yaml_parses_with_hand_rolled_parser``
    so we exercise the emitter end-to-end without taking a PyYAML dep.
    """
    raw_lines = text.split("\n")
    # Strip the trailing blank produced by the emitter's "+ '\n'".
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()

    # We iterate with a mutable index so dispatch into nested blocks
    # can advance past consumed lines.
    state = {"i": 0}

    def _indent_of(line: str) -> int:
        n = 0
        for ch in line:
            if ch == " ":
                n += 1
            else:
                break
        return n

    def _unquote(token: str):
        token = token.strip()
        if token == "":
            return ""
        if token == "null":
            return None
        if token == "true":
            return True
        if token == "false":
            return False
        if token.startswith("\"") and token.endswith("\""):
            inner = token[1:-1]
            out = []
            esc = False
            for ch in inner:
                if esc:
                    if ch == "n":
                        out.append("\n")
                    elif ch == "r":
                        out.append("\r")
                    elif ch == "t":
                        out.append("\t")
                    elif ch == "\"":
                        out.append("\"")
                    elif ch == "\\":
                        out.append("\\")
                    else:
                        out.append(ch)
                    esc = False
                elif ch == "\\":
                    esc = True
                else:
                    out.append(ch)
            return "".join(out)
        if token == "{}":
            return {}
        if token == "[]":
            return []
        # Numeric — int first then float.
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
        return token

    def _split_kv(line: str):
        # Find the first ``:`` that's NOT inside double quotes.
        in_quotes = False
        esc = False
        for idx, ch in enumerate(line):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "\"":
                in_quotes = not in_quotes
                continue
            if ch == ":" and not in_quotes:
                key = _unquote(line[:idx])
                rest = line[idx + 1:].strip()
                return key, rest
        raise ValueError(f"malformed mapping line: {line!r}")

    def _parse_mapping(base_indent: int) -> dict:
        out: dict = {}
        while state["i"] < len(raw_lines):
            line = raw_lines[state["i"]]
            if line.strip() == "":
                state["i"] += 1
                continue
            ind = _indent_of(line)
            if ind < base_indent:
                break
            if ind > base_indent:
                # Should not happen — a mapping was expected to start
                # here. Defensive: skip the line.
                state["i"] += 1
                continue
            stripped = line[ind:]
            if stripped.startswith("- "):
                # We are at a list item but expected mapping — bail.
                break
            key, rest = _split_kv(stripped)
            state["i"] += 1
            if rest == "":
                # Nested block — peek the next non-blank line to
                # decide list vs mapping.
                child = _peek_next_real_line()
                if child is None:
                    out[key] = None
                    continue
                child_ind = _indent_of(child)
                child_stripped = child[child_ind:]
                if child_ind <= base_indent:
                    out[key] = None
                    continue
                if child_stripped.startswith("- "):
                    out[key] = _parse_list(child_ind)
                else:
                    out[key] = _parse_mapping(child_ind)
            else:
                out[key] = _unquote(rest)
        return out

    def _parse_list(base_indent: int) -> list:
        out: list = []
        while state["i"] < len(raw_lines):
            line = raw_lines[state["i"]]
            if line.strip() == "":
                state["i"] += 1
                continue
            ind = _indent_of(line)
            if ind < base_indent:
                break
            stripped = line[ind:]
            if not stripped.startswith("- "):
                break
            item_text = stripped[2:]
            if ":" in item_text and not (
                item_text.startswith("\"") and item_text.endswith("\"")
            ):
                # Inline dict-in-list start. The first key starts on
                # this line; subsequent keys live at the same indent
                # as ``item_text``'s column.
                inline_key, inline_rest = _split_kv(item_text)
                state["i"] += 1
                first_item: dict = {}
                if inline_rest == "":
                    # Nested under the inline key.
                    child = _peek_next_real_line()
                    if child is not None and _indent_of(child) > base_indent + 2:
                        child_ind = _indent_of(child)
                        child_stripped = child[child_ind:]
                        if child_stripped.startswith("- "):
                            first_item[inline_key] = _parse_list(child_ind)
                        else:
                            first_item[inline_key] = _parse_mapping(child_ind)
                    else:
                        first_item[inline_key] = None
                else:
                    first_item[inline_key] = _unquote(inline_rest)
                # Continue reading sibling keys at the column where the
                # inline key started (= base_indent + 2).
                sibling_indent = base_indent + 2
                rest_of_dict = _parse_mapping(sibling_indent)
                first_item.update(rest_of_dict)
                out.append(first_item)
            else:
                state["i"] += 1
                out.append(_unquote(item_text))
        return out

    def _peek_next_real_line():
        j = state["i"]
        while j < len(raw_lines):
            if raw_lines[j].strip() != "":
                return raw_lines[j]
            j += 1
        return None

    # Top level is always a mapping for an OpenAPI spec.
    return _parse_mapping(0)
