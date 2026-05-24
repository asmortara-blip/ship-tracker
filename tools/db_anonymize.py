"""``tools.db_anonymize`` — SQLite DB anonymization for safe sharing.

Operators sometimes need to ship a copy of the live SQLite state DB
to a teammate (repro a bug, populate a staging environment, hand a
QA contractor a realistic dataset) without leaking the PII, secrets,
or operational fingerprints the live DB has accumulated.

This module produces an ANONYMIZED COPY of a source DB. The source is
opened READ-ONLY (``mode=ro``) and copied to the output path before any
mutation runs, so the source file is guaranteed to be untouched.

Three profiles
--------------

* ``standard`` — default. Scrubs PII + secrets (user emails / usernames,
  password hashes, MFA secrets, API tokens, delivery channel targets,
  vault keys, sensitive audit payloads, report file paths, annotation
  bodies). KEEPS the shape of alerts, rules, reports, schedules so the
  output is still usable for QA / staging / repro.
* ``aggressive`` — same as standard PLUS empties annotation bodies,
  zeroes ALL audit detail_json, and redacts alert bodies. Use when the
  recipient should see structure only, no operational content.
* ``redact-only`` — preserves row counts but replaces every scrubbed
  string field with the literal "REDACTED". Useful when you want to
  hand someone a shape-faithful skeleton with zero real data.

Determinism
-----------

Email / username replacement is keyed off a SHA-256 hash of the original
value so a second run on the same source produces the same output
byte-for-byte (modulo the underlying SQLite metadata). This makes diffs
between two anonymized DBs meaningful.

Public API
----------

* ``anonymize_db(source_path, output_path, *, profile, dry_run, verbose)``
* ``get_profile(profile_name)``

CLI: ``python -m tools.anonymize_cli`` (see ``tools/anonymize_cli.py``).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─── Profiles ─────────────────────────────────────────────────────────────


# A profile is a dict of toggles. Each anonymization pass reads the
# relevant key and decides what to do. New profiles only need to add a
# top-level entry below — every pass already consults the dict.
_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": {
        # User identity
        "scrub_user_emails": True,
        "scrub_password_hashes": True,
        "scrub_mfa_secrets": True,
        # API + recovery tokens
        "drop_api_tokens": True,
        "drop_mfa_recovery_codes": True,
        "drop_unconsumed_invitations": True,
        # Delivery targets
        "scrub_delivery_targets": True,
        # kv_state secret rows
        "drop_secret_kv_rows": True,
        # Annotations: keep entries, redact body content
        "redact_annotation_bodies": True,
        "empty_annotation_bodies": False,
        # Audit: redact only sensitive action payloads
        "redact_sensitive_audit_payloads": True,
        "empty_all_audit_payloads": False,
        # Reports
        "scrub_report_file_paths": True,
        # Alerts
        "redact_alert_bodies": False,
        # redact-only mode
        "redact_only_mode": False,
    },
    "aggressive": {
        "scrub_user_emails": True,
        "scrub_password_hashes": True,
        "scrub_mfa_secrets": True,
        "drop_api_tokens": True,
        "drop_mfa_recovery_codes": True,
        "drop_unconsumed_invitations": True,
        "scrub_delivery_targets": True,
        "drop_secret_kv_rows": True,
        # Aggressive: BLANK annotation bodies (no length hint kept).
        "redact_annotation_bodies": False,
        "empty_annotation_bodies": True,
        # Aggressive: zero EVERY audit payload, not just sensitive ones.
        "redact_sensitive_audit_payloads": False,
        "empty_all_audit_payloads": True,
        "scrub_report_file_paths": True,
        "redact_alert_bodies": True,
        "redact_only_mode": False,
    },
    "redact-only": {
        # Redact-only: structurally replace strings. Preserve row counts
        # everywhere (no row drops), so the recipient sees the exact
        # shape of the live DB.
        "scrub_user_emails": True,
        "scrub_password_hashes": True,
        "scrub_mfa_secrets": True,
        # row-drop passes disabled — preserve counts
        "drop_api_tokens": False,
        "drop_mfa_recovery_codes": False,
        "drop_unconsumed_invitations": False,
        "scrub_delivery_targets": True,
        "drop_secret_kv_rows": False,
        "redact_annotation_bodies": True,
        "empty_annotation_bodies": False,
        "redact_sensitive_audit_payloads": True,
        "empty_all_audit_payloads": False,
        "scrub_report_file_paths": True,
        "redact_alert_bodies": True,
        "redact_only_mode": True,
    },
}


# Audit actions whose detail_json may carry secrets (login attempts,
# MFA flows, token CRUD, channel CRUD). Standard profile zeros these
# but leaves benign actions (alert_ack, rule_edit) intact.
_SENSITIVE_AUDIT_ACTION_PREFIXES = (
    "login_",
    "logout_",
    "mfa_",
    "token_",
    "channel_",
    "signup_",
    "password_",
    "invitation_",
)


# kv_state keys matching any of these substrings are dropped (case-
# insensitive). ``vault:*`` is the explicit per-key vault namespace.
_SECRET_KV_PATTERNS = ("vault:", "secret", "token", "key", "password", "credential")


# Dummy password hash + salt — NOT a real bcrypt because the live system
# uses PBKDF2-HMAC-SHA256 (auth/gate.py). We supply a constant placeholder
# the auth layer will reject for any real login attempt. Storing a fixed
# string keeps determinism across runs and signals "this DB has been
# anonymized" to anyone inspecting it.
_DUMMY_PASSWORD_HASH = "REDACTED_PASSWORD_HASH"
_DUMMY_PASSWORD_SALT = "REDACTED_PASSWORD_SALT"


# ─── Public API ───────────────────────────────────────────────────────────


def get_profile(profile_name: str) -> Dict[str, Any]:
    """Return the anonymization rules dict for ``profile_name``.

    Raises ``ValueError`` for an unknown profile so the caller cannot
    silently fall back to a less-redacted profile by typo.
    """
    if profile_name not in _PROFILES:
        raise ValueError(
            f"unknown profile {profile_name!r} — "
            f"valid: {sorted(_PROFILES.keys())}"
        )
    # Return a shallow copy so the caller can't mutate the canonical
    # rules dict and accidentally leak that mutation into the next call.
    return dict(_PROFILES[profile_name])


def anonymize_db(
    source_path: str | Path,
    output_path: str | Path,
    *,
    profile: str = "standard",
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Dict[str, int]]:
    """Anonymize a copy of ``source_path`` into ``output_path``.

    Steps:
      1. Validate the profile name (raises ``ValueError`` on a typo).
      2. Open source READ-ONLY via ``mode=ro`` — even if a pass below
         had a bug, the source DB cannot be modified.
      3. If ``dry_run`` is False, ``shutil.copy2`` the source to
         output and run every enabled pass against the OUTPUT file.
      4. If ``dry_run`` is True, count what each pass WOULD modify
         (reading from source read-only) and skip the write.

    Returns ``{'scrubbed': {table: rows_modified}, 'dropped':
    {table: rows_deleted}}``.

    Never modifies the source DB.
    """
    src = Path(source_path)
    out = Path(output_path)

    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")

    rules = get_profile(profile)

    counts: Dict[str, Dict[str, int]] = {"scrubbed": {}, "dropped": {}}

    if dry_run:
        # Read-only scan against source. Open with mode=ro so a stray
        # write attempt below would error out immediately rather than
        # silently mutating the live DB.
        uri = f"file:{src}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            _count_passes(conn, rules, counts)
        finally:
            conn.close()
        if verbose:
            _emit_summary(counts, dest="<dry-run, no file written>")
        return counts

    # Wet run. Copy source → output (preserves mtime so caller can spot
    # an anomaly if a downstream pass crashes mid-flight), then mutate
    # the OUTPUT.
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out)

    conn = sqlite3.connect(str(out))
    try:
        # Apply each pass independently — a failure in one table doesn't
        # break the others. Counts come back as we go.
        _run_passes(conn, rules, counts)
        conn.commit()
        # VACUUM rebuilds the file so freed pages from row-drop passes
        # are physically reclaimed. Without VACUUM the output file can
        # actually be LARGER than the source (free pages plus the new
        # schema_change rows).
        conn.execute("VACUUM")
    finally:
        conn.close()

    if verbose:
        _emit_summary(counts, dest=str(out))

    return counts


# ─── Pass orchestration ───────────────────────────────────────────────────


def _run_passes(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Run every enabled pass against the writable connection. Each
    pass is wrapped in try/except — one bad table doesn't break the
    whole anonymization. We log + continue so the operator still gets
    a partially-scrubbed DB plus a clear error log."""
    for name, fn in _PASSES:
        try:
            fn(conn, rules, counts)
        except sqlite3.OperationalError as exc:
            # Missing table — the source DB predates a schema bump.
            # Skip and move on, but tell the operator.
            logger.warning(f"db_anonymize: pass {name!r} skipped: {exc}")
        except Exception as exc:  # noqa: BLE001 — operator-facing tool
            logger.error(f"db_anonymize: pass {name!r} failed: {exc}")


def _count_passes(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Read-only counterpart to ``_run_passes`` for ``--dry-run``. Counts
    rows each pass WOULD touch without mutating anything."""
    for name, count_fn in _DRY_RUN_COUNTS:
        try:
            count_fn(conn, rules, counts)
        except sqlite3.OperationalError as exc:
            logger.warning(f"db_anonymize: dry-count {name!r} skipped: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"db_anonymize: dry-count {name!r} failed: {exc}")


# ─── Deterministic hashing ────────────────────────────────────────────────


def _stable_hash(value: str) -> str:
    """SHA-256 of ``value`` truncated to 8 hex chars. Stable across runs
    so re-anonymizing the same DB produces the same output. 8 hex chars
    = 32 bits of entropy — plenty to avoid collisions in a typical
    operator DB (< 100 users)."""
    if value is None:
        value = ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _hashed_email(original: str) -> str:
    """Stable replacement email for ``original``. Same input → same
    output across runs, which keeps diffs between two anonymized DBs
    meaningful."""
    return f"user_{_stable_hash(original)}@example.com"


# ─── Passes ───────────────────────────────────────────────────────────────


def _pass_users(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Scrub users.username (effectively the email — see auth/users.py
    where invite.email must equal username), password_hash, password_salt,
    mfa_secret, mfa_enabled, last_login_at."""
    rows = conn.execute(
        "SELECT user_id, username FROM users"
    ).fetchall()
    if not rows:
        return

    modified = 0
    for user_id, username in rows:
        new_username = _hashed_email(username) if rules["scrub_user_emails"] else username

        if rules["redact_only_mode"]:
            # Redact-only: explicit literal so the recipient knows the
            # field was redacted (rather than hash-shaped).
            new_username = f"REDACTED_{_stable_hash(username)}"

        # Build the SET clause dynamically — only touch the columns the
        # profile enables. Saves a round-trip per disabled toggle.
        sets: list[str] = []
        params: list[Any] = []

        if rules["scrub_user_emails"]:
            sets.append("username = ?")
            params.append(new_username)
        if rules["scrub_password_hashes"]:
            sets.append("password_hash = ?")
            sets.append("password_salt = ?")
            params.extend([_DUMMY_PASSWORD_HASH, _DUMMY_PASSWORD_SALT])
        if rules["scrub_mfa_secrets"]:
            # Empty secret + flag-down so the recipient cannot get past
            # the MFA gate using a leftover authenticator app entry.
            sets.append("mfa_secret = ?")
            sets.append("mfa_enabled = ?")
            params.extend(["", 0])
        # last_login_at is operational fingerprinting — clear it.
        sets.append("last_login_at = ?")
        params.append("")

        if not sets:
            continue

        params.append(user_id)
        conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?",
            params,
        )
        modified += 1

    if modified:
        counts["scrubbed"]["users"] = modified


def _pass_api_tokens(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Wipe api_tokens entirely (force re-creation) OR redact strings
    if redact-only mode."""
    if rules["drop_api_tokens"]:
        cur = conn.execute("SELECT COUNT(*) FROM api_tokens")
        n = cur.fetchone()[0]
        if n:
            conn.execute("DELETE FROM api_tokens")
            counts["dropped"]["api_tokens"] = n
    elif rules["redact_only_mode"]:
        # Redact strings, preserve row count.
        cur = conn.execute("SELECT COUNT(*) FROM api_tokens")
        n = cur.fetchone()[0]
        if n:
            conn.execute(
                "UPDATE api_tokens SET label = 'REDACTED', "
                "token_hash = 'REDACTED', token_salt = 'REDACTED', "
                "token_prefix = 'REDACT00', last_used_at = ''"
            )
            counts["scrubbed"]["api_tokens"] = n


def _pass_mfa_recovery(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Wipe mfa_recovery_codes — the user must regenerate via the
    in-app flow after restoring the anonymized DB."""
    if rules["drop_mfa_recovery_codes"]:
        cur = conn.execute("SELECT COUNT(*) FROM mfa_recovery_codes")
        n = cur.fetchone()[0]
        if n:
            conn.execute("DELETE FROM mfa_recovery_codes")
            counts["dropped"]["mfa_recovery_codes"] = n
    elif rules["redact_only_mode"]:
        cur = conn.execute("SELECT COUNT(*) FROM mfa_recovery_codes")
        n = cur.fetchone()[0]
        if n:
            conn.execute(
                "UPDATE mfa_recovery_codes SET code_hash = 'REDACTED', "
                "salt = 'REDACTED'"
            )
            counts["scrubbed"]["mfa_recovery_codes"] = n


def _pass_user_invitations(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Drop unconsumed invitations (their tokens are still usable to
    sign up); rewrite emails on consumed invitations so the recipient
    cannot enumerate who got invited."""
    # Consumed rows: rewrite email (PII) but keep the row so the audit
    # trail of "who invited who" is intact (audit_events references
    # invite_id elsewhere).
    cur = conn.execute(
        "SELECT invite_id, email FROM user_invitations "
        "WHERE consumed_at IS NOT NULL AND email IS NOT NULL"
    )
    modified = 0
    for invite_id, email in cur.fetchall():
        if not email:
            continue
        new_email = _hashed_email(email)
        if rules["redact_only_mode"]:
            new_email = f"REDACTED_{_stable_hash(email)}"
        # Also blank the invite_token even on consumed rows (no point
        # carrying a one-shot token after the fact, and a recipient
        # might think it's still valid).
        conn.execute(
            "UPDATE user_invitations SET email = ?, invite_token = ? "
            "WHERE invite_id = ?",
            (new_email, f"consumed_{_stable_hash(invite_id)}", invite_id),
        )
        modified += 1

    if modified:
        counts["scrubbed"]["user_invitations"] = modified

    if rules["drop_unconsumed_invitations"]:
        # Drop unconsumed rows: token + email are both leakable.
        cur = conn.execute(
            "SELECT COUNT(*) FROM user_invitations WHERE consumed_at IS NULL"
        )
        n = cur.fetchone()[0]
        if n:
            conn.execute(
                "DELETE FROM user_invitations WHERE consumed_at IS NULL"
            )
            counts["dropped"]["user_invitations"] = n
    elif rules["redact_only_mode"]:
        # Redact-only: keep counts, scrub remaining unconsumed rows.
        cur = conn.execute(
            "SELECT invite_id, email FROM user_invitations "
            "WHERE consumed_at IS NULL"
        )
        rk = 0
        for invite_id, email in cur.fetchall():
            new_email = (
                f"REDACTED_{_stable_hash(email)}" if email else None
            )
            conn.execute(
                "UPDATE user_invitations SET email = ?, "
                "invite_token = ? WHERE invite_id = ?",
                (new_email, f"REDACTED_{_stable_hash(invite_id)}", invite_id),
            )
            rk += 1
        if rk:
            counts["scrubbed"]["user_invitations"] = (
                counts["scrubbed"].get("user_invitations", 0) + rk
            )


def _pass_delivery_channels(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Replace ``target`` (webhook URL / PagerDuty key / SMTP host /
    SMS number) per channel kind. The kind itself is fine to leak —
    "slack" / "email" / "pagerduty" tell the recipient how many of
    each channel are configured but no credentials. The ``name``
    column is operator-supplied (e.g. "Eng on-call Slack") so we
    redact-only that as well."""
    if not rules["scrub_delivery_targets"]:
        return

    cur = conn.execute(
        "SELECT channel_id, kind, name FROM delivery_channels"
    )
    modified = 0
    for channel_id, kind, _name in cur.fetchall():
        # Pick an obviously-fake stub per kind so anyone debugging
        # later sees immediately the value was scrubbed.
        stub = _stub_target_for_kind(kind)
        new_name = "REDACTED CHANNEL" if rules["redact_only_mode"] else "channel"
        conn.execute(
            "UPDATE delivery_channels SET target = ?, name = ? "
            "WHERE channel_id = ?",
            (stub, new_name, channel_id),
        )
        modified += 1

    if modified:
        counts["scrubbed"]["delivery_channels"] = modified


def _stub_target_for_kind(kind: str) -> str:
    """Return an obviously-fake delivery target appropriate for ``kind``.
    Anything the recipient tries to POST/email/SMS to will fail in a
    visible way, which is the point — no accidental delivery to a real
    target."""
    k = (kind or "").lower()
    if k == "slack":
        return "https://hooks.slack.com/services/REDACTED/REDACTED/REDACTED"
    if k == "email" or k == "smtp":
        return "redacted@example.com"
    if k == "sms":
        return "+10000000000"
    if k == "pagerduty":
        return "REDACTED_PAGERDUTY_INTEGRATION_KEY"
    if k == "webhook":
        return "https://example.com/redacted"
    # Generic fallback.
    return "https://example.com/redacted"


def _pass_kv_state(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Drop any kv_state row whose key matches a secret pattern. The
    rest of kv_state (schema_version, feature flags, etc.) is benign
    operational metadata."""
    if not rules["drop_secret_kv_rows"]:
        if rules["redact_only_mode"]:
            # Redact-only: scrub values of secret-pattern rows but keep
            # the row count.
            keys = [row[0] for row in conn.execute("SELECT key FROM kv_state").fetchall()]
            modified = 0
            for key in keys:
                if _looks_like_secret_key(key):
                    conn.execute(
                        "UPDATE kv_state SET value = 'REDACTED' WHERE key = ?",
                        (key,),
                    )
                    modified += 1
            if modified:
                counts["scrubbed"]["kv_state"] = modified
        return

    # Standard / aggressive: drop matching rows outright. Use a single
    # SELECT-then-DELETE round trip so we can report the count.
    keys = [row[0] for row in conn.execute("SELECT key FROM kv_state").fetchall()]
    to_drop = [k for k in keys if _looks_like_secret_key(k)]
    if to_drop:
        # Parameterize the IN clause with one placeholder per key.
        placeholders = ",".join("?" * len(to_drop))
        conn.execute(
            f"DELETE FROM kv_state WHERE key IN ({placeholders})",
            to_drop,
        )
        counts["dropped"]["kv_state"] = len(to_drop)


def _looks_like_secret_key(key: str) -> bool:
    """True if ``key`` matches any of the secret patterns (case-
    insensitive). We deliberately err on the side of over-scrubbing —
    a false positive on a non-secret key only costs the recipient that
    one row; a false negative leaks a secret."""
    if not key:
        return False
    k = key.lower()
    # vault:* is an exact prefix match. The rest are substring matches
    # because operators name keys things like "user_42_api_token" or
    # "anthropic_secret_key".
    if k.startswith("vault:"):
        return True
    for pattern in _SECRET_KV_PATTERNS:
        if pattern == "vault:":
            continue
        if pattern in k:
            return True
    return False


def _pass_alert_annotations(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Standard profile: replace body with ``REDACTED ANNOTATION (N
    chars)`` so the recipient can still see the thread shape. Aggressive
    profile: blank the body entirely."""
    cur = conn.execute(
        "SELECT annotation_id, body FROM alert_annotations"
    )
    modified = 0
    for annotation_id, body in cur.fetchall():
        body_str = body or ""
        if rules["empty_annotation_bodies"]:
            new_body = ""
        elif rules["redact_annotation_bodies"]:
            new_body = f"REDACTED ANNOTATION ({len(body_str)} chars)"
        else:
            continue
        conn.execute(
            "UPDATE alert_annotations SET body = ? WHERE annotation_id = ?",
            (new_body, annotation_id),
        )
        modified += 1

    if modified:
        counts["scrubbed"]["alert_annotations"] = modified


def _pass_audit_events(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Standard: zero detail_json for sensitive actions (login_*,
    mfa_*, token_*, channel_*) — those payloads can carry secrets.
    Aggressive: zero ALL detail_json."""
    if rules["empty_all_audit_payloads"]:
        cur = conn.execute("SELECT COUNT(*) FROM audit_events")
        n = cur.fetchone()[0]
        if n:
            conn.execute("UPDATE audit_events SET detail_json = '{}'")
            counts["scrubbed"]["audit_events"] = n
        return

    if not rules["redact_sensitive_audit_payloads"]:
        return

    cur = conn.execute(
        "SELECT event_id, action FROM audit_events"
    )
    modified = 0
    sentinel = '{"REDACTED": true}'
    for event_id, action in cur.fetchall():
        if not action:
            continue
        if any(action.startswith(prefix) for prefix in _SENSITIVE_AUDIT_ACTION_PREFIXES):
            conn.execute(
                "UPDATE audit_events SET detail_json = ? WHERE event_id = ?",
                (sentinel, event_id),
            )
            modified += 1
    if modified:
        counts["scrubbed"]["audit_events"] = modified


def _pass_report_history(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Stub file_path (leaks fs layout) + zero password hashes on
    public share links. The HTML itself lives on disk outside the DB
    (see tools/backup_cli.py) — we don't touch it here; the operator
    is expected to exclude cache/reports/*.html from the share, OR
    use ``tools.backup_cli create --exclude-reports`` (if they ship
    the backup tar.gz to a teammate)."""
    if not rules["scrub_report_file_paths"]:
        return

    cur = conn.execute("SELECT COUNT(*) FROM report_history")
    n = cur.fetchone()[0]
    if n:
        # Replace file_path with a constant stub so the structure of
        # the local fs (username in path? environment-specific cache
        # location?) doesn't leak. Also wipe any public-share password
        # hash — the link's slug is now meaningless because the file
        # is gone too.
        conn.execute(
            "UPDATE report_history SET file_path = 'cache/reports/REDACTED.html', "
            "public_password_hash = NULL, public_password_salt = NULL"
        )
        counts["scrubbed"]["report_history"] = n


def _pass_alerts(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """Aggressive only: redact alert body + acknowledged_note. Standard
    leaves alert content alone — alert.body is typically operator-
    generated detection text (e.g. 'BDI fell 12% over 7 days'); the
    body itself isn't a secret. Aggressive treats it as a free-text
    field the operator may have customized to embed customer or trader
    names — wipe it."""
    if not rules["redact_alert_bodies"]:
        return

    cur = conn.execute("SELECT COUNT(*) FROM alerts")
    n = cur.fetchone()[0]
    if n:
        conn.execute(
            "UPDATE alerts SET body = 'REDACTED', "
            "acknowledged_note = NULL"
        )
        counts["scrubbed"]["alerts"] = n


def _pass_user_settings(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    """user_settings.settings_json may carry per-user preferences that
    include third-party connector credentials or personal data (e.g.
    a custom watchlist of tickers). Standard and aggressive both wipe
    settings_json to '{}' — the recipient gets default settings on
    next login."""
    if rules["redact_only_mode"]:
        # Preserve row count.
        cur = conn.execute("SELECT COUNT(*) FROM user_settings")
        n = cur.fetchone()[0]
        if n:
            conn.execute(
                "UPDATE user_settings SET settings_json = '{}'"
            )
            counts["scrubbed"]["user_settings"] = n
        return

    # Standard / aggressive: same — wipe the json blob, keep the row.
    cur = conn.execute("SELECT COUNT(*) FROM user_settings")
    n = cur.fetchone()[0]
    if n:
        conn.execute("UPDATE user_settings SET settings_json = '{}'")
        counts["scrubbed"]["user_settings"] = n


# ─── Dry-run counters (read-only) ─────────────────────────────────────────


def _count_users(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["users"] = n


def _count_api_tokens(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM api_tokens")
    n = cur.fetchone()[0]
    if not n:
        return
    if rules["drop_api_tokens"]:
        counts["dropped"]["api_tokens"] = n
    elif rules["redact_only_mode"]:
        counts["scrubbed"]["api_tokens"] = n


def _count_mfa_recovery(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM mfa_recovery_codes")
    n = cur.fetchone()[0]
    if not n:
        return
    if rules["drop_mfa_recovery_codes"]:
        counts["dropped"]["mfa_recovery_codes"] = n
    elif rules["redact_only_mode"]:
        counts["scrubbed"]["mfa_recovery_codes"] = n


def _count_user_invitations(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    cur = conn.execute(
        "SELECT COUNT(*) FROM user_invitations WHERE consumed_at IS NULL"
    )
    n_unconsumed = cur.fetchone()[0]
    cur = conn.execute(
        "SELECT COUNT(*) FROM user_invitations WHERE consumed_at IS NOT NULL"
    )
    n_consumed = cur.fetchone()[0]
    if rules["drop_unconsumed_invitations"] and n_unconsumed:
        counts["dropped"]["user_invitations"] = n_unconsumed
    if n_consumed:
        counts["scrubbed"]["user_invitations"] = n_consumed


def _count_delivery_channels(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    if not rules["scrub_delivery_targets"]:
        return
    cur = conn.execute("SELECT COUNT(*) FROM delivery_channels")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["delivery_channels"] = n


def _count_kv_state(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    keys = [row[0] for row in conn.execute("SELECT key FROM kv_state").fetchall()]
    n = sum(1 for k in keys if _looks_like_secret_key(k))
    if not n:
        return
    if rules["drop_secret_kv_rows"]:
        counts["dropped"]["kv_state"] = n
    elif rules["redact_only_mode"]:
        counts["scrubbed"]["kv_state"] = n


def _count_alert_annotations(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    if not (rules["redact_annotation_bodies"] or rules["empty_annotation_bodies"]):
        return
    cur = conn.execute("SELECT COUNT(*) FROM alert_annotations")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["alert_annotations"] = n


def _count_audit_events(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    if rules["empty_all_audit_payloads"]:
        cur = conn.execute("SELECT COUNT(*) FROM audit_events")
        n = cur.fetchone()[0]
        if n:
            counts["scrubbed"]["audit_events"] = n
        return
    if not rules["redact_sensitive_audit_payloads"]:
        return
    actions = [
        row[0] for row in conn.execute("SELECT action FROM audit_events").fetchall()
    ]
    n = sum(
        1
        for a in actions
        if a and any(a.startswith(p) for p in _SENSITIVE_AUDIT_ACTION_PREFIXES)
    )
    if n:
        counts["scrubbed"]["audit_events"] = n


def _count_report_history(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    if not rules["scrub_report_file_paths"]:
        return
    cur = conn.execute("SELECT COUNT(*) FROM report_history")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["report_history"] = n


def _count_alerts(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    if not rules["redact_alert_bodies"]:
        return
    cur = conn.execute("SELECT COUNT(*) FROM alerts")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["alerts"] = n


def _count_user_settings(
    conn: sqlite3.Connection,
    rules: Dict[str, Any],
    counts: Dict[str, Dict[str, int]],
) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM user_settings")
    n = cur.fetchone()[0]
    if n:
        counts["scrubbed"]["user_settings"] = n


# Pass registry — order matters only for the verbose summary; passes
# are independent and could run in any order.
_PASSES: list = [
    ("users", _pass_users),
    ("api_tokens", _pass_api_tokens),
    ("mfa_recovery_codes", _pass_mfa_recovery),
    ("user_invitations", _pass_user_invitations),
    ("delivery_channels", _pass_delivery_channels),
    ("kv_state", _pass_kv_state),
    ("alert_annotations", _pass_alert_annotations),
    ("audit_events", _pass_audit_events),
    ("report_history", _pass_report_history),
    ("alerts", _pass_alerts),
    ("user_settings", _pass_user_settings),
]

_DRY_RUN_COUNTS: list = [
    ("users", _count_users),
    ("api_tokens", _count_api_tokens),
    ("mfa_recovery_codes", _count_mfa_recovery),
    ("user_invitations", _count_user_invitations),
    ("delivery_channels", _count_delivery_channels),
    ("kv_state", _count_kv_state),
    ("alert_annotations", _count_alert_annotations),
    ("audit_events", _count_audit_events),
    ("report_history", _count_report_history),
    ("alerts", _count_alerts),
    ("user_settings", _count_user_settings),
]


# ─── Operator-facing summary ──────────────────────────────────────────────


def _emit_summary(counts: Dict[str, Dict[str, int]], *, dest: str) -> None:
    """Pretty-print per-table counts to stderr for ``--verbose``."""
    print(f"db_anonymize: wrote {dest}", file=sys.stderr)
    scrubbed = counts.get("scrubbed", {})
    dropped = counts.get("dropped", {})
    if scrubbed:
        print("  scrubbed:", file=sys.stderr)
        for table in sorted(scrubbed):
            print(f"    {table}: {scrubbed[table]} rows", file=sys.stderr)
    if dropped:
        print("  dropped:", file=sys.stderr)
        for table in sorted(dropped):
            print(f"    {table}: {dropped[table]} rows", file=sys.stderr)
    if not scrubbed and not dropped:
        print("  (no changes)", file=sys.stderr)
