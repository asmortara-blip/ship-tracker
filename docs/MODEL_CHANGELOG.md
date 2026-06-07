# Model Changelog (SR 11-7 change management)

Every change to a model's **live scoring constants** must appear here. The
governance gate `tests/test_model_versioning.py` fails CI if a model's live
`model_config_hash()` does not match the changelog row for its current
`MODEL_VERSION`. To change a weight set legitimately you must, in the same
commit:

1. edit the constant in the model module,
2. bump that module's `MODEL_VERSION` (semver),
3. add a new row below with the **new** `model_config_hash()`.

This makes "which weight set was live on date X" answerable from this file
alone, and makes silent weight edits impossible.

## Models tracked

| Model | Module | Versioned constant | Hash covers (`_MODEL_CONFIG_KEYS`) |
|-------|--------|--------------------|------------------------------------|
| SSI (Shipping Stress Index) | `processing/shipping_stress_index.py` | `MODEL_VERSION` | `COMPONENT_WEIGHTS`, `_PROMINENT_ROUTES`, `_DEFAULT_ROUTE_WEIGHT`, `_SSI_BANDS` |
| Disruption cascade scorer | `processing/disruption_cascade.py` | `MODEL_VERSION` | `_CONVICTION_WEIGHT_SETS`, `_DIRECTION_RULES`, `_DRIVER_WEIGHT_SET`, `_DRIVER_PERSISTENCE`, `_DEFAULT_PERSISTENCE`, `_DRY_BULK_DRIVER_DAMPEN`, `_FUEL_OVERLAY_THRESHOLD`, `_FUEL_COST_RULE`, `_CASCADE_FULL_SCALE`, `_AGREEMENT_FULL_COUNT`, `_CONVICTION_BANDS` |

The config hash is a truncated SHA-256 (16 hex chars) over the canonically
serialised constants (sorted keys, floats rounded to 6 dp), so it is stable
across runs and invariant to dict-insertion order.

## Change log

| Date | Model | Version | Config-hash | Summary |
|------|-------|---------|-------------|---------|
| 2026-06-07 | SSI | 1.0.0 | `dfd4b4e37d257ea5` | Baseline. Records the live SSI scoring constants at the introduction of model versioning (R120). No value change — pure instrumentation. |
| 2026-06-07 | cascade | 1.0.0 | `e150136c30a57de9` | Baseline. Records the live cascade scoring constants (conviction weight sets, direction rules, persistence, fuel overlay, bands) at the introduction of model versioning (R120). No value change — pure instrumentation. |
