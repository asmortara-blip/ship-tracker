# Authentication

The Ship Tracker ships with a **single-password authentication gate** for the
Streamlit app. It's intentionally simple: one shared password protects the
whole app. There are no per-user accounts, no per-user data, and no user
table in the DB.

> **This is not a multi-user system.** See [Why single-password?](#why-single-password)
> below for the scope decision.

---

## How it works

When the Streamlit app starts, `auth.gate.require_auth()` runs at the very top
of `app.py` (right after `st.set_page_config`). Its behavior:

| App start-up condition | What `require_auth()` does |
| --- | --- |
| `APP_PASSWORD_HASH` **and** `APP_PASSWORD_SALT` are both set | Renders a login form, halts the page via `st.stop()` until the correct password is entered. |
| Either env var is missing or malformed | **Open mode** — no auth required. Logs a `WARNING` once at startup. Intentional for local dev. |
| The `auth` module itself errors | Logs a warning and lets the app through (so a bug in auth can never lock you out of the app entirely). |

The hash and salt are read from environment variables, falling back to
`st.secrets.get(...)` — the same pattern as `engine/narration_engine.py::_get_anthropic_key`.
Passwords are **never** stored in the SQLite DB.

---

## Setup

### 1. Generate a hash for your chosen password

Run this **once**, from the repo root, with the password you want to use:

```bash
python3 -c "from auth.gate import generate_password_hash; print(generate_password_hash('mypassword'))"
```

That prints a `(hash_hex, salt_hex)` tuple, e.g.:

```
('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', '6e72d4f1a5e9b8c3a2f4d7e6b1c0a3d5')
```

> A fresh random salt is used every time you run this, so the output
> changes each call even for the same password.

### 2. Save the pair to your secrets

**For local dev** — add to `.streamlit/secrets.toml`:

```toml
APP_PASSWORD_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
APP_PASSWORD_SALT = "6e72d4f1a5e9b8c3a2f4d7e6b1c0a3d5"
```

**For Streamlit Cloud** — paste the same two lines into
*App Settings → Secrets*.

**For Docker / a `.env` file** — set them as environment variables:

```bash
export APP_PASSWORD_HASH="e3b0c44298..."
export APP_PASSWORD_SALT="6e72d4f1..."
```

The `.streamlit/secrets.toml` file is already in `.gitignore` — make sure
**you never commit the hash + salt to the repo**, even though they are not
the plaintext password.

### 3. Restart the Streamlit app

```bash
streamlit run app.py
```

You should now see the login form before any tab content renders.

---

## Open mode (no password configured)

When neither env var is set, the app runs in **open mode**:

- No login form is rendered.
- A `WARNING` is logged exactly once per process: `auth.gate:
  APP_PASSWORD_HASH/APP_PASSWORD_SALT not configured — app is running in
  OPEN MODE`.
- Every visitor has full access to the app.

This is intentional for local development. If you do not want open mode in a
particular environment, configure the hash + salt there — the gate flips on
automatically.

---

## Lockout policy

After **5 consecutive failed login attempts**, the current session is locked
for **5 minutes**. While locked the form is replaced with an error that
counts down the remaining seconds.

The lockout is **session-scoped** — it lives in `st.session_state`, so
closing and re-opening the browser tab will reset the counter. That is
acceptable for a single-password gate. If you need durable, IP-scoped
rate limiting you have outgrown this module.

Constants live in `auth/gate.py`:

```python
MAX_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300  # 5 minutes
```

---

## Implementation notes

### KDF: scrypt with PBKDF2 fallback

The hash is produced by `hashlib.scrypt` (RFC 7914, `n=2**14, r=8, p=1,
dklen=32`) when the platform's OpenSSL exposes it. When it doesn't — notably
**macOS system Python**, which is linked against a LibreSSL that omits scrypt
— the module transparently falls back to `hashlib.pbkdf2_hmac("sha256", ...,
600_000)`. Both are stdlib-only; no `bcrypt`, no `argon2-cffi`, no new
package in `requirements.txt`.

A hash + salt produced on one platform must verify on the same platform.
If you generate the hash on a macOS box that uses PBKDF2 and then deploy to
Linux where scrypt is available, the stored bytes won't verify. Generate the
hash on the same platform where the app will run. (The platform is fixed for
any given deployment, so this is a one-time consideration.)

### Constant-time comparison

`_verify_password` uses `hmac.compare_digest` for the equality check, not
`==`. This avoids leaking password length or prefix-match information
through timing side channels.

### Engine layer is Streamlit-free

The pure functions in `auth/gate.py` (`_hash_password`, `_verify_password`,
`_get_password_config`, `generate_password_hash`) do **not** import
Streamlit. Only `require_auth()` and `logout()`, plus the small session-state
helper, import it (lazily). This keeps the engine fully unit-testable in
isolation and means `auth/gate.py` can be imported safely from CLI tools
or scripts.

---

## Why single-password?

This is a deliberate scope decision, not an oversight. A real multi-user
system would mean:

- A `users` table in `state/db.py` with hashed passwords and metadata.
- A `user_id` foreign key on every persistence table — scenarios,
  watchlists, portfolios, alerts, screener saves, etc. (≈ a dozen tables).
- Per-user data scoping on every read/write across the codebase.
- Session management, password resets, email verification, role-based
  access — and the policy decisions that go with them.
- A migration from the current schema (`v3`) plus a backfill of existing
  rows under a default user.

That's a large architectural change. It's not the right tool for the actual
need here, which is "keep casual visitors out of my deployed Streamlit app."
A single shared password does exactly that, with one file, no new
dependencies, no schema changes, and ~250 lines of code.

If multi-user becomes the real requirement, that's a future project — start
fresh with a proper user identity model rather than retrofitting users onto
this gate.

---

## Files

- `auth/__init__.py` — package marker; re-exports the public API.
- `auth/gate.py` — engine functions + Streamlit-facing `require_auth`/`logout`.
- `tests/test_auth_gate.py` — 24 unit tests covering hashing, verification,
  env-var handling, lockout, and the engine/Streamlit boundary.
- `app.py` — wires `require_auth()` in immediately after `set_page_config`.
