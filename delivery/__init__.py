"""delivery/ — pre-rendered artifacts for downstream delivery channels.

Modules in this package produce ready-to-send payloads (HTML, plain
text, subject lines) from upstream analytical results. They do NOT
open SMTP / HTTP connections themselves — the artifacts are the
deliverable, and the downstream channels in ``engine.alert_delivery``
+ ``engine.operator_digest`` pick them up.

Why a separate package
----------------------
Snapshot jobs (``processing.port_supply_history``) own the data; the
existing ``engine.*_digest`` modules own the transport. Sitting in
between is the rendering step — pure-function, no side effects, no
network. Splitting it out keeps the snapshot job independent of any
specific channel and lets new channels (Teams, RSS, anything that
takes HTML) pick up the same artifacts without touching the analytics.
"""
