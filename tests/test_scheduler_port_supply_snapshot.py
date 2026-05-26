"""Defining-property tests for worker.scheduler.run_port_supply_snapshot_job."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

import worker.scheduler as sched


# ── Per-test isolation: redirect SNAPSHOT_ROOT to tmp_path ────────────────


@pytest.fixture(autouse=True)
def isolate_snapshot_root(monkeypatch, tmp_path):
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)
    yield tmp_path


# ── 1. Wrapper return shape ──────────────────────────────────────────────


def test_returns_required_count_keys(isolate_snapshot_root) -> None:
    out = sched.run_port_supply_snapshot_job()
    assert set(out.keys()) >= {
        "ok", "saved_bytes", "diff_present",
        "severity_shifts", "entered_deficit",
        "exited_deficit", "deficit_moves",
    }


def test_first_run_returns_ok_with_no_diff(isolate_snapshot_root) -> None:
    """First-ever run has nothing prior to compare to — diff_present=False
    and every count is zero, but ok=True (snapshot landed)."""
    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is True
    assert out["saved_bytes"] > 0
    assert out["diff_present"] is False
    assert out["severity_shifts"] == 0
    assert out["entered_deficit"] == 0


# ── 2. Diff surfaces in the count dict when prior snapshot exists ────────


def test_diff_counts_surface_after_second_run(isolate_snapshot_root) -> None:
    """Save day 1, then run again for day 2 — the diff count keys
    must reflect whatever the comparator returned (zero on stable
    synth is also a valid count)."""
    from processing.port_supply_history import save_snapshot

    save_snapshot(snapshot_date=date(2026, 5, 25),
                  root=isolate_snapshot_root)

    # Patch run_daily_snapshot_job to use a fixed "today" so the test
    # is deterministic regardless of when it runs.
    import processing.port_supply_history as psh
    real_job = psh.run_daily_snapshot_job

    def _fixed_today_job(**kwargs):
        kwargs.setdefault("today", date(2026, 5, 26))
        return real_job(**kwargs)

    with patch.object(psh, "run_daily_snapshot_job", _fixed_today_job):
        out = sched.run_port_supply_snapshot_job()

    assert out["ok"] is True
    assert out["diff_present"] is True


# ── 3. Top-level failure surfaces ok=False but doesn't raise ─────────────


def test_wrapper_returns_ok_false_when_underlying_raises(monkeypatch) -> None:
    """If processing.port_supply_history.run_daily_snapshot_job raises,
    the wrapper logs + returns ok=False with zero counts rather than
    bubbling the exception up into the cron tick."""
    def _boom(**_kwargs):
        raise RuntimeError("simulated")

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _boom)

    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is False
    assert out["saved_bytes"] == 0
    assert out["diff_present"] is False


def test_wrapper_propagates_container_type_kwarg(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """container_type kwarg must reach the underlying job verbatim."""
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        # Return a minimal-shape SnapshotJobResult so the wrapper proceeds.
        from processing.port_supply_history import SnapshotJobResult
        return SnapshotJobResult(
            ok=True, today="2026-05-26", container_type=kwargs.get(
                "container_type", ""),
            snapshot_path="/tmp/x.csv", bytes_written=100,
            prior_snapshot_date="", diff=None, error_msg="",
        )

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _capture)

    sched.run_port_supply_snapshot_job(container_type="40FT_REEFER")
    assert captured.get("container_type") == "40FT_REEFER"


def test_wrapper_propagates_min_diff_delta_days_kwarg(
    isolate_snapshot_root, monkeypatch,
) -> None:
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        from processing.port_supply_history import SnapshotJobResult
        return SnapshotJobResult(
            ok=True, today="2026-05-26", container_type="40FT_DRY",
            snapshot_path="/tmp/x.csv", bytes_written=100,
            prior_snapshot_date="", diff=None, error_msg="",
        )

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _capture)

    sched.run_port_supply_snapshot_job(min_diff_delta_days=2.5)
    assert captured.get("min_diff_delta_days") == 2.5


# ── 4. Wiring contract — main() guards the call with try/except ──────────


def test_main_function_calls_snapshot_job_under_try_except() -> None:
    """The main() entry point must wrap run_port_supply_snapshot_job in
    its own try/except so a failure doesn't take down the rest of the
    daily cron. Verified by reading the source — the canonical pattern
    every other run_*_job follows in this file."""
    import inspect
    src = inspect.getsource(sched.main)
    # The call site exists
    assert "run_port_supply_snapshot_job()" in src
    # ...and is wrapped in a try block (logger.warning pairs with the
    # try/except idiom used by every sibling job).
    assert "port supply snapshot step failed" in src


# ── 5. Digest persistence — quiet day vs material day ────────────────────


def test_digest_paths_key_always_in_returned_dict(
    isolate_snapshot_root,
) -> None:
    """``digest_paths`` is part of the documented wrapper return shape
    every invocation regardless of whether anything was written.
    Callers can rely on ``out['digest_paths']`` always being a dict."""
    out = sched.run_port_supply_snapshot_job()
    assert "digest_paths" in out
    assert isinstance(out["digest_paths"], dict)


def test_quiet_day_writes_no_digest_files(isolate_snapshot_root) -> None:
    """First-ever run (no prior to diff) is a quiet day — no digest
    artifacts may land on disk."""
    out = sched.run_port_supply_snapshot_job()
    # First-ever run has no prior snapshot -> diff is None -> quiet day.
    assert out["diff_present"] is False
    assert out["digest_paths"] == {}
    # Belt-and-braces: walk every subdirectory under the isolated root
    # and confirm zero digest_*.{html,txt} files were created.
    digest_files = list(isolate_snapshot_root.rglob("digest_*"))
    assert digest_files == []


def test_material_day_writes_all_three_digest_artifacts(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """When the diff has material content, all three digest artifacts
    (html, txt, subject.txt) must land under the snapshot date dir."""
    import processing.port_supply_history as psh
    from tools.port_supply_diff import DiffReport, PortDelta

    # Build a synthetic SnapshotJobResult with a populated diff so we
    # can deterministically exercise the digest-persistence branch.
    today_iso = "2026-05-26"
    snapshot_dir = isolate_snapshot_root / today_iso
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "port_supply_summary_40ft_dry.csv"
    snapshot_path.write_text("# stub\nlocode\nUSLAX\n", encoding="utf-8")

    diff = DiffReport(n_ports_before=2, n_ports_after=2)
    diff.severity_shifts.append(PortDelta(
        locode="USLAX", name="Los Angeles", region="North America",
        severity_before="WATCH", severity_after="STRESSED",
        severity_shifted=True,
        deficit_before=1.0, deficit_after=-3.0, deficit_delta=-4.0,
        entered_deficit=False, exited_deficit=False,
        tickers_before=[], tickers_after=[],
        tickers_added=[], tickers_removed=[],
    ))

    def _stub_job(**_kwargs):
        return psh.SnapshotJobResult(
            ok=True, today=today_iso, container_type="40FT_DRY",
            snapshot_path=str(snapshot_path), bytes_written=100,
            regional_snapshot_path="", regional_bytes_written=0,
            prior_snapshot_date="2026-05-25", diff=diff, error_msg="",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _stub_job)

    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is True
    paths = out["digest_paths"]
    assert set(paths.keys()) == {"html", "text", "subject"}
    # Every artifact lives under the snapshot's date dir.
    for kind, p in paths.items():
        assert Path(p).exists(), f"{kind} digest missing: {p}"
        assert Path(p).parent == snapshot_dir, (
            f"{kind} digest landed in wrong dir: {p}"
        )
    # File-name convention: digest_<container>.{html,txt,subject.txt}
    assert Path(paths["html"]).name == "digest_40ft_dry.html"
    assert Path(paths["text"]).name == "digest_40ft_dry.txt"
    assert Path(paths["subject"]).name == "digest_40ft_dry.subject.txt"


def test_digest_subject_artifact_contains_summary(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """The persisted .subject.txt artifact must contain the human-
    readable subject line so downstream channels can pick it up
    verbatim without re-rendering."""
    import processing.port_supply_history as psh
    from tools.port_supply_diff import DiffReport, PortDelta

    snapshot_dir = isolate_snapshot_root / "2026-05-26"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "port_supply_summary_40ft_dry.csv"
    snapshot_path.write_text("# stub\nlocode\nUSLAX\n", encoding="utf-8")

    diff = DiffReport(n_ports_before=1, n_ports_after=1)
    diff.severity_shifts.append(PortDelta(
        locode="USLAX", name="Los Angeles", region="North America",
        severity_before="WATCH", severity_after="STRESSED",
        severity_shifted=True,
        deficit_before=1.0, deficit_after=-3.0, deficit_delta=-4.0,
        entered_deficit=False, exited_deficit=False,
        tickers_before=[], tickers_after=[],
        tickers_added=[], tickers_removed=[],
    ))

    def _stub_job(**_kwargs):
        return psh.SnapshotJobResult(
            ok=True, today="2026-05-26", container_type="40FT_DRY",
            snapshot_path=str(snapshot_path), bytes_written=100,
            regional_snapshot_path="", regional_bytes_written=0,
            prior_snapshot_date="2026-05-25", diff=diff, error_msg="",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _stub_job)

    out = sched.run_port_supply_snapshot_job()
    subj_path = Path(out["digest_paths"]["subject"])
    subj = subj_path.read_text(encoding="utf-8")
    assert "Port-Supply" in subj
    assert "1 severity shift" in subj
    assert "(2026-05-26)" in subj


def test_digest_generation_failure_does_not_kill_snapshot(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """If digest rendering blows up, the snapshot itself must still be
    reported as ok=True with its diff counts intact. The digest is
    purely downstream — render failure is a delivery-side concern,
    not a snapshot failure."""
    import processing.port_supply_history as psh
    from tools.port_supply_diff import DiffReport, PortDelta

    snapshot_dir = isolate_snapshot_root / "2026-05-26"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "port_supply_summary_40ft_dry.csv"
    snapshot_path.write_text("# stub\nlocode\nUSLAX\n", encoding="utf-8")

    diff = DiffReport(n_ports_before=1, n_ports_after=1)
    diff.severity_shifts.append(PortDelta(
        locode="USLAX", name="Los Angeles", region="North America",
        severity_before="WATCH", severity_after="STRESSED",
        severity_shifted=True,
        deficit_before=1.0, deficit_after=-3.0, deficit_delta=-4.0,
        entered_deficit=False, exited_deficit=False,
        tickers_before=[], tickers_after=[],
        tickers_added=[], tickers_removed=[],
    ))

    def _stub_job(**_kwargs):
        return psh.SnapshotJobResult(
            ok=True, today="2026-05-26", container_type="40FT_DRY",
            snapshot_path=str(snapshot_path), bytes_written=100,
            regional_snapshot_path="", regional_bytes_written=0,
            prior_snapshot_date="2026-05-25", diff=diff, error_msg="",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _stub_job)

    # Sabotage the digest renderer so any call raises.
    import delivery.port_supply_shock_digest as digest_mod

    def _broken_render(*_a, **_kw):
        raise RuntimeError("simulated digest failure")

    monkeypatch.setattr(digest_mod, "render_html", _broken_render)

    out = sched.run_port_supply_snapshot_job()
    # Snapshot still succeeded; diff counts still surface.
    assert out["ok"] is True
    assert out["diff_present"] is True
    assert out["severity_shifts"] == 1
    # No digest paths landed because render failed.
    assert out["digest_paths"] == {}
    # No partial files on disk either.
    digest_files = list(snapshot_dir.glob("digest_*"))
    assert digest_files == []


def test_quiet_day_with_empty_diff_writes_no_digest(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """A diff that exists but has all empty buckets (e.g. yesterday and
    today look identical) is a quiet day — no digest gets persisted
    even though ``diff is not None``."""
    import processing.port_supply_history as psh
    from tools.port_supply_diff import DiffReport

    snapshot_dir = isolate_snapshot_root / "2026-05-26"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "port_supply_summary_40ft_dry.csv"
    snapshot_path.write_text("# stub\n", encoding="utf-8")

    # Empty diff — every bucket is an empty list by dataclass default.
    empty_diff = DiffReport(n_ports_before=1, n_ports_after=1)

    def _stub_job(**_kwargs):
        return psh.SnapshotJobResult(
            ok=True, today="2026-05-26", container_type="40FT_DRY",
            snapshot_path=str(snapshot_path), bytes_written=100,
            regional_snapshot_path="", regional_bytes_written=0,
            prior_snapshot_date="2026-05-25", diff=empty_diff, error_msg="",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _stub_job)

    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is True
    assert out["diff_present"] is True   # diff IS present...
    assert out["digest_paths"] == {}     # ...but every bucket is empty
    assert list(snapshot_dir.glob("digest_*")) == []