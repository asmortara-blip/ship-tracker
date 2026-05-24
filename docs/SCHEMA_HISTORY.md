# Ship Tracker - Migration History

**Total migrations:** 23  
**Generated:** 2026-05-24T21:31:12.321816+00:00  

Source-of-truth: `state/migrations.py`. Each entry is the docstring + any inline SQL extracted via the stdlib `ast` module. Schemas declared via `_SCHEMA_VN` constants in `state/db.py` are documented by their summary; see that module for the literal SQL.

## v2 - `_migrate_to_v2`

Add the delivery_channels table for external alert delivery.

## v3 - `_migrate_to_v3`

Add the llm_calls table for LLM cost telemetry.

## v4 - `_migrate_to_v4`

Add the ``acknowledged_at`` column to the alerts table.

**SQL excerpts:**

```sql
ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT NOT NULL DEFAULT ''
```

## v5 - `_migrate_to_v5`

Add the public-share-link columns to ``report_history``.

**SQL excerpts:**

```sql
ALTER TABLE report_history ADD COLUMN
```

## v6 - `_migrate_to_v6`

Add the ``digest_mode`` column to ``delivery_channels``.

**SQL excerpts:**

```sql
ALTER TABLE delivery_channels ADD COLUMN digest_mode TEXT NOT NULL DEFAULT 'immediate'
```

## v7 - `_migrate_to_v7`

Add a nullable ``user_id`` column to each of the five existing domain tables (alerts, alert_rules, report_history, delivery_channels, llm_calls).

**SQL excerpts:**

```sql
ALTER TABLE
```

## v8 - `_migrate_to_v8`

Add the ``tab_render_events`` table for per-tab render telemetry.

## v9 - `_migrate_to_v9`

Add the ``investor_report_snapshots`` table.

## v10 - `_migrate_to_v10`

Add the ``audit_events`` table for security-review audit logging.

## v11 - `_migrate_to_v11`

Add the ``api_tokens`` table for per-user API access tokens (PATs).

## v12 - `_migrate_to_v12`

Add the ``data_source_health`` table for periodic feed liveness probes.

## v13 - `_migrate_to_v13`

Add the three quiet-hours columns to ``delivery_channels``.

**SQL excerpts:**

```sql
ALTER TABLE delivery_channels ADD COLUMN
```

## v14 - `_migrate_to_v14`

Add the ``fire_count`` and ``last_fired_at`` columns to ``alerts``.

**SQL excerpts:**

```sql
ALTER TABLE alerts ADD COLUMN
```

## v15 - `_migrate_to_v15`

Add the ``user_settings`` table for per-user preferences.

## v16 - `_migrate_to_v16`

Add the ``mfa_secret`` and ``mfa_enabled`` columns to ``users``.

**SQL excerpts:**

```sql
ALTER TABLE users ADD COLUMN
```

## v17 - `_migrate_to_v17`

Add the ``public_password_hash`` and ``public_password_salt`` columns to ``report_history`` for optional password-gated public share links.

**SQL excerpts:**

```sql
ALTER TABLE report_history ADD COLUMN
```

## v18 - `_migrate_to_v18`

Add per-rule cooldown support to the alert engine.

**SQL excerpts:**

```sql
ALTER TABLE
```

## v19 - `_migrate_to_v19`

Add bulk-acknowledgement metadata to the ``alerts`` table.

**SQL excerpts:**

```sql
ALTER TABLE alerts ADD COLUMN
```

## v20 - `_migrate_to_v20`

Add the ``report_schedules`` table for cron-driven auto-generated reports.

## v21 - `_migrate_to_v21`

Add the ``mfa_recovery_codes`` and ``user_invitations`` tables for the auth follow-on commit.

## v22 - `_migrate_to_v22`

Add the ``alert_silences`` table for planned-downtime alert silencing.

## v23 - `_migrate_to_v23`

Add the ``alert_annotations`` table for per-alert operator commentary threads.

## v24 - `_migrate_to_v24`

Add the alert escalation chain machinery.

**SQL excerpts:**

```sql
ALTER TABLE alerts ADD COLUMN
```

