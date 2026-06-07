"""Report history — save, load, list, and delete generated investor reports.

Reports are stored as HTML files under cache/reports/. Metadata for every
saved report lives in the SQLite ``report_history`` table (see ``state.db``).
The legacy ``report_index.json`` is no longer written; on first migration
its contents are imported into SQLite and the JSON file is left in place
as a safety net.

The module is intentionally crash-proof: every public function catches all
exceptions and returns a sensible default (empty list, None, False, {}).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_DIR: Path = Path(__file__).parent.parent / "cache" / "reports"
MAX_REPORTS: int = 30  # keep the last N reports on disk

# Legacy path — kept so state.migrations can find it on first run. Production
# reads/writes go through SQLite.
_INDEX_FILE: Path = REPORT_DIR / "report_index.json"

# ── Public-link password KDF parameters ───────────────────────────────────
# Matches the iteration count used by ``auth.gate._hash_password`` in the
# pbkdf2 fallback path. The hash is stored hex-encoded for SQLite TEXT
# columns; the salt is stored hex-encoded for the same reason.
_PUBLIC_PASSWORD_KDF: str = "sha256"
_PUBLIC_PASSWORD_ITERATIONS: int = 200_000
_PUBLIC_PASSWORD_SALT_BYTES: int = 16
_PUBLIC_PASSWORD_DKLEN: int = 32


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ReportMeta:
    report_id: str               # UUID
    generated_at: str            # ISO timestamp (UTC)
    report_date: str             # human-readable date shown in the report
    sentiment_label: str         # BULLISH / BEARISH / NEUTRAL / MIXED
    sentiment_score: float       # -1.0 to +1.0
    risk_level: str              # LOW / MODERATE / HIGH / CRITICAL
    signal_count: int            # number of alpha signals included
    data_quality: str            # FULL / PARTIAL / DEGRADED
    file_path: str               # absolute path to the stored HTML file
    file_size_kb: float          # file size in kilobytes
    public_slug: str = ""        # URL-safe base64url token; "" = not shared
    public_expires_at: str = ""  # ISO-8601 UTC; "" or past time = invalid
    public_password_protected: bool = False  # True iff public link requires a password
    report_version: int = 1      # 1-indexed position in the lineage (v34)
    supersedes_id: str = ""      # report_id of the prior version; "" = original (v34)


# ---------------------------------------------------------------------------
# Password-hashing helpers for optional public-link password protection
# ---------------------------------------------------------------------------


def _hash_public_password(
    password: str,
    salt: bytes | None = None,
) -> tuple[str, str]:
    """Derive a (hex_hash, hex_salt) pair from *password*.

    Uses ``hashlib.pbkdf2_hmac('sha256', …, 200_000)`` — the same KDF
    family and iteration count the rest of ``auth/`` uses for password
    hashing — so the cost profile is consistent across the codebase.

    Args:
        password: The user-supplied plaintext. Encoded as UTF-8 before
                  hashing.
        salt:     Optional pre-generated salt (bytes). When ``None``,
                  a fresh random salt is generated via
                  ``secrets.token_bytes``. Explicit salts are accepted
                  so :func:`_verify_public_password` can re-derive a
                  candidate hash against the stored salt.

    Returns:
        ``(hex_hash, hex_salt)`` — both hex-encoded so they fit cleanly
        into SQLite TEXT columns.
    """
    if salt is None:
        salt = secrets.token_bytes(_PUBLIC_PASSWORD_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        _PUBLIC_PASSWORD_KDF,
        password.encode("utf-8"),
        salt,
        _PUBLIC_PASSWORD_ITERATIONS,
        dklen=_PUBLIC_PASSWORD_DKLEN,
    )
    return derived.hex(), salt.hex()


def _verify_public_password(
    password: str,
    stored_hash: str,
    stored_salt: str,
) -> bool:
    """Constant-time verification of *password* against the stored pair.

    Returns ``True`` iff PBKDF2(password, decode_hex(stored_salt))
    matches ``decode_hex(stored_hash)``. Uses ``hmac.compare_digest``
    so a timing attacker cannot iterate hash-prefix bytes.

    Any decoding error (bad hex) or any other exception returns
    ``False`` — this function never raises. A missing / empty stored
    hash or salt is treated as "not protected" by the caller; this
    helper is only consulted when both are non-empty.
    """
    try:
        salt_bytes = bytes.fromhex(stored_salt)
        expected = bytes.fromhex(stored_hash)
    except (TypeError, ValueError):
        return False
    try:
        candidate = hashlib.pbkdf2_hmac(
            _PUBLIC_PASSWORD_KDF,
            password.encode("utf-8"),
            salt_bytes,
            _PUBLIC_PASSWORD_ITERATIONS,
            dklen=len(expected) if expected else _PUBLIC_PASSWORD_DKLEN,
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _row_to_meta(row) -> ReportMeta:
    """Map a sqlite3.Row from the report_history table to a ReportMeta.

    Tolerates rows missing the v5 public-share columns — older databases
    that haven't run the v5 migration yet, or row factories that strip
    columns — by defaulting both fields to the empty string. The v17
    ``public_password_hash`` column is reduced to a single boolean
    ``public_password_protected`` so the metadata never carries the
    hash itself (defence-in-depth: a row returned to a logger or a UI
    cannot leak the hash)."""
    # sqlite3.Row does not implement .get(); reach for the column by key
    # and fall back if it does not exist on the row.
    try:
        public_slug = row["public_slug"] or ""
    except (IndexError, KeyError):
        public_slug = ""
    try:
        public_expires_at = row["public_expires_at"] or ""
    except (IndexError, KeyError):
        public_expires_at = ""
    try:
        # NULL / empty-string both count as "no password set". We only
        # want the BOOLEAN exposed on the meta — never the hash itself.
        _pw_hash = row["public_password_hash"]
        public_password_protected = bool(_pw_hash)
    except (IndexError, KeyError):
        public_password_protected = False
    # v34 lineage columns — tolerate rows from a pre-v34 DB / a row factory
    # that strips columns. report_version defaults to 1 (original);
    # supersedes_id is NULL for an original report, which we normalise to ""
    # on the meta so the dataclass carries a plain string.
    try:
        _rv = row["report_version"]
        report_version = int(_rv) if _rv is not None else 1
    except (IndexError, KeyError, TypeError, ValueError):
        report_version = 1
    try:
        supersedes_id = row["supersedes_id"] or ""
    except (IndexError, KeyError):
        supersedes_id = ""
    return ReportMeta(
        report_id=row["report_id"],
        generated_at=row["generated_at"],
        report_date=row["report_date"],
        sentiment_label=row["sentiment_label"],
        sentiment_score=float(row["sentiment_score"]),
        risk_level=row["risk_level"],
        signal_count=int(row["signal_count"]),
        data_quality=row["data_quality"],
        file_path=row["file_path"],
        file_size_kb=float(row["file_size_kb"]),
        public_slug=public_slug,
        public_expires_at=public_expires_at,
        public_password_protected=public_password_protected,
        report_version=report_version,
        supersedes_id=supersedes_id,
    )


def save_report(html_content: str, report_obj: "Any") -> ReportMeta | None:
    """Persist *html_content* to disk and insert a row in report_history.

    Args:
        html_content: Fully rendered HTML string.
        report_obj:   An InvestorReport (or DailyDigest-compatible) instance
                      whose attributes supply the metadata fields.

    Returns:
        A populated ReportMeta on success, or None if saving fails.

    The row is stamped with the active user's id (resolved via
    ``state.user_scope.current_user_id``) so subsequent
    ``list_reports(user_id=...)`` calls can filter on it. Outside a
    Streamlit session the stamp is ``""`` and the report joins the
    legacy global bucket.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id

        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp_str}_{report_id[:8]}.html"
        file_path = REPORT_DIR / filename

        # Write HTML
        file_path.write_text(html_content, encoding="utf-8")
        file_size_kb = round(file_path.stat().st_size / 1024, 2)

        meta = ReportMeta(
            report_id=report_id,
            generated_at=now.isoformat(),
            report_date=_attr(report_obj, "date", now.strftime("%B %d, %Y")),
            sentiment_label=_attr(report_obj, "market_sentiment", "NEUTRAL"),
            sentiment_score=_safe_float(_attr(report_obj, "sentiment_score", 0.0)),
            risk_level=_attr(report_obj, "risk_level", "MODERATE"),
            signal_count=_safe_int(_attr(report_obj, "signal_count", 0)),
            data_quality=_attr(report_obj, "data_quality", "PARTIAL"),
            file_path=str(file_path.resolve()),
            file_size_kb=file_size_kb,
        )

        uid = current_user_id()
        conn = get_connection()
        # A fresh save is always version 1 with no predecessor — the v34
        # lineage columns are written explicitly (rather than left to the
        # DEFAULT) so the row shape is identical whether it was minted here
        # or by :func:`amend_report`.
        meta.report_version = 1
        meta.supersedes_id = ""
        _insert_report_row(conn, meta, uid, report_version=1, supersedes_id=None)

        # Audit-log the generation. Placed AFTER the row was successfully
        # inserted so we only emit an event when the report actually
        # persisted — the failure path (returns None below) does not fire
        # this hook. We pass ``user_id=uid`` (the same stamp the row got)
        # rather than None so a scheduled save outside a Streamlit session
        # records the resolved id consistently with the row it audits.
        try:
            from auth.audit import record_audit
            record_audit(
                "generate_report",
                entity_type="report",
                entity_id=meta.report_id,
                detail={
                    "sentiment_label": meta.sentiment_label,
                    "risk_level": meta.risk_level,
                    "data_quality": meta.data_quality,
                },
                user_id=uid,
            )
        except Exception:  # noqa: BLE001
            pass

        # Prune any rows over MAX_REPORTS, oldest first. Also delete the
        # pruned files from disk.
        _prune_old_reports()

        logger.info(
            f"Report saved: {filename} "
            f"({file_size_kb:.1f} KB, {meta.sentiment_label}, {meta.risk_level})"
        )
        return meta

    except Exception as exc:
        logger.error(f"save_report failed: {exc}")
        return None


def list_reports(*, user_id: str | None = None) -> list[ReportMeta]:
    """Return all saved reports sorted newest-first, skipping missing files.

    If any rows reference files that have been deleted outside this module,
    those rows are removed from the SQLite index before returning.

    Honours per-user scoping with dual-set semantics — when ``user_id``
    resolves to a non-empty string, rows belonging to that user PLUS
    legacy ``user_id=''`` rows are returned. The empty-string case
    returns every row (legacy behaviour).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM report_history WHERE 1=1 {scope_sql} "
            f"ORDER BY generated_at DESC",
            scope_params,
        ).fetchall()
        valid: list[ReportMeta] = []
        stale_ids: list[str] = []
        for r in rows:
            meta = _row_to_meta(r)
            if Path(meta.file_path).exists():
                valid.append(meta)
            else:
                stale_ids.append(meta.report_id)

        if stale_ids:
            with conn:
                conn.executemany(
                    "DELETE FROM report_history WHERE report_id = ?",
                    [(rid,) for rid in stale_ids],
                )
        return valid
    except Exception as exc:
        logger.error(f"list_reports failed: {exc}")
        return []


def load_report_html(report_id: str, *, user_id: str | None = None) -> str | None:
    """Read and return the HTML for the given report_id.

    Honours per-user scoping: a user can only read reports in their
    own scope (their own rows + legacy ``user_id=''`` rows). Crossing
    scope returns ``None`` exactly like an unknown report_id — no
    distinguishable "permission denied" leak. Public-link reads go
    through :func:`load_public_report` instead and bypass scoping by
    design.

    Returns:
        HTML string, or None if the report is not found or unreadable.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT file_path FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"load_report_html: report_id not found: {report_id}")
            return None
        path = Path(row["file_path"])
        if not path.exists():
            logger.warning(f"load_report_html: file missing for {report_id}")
            return None
        html = path.read_text(encoding="utf-8")
        # Audit-log the in-scope access. Placed AFTER the read succeeds so
        # only genuine reads are recorded — the not-found / missing-file
        # early returns above do not fire this hook. We pass ``user_id``
        # through verbatim (``None`` resolves to the active session user)
        # so the audit row attributes the access to whoever opened it.
        try:
            from auth.audit import record_audit
            record_audit(
                "read_report",
                entity_type="report",
                entity_id=report_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return html
    except Exception as exc:
        logger.error(f"load_report_html failed for {report_id}: {exc}")
        return None


def delete_report(report_id: str, *, user_id: str | None = None) -> bool:
    """Remove a report from the index and delete its file from disk.

    Honours per-user scoping the same way as ``load_report_html`` —
    crossing scope returns ``False`` indistinguishably from an unknown
    report_id, and no row is deleted.

    Returns:
        True if the report was found and removed; False otherwise.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT file_path FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"delete_report: report_id not found: {report_id}")
            return False

        with conn:
            conn.execute(
                "DELETE FROM report_history WHERE report_id = ?",
                (report_id,),
            )

        path = Path(row["file_path"])
        if path.exists():
            try:
                path.unlink()
                logger.info(f"Deleted report file: {path.name}")
            except Exception as exc:
                logger.warning(f"Could not unlink {path}: {exc}")
        # Audit-log the deletion. Placed AFTER the row was successfully
        # removed so we only emit an event when the delete actually
        # happened — the early-return path on "report not found" does
        # not fire this hook.
        try:
            from auth.audit import record_audit
            record_audit(
                "delete_report",
                entity_type="report",
                entity_id=report_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:
        logger.error(f"delete_report failed for {report_id}: {exc}")
        return False


def make_public(
    report_id: str,
    expires_in_days: int = 30,
    *,
    password: str | None = None,
    user_id: str | None = None,
) -> str | None:
    """Generate a public-share slug for *report_id* and persist it.

    The slug is a URL-safe base64url token from
    ``secrets.token_urlsafe(12)`` (16-char string) — short enough to be
    pasted into a chat or email, long enough to be unguessable, and
    carrying no information about the internal ``report_id`` or
    ``file_path``. The expiry is stored as an ISO-8601 UTC timestamp
    and enforced on read by :func:`load_public_report`.

    When ``password`` is provided and non-empty, an additional layer
    of protection is added: the link still requires the unguessable
    slug, BUT the viewer must also supply the password before the
    report renders. The password is hashed via
    :func:`_hash_public_password` (PBKDF2-HMAC-SHA256, 200_000
    iterations, random salt) and ONLY the hex hash + hex salt are
    persisted — the plaintext is never written to disk or logged.
    When ``password`` is ``None`` or empty, the password columns are
    cleared (NULL), preserving the v5 "slug is sufficient" behaviour.

    Args:
        report_id:        The internal UUID of the report to share.
        expires_in_days:  Lifetime of the link in days. Must be > 0.
        password:         Optional plaintext password. When ``None``
                          or empty, no password protection is applied.
        user_id:          Optional explicit owner id. ``None`` resolves
                          to the active Streamlit user via
                          ``current_user_id``.

    Returns:
        The newly-generated slug on success, or ``None`` if the report
        cannot be found, ``expires_in_days`` is non-positive, or
        anything else goes wrong (this function never raises).
    """
    try:
        if expires_in_days <= 0:
            return None

        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        # Scope the lookup so only the report's owner (or a legacy
        # report under the empty-string user_id) can publish it. A
        # non-owner gets the same None return as an unknown report_id.
        row = conn.execute(
            f"SELECT report_id FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"make_public: report_id not found: {report_id}")
            return None

        slug = secrets.token_urlsafe(12)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        ).isoformat()

        # Derive password hash + salt up-front so the plaintext lives
        # only as a stack-local variable for the duration of this call
        # — never touches the DB, the audit row, or any log line.
        pw_hash_hex: str | None = None
        pw_salt_hex: str | None = None
        if password:
            pw_hash_hex, pw_salt_hex = _hash_public_password(password)

        with conn:
            conn.execute(
                """
                UPDATE report_history
                   SET public_slug = ?,
                       public_expires_at = ?,
                       public_password_hash = ?,
                       public_password_salt = ?
                 WHERE report_id = ?
                """,
                (slug, expires_at, pw_hash_hex, pw_salt_hex, report_id),
            )
        # Audit-log the share-link generation. The slug itself is NOT
        # logged — a stolen audit row should not give an attacker the
        # working URL. expires_in_days is the only payload field; it's
        # what a security review actually wants to see ("user shared
        # report X with a 30-day link"). ``password_protected`` is a
        # boolean — we never log the plaintext or the hash.
        try:
            from auth.audit import record_audit
            record_audit(
                "make_public",
                entity_type="report",
                entity_id=report_id,
                detail={
                    "expires_in_days": expires_in_days,
                    "password_protected": bool(password),
                },
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return slug
    except Exception as exc:
        logger.error(f"make_public failed for {report_id}: {exc}")
        return None


def revoke_public(report_id: str, *, user_id: str | None = None) -> bool:
    """Clear the public-share slug + expiry for *report_id*.

    After revoke, any prior call to :func:`load_public_report` with the
    old slug returns ``None`` (slug not found).

    Honours per-user scoping — only the report's owner (or the legacy
    ``user_id=''`` owner) can revoke its public link. A non-owner gets
    the same ``False`` return as an unknown report_id.

    Returns:
        True if a row was updated, False if the report_id is unknown or
        anything else goes wrong (this function never raises).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT report_id FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"revoke_public: report_id not found: {report_id}")
            return False

        with conn:
            conn.execute(
                """
                UPDATE report_history
                   SET public_slug = '',
                       public_expires_at = '',
                       public_password_hash = NULL,
                       public_password_salt = NULL
                 WHERE report_id = ?
                """,
                (report_id,),
            )
        # Audit-log the revoke. Same pattern as make_public —
        # entity_type 'report' so a user can see "I shared X then
        # revoked X" in chronological order during a review.
        try:
            from auth.audit import record_audit
            record_audit(
                "revoke_public",
                entity_type="report",
                entity_id=report_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:
        logger.error(f"revoke_public failed for {report_id}: {exc}")
        return False


def load_public_report(slug: str, password: str | None = None) -> str | None:
    """Return the HTML for the report identified by *slug*, if the link is valid.

    Validity requires all of:
      * ``slug`` is non-empty.
      * A ``report_history`` row exists with that ``public_slug``.
      * ``public_expires_at`` parses as a future ISO-8601 UTC timestamp.
      * The on-disk HTML file referenced by ``file_path`` still exists.
      * **If the row carries a non-empty ``public_password_hash``**,
        ``password`` must be supplied AND must verify against the
        stored hash via :func:`_verify_public_password`. A missing or
        wrong password collapses to the same ``None`` return as an
        unknown slug — no distinction is surfaced so a probing client
        cannot enumerate which slugs require a password by status
        code alone. When the row has no password (NULL / empty hash),
        the ``password`` argument is ignored and the behaviour matches
        the pre-v17 path exactly.

    Returns:
        The HTML string on success, or ``None`` for any failure
        condition (slug unknown / empty / expired / file deleted /
        unreadable / missing-or-wrong password). Never raises.
    """
    try:
        if not slug:
            return None

        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            """
            SELECT file_path, public_slug, public_expires_at,
                   public_password_hash, public_password_salt
              FROM report_history
             WHERE public_slug = ?
            """,
            (slug,),
        ).fetchone()
        if row is None:
            logger.debug(f"load_public_report: slug not found: {slug}")
            return None

        # Defensive — a row where the slug column happened to be empty
        # would have been matched by ``WHERE public_slug = ''``. Treat
        # that as "not shared" and refuse.
        if not row["public_slug"]:
            return None

        expires_at_raw = row["public_expires_at"] or ""
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            logger.debug(
                f"load_public_report: bad expires_at for slug {slug}: "
                f"{expires_at_raw!r}"
            )
            return None
        # Normalise to UTC-aware so we can compare against a
        # timezone-aware "now" without raising.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            logger.debug(f"load_public_report: slug expired: {slug}")
            return None

        # Password gate. ``public_password_hash`` is NULL (or empty)
        # for legacy / unprotected reports; only enforce when both the
        # hash AND the salt are present. The two-column AND avoids a
        # half-migrated row from accidentally locking out viewers.
        try:
            stored_hash = row["public_password_hash"]
        except (IndexError, KeyError):
            stored_hash = None
        try:
            stored_salt = row["public_password_salt"]
        except (IndexError, KeyError):
            stored_salt = None
        if stored_hash and stored_salt:
            if not password:
                logger.debug(
                    f"load_public_report: password required for slug {slug}"
                )
                return None
            if not _verify_public_password(password, stored_hash, stored_salt):
                logger.debug(
                    f"load_public_report: wrong password for slug {slug}"
                )
                return None

        path = Path(row["file_path"])
        if not path.exists():
            logger.debug(f"load_public_report: file missing for slug {slug}")
            return None
        html = path.read_text(encoding="utf-8")
        # Audit-log the public (anonymous) access. Placed AFTER every gate
        # passes (slug found, not expired, password ok, file present) so a
        # probe that fails any check does NOT generate an audit row. The
        # accessing user is recorded honestly as ``""`` — a public-link
        # viewer has no authenticated identity. The slug is NEVER written
        # to the audit row (mirrors ``make_public``: a stolen audit row
        # must not hand an attacker the working URL); ``password_protected``
        # is the only payload field, which is what a review wants to see.
        try:
            from auth.audit import record_audit
            record_audit(
                "read_public_report",
                entity_type="report",
                detail={"password_protected": bool(stored_hash and stored_salt)},
                user_id="",
            )
        except Exception:  # noqa: BLE001
            pass
        return html
    except Exception as exc:
        logger.error(f"load_public_report failed for slug {slug!r}: {exc}")
        return None


def verify_public_report_password(slug: str, password: str) -> bool:
    """Return True iff ``password`` unlocks the public link at ``slug``.

    Semantics:
      * Slug unknown / empty / expired → ``False``. Never raises.
      * Slug found AND no password set on the row → ``True`` (the
        link is open; any caller asking should be allowed through).
      * Slug found AND password set → constant-time compare via
        :func:`_verify_public_password`.

    This helper exists so a UI / API layer can pre-check a password
    without forcing a full HTML load. The viewer endpoint can still
    call :func:`load_public_report` directly — both paths apply the
    same gate.
    """
    try:
        if not slug:
            return False

        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            """
            SELECT public_slug, public_expires_at,
                   public_password_hash, public_password_salt
              FROM report_history
             WHERE public_slug = ?
            """,
            (slug,),
        ).fetchone()
        if row is None or not row["public_slug"]:
            return False

        expires_at_raw = row["public_expires_at"] or ""
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return False

        try:
            stored_hash = row["public_password_hash"]
        except (IndexError, KeyError):
            stored_hash = None
        try:
            stored_salt = row["public_password_salt"]
        except (IndexError, KeyError):
            stored_salt = None
        if not stored_hash or not stored_salt:
            # No password set → viewing is permitted with or without
            # one; "verify" of any candidate trivially succeeds.
            return True
        if password is None:
            return False
        return _verify_public_password(password, stored_hash, stored_salt)
    except Exception as exc:
        logger.error(
            f"verify_public_report_password failed for slug {slug!r}: {exc}"
        )
        return False


def get_report_stats(*, user_id: str | None = None) -> dict:
    """Return aggregate statistics across all saved reports.

    Honours per-user scoping via the underlying ``list_reports`` call —
    when ``user_id`` is non-empty, the stats only cover that user's
    rows plus legacy ``user_id=''`` rows.

    Returns a dict with keys:
        total_reports, total_size_mb, oldest_date, newest_date,
        avg_sentiment_score, sentiment_distribution
    """
    try:
        # Forward the scope parameter so the stats match whatever
        # list_reports would have surfaced for the same user.
        entries = list_reports(user_id=user_id)  # already filtered + sorted newest-first
        if not entries:
            return {
                "total_reports": 0,
                "total_size_mb": 0.0,
                "oldest_date": None,
                "newest_date": None,
                "avg_sentiment_score": 0.0,
                "sentiment_distribution": {},
            }

        total_size_mb = round(sum(e.file_size_kb for e in entries) / 1024, 3)
        dates = sorted(e.generated_at for e in entries)
        avg_score = round(
            sum(e.sentiment_score for e in entries) / len(entries), 4
        )
        distribution: dict[str, int] = {}
        for e in entries:
            distribution[e.sentiment_label] = (
                distribution.get(e.sentiment_label, 0) + 1
            )

        return {
            "total_reports": len(entries),
            "total_size_mb": total_size_mb,
            "oldest_date": dates[0],
            "newest_date": dates[-1],
            "avg_sentiment_score": avg_score,
            "sentiment_distribution": distribution,
        }
    except Exception as exc:
        logger.error(f"get_report_stats failed: {exc}")
        return {
            "total_reports": 0,
            "total_size_mb": 0.0,
            "oldest_date": None,
            "newest_date": None,
            "avg_sentiment_score": 0.0,
            "sentiment_distribution": {},
        }


# ---------------------------------------------------------------------------
# Immutable version / supersede-amend lineage (R119)
# ---------------------------------------------------------------------------


def amend_report(
    original_report_id: str,
    new_html: str,
    *,
    user_id: str | None = None,
    reason: str = "",
) -> str | None:
    """Mint a NEW immutable report row that supersedes *original_report_id*.

    A report is never edited in place. Instead an amendment writes a fresh
    ``report_history`` row (new ``report_id``, new on-disk HTML file) whose
    ``report_version`` is ``original.report_version + 1`` and whose
    ``supersedes_id`` points back at *original_report_id*. The original row
    is left COMPLETELY untouched, so the full version chain stays auditable
    via :func:`version_chain`.

    The new row inherits the original's metadata (sentiment, risk, etc.) —
    an amendment is a re-issue of the SAME report with corrected/updated
    content, not a freshly-computed report — except for ``generated_at``
    (stamped now), the file path (new file), and the lineage columns. The
    new row is owned by the same user as the original (per-user scoping is
    honoured on the lookup), so an amendment can only be made within the
    caller's own scope.

    Args:
        original_report_id: The report_id of the version being amended.
        new_html:           The corrected/updated HTML for the new version.
        user_id:            Optional explicit owner id. ``None`` resolves to
                            the active Streamlit user via ``current_user_id``.
        reason:             Free-form amendment reason, recorded on the audit
                            event (best-effort). Not persisted as a column.

    Returns:
        The new report_id on success, or ``None`` if the original cannot be
        found in scope or anything else goes wrong (never raises).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        conn = get_connection()
        # Load the original row in-scope. A non-owner gets the same None
        # return as an unknown report_id — no cross-scope amend.
        orig = conn.execute(
            f"SELECT * FROM report_history WHERE report_id = ? {scope_sql}",
            (original_report_id, *scope_params),
        ).fetchone()
        if orig is None:
            logger.debug(f"amend_report: original not found: {original_report_id}")
            return None

        orig_meta = _row_to_meta(orig)
        # The row's owner stamp drives the new row's owner so the amendment
        # lands in the same scope as the report it supersedes.
        try:
            orig_uid = orig["user_id"]
        except (IndexError, KeyError):
            orig_uid = uid

        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp_str}_{new_id[:8]}.html"
        file_path = REPORT_DIR / filename
        file_path.write_text(new_html, encoding="utf-8")
        file_size_kb = round(file_path.stat().st_size / 1024, 2)

        new_version = int(orig_meta.report_version) + 1
        new_meta = ReportMeta(
            report_id=new_id,
            generated_at=now.isoformat(),
            report_date=orig_meta.report_date,
            sentiment_label=orig_meta.sentiment_label,
            sentiment_score=orig_meta.sentiment_score,
            risk_level=orig_meta.risk_level,
            signal_count=orig_meta.signal_count,
            data_quality=orig_meta.data_quality,
            file_path=str(file_path.resolve()),
            file_size_kb=file_size_kb,
            report_version=new_version,
            supersedes_id=original_report_id,
        )

        # NB: we deliberately DO NOT touch the original row here — it stays
        # immutable. We only INSERT the new version.
        _insert_report_row(
            conn, new_meta, orig_uid,
            report_version=new_version,
            supersedes_id=original_report_id,
        )

        # Audit-log the amendment (best-effort). The chain link + reason are
        # the payload a reviewer wants ("v2 of X, because ...").
        try:
            from auth.audit import record_audit
            record_audit(
                "amend_report",
                entity_type="report",
                entity_id=new_id,
                detail={
                    "supersedes_id": original_report_id,
                    "report_version": new_version,
                    "reason": (reason or "")[:200],
                },
                user_id=orig_uid,
            )
        except Exception:  # noqa: BLE001
            pass

        # Pruning is intentionally NOT triggered here: an amendment is a
        # deliberate audit action and we don't want the freshly-minted
        # version (or its predecessor) silently pruned out from under a
        # lineage the caller just created.
        logger.info(
            f"Report amended: {original_report_id} -> {new_id} "
            f"(v{new_version})"
        )
        return new_id
    except Exception as exc:
        logger.error(f"amend_report failed for {original_report_id}: {exc}")
        return None


def reissue_report(
    original_report_id: str,
    *,
    user_id: str | None = None,
    reason: str = "",
) -> str | None:
    """Re-issue *original_report_id* as a NEW version with the SAME content.

    A reissue is an :func:`amend_report` where the content is unchanged —
    the same HTML is re-stamped as version N+1 (e.g. a re-fire of an
    identical briefing under a new immutable id). It loads the original's
    on-disk HTML and delegates to :func:`amend_report`, so the lineage
    semantics (new row, ``supersedes_id`` link, original untouched) are
    identical. If the original's HTML cannot be read, returns ``None``.

    Returns the new report_id on success, or ``None`` on any failure
    (never raises).
    """
    try:
        html = load_report_html(original_report_id, user_id=user_id)
        if html is None:
            logger.debug(
                f"reissue_report: could not load HTML for {original_report_id}"
            )
            return None
        return amend_report(
            original_report_id,
            html,
            user_id=user_id,
            reason=reason or "reissue",
        )
    except Exception as exc:
        logger.error(f"reissue_report failed for {original_report_id}: {exc}")
        return None


def version_chain(
    report_id: str,
    *,
    user_id: str | None = None,
) -> list[ReportMeta]:
    """Return the full version lineage for *report_id*, oldest → newest.

    Given any report in a chain, walks BOTH directions:
      * backwards via ``supersedes_id`` (this row → its predecessor → ...)
        until it reaches the original (NULL/empty ``supersedes_id``);
      * forwards by finding the row whose ``supersedes_id`` equals each
        known id, until no successor exists.

    The returned list is ordered oldest-first (version 1 → latest). A v1
    report with no supersedes and no successor yields a single-element
    chain. Honours per-user scoping: only rows in the caller's scope are
    traversed (a missing in-scope link simply truncates the walk).

    Cycle-guarded: a ``supersedes_id`` that loops back to an already-seen
    id terminates the walk rather than spinning forever. Returns an empty
    list if *report_id* itself is not found in scope, or on any error
    (never raises).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()

        def _fetch(rid: str):
            return conn.execute(
                f"SELECT * FROM report_history WHERE report_id = ? {scope_sql}",
                (rid, *scope_params),
            ).fetchone()

        start = _fetch(report_id)
        if start is None:
            logger.debug(f"version_chain: report_id not found: {report_id}")
            return []

        seen: set[str] = set()
        by_id: dict[str, ReportMeta] = {}

        # Walk backwards (newest known → oldest) following supersedes_id.
        cur = start
        while cur is not None:
            rid = cur["report_id"]
            if rid in seen:  # cycle guard
                break
            seen.add(rid)
            by_id[rid] = _row_to_meta(cur)
            try:
                prev_id = cur["supersedes_id"]
            except (IndexError, KeyError):
                prev_id = None
            if not prev_id or prev_id in seen:
                break
            cur = _fetch(prev_id)

        # Walk forwards (each known id → the row that supersedes it).
        frontier = list(seen)
        while frontier:
            parent_id = frontier.pop()
            succ = conn.execute(
                f"SELECT * FROM report_history "
                f"WHERE supersedes_id = ? {scope_sql}",
                (parent_id, *scope_params),
            ).fetchall()
            for row in succ:
                rid = row["report_id"]
                if rid in seen:  # cycle guard
                    continue
                seen.add(rid)
                by_id[rid] = _row_to_meta(row)
                frontier.append(rid)

        # Order oldest → newest: primarily by report_version, then by
        # generated_at as a tie-breaker (defensive against duplicate
        # version numbers in a malformed chain).
        chain = sorted(
            by_id.values(),
            key=lambda m: (int(m.report_version), str(m.generated_at)),
        )
        return chain
    except Exception as exc:
        logger.error(f"version_chain failed for {report_id}: {exc}")
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _insert_report_row(
    conn,
    meta: "ReportMeta",
    uid: str,
    *,
    report_version: int = 1,
    supersedes_id: "str | None" = None,
) -> None:
    """Insert one fully-formed report row, including the v34 lineage columns.

    Shared by :func:`save_report` (fresh report → version 1, no
    predecessor) and :func:`amend_report` (mints version N+1 linked to its
    predecessor via ``supersedes_id``). The lineage columns are written
    explicitly so every row has an identical shape regardless of which
    path created it.

    ``supersedes_id`` is stored as SQL NULL for an original report (the
    "head of chain" marker) and as the predecessor's ``report_id`` for an
    amendment. The write runs inside ``with conn:`` so it is atomic.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb, user_id,
               report_version, supersedes_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (meta.report_id, meta.generated_at, meta.report_date,
             meta.sentiment_label, meta.sentiment_score, meta.risk_level,
             meta.signal_count, meta.data_quality, meta.file_path,
             meta.file_size_kb, uid,
             int(report_version), supersedes_id),
        )


def _prune_old_reports() -> None:
    """Keep only the MAX_REPORTS most recent rows; delete pruned files."""
    try:
        from state.db import get_connection

        conn = get_connection()
        # Find rows beyond the MAX_REPORTS newest
        rows = conn.execute(
            """
            SELECT report_id, file_path FROM report_history
            ORDER BY generated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (MAX_REPORTS,),
        ).fetchall()

        if not rows:
            return

        with conn:
            conn.executemany(
                "DELETE FROM report_history WHERE report_id = ?",
                [(r["report_id"],) for r in rows],
            )

        # Delete pruned files from disk (best-effort).
        for r in rows:
            try:
                path = Path(r["file_path"])
                if path.exists():
                    path.unlink()
                    logger.debug(f"Pruned old report: {path.name}")
            except Exception as exc:
                logger.warning(f"Could not delete pruned report {r['file_path']}: {exc}")
    except Exception as exc:
        logger.error(f"_prune_old_reports failed: {exc}")


def prune_old_reports(keep_n: int | None = None) -> int:
    """Public wrapper around ``_prune_old_reports`` for CLI / worker calls.

    Returns the count of rows that were pruned. Counts before + after
    the call to compute the delta; the underlying helper returns None
    so a row-count would otherwise require duplicating the SELECT.

    Args:
        keep_n: optional override for ``MAX_REPORTS``. When None (the
                default), uses the module-level constant.

    Returns:
        Number of rows pruned (0 on no-op or any error). Never raises.
    """
    try:
        from state.db import get_connection
        global MAX_REPORTS

        before = get_connection().execute(
            "SELECT COUNT(*) AS n FROM report_history"
        ).fetchone()["n"]
        if keep_n is not None:
            original = MAX_REPORTS
            MAX_REPORTS = int(keep_n)
            try:
                _prune_old_reports()
            finally:
                MAX_REPORTS = original
        else:
            _prune_old_reports()
        after = get_connection().execute(
            "SELECT COUNT(*) AS n FROM report_history"
        ).fetchone()["n"]
        return max(0, int(before) - int(after))
    except Exception as exc:
        logger.warning(f"prune_old_reports: failed: {exc}")
        return 0


# ---------------------------------------------------------------------------
# Attribute extraction helpers
# ---------------------------------------------------------------------------

def _attr(obj: object, name: str, default: "Any") -> "Any":
    """Return getattr(obj, name, default), also checking dict-style access."""
    try:
        if hasattr(obj, name):
            val = getattr(obj, name)
            return val if val is not None else default
        if isinstance(obj, dict):
            return obj.get(name, default)
    except Exception:
        pass
    return default


def _safe_float(val: "Any", default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: "Any", default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
