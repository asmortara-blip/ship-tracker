"""auth/ids.py — opaque identifier generation.

Single source of truth for the random, opaque IDs the platform hands out
and later accepts back as arguments — user ids (``user_id``) and API-token
ids (``token_id``) today. These IDs are routinely passed to the operator
CLI as positional / option arguments, e.g.::

    ops_cli silences create --user-id <user_id>
    ops_cli tokens revoke <token_id>

so they MUST be safe to use as a CLI argument.

``secrets.token_urlsafe`` draws from the URL-safe base64 alphabet
``[A-Za-z0-9_-]``, which means ~1 in 64 tokens *begins* with ``-``.
argparse — and most CLIs — read a leading ``-`` as an option flag and
reject the whole invocation (exit 2), so such an id is unusable as an
argument. That surfaced as an intermittent ``ops_cli`` failure: roughly
once per full test run a freshly-signed-up user's id started with ``-``
and every ``--user-id <id>`` invocation in that test died with exit 2.

``opaque_id`` returns a ``token_urlsafe`` value with the SAME length,
alphabet, and ~``nbytes * 8`` bits of entropy — the only added
constraint is that the first character is never ``-``. (URL secrets that
live only in links, e.g. invite/calendar tokens, keep using
``token_urlsafe`` directly; they are never CLI arguments.)
"""
from __future__ import annotations

import secrets


__all__ = ["opaque_id"]


def opaque_id(nbytes: int = 16) -> str:
    """A ``secrets.token_urlsafe(nbytes)`` value that is CLI-argument safe.

    Identical length / alphabet / entropy to ``token_urlsafe`` except the
    leading character is never ``-`` (which argparse would mistake for an
    option flag). The rejection rate is ~1/64, so the expected number of
    underlying draws is ~1.016 — negligible, and entropy loss from the
    conditioning is < 0.03 bits.

    Raises ``ValueError`` for ``nbytes < 1``. (``token_urlsafe(0)`` returns
    ``""`` — without this guard the dash check would never be satisfied and
    the loop would spin forever; fail fast instead.)
    """
    if nbytes < 1:
        raise ValueError(f"opaque_id: nbytes must be >= 1, got {nbytes}")
    while True:
        # nbytes >= 1 guarantees a non-empty token, so token[0] is safe.
        token = secrets.token_urlsafe(nbytes)
        if token[0] != "-":
            return token
