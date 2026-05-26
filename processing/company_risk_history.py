"""processing/company_risk_history.py — daily per-ticker risk-score persistence.

Persists ``cache/company_risk_history/<YYYY-MM-DD>/company_risk.jsonl``
— one record per ticker per day. Mirrors the
``processing.cargo_mix_history`` layout so the bulk-export job picks it
up alongside the others.

Operators can then:
  * Ask "how has ZIM's port-side supply risk evolved this month?"
  * Surface tickers whose risk_band JUST flipped (Moderate → Elevated)
  * Trend the fleet-wide mean risk so a market-wide shock is visible

Each line is JSON: ``{"ticker": str, "total_risk_score": float,
"risk_band": str, "port_count": int, "weighted_deficit_days": float,
"critical_port_count": int, "top_problem_ports":
[[locode, share, deficit], ...]}``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


__all__ = [
    "COMPANY_RISK_ROOT",
    "CompanyRiskHistoryJobResult",
    "company_risk_dir_for",
    "save_company_risk_snapshot",
    "load_company_risk_for_ticker",
    "list_company_risk_dates",
    "run_daily_company_risk_snapshot_job",
]


# Default persistence root — under cache/ alongside the other daily trees.
COMPANY_RISK_ROOT: Path = (
    Path(__file__).parent.parent / "cache" / "company_risk_history"
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CompanyRiskHistoryJobResult:
    """Outcome of one daily snapshot. Never raises."""

    ok: bool = False
    today: str = ""
    n_tickers_saved: int = 0
    bytes_written: int = 0
    snapshot_path: str = ""
    # Tickers whose risk_band differs from their most-recent prior
    # band. Empty on first-ever run (no prior to compare against).
    band_transitions: list[dict] = field(default_factory=list)
    error_msg: str = ""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def company_risk_dir_for(
    snapshot_date: date,
    *,
    root: Path | None = None,
) -> Path:
    """Return the directory path for a given snapshot date."""
    base = Path(root) if root is not None else COMPANY_RISK_ROOT
    return base / snapshot_date.isoformat()


_FILENAME = "company_risk.jsonl"


# ---------------------------------------------------------------------------
# Save / load — JSONL one record per ticker
# ---------------------------------------------------------------------------


def _score_to_blob(score) -> dict:
    """Serialize a CompanySupplyRiskScore into the JSONL row shape."""
    return {
        "ticker":               getattr(score, "ticker", ""),
        "total_risk_score":     float(getattr(score, "total_risk_score", 0.0)),
        "risk_band":            str(getattr(score, "risk_band", "")),
        "port_count":           int(getattr(score, "port_count", 0)),
        "weighted_deficit_days": float(
            getattr(score, "weighted_deficit_days", 0.0)
        ),
        "critical_port_count":  int(getattr(score, "critical_port_count", 0)),
        "top_problem_ports":    [
            list(triple) for triple in
            getattr(score, "top_problem_ports", []) or []
        ],
    }


def save_company_risk_snapshot(
    *,
    snapshot_date: date | None = None,
    root: Path | None = None,
    container_type: str = "40FT_DRY",
    scores: list | None = None,
) -> tuple[Path, int]:
    """Build today's per-ticker risk scores + write the JSONL file.

    ``scores`` defaults to ``compute_company_supply_risk()``; tests
    inject a stub list. Returns ``(path_written, bytes_written)``.
    """
    if scores is None:
        from processing.company_supply_risk import compute_company_supply_risk
        scores = compute_company_supply_risk(container_type=container_type)

    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    out_dir = company_risk_dir_for(snapshot_date, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _FILENAME

    lines: list[str] = []
    for score in scores:
        lines.append(json.dumps(_score_to_blob(score), sort_keys=True))
    body = "\n".join(lines) + ("\n" if lines else "")
    target.write_text(body, encoding="utf-8")
    return target, len(body.encode("utf-8"))


def load_company_risk_for_ticker(
    ticker: str,
    *,
    window_days: int = 30,
    today: date | None = None,
    root: Path | None = None,
) -> list[dict]:
    """Return the trailing N days of risk-score records for ``ticker``.

    Oldest-first. Missing dates / missing tickers are skipped silently
    so the caller gets a clean list even when history is incomplete.
    Each returned dict carries ``date_iso`` (parsed from the dir name)
    in addition to the saved fields.
    """
    today = today or datetime.now(timezone.utc).date()
    window = max(1, int(window_days))
    base = Path(root) if root is not None else COMPANY_RISK_ROOT
    out: list[dict] = []
    for delta in range(window, 0, -1):
        d = today - timedelta(days=delta)
        path = company_risk_dir_for(d, root=base) / _FILENAME
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(blob.get("ticker", "")) != ticker:
                    continue
                blob["date_iso"] = d.isoformat()
                out.append(blob)
        except Exception:
            continue
    return out


def list_company_risk_dates(
    *,
    root: Path | None = None,
) -> list[date]:
    """Every ISO date present under ``root`` with a company_risk.jsonl file."""
    base = Path(root) if root is not None else COMPANY_RISK_ROOT
    if not base.exists():
        return []
    dates: list[date] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if not (child / _FILENAME).exists():
            continue
        try:
            dates.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    dates.sort()
    return dates


# ---------------------------------------------------------------------------
# Band-transition detection — fires the "ZIM JUST flipped to Elevated"
# operator-facing signal
# ---------------------------------------------------------------------------


def _find_prior_snapshot_date(
    today: date,
    *,
    max_lookback_days: int = 14,
    root: Path | None = None,
) -> Optional[date]:
    """Most-recent snapshot date BEFORE ``today``, up to N days back."""
    base = Path(root) if root is not None else COMPANY_RISK_ROOT
    for delta in range(1, max(1, int(max_lookback_days)) + 1):
        candidate = today - timedelta(days=delta)
        if (company_risk_dir_for(candidate, root=base) / _FILENAME).exists():
            return candidate
    return None


def _load_band_map(path: Path) -> dict[str, str]:
    """Parse a saved snapshot into a ``{ticker: risk_band}`` dict."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            continue
        ticker = str(blob.get("ticker", "") or "")
        if not ticker:
            continue
        out[ticker] = str(blob.get("risk_band", "") or "")
    return out


def detect_band_transitions(
    *,
    today: date,
    root: Path | None = None,
    max_lookback_days: int = 14,
) -> list[dict]:
    """Compare today's bands to the most-recent prior snapshot.

    Returns a list of ``{ticker, prior_band, current_band}`` for every
    ticker whose band changed. Empty when no prior snapshot exists or
    when no bands shifted.
    """
    base = Path(root) if root is not None else COMPANY_RISK_ROOT
    prior_date = _find_prior_snapshot_date(
        today, max_lookback_days=max_lookback_days, root=base,
    )
    if prior_date is None:
        return []
    prior_path = company_risk_dir_for(prior_date, root=base) / _FILENAME
    today_path = company_risk_dir_for(today, root=base) / _FILENAME

    prior_bands = _load_band_map(prior_path)
    today_bands = _load_band_map(today_path)

    transitions: list[dict] = []
    for ticker, today_band in today_bands.items():
        prior_band = prior_bands.get(ticker, "")
        if prior_band and prior_band != today_band:
            transitions.append({
                "ticker":       ticker,
                "prior_band":   prior_band,
                "current_band": today_band,
                "prior_date":   prior_date.isoformat(),
            })
    transitions.sort(key=lambda t: t["ticker"])
    return transitions


# ---------------------------------------------------------------------------
# Worker job — save today + detect band transitions
# ---------------------------------------------------------------------------


def run_daily_company_risk_snapshot_job(
    *,
    today: date | None = None,
    root: Path | None = None,
    container_type: str = "40FT_DRY",
    max_lookback_days: int = 14,
) -> CompanyRiskHistoryJobResult:
    """Save today's per-ticker risk scores + detect band transitions.

    Defensive — every step is wrapped so a failure surfaces in
    ``CompanyRiskHistoryJobResult.ok=False`` + ``error_msg`` rather than
    raising out of the worker pool.
    """
    today = today or datetime.now(timezone.utc).date()
    result = CompanyRiskHistoryJobResult(today=today.isoformat())

    # ── Save today's scores ───────────────────────────────────────────
    try:
        path, byte_count = save_company_risk_snapshot(
            snapshot_date=today, root=root, container_type=container_type,
        )
    except Exception as exc:
        result.error_msg = f"save_company_risk_snapshot failed: {type(exc).__name__}: {exc}"
        return result

    result.snapshot_path = str(path)
    result.bytes_written = byte_count

    # Count saved tickers from the file (defensive — input might be a
    # generator we can't re-iterate).
    n_saved = 0
    try:
        n_saved = sum(
            1 for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except Exception:
        pass
    result.n_tickers_saved = n_saved

    # ── Detect band transitions vs prior snapshot ────────────────────
    try:
        result.band_transitions = detect_band_transitions(
            today=today, root=root, max_lookback_days=max_lookback_days,
        )
    except Exception as exc:
        result.error_msg = f"detect_band_transitions failed (snapshot still saved): {exc}"

    result.ok = True
    return result
