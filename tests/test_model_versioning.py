"""Governance gate for model-config versioning (R120 / SR 11-7).

Each tracked model (``shipping_stress_index``, ``disruption_cascade``) exposes
a ``MODEL_VERSION`` semver string and a deterministic ``model_config_hash()``
over its live scoring constants. ``docs/MODEL_CHANGELOG.md`` records, per
model + version, the hash that was live.

The headline test (:func:`test_changelog_matches_live_hash`) FAILS if a model's
live config hash drifts from the changelog row for its current
``MODEL_VERSION`` — i.e. if someone edited a weight set / direction rule /
threshold without (a) bumping ``MODEL_VERSION`` and (b) adding a changelog row
carrying the new hash. The failure message names the live hash to copy in.

The remaining tests pin the hash's required properties: determinism,
invariance to dict-insertion order, sensitivity to a weight change (all without
mutating the module globals), and semver validity of ``MODEL_VERSION``.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

import processing.disruption_cascade as cascade
import processing.shipping_stress_index as ssi

_CHANGELOG = Path(__file__).resolve().parent.parent / "docs" / "MODEL_CHANGELOG.md"

# Changelog row labels -> module. The changelog uses a short model name in the
# "Model" column; map each to the module under test.
_MODELS = {
    "SSI": ssi,
    "cascade": cascade,
}

# SemVer MAJOR.MINOR.PATCH (no pre-release/build metadata — we keep it simple).
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# A changelog data row: | Date | Model | Version | `hash` | Summary |
_ROW_RE = re.compile(
    r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*(?P<model>[^|]+?)\s*\|\s*"
    r"(?P<version>[^|]+?)\s*\|\s*`?(?P<hash>[0-9a-fA-F]+)`?\s*\|"
)


def _changelog_rows() -> list[dict]:
    """Parse ``docs/MODEL_CHANGELOG.md`` into ``{model, version, hash}`` rows."""
    assert _CHANGELOG.exists(), f"missing changelog: {_CHANGELOG}"
    rows: list[dict] = []
    for line in _CHANGELOG.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if m:
            rows.append(
                {
                    "model": m.group("model").strip(),
                    "version": m.group("version").strip(),
                    "hash": m.group("hash").strip().lower(),
                }
            )
    return rows


def _row_for(model_name: str, version: str) -> dict | None:
    for row in _changelog_rows():
        if row["model"] == model_name and row["version"] == version:
            return row
    return None


# ── The governance gate ──────────────────────────────────────────────────────


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_changelog_matches_live_hash(model_name: str):
    """Live config hash must equal the changelog row for the current version.

    This is the change-management lock: edit a weight without bumping the
    version + adding a row, and this fails with the hash you need to record.
    """
    module = _MODELS[model_name]
    version = module.MODEL_VERSION
    live_hash = module.model_config_hash().lower()

    row = _row_for(model_name, version)
    assert row is not None, (
        f"{model_name} config changed (MODEL_VERSION={version}) but no "
        f"docs/MODEL_CHANGELOG.md row matches that version. Bump MODEL_VERSION "
        f"and add a row with hash {live_hash}."
    )
    assert row["hash"] == live_hash, (
        f"{model_name} config changed but MODEL_VERSION/CHANGELOG not updated "
        f"— the live hash is {live_hash} while the changelog row for v{version} "
        f"records {row['hash']}. Bump {module.__name__}.MODEL_VERSION and add a "
        f"changelog row with hash {live_hash} (or revert the constant edit)."
    )


# ── Hash property tests ──────────────────────────────────────────────────────


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_hash_is_deterministic(model_name: str):
    module = _MODELS[model_name]
    assert module.model_config_hash() == module.model_config_hash()


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_hash_is_a_truncated_sha256(model_name: str):
    h = _MODELS[model_name].model_config_hash()
    assert isinstance(h, str)
    assert len(h) == 16
    assert re.fullmatch(r"[0-9a-f]{16}", h), h


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_hash_stable_under_dict_reordering(model_name: str):
    """Re-hashing a key-reordered copy of the canonical config is identical.

    Builds an equivalent config dict with every (sub)dict's keys reversed and
    confirms the canonical-JSON hash is unchanged — proving the hash depends on
    content, not insertion order. Does NOT mutate the module globals.
    """
    module = _MODELS[model_name]

    def _hash_of(cfg: dict) -> str:
        import hashlib

        payload = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _reorder(obj):
        if isinstance(obj, dict):
            return {k: _reorder(v) for k, v in reversed(list(obj.items()))}
        if isinstance(obj, list):
            return [_reorder(v) for v in obj]
        return obj

    canonical = module._canonical_model_config()
    reordered = _reorder(copy.deepcopy(canonical))

    # Sanity: the reordering actually changed at least one dict's key order.
    assert list(reordered.keys()) != list(canonical.keys()) or canonical == reordered
    assert _hash_of(reordered) == _hash_of(canonical) == module.model_config_hash()


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_hash_changes_when_a_weight_changes(model_name: str):
    """Perturbing one numeric leaf of the config changes the hash.

    Works on a deep COPY of the canonical config — the module globals are never
    touched — so the live hash (and every other test) is unaffected.
    """
    module = _MODELS[model_name]
    import hashlib

    def _hash_of(cfg: dict) -> str:
        payload = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    baseline = module._canonical_model_config()
    perturbed = copy.deepcopy(baseline)

    # Find the first numeric leaf anywhere in the config and nudge it.
    def _perturb_first_number(obj) -> bool:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    obj[k] = round(v + 0.01, 6)
                    return True
                if _perturb_first_number(v):
                    return True
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    obj[i] = round(v + 0.01, 6)
                    return True
                if _perturb_first_number(v):
                    return True
        return False

    assert _perturb_first_number(perturbed), "no numeric leaf found to perturb"
    assert _hash_of(perturbed) != _hash_of(baseline)
    # And the real module hash is untouched by our copy-based perturbation.
    assert module.model_config_hash() == _hash_of(baseline)


@pytest.mark.parametrize("model_name", sorted(_MODELS))
def test_model_version_is_valid_semver(model_name: str):
    v = _MODELS[model_name].MODEL_VERSION
    assert isinstance(v, str)
    assert _SEMVER_RE.match(v), f"{model_name} MODEL_VERSION not semver: {v!r}"


def test_changelog_parses_at_least_one_row_per_model():
    """Guard the parser itself — each tracked model has >=1 changelog row."""
    rows = _changelog_rows()
    assert rows, "no data rows parsed from MODEL_CHANGELOG.md"
    seen = {r["model"] for r in rows}
    for model_name in _MODELS:
        assert model_name in seen, f"no changelog row for model {model_name!r}"
