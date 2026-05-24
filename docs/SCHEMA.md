# Ship Tracker - Database Schema

**Schema version:** 24  
**Generated:** 2026-05-24T21:31:00.715304+00:00  
**Total tables:** 19  
**Total rows (this snapshot):** 15653  
**DB path:** `/Users/aaronmortara/MC/Models/Ship/cache/ship_tracker.db`

## Table of contents

- [alert_annotations](#alert_annotations)
- [alert_escalation_chains](#alert_escalation_chains)
- [alert_rules](#alert_rules)
- [alert_silences](#alert_silences)
- [alerts](#alerts)
- [api_tokens](#api_tokens)
- [audit_events](#audit_events)
- [data_source_health](#data_source_health)
- [delivery_channels](#delivery_channels)
- [investor_report_snapshots](#investor_report_snapshots)
- [kv_state](#kv_state)
- [llm_calls](#llm_calls)
- [mfa_recovery_codes](#mfa_recovery_codes)
- [report_history](#report_history)
- [report_schedules](#report_schedules)
- [tab_render_events](#tab_render_events)
- [user_invitations](#user_invitations)
- [user_settings](#user_settings)
- [users](#users)

## alert_annotations

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `annotation_id` | TEXT | yes | - | yes |  |
| `alert_id` | TEXT | no | - | - |  |
| `user_id` | TEXT | no | - | - |  |
| `author_user_id` | TEXT | no | - | - |  |
| `body` | TEXT | no | - | - |  |
| `created_at` | TEXT | no | - | - |  |
| `edited_at` | TEXT | yes | - | - |  |

**Indexes:**

- `idx_alert_annotations_alert` (alert_id, created_at)

## alert_escalation_chains

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `chain_id` | TEXT | yes | - | yes |  |
| `rule_id` | TEXT | no | - | - |  |
| `user_id` | TEXT | no | - | - |  |
| `step_number` | INTEGER | no | - | - |  |
| `after_minutes` | INTEGER | no | - | - |  |
| `channel_id` | TEXT | no | - | - |  |
| `created_at` | TEXT | no | - | - |  |

**Indexes:**

- `idx_escalation_rule` (rule_id, step_number)
- `uq_escalation_step` UNIQUE (rule_id, user_id, step_number)

## alert_rules

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `rule_id` | TEXT | yes | - | yes | UUID primary key for the alert rule. |
| `data` | TEXT | no | - | - |  |
| `user_id` | TEXT | no | `''` | - |  |
| `cooldown_minutes` | INTEGER | no | `0` | - | Suppress repeat fires of this rule for N minutes (0 = no cooldown). |

## alert_silences

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `silence_id` | TEXT | yes | - | yes |  |
| `user_id` | TEXT | no | - | - |  |
| `rule_id` | TEXT | yes | - | - |  |
| `ticker` | TEXT | yes | - | - |  |
| `severity` | TEXT | yes | - | - |  |
| `reason` | TEXT | yes | - | - |  |
| `starts_at` | TEXT | no | - | - |  |
| `expires_at` | TEXT | no | - | - |  |
| `created_at` | TEXT | no | - | - |  |
| `created_by_user_id` | TEXT | no | - | - |  |

**Indexes:**

- `idx_silences_active` (user_id, expires_at)

**Foreign keys:**

- `created_by_user_id` -> `users(user_id)`

## alerts

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `alert_id` | TEXT | yes | - | yes | UUID primary key for the alert row. |
| `created_at` | TEXT | no | - | - |  |
| `alert_type` | TEXT | no | - | - | Category bucket — MACRO, MICRO, FREIGHT, etc. |
| `severity` | TEXT | no | - | - | LOW / MEDIUM / HIGH / CRITICAL. |
| `title` | TEXT | no | - | - |  |
| `body` | TEXT | no | - | - |  |
| `ticker` | TEXT | no | `''` | - |  |
| `route_id` | TEXT | no | `''` | - |  |
| `port_locode` | TEXT | no | `''` | - |  |
| `value` | REAL | no | `0.0` | - |  |
| `threshold` | REAL | no | `0.0` | - |  |
| `change_pct` | REAL | no | `0.0` | - |  |
| `acknowledged` | INTEGER | no | `0` | - |  |
| `acknowledged_at` | TEXT | no | `''` | - | ISO-8601 UTC stamp set when an operator acks the alert. |
| `user_id` | TEXT | no | `''` | - |  |
| `fire_count` | INTEGER | no | `1` | - | How many times this dedup-key tuple has fired in the dedup window. |
| `last_fired_at` | TEXT | no | `''` | - | ISO-8601 UTC stamp of the most-recent fire (for window dedup). |
| `rule_id` | TEXT | yes | - | - | Originating AlertRule id (NULL on detection-path alerts). |
| `acknowledged_note` | TEXT | yes | - | - | Free-form note attached at ack time. NULL when no note. |
| `acknowledged_by_user_id` | TEXT | yes | - | - | user_id of the operator who acked the alert. |
| `last_escalated_at` | TEXT | yes | - | - | ISO-8601 stamp of the most-recent escalation step fire. |
| `escalation_step` | INTEGER | no | `0` | - | Which step of the rule's escalation chain has fired so far. |

**Indexes:**

- `idx_alerts_created_at` (created_at)
- `idx_alerts_unacknowledged` (acknowledged)

## api_tokens

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `token_id` | TEXT | yes | - | yes | UUID primary key for the PAT row. |
| `user_id` | TEXT | no | - | - |  |
| `label` | TEXT | no | - | - |  |
| `token_hash` | TEXT | no | - | - | Hashed-and-salted token; raw secret returned once at creation. |
| `token_salt` | TEXT | no | - | - |  |
| `token_prefix` | TEXT | no | - | - | First 8 chars of the plaintext token — indexed for O(log n) lookup. |
| `created_at` | TEXT | no | - | - |  |
| `last_used_at` | TEXT | no | `''` | - |  |
| `revoked` | INTEGER | no | `0` | - | 0/1 — when 1, the token can no longer authenticate. |

**Indexes:**

- `idx_api_tokens_prefix` (token_prefix)
- `idx_api_tokens_user_id` (user_id)

## audit_events

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `event_id` | TEXT | yes | - | yes | UUID primary key for the audit row. |
| `created_at` | TEXT | no | - | - |  |
| `user_id` | TEXT | no | `''` | - |  |
| `action` | TEXT | no | - | - | Verb describing the action — login, ack, rule_create, etc. |
| `entity_type` | TEXT | no | `''` | - |  |
| `entity_id` | TEXT | no | `''` | - |  |
| `detail_json` | TEXT | no | `'{}'` | - |  |

**Indexes:**

- `idx_audit_events_action` (action)
- `idx_audit_events_created_at` (created_at)
- `idx_audit_events_user_id` (user_id)

## data_source_health

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `ping_id` | TEXT | yes | - | yes |  |
| `source` | TEXT | no | - | - |  |
| `started_at` | TEXT | no | - | - |  |
| `duration_ms` | INTEGER | no | - | - |  |
| `status` | TEXT | no | - | - |  |
| `error_msg` | TEXT | no | `''` | - |  |

**Indexes:**

- `idx_data_source_health_source` (source)
- `idx_data_source_health_started_at` (started_at)

## delivery_channels

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `channel_id` | TEXT | yes | - | yes | UUID primary key for the channel. |
| `name` | TEXT | no | - | - |  |
| `kind` | TEXT | no | - | - | Channel transport — slack / email / sms / pagerduty / webhook / opsgenie. |
| `target` | TEXT | no | - | - | Endpoint string — webhook URL, email address, phone number, etc. |
| `severity_threshold` | TEXT | no | `'LOW'` | - |  |
| `enabled` | INTEGER | no | `1` | - |  |
| `created_at` | TEXT | no | - | - |  |
| `digest_mode` | TEXT | no | `'immediate'` | - | 'immediate' (one delivery per alert) or 'daily' (batched digest). |
| `user_id` | TEXT | no | `''` | - |  |
| `quiet_start` | TEXT | no | `''` | - | HH:MM UTC start of the quiet window (empty = no window). |
| `quiet_end` | TEXT | no | `''` | - | HH:MM UTC end of the quiet window. |
| `quiet_override_critical` | INTEGER | no | `1` | - | 0/1 — when 1, CRITICAL alerts always deliver during quiet hours. |

## investor_report_snapshots

214 rows.

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `snapshot_id` | TEXT | yes | - | yes |  |
| `generated_at` | TEXT | no | - | - |  |
| `report_date` | TEXT | no | `''` | - |  |
| `payload_json` | TEXT | no | - | - |  |
| `user_id` | TEXT | no | `''` | - |  |

**Indexes:**

- `idx_investor_report_snapshots_generated_at` (generated_at)

## kv_state

1 row.

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `key` | TEXT | yes | - | yes | Lookup key — e.g. 'schema_version', 'last_alert_prune_at'. |
| `value` | TEXT | no | - | - | Free-form string value (JSON-encoded for complex types). |
| `updated_at` | TEXT | no | - | - |  |

## llm_calls

256 rows.

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `call_id` | TEXT | yes | - | yes |  |
| `created_at` | TEXT | no | - | - |  |
| `source` | TEXT | no | - | - |  |
| `tab_name` | TEXT | no | `''` | - |  |
| `model` | TEXT | no | - | - |  |
| `tokens_in` | INTEGER | no | `0` | - |  |
| `tokens_out` | INTEGER | no | `0` | - |  |
| `cached_tokens` | INTEGER | no | `0` | - |  |
| `est_cost_usd` | REAL | no | `0.0` | - |  |
| `user_id` | TEXT | no | `''` | - |  |

**Indexes:**

- `idx_llm_calls_created_at` (created_at)
- `idx_llm_calls_source` (source)

## mfa_recovery_codes

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `code_id` | TEXT | yes | - | yes |  |
| `user_id` | TEXT | no | - | - |  |
| `code_hash` | TEXT | no | - | - |  |
| `salt` | TEXT | no | - | - |  |
| `used_at` | TEXT | yes | - | - |  |
| `created_at` | TEXT | no | - | - |  |

**Indexes:**

- `idx_mfa_recovery_user` (user_id, used_at)

**Foreign keys:**

- `user_id` -> `users(user_id)`

## report_history

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `report_id` | TEXT | yes | - | yes | UUID primary key for the persisted report row. |
| `generated_at` | TEXT | no | - | - |  |
| `report_date` | TEXT | no | `''` | - |  |
| `sentiment_label` | TEXT | no | `''` | - |  |
| `sentiment_score` | REAL | no | `0.0` | - |  |
| `risk_level` | TEXT | no | `''` | - |  |
| `signal_count` | INTEGER | no | `0` | - |  |
| `data_quality` | TEXT | no | `''` | - |  |
| `file_path` | TEXT | no | - | - |  |
| `file_size_kb` | REAL | no | `0.0` | - |  |
| `public_slug` | TEXT | no | `''` | - | URL-safe token for a read-only public share link (empty when not shared). |
| `public_expires_at` | TEXT | no | `''` | - | ISO-8601 UTC; the public link is valid only while in the future. |
| `user_id` | TEXT | no | `''` | - |  |
| `public_password_hash` | TEXT | yes | - | - | pbkdf2-sha256 hash of the optional public-link password (NULL when none). |
| `public_password_salt` | TEXT | yes | - | - | Random salt paired with public_password_hash. |

**Indexes:**

- `idx_report_history_generated_at` (generated_at)

## report_schedules

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `schedule_id` | TEXT | yes | - | yes |  |
| `user_id` | TEXT | no | - | - |  |
| `name` | TEXT | no | - | - |  |
| `cron_expr` | TEXT | no | - | - |  |
| `enabled` | INTEGER | no | `1` | - |  |
| `last_run_at` | TEXT | yes | - | - |  |
| `last_run_status` | TEXT | yes | - | - |  |
| `last_run_message` | TEXT | yes | - | - |  |
| `next_run_at` | TEXT | yes | - | - |  |
| `created_at` | TEXT | no | - | - |  |
| `updated_at` | TEXT | yes | - | - |  |

**Indexes:**

- `idx_report_schedules_next` (enabled, next_run_at)

## tab_render_events

15182 rows.

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `event_id` | TEXT | yes | - | yes |  |
| `tab_name` | TEXT | no | - | - |  |
| `started_at` | TEXT | no | - | - |  |
| `duration_ms` | INTEGER | no | `0` | - |  |
| `success` | INTEGER | no | `1` | - |  |
| `error_msg` | TEXT | no | `''` | - |  |

**Indexes:**

- `idx_tab_render_events_started_at` (started_at)
- `idx_tab_render_events_tab` (tab_name)

## user_invitations

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `invite_id` | TEXT | yes | - | yes |  |
| `invite_token` | TEXT | no | - | - |  |
| `email` | TEXT | yes | - | - |  |
| `role` | TEXT | no | `'user'` | - |  |
| `invited_by_user_id` | TEXT | no | - | - |  |
| `expires_at` | TEXT | no | - | - |  |
| `consumed_at` | TEXT | yes | - | - |  |
| `consumed_by_user_id` | TEXT | yes | - | - |  |
| `created_at` | TEXT | no | - | - |  |

**Indexes:**

- `idx_invite_token` (invite_token)

## user_settings

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `user_id` | TEXT | yes | - | yes |  |
| `settings_json` | TEXT | no | `'{}'` | - |  |
| `updated_at` | TEXT | no | - | - |  |

## users

Empty (0 rows).

| Column | Type | Nullable | Default | PK | Description |
|---|---|---|---|---|---|
| `user_id` | TEXT | yes | - | yes | UUID primary key for the user account. |
| `username` | TEXT | no | - | - | Login username — UNIQUE per the v7 index. |
| `password_hash` | BLOB | no | - | - | Hex-encoded scrypt-with-PBKDF2-fallback hash. |
| `password_salt` | BLOB | no | - | - | Per-user random salt; pairs with password_hash. |
| `role` | TEXT | no | `'user'` | - | 'admin' or 'user' — gates admin-only routes. |
| `created_at` | TEXT | no | - | - |  |
| `last_login_at` | TEXT | no | `''` | - |  |
| `mfa_secret` | TEXT | no | `''` | - | Base32 TOTP secret (empty when MFA disabled). |
| `mfa_enabled` | INTEGER | no | `0` | - | 0/1 — when 1, login requires a valid TOTP code. |

**Indexes:**

- `idx_users_username` (username)

