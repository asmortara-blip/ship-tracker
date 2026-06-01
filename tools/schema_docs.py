"""Auto-generated DB schema documentation for the Ship Tracker app.

Before this module existed, the only place to learn the SQLite schema was
to scrape ``state/db.py`` comments + every ``_migrate_to_vN`` function in
``state/migrations.py`` plus the commit messages around each schema bump.
This module replaces that scavenger hunt with three pure functions that
introspect a live DB and render the result as Markdown or JSON.

Public surface
--------------
``introspect_schema(db_path=None)``
    Open ``db_path`` (or fall back to ``state.db.DB_PATH``) read-only
    and return a structured dict carrying schema_version, every table's
    column/index/foreign-key metadata, and the snapshot row count per
    table. On any failure the dict carries an ``error`` key and never
    raises.

``render_schema_markdown(schema_dict)``
    Render the introspected dict as Markdown — a header block with
    counts + generated-at timestamp, a table-of-contents, and one
    section per table holding columns / indexes / foreign keys.

``schema_history_from_migrations()``
    Parse ``state/migrations.py`` via the stdlib ``ast`` module and
    extract one entry per ``_migrate_to_vN`` function: the version
    number, the function name, the first paragraph of its docstring
    as a summary, and any string-literal SQL excerpts found in the
    function body.

Per-column descriptions
-----------------------
A hand-curated ``COLUMN_DESCRIPTIONS`` map carries short human-written
descriptions for the most important columns (alert_id, user_id, …).
Anything not in the map renders as an empty string in the Description
column — keep the map intentionally small so it stays maintainable.

Determinism
-----------
The introspected dict sorts tables, indexes, and FKs alphabetically /
by ordinal so the rendered Markdown is byte-stable across runs (the
only variation is the ``Generated`` timestamp in the header). That
keeps ``docs/SCHEMA.md`` git-diffs meaningful — a real schema change
shows up, a regeneration with no change does not.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─── Module-level constants ────────────────────────────────────────────────

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Hand-curated per-column descriptions. Keep this map intentionally small
# — only the columns where the type / name does not already explain the
# meaning. Anything missing renders as an empty Description cell.
COLUMN_DESCRIPTIONS: dict[tuple[str, str], str] = {
    # alerts
    ("alerts", "alert_id"): "UUID primary key for the alert row.",
    ("alerts", "alert_type"): "Category bucket — MACRO, MICRO, FREIGHT, etc.",
    ("alerts", "severity"): "LOW / MEDIUM / HIGH / CRITICAL.",
    ("alerts", "acknowledged_at"): "ISO-8601 UTC stamp set when an operator acks the alert.",
    ("alerts", "acknowledged_note"): "Free-form note attached at ack time. NULL when no note.",
    ("alerts", "acknowledged_by_user_id"): "user_id of the operator who acked the alert.",
    ("alerts", "fire_count"): "How many times this dedup-key tuple has fired in the dedup window.",
    ("alerts", "last_fired_at"): "ISO-8601 UTC stamp of the most-recent fire (for window dedup).",
    ("alerts", "rule_id"): "Originating AlertRule id (NULL on detection-path alerts).",
    ("alerts", "last_escalated_at"): "ISO-8601 stamp of the most-recent escalation step fire.",
    ("alerts", "escalation_step"): "Which step of the rule's escalation chain has fired so far.",

    # users
    ("users", "user_id"): "UUID primary key for the user account.",
    ("users", "username"): "Login username — UNIQUE per the v7 index.",
    ("users", "password_hash"): "Hex-encoded scrypt-with-PBKDF2-fallback hash.",
    ("users", "password_salt"): "Per-user random salt; pairs with password_hash.",
    ("users", "role"): "'admin' or 'user' — gates admin-only routes.",
    ("users", "mfa_secret"): "Base32 TOTP secret (empty when MFA disabled).",
    ("users", "mfa_enabled"): "0/1 — when 1, login requires a valid TOTP code.",

    # alert_rules
    ("alert_rules", "rule_id"): "UUID primary key for the alert rule.",
    ("alert_rules", "cooldown_minutes"): "Suppress repeat fires of this rule for N minutes (0 = no cooldown).",

    # delivery_channels
    ("delivery_channels", "channel_id"): "UUID primary key for the channel.",
    ("delivery_channels", "kind"): "Channel transport — slack / email / sms / pagerduty / webhook / opsgenie.",
    ("delivery_channels", "target"): "Endpoint string — webhook URL, email address, phone number, etc.",
    ("delivery_channels", "digest_mode"): "'immediate' (one delivery per alert) or 'daily' (batched digest).",
    ("delivery_channels", "quiet_start"): "HH:MM UTC start of the quiet window (empty = no window).",
    ("delivery_channels", "quiet_end"): "HH:MM UTC end of the quiet window.",
    ("delivery_channels", "quiet_override_critical"): "0/1 — when 1, CRITICAL alerts always deliver during quiet hours.",

    # report_history
    ("report_history", "report_id"): "UUID primary key for the persisted report row.",
    ("report_history", "public_slug"): "URL-safe token for a read-only public share link (empty when not shared).",
    ("report_history", "public_expires_at"): "ISO-8601 UTC; the public link is valid only while in the future.",
    ("report_history", "public_password_hash"): "pbkdf2-sha256 hash of the optional public-link password (NULL when none).",
    ("report_history", "public_password_salt"): "Random salt paired with public_password_hash.",

    # audit_events
    ("audit_events", "event_id"): "UUID primary key for the audit row.",
    ("audit_events", "action"): "Verb describing the action — login, ack, rule_create, etc.",

    # api_tokens
    ("api_tokens", "token_id"): "UUID primary key for the PAT row.",
    ("api_tokens", "token_hash"): "Hashed-and-salted token; raw secret returned once at creation.",
    ("api_tokens", "token_prefix"): "First 8 chars of the plaintext token — indexed for O(log n) lookup.",
    ("api_tokens", "revoked"): "0/1 — when 1, the token can no longer authenticate.",

    # kv_state
    ("kv_state", "key"): "Lookup key — e.g. 'schema_version', 'last_alert_prune_at'.",
    ("kv_state", "value"): "Free-form string value (JSON-encoded for complex types).",
}


# ─── Helpers ───────────────────────────────────────────────────────────────


def _resolve_default_db_path() -> Path:
    """Look up the live DB path through state.db so tests that
    monkeypatch ``state.db.DB_PATH`` see the redirected location."""
    try:
        from state import db as state_db
        return Path(state_db.DB_PATH)
    except Exception:  # noqa: BLE001
        return _PROJECT_ROOT / "cache" / "ship_tracker.db"


def _live_schema_version() -> int:
    """Best-effort lookup of the running code's expected schema version."""
    try:
        from state import db as state_db
        return int(getattr(state_db, "SCHEMA_VERSION", 0))
    except Exception:  # noqa: BLE001
        return 0


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open ``db_path`` read-only via the SQLite URI flag.

    Raises sqlite3.OperationalError when the file is missing — the
    caller's introspect_schema catches that and returns the error
    dict shape rather than propagating.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _read_kv_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """Read kv_state.value WHERE key='schema_version'. Returns None when
    no row yet (legacy pre-v1) and on any error."""
    try:
        cur = conn.execute(
            "SELECT value FROM kv_state WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return int(row[0])
    except Exception:  # noqa: BLE001
        return None


# ─── Introspection ────────────────────────────────────────────────────────


def introspect_schema(db_path: Optional[Path] = None) -> dict:
    """Open the DB and return a structured dict.

    Shape:

        {
          'schema_version': N,                # from kv_state, None when missing
          'expected_schema_version': N,       # from state.db.SCHEMA_VERSION
          'db_path': '/abs/path/to/db',
          'introspected_at': '<ISO>',
          'tables': {
              'alerts': {
                  'columns':      [{name, type, nullable, default, pk}, ...],
                  'indexes':      [{name, unique, columns}, ...],
                  'foreign_keys': [{from, to_table, to_column, on_delete}, ...],
                  'row_count':    N,
              },
              ...
          },
        }

    On error (missing file, broken open, …) returns:

        {'error': '<message>', 'db_path': '<path>', 'tables': {}}

    Never raises.
    """
    path = Path(db_path) if db_path is not None else _resolve_default_db_path()
    if not path.exists():
        return {
            "error": f"DB not found at {path}",
            "db_path": str(path),
            "tables": {},
            "introspected_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        conn = _open_ro(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"cannot open DB at {path}: {exc}",
            "db_path": str(path),
            "tables": {},
            "introspected_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        # Enumerate tables. Skip the SQLite internal sqlite_* prefix —
        # those are bookkeeping (sqlite_sequence for autoincrement, etc.)
        # and would clutter the docs.
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        table_names = [r["name"] for r in cur.fetchall()]

        tables: dict[str, dict[str, Any]] = {}
        for tbl in table_names:
            tables[tbl] = _introspect_table(conn, tbl)

        return {
            "schema_version": _read_kv_schema_version(conn),
            "expected_schema_version": _live_schema_version(),
            "db_path": str(path),
            "introspected_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"introspection failed: {exc}",
            "db_path": str(path),
            "tables": {},
            "introspected_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _introspect_table(conn: sqlite3.Connection, table: str) -> dict:
    """Run PRAGMA table_info / index_list / index_info / foreign_key_list
    against ``table`` and bundle the results into the per-table dict
    shape documented on ``introspect_schema``."""
    # ``PRAGMA table_info`` returns (cid, name, type, notnull, dflt_value, pk)
    cur = conn.execute(f"PRAGMA table_info({_quote_ident(table)})")
    columns = []
    for row in cur.fetchall():
        columns.append({
            "name":     str(row[1]),
            "type":     str(row[2]),
            "nullable": int(row[3]) == 0,   # notnull=0 means nullable
            "default":  row[4],             # may be None
            "pk":       int(row[5]) > 0,    # composite-PK ordinal > 0 too
        })

    # ``PRAGMA index_list`` returns (seq, name, unique, origin, partial)
    # We pull the column list for each index via index_info.
    cur = conn.execute(f"PRAGMA index_list({_quote_ident(table)})")
    raw_indexes = cur.fetchall()
    indexes = []
    for ix_row in raw_indexes:
        ix_name = str(ix_row[1])
        unique = int(ix_row[2]) == 1
        # Skip the implicit indexes SQLite creates for UNIQUE columns —
        # they are named sqlite_autoindex_* and add no information beyond
        # the column constraint itself.
        if ix_name.startswith("sqlite_autoindex_"):
            continue
        # ``PRAGMA index_info(name)`` returns (seqno, cid, name)
        cur2 = conn.execute(f"PRAGMA index_info({_quote_ident(ix_name)})")
        ix_cols = [str(r[2]) for r in cur2.fetchall()]
        indexes.append({
            "name":    ix_name,
            "unique":  unique,
            "columns": ix_cols,
        })
    indexes.sort(key=lambda d: d["name"])

    # ``PRAGMA foreign_key_list`` returns (id, seq, table, from, to,
    # on_update, on_delete, match)
    cur = conn.execute(f"PRAGMA foreign_key_list({_quote_ident(table)})")
    foreign_keys = []
    for row in cur.fetchall():
        foreign_keys.append({
            "from":      str(row[3]),
            "to_table":  str(row[2]),
            "to_column": str(row[4]),
            "on_delete": str(row[6]) if row[6] is not None else "",
        })
    foreign_keys.sort(key=lambda d: (d["from"], d["to_table"]))

    # Row count — best-effort, fall back to -1 on any read error so the
    # docs say "?" rather than crashing on a partial / locked DB.
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}")
        row_count = int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001
        row_count = -1

    return {
        "columns":      columns,
        "indexes":      indexes,
        "foreign_keys": foreign_keys,
        "row_count":    row_count,
    }


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier defensively. SQLite identifiers in this
    project are all snake_case ASCII, but quoting protects against a
    pathological table name discovered via sqlite_master (e.g. one
    containing a reserved word)."""
    return '"' + str(name).replace('"', '""') + '"'


# ─── Markdown rendering ───────────────────────────────────────────────────


def render_schema_markdown(schema_dict: dict) -> str:
    """Render the introspected schema as Markdown.

    Returns a single string suitable for writing to docs/SCHEMA.md. The
    output is byte-stable for the same input (modulo the timestamp in
    the header), which keeps the generated file's git-diff meaningful.
    """
    if not isinstance(schema_dict, dict):
        return "# Ship Tracker - Database Schema\n\n_Error: invalid schema dict._\n"

    lines: list[str] = []
    lines.append("# Ship Tracker - Database Schema")
    lines.append("")

    # An error dict short-circuits to a one-line body. We still emit the
    # title so consumers grepping for the heading find something.
    if schema_dict.get("error"):
        lines.append(f"**Error:** {schema_dict['error']}")
        lines.append("")
        lines.append(f"**DB path:** `{schema_dict.get('db_path', '?')}`")
        return "\n".join(lines) + "\n"

    schema_v = schema_dict.get("schema_version")
    expected_v = schema_dict.get("expected_schema_version")
    introspected_at = schema_dict.get("introspected_at", "")
    tables: dict[str, dict] = dict(schema_dict.get("tables", {}))
    total_tables = len(tables)
    total_rows = sum(
        max(0, int(t.get("row_count") or 0)) for t in tables.values()
    )

    lines.append(f"**Schema version:** {schema_v if schema_v is not None else '(not set)'}  ")
    if expected_v is not None and expected_v != schema_v:
        lines.append(f"**Expected (running code):** {expected_v}  ")
    lines.append(f"**Generated:** {introspected_at}  ")
    lines.append(f"**Total tables:** {total_tables}  ")
    lines.append(f"**Total rows (this snapshot):** {total_rows}  ")
    lines.append(f"**DB path:** `{schema_dict.get('db_path', '?')}`")
    lines.append("")

    if total_tables == 0:
        lines.append("_No tables in this database._")
        lines.append("")
        return "\n".join(lines) + "\n"

    # Table of contents — alphabetical for determinism.
    ordered_names = sorted(tables.keys())
    lines.append("## Table of contents")
    lines.append("")
    for name in ordered_names:
        # GitHub-flavoured anchor — lowercase, spaces → dashes. Our table
        # names are snake_case already so the anchor is just the name.
        lines.append(f"- [{name}](#{name})")
    lines.append("")

    # Per-table sections.
    for name in ordered_names:
        lines.extend(_render_table_section(name, tables[name]))

    return "\n".join(lines) + "\n"


def _render_table_section(name: str, tbl: dict) -> list[str]:
    """One table's Markdown block: heading, row count, column table,
    indexes, foreign keys. Returns the lines so the caller can extend
    its own list."""
    lines: list[str] = []
    lines.append(f"## {name}")
    lines.append("")

    row_count = tbl.get("row_count", -1)
    if row_count < 0:
        lines.append("_Row count unavailable._")
    elif row_count == 0:
        lines.append("Empty (0 rows).")
    elif row_count == 1:
        lines.append("1 row.")
    else:
        lines.append(f"{row_count} rows.")
    lines.append("")

    cols = list(tbl.get("columns", []))
    if cols:
        lines.append("| Column | Type | Nullable | Default | PK | Description |")
        lines.append("|---|---|---|---|---|---|")
        for c in cols:
            default = c.get("default")
            default_str = "-" if default is None else f"`{default}`"
            nullable_str = "yes" if c.get("nullable") else "no"
            pk_str = "yes" if c.get("pk") else "-"
            desc = COLUMN_DESCRIPTIONS.get((name, c.get("name", "")), "")
            lines.append(
                f"| `{c.get('name', '')}` | {c.get('type', '') or 'TEXT'} "
                f"| {nullable_str} | {default_str} | {pk_str} | {desc} |"
            )
    else:
        lines.append("_No columns._")
    lines.append("")

    indexes = list(tbl.get("indexes", []))
    if indexes:
        lines.append("**Indexes:**")
        lines.append("")
        for ix in indexes:
            cols_str = ", ".join(ix.get("columns", []))
            unique_str = " UNIQUE" if ix.get("unique") else ""
            lines.append(f"- `{ix.get('name', '')}`{unique_str} ({cols_str})")
        lines.append("")

    fks = list(tbl.get("foreign_keys", []))
    if fks:
        lines.append("**Foreign keys:**")
        lines.append("")
        for fk in fks:
            on_delete = fk.get("on_delete") or ""
            tail = f" ON DELETE {on_delete}" if on_delete and on_delete != "NO ACTION" else ""
            lines.append(
                f"- `{fk.get('from', '')}` -> `{fk.get('to_table', '')}"
                f"({fk.get('to_column', '')})`{tail}"
            )
        lines.append("")

    return lines


# ─── Migration history (source-parse of state/migrations.py) ──────────────


_MIGRATION_NAME_RE = re.compile(r"^_migrate_to_v(\d+)$")
# Match SQL-ish keywords at the start of a stripped string-literal. We
# pull these literally from the source so future migrations that add
# CREATE INDEX / ALTER TABLE / etc. snippets land in the excerpt list
# automatically.
_SQL_HEAD_RE = re.compile(
    r"^\s*(CREATE\s+TABLE|CREATE\s+INDEX|CREATE\s+UNIQUE\s+INDEX|"
    r"ALTER\s+TABLE|DROP\s+TABLE|DROP\s+INDEX|INSERT\s+INTO|"
    r"UPDATE\s+|DELETE\s+FROM|PRAGMA\s+)",
    re.IGNORECASE,
)


def schema_history_from_migrations(
    migrations_path: Optional[Path] = None,
) -> list[dict]:
    """Parse state/migrations.py with the stdlib ``ast`` module and
    return one entry per ``_migrate_to_vN`` function found.

    Each entry shape:

        {
          'version':         17,
          'migration_name':  '_migrate_to_v17',
          'summary':         '<first paragraph of docstring>',
          'sql_excerpts':    ['ALTER TABLE …', 'CREATE INDEX …'],
        }

    Sorted by version ASC so the history reads chronologically. Returns
    an empty list when the file cannot be read (rather than raising)
    so a caller from a partial install still gets a usable shape.
    """
    path = (
        Path(migrations_path)
        if migrations_path is not None
        else _PROJECT_ROOT / "state" / "migrations.py"
    )
    if not path.exists():
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:  # noqa: BLE001
        return []

    entries: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        m = _MIGRATION_NAME_RE.match(node.name)
        if not m:
            continue
        version = int(m.group(1))

        # Docstring summary — first paragraph (split on first blank line).
        docstring = ast.get_docstring(node) or ""
        summary = docstring.split("\n\n", 1)[0].strip()
        # Collapse newlines inside the first paragraph so the entry is
        # one logical line for downstream Markdown rendering.
        summary = " ".join(s.strip() for s in summary.splitlines() if s.strip())

        # SQL excerpts — walk the function body for string literals that
        # look like SQL. The migrations.py pattern is mostly "import the
        # _SCHEMA_VN constant and executescript it" PLUS "conn.execute(...)
        # with a literal SQL string", so we catch both shapes.
        sql_excerpts: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                text = sub.value
                # Multi-line SQL scripts — keep as one entry.
                if _SQL_HEAD_RE.match(text):
                    sql_excerpts.append(text.strip())
            # Older f-strings rendered as ast.JoinedStr with f-string parts.
            # We don't try to reconstruct those — the literal string path
            # covers every migration in state/migrations.py today.

        # Deduplicate while preserving order so the same SQL string
        # appearing twice (in a try / fallback path) shows up once.
        seen: set[str] = set()
        unique_excerpts: list[str] = []
        for ex in sql_excerpts:
            key = ex
            if key in seen:
                continue
            seen.add(key)
            unique_excerpts.append(ex)

        entries.append({
            "version":        version,
            "migration_name": node.name,
            "summary":        summary,
            "sql_excerpts":   unique_excerpts,
        })

    entries.sort(key=lambda d: int(d["version"]))
    return entries


def render_history_markdown(entries: list[dict]) -> str:
    """Render the migration-history list as Markdown.

    One section per entry, sorted by version. Each section shows the
    version, the function name, the summary, and the SQL excerpts in a
    fenced code block (only when present — a migration that imports
    _SCHEMA_VN from state.db has no inline SQL and gets a one-line
    "see state.db._SCHEMA_VN" note via the summary).
    """
    lines: list[str] = []
    lines.append("# Ship Tracker - Migration History")
    lines.append("")
    lines.append(
        f"**Total migrations:** {len(entries)}  "
    )
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    lines.append("")
    lines.append(
        "Source-of-truth: `state/migrations.py`. Each entry is the "
        "docstring + any inline SQL extracted via the stdlib `ast` module. "
        "Schemas declared via `_SCHEMA_VN` constants in `state/db.py` are "
        "documented by their summary; see that module for the literal SQL."
    )
    lines.append("")

    if not entries:
        lines.append("_No migrations found._")
        return "\n".join(lines) + "\n"

    for e in entries:
        lines.append(f"## v{e['version']} - `{e['migration_name']}`")
        lines.append("")
        summary = e.get("summary", "")
        if summary:
            lines.append(summary)
            lines.append("")
        excerpts = e.get("sql_excerpts", [])
        if excerpts:
            lines.append("**SQL excerpts:**")
            lines.append("")
            for ex in excerpts:
                lines.append("```sql")
                lines.append(ex)
                lines.append("```")
                lines.append("")

    return "\n".join(lines) + "\n"
