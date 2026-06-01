"""Tests for state.vault — opt-in encrypted secrets vault.

Covers:
  - Round-trip: encrypt(plaintext) → decrypt(envelope) == plaintext
  - Per-message nonce: same plaintext → distinct envelopes
  - Envelope format: starts with ``vault:v1:``
  - is_encrypted() prefix recognition
  - Decryption robustness: tampered ciphertext, truncated envelope,
    malformed prefix, ``None``/empty/non-string all return ``None`` and
    NEVER raise
  - rotate_key(): re-encrypts every encrypted channel target, leaves
    plaintext targets untouched, decryption still works post-rotation,
    never raises on partial failure
  - Wiring: save_channel(encrypt_target=True) persists an encrypted
    target; load_channels() decrypts transparently
  - save_channel(encrypt_target=False) (the default) preserves the
    pre-vault plaintext-target behaviour exactly
  - Audit hook fires on rotate_key with a metadata-only payload
    (no key material)
"""
from __future__ import annotations

import base64

import pytest

from engine.alert_delivery import (
    DeliveryChannel,
    load_channels,
    save_channel,
)
from state import vault


# ─── Fixture: isolate SQLite DB per test ──────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db, and clear VAULT_KEY so the
    tests exercise the kv_state-fallback master-key path by default
    (individual tests opt into the env-var path explicitly)."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    monkeypatch.delenv("VAULT_KEY", raising=False)
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_channel(
    *,
    channel_id: str = "ch1",
    name: str = "Test channel",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T000/B000/secret",
    severity_threshold: str = "LOW",
    enabled: bool = True,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind=kind,
        target=target,
        severity_threshold=severity_threshold,
        enabled=enabled,
    )


# ─────────────────────────────────────────────────────────────────────────
#  Round-trip + envelope shape
# ─────────────────────────────────────────────────────────────────────────

def test_encrypt_decrypt_round_trip_preserves_plaintext() -> None:
    """The defining property: decrypt(encrypt(x)) == x for any string x."""
    plaintext = "https://hooks.slack.com/services/T000/B000/XXXX/secret"
    envelope = vault.encrypt(plaintext)
    assert vault.decrypt(envelope) == plaintext


def test_encrypt_decrypt_round_trip_unicode() -> None:
    """Round-trip must work for non-ASCII UTF-8 content too (channel names
    can carry emoji / non-Latin characters)."""
    plaintext = "Slack channel: 物流-アラート — pager-key=ßecret"
    assert vault.decrypt(vault.encrypt(plaintext)) == plaintext


def test_encrypt_decrypt_round_trip_empty_string() -> None:
    """Empty string is a valid plaintext — envelope is just nonce + tag."""
    envelope = vault.encrypt("")
    assert envelope.startswith("vault:v1:")
    assert vault.decrypt(envelope) == ""


def test_encrypt_produces_distinct_outputs_for_same_plaintext() -> None:
    """Per-call random nonce means two encrypts of the same plaintext
    produce different envelopes — critical for hiding equal targets
    across channels in a DB leak."""
    pt = "same-secret-twice"
    e1 = vault.encrypt(pt)
    e2 = vault.encrypt(pt)
    assert e1 != e2
    # But both decrypt to the same plaintext.
    assert vault.decrypt(e1) == pt
    assert vault.decrypt(e2) == pt


def test_encrypt_output_starts_with_vault_v1_prefix() -> None:
    envelope = vault.encrypt("anything")
    assert envelope.startswith("vault:v1:")


def test_is_encrypted_recognizes_envelopes() -> None:
    assert vault.is_encrypted(vault.encrypt("hi"))
    assert vault.is_encrypted("vault:v1:abc")
    # And rejects non-envelopes.
    assert not vault.is_encrypted("https://hooks.slack.com/services/T/B/X")
    assert not vault.is_encrypted("")
    assert not vault.is_encrypted("vault:v2:future")
    assert not vault.is_encrypted("vault:")


def test_is_encrypted_tolerates_non_string_inputs() -> None:
    """is_encrypted must not raise on None / int / list — callers may
    pass row['target'] from a sqlite3.Row without type-checking."""
    assert vault.is_encrypted(None) is False  # type: ignore[arg-type]
    assert vault.is_encrypted(123) is False  # type: ignore[arg-type]
    assert vault.is_encrypted(["vault:v1:x"]) is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────
#  Decrypt: error robustness — never raise, return None on bad input
# ─────────────────────────────────────────────────────────────────────────

def test_decrypt_tampered_ciphertext_returns_none() -> None:
    """A single byte flip in the ciphertext must trip the HMAC and
    return None — this is the defining property of authenticated
    encryption."""
    envelope = vault.encrypt("sensitive webhook url")
    b64 = envelope[len("vault:v1:"):]
    # Re-pad, decode, flip one byte in the middle (well inside the
    # ciphertext, away from the nonce + tag), re-encode.
    pad = (-len(b64)) % 4
    blob = bytearray(base64.urlsafe_b64decode(b64 + ("=" * pad)))
    # Flip a byte 20 bytes in — past the 16-byte nonce, inside the ciphertext.
    flip_idx = 20 if len(blob) > 25 else len(blob) // 2
    blob[flip_idx] ^= 0x01
    tampered_b64 = base64.urlsafe_b64encode(bytes(blob)).rstrip(b"=").decode("ascii")
    tampered = f"vault:v1:{tampered_b64}"
    assert vault.decrypt(tampered) is None


def test_decrypt_tampered_tag_returns_none() -> None:
    """A flipped bit in the HMAC tag must also return None."""
    envelope = vault.encrypt("sensitive webhook url")
    b64 = envelope[len("vault:v1:"):]
    pad = (-len(b64)) % 4
    blob = bytearray(base64.urlsafe_b64decode(b64 + ("=" * pad)))
    # Flip the last byte of the tag.
    blob[-1] ^= 0x01
    tampered_b64 = base64.urlsafe_b64encode(bytes(blob)).rstrip(b"=").decode("ascii")
    assert vault.decrypt(f"vault:v1:{tampered_b64}") is None


def test_decrypt_truncated_envelope_returns_none() -> None:
    """A truncated payload (shorter than nonce + tag) must return None."""
    envelope = vault.encrypt("anything")
    # Chop off most of the base64 payload.
    truncated = envelope[: len("vault:v1:") + 4]
    assert vault.decrypt(truncated) is None


def test_decrypt_malformed_prefix_returns_none() -> None:
    """A string without the ``vault:v1:`` prefix returns None — not the
    raw string, not an exception."""
    assert vault.decrypt("not-an-envelope") is None
    assert vault.decrypt("vault:v2:abc") is None  # future scheme, can't decrypt
    assert vault.decrypt("https://hooks.slack.com/foo") is None


def test_decrypt_none_returns_none() -> None:
    """decrypt(None) is the load_channels() degenerate case — must not raise."""
    assert vault.decrypt(None) is None


def test_decrypt_empty_string_returns_none() -> None:
    assert vault.decrypt("") is None


def test_decrypt_non_string_input_returns_none() -> None:
    """Belt-and-suspenders: decrypt must tolerate non-string inputs."""
    assert vault.decrypt(123) is None  # type: ignore[arg-type]
    assert vault.decrypt([]) is None  # type: ignore[arg-type]


def test_decrypt_invalid_base64_returns_none() -> None:
    """Garbage after the prefix must return None, not raise binascii.Error."""
    assert vault.decrypt("vault:v1:!!!not-base64!!!") is None


def test_encrypt_never_raises_on_weird_input(monkeypatch) -> None:
    """encrypt must NEVER raise — even when the master-key resolver
    blows up. The contract is "return plaintext unchanged on failure"."""
    def boom() -> bytes:
        raise RuntimeError("synthetic master-key failure")

    monkeypatch.setattr(vault, "_get_master_key", boom)
    # Returns the plaintext unchanged rather than raising.
    out = vault.encrypt("payload")
    assert out == "payload"


# ─────────────────────────────────────────────────────────────────────────
#  Master-key resolution: env-var precedence
# ─────────────────────────────────────────────────────────────────────────

def test_vault_key_env_var_takes_precedence(monkeypatch) -> None:
    """When VAULT_KEY is set, the kv_state row is ignored — proves the
    env-var path is wired up."""
    import secrets as _secrets

    monkeypatch.setenv("VAULT_KEY", _secrets.token_hex(32))
    envelope = vault.encrypt("secret-under-env-key")
    assert vault.decrypt(envelope) == "secret-under-env-key"
    # Now flip to a DIFFERENT env-var key. The previous envelope must
    # fail to decrypt (HMAC mismatch).
    monkeypatch.setenv("VAULT_KEY", _secrets.token_hex(32))
    assert vault.decrypt(envelope) is None


def test_kv_state_master_key_persists_across_calls() -> None:
    """Without VAULT_KEY, the master key is generated + stored in
    kv_state on first use and re-read on subsequent calls — so two
    encrypts in the same process round-trip correctly."""
    e1 = vault.encrypt("first")
    e2 = vault.encrypt("second")
    assert vault.decrypt(e1) == "first"
    assert vault.decrypt(e2) == "second"
    # Confirm the key actually landed in kv_state.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT value FROM kv_state WHERE key = 'vault_master_key'"
    ).fetchone()
    assert row is not None
    assert row["value"]  # non-empty hex


# ─────────────────────────────────────────────────────────────────────────
#  rotate_key
# ─────────────────────────────────────────────────────────────────────────

def test_rotate_key_re_encrypts_every_encrypted_channel_target() -> None:
    """After rotation:
      - the kv_state master key has changed
      - every previously-encrypted channel target is still readable
      - the stored envelopes themselves have changed (re-wrapped)
    """
    ch1 = _make_channel(channel_id="c1", target="https://hooks/secret-1")
    ch2 = _make_channel(channel_id="c2", target="https://hooks/secret-2")
    save_channel(ch1, encrypt_target=True)
    save_channel(ch2, encrypt_target=True)

    from state.db import get_connection
    conn = get_connection()

    before = {r["channel_id"]: r["target"] for r in conn.execute(
        "SELECT channel_id, target FROM delivery_channels"
    ).fetchall()}
    old_key = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'vault_master_key'"
    ).fetchone()["value"]

    assert vault.rotate_key() is True

    after = {r["channel_id"]: r["target"] for r in conn.execute(
        "SELECT channel_id, target FROM delivery_channels"
    ).fetchall()}
    new_key = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'vault_master_key'"
    ).fetchone()["value"]

    # Master key actually rotated.
    assert new_key != old_key
    # Stored envelopes changed (re-encrypted with the new key).
    assert after["c1"] != before["c1"]
    assert after["c2"] != before["c2"]
    # Still vault envelopes.
    assert vault.is_encrypted(after["c1"])
    assert vault.is_encrypted(after["c2"])


def test_rotate_key_decrypt_still_works_after_rotation() -> None:
    """Channels reloaded after rotation transparently decrypt to the
    original plaintext targets."""
    ch1 = _make_channel(channel_id="c1", target="https://hooks/A")
    ch2 = _make_channel(channel_id="c2", target="https://hooks/B")
    save_channel(ch1, encrypt_target=True)
    save_channel(ch2, encrypt_target=True)

    assert vault.rotate_key() is True

    loaded = {ch.channel_id: ch.target for ch in load_channels()}
    assert loaded["c1"] == "https://hooks/A"
    assert loaded["c2"] == "https://hooks/B"


def test_rotate_key_leaves_plaintext_targets_untouched() -> None:
    """A channel saved with encrypt_target=False stays plaintext after
    rotation — the vault is opt-in per channel."""
    ch_plain = _make_channel(channel_id="plain", target="plaintext-target")
    save_channel(ch_plain)  # default encrypt_target=False
    ch_enc = _make_channel(channel_id="enc", target="encrypted-target")
    save_channel(ch_enc, encrypt_target=True)

    from state.db import get_connection
    conn = get_connection()
    plain_before = conn.execute(
        "SELECT target FROM delivery_channels WHERE channel_id = 'plain'"
    ).fetchone()["target"]

    assert vault.rotate_key() is True

    plain_after = conn.execute(
        "SELECT target FROM delivery_channels WHERE channel_id = 'plain'"
    ).fetchone()["target"]
    # Plaintext row is untouched.
    assert plain_after == plain_before == "plaintext-target"
    # Encrypted row is still encrypted (and decrypts cleanly).
    enc_after = conn.execute(
        "SELECT target FROM delivery_channels WHERE channel_id = 'enc'"
    ).fetchone()["target"]
    assert vault.is_encrypted(enc_after)
    assert vault.decrypt(enc_after) == "encrypted-target"


def test_rotate_key_never_raises_on_save_failure(monkeypatch) -> None:
    """If a single channel re-encrypt fails mid-rotation, rotate_key
    returns False (signals partial) but NEVER raises — the alert
    pipeline cannot afford a crash here.

    We can't monkeypatch ``sqlite3.Connection.execute`` (it's a read-
    only C attribute), so we inject the failure one layer up: make
    the per-channel re-encrypt helper throw by patching the keystream
    derivation. The first decrypt (under the old key) succeeds because
    that path uses the live kv_state read; only the post-rotation
    re-encrypt path goes through ``_derive_subkey`` with the freshly
    minted ``new_key``."""
    ch = _make_channel(channel_id="c1", target="https://hooks/A")
    save_channel(ch, encrypt_target=True)

    real_derive = vault._derive_subkey
    # Track the master-key bytes used in the FIRST call (decrypt under
    # old key); throw on any subsequent call (the re-encrypt step).
    seen: list[bytes] = []

    def selective_boom(master_key: bytes, nonce: bytes) -> bytes:
        if not seen:
            seen.append(master_key)
            return real_derive(master_key, nonce)
        if master_key != seen[0]:
            raise RuntimeError("synthetic re-encrypt failure")
        return real_derive(master_key, nonce)

    monkeypatch.setattr(vault, "_derive_subkey", selective_boom)

    # rotate_key must not raise; partial failure => False.
    result = vault.rotate_key()
    assert result is False


def test_rotate_key_with_no_channels_returns_true() -> None:
    """No channels in the DB → nothing to re-encrypt → success."""
    assert vault.rotate_key() is True


def test_rotate_key_audit_event_recorded(monkeypatch) -> None:
    """The audit hook must fire on rotate_key with channels_rerencrypted
    in the payload. Key material must NOT appear anywhere in the audit
    detail."""
    ch = _make_channel(channel_id="c1", target="https://hooks/A")
    save_channel(ch, encrypt_target=True)

    captured: list[dict] = []
    from auth import audit as audit_mod

    real_record = audit_mod.record_audit

    def spy(action, *, entity_type="", entity_id="", detail=None, user_id=None):
        captured.append({
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "detail": detail or {},
            "user_id": user_id,
        })
        return real_record(
            action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            user_id=user_id,
        )

    monkeypatch.setattr(audit_mod, "record_audit", spy)

    assert vault.rotate_key() is True

    rotate_events = [e for e in captured if e["action"] == "rotate_vault_key"]
    assert len(rotate_events) == 1
    detail = rotate_events[0]["detail"]
    assert detail.get("channels_rerencrypted") == 1
    # Key material must not leak into the audit payload.
    flat = repr(detail).lower()
    assert "key" not in flat or "channels_rerencrypted" in flat  # the only "key" allowed
    # Stronger: no hex master-key string in the detail.
    assert not any(
        isinstance(v, str) and len(v) >= 32 and all(c in "0123456789abcdef" for c in v.lower())
        for v in detail.values()
    )


# ─────────────────────────────────────────────────────────────────────────
#  save_channel / load_channels wiring
# ─────────────────────────────────────────────────────────────────────────

def test_save_channel_with_encrypt_target_true_persists_encrypted_target() -> None:
    """The persisted DB column is the vault envelope; the dataclass
    instance still carries the plaintext target so an immediate
    deliver_alert() call works."""
    ch = _make_channel(target="https://hooks.slack.com/secret-webhook")
    save_channel(ch, encrypt_target=True)

    # Dataclass instance is NOT mutated.
    assert ch.target == "https://hooks.slack.com/secret-webhook"

    # The DB column is wrapped.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT target FROM delivery_channels WHERE channel_id = ?",
        (ch.channel_id,),
    ).fetchone()
    assert vault.is_encrypted(row["target"])
    assert row["target"] != "https://hooks.slack.com/secret-webhook"


def test_load_channels_decrypts_transparently() -> None:
    """A channel saved with encrypt_target=True round-trips through
    load_channels() as the original plaintext."""
    ch = _make_channel(target="https://hooks.slack.com/super-secret")
    save_channel(ch, encrypt_target=True)

    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].target == "https://hooks.slack.com/super-secret"


def test_save_channel_default_encrypt_target_false_preserves_plaintext() -> None:
    """The default ``encrypt_target=False`` matches today's behaviour
    EXACTLY — the persisted target equals the plaintext, no envelope."""
    ch = _make_channel(target="https://hooks.slack.com/plain")
    save_channel(ch)  # no encrypt_target kwarg

    from state.db import get_connection
    row = get_connection().execute(
        "SELECT target FROM delivery_channels WHERE channel_id = ?",
        (ch.channel_id,),
    ).fetchone()
    assert row["target"] == "https://hooks.slack.com/plain"
    assert not vault.is_encrypted(row["target"])


def test_load_channels_bad_envelope_falls_back_to_raw_string() -> None:
    """If a row's target is mis-wrapped (e.g. master key lost / tampered
    envelope), load_channels falls back to the raw string and logs a
    WARNING — it must NOT crash the alert-routing UI on read."""
    # Insert a malformed vault envelope directly.
    from datetime import datetime, timezone
    from state.db import get_connection

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO delivery_channels
              (channel_id, name, kind, target, severity_threshold,
               enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad",
                "Bad envelope channel",
                "slack",
                "vault:v1:totally-not-valid-base64-or-hmac",
                "LOW",
                1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    loaded = load_channels()
    # Channel survives — we fall back to the raw stored value.
    bad_loaded = [c for c in loaded if c.channel_id == "bad"]
    assert len(bad_loaded) == 1
    # The fallback target is the raw envelope string (so the operator
    # can spot it in the UI and re-save).
    assert bad_loaded[0].target.startswith("vault:v1:")
