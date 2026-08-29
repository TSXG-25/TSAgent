# v2.4A-2c Contract Calibration

> Calibration only: no Provider call, Planner modification, Runtime modification, or old-baseline overwrite.

- Status: **CALIBRATED_BASELINE**
- HEAD: `6c1743078d67cf1644c22779f40649fb4b9880d2`

## Chat ownership

- Decision: **CHAT_OUTSIDE_PLANNER**
- Planner-owned cases: **46**
- Routing-owned cases: **4**
- Routing oracle: **PASS**

## Versioned evidence

| Dataset | Version | Cases | Hash | Check |
| --- | --- | ---: | --- | --- |
| Planner v1 (immutable) | v2.4A-planner-v1 | 50 | `7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291` | PASS |
| Planner v1.1 calibrated view | v2.4A-planner-v1.1 | 50 | `8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023` | PASS |
| Routing v1 | v2.4A-routing-v1 | 4 | `f3aea7b4cecdcd1997a7716f9c3e7b2396efa2ff6fb3e6b1721784135d345458` | PASS |
| Uncertainty v1 | v2.4A-uncertainty-v1 | 27 | `8f1479bdded0f00e20fd4d283082869d078b4fa217719dc768ae8a6afaaf1cdb` | VALID |

## Uncertainty policy baseline

- Status: **NEEDS_POLICY_WORK**
- Exact decisions: **20/27**
- Abstain precision / recall: **85.7% / 50.0%**
- False / missed abstention rate: **6.7% / 50.0%**

This is a measured baseline for the current deterministic policy. A mismatch remains `P-UNCERTAINTY`; no policy fix is included in this calibration.

## Watchlist

- PA013 and PA016 remain Planner Capability Watchlist items.
- v1 baseline remains bound to its original hash; v1.1 is not used to rewrite the old Provider score.
- Next step: v2.4A-2d real-provider re-baseline with the calibrated contract.
