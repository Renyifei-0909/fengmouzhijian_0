# P2-1 最小验收契约

**Applies to**: P2-1.x work-order freeze / status / audit  
**No new tables** unless a later checkpoint proves impossibility.

---

## A. Frozen snapshot (P2-1.1 must pass)

1. Creating a WorkOrder copies into durable fields:
   - `design_snapshot_json` (object identity, package id, attributes, design_version)
   - `geometry_snapshot_json` (geometry_type, geometry_wgs84, source CRS)
   - `rules_snapshot_json` including at least:
     - expected rules / rule_version
     - `spatial_tolerance_m`
     - `gps_accuracy_threshold_m`
2. After create, mutating the live `EngineeringObject` geometry or `expected_rules_json` **must not** change:
   - SpatialCheck distance basis (still uses WO `geometry_snapshot_json`)
   - RuleEvaluation expected basis (still uses WO `rules_snapshot_json`)
3. SpatialCheck API path and compliance path **must not** re-load current EO for historical judgment.

## B. Status machine (incremental)

1. No public HTTP verb may set `WorkOrder.status` from an arbitrary client body field.
2. All status changes go through `transition_work_order` (or a thin command wrapper that calls it).
3. Allowed transitions remain `WORK_ORDER_TRANSITIONS` until an explicit product change.
4. Engine `compliant` → WO `needs_review` (human seal) remains correct; not a bug.

## C. Audit events (P2-1.1 partial)

On the paths that already exist, emit **exact** action strings:

| When | action |
|------|--------|
| Work order created | `work_order_created` |
| Assigned at create or via assign command | `work_order_assigned` |
| Evidence uploaded on WO verification path | `evidence_captured` |
| Spatial check result persisted | `spatial_check_completed` |
| Analyzer result validated (observations only) | `analysis_observations_received` |
| Server rule engine finished | `rule_evaluation_completed` |

Deferred (must not fake as done):  
`human_review_completed`, `remediation_started`, `remediation_evidence_submitted`, `remediation_closed`.

## D. AI / truth

1. Analyzer result may carry observations only for compliance input.
2. `ComplianceEvaluation.verdict` is written only by server `evaluate_compliance`.
3. Synthetic location must remain labeled; not field GPS proof.

## E. Tests required for P2-1.1

- Unit/service: freeze immutability (EO mutated after WO create).
- API or service: audit actions include the P2-1.1 set for create + capture + evaluate path.
- Existing `test_work_order_compliance` suite still green.
- No OpenAPI schema require for new tables.
- Full suite: **exact failed node-id set** must not introduce new failures vs baseline.

## F. Explicit non-goals for P2-1.1

- New migrations / tables
- Frontend status editor
- Full remediation wiring
- Claiming E2E browser for P2 (not required unless UI changes)
