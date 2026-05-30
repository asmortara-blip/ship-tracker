"""auth.rate_limit — per-user in-process token-bucket rate limiting.

Design
------
``worker.api_server`` runs as a single stdlib-``http.server`` process per
container. A misbehaving client (or a leaked token) can hammer the
authenticated endpoints in a tight loop and either starve the process
of CPU or fill SQLite with churn from the audit-log writes the engine
emits on every request. This module gives the dispatcher a cheap
``check_rate_limit(user_id)`` call that says yes/no per request and
emits a ``Retry-After`` interval when the answer is no.

The algorithm is classic token bucket:

  tokens_available = min(capacity, tokens + elapsed * refill_per_sec)
  if tokens_available >= cost:
      consume cost tokens, allow
  else:
      compute retry_after = (cost - tokens_available) / refill_per_sec
      deny

Refill is lazy — there is no background thread. Every call to
``consume`` recomputes the bucket level from ``time.monotonic()`` and
the stored ``last_refill``. The bucket starts full so a fresh user
can burst up to ``capacity`` requests before being throttled, then
settles into the steady-state ``refill_per_sec`` rate.

Concurrency
-----------
The HTTP server uses ``BaseHTTPRequestHandler`` which is invoked once
per connection. A multi-threaded dispatcher (``ThreadingHTTPServer``)
may call ``check_rate_limit`` for the SAME user_id from multiple
threads simultaneously. Each ``TokenBucket`` owns a ``threading.Lock``
so the read-refill-write of the bucket level is atomic; the
module-level ``_BUCKETS_LOCK`` only guards the dict insert in
``get_bucket`` (the dict is then read lock-free, since per-key locking
on the bucket handles the actual atomicity).

What this module does NOT do
----------------------------
* No distributed coordination. Single-process, single-worker only.
  If we go horizontal, swap this for a Redis-backed limiter that
  shares state across pods. The public API
  (``check_rate_limit(user_id, *, capacity, refill_per_sec)``) is
  designed to remain stable across that swap.
* No background refill. Refill is computed lazily on each ``consume``
  call — no daemon thread to leak on shutdown.
* No persistence. Buckets live in process memory; restarts reset the
  limit (which is fine — the rate limit is best-effort DoS protection,
  not a fairness contract).
* No per-endpoint differentiation in the public surface. The dispatch
  layer can keep multiple bucket configs per user_id by passing
  different ``capacity`` / ``refill_per_sec`` keys, but the module
  itself is shape-agnostic.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


# ── Module-level state ────────────────────────────────────────────────────

# user_id -> TokenBucket. Lazy-created on first access so a user who
# never hits the API never allocates a bucket. The dict insert happens
# under ``_BUCKETS_LOCK``; subsequent reads of an already-present key
# are lock-free (Python dict reads are atomic under the GIL — the
# concern is only the insert race when two threads hit ``get_bucket``
# for a brand-new user_id at the same instant).
_BUCKETS: dict[str, "TokenBucket"] = {}
_BUCKETS_LOCK = threading.Lock()


# ── TokenBucket ───────────────────────────────────────────────────────────


class TokenBucket:
    """A classic token bucket.

    Parameters
    ----------
    capacity:
        Maximum number of tokens the bucket can hold. Also the
        starting balance — a fresh bucket is full so the first
        ``capacity`` calls burst through without throttling.
    refill_per_sec:
        Tokens added per second of wall-clock elapsed. Implemented as
        a float so sub-integer rates ("0.5 tokens/sec = 1 request
        every 2s") are expressible without rounding.

    Notes
    -----
    * The bucket uses ``time.monotonic()`` (NOT ``time.time()``) so
      NTP corrections / DST jumps cannot retroactively grant or revoke
      tokens. ``time.monotonic`` is guaranteed non-decreasing within a
      process.
    * ``consume`` is atomic via ``threading.Lock``. The check, refill,
      and decrement happen in one critical section so a concurrent
      caller cannot observe an intermediate "refilled but not yet
      consumed" state.
    """

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        if not isinstance(capacity, int) or capacity <= 0:
            raise ValueError(
                f"capacity must be a positive int, got {capacity!r}"
            )
        if not isinstance(refill_per_sec, (int, float)) or refill_per_sec <= 0:
            raise ValueError(
                f"refill_per_sec must be a positive number, "
                f"got {refill_per_sec!r}"
            )
        self.capacity: int = capacity
        self.refill_per_sec: float = float(refill_per_sec)
        # Float on purpose — refill can leave a fractional token in the
        # bucket between calls; rounding to int here would systematically
        # under-refill at high cadences.
        self.tokens: float = float(capacity)
        self.last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    # ── Internal ──────────────────────────────────────────────────────────

    def _refill(self) -> None:
        """Recompute the bucket level from elapsed wall-clock.

        Called from inside ``consume`` under ``self._lock``. Capped at
        ``capacity`` so a long idle window doesn't allow a burst
        larger than the bucket's design size.
        """
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed <= 0:
            # Monotonic clock isn't supposed to go backwards, but defensive
            # — at worst this skips a refill cycle that was about to happen.
            return
        self.tokens = min(
            float(self.capacity),
            self.tokens + elapsed * self.refill_per_sec,
        )
        self.last_refill = now

    # ── Public ────────────────────────────────────────────────────────────

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume ``tokens`` tokens. Returns True on success.

        Atomic check-and-decrement: a concurrent caller cannot
        observe a state where the refill happened but the decrement
        didn't. ``tokens`` defaults to 1 so the common one-request-
        one-token call is the natural shape.

        Returns False when there aren't enough tokens to satisfy the
        cost; the bucket level is left unchanged on a denied consume
        (so a follow-up call after waiting can succeed).
        """
        if not isinstance(tokens, int) or tokens <= 0:
            # Cheap reject — never lock for a malformed cost.
            return False
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def time_until_available(self, tokens: int = 1) -> float:
        """Seconds to wait before ``tokens`` tokens are available.

        Returns 0.0 when the bucket already has enough. Used by
        ``check_rate_limit`` to compute the ``Retry-After`` value
        that goes on the 429 response.

        Does NOT consume — read-only inspection.
        """
        if tokens <= 0:
            return 0.0
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                return 0.0
            deficit = tokens - self.tokens
            return deficit / self.refill_per_sec


# ── Public API ────────────────────────────────────────────────────────────


def get_bucket(
    user_id: str,
    *,
    capacity: int,
    refill_per_sec: float,
) -> TokenBucket:
    """Return the ``TokenBucket`` for ``user_id``; create on first access.

    The ``capacity`` and ``refill_per_sec`` arguments are used ONLY
    on first creation — subsequent calls with the same ``user_id``
    return the existing bucket regardless of what parameters are
    passed. This is intentional: changing a user's bucket size at
    runtime would require either flushing their current balance
    (handing back tokens they shouldn't have) or capping (revoking
    tokens already in their balance). Both surprise the caller. If
    you need to change defaults, restart the process.
    """
    # Fast path: existing bucket. Reading the dict is atomic under the
    # GIL so we don't need to hold the lock for the read.
    bucket = _BUCKETS.get(user_id)
    if bucket is not None:
        return bucket
    # Slow path: create under the lock. The double-check inside the
    # lock guards against two threads racing on a brand-new user_id.
    with _BUCKETS_LOCK:
        bucket = _BUCKETS.get(user_id)
        if bucket is None:
            bucket = TokenBucket(capacity, refill_per_sec)
            _BUCKETS[user_id] = bucket
        return bucket


def check_rate_limit(
    user_id: str,
    *,
    capacity: int = 60,
    refill_per_sec: float = 1.0,
) -> tuple[bool, float]:
    """Atomic rate-limit check for ``user_id``.

    Returns ``(allowed, retry_after_seconds)``:

      * ``(True, 0.0)`` — the request fits in the user's bucket and
        one token has been consumed.
      * ``(False, N)`` — the bucket was empty; N is the wall-clock
        seconds to wait before another request would succeed. The
        value is computed from the bucket's refill rate so callers
        can put it on a ``Retry-After`` header per RFC 7231 §7.1.3.

    Defaults (``capacity=60``, ``refill_per_sec=1.0``) are mild — they
    allow a burst of 60 requests followed by a sustained 1 req/sec.
    The API server calls this with overrides matching its tuning
    (``RATE_LIMIT_CAPACITY`` / ``RATE_LIMIT_REFILL_PER_SEC`` env vars).
    """
    bucket = get_bucket(
        user_id, capacity=capacity, refill_per_sec=refill_per_sec,
    )
    if bucket.consume(1):
        return True, 0.0
    return False, bucket.time_until_available(1)


def clear_buckets() -> None:
    """Reset the module-level bucket registry. For tests only.

    Production code MUST NOT call this — flushing the registry would
    give every user a fresh burst allowance, which is exactly the
    "you can dodge the rate limit by triggering a clear" footgun the
    limit is meant to prevent. The tests rely on it because the
    module-level dict otherwise leaks state across test cases.
    """
    with _BUCKETS_LOCK:
        _BUCKETS.clear()


def reset_bucket(user_id: str) -> None:
    """Drop one key's bucket so its NEXT access starts full again.

    Unlike ``clear_buckets`` (all keys, tests only), this resets a
    single key and IS safe in production for one specific pattern:
    resetting a *failed-attempt* counter after a SUCCESSFUL
    authentication. ``auth.users.login`` consumes a token per attempt
    and calls this on success, so the bucket tracks *consecutive*
    failures — a legitimate user who eventually logs in clears their
    own budget, and only an unbroken run of failures (a brute-force
    flood) can drain it to the throttle threshold.

    Do NOT call this on the *failure* path — that would defeat the
    limit. No-op for an unknown key.
    """
    with _BUCKETS_LOCK:
        _BUCKETS.pop(user_id, None)


__all__ = [
    "TokenBucket",
    "check_rate_limit",
    "clear_buckets",
    "get_bucket",
    "reset_bucket",
]
