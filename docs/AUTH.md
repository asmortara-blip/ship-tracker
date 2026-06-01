# Authentication

Ship Tracker has **two coexisting authentication modes**, picked at request
time by `auth.gate.require_auth_with_users()`:

| Mode | Triggered when | What it does |
| --- | --- | --- |
| **Multi-user** | At least one row exists in the `users` table | Renders the login + signup tabs; on success, the authenticated `User` is stored on `st.session_state.current_user`. MFA is enforced per-user when enabled. |
| **Single-password (legacy)** | `users` table is empty **and** `APP_PASSWORD_HASH` + `APP_PASSWORD_SALT` are both set | Delegates to the legacy `require_auth()` — one shared password, no accounts. |
| **Open mode (dev)** | Neither of the above | No login form, full access, one `WARNING` logged at startup. |

The mode is decided dynamically — the first signup auto-promotes the
deployment from single-password to multi-user. You can also run
multi-user from day one by signing up the first admin via the in-app
form; once `count_users() > 0` the single-password path is bypassed.

> **Wiring point**: `app.py` calls `require_auth_with_users()` right
> after `st.set_page_config`. Replacing that call with `require_auth()`
> hard-pins the deployment to single-password mode.

---

## Multi-user mode

### Sign up the first user

When the `users` table is empty, the login form exposes a **Sign Up**
tab. Filling it out and submitting creates the first user with role
`admin` (subsequent signups default to `user`).

The same flow works for additional users — but in a production
deployment you usually want signups gated. See [Invitations](#invitations)
below.

### Login

The login form takes `username` + `password`. On success the
authenticated `User` is stored on `st.session_state.current_user` and
every downstream loader receives the `user_id` for per-user data
scoping.

### Per-user MFA (optional)

Each user can enable **TOTP MFA** from the in-app *My Security* panel:

1. Generate a secret + scan the QR code into an authenticator app
   (1Password, Authy, Google Authenticator — anything that speaks
   RFC 6238).
2. Verify a six-digit code to confirm the secret is provisioned.
3. After enable, the login flow requires the TOTP code on every login.

Recovery codes are issued at enrolment (schema v21) — single-use 8-digit
backup codes for the case where the operator loses their TOTP device.

Implementation: `auth/mfa.py`, stdlib-only (`hmac` + `hashlib`), no
`pyotp` dependency.

### API tokens (programmatic access)

Each user can mint API tokens from *My Security* (schema v11+). Tokens
authenticate against the `worker.api_server` HTTP API on port 8503;
they don't grant Streamlit-app access — that still needs a logged-in
session.

Token rows live in `api_tokens`; tokens can be revoked and have an
optional expiry. The default is no expiry.

### Invitations

To add a teammate without exposing signup publicly, an admin can mint
an **invitation token** (schema v20+):

```bash
python3 -m tools.ops invitations create --email teammate@example.com --role user
```

The invitation contains a one-time signup token. Sending the email +
token out-of-band is the operator's job; the token is consumed by a
single signup.

### Per-user settings

`auth/settings.py` persists per-user preferences (schema v15+):

- `timezone` — used by `utils.tz.format_user_tz` for briefing render
- `theme` — UI theme override
- `defaults` — per-user defaults for tabs that ship filters / pickers

### Per-user data scoping

Every data loader in the engine takes an optional `user_id`. When
present, persisted state (alerts, scenarios, watchlists, saved filters,
report snapshots, notification preferences, etc.) is scoped to that
user. When absent (legacy single-password mode), data is global. See
`feat(auth): per-user data scoping` for the full list of loaders.

---

## Single-password mode (legacy)

When no users exist and `APP_PASSWORD_HASH` + `APP_PASSWORD_SALT` are
configured, the legacy gate runs. It's intentionally simple — one
shared password protects the whole app, no per-user accounts, no user
table active.

### Generating a hash

```bash
python3 -c "from auth.gate import generate_password_hash; print(generate_password_hash('mypassword'))"
```

Prints a `(hash_hex, salt_hex)` tuple. A fresh random salt is generated
on every call, so the output changes each invocation even for the same
password.

### Saving the pair

**Local dev** — `.streamlit/secrets.toml`:

```toml
APP_PASSWORD_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
APP_PASSWORD_SALT = "6e72d4f1a5e9b8c3a2f4d7e6b1c0a3d5"
```

**Docker / Streamlit Cloud** — set them as environment variables; the
gate reads either `os.environ` or `st.secrets` (in that order).

`.streamlit/secrets.toml` is in `.gitignore` — never commit the pair.

### Lockout policy

After **5 consecutive failed attempts**, the session is locked for
**5 minutes**. The lockout is session-scoped (lives in
`st.session_state`); closing the tab resets it. For durable IP-scoped
rate limiting use the multi-user mode plus `auth/rate_limit.py`.

Constants live in `auth/gate.py`:

```python
MAX_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300
```

---

## Open mode (no auth)

When `users` table is empty AND no `APP_PASSWORD_HASH` is configured,
the app runs in **open mode**: no login form, full access, one
`WARNING` logged at startup. Intentional for local dev — flip the gate
on by either configuring `APP_PASSWORD_HASH` or signing up the first
user from the in-app form.

---

## Implementation notes

### KDF: scrypt with PBKDF2 fallback

The legacy single-password hash uses `hashlib.scrypt` (RFC 7914, `n=2**14, r=8,
p=1, dklen=32`) when the platform's OpenSSL exposes it. When it doesn't —
notably macOS system Python (LibreSSL omits scrypt) — the module
transparently falls back to `hashlib.pbkdf2_hmac("sha256", ..., 600_000)`.
Both are stdlib-only; no `bcrypt`, no `argon2-cffi`, no new package
in `requirements.txt`.

A hash + salt produced on one platform must verify on the same
platform. Generate the hash on the same OS where the app will run.

### Multi-user password storage

Multi-user passwords are stored in the `users` table using the same
KDF (scrypt with PBKDF2 fallback). Each user gets its own salt. Stored
columns: `id`, `username`, `password_hash`, `password_salt`, `role`,
`created_at`, `last_login_at`.

### Constant-time comparison

Both modes use `hmac.compare_digest` for password verification — never
`==`. Avoids leaking length / prefix-match information through timing
side channels.

### Engine layer is Streamlit-free

The pure functions in `auth/gate.py`, `auth/users.py`, `auth/mfa.py`
(hashing, verification, env-var handling, TOTP arithmetic) do **not**
import Streamlit. Only the `require_auth*` and `logout` helpers
import it lazily. This keeps the engine fully unit-testable in
isolation and means the auth modules can be imported safely from CLI
tools (e.g., `tools.ops invitations`).

### Vault encryption for sensitive fields

`state/vault.py` provides stdlib-only field-level encryption for
sensitive channel targets (webhook URLs, SMS phone numbers,
PagerDuty integration keys). The vault key is read from env;
bulk encrypt/decrypt + 2-secret rotation window are exposed via
the in-app *Vault* panel and `tools.ops vault` CLI.

### Security hardening

Hardened via a 2026-05 security review (all stdlib, no new deps):

- **Login brute-force throttle** — `auth.users.login` checks a per-username
  in-process token bucket (burst 10, ~1 attempt / 5s sustained) and returns
  the same `None` shape as a bad password (no enumeration signal). A
  successful login resets the bucket, so only *consecutive* failures count.
- **API-token throttle + expiry** — `worker.api_server` checks a per-IP
  bucket BEFORE verifying the token (a flood is rejected without paying the
  KDF), and tokens carry an `expires_at` (TTL via `--expires-in-days` / env /
  90d default; `<= 0` = non-expiring; schema v27).
- **TOTP replay protection** — a verified code's step is recorded
  (`users.mfa_last_used_step`, schema v28) via an atomic conditional UPDATE,
  so the same code can't be replayed within its validity window.
- **MFA completeness** — recovery codes are accepted at login (single-use);
  enabling MFA requires proof-of-possession of a current code.
- **Enumeration resistance** — an unknown username runs a dummy KDF so its
  response time matches a real bad-password attempt; all failure paths return
  an identical `None`.
- **Failed-login auditing** — a failed credential attempt records a
  `login_failed` audit event (`auth.audit.record_login_failure`) carrying a
  *hashed* attempted username (no raw value / no PII), so brute-force shows up
  in the audit log without revealing which usernames exist.
- **Case-collision-resistant signup** — a new username is rejected if it
  collides case-insensitively with an existing one (`COLLATE NOCASE`), so
  "Admin" cannot shadow "admin". (Login lookup itself remains case-sensitive.)

---

## Files

- `auth/__init__.py` — package marker; re-exports public API.
- `auth/gate.py` — engine + Streamlit gates (`require_auth`, `require_auth_with_users`).
- `auth/users.py` — `users` table CRUD: `signup`, `login`, `get_user`, etc.
- `auth/mfa.py` — TOTP enrolment, verification, recovery codes (stdlib RFC 6238).
- `auth/tokens.py` — per-user API tokens for `worker.api_server`.
- `auth/invitations.py` — single-use signup tokens for invited users.
- `auth/settings.py` — per-user preferences (timezone, theme, defaults).
- `auth/notification_prefs.py` — per-user notification severity / type / quiet-hours filters.
- `auth/calendar_tokens.py` — per-user tokens for the ICS calendar feed.
- `auth/rate_limit.py` — per-user token-bucket rate limiting (for the HTTP API).
- `auth/audit.py` — per-user audit-log entries.
- `app.py` — wires `require_auth_with_users()` in immediately after `set_page_config`.

Tests live alongside in `tests/test_auth_*.py` — unit coverage for hashing,
verification, env-var handling, lockout, signup/login, MFA enrolment +
verification, recovery code single-use semantics, token issuance + revocation,
and the engine/Streamlit boundary.
