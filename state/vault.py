"""Encrypted secrets vault for opt-in field-level encryption.

Today every delivery channel's webhook URL or integration key is stored
as plaintext in the ``delivery_channels.target`` column. That's fine for
a single-user local SQLite file, but if the DB ever leaks (e.g. an
operator hands a colleague a ``utils.bulk_export`` archive), every
Slack webhook + PagerDuty key is exposed.

This module adds an opt-in vault. Callers can choose to encrypt
sensitive strings before persisting them; the envelope is
self-describing (``vault:v1:<base64>``) so future schemes can coexist
with v1 envelopes on the same DB.

Threat model
============
This is a **'protect against casual DB leaks'** scheme — it stops a
copied-off ``ship_tracker.db`` file or a leaked ``bulk_export.tar.gz``
from immediately exposing every webhook URL.

It does **NOT** protect against an attacker with access to the running
process — the master key has to be readable by the process to decrypt
the targets at delivery time, so anyone who can read process memory,
attach a debugger, or read the same VAULT_KEY env var can read every
secret.

Stdlib-only — no ``cryptography`` library dependency
====================================================
We deliberately avoid pulling in the ``cryptography`` library. The
scheme below uses ``hashlib.blake2b`` to derive a per-message subkey,
``hashlib.sha256`` to expand a keystream, XOR for the cipher, and
``hmac.HMAC(SHA256)`` for authentication. This is NOT a recommended
construction against a motivated attacker — there is no AEAD nonce-
misuse resistance, the keystream is deterministic per (subkey,
counter), and the cipher gives no security if the same subkey is ever
reused. We pick a fresh 16-byte nonce per encrypt() call (via
``secrets.token_bytes(16)``) which makes that vanishingly unlikely in
practice for the volume this vault sees (a few hundred channels).

Rotate the master key regularly (``rotate_key()`` re-encrypts every
encrypted ``delivery_channels.target`` against the new key) to limit
the blast radius if the key ever leaks.

Envelope format
===============
``vault:v1:<urlsafe-base64-no-padding(nonce || ciphertext || tag)>``

  * ``nonce``      — 16 random bytes per call (``secrets.token_bytes``).
  * ``ciphertext`` — same length as the plaintext (XOR cipher).
  * ``tag``        — 32-byte ``HMAC-SHA256(master_key, nonce || ciphertext)``.

``decrypt`` verifies the HMAC with ``hmac.compare_digest`` (constant
time) BEFORE attempting to recover the plaintext. A bad envelope, a
tampered ciphertext, a truncated payload, ``None``, or an empty string
all return ``None`` — ``decrypt`` never raises.

Master key
==========
``_get_master_key()`` returns the raw 32-byte master key, sourced (in
order):

  1. The ``VAULT_KEY`` env var (hex-encoded — ``secrets.token_hex(32)``).
  2. ``st.secrets['VAULT_KEY']`` if Streamlit secrets are configured.
  3. ``kv_state['vault_master_key']`` in SQLite. Generated on first
     call via ``secrets.token_bytes(32)`` and persisted hex-encoded.

The kv_state fallback is convenient for local development (no env var
needed) but means the master key sits in the same DB file as the
encrypted secrets it protects — equivalent to "no encryption at rest"
against a DB-file leak. **Production deployments must set the
VAULT_KEY env var** so the master key lives outside the DB.

Public API
==========
* :func:`encrypt` — wrap a plaintext string in a ``vault:v1:`` envelope.
  Never raises; returns the plaintext unchanged on failure.
* :func:`decrypt` — recover a plaintext from a ``vault:v1:`` envelope.
  Returns ``None`` on any error (bad envelope / HMAC mismatch / etc.).
* :func:`is_encrypted` — cheap prefix check for callers that want to
  branch on "is this value already wrapped?".
* :func:`rotate_key` — generate a fresh master key and re-encrypt every
  currently-encrypted ``delivery_channels.target`` against the new
  key. Best-effort: returns ``True`` on full success, ``False`` on
  partial.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

_ENVELOPE_PREFIX = "vault:v1:"
_KV_KEY = "vault_master_key"           # kv_state row key for the master key
_MASTER_KEY_BYTES = 32                 # 256-bit master key
_NONCE_BYTES = 16                      # 128-bit per-message nonce
_TAG_BYTES = 32                        # HMAC-SHA256 tag length
_SUBKEY_BYTES = 32                     # Per-message subkey for the keystream
_KEYSTREAM_BLOCK = 32                  # SHA256 produces 32 bytes per call


# ─────────────────────────────────────────────────────────────────────────────
#  Master-key resolution
# ─────────────────────────────────────────────────────────────────────────────

def _master_key_from_env() -> Optional[bytes]:
    """Read VAULT_KEY from the OS env. Returns ``None`` if absent / malformed.

    The env var is hex-encoded (``secrets.token_hex(32)`` → 64 chars). We
    accept any non-empty hex string of even length and let
    ``bytes.fromhex`` decode; if the value is short we still accept it
    (the master key only needs to be unpredictable, not exactly 32
    bytes), but we log a debug note so an operator can spot the typo.
    """
    raw = os.environ.get("VAULT_KEY", "")
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        # Not hex — treat the raw string as UTF-8 bytes. Still better
        # than failing closed and silently using the kv_state fallback.
        key = raw.encode("utf-8")
    if not key:
        return None
    if len(key) < 16:
        logger.debug(
            f"state.vault: VAULT_KEY is only {len(key)} bytes — recommend "
            f">= {_MASTER_KEY_BYTES} bytes for production."
        )
    return key


def _master_key_from_streamlit() -> Optional[bytes]:
    """Read VAULT_KEY from st.secrets if Streamlit is importable. Never raises."""
    try:
        import streamlit as st
        # ``st.secrets`` raises ``StreamlitSecretNotFoundError`` outside a
        # configured session; the inner ``if st.secrets`` guards against
        # the bare-Streamlit-import case.
        if not st.secrets:
            return None
        raw = str(st.secrets.get("VAULT_KEY", "") or "")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _master_key_from_kv_state() -> bytes:
    """Read the persisted master key from kv_state, generating + persisting
    one on first call. Returns the raw 32 bytes.

    A read or write failure falls back to a process-lifetime ephemeral
    key so encrypt/decrypt still work for the rest of the run. That
    means decrypt will fail after a restart in the failure path — which
    is the safer mode: an unreadable kv_state row should not silently
    expose every secret in plaintext.
    """
    try:
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_KV_KEY,)
        ).fetchone()
        if row is not None:
            stored = row["value"] if hasattr(row, "keys") else row[0]
            if stored:
                try:
                    return bytes.fromhex(stored)
                except ValueError:
                    # Fall through to regenerate.
                    pass
        # No row yet — generate and persist.
        new_key = secrets.token_bytes(_MASTER_KEY_BYTES)
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_KV_KEY, new_key.hex(), now_iso),
            )
        return new_key
    except Exception as exc:
        logger.warning(
            f"state.vault: kv_state master-key read/write failed: {exc}. "
            f"Using ephemeral process-lifetime key — decrypts will fail "
            f"after restart."
        )
        return secrets.token_bytes(_MASTER_KEY_BYTES)


def _get_master_key() -> bytes:
    """Resolve the 32-byte master key. Order: VAULT_KEY env → st.secrets
    → kv_state-persisted (auto-generated on first use)."""
    key = _master_key_from_env()
    if key:
        return key
    key = _master_key_from_streamlit()
    if key:
        return key
    return _master_key_from_kv_state()


# ─────────────────────────────────────────────────────────────────────────────
#  Cipher primitives
# ─────────────────────────────────────────────────────────────────────────────

def _derive_subkey(master_key: bytes, nonce: bytes) -> bytes:
    """Derive a 32-byte subkey from the master key + per-message nonce.

    blake2b is a keyed PRF — ``hashlib.blake2b(nonce, key=master_key,
    digest_size=32)`` gives us a fast 256-bit subkey that is bound to
    both the master key and the nonce. We can't use the master key
    directly for the keystream because the keystream is then
    deterministic per (master_key, counter), so the same plaintext
    encrypts to the same ciphertext — visible in the DB.
    """
    h = hashlib.blake2b(nonce, key=master_key, digest_size=_SUBKEY_BYTES)
    return h.digest()


def _keystream(subkey: bytes, length: int) -> bytes:
    """Generate ``length`` bytes of SHA256-based keystream from ``subkey``.

    Block ``i`` is ``SHA256(subkey || i.to_bytes(8, 'big'))``. Truncate
    to ``length``.  Counter-mode style — each block is independent so a
    bit-flip in the ciphertext flips exactly the same bit in the
    recovered plaintext (caught by the outer HMAC).
    """
    out = bytearray()
    block = 0
    while len(out) < length:
        h = hashlib.sha256()
        h.update(subkey)
        h.update(block.to_bytes(8, "big"))
        out.extend(h.digest())
        block += 1
    return bytes(out[:length])


def _xor(data: bytes, keystream: bytes) -> bytes:
    """Byte-wise XOR of ``data`` against ``keystream``. Assumes
    ``len(keystream) >= len(data)``."""
    return bytes(a ^ b for a, b in zip(data, keystream))


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_encrypted(value: object) -> bool:
    """Return True iff ``value`` is a ``vault:v1:`` envelope.

    Cheap prefix check — doesn't actually verify the HMAC. Use
    :func:`decrypt` for that. Tolerates non-string inputs (returns
    False).
    """
    return isinstance(value, str) and value.startswith(_ENVELOPE_PREFIX)


def encrypt(plaintext: str) -> str:
    """Wrap ``plaintext`` in a ``vault:v1:`` envelope.

    Returns the envelope string. **Never raises** — on any internal
    error the original plaintext is returned unchanged and the failure
    is logged at WARNING. The caller can detect "encryption skipped"
    by checking ``is_encrypted(result)`` afterwards.

    Same plaintext → distinct envelopes across calls (fresh 16-byte
    nonce per call). Empty string is accepted and encrypts to a valid
    envelope (just a nonce + tag, zero-length ciphertext).
    """
    if not isinstance(plaintext, str):
        # Defensive — callers should pass str. Return whatever they
        # gave us so we don't break their call site.
        logger.debug(
            f"state.vault.encrypt: non-string input ({type(plaintext).__name__}); "
            f"returning unchanged"
        )
        return plaintext  # type: ignore[return-value]
    try:
        master_key = _get_master_key()
        nonce = secrets.token_bytes(_NONCE_BYTES)
        pt_bytes = plaintext.encode("utf-8")
        subkey = _derive_subkey(master_key, nonce)
        ks = _keystream(subkey, len(pt_bytes))
        ct = _xor(pt_bytes, ks)
        tag = hmac.new(master_key, nonce + ct, hashlib.sha256).digest()
        envelope_bytes = nonce + ct + tag
        b64 = base64.urlsafe_b64encode(envelope_bytes).rstrip(b"=").decode("ascii")
        return f"{_ENVELOPE_PREFIX}{b64}"
    except Exception as exc:  # noqa: BLE001 — encrypt MUST NOT raise
        logger.warning(
            f"state.vault.encrypt: failed ({type(exc).__name__}: {exc}); "
            f"returning plaintext unchanged"
        )
        return plaintext


def decrypt(envelope: Optional[str]) -> Optional[str]:
    """Recover a plaintext from a ``vault:v1:`` envelope.

    Returns:
        The original plaintext on success, or ``None`` on any error
        (bad envelope / HMAC mismatch / truncated payload / ``None`` /
        empty string / non-string input). **Never raises.**

    The HMAC is verified with ``hmac.compare_digest`` (constant time)
    BEFORE the keystream-XOR step, so a tampered ciphertext is caught
    without leaking timing information about the underlying plaintext.
    """
    try:
        if not isinstance(envelope, str) or not envelope:
            return None
        if not envelope.startswith(_ENVELOPE_PREFIX):
            return None
        b64 = envelope[len(_ENVELOPE_PREFIX):]
        # urlsafe_b64decode requires padding — re-add it.
        pad_len = (-len(b64)) % 4
        try:
            blob = base64.urlsafe_b64decode(b64 + ("=" * pad_len))
        except (ValueError, base64.binascii.Error):
            return None
        if len(blob) < _NONCE_BYTES + _TAG_BYTES:
            return None
        nonce = blob[:_NONCE_BYTES]
        tag = blob[-_TAG_BYTES:]
        ct = blob[_NONCE_BYTES:-_TAG_BYTES]
        master_key = _get_master_key()
        expected_tag = hmac.new(master_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            return None
        subkey = _derive_subkey(master_key, nonce)
        ks = _keystream(subkey, len(ct))
        pt_bytes = _xor(ct, ks)
        try:
            return pt_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None
    except Exception as exc:  # noqa: BLE001 — decrypt MUST NOT raise
        logger.debug(f"state.vault.decrypt: failed silently: {exc}")
        return None


def rotate_key() -> bool:
    """Generate a fresh master key and re-encrypt every currently-encrypted
    ``delivery_channels.target`` against it.

    Returns:
        ``True`` if every encrypted channel target was successfully
        re-encrypted under the new key, ``False`` if any single
        re-encrypt or persistence step failed (partial rotation). On
        partial failure the master key is still rotated — the failed
        channels will simply fail to decrypt until they are re-saved
        with ``encrypt_target=True`` against the new key.

    Records an ``rotate_vault_key`` audit event with the count of
    channels re-encrypted. Key material is deliberately NOT logged.
    """
    failures = 0
    rerencrypted = 0
    try:
        from state.db import get_connection
        conn = get_connection()
        # Snapshot the encrypted channel rows BEFORE we rotate the key
        # — decrypt them under the old key while the old key is still
        # active, then swap the key, then re-encrypt + persist.
        try:
            rows = conn.execute(
                "SELECT channel_id, target FROM delivery_channels"
            ).fetchall()
        except Exception as exc:
            logger.warning(f"state.vault.rotate_key: read failed: {exc}")
            rows = []

        # Decrypt every encrypted target under the OLD key. Plaintext
        # targets are skipped — they stay plaintext post-rotation
        # (only opt-in channels are re-encrypted).
        plaintexts: list[tuple[str, str]] = []
        for r in rows:
            try:
                cid = r["channel_id"] if hasattr(r, "keys") else r[0]
                tgt = r["target"] if hasattr(r, "keys") else r[1]
            except Exception:
                failures += 1
                continue
            if not is_encrypted(tgt):
                continue
            pt = decrypt(tgt)
            if pt is None:
                # An undecryptable row pre-rotation means the old key
                # was already wrong — we can't recover, so we count
                # it as a failure and skip.
                failures += 1
                continue
            plaintexts.append((cid, pt))

        # Rotate the master key in kv_state. We write the new key
        # AFTER reading the rows so a write failure does not leave
        # half the channels under the new key with no way back.
        new_key = secrets.token_bytes(_MASTER_KEY_BYTES)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                    "VALUES (?, ?, ?)",
                    (_KV_KEY, new_key.hex(), now_iso),
                )
        except Exception as exc:
            logger.warning(f"state.vault.rotate_key: key write failed: {exc}")
            return False

        # Re-encrypt every previously-encrypted channel target under
        # the new key. We bypass _get_master_key() and use the new
        # key directly so we don't depend on env-var precedence
        # mid-rotation.
        for cid, pt in plaintexts:
            try:
                # Inline encrypt with the explicit new_key — same
                # construction as encrypt() but key-bound.
                nonce = secrets.token_bytes(_NONCE_BYTES)
                pt_bytes = pt.encode("utf-8")
                subkey = _derive_subkey(new_key, nonce)
                ks = _keystream(subkey, len(pt_bytes))
                ct = _xor(pt_bytes, ks)
                tag = hmac.new(new_key, nonce + ct, hashlib.sha256).digest()
                blob = nonce + ct + tag
                b64 = base64.urlsafe_b64encode(blob).rstrip(b"=").decode("ascii")
                new_envelope = f"{_ENVELOPE_PREFIX}{b64}"
                with conn:
                    conn.execute(
                        "UPDATE delivery_channels SET target = ? "
                        "WHERE channel_id = ?",
                        (new_envelope, cid),
                    )
                rerencrypted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"state.vault.rotate_key: channel {cid} re-encrypt "
                    f"failed: {exc}"
                )
                failures += 1
    except Exception as exc:  # noqa: BLE001 — rotate_key MUST NOT raise
        logger.warning(f"state.vault.rotate_key: unexpected failure: {exc}")
        return False

    # Audit-log the rotation. Key material deliberately omitted — the
    # audit log is meant to surface intent + scope, not secrets.
    try:
        from auth.audit import record_audit
        record_audit(
            "rotate_vault_key",
            detail={"channels_rerencrypted": rerencrypted},
        )
    except Exception:  # noqa: BLE001
        pass

    return failures == 0


__all__ = [
    "encrypt",
    "decrypt",
    "is_encrypted",
    "rotate_key",
]
