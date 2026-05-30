"""Tests for ``auth.rate_limit`` — the in-process token-bucket limiter.

Covers two surfaces:

  * ``TokenBucket`` directly — capacity initialisation, ``consume``,
    refill mechanics, the time-travel hook via ``monkeypatch`` on
    ``time.monotonic``, and per-instance isolation.
  * ``check_rate_limit`` / ``get_bucket`` / ``clear_buckets`` — the
    module-level API consumed by ``worker.api_server``.

The refill tests monkeypatch ``time.monotonic`` so the suite stays
deterministic and runs in milliseconds — sleeping for real would
either bloat CI or be flaky.
"""
from __future__ import annotations

import threading

import pytest

from auth import rate_limit as rl


@pytest.fixture(autouse=True)
def _reset_buckets():
    """Wipe the module-level registry around every test so an earlier
    test's bucket doesn't leak into the next test's count."""
    rl.clear_buckets()
    yield
    rl.clear_buckets()


# ─── TokenBucket primitives ───────────────────────────────────────────────


def test_bucket_starts_at_full_capacity():
    """A fresh bucket holds ``capacity`` tokens — first call can burst
    through up to N requests with no throttling."""
    bucket = rl.TokenBucket(capacity=5, refill_per_sec=1.0)
    assert bucket.tokens == pytest.approx(5.0)
    assert bucket.capacity == 5


def test_consume_one_decrements_by_one():
    """The default ``consume()`` takes one token off the balance."""
    bucket = rl.TokenBucket(capacity=3, refill_per_sec=1.0)
    assert bucket.consume() is True
    assert bucket.tokens == pytest.approx(2.0)


def test_consume_n_times_when_capacity_is_n_all_succeed(monkeypatch):
    """All N consumes succeed when starting from a full bucket of
    size N. Freeze time so refill doesn't sneak extra tokens in
    between the calls."""
    monkeypatch.setattr(rl.time, "monotonic", lambda: 1000.0)
    bucket = rl.TokenBucket(capacity=4, refill_per_sec=1.0)
    results = [bucket.consume() for _ in range(4)]
    assert results == [True, True, True, True]
    assert bucket.tokens == pytest.approx(0.0)


def test_consume_returns_false_when_empty(monkeypatch):
    """Once the bucket is drained, the next consume is denied."""
    monkeypatch.setattr(rl.time, "monotonic", lambda: 5000.0)
    bucket = rl.TokenBucket(capacity=2, refill_per_sec=1.0)
    assert bucket.consume() is True
    assert bucket.consume() is True
    # Drained — third call must deny.
    assert bucket.consume() is False
    # And the bucket level stays at zero (denied consume must not
    # decrement past zero).
    assert bucket.tokens == pytest.approx(0.0)


def test_bucket_refills_over_time(monkeypatch):
    """After draining, advancing the clock refills the bucket at
    ``refill_per_sec`` tokens/sec."""
    now = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: now[0])
    bucket = rl.TokenBucket(capacity=10, refill_per_sec=2.0)
    # Drain the bucket.
    for _ in range(10):
        assert bucket.consume() is True
    assert bucket.consume() is False
    # Advance 3 seconds → 6 tokens refilled.
    now[0] = 1003.0
    # Next consume should succeed since 6 >= 1.
    assert bucket.consume() is True
    # And the remainder should be ~5 (6 refilled, 1 consumed).
    assert bucket.tokens == pytest.approx(5.0, abs=1e-6)


def test_refill_caps_at_capacity(monkeypatch):
    """A long idle window can't fill the bucket above ``capacity`` —
    bursts are bounded by the bucket design, not by elapsed time."""
    now = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: now[0])
    bucket = rl.TokenBucket(capacity=5, refill_per_sec=1.0)
    # Drain.
    for _ in range(5):
        bucket.consume()
    # Sleep an hour — would produce 3600 tokens uncapped.
    now[0] = 4600.0
    # Force a refill via a denied-then-allow consume, then inspect.
    assert bucket.consume() is True
    # 5 (cap) - 1 (consumed) = 4 left, NOT 3599.
    assert bucket.tokens == pytest.approx(4.0)


# ─── check_rate_limit (public API) ────────────────────────────────────────


def test_check_rate_limit_allows_up_to_capacity_bursts(monkeypatch):
    """``check_rate_limit`` returns ``(True, 0.0)`` for the first N
    calls when capacity=N."""
    monkeypatch.setattr(rl.time, "monotonic", lambda: 2000.0)
    user_id = "alice"
    for i in range(8):
        allowed, retry = rl.check_rate_limit(
            user_id, capacity=8, refill_per_sec=1.0,
        )
        assert allowed is True, f"call {i} unexpectedly denied"
        assert retry == 0.0
    # 9th call denied.
    allowed, retry = rl.check_rate_limit(
        user_id, capacity=8, refill_per_sec=1.0,
    )
    assert allowed is False


def test_check_rate_limit_returns_retry_after_with_right_magnitude(monkeypatch):
    """When denied, ``retry_after_seconds`` ≈ 1/refill_per_sec for a
    one-token deficit. With refill=2.0, retry ≈ 0.5s."""
    monkeypatch.setattr(rl.time, "monotonic", lambda: 3000.0)
    user_id = "bob"
    # Drain the bucket of capacity=2.
    rl.check_rate_limit(user_id, capacity=2, refill_per_sec=2.0)
    rl.check_rate_limit(user_id, capacity=2, refill_per_sec=2.0)
    # Now the bucket has 0 tokens; the next denial should quote
    # retry_after ≈ 1 token / 2 tokens-per-sec = 0.5s.
    allowed, retry = rl.check_rate_limit(
        user_id, capacity=2, refill_per_sec=2.0,
    )
    assert allowed is False
    assert retry == pytest.approx(0.5, abs=1e-6)


def test_per_user_id_isolation(monkeypatch):
    """Alice's bucket is independent of Bob's — exhausting one does
    NOT throttle the other."""
    monkeypatch.setattr(rl.time, "monotonic", lambda: 4000.0)
    # Drain alice completely.
    for _ in range(3):
        ok, _ = rl.check_rate_limit("alice", capacity=3, refill_per_sec=1.0)
        assert ok is True
    ok, _ = rl.check_rate_limit("alice", capacity=3, refill_per_sec=1.0)
    assert ok is False
    # Bob's bucket is still fresh — first call succeeds.
    ok, _ = rl.check_rate_limit("bob", capacity=3, refill_per_sec=1.0)
    assert ok is True


def test_get_bucket_returns_same_instance_for_same_user_id():
    """Lazy creation is idempotent — repeat lookups return the
    same TokenBucket so state actually accumulates."""
    b1 = rl.get_bucket("carol", capacity=10, refill_per_sec=1.0)
    b2 = rl.get_bucket("carol", capacity=99, refill_per_sec=99.0)
    assert b1 is b2
    # And the second call's params are IGNORED — we keep the
    # original bucket's config.
    assert b1.capacity == 10


def test_clear_buckets_resets_state():
    """``clear_buckets`` wipes the registry so a fresh user_id lookup
    after a clear yields a brand-new bucket."""
    b1 = rl.get_bucket("dave", capacity=5, refill_per_sec=1.0)
    assert b1.consume() is True
    assert b1.tokens == pytest.approx(4.0)
    rl.clear_buckets()
    b2 = rl.get_bucket("dave", capacity=5, refill_per_sec=1.0)
    # New instance — different object, fresh balance.
    assert b2 is not b1
    assert b2.tokens == pytest.approx(5.0)


def test_reset_bucket_drops_single_key():
    """``reset_bucket`` drops ONE key so its next lookup starts full,
    without disturbing other keys (unlike ``clear_buckets``)."""
    drained = rl.get_bucket("eve", capacity=5, refill_per_sec=1.0)
    other = rl.get_bucket("frank", capacity=5, refill_per_sec=1.0)
    assert drained.consume() is True
    assert other.consume() is True
    assert drained.tokens == pytest.approx(4.0)

    rl.reset_bucket("eve")

    # eve's bucket is recreated full on next access…
    fresh = rl.get_bucket("eve", capacity=5, refill_per_sec=1.0)
    assert fresh is not drained
    assert fresh.tokens == pytest.approx(5.0)
    # …but frank's is untouched (same instance, balance preserved).
    assert rl.get_bucket("frank", capacity=5, refill_per_sec=1.0) is other
    assert other.tokens == pytest.approx(4.0)


def test_reset_bucket_unknown_key_is_noop():
    """Resetting a key that was never created must not raise."""
    rl.reset_bucket("never-seen-this-key")  # no exception


def test_thread_safe_concurrent_consume():
    """Hammer one bucket from N threads, each calling ``consume(1)``
    M times. The total allowed count should equal ``capacity`` — not
    more (races would over-grant) and not less (lock starvation
    shouldn't drop legitimate consumes when tokens are available)."""
    capacity = 200
    bucket = rl.TokenBucket(capacity=capacity, refill_per_sec=0.001)
    # Use a tiny refill rate so we don't accidentally count refilled
    # tokens — the test wants to assert "exactly capacity bursts get
    # through before throttling kicks in".

    allowed_count = [0]
    counter_lock = threading.Lock()
    n_threads = 20
    calls_per_thread = 30  # 20 * 30 = 600 attempts vs capacity 200

    def worker():
        local_allowed = 0
        for _ in range(calls_per_thread):
            if bucket.consume(1):
                local_allowed += 1
        with counter_lock:
            allowed_count[0] += local_allowed

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly capacity tokens were available across all attempts; the
    # lock guarantees no over-grant. We allow a small +tolerance for
    # refill that may have happened during execution (refill is 0.001
    # tokens/sec so this is essentially 0 in a fast test).
    assert allowed_count[0] >= capacity
    assert allowed_count[0] <= capacity + 5  # generous slack for refill


# ─── Input validation ─────────────────────────────────────────────────────


def test_invalid_capacity_raises():
    """The constructor rejects malformed capacity at construction
    time — better a clear failure than a silently broken bucket."""
    with pytest.raises(ValueError):
        rl.TokenBucket(capacity=0, refill_per_sec=1.0)
    with pytest.raises(ValueError):
        rl.TokenBucket(capacity=-1, refill_per_sec=1.0)


def test_invalid_refill_raises():
    """Same as capacity — zero/negative refill is rejected."""
    with pytest.raises(ValueError):
        rl.TokenBucket(capacity=5, refill_per_sec=0)
    with pytest.raises(ValueError):
        rl.TokenBucket(capacity=5, refill_per_sec=-0.5)


def test_consume_zero_or_negative_returns_false():
    """A malformed cost (<=0) is rejected without touching the
    bucket. Prevents a buggy caller from sneaking through an
    'allowed' response without consuming tokens."""
    bucket = rl.TokenBucket(capacity=10, refill_per_sec=1.0)
    assert bucket.consume(0) is False
    assert bucket.consume(-1) is False
    # Bucket level untouched.
    assert bucket.tokens == pytest.approx(10.0)
