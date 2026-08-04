# DEVELOPMENT_LOG

## 2026-08-01 — bootstrap autonomous state + P1-1.1

- Established `docs/development/` autonomous state files.
- Completed **P1-1.1**: GeoPackageBinary flags, metadata consistency, empty whitelist policy, path safety.
- Contract version → `gpkg-import-contract-v0.1.1`.
- Targeted: **58 passed, 1 skipped**.

## 2026-08-01 — P1-2A geometry stack feasibility

- Optional extra `gpkg`: pyogrio==0.13.0, shapely==2.1.2, pyproj==3.7.2 (uv.lock updated).
- `gpkg_geometry_stack.py` probe + bounded sample read; no DB/API; no handwritten fallback.
- GDAL 3.12.4, GPKG driver ok; EPSG:25832→4326 always_xy ok.
- Dependency lock counts updated (52 packages).

## 2026-08-01 — P1-2B normalize + ADR-002

- `gpkg_normalize.py`: preflight → whitelist attrs → WKB/Shapely → CRS → WGS84 GeoJSON candidates.
- ADR-002: stable object_code; idempotency project+sha256+contract.
- Targeted: **81 passed, 1 skipped**.

## 2026-08-01 — P1-3A transactional standard GPKG import

- Migration `20260801_0004`: `standard_gpkg`, `import_contract_version`, unique idempotency key.
- Service `import_standard_gpkg` (library path only).
- Targeted: **88 passed, 1 skipped**.

## 2026-08-01 — P1-3B audit + P1-4 preview/confirm API

- Audit + staging preview/confirm API.
- Targeted earlier: **131 passed, 1 skipped**.

## 2026-08-01 — P1-4.1 integrity hardening

- TOCTOU: private confirm snapshot + `expected_source_sha256` after normalize, before DB writes.
- Staging TTL purge, per-actor quotas, exclusive confirm lock.
- Independent `FENGMOU_GPKG_PREVIEW_SIGNING_SECRET` (not operator key).
- Server forces synthetic=true / purpose=controlled / sample_or_unverified.
- Stable API `error_code`; migration downgrade refuses when standard_gpkg rows exist.
- Directed: **120 passed, 1 skipped**. Full: **425p / 153f / 32s** (failed count unchanged).

## 2026-08-01 — P1-4B commercial frontend GPKG import

- `api.previewStandardGpkg` / `confirmStandardGpkg`; GpkgImportPanel state machine on 工程作业.
- JSON moved to 兼容格式; internal routes feature-flagged.
- Frontend: typecheck + **26** tests + build pass.
- Next: P2 domain loop / optional isolated browser smoke.

## 2026-08-01 — P1-4C 发布门通过

- TEST_BASELINE ull_suite_windows_p14c：153 failed nodeids，sha 2994b32b… 程序化采集精确匹配。
- 定向：baseline/staging/migrations/gpkg_import 全绿。
- 前端：38 tests + typecheck + build。
- 隔离 E2E：scripts/p14c_isolated_e2e.py 8002/5175，预检不写库、确认后 synthetic standard_gpkg、移动端无横向溢出、内部路由关闭；证据 ackend/test-artifacts/p14c_e2e_evidence.json。
- last_completed = **P1-4C**。

## 2026-08-01 — P2-1 差距审计 + P2-1.1 冻结/审计小检查点

- 文档：P2_1_GAP_AUDIT.md、P2_1_ACCEPTANCE_CONTRACT.md。
- **无新表/无迁移**；现有 WorkOrder 快照列 + AuditEvent + ComplianceEvaluation 可满足。
- 
ules_snapshot 写入 spatial_tolerance_m / gps_accuracy_threshold_m。
- 标准审计 action；SpatialCheck/合规读 rozen_* 助手。
- 测试：	est_work_order_compliance 9 passed（含 freeze / audit / no status write）。
- 下一：P2-1.2 独立 assign 命令。

## 2026-08-01 — P2-1.2 assign 命令

- `create_work_order` 始终 `draft`；`POST /work-orders/{id}/assign` → `assigned` + `work_order_assigned`。
- 证据上传：draft 返回 409，要求先 assign。
- 测试：work_order_compliance 10 passed；openapi 重生。
- seed_alpha18_commercial 跟进 assign。
- 无新表/迁移。

## 2026-08-01 — P2-1.3 分析完成转移可观测

- 删除 analysis 完成链路静默 `except Exception: pass` 与 needs_review fallback。
- `apply_analysis_completion_transitions`：evidence_uploaded→analyzing→target。
- 失败码：`WORK_ORDER_TRANSITION_FAILED` / `WORK_ORDER_MISSING`；审计 `work_order_transition_failed`。
- 测试：`test_work_order_analysis_transitions` + compliance 25p；leases/recovery 31p；workflow 等 8p/26s。
- 无新迁移。

## 2026-08-01 — P2-1.4 read-only gap audit

- Doc: P2_1_4_GAP_AUDIT.md
- Conclusion: no new tables; bridge via job->EvidenceCapture->WorkOrder + AuditEvent.
- Next impl: P2-1.4.1 after multi-FindingCase aggregation rule is fixed.

