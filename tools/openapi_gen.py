"""tools/openapi_gen.py — hand-rolled OpenAPI 3.0 spec for ``worker/api_server.py``.

The Ship Tracker API is implemented in stdlib ``http.server`` with a path-
regex dispatch table (``worker/api_server.py``). It exposes 35+ public
endpoints (alerts, reports, rules, channels, audit, silences, annotations,
escalations, schedules, telemetry, health, …) that until now were documented
only in ``docs/DEPLOYMENT.md`` prose.

This module emits the same surface as a complete OpenAPI 3.0 specification
dict — suitable for serving at ``/api/v1/openapi.json``, feeding into
``openapi-generator-cli`` to produce typed SDKs, or rendering with Swagger
UI / Redoc for browsable docs.

Design notes
------------

* **Hand-built, not auto-introspected.** OpenAPI specs that are derived
  from runtime route registration always drift from reality the moment
  someone tweaks a docstring or a response shape. We accept the cost of
  keeping the spec in lock-step with the implementation explicitly — the
  spec is a deliverable, not a side-effect.

* **Stdlib only.** No PyYAML dep — the YAML emitter is hand-rolled. The
  spec is purely nested dicts of JSON-scalar values + lists, which is a
  proper subset of YAML 1.2.

* **Cached at module level.** ``build_openapi_spec`` returns a fresh dict
  on each call so callers can mutate it, but the API-server endpoint
  caches the rendered JSON bytes on first request — see
  ``worker.api_server._serve_openapi``.

* **Schemas mirror dataclass shapes 1:1.** The ``components.schemas``
  section reflects the same field names + types as the Python
  dataclasses in ``engine.alert_engine_v2``, ``engine.alert_delivery``,
  ``engine.alert_silences``, etc. Refactor a dataclass → refactor the
  spec, or the docs lie.
"""
from __future__ import annotations

import json
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers — small factories for parts of the spec that repeat across many
#  endpoints. Keeping them as functions instead of module-level constants
#  means each ``build_openapi_spec`` call returns an independent dict (so
#  callers cannot accidentally mutate the source-of-truth).
# ─────────────────────────────────────────────────────────────────────────────

def _bearer_security() -> list[dict[str, list]]:
    """OpenAPI ``security`` array referencing the ``BearerAuth`` scheme.

    Used as the ``security`` field of every authenticated operation.
    A separate ``[]`` (empty list) at the operation level overrides the
    global security requirement to "no auth needed" — that's how
    ``/api/v1/health`` and ``/api/v1/openapi.json`` are marked public.
    """
    return [{"BearerAuth": []}]


def _response_unauthorized() -> dict:
    """Reusable ``401`` response — bearer token missing / invalid /
    revoked. Body shape matches ``_send_unauthorized`` in api_server.py
    (``{"error": "unauthorized"}``)."""
    return {
        "description": "Bearer token missing, malformed, or revoked.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {"error": "unauthorized"},
            },
        },
    }


def _response_not_found() -> dict:
    return {
        "description": (
            "Resource not in caller's scope, or unknown id. Per the no-"
            "info-leak contract, cross-user reads collapse to 404 rather "
            "than 403 so callers cannot enumerate other users' ids."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {"error": "not found"},
            },
        },
    }


def _response_bad_request(example_message: str = "bad request") -> dict:
    return {
        "description": "Request body or query string was malformed.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {"error": example_message},
            },
        },
    }


def _response_unsupported_media() -> dict:
    return {
        "description": (
            "Request had a non-empty body without "
            "``Content-Type: application/json``."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {"error": "content-type must be application/json"},
            },
        },
    }


def _response_rate_limited() -> dict:
    """``429`` carries both the JSON envelope AND the standard
    ``Retry-After`` header — we surface the header in the spec so SDK
    generators wire up a typed accessor for it."""
    return {
        "description": (
            "Per-user token-bucket rate limit exceeded. The "
            "``Retry-After`` header carries the wait in seconds; the "
            "body repeats the value as ``retry_after_seconds``."
        ),
        "headers": {
            "Retry-After": {
                "schema": {"type": "integer", "minimum": 1},
                "description": "Integer seconds before the next request will be admitted.",
            },
        },
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/RateLimitedResponse"},
                "example": {"error": "rate_limited", "retry_after_seconds": 1},
            },
        },
    }


def _response_internal_error() -> dict:
    return {
        "description": "Server-side failure. Body shape is always ``{\"error\": \"internal server error\"}``.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {"error": "internal server error"},
            },
        },
    }


def _standard_auth_responses(include_404: bool = False, include_400: bool = False) -> dict:
    """Wire up the responses that every authenticated endpoint produces.

    Every auth'd endpoint can emit 401 / 429 / 500. The body-bearing
    endpoints additionally may emit 400 (malformed body / query) and
    415 (wrong Content-Type). Per-id endpoints may emit 404 when the
    id doesn't exist in the caller's scope.
    """
    out = {
        "401": _response_unauthorized(),
        "429": _response_rate_limited(),
        "500": _response_internal_error(),
    }
    if include_400:
        out["400"] = _response_bad_request()
    if include_404:
        out["404"] = _response_not_found()
    return out


def _path_param(name: str, description: str) -> dict:
    """An OpenAPI path-parameter definition. Path params are always
    ``required=True`` (no optional path segments in OpenAPI 3)."""
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": description,
    }


def _query_int(name: str, description: str, default: int) -> dict:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "default": default},
        "description": description,
    }


def _query_string(name: str, description: str, default: str = "") -> dict:
    schema: dict = {"type": "string"}
    if default:
        schema["default"] = default
    return {
        "name": name,
        "in": "query",
        "required": False,
        "schema": schema,
        "description": description,
    }


def _query_bool(name: str, description: str) -> dict:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "schema": {"type": "boolean", "default": False},
        "description": description,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Component schemas — mirror the dataclass shapes in engine/* and auth/*.
#  Every field present in the dataclass is present here; types use the
#  OpenAPI scalar names (string/integer/number/boolean) plus ``nullable``
#  where the dataclass uses ``Optional[...]``.
# ─────────────────────────────────────────────────────────────────────────────

def _components_schemas() -> dict:
    """Reusable schema definitions referenced by ``$ref`` from operations.

    Each schema mirrors the dataclass in the engine module — see the
    docstring on each ``description`` for the source-of-truth class.
    """
    return {
        # Error envelopes -------------------------------------------------
        "ErrorResponse": {
            "type": "object",
            "description": "Standard error envelope. ``error`` is a short machine code; longer human-readable detail is in the operation summary.",
            "required": ["error"],
            "properties": {
                "error": {"type": "string", "example": "unauthorized"},
            },
        },
        "RateLimitedResponse": {
            "type": "object",
            "description": "429 body envelope. Same shape as ``ErrorResponse`` plus the wait-time hint.",
            "required": ["error", "retry_after_seconds"],
            "properties": {
                "error": {"type": "string", "example": "rate_limited"},
                "retry_after_seconds": {"type": "integer", "minimum": 1},
            },
        },
        # Health probe ----------------------------------------------------
        "HealthResponse": {
            "type": "object",
            "description": "Public health-probe payload. Same shape as ``worker.webhook_listener``'s ``/health`` so one LB template fits both.",
            "required": [
                "status", "schema_version", "users", "now_utc",
                "up_seconds", "unacked_critical_count", "current_outages",
            ],
            "properties": {
                "status": {"type": "string", "enum": ["ok", "degraded", "down"]},
                "schema_version": {"type": "integer"},
                "users": {"type": "integer"},
                "now_utc": {"type": "string", "format": "date-time"},
                "up_seconds": {"type": "number"},
                "unacked_critical_count": {"type": "integer"},
                "recent_render_success_rate": {"type": "number", "nullable": True},
                "current_outages": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        # Backtests health probe ------------------------------------------
        "BacktestValidator": {
            "type": "object",
            "description": "One backtest validator's snapshot, normalised across all 9 modules.",
            "required": ["name", "healthy", "headline_label",
                         "headline_value", "summary", "raw"],
            "properties": {
                "name":            {"type": "string"},
                "healthy":         {"type": "boolean"},
                "headline_label":  {"type": "string"},
                "headline_value":  {"type": "string"},
                "summary":         {"type": "string"},
                "raw": {
                    "type": "object",
                    "description": "Validator-specific structured fields (e.g. best_component, n_observations, MAE, spread_*) — shape varies per validator.",
                    "additionalProperties": True,
                },
            },
        },
        "BacktestsHealthResponse": {
            "type": "object",
            "description": "Public consolidated backtest-layer health report from ``tools.backtests``.",
            "required": [
                "status", "validators", "healthy_count", "total", "now_utc",
            ],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ok", "degraded", "down"],
                    "description": "Top-level rollup: ok=all healthy, degraded=any unhealthy, down=run failed.",
                },
                "now_utc":        {"type": "string", "format": "date-time"},
                "healthy_count":  {"type": "integer"},
                "total":          {"type": "integer"},
                "validators": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/BacktestValidator"},
                },
            },
        },
        # Port supply lines payload --------------------------------------
        "PortSupplyState": {
            "type": "object",
            "required": ["locode", "name", "region", "country_iso3",
                         "lat", "lon", "supply_deficit_days",
                         "utilization_pct", "severity_label", "container_type"],
            "properties": {
                "locode":              {"type": "string"},
                "name":                {"type": "string"},
                "region":              {"type": "string"},
                "country_iso3":        {"type": "string"},
                "lat":                 {"type": "number"},
                "lon":                 {"type": "number"},
                "supply_deficit_days": {"type": "number"},
                "utilization_pct":     {"type": "number"},
                "severity_label": {
                    "type": "string",
                    "enum": ["Critical Deficit", "Deficit", "Balanced",
                             "Surplus", "Heavy Surplus"],
                },
                "container_type":      {"type": "string"},
            },
        },
        "PortCompanyExposure": {
            "type": "object",
            "required": ["ticker", "exposure_weight",
                         "via_commodities", "via_routes"],
            "properties": {
                "ticker":          {"type": "string"},
                "exposure_weight": {"type": "number"},
                "via_commodities": {"type": "array",
                                    "items": {"type": "string"}},
                "via_routes":      {"type": "array",
                                    "items": {"type": "string"}},
            },
        },
        "PortSupplyChain": {
            "type": "object",
            "required": ["port", "exposed_companies", "routes_touching",
                         "top_commodities", "summary"],
            "properties": {
                "port": {"$ref": "#/components/schemas/PortSupplyState"},
                "exposed_companies": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/PortCompanyExposure"},
                },
                "routes_touching": {"type": "array",
                                    "items": {"type": "string"}},
                "top_commodities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["hs_category", "weight"],
                        "properties": {
                            "hs_category": {"type": "string"},
                            "weight":      {"type": "number"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
        },
        "PortSupplyLinesResponse": {
            "type": "object",
            "description": "Per-port container-supply state + exposed-company chains. "
                           "Mirrors the data the Port Supply Lines tab consumes.",
            "required": ["container_type", "total", "now_utc", "chains"],
            "properties": {
                "container_type": {"type": "string"},
                "total":          {"type": "integer"},
                "now_utc":        {"type": "string", "format": "date-time"},
                "chains": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/PortSupplyChain"},
                },
            },
        },
        # ShippingAlert (engine.alert_engine_v2) --------------------------
        "Alert": {
            "type": "object",
            "description": "One alert row. Mirrors ``engine.alert_engine_v2.ShippingAlert``.",
            "required": [
                "alert_id", "created_at", "alert_type", "severity",
                "title", "body", "ticker", "route_id", "port_locode",
                "value", "threshold", "change_pct", "acknowledged",
            ],
            "properties": {
                "alert_id": {"type": "string", "format": "uuid"},
                "created_at": {"type": "string", "format": "date-time"},
                "alert_type": {
                    "type": "string",
                    "description": "BDI_MOVE | SIGNAL_FIRE | CONGESTION | RATE_SURGE | STOCK_MOVE | MACRO | FLAP | …",
                },
                "severity": {
                    "type": "string",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                },
                "title": {"type": "string"},
                "body": {"type": "string", "description": "2-3 sentence human description."},
                "ticker": {"type": "string", "description": "Empty when the alert is not stock-related."},
                "route_id": {"type": "string", "description": "Empty when the alert is not freight-related."},
                "port_locode": {"type": "string", "description": "Empty when the alert is not port-related."},
                "value": {"type": "number"},
                "threshold": {"type": "number"},
                "change_pct": {"type": "number"},
                "acknowledged": {"type": "boolean"},
            },
        },
        # AlertRule (engine.alert_engine_v2) ------------------------------
        "AlertRule": {
            "type": "object",
            "description": "One alert rule. Mirrors ``engine.alert_engine_v2.AlertRule``.",
            "required": ["rule_id", "name", "alert_type", "enabled", "threshold", "severity"],
            "properties": {
                "rule_id": {"type": "string"},
                "name": {"type": "string"},
                "alert_type": {"type": "string"},
                "enabled": {"type": "boolean"},
                "threshold": {"type": "number"},
                "severity": {
                    "type": "string",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                },
                "target_channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Channel NAMES (not ids) for fan-out routing. Empty = match every eligible channel.",
                },
                "cooldown_minutes": {"type": "integer", "default": 0},
                "flap_window_minutes": {"type": "integer", "default": 30},
                "flap_threshold_crossings": {"type": "integer", "default": 5},
                "flap_detection_enabled": {"type": "boolean", "default": False},
            },
        },
        # DeliveryChannel (engine.alert_delivery) -------------------------
        "DeliveryChannel": {
            "type": "object",
            "description": (
                "One delivery channel. Mirrors ``engine.alert_delivery."
                "DeliveryChannel`` minus the ``target`` field — the API "
                "deliberately withholds ``target`` from list responses to "
                "avoid leaking the secret webhook URL / email."
            ),
            "required": ["channel_id", "name", "kind", "severity_threshold"],
            "properties": {
                "channel_id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["slack", "email", "sms", "webhook", "discord", "pagerduty"],
                },
                "target": {
                    "type": "string",
                    "description": "REQUIRED in POST request bodies (the webhook URL / email / phone). NEVER returned in GET responses.",
                },
                "severity_threshold": {
                    "type": "string",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                },
                "enabled": {"type": "boolean", "default": True},
                "created_at": {"type": "string", "format": "date-time"},
                "digest_mode": {
                    "type": "string",
                    "enum": ["immediate", "daily"],
                    "default": "immediate",
                },
                "quiet_start": {"type": "string", "description": "HH:MM UTC, empty = disabled."},
                "quiet_end": {"type": "string", "description": "HH:MM UTC, empty = disabled."},
                "quiet_override_critical": {"type": "boolean", "default": True},
                "monthly_budget": {
                    "type": "integer",
                    "default": 0,
                    "description": "0 = unlimited. Positive = suppress further deliveries once the per-calendar-month counter hits this cap.",
                },
            },
        },
        # ReportMeta (utils.report_history) -------------------------------
        "ReportMeta": {
            "type": "object",
            "description": "Saved-report metadata. Mirrors ``utils.report_history.ReportMeta``.",
            "required": [
                "report_id", "generated_at", "report_date",
                "sentiment_label", "sentiment_score", "risk_level",
                "signal_count", "data_quality", "file_size_kb",
            ],
            "properties": {
                "report_id": {"type": "string", "format": "uuid"},
                "generated_at": {"type": "string", "format": "date-time"},
                "report_date": {"type": "string"},
                "sentiment_label": {
                    "type": "string",
                    "enum": ["BULLISH", "BEARISH", "NEUTRAL", "MIXED"],
                },
                "sentiment_score": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "risk_level": {
                    "type": "string",
                    "enum": ["LOW", "MODERATE", "HIGH", "CRITICAL"],
                },
                "signal_count": {"type": "integer"},
                "data_quality": {
                    "type": "string",
                    "enum": ["FULL", "PARTIAL", "DEGRADED"],
                },
                "file_size_kb": {"type": "number"},
                "public_slug": {"type": "string", "default": ""},
                "public_expires_at": {"type": "string", "format": "date-time", "nullable": True},
            },
        },
        # AlertSilence (engine.alert_silences) ----------------------------
        "AlertSilence": {
            "type": "object",
            "description": "One silence row. Mirrors ``engine.alert_silences.AlertSilence``. NULL match-keys mean 'matches any value'.",
            "required": [
                "silence_id", "user_id", "starts_at", "expires_at",
                "created_at", "created_by_user_id",
            ],
            "properties": {
                "silence_id": {"type": "string", "format": "uuid"},
                "user_id": {"type": "string"},
                "rule_id": {"type": "string", "nullable": True},
                "ticker": {"type": "string", "nullable": True},
                "severity": {"type": "string", "nullable": True},
                "reason": {"type": "string", "nullable": True},
                "starts_at": {"type": "string", "format": "date-time"},
                "expires_at": {"type": "string", "format": "date-time"},
                "created_at": {"type": "string", "format": "date-time"},
                "created_by_user_id": {"type": "string"},
            },
        },
        # AlertAnnotation (engine.alert_annotations) ----------------------
        "AlertAnnotation": {
            "type": "object",
            "description": "One annotation. Mirrors ``engine.alert_annotations.AlertAnnotation``.",
            "required": [
                "annotation_id", "alert_id", "user_id", "author_user_id",
                "body", "created_at",
            ],
            "properties": {
                "annotation_id": {"type": "string", "format": "uuid"},
                "alert_id": {"type": "string", "format": "uuid"},
                "user_id": {"type": "string", "description": "Owner of the parent alert."},
                "author_user_id": {"type": "string", "description": "Who wrote this comment."},
                "body": {"type": "string", "maxLength": 4000},
                "created_at": {"type": "string", "format": "date-time"},
                "edited_at": {"type": "string", "format": "date-time", "nullable": True},
            },
        },
        # EscalationStep (engine.alert_escalation) ------------------------
        "EscalationStep": {
            "type": "object",
            "description": "One step of an escalation chain. Mirrors ``engine.alert_escalation.EscalationStep``.",
            "required": [
                "chain_id", "rule_id", "user_id", "step_number",
                "after_minutes", "channel_id", "created_at",
            ],
            "properties": {
                "chain_id": {"type": "string", "format": "uuid"},
                "rule_id": {"type": "string"},
                "user_id": {"type": "string"},
                "step_number": {"type": "integer", "minimum": 1, "description": "1-indexed."},
                "after_minutes": {"type": "integer", "minimum": 0},
                "channel_id": {"type": "string"},
                "created_at": {"type": "string", "format": "date-time"},
            },
        },
        # ReportSchedule (engine.report_scheduler) ------------------------
        "ReportSchedule": {
            "type": "object",
            "description": "One schedule row. Mirrors ``engine.report_scheduler.ReportSchedule``.",
            "required": ["schedule_id", "user_id", "name", "cron_expr", "enabled"],
            "properties": {
                "schedule_id": {"type": "string", "format": "uuid"},
                "user_id": {"type": "string"},
                "name": {"type": "string"},
                "cron_expr": {"type": "string", "description": "5-field cron string."},
                "enabled": {"type": "boolean", "default": True},
                "last_run_at": {"type": "string", "format": "date-time", "nullable": True},
                "last_run_status": {"type": "string", "nullable": True},
                "last_run_message": {"type": "string", "nullable": True},
                "next_run_at": {"type": "string", "format": "date-time", "nullable": True},
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time", "nullable": True},
            },
        },
        # NotificationPrefs (auth.notification_prefs) ---------------------
        "NotificationPrefs": {
            "type": "object",
            "description": "Per-user prefs. Mirrors ``auth.notification_prefs.NotificationPrefs``.",
            "required": ["user_id", "enabled", "min_severity"],
            "properties": {
                "user_id": {"type": "string"},
                "enabled": {"type": "boolean", "default": True},
                "min_severity": {
                    "type": "string",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    "default": "LOW",
                },
                "alert_type_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Global allow-list. Empty = no filter.",
                },
                "severity_channel_map": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "description": "Per-severity channel allow-list keyed by severity name.",
                },
                "quiet_during_hours": {
                    "type": "array",
                    "nullable": True,
                    "items": {"type": "integer", "minimum": 0, "maximum": 23},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "``[start_hour, end_hour]`` UTC or ``null`` to disable.",
                },
            },
        },
        # AuditEvent (auth.audit) -----------------------------------------
        "AuditEvent": {
            "type": "object",
            "description": "One audit-log row. Mirrors ``auth.audit.AuditEvent``.",
            "required": ["event_id", "created_at", "user_id", "action"],
            "properties": {
                "event_id": {"type": "string", "format": "uuid"},
                "created_at": {"type": "string", "format": "date-time"},
                "user_id": {"type": "string"},
                "action": {"type": "string"},
                "entity_type": {"type": "string"},
                "entity_id": {"type": "string"},
                "detail_json": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Free-form structured detail. Channel ``target`` is suppressed at recording time so secrets cannot leak via the audit log.",
                },
            },
        },
        # Generic envelope --------------------------------------------------
        "ItemListResponse": {
            "type": "object",
            "description": "Standard ``{items: [...], count: N}`` envelope used by audit/incidents/source-health.",
            "required": ["items", "count"],
            "properties": {
                "items": {"type": "array", "items": {}},
                "count": {"type": "integer"},
            },
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Paths — the bulk of the spec. One ``operationId`` per endpoint; the
#  ids match the python method names in api_server.py so a grep across
#  both files finds the wiring cleanly.
# ─────────────────────────────────────────────────────────────────────────────

def _paths() -> dict:
    """Build the full ``paths`` dict for the OpenAPI spec.

    One entry per concrete route in ``worker/api_server.py``. Each
    operation specifies summary/description/parameters/requestBody/
    responses/security/tags.
    """
    paths: dict[str, dict] = {}

    # ── /api/v1/openapi.json (public) ──────────────────────────────────
    paths["/api/v1/openapi.json"] = {
        "get": {
            "operationId": "getOpenApiSpec",
            "summary": "Return this OpenAPI specification.",
            "description": (
                "Public — no auth required. Returns the full OpenAPI 3.0 "
                "spec as JSON, suitable for feeding into Swagger UI, "
                "Redoc, or ``openapi-generator-cli``. The spec is built "
                "lazily on first request and cached in-process."
            ),
            "tags": ["Meta"],
            "security": [],
            "responses": {
                "200": {
                    "description": "Full OpenAPI 3.0 spec as JSON.",
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                        },
                    },
                },
            },
        },
    }

    # ── /api/v1/health (public) ────────────────────────────────────────
    paths["/api/v1/health"] = {
        "get": {
            "operationId": "getHealth",
            "summary": "Public liveness + system-health probe.",
            "description": (
                "Unauthenticated. Returns ``{status, schema_version, "
                "users, now_utc, up_seconds, unacked_critical_count, "
                "recent_render_success_rate, current_outages}``. "
                "``status`` is ``ok``, ``degraded`` (system up but "
                "indicators flag a problem — unacked criticals, render "
                "errors, source outages), or ``down`` (DB unreachable)."
            ),
            "tags": ["Health"],
            "security": [],
            "responses": {
                "200": {
                    "description": "System is up; status is ok or degraded.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/HealthResponse"},
                        },
                    },
                },
                "503": {
                    "description": "DB unreachable / count_users failed.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/HealthResponse"},
                            "example": {"status": "down", "error": "OperationalError: ..."},
                        },
                    },
                },
            },
        },
    }

    # ── /api/v1/backtests/health (public) ──────────────────────────────
    paths["/api/v1/backtests/health"] = {
        "get": {
            "operationId": "getBacktestsHealth",
            "summary": "Public consolidated backtest-layer health probe.",
            "description": (
                "Unauthenticated. Runs every validator in "
                "``tools.backtests`` and returns the consolidated JSON "
                "report. Status code follows the rollup: ``200`` when "
                "all 9 validators report healthy, ``503`` when any "
                "reports unhealthy. External monitoring (Datadog HTTP "
                "check, k8s liveness probe, Pingdom, status page) can "
                "alarm on the status code alone without parsing the body."
            ),
            "tags": ["Health"],
            "security": [],
            "responses": {
                "200": {
                    "description": "All 9 validators healthy. status=ok.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/BacktestsHealthResponse"},
                        },
                    },
                },
                "503": {
                    "description": (
                        "Either at least one validator reports unhealthy "
                        "(status=degraded), or the underlying tools.backtests "
                        "run itself failed (status=down + error field)."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/BacktestsHealthResponse"},
                            "example": {
                                "status": "degraded",
                                "healthy_count": 7,
                                "total": 9,
                                "now_utc": "2026-05-25T22:00:00+00:00",
                                "validators": [],
                            },
                        },
                    },
                },
            },
        },
    }

    # ── /api/v1/ports/supply-lines (authenticated) ─────────────────────
    paths["/api/v1/ports/supply-lines"] = {
        "get": {
            "operationId": "getPortSupplyLines",
            "summary": "Per-port supply state + exposed-company chains.",
            "description": (
                "Returns the same per-port chain data the **Port Supply "
                "Lines** tab consumes. Each chain ties a port's "
                "container-supply state to the publicly-traded shipping "
                "companies exposed to the supply lines flowing through "
                "it (port → routes → cargo mix → company commodity "
                "weights). Sorted most-stressed (largest deficit) first. "
                "Authenticated (per-user bearer token)."
            ),
            "tags": ["Ports"],
            "security": _bearer_security(),
            "parameters": [
                _query_string(
                    "container_type",
                    "Container-type slice of regional supply data. One of "
                    "40FT_DRY | 20FT_DRY | 40FT_HC | 40FT_REEFER | 20FT_TANK. "
                    "Default 40FT_DRY.",
                    default="40FT_DRY",
                ),
                _query_int(
                    "top_n",
                    "Cap on exposed_companies + top_commodities per port. "
                    "Range [1, 50]. Default 8.",
                    default=8,
                ),
            ],
            "responses": {
                "200": {
                    "description": "Per-port supply + exposure chains envelope.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/PortSupplyLinesResponse"},
                        },
                    },
                },
                **_standard_auth_responses(include_400=True),
                "503": {
                    "description": (
                        "Underlying port_supply_lines build raised an "
                        "unexpected error (extremely defensive — the "
                        "joiner tolerates empty inputs internally)."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                            "example": {"status": "down",
                                        "error": "ImportError: ..."},
                        },
                    },
                },
            },
        },
    }

    # ── /api/v1/ports/supply-lines.xlsx (authenticated) ────────────────
    paths["/api/v1/ports/supply-lines.xlsx"] = {
        "get": {
            "operationId": "getPortSupplyLinesXlsx",
            "summary": "Port supply state + exposures as an Excel workbook.",
            "description": (
                "Same data the JSON endpoint serves, bundled as a single "
                "``.xlsx`` workbook with six sheets: ``overview`` "
                "(snapshot metadata + per-sheet row counts) plus five "
                "data sheets that mirror the five CSV views (``summary``, "
                "``exposure``, ``footprint``, ``regional``, "
                "``watchlist``). Headers bold + frozen, numeric cells "
                "coerced from strings so ``SUM`` / sort / pivot work "
                "without conversion. Authenticated (per-user bearer)."
            ),
            "tags": ["Ports"],
            "security": _bearer_security(),
            "parameters": [
                _query_string(
                    "container_type",
                    "Container-type slice. One of 40FT_DRY (default) | "
                    "20FT_DRY | 40FT_HC | 40FT_REEFER | 20FT_TANK.",
                    default="40FT_DRY",
                ),
                _query_string(
                    "threshold_days",
                    "Deficit-watchlist firing threshold (numeric string; "
                    "default -3.0). Only affects the watchlist sheet.",
                    default="-3.0",
                ),
            ],
            "responses": {
                "200": {
                    "description": "The .xlsx workbook bytes.",
                    "content": {
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet": {
                            "schema": {"type": "string", "format": "binary"},
                        },
                    },
                    "headers": {
                        "Content-Disposition": {
                            "description": (
                                "``attachment; filename=...`` with the "
                                "canonical CLI stamp pattern: "
                                "``port_supply_lines_workbook"
                                "_<container>_<YYYYMMDD>.xlsx``"
                            ),
                            "schema": {"type": "string"},
                        },
                    },
                },
                **_standard_auth_responses(include_400=True),
                "503": {
                    "description": (
                        "Underlying port_supply_lines or openpyxl build "
                        "raised — extremely defensive, the joiner "
                        "tolerates empty inputs internally."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                            "example": {"status": "down",
                                        "error": "ImportError: ..."},
                        },
                    },
                },
            },
        },
    }

    # ── /api/v1/alerts ─────────────────────────────────────────────────
    paths["/api/v1/alerts"] = {
        "get": {
            "operationId": "listAlerts",
            "summary": "List the caller's recent alerts.",
            "description": (
                "Returns up to 500 alerts created in the last "
                "``window_days`` (default 30), optionally filtered by "
                "severity. Filtering is applied AFTER the engine load so "
                "asking for ``severity=CRITICAL`` with a 365-day window "
                "doesn't silently drop criticals at the cap."
            ),
            "tags": ["Alerts"],
            "security": _bearer_security(),
            "parameters": [
                _query_int("window_days", "Look-back window in days.", 30),
                _query_string("severity", "Filter to one severity (case-insensitive)."),
            ],
            "responses": {
                "200": {
                    "description": "Array of alerts (newest first).",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Alert"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/alerts/{alert_id} ──────────────────────────────────────
    paths["/api/v1/alerts/{alert_id}"] = {
        "get": {
            "operationId": "getAlert",
            "summary": "Fetch one alert by id, scoped to the caller.",
            "description": (
                "Returns 404 when the id is not in the caller's scope — "
                "cross-user reads collapse to 404 (no info leak)."
            ),
            "tags": ["Alerts"],
            "security": _bearer_security(),
            "parameters": [_path_param("alert_id", "Alert UUID.")],
            "responses": {
                "200": {
                    "description": "One alert.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Alert"},
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/alerts/{alert_id}/ack ──────────────────────────────────
    paths["/api/v1/alerts/{alert_id}/ack"] = {
        "post": {
            "operationId": "ackAlert",
            "summary": "Acknowledge one alert.",
            "description": (
                "Idempotent. Cross-user attempts silently no-op (zero "
                "rows updated). Response is always ``{acknowledged: "
                "true, alert_id}`` so a caller cannot probe for the "
                "existence of other users' alerts by status code."
            ),
            "tags": ["Alerts"],
            "security": _bearer_security(),
            "parameters": [_path_param("alert_id", "Alert UUID.")],
            "responses": {
                "200": {
                    "description": "Ack accepted (may have been a no-op cross-user).",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["acknowledged", "alert_id"],
                                "properties": {
                                    "acknowledged": {"type": "boolean"},
                                    "alert_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/alerts/{alert_id}/annotations ──────────────────────────
    paths["/api/v1/alerts/{alert_id}/annotations"] = {
        "get": {
            "operationId": "listAnnotations",
            "summary": "List the annotation thread for one alert.",
            "description": "Created-ascending. Per-user scoped — cross-user alert ids return an empty list.",
            "tags": ["Annotations"],
            "security": _bearer_security(),
            "parameters": [_path_param("alert_id", "Alert UUID.")],
            "responses": {
                "200": {
                    "description": "Array of annotations (empty when none).",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/AlertAnnotation"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "createAnnotation",
            "summary": "Add one annotation to an alert.",
            "description": (
                "``body`` is the only required field. Silently truncated "
                "at 4000 chars; whitespace-only bodies → 400."
            ),
            "tags": ["Annotations"],
            "security": _bearer_security(),
            "parameters": [_path_param("alert_id", "Alert UUID.")],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["body"],
                            "properties": {
                                "body": {"type": "string", "maxLength": 4000},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Annotation saved.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "annotation_id", "annotation"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "annotation_id": {"type": "string"},
                                    "annotation": {"$ref": "#/components/schemas/AlertAnnotation"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/annotations/{annotation_id} ────────────────────────────
    paths["/api/v1/annotations/{annotation_id}"] = {
        "patch": {
            "operationId": "editAnnotation",
            "summary": "Replace the body of one annotation.",
            "description": "Author-only. Cross-author / cross-user attempts → 404. The body itself is never logged.",
            "tags": ["Annotations"],
            "security": _bearer_security(),
            "parameters": [_path_param("annotation_id", "Annotation UUID.")],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["body"],
                            "properties": {"body": {"type": "string", "maxLength": 4000}},
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Annotation updated.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["updated", "annotation_id"],
                                "properties": {
                                    "updated": {"type": "boolean"},
                                    "annotation_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_404=True, include_400=True),
            },
        },
        "delete": {
            "operationId": "deleteAnnotation",
            "summary": "Delete one annotation.",
            "description": "Author-only. Cross-author / cross-user attempts → 404.",
            "tags": ["Annotations"],
            "security": _bearer_security(),
            "parameters": [_path_param("annotation_id", "Annotation UUID.")],
            "responses": {
                "200": {
                    "description": "Annotation removed.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted", "annotation_id"],
                                "properties": {
                                    "deleted": {"type": "boolean"},
                                    "annotation_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/reports ────────────────────────────────────────────────
    paths["/api/v1/reports"] = {
        "get": {
            "operationId": "listReports",
            "summary": "List the caller's saved-report metadata.",
            "description": "Sorted newest-first. ``limit`` truncates after sorting.",
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [_query_int("limit", "Cap on rows returned.", 50)],
            "responses": {
                "200": {
                    "description": "Array of report metadata.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/ReportMeta"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/reports/diff ───────────────────────────────────────────
    # Defined BEFORE the /reports/{report_id}/... paths to mirror the
    # dispatch ordering in worker/api_server.py (the literal `diff`
    # route is matched first).
    paths["/api/v1/reports/diff"] = {
        "get": {
            "operationId": "diffReports",
            "summary": "Compare two reports and return a structured diff.",
            "description": (
                "Returns the per-entry diff between two report payloads "
                "in the caller's scope. Categories: ``signal`` / ``route`` "
                "/ ``sentiment`` / ``risk`` / ``metadata``. Change types: "
                "``added`` / ``removed`` / ``changed``. Signal direction "
                "flips and risk-level changes always surface; signal "
                "confidence deltas surface above 0.10; route value deltas "
                "surface above 5%; sentiment-score deltas surface above "
                "0.05. 400 when ``from`` or ``to`` is missing; 404 when "
                "either id is unknown in the caller's scope (no info leak "
                "vs another user's id)."
            ),
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [
                {
                    "name": "from",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "report_id of the older / baseline report.",
                },
                {
                    "name": "to",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "report_id of the newer / current report.",
                },
            ],
            "responses": {
                "200": {
                    "description": "Structured diff payload.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": [
                                    "report_a_id", "report_b_id",
                                    "summary", "entries",
                                ],
                                "properties": {
                                    "report_a_id": {"type": "string"},
                                    "report_b_id": {"type": "string"},
                                    "summary": {
                                        "type": "object",
                                        "properties": {
                                            "added": {"type": "integer"},
                                            "removed": {"type": "integer"},
                                            "changed": {"type": "integer"},
                                        },
                                    },
                                    "entries": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": [
                                                "category", "change_type",
                                                "key", "description",
                                            ],
                                            "properties": {
                                                "category": {
                                                    "type": "string",
                                                    "enum": [
                                                        "signal", "route",
                                                        "sentiment", "risk",
                                                        "metadata",
                                                    ],
                                                },
                                                "change_type": {
                                                    "type": "string",
                                                    "enum": [
                                                        "added", "removed",
                                                        "changed",
                                                    ],
                                                },
                                                "key": {"type": "string"},
                                                "before": {},
                                                "after": {},
                                                "description": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "400": {
                    "description": "Missing ``from`` or ``to`` query parameter.",
                },
                "404": {
                    "description": "One or both report_ids unknown in the caller's scope.",
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/reports/{report_id}/html ───────────────────────────────
    paths["/api/v1/reports/{report_id}/html"] = {
        "get": {
            "operationId": "getReportHtml",
            "summary": "Fetch the rendered HTML for one saved report.",
            "description": (
                "Returns ``text/html``. When the report is password-"
                "protected (``public_password_hash`` is set), the caller "
                "must additionally supply the password via "
                "``X-Report-Password`` header or ``?password=`` query "
                "string — missing → 401, wrong → 401."
            ),
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [
                _path_param("report_id", "Report UUID."),
                _query_string("password", "Required only when the report is password-protected."),
                {
                    "name": "X-Report-Password",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "Alternate location for the report password (preferred over query string).",
                },
            ],
            "responses": {
                "200": {
                    "description": "The report's rendered HTML body.",
                    "content": {"text/html": {"schema": {"type": "string"}}},
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/reports/{report_id}/markdown ───────────────────────────
    paths["/api/v1/reports/{report_id}/markdown"] = {
        "get": {
            "operationId": "getReportMarkdown",
            "summary": "Fetch a Markdown rendering of one saved report.",
            "description": (
                "Returns ``text/markdown``. Built at request time from "
                "the structured ReportMeta fields (sentiment, risk, "
                "data-quality) — the on-disk HTML body is NOT embedded "
                "(it would render as an unreadable code block). Same "
                "password contract as the HTML endpoint."
            ),
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [
                _path_param("report_id", "Report UUID."),
                _query_string("password", "Required only when the report is password-protected."),
            ],
            "responses": {
                "200": {
                    "description": "Markdown source.",
                    "content": {"text/markdown": {"schema": {"type": "string"}}},
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/reports/{report_id}/public ─────────────────────────────
    paths["/api/v1/reports/{report_id}/public"] = {
        "post": {
            "operationId": "makeReportPublic",
            "summary": "Generate a public-share slug for one of the caller's reports.",
            "description": (
                "Body is optional — when omitted, defaults to 30 days "
                "and no password. ``expires_in_days`` is an integer; "
                "``password`` is an optional string (when non-empty the "
                "share link will require it to view)."
            ),
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [_path_param("report_id", "Report UUID.")],
            "requestBody": {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "expires_in_days": {"type": "integer", "default": 30},
                                "password": {"type": "string", "nullable": True},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Slug minted.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["slug"],
                                "properties": {"slug": {"type": "string"}},
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_404=True, include_400=True),
            },
        },
        "delete": {
            "operationId": "revokeReportPublic",
            "summary": "Revoke a report's public-share slug.",
            "description": "404 when the report is not in the caller's scope.",
            "tags": ["Reports"],
            "security": _bearer_security(),
            "parameters": [_path_param("report_id", "Report UUID.")],
            "responses": {
                "200": {
                    "description": "Slug revoked.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["revoked"],
                                "properties": {"revoked": {"type": "boolean"}},
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/telemetry/llm ──────────────────────────────────────────
    paths["/api/v1/telemetry/llm"] = {
        "get": {
            "operationId": "getLlmTelemetry",
            "summary": "Return LLM-call usage summary for the caller's window.",
            "description": "Counters + cost summary scoped to the caller's user_id.",
            "tags": ["Telemetry"],
            "security": _bearer_security(),
            "parameters": [_query_int("window_days", "Look-back window in days.", 7)],
            "responses": {
                "200": {
                    "description": "Usage summary object.",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/telemetry/perf ─────────────────────────────────────────
    paths["/api/v1/telemetry/perf"] = {
        "get": {
            "operationId": "getPerfTelemetry",
            "summary": "Render-performance telemetry summary.",
            "description": "NOT user-scoped — process-wide render counters. Still gated by bearer auth.",
            "tags": ["Telemetry"],
            "security": _bearer_security(),
            "parameters": [_query_int("window_hours", "Look-back window in hours.", 24)],
            "responses": {
                "200": {
                    "description": "Performance summary object.",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/audit ──────────────────────────────────────────────────
    paths["/api/v1/audit"] = {
        "get": {
            "operationId": "listAudit",
            "summary": "List the caller's audit-log rows.",
            "description": (
                "Per-user scoped. ``limit`` is clamped to "
                "[1, 1000]; ``action`` is forwarded into the SQL filter "
                "so the LIMIT applies post-filter."
            ),
            "tags": ["Audit"],
            "security": _bearer_security(),
            "parameters": [
                _query_int("limit", "Cap on rows returned (max 1000).", 100),
                _query_string("action", "Filter on the action verb (e.g. ``login_success``)."),
            ],
            "responses": {
                "200": {
                    "description": "Envelope with items array + total count.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["items", "count"],
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/AuditEvent"},
                                    },
                                    "count": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/audit/export ───────────────────────────────────────────
    paths["/api/v1/audit/export"] = {
        "get": {
            "operationId": "exportAudit",
            "summary": "SIEM-friendly export of the caller's audit-log rows.",
            "description": (
                "Same query knobs as ``/audit`` plus ``since`` / "
                "``until`` ISO-8601 filters and a ``format`` switch — "
                "``jsonl`` (default; ``application/x-ndjson``) for "
                "Splunk / Vector / Loki ingestion, ``json`` for the "
                "regular envelope. Large bodies (>100KB) stream via "
                "chunked Transfer-Encoding."
            ),
            "tags": ["Audit"],
            "security": _bearer_security(),
            "parameters": [
                _query_int("limit", "Cap on rows returned (max 1000).", 100),
                _query_string("action", "Filter on the action verb."),
                _query_string("since", "Inclusive lower-bound ISO-8601 timestamp."),
                _query_string("until", "Exclusive upper-bound ISO-8601 timestamp."),
                _query_string("format", "Output format: jsonl (default) or json.", "jsonl"),
            ],
            "responses": {
                "200": {
                    "description": (
                        "Export payload. Content-type is "
                        "``application/x-ndjson`` for jsonl (one JSON "
                        "object per line) or ``application/json`` for "
                        "the envelope."
                    ),
                    "content": {
                        "application/x-ndjson": {
                            "schema": {"type": "string"},
                        },
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ItemListResponse"},
                        },
                    },
                },
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/incidents ──────────────────────────────────────────────
    paths["/api/v1/incidents"] = {
        "get": {
            "operationId": "listIncidents",
            "summary": "Return correlated alert-incidents for the caller's window.",
            "description": "Per-user scoped. ``window`` is in days (default 7).",
            "tags": ["Incidents"],
            "security": _bearer_security(),
            "parameters": [_query_int("window", "Look-back window in days.", 7)],
            "responses": {
                "200": {
                    "description": "Envelope with incidents array + count.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ItemListResponse"},
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/source-health ──────────────────────────────────────────
    paths["/api/v1/source-health"] = {
        "get": {
            "operationId": "getSourceHealth",
            "summary": "Per-source feed liveness telemetry.",
            "description": (
                "NOT user-scoped — global platform telemetry. Still "
                "gated by bearer auth. ``window_hours`` is the look-back."
            ),
            "tags": ["Telemetry"],
            "security": _bearer_security(),
            "parameters": [_query_int("window_hours", "Look-back window in hours.", 24)],
            "responses": {
                "200": {
                    "description": "Envelope: items + count + window_hours + total_pings + current_outages.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["items", "count", "window_hours", "total_pings", "current_outages"],
                                "properties": {
                                    "items": {"type": "array", "items": {"type": "object"}},
                                    "count": {"type": "integer"},
                                    "window_hours": {"type": "integer"},
                                    "total_pings": {"type": "integer"},
                                    "current_outages": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/rules ──────────────────────────────────────────────────
    paths["/api/v1/rules"] = {
        "get": {
            "operationId": "listRules",
            "summary": "List the caller's alert rules.",
            "description": "Empty list when the user has no rules.",
            "tags": ["Rules"],
            "security": _bearer_security(),
            "responses": {
                "200": {
                    "description": "Array of rules.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/AlertRule"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "saveRules",
            "summary": "Replace the caller's rule set with the posted array.",
            "description": "Body must be a JSON array of rule dicts. 415 on non-JSON Content-Type; 400 on malformed JSON.",
            "tags": ["Rules"],
            "security": _bearer_security(),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/AlertRule"},
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Rule set saved.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "count"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "count": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
        "delete": {
            "operationId": "resetRules",
            "summary": "Wipe the caller's rule set.",
            "description": "Per-user — does NOT touch other users' rules.",
            "tags": ["Rules"],
            "security": _bearer_security(),
            "responses": {
                "200": {
                    "description": "Rules wiped.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["reset"],
                                "properties": {"reset": {"type": "boolean"}},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/rules/{rule_id}/escalations ────────────────────────────
    paths["/api/v1/rules/{rule_id}/escalations"] = {
        "get": {
            "operationId": "listEscalations",
            "summary": "List the escalation chain for one rule.",
            "description": "Ordered step_number ASC. Empty / unknown chain returns ``[]`` (200), not 404.",
            "tags": ["Escalations"],
            "security": _bearer_security(),
            "parameters": [_path_param("rule_id", "Rule id.")],
            "responses": {
                "200": {
                    "description": "Array of escalation steps.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/EscalationStep"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "createEscalationStep",
            "summary": "Add (or replace) one step in a rule's chain.",
            "description": (
                "``(rule_id, user_id, step_number)`` is UNIQUE — posting "
                "a step_number that already exists REPLACES the row. "
                "``channel_id`` must exist in the caller's channel set "
                "or → 400."
            ),
            "tags": ["Escalations"],
            "security": _bearer_security(),
            "parameters": [_path_param("rule_id", "Rule id.")],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["step_number", "after_minutes", "channel_id"],
                            "properties": {
                                "step_number": {"type": "integer", "minimum": 1},
                                "after_minutes": {"type": "integer", "minimum": 0},
                                "channel_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Step saved.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "chain_id", "step"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "chain_id": {"type": "string"},
                                    "step": {"$ref": "#/components/schemas/EscalationStep"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
        "delete": {
            "operationId": "deleteChain",
            "summary": "Bulk-clear every step in a rule's chain.",
            "description": "Idempotent — returns ``{deleted_steps: N}`` (N may be 0).",
            "tags": ["Escalations"],
            "security": _bearer_security(),
            "parameters": [_path_param("rule_id", "Rule id.")],
            "responses": {
                "200": {
                    "description": "Chain wiped; count of removed steps.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted_steps", "rule_id"],
                                "properties": {
                                    "deleted_steps": {"type": "integer"},
                                    "rule_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/escalations/{chain_id} ─────────────────────────────────
    paths["/api/v1/escalations/{chain_id}"] = {
        "delete": {
            "operationId": "deleteEscalationStep",
            "summary": "Delete one step from a chain by chain_id.",
            "description": "Per-user scoped — cross-user attempts → 404.",
            "tags": ["Escalations"],
            "security": _bearer_security(),
            "parameters": [_path_param("chain_id", "Chain step UUID.")],
            "responses": {
                "200": {
                    "description": "Step removed.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted", "chain_id"],
                                "properties": {
                                    "deleted": {"type": "boolean"},
                                    "chain_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/channels ───────────────────────────────────────────────
    paths["/api/v1/channels"] = {
        "get": {
            "operationId": "listChannels",
            "summary": "List the caller's delivery channels.",
            "description": "``target`` field is NEVER returned (it's the secret webhook URL / email).",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "responses": {
                "200": {
                    "description": "Array of channels.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/DeliveryChannel"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "saveChannel",
            "summary": "Insert / upsert a delivery channel.",
            "description": "``channel_id`` and ``target`` are required; everything else falls back to ``DeliveryChannel`` defaults.",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/DeliveryChannel"},
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Channel saved.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "channel_id"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "channel_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/channels/{channel_id} ──────────────────────────────────
    paths["/api/v1/channels/{channel_id}"] = {
        "patch": {
            "operationId": "patchChannel",
            "summary": "Partial update on one channel.",
            "description": "Currently exposes ``monthly_budget`` only. Unknown fields silently ignored.",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "parameters": [_path_param("channel_id", "Channel id.")],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "monthly_budget": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Channel updated.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["channel_id", "updated"],
                                "properties": {
                                    "channel_id": {"type": "string"},
                                    "updated": {"type": "object"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_404=True, include_400=True),
            },
        },
        "delete": {
            "operationId": "deleteChannel",
            "summary": "Delete one channel by id.",
            "description": "Cross-user deletes silently no-op (engine scope filter excludes the row). Status 200 either way.",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "parameters": [_path_param("channel_id", "Channel id.")],
            "responses": {
                "200": {
                    "description": "Delete request accepted.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted", "channel_id"],
                                "properties": {
                                    "deleted": {"type": "boolean"},
                                    "channel_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
    }

    # ── /api/v1/channels/{channel_id}/usage ────────────────────────────
    paths["/api/v1/channels/{channel_id}/usage"] = {
        "get": {
            "operationId": "getChannelUsage",
            "summary": "Per-channel monthly delivery counter.",
            "description": "Returns ``{channel_id, name, kind, budget, usage, pct, over_budget}``. 404 on cross-user / unknown id.",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "parameters": [_path_param("channel_id", "Channel id.")],
            "responses": {
                "200": {
                    "description": "Usage object.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["channel_id", "name", "kind", "budget", "usage", "over_budget"],
                                "properties": {
                                    "channel_id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "kind": {"type": "string"},
                                    "budget": {"type": "integer"},
                                    "usage": {"type": "integer"},
                                    "pct": {"type": "number", "nullable": True},
                                    "over_budget": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/channels/{channel_id}/reset-usage ──────────────────────
    paths["/api/v1/channels/{channel_id}/reset-usage"] = {
        "post": {
            "operationId": "resetChannelUsage",
            "summary": "Zero the current month's counter for one channel.",
            "description": "404 on cross-user / unknown id.",
            "tags": ["Channels"],
            "security": _bearer_security(),
            "parameters": [_path_param("channel_id", "Channel id.")],
            "responses": {
                "200": {
                    "description": "Counter reset.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["channel_id", "reset"],
                                "properties": {
                                    "channel_id": {"type": "string"},
                                    "reset": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/schedules ──────────────────────────────────────────────
    paths["/api/v1/schedules"] = {
        "get": {
            "operationId": "listSchedules",
            "summary": "List the caller's recurring report schedules.",
            "description": "Empty list when the user has no schedules.",
            "tags": ["Schedules"],
            "security": _bearer_security(),
            "responses": {
                "200": {
                    "description": "Array of schedules.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/ReportSchedule"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "createSchedule",
            "summary": "Create one recurring report schedule.",
            "description": "``name`` and ``cron_expr`` are required. ``enabled`` defaults to ``true``. Invalid cron → 400.",
            "tags": ["Schedules"],
            "security": _bearer_security(),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["name", "cron_expr"],
                            "properties": {
                                "name": {"type": "string"},
                                "cron_expr": {"type": "string", "description": "5-field cron string."},
                                "enabled": {"type": "boolean", "default": True},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Schedule created.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "schedule_id"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "schedule_id": {"type": "string"},
                                    "schedule": {"$ref": "#/components/schemas/ReportSchedule"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/schedules/{schedule_id} ────────────────────────────────
    paths["/api/v1/schedules/{schedule_id}"] = {
        "patch": {
            "operationId": "patchSchedule",
            "summary": "Update one schedule (partial).",
            "description": "Only the supplied fields move. Invalid cron → 400. Unknown / cross-user id → 404.",
            "tags": ["Schedules"],
            "security": _bearer_security(),
            "parameters": [_path_param("schedule_id", "Schedule UUID.")],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "cron_expr": {"type": "string"},
                                "enabled": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Schedule updated.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["updated", "schedule_id"],
                                "properties": {
                                    "updated": {"type": "boolean"},
                                    "schedule_id": {"type": "string"},
                                    "schedule": {"$ref": "#/components/schemas/ReportSchedule"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_404=True, include_400=True),
            },
        },
        "delete": {
            "operationId": "deleteSchedule",
            "summary": "Delete one schedule.",
            "description": "404 on cross-user / unknown id.",
            "tags": ["Schedules"],
            "security": _bearer_security(),
            "parameters": [_path_param("schedule_id", "Schedule UUID.")],
            "responses": {
                "200": {
                    "description": "Schedule removed.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted", "schedule_id"],
                                "properties": {
                                    "deleted": {"type": "boolean"},
                                    "schedule_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/silences ───────────────────────────────────────────────
    paths["/api/v1/silences"] = {
        "get": {
            "operationId": "listSilences",
            "summary": "List the caller's silences.",
            "description": "Active-only by default. Pass ``include_expired=true`` to surface the audit-retention tail.",
            "tags": ["Silences"],
            "security": _bearer_security(),
            "parameters": [
                _query_bool("include_expired", "Include expired silences awaiting cleanup."),
            ],
            "responses": {
                "200": {
                    "description": "Array of silences.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/AlertSilence"},
                            },
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "post": {
            "operationId": "createSilence",
            "summary": "Create one silence.",
            "description": (
                "``duration_minutes`` is required. ``rule_id`` / "
                "``ticker`` / ``severity`` are optional match keys — "
                "omit to match any value. ``reason`` is an operator note."
            ),
            "tags": ["Silences"],
            "security": _bearer_security(),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["duration_minutes"],
                            "properties": {
                                "duration_minutes": {"type": "integer", "minimum": 1},
                                "rule_id": {"type": "string", "nullable": True},
                                "ticker": {"type": "string", "nullable": True},
                                "severity": {"type": "string", "nullable": True},
                                "reason": {"type": "string", "nullable": True},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Silence created.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["saved", "silence_id", "silence"],
                                "properties": {
                                    "saved": {"type": "boolean"},
                                    "silence_id": {"type": "string"},
                                    "silence": {"$ref": "#/components/schemas/AlertSilence"},
                                },
                            },
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/silences/{silence_id} ──────────────────────────────────
    paths["/api/v1/silences/{silence_id}"] = {
        "delete": {
            "operationId": "deleteSilence",
            "summary": "Cancel one silence early.",
            "description": "Per-user scoped. Cross-user attempts → 404.",
            "tags": ["Silences"],
            "security": _bearer_security(),
            "parameters": [_path_param("silence_id", "Silence UUID.")],
            "responses": {
                "200": {
                    "description": "Silence removed.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["deleted", "silence_id"],
                                "properties": {
                                    "deleted": {"type": "boolean"},
                                    "silence_id": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_404=True),
            },
        },
    }

    # ── /api/v1/notification-prefs ─────────────────────────────────────
    paths["/api/v1/notification-prefs"] = {
        "get": {
            "operationId": "getNotificationPrefs",
            "summary": "Return the caller's notification prefs.",
            "description": "A user with no saved prefs gets the defaults — the response is identical to 'saved-as-defaults', no leak.",
            "tags": ["NotificationPrefs"],
            "security": _bearer_security(),
            "responses": {
                "200": {
                    "description": "Notification prefs object.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/NotificationPrefs"},
                        },
                    },
                },
                **_standard_auth_responses(),
            },
        },
        "patch": {
            "operationId": "patchNotificationPrefs",
            "summary": "Partial-update the caller's notification prefs.",
            "description": (
                "Body is a JSON object whose keys are any subset of the "
                "NotificationPrefs field names. Unknown keys are silently "
                "ignored. ``quiet_during_hours`` accepts ``null`` or a "
                "two-element ``[start_hour, end_hour]`` list."
            ),
            "tags": ["NotificationPrefs"],
            "security": _bearer_security(),
            "requestBody": {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/NotificationPrefs"},
                    },
                },
            },
            "responses": {
                "200": {
                    "description": "Full updated prefs object (post-patch).",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/NotificationPrefs"},
                        },
                    },
                },
                "415": _response_unsupported_media(),
                **_standard_auth_responses(include_400=True),
            },
        },
    }

    # ── /api/v1/ports/spillover-graph (authenticated) ──────────────────
    paths["/api/v1/ports/spillover-graph"] = {
        "get": {
            "operationId": "getPortSpilloverGraph",
            "summary": "Port-to-port deficit-contagion (spillover) graph.",
            "description": (
                "Walks the recent daily port-supply snapshots and tallies "
                "lead-lag co-occurrence: when port A enters container deficit, "
                "which ports B follow within the lookahead window? Returns the "
                "directed spillover edges (support = P(B follows | A) in [0, 1]; "
                "lift = support / B's unconditional base rate, > 1 = more than "
                "chance), sorted by lift descending. Authenticated (per-user "
                "bearer token); reads shared snapshot history (not per-user)."
            ),
            "tags": ["Ports"],
            "security": _bearer_security(),
            "parameters": [
                _query_string(
                    "container_type",
                    "Container-type slice. One of 40FT_DRY | 20FT_DRY | "
                    "40FT_HC | 40FT_REEFER | 20FT_TANK. Default 40FT_DRY.",
                    default="40FT_DRY",
                ),
                _query_int(
                    "window_days",
                    "Trailing window of snapshot days to walk. Range [2, 365]. "
                    "Default 60.",
                    default=60,
                ),
                _query_int(
                    "lag_within_days",
                    "Lookahead: B must enter deficit within this many days "
                    "after A. Range [1, 14]. Default 3.",
                    default=3,
                ),
                _query_int(
                    "min_co",
                    "Minimum co-occurrence count for an edge to survive "
                    "(filters single coincidences). Range [1, 100]. Default 2.",
                    default=2,
                ),
                {
                    "name": "min_lift",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "number", "default": 1.0, "minimum": 0.0},
                    "description": (
                        "Minimum lift for an edge to survive. Must be a finite "
                        "number >= 0 (NaN / inf are rejected with 400). "
                        "Default 1.0."
                    ),
                },
            ],
            "responses": {
                "200": {
                    "description": (
                        "Spillover-graph envelope: the echoed query params plus "
                        "summary counts and the directed edge list."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "container_type": {"type": "string"},
                                    "window_days": {"type": "integer"},
                                    "lag_within_days": {"type": "integer"},
                                    "min_co": {"type": "integer"},
                                    "min_lift": {"type": "number"},
                                    "n_days_examined": {"type": "integer"},
                                    "n_unique_sources": {"type": "integer"},
                                    "n_unique_targets": {"type": "integer"},
                                    "total_edges": {"type": "integer"},
                                    "edges": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "source_locode": {"type": "string"},
                                                "target_locode": {"type": "string"},
                                                "co_occurrence_count": {"type": "integer"},
                                                "source_event_count": {"type": "integer"},
                                                "support": {"type": "number"},
                                                "target_base_rate": {"type": "number"},
                                                "lift": {"type": "number"},
                                                "lag_within_days": {"type": "integer"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                **_standard_auth_responses(include_400=True),
                "503": {
                    "description": (
                        "Snapshot-history walk raised an unexpected error "
                        "(defensive — the builder tolerates empty inputs)."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                            "example": {"status": "down",
                                        "error": "ImportError: ..."},
                        },
                    },
                },
            },
        },
    }

    return paths


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_openapi_spec(
    *,
    base_url: str = "http://localhost:8503",
    version: str = "1.0.0",
    title: str = "Ship Tracker API",
) -> dict[str, Any]:
    """Build the complete OpenAPI 3.0 spec as a Python dict.

    Args:
        base_url: Base URL for the ``servers`` field — typically
            ``http://localhost:8503`` in dev or the public-facing URL
            in prod. The path segments in every operation are absolute
            (begin with ``/api/v1/``) so adjusting ``base_url`` does
            not require touching the per-operation paths.
        version: The version string published in ``info.version``.
            Bump this when the wire shape of the API changes
            incompatibly. The codebase has no enforced contract between
            this value and any other version string — it's a free
            field for the operator to roll.
        title: Human-readable title shown in Swagger UI / Redoc.

    Returns:
        A fresh dict — callers can mutate it freely without affecting
        future calls. Top-level keys: ``openapi``, ``info``,
        ``servers``, ``paths``, ``components``, ``tags``,
        ``security``.
    """
    return {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": version,
            "description": (
                "Read + scoped-write surface for Ship Tracker. Bearer-"
                "token authenticated (one per user via "
                "``tools.ops_cli tokens create``). Every authenticated "
                "endpoint is per-user scoped — Alice cannot read or "
                "mutate Bob's data even by guessing ids. Cross-user "
                "reads collapse to 404; cross-user writes silently "
                "no-op (engine SQL scope filter excludes the row)."
            ),
            "contact": {
                "name": "Ship Tracker maintainers",
                "url": "https://github.com/anthropics/ship-tracker",
            },
            "license": {
                "name": "MIT",
            },
        },
        "servers": [
            {
                "url": base_url,
                "description": "Default server URL (override at deploy).",
            },
        ],
        "tags": [
            {"name": "Health", "description": "Public liveness probe."},
            {"name": "Meta", "description": "Spec discovery (this OpenAPI document)."},
            {"name": "Alerts", "description": "Alert reads + ack."},
            {"name": "Annotations", "description": "Per-alert annotation threads."},
            {"name": "Reports", "description": "Saved report metadata + content."},
            {"name": "Rules", "description": "User alert rule set CRUD."},
            {"name": "Channels", "description": "Delivery channels + per-channel budget."},
            {"name": "Schedules", "description": "Recurring report schedules."},
            {"name": "Silences", "description": "Planned-downtime alert suppression."},
            {"name": "Escalations", "description": "Per-rule escalation chains."},
            {"name": "Telemetry", "description": "LLM-usage, render-perf, source-health."},
            {"name": "Incidents", "description": "Correlated alert incidents."},
            {"name": "Audit", "description": "Audit log read + SIEM export."},
            {"name": "NotificationPrefs", "description": "Per-user delivery preferences."},
        ],
        "security": _bearer_security(),
        "paths": _paths(),
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "API token (raw secret from ``tools.ops_cli tokens create``)",
                    "description": (
                        "RFC 6750 bearer scheme. Generate a per-user "
                        "token via ``python -m tools.ops_cli tokens "
                        "create <user_id> <label>`` and send the raw "
                        "secret in the ``Authorization`` header. The "
                        "scheme name match is case-insensitive."
                    ),
                },
            },
            "schemas": _components_schemas(),
        },
    }


def render_openapi_json(spec: dict) -> str:
    """Pretty-printed JSON with sorted keys.

    ``sort_keys=True`` is important — it makes the on-disk artifact
    stable across runs so a regenerated ``docs/openapi.json`` produces
    a clean diff when (and only when) the spec content actually
    changes.
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
#  Hand-rolled YAML emitter — covers the subset of YAML 1.2 needed for an
#  OpenAPI spec (nested dicts, lists, scalars: str/int/float/bool/null).
#  No PyYAML dep — we want one fewer transitive package on the worker.
# ─────────────────────────────────────────────────────────────────────────────

# Characters that force a YAML string to be quoted. Anything in this set
# (or a leading/trailing space, or empty, or any of the YAML special words
# below) will produce a double-quoted scalar instead of the plain form.
_YAML_FORCE_QUOTE_CHARS = set(":#&*!|>'\"%@`,[]{}\n\r\t")
_YAML_SPECIAL_WORDS = {
    "true", "false", "null", "yes", "no", "on", "off",
    "True", "False", "Null", "Yes", "No", "On", "Off",
    "TRUE", "FALSE", "NULL", "YES", "NO", "ON", "OFF",
    "~",
}


def _yaml_needs_quoting(s: str) -> bool:
    """Decide whether a string needs to be emitted as a quoted scalar.

    Plain scalars (no quotes) are nice because they keep the spec
    readable, but YAML has a long list of edge cases where a plain
    scalar would be reinterpreted as something else (a number, a
    boolean, a null). We escape conservatively — quoting too often is
    just visual noise, quoting too rarely is a wire-shape bug.
    """
    if s == "":
        return True
    if s in _YAML_SPECIAL_WORDS:
        return True
    # Leading / trailing whitespace would be stripped by the parser if
    # the value isn't quoted.
    if s != s.strip():
        return True
    # Anything that looks like a number must be quoted (otherwise YAML
    # parses it as an int / float).
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        pass
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        pass
    # Leading characters that have special meaning at the start of a
    # value.
    if s[0] in "-?@&*!|>'\"%`#,[]{}":
        return True
    for ch in s:
        if ch in _YAML_FORCE_QUOTE_CHARS:
            return True
    return False


def _yaml_escape_double_quoted(s: str) -> str:
    """Escape a Python string for emission as a YAML double-quoted scalar.

    Only the two characters that MUST be escaped inside a double-
    quoted YAML scalar are: ``\\`` and ``"``. Newlines / tabs / unicode
    are all safe inside double quotes per the YAML spec, but we still
    escape control chars to keep the on-disk artifact ASCII-friendly.
    """
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\"":
            out.append("\\\"")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\x{ord(ch):02X}")
        else:
            out.append(ch)
    return "\"" + "".join(out) + "\""


def _yaml_scalar(value: Any) -> str:
    """Render a scalar Python value as a YAML scalar string.

    Handles: None, bool, int, float, str. Anything else collapses to
    its repr() — the OpenAPI spec doesn't contain those types so we
    don't need a richer formatter.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        # Must come before the int check because bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if _yaml_needs_quoting(value):
            return _yaml_escape_double_quoted(value)
        return value
    # Fallback for unanticipated types — render as a double-quoted
    # string so the result still parses.
    return _yaml_escape_double_quoted(str(value))


def _yaml_emit(value: Any, indent: int) -> list[str]:
    """Recursive YAML emitter. Returns a list of LINES (no trailing
    newlines — joined by the caller). ``indent`` is the column at
    which the CURRENT node's keys / list items begin.

    Empty dicts emit ``{}`` and empty lists emit ``[]`` so an
    empty-block marker stays visible in the output.
    """
    pad = "  " * indent
    lines: list[str] = []

    if isinstance(value, dict):
        if not value:
            return ["{}"]
        # Sorted keys → stable on-disk output. Mirrors the JSON
        # renderer's sort_keys=True so both artifacts diff cleanly.
        for k in sorted(value.keys(), key=str):
            v = value[k]
            key_str = _yaml_scalar(str(k))
            if isinstance(v, dict):
                if not v:
                    lines.append(f"{pad}{key_str}: {{}}")
                else:
                    lines.append(f"{pad}{key_str}:")
                    lines.extend(_yaml_emit(v, indent + 1))
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{key_str}: []")
                else:
                    lines.append(f"{pad}{key_str}:")
                    lines.extend(_yaml_emit(v, indent + 1))
            else:
                lines.append(f"{pad}{key_str}: {_yaml_scalar(v)}")
        return lines

    if isinstance(value, list):
        if not value:
            return ["[]"]
        for item in value:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{pad}- {{}}")
                else:
                    # Emit each dict as ``- key1: …`` on first line,
                    # then aligned ``  key2: …`` on subsequent lines.
                    inner_lines = _yaml_emit(item, indent + 1)
                    if inner_lines:
                        first = inner_lines[0].lstrip()
                        lines.append(f"{pad}- {first}")
                        for rest in inner_lines[1:]:
                            lines.append(rest)
                    else:
                        lines.append(f"{pad}- {{}}")
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{pad}- []")
                else:
                    inner = _yaml_emit(item, indent + 1)
                    if inner:
                        first = inner[0].lstrip()
                        lines.append(f"{pad}- {first}")
                        for rest in inner[1:]:
                            lines.append(rest)
                    else:
                        lines.append(f"{pad}- []")
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return lines

    # Top-level scalar (rare but legal).
    return [f"{pad}{_yaml_scalar(value)}"]


def render_openapi_yaml(spec: dict) -> str:
    """Render the spec as YAML.

    Hand-rolled to avoid pulling in PyYAML. Covers the YAML subset
    needed for OpenAPI: nested dicts + lists + scalars (str / int /
    float / bool / null). Output is sorted by key for stable diffs.
    """
    lines = _yaml_emit(spec, indent=0)
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
#  Validation — quick structural sanity check used by the CLI ``validate``
#  subcommand. Not a full OpenAPI validator (that would mean importing a
#  third-party schema package); just covers the must-haves.
# ─────────────────────────────────────────────────────────────────────────────

def validate_spec(spec: dict) -> list[str]:
    """Return a list of human-readable validation errors. Empty list =
    spec is structurally sound. The CLI ``validate`` subcommand prints
    each error and exits non-zero when the list is non-empty."""
    errors: list[str] = []

    # Top-level keys.
    for required in ("openapi", "info", "paths", "components"):
        if required not in spec:
            errors.append(f"missing top-level key: {required}")
    # openapi version must be 3.0.x or 3.1.x.
    raw_version = spec.get("openapi", "")
    if not (raw_version.startswith("3.0.") or raw_version.startswith("3.1.")):
        errors.append(f"unsupported openapi version: {raw_version!r}")
    # info.title + info.version.
    info = spec.get("info") or {}
    if not info.get("title"):
        errors.append("info.title is empty")
    if not info.get("version"):
        errors.append("info.version is empty")
    # Each path must have at least one operation; each operation must
    # carry a unique operationId.
    seen_op_ids: dict[str, str] = {}
    paths = spec.get("paths") or {}
    if not paths:
        errors.append("paths object is empty")
    for path, methods in paths.items():
        if not methods:
            errors.append(f"path {path} has no methods defined")
            continue
        for verb, op in methods.items():
            if not isinstance(op, dict):
                errors.append(f"{verb.upper()} {path}: operation is not a dict")
                continue
            op_id = op.get("operationId")
            if not op_id:
                errors.append(f"{verb.upper()} {path}: missing operationId")
            else:
                if op_id in seen_op_ids:
                    other = seen_op_ids[op_id]
                    errors.append(
                        f"operationId {op_id!r} collides: "
                        f"{other} and {verb.upper()} {path}"
                    )
                else:
                    seen_op_ids[op_id] = f"{verb.upper()} {path}"
            # Each operation must define at least one response.
            if not op.get("responses"):
                errors.append(f"{verb.upper()} {path}: missing responses")
    # securitySchemes / BearerAuth must be present (every auth'd
    # operation references it).
    components = spec.get("components") or {}
    schemes = components.get("securitySchemes") or {}
    if "BearerAuth" not in schemes:
        errors.append("components.securitySchemes.BearerAuth is not defined")

    return errors
