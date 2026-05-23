"""Entry point so ``python -m tools.ops`` works the same as
``python -m tools.ops_cli``.

The CLI implementation lives in :mod:`tools.ops_cli` — keeping the
module ``tools.ops`` as the spec'd invocation while the implementation
lives next door lets tests import ``tools.ops_cli`` directly (avoiding
the ``__main__`` re-entry path) and lets operators type the shorter
form.
"""
from __future__ import annotations

import sys

from tools.ops_cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
