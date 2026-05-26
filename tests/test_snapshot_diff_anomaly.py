"""Defining-property tests for processing/snapshot_diff_anomaly.py.

Per-test isolation via SNAPSHOT_ROOT monkeypatch + tmp_path — no test
leaves state on disk that could leak into another test run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest

from processing.snapshot_diff_anomaly import (
    AnomalyScore,
    DiffMagnitudeRecord,
    MAD_TO_STDEV,
    WEIGHT_DEFICIT_MOVE,
    WEIGHT_ENTERED_DEFICIT,
    WEIGHT_EXITED_DEFICIT,
    WEIGHT_SEVERITY_SHIFT,
    WEIGHT_TICKER_SHUFFLE,
    build_history_from_snapshots,
    compute_diff_magnitude,
    score_anomaly,
)


# ---------------------------------------------------------------------------
# Test helpers — minimal stand-ins for DiffReport + PortDelta
# ---------------------------------------------------------------------------


@dataclass
class _FakeDelta:
    """Stand-in for ``tools.port_supply_diff.PortDelta`` — only the
    fields the anomaly detector reads off (none, currently — it only
    len()s the buckets). Empty class is enough."""
    pass


@dataclass
class _FakeDiff:
    """Stand-in for ``tools.port_supply_diff.DiffReport`` — only the
    five list-shaped buckets the magnitude reducer reads."""
    severity_shifts: list = field(default_factory=list)
    entered_deficit: list = field(default_factory=list)
    exited_deficit: list = field(default_factory=list)
    deficit_moves: list = field(default_factory=list)
    ticker_shuffles: list = field(default_factory=list)


def _make_diff(
    severity: int = 0,
    entered: int = 0,
    exited: int = 0,
    moves: int = 0,
    shuffles: int = 0,
) -> _FakeDiff:
    """Build a fake diff with N entries per bucket."""
    return _FakeDiff(
        severity_shifts=[_FakeDelta() for _ in range(severity)],
        entered_deficit=[_FakeDelta() for _ in range(entered)],
        exited_deficit=[_FakeDelta() for _ in range(exited)],
        deficit_moves=[_FakeDelta() for _ in range(moves)],
        ticker_shuffles=[_FakeDelta() for _ in range(shuffles)],
    )


def _flat_history(n: int, value: float = 5.0) -> list[DiffMagnitudeRecord]:
    """Build a flat trailing history of N records all sharing the same
    composite magnitude."""
    return [
        DiffMagnitudeRecord(
            date_iso=f"2026-04-{(i % 28) + 1:02d}",
            severity_shifts=0,
            entered_deficit=0,
            exited_deficit=0,
            deficit_moves=int(value),
            ticker_shuffles=0,
            composite_magnitude=float(value),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. compute_diff_magnitude — weighting + edge cases
# ---------------------------------------------------------------------------


def test_compute_magnitude_all_zero_yields_zero_composite() -> None:
    """An all-zero diff has composite_magnitude=0.0 — no signal."""
    rec = compute_diff_magnitude(_make_diff())
    assert isinstance(rec, DiffMagnitudeRecord)
    assert rec.severity_shifts == 0
    assert rec.entered_deficit == 0
    assert rec.exited_deficit == 0
    assert rec.deficit_moves == 0
    assert rec.ticker_shuffles == 0
    assert rec.composite_magnitude == 0.0


def test_compute_magnitude_single_severity_shift_weighted_3() -> None:
    """One severity shift, zero everything else → composite = 3.0."""
    rec = compute_diff_magnitude(_make_diff(severity=1))
    assert rec.composite_magnitude == 3.0
    assert rec.severity_shifts == 1


def test_compute_magnitude_single_ticker_shuffle_weighted_half() -> None:
    """One ticker shuffle, zero everything else → composite = 0.5."""
    rec = compute_diff_magnitude(_make_diff(shuffles=1))
    assert rec.composite_magnitude == 0.5
    assert rec.ticker_shuffles == 1


def test_compute_magnitude_single_entered_deficit_weighted_2() -> None:
    """entered_deficit weight is 2.0."""
    rec = compute_diff_magnitude(_make_diff(entered=1))
    assert rec.composite_magnitude == 2.0


def test_compute_magnitude_single_exited_deficit_weighted_1() -> None:
    """exited_deficit weight is 1.0."""
    rec = compute_diff_magnitude(_make_diff(exited=1))
    assert rec.composite_magnitude == 1.0


def test_compute_magnitude_single_deficit_move_weighted_1() -> None:
    """deficit_moves weight is 1.0."""
    rec = compute_diff_magnitude(_make_diff(moves=1))
    assert rec.composite_magnitude == 1.0


def test_compute_magnitude_combined_buckets_sum_correctly() -> None:
    """A multi-bucket diff sums weighted contributions:
    2 severity (×3) + 1 entered (×2) + 3 ticker_shuffles (×0.5)
    = 6 + 2 + 1.5 = 9.5."""
    rec = compute_diff_magnitude(_make_diff(severity=2, entered=1, shuffles=3))
    assert rec.composite_magnitude == 9.5
    assert rec.severity_shifts == 2
    assert rec.entered_deficit == 1
    assert rec.ticker_shuffles == 3


def test_compute_magnitude_weight_constants_match_spec() -> None:
    """Pin the published weight constants — downstream consumers (the
    digest agent) rely on these numbers."""
    assert WEIGHT_SEVERITY_SHIFT == 3.0
    assert WEIGHT_ENTERED_DEFICIT == 2.0
    assert WEIGHT_EXITED_DEFICIT == 1.0
    assert WEIGHT_DEFICIT_MOVE == 1.0
    assert WEIGHT_TICKER_SHUFFLE == 0.5


def test_compute_magnitude_defensive_against_missing_bucket() -> None:
    """If a partial diff has only severity_shifts populated (e.g. an
    older serialization), the reducer must not crash — missing buckets
    count as zero."""
    @dataclass
    class _Partial:
        severity_shifts: list = field(default_factory=lambda: [_FakeDelta()])
        # All other buckets intentionally absent

    rec = compute_diff_magnitude(_Partial())
    assert rec.severity_shifts == 1
    assert rec.entered_deficit == 0
    assert rec.composite_magnitude == WEIGHT_SEVERITY_SHIFT


# ---------------------------------------------------------------------------
# 2. score_anomaly — empty history, flat history, normal/elevated/shock
# ---------------------------------------------------------------------------


def test_score_empty_history_returns_normal_with_defaults() -> None:
    """Empty history → AnomalyScore with z=0, normal band, explanation
    that names the missing-history condition. No raise."""
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=5, entered_deficit=2, exited_deficit=0,
        deficit_moves=4, ticker_shuffles=3,
        composite_magnitude=22.5,
    )
    out = score_anomaly(today, [])
    assert isinstance(out, AnomalyScore)
    assert out.date_iso == "2026-05-26"
    assert out.composite_magnitude == 22.5
    assert out.rolling_median == 0.0
    assert out.rolling_mad == 0.0
    assert out.mad_z_score == 0.0
    assert out.is_anomaly is False
    assert out.anomaly_band == "normal"
    assert "no history" in out.explanation


def test_score_today_equals_median_yields_z_zero_normal() -> None:
    """When today's composite == rolling median, z=0 and band=normal —
    the central case anchoring the rest of the test family."""
    history = _flat_history(20, value=5.0)
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=5, ticker_shuffles=0,
        composite_magnitude=5.0,
    )
    out = score_anomaly(today, history)
    assert out.mad_z_score == 0.0
    assert out.anomaly_band == "normal"
    assert out.is_anomaly is False
    assert out.rolling_median == 5.0


def test_score_huge_today_vs_flat_history_yields_shock() -> None:
    """The shock-day defining property: a flat trailing history with a
    massive today must produce band='shock' + is_anomaly=True."""
    # Build a noisy-but-low-magnitude trailing window so MAD > 0.
    history = [
        DiffMagnitudeRecord(
            date_iso=f"2026-04-{i+1:02d}",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=0, ticker_shuffles=0,
            composite_magnitude=float(v),
        )
        for i, v in enumerate([3, 4, 5, 4, 3, 5, 4, 3, 5, 4,
                                3, 4, 5, 4, 3, 5, 4, 3, 5, 4])
    ]
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=20, entered_deficit=10, exited_deficit=0,
        deficit_moves=15, ticker_shuffles=8,
        composite_magnitude=99.0,
    )
    out = score_anomaly(today, history)
    assert out.anomaly_band == "shock"
    assert out.is_anomaly is True
    assert out.mad_z_score > 3.0
    assert "shock" in out.explanation


def test_score_elevated_band_for_moderate_deviation() -> None:
    """Between 1 and z_threshold MADs from median → 'elevated'."""
    # History with spread so MAD > 0. Values: [3,4,5,6,7,3,4,5,6,7,...]
    # → median=5, deviations=[2,1,0,1,2,2,1,0,1,2,...] → MAD=1.
    history = [
        DiffMagnitudeRecord(
            date_iso=f"2026-04-{i+1:02d}",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=int(v), ticker_shuffles=0,
            composite_magnitude=float(v),
        )
        for i, v in enumerate([3, 4, 5, 6, 7, 3, 4, 5, 6, 7,
                                3, 4, 5, 6, 7, 3, 4, 5, 6, 7])
    ]
    # Median=5, MAD=1. Today=8 → z = 3 / (1.4826 * 1) ≈ 2.02 → elevated.
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=8, ticker_shuffles=0,
        composite_magnitude=8.0,
    )
    out = score_anomaly(today, history, z_threshold=3.0)
    assert out.anomaly_band == "elevated"
    assert out.is_anomaly is True
    assert 1.0 < abs(out.mad_z_score) <= 3.0


def test_score_normal_band_for_small_deviation() -> None:
    """|z| ≤ 1 → 'normal' even though today differs from median."""
    history = [
        DiffMagnitudeRecord(
            date_iso=f"2026-04-{i+1:02d}",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=int(v), ticker_shuffles=0,
            composite_magnitude=float(v),
        )
        for i, v in enumerate([3, 5, 7, 5, 3, 5, 7, 5, 3, 5,
                                7, 5, 3, 5, 7, 5, 3, 5, 7, 5])
    ]
    # Median=5, MAD=2. Today=6 → z = 1 / (1.4826 * 2) ≈ 0.34 → normal.
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=6, ticker_shuffles=0,
        composite_magnitude=6.0,
    )
    out = score_anomaly(today, history)
    assert out.anomaly_band == "normal"
    assert out.is_anomaly is False


# ---------------------------------------------------------------------------
# 3. MAD robustness — the whole point of this module
# ---------------------------------------------------------------------------


def test_mad_robust_to_single_outlier_in_history() -> None:
    """The defining property of MAD-based scoring: a single huge outlier
    in the trailing window does NOT inflate the threshold. A subsequent
    shock day still scores as a shock.

    Compare with mean+stdev: a single 1000-magnitude outlier in a window
    of 5s would push stdev up so far that even a 200-magnitude follow-up
    looks normal. MAD shrugs off the outlier and keeps the threshold
    near the actual centre."""
    # 19 calm days + 1 historical outlier
    history = _flat_history(19, value=5.0)
    history.append(
        DiffMagnitudeRecord(
            date_iso="2026-04-30",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=0, ticker_shuffles=0,
            composite_magnitude=1000.0,   # massive historical shock
        )
    )

    # Today is a follow-up shock, much smaller than the historical one
    # but still huge vs the calm baseline.
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=20, entered_deficit=10, exited_deficit=0,
        deficit_moves=15, ticker_shuffles=8,
        composite_magnitude=80.0,
    )

    out = score_anomaly(today, history)

    # With MAD, the median is still ~5 (since 19 of 20 days are 5s) and
    # the MAD is small — today's 80 is many MADs above median → shock.
    # If this were mean+stdev, the 1000 outlier would have ballooned
    # stdev so today's 80 looks "normal-ish".
    assert out.anomaly_band == "shock"
    assert out.is_anomaly is True
    assert out.rolling_median == pytest.approx(5.0, abs=1.0)


def test_mad_normalisation_uses_1_4826_scale_factor() -> None:
    """The module's MAD_TO_STDEV constant equals 1.4826 (the standard
    consistency constant for MAD-as-stdev under a normal distribution).
    Pinned to flag any accidental drift in the formula."""
    assert MAD_TO_STDEV == 1.4826


def test_score_handles_flat_history_today_equal_median() -> None:
    """Edge case: history is perfectly flat (MAD=0) AND today equals
    that flat value → z=0, band=normal. Must not divide by zero."""
    history = _flat_history(10, value=7.0)
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=7, ticker_shuffles=0,
        composite_magnitude=7.0,
    )
    out = score_anomaly(today, history)
    assert out.rolling_mad == 0.0
    assert out.mad_z_score == 0.0
    assert out.anomaly_band == "normal"


def test_score_handles_flat_history_today_deviates() -> None:
    """Edge case: history is perfectly flat (MAD=0) but today differs.
    Any deviation from a flat baseline IS the signal — band=shock.
    Must not divide by zero."""
    history = _flat_history(10, value=7.0)
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=10, entered_deficit=0, exited_deficit=0,
        deficit_moves=0, ticker_shuffles=0,
        composite_magnitude=30.0,
    )
    out = score_anomaly(today, history)
    assert out.rolling_mad == 0.0
    assert out.anomaly_band == "shock"
    assert out.is_anomaly is True


# ---------------------------------------------------------------------------
# 4. Window clamping
# ---------------------------------------------------------------------------


def test_window_clamping_only_uses_last_n_records() -> None:
    """window_days=30 with 100 records → median computed over the last
    30 only. Verified by constructing a history where the first 70 days
    have one median and the last 30 have a wildly different one — the
    rolling median must reflect the last 30."""
    # First 70 records all 100.0 (would push median to 100 if used)
    old_records = _flat_history(70, value=100.0)
    # Last 30 records all 5.0 (the actual relevant window)
    recent_records = _flat_history(30, value=5.0)
    history = old_records + recent_records

    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=5, ticker_shuffles=0,
        composite_magnitude=5.0,
    )

    out = score_anomaly(today, history, window_days=30)
    # The rolling median must reflect the trailing 30 days (all 5.0),
    # NOT the older 70 days (all 100.0).
    assert out.rolling_median == 5.0
    assert out.mad_z_score == 0.0
    assert out.anomaly_band == "normal"


def test_window_clamping_short_history_uses_all_available() -> None:
    """If history is shorter than window_days, all available records
    are used (no padding, no error)."""
    history = _flat_history(5, value=10.0)   # only 5 records, window=30
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=10, ticker_shuffles=0,
        composite_magnitude=10.0,
    )
    out = score_anomaly(today, history, window_days=30)
    assert out.rolling_median == 10.0
    assert out.anomaly_band == "normal"


def test_window_clamping_handles_window_one() -> None:
    """window_days=1 → only the most recent record contributes."""
    # Older records all 100, most recent record = 5.
    history = _flat_history(10, value=100.0)
    history.append(
        DiffMagnitudeRecord(
            date_iso="2026-05-25",
            severity_shifts=0, entered_deficit=0, exited_deficit=0,
            deficit_moves=5, ticker_shuffles=0,
            composite_magnitude=5.0,
        )
    )
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=5, ticker_shuffles=0,
        composite_magnitude=5.0,
    )
    out = score_anomaly(today, history, window_days=1)
    # With window=1, median is just the latest record's value (5.0),
    # not the bulk-100 history.
    assert out.rolling_median == 5.0


# ---------------------------------------------------------------------------
# 5. build_history_from_snapshots — walks snapshots + excludes today
# ---------------------------------------------------------------------------


def test_build_history_returns_n_minus_one_when_n_snapshots_planted(
    tmp_path, monkeypatch,
) -> None:
    """Plant N snapshots ending at today, ask for history → N-1 records
    returned (excludes today itself, plus the very-first snapshot has
    no prior to diff against)."""
    # Isolate the snapshot root for this test.
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)

    from processing.port_supply_history import save_snapshot

    today = date(2026, 5, 26)
    # Plant 5 consecutive daily snapshots: 5/22 .. 5/26 (today)
    planted = []
    for delta in range(5):
        snap_date = today - timedelta(days=4 - delta)
        save_snapshot(
            snapshot_date=snap_date,
            container_type="40FT_DRY",
            root=tmp_path,
        )
        planted.append(snap_date)
    assert len(planted) == 5

    # window_days=10 → walks back 10 days, but only 4 pairs are formable
    # (need both D-1 AND D snapshots; today excluded as the target day).
    # Pairs: (5/22, 5/23), (5/23, 5/24), (5/24, 5/25) → 3 records.
    # The (5/25, 5/26) pair would require us to include today, but
    # today is excluded by contract.
    records = build_history_from_snapshots(
        container_type="40FT_DRY",
        root=tmp_path,
        today=today,
        window_days=10,
    )
    assert isinstance(records, list)
    # Each record corresponds to a target day D where both D and D-1
    # snapshots exist AND D < today. That's 5/23, 5/24, 5/25 → 3 records.
    # (The 5/22 snapshot has no D-1 prior; the 5/26 snapshot IS today.)
    assert len(records) == 3
    iso_dates = [r.date_iso for r in records]
    assert iso_dates == ["2026-05-23", "2026-05-24", "2026-05-25"]


def test_build_history_chronological_order(tmp_path, monkeypatch) -> None:
    """Returned records are ordered oldest-first (so the trailing-window
    slice [-window:] semantics work correctly)."""
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)

    from processing.port_supply_history import save_snapshot

    today = date(2026, 5, 26)
    for delta in range(7):
        snap_date = today - timedelta(days=6 - delta)
        save_snapshot(
            snapshot_date=snap_date,
            container_type="40FT_DRY",
            root=tmp_path,
        )

    records = build_history_from_snapshots(
        container_type="40FT_DRY",
        root=tmp_path,
        today=today,
        window_days=10,
    )
    iso_dates = [r.date_iso for r in records]
    assert iso_dates == sorted(iso_dates)


def test_build_history_skips_missing_snapshot_days(
    tmp_path, monkeypatch,
) -> None:
    """If a day's snapshot is missing (weekend gap, scheduler outage),
    the walker silently skips that day rather than raising."""
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)

    from processing.port_supply_history import save_snapshot

    today = date(2026, 5, 26)
    # Plant 5/22, 5/23, [missing 5/24], 5/25 — gap in the middle.
    for d in (date(2026, 5, 22), date(2026, 5, 23), date(2026, 5, 25)):
        save_snapshot(
            snapshot_date=d,
            container_type="40FT_DRY",
            root=tmp_path,
        )

    records = build_history_from_snapshots(
        container_type="40FT_DRY",
        root=tmp_path,
        today=today,
        window_days=10,
    )
    iso_dates = [r.date_iso for r in records]
    # Only (5/22, 5/23) and (5/24-missing) → 5/23 only.
    # 5/24 target needs 5/23 prior — but 5/24 itself is missing, skip.
    # 5/25 target needs 5/24 prior — 5/24 missing, skip.
    assert iso_dates == ["2026-05-23"]


def test_build_history_empty_when_no_snapshots_planted(
    tmp_path, monkeypatch,
) -> None:
    """Empty snapshot root → empty history list, no crash."""
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)

    records = build_history_from_snapshots(
        container_type="40FT_DRY",
        root=tmp_path,
        today=date(2026, 5, 26),
        window_days=30,
    )
    assert records == []


def test_build_history_records_have_populated_date_iso(
    tmp_path, monkeypatch,
) -> None:
    """Each returned record has its date_iso field set to the *target*
    day (not the prior day) so downstream can pin "this magnitude
    belongs to date X"."""
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)

    from processing.port_supply_history import save_snapshot

    today = date(2026, 5, 26)
    for delta in range(3):
        snap_date = today - timedelta(days=2 - delta)
        save_snapshot(
            snapshot_date=snap_date,
            container_type="40FT_DRY",
            root=tmp_path,
        )

    records = build_history_from_snapshots(
        container_type="40FT_DRY",
        root=tmp_path,
        today=today,
        window_days=5,
    )
    for r in records:
        assert r.date_iso
        # Each must be a valid ISO date string.
        date.fromisoformat(r.date_iso)


# ---------------------------------------------------------------------------
# 6. Explanation field shape
# ---------------------------------------------------------------------------


def test_explanation_includes_today_median_and_mad() -> None:
    """The narration string drops into a digest HTML verbatim — must
    name today's value, the trailing median, and the MAD so an operator
    can read the score without inspecting the dataclass."""
    history = _flat_history(20, value=5.0)
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=5, ticker_shuffles=0,
        composite_magnitude=5.0,
    )
    out = score_anomaly(today, history)
    # Composite values appear in the string.
    assert "5.0" in out.explanation or "5.00" in out.explanation
    # Band is named.
    assert out.anomaly_band in out.explanation


def test_explanation_handles_flat_history_case() -> None:
    """When MAD=0, the explanation says the window is flat rather than
    printing a nonsense MADs-from-median count."""
    history = _flat_history(10, value=7.0)
    today = DiffMagnitudeRecord(
        date_iso="2026-05-26",
        severity_shifts=0, entered_deficit=0, exited_deficit=0,
        deficit_moves=7, ticker_shuffles=0,
        composite_magnitude=7.0,
    )
    out = score_anomaly(today, history)
    assert "flat" in out.explanation.lower() or "mad=0" in out.explanation.lower()
