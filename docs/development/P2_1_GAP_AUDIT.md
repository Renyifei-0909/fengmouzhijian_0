# P2-1 差距审计：WorkOrder 冻结快照 / 状态机 / 审计事件

**Checkpoint**: P2-1  
**Date**: 2026-08-01  
**Scope**: 对照产品主线，审计现有领域模型与服务路径。**不重写领域模型**；确需迁移时须先证明现有模型无法满足。

---

## 1. 目标链路（产品主线）

```
Project → DesignPackage → EngineeringObject
  → WorkOrder (frozen design/geometry/rules_snapshot)
  → EvidenceCapture → SpatialCheck
  → AI observations only
  → RuleEvaluation (server)
  → HumanReview → Remediation
  → Report / Proof / Audit
```

状态机（服务端命令控制，禁止前端任意写 status）：

```
draft → assigned → evidence_uploaded → analyzing
  → needs_review | deviation | approved
  → remediating → evidence_uploaded | closed
```

审计事件集合：

- `work_order_created`
- `work_order_assigned`
- `evidence_captured`
- `spatial_check_completed`
- `analysis_observations_received`
- `rule_evaluation_completed`
- `human_review_completed`
- `remediation_started`
- `remediation_evidence_submitted`
- `remediation_closed`

---

## 2. 现有模型能力（无需新表）

| 实体 / 字段 | 位置 | 结论 |
|-------------|------|------|
| `WorkOrder.design_snapshot_json` | models.py | 已有；创建 时从 EO 拷贝 |
| `WorkOrder.geometry_snapshot_json` | models.py | 已有；SpatialCheck 读取此快照 |
| `WorkOrder.rules_snapshot_json` | models.py | 已有；合规引擎读取此快照 |
| `WorkOrder.spatial_tolerance_m` | 列 | 已冻结在工单列；**未**写入 `rules_snapshot`（见差距） |
| `WorkOrder.gps_accuracy_threshold_m` | 列 | 同上 |
| `EvidenceCapture` + spatial 字段 | models.py | 已有 |
| `ComplianceEvaluation` | models.py | 服务端 verdict；适配器不可写 |
| `AuditEvent` | models.py | 通用 entity/action/payload；**可复用，勿新建审计表** |
| `WORK_ORDER_TRANSITIONS` | work_orders.py | 服务端转移表已存在 |
| 公开 `PATCH` status | API | **不存在**（正确） |

**迁移判定**：现有表已覆盖冻结快照、空间检查、合规 verdict、审计事件存储。  
**P2-1 不新增表、不新增迁移。**

---

## 3. 差距矩阵

| # | 要求 | 现状 | 严重度 | 拟议最小修复（无新表） |
|---|------|------|--------|------------------------|
| G1 | `rules_snapshot` 含 spatial tolerance + GPS accuracy threshold | 阈值在 WO 列；`rules_snapshot` 仅 expected 规则 | P1 | 创建 时写入 `rules_snapshot.spatial_tolerance_m` / `gps_accuracy_threshold_m`（列保留） |
| G2 | EO 后续变更不改写历史判定依据 | 快照已拷贝；**缺回归测试**；无 DB 层“快照只读”强制 | P0 | 测试：改 EO 几何/规则后 SpatialCheck/规则仍用 WO 快照 |
| G3 | SpatialCheck / 规则引擎只读冻结快照 | **已实现**（`geometry_snapshot_json` / `rules_snapshot_json`） | — | 锁测试防回归 |
| G4 | 状态仅服务端命令 | 无 PATCH status；转移多挂在 upload/analysis 副作用；**无独立 assign 命令**；create 可直接 `assigned` | P1 | 后续：`POST .../assign`；create 默认 draft；本检查点先规范化审计 |
| G5 | 转移图与产品一致 | 近似；`draft→closed`、`compliant→needs_review`（人审封口）属有意设计 | P2 | 文档化；禁止 silent `except: pass` 吞转移失败（后续） |
| G6 | 审计事件标准名 | 现为 `created` / `uploaded_for_work_order` / `compliance_evaluated` 等 | P0 | 常量 + 在现有挂钩点写标准 action |
| G7 | AI 只输出 observations | **已实现**（compliance 服务端） | — | 保持 |
| G8 | human_review / remediation 工单级事件 | FindingCase 有人审/整改；**未**与 WO 状态机审计名对齐 | P1 | 后续检查点接线，不在本轮重写 |
| G9 | Report/Proof 关联完整链 | 部分存在；报告未必引用 WO 快照 ID | P2 | 后续 |
| G10 | 前端不得写 status | 前端无 WO status 写 API 调用 | — | 保持；契约测试可选 |

---

## 4. 不在本轮做的事

- 不为“架构漂亮”新增 `spatial_checks` / `rule_evaluations` 分表（`EvidenceCapture` + `ComplianceEvaluation` 已承载）。
- 不重写 `FindingCase` / 整改流水线。
- 不把 `compliant` 直接映射为 `approved`（人审封口保留）。
- 不做破坏性数据迁移。

---

## 5. 最小验收契约

见同目录 `P2_1_ACCEPTANCE_CONTRACT.md`。

---

## 6. 检查点切分（建议）

| ID | 内容 | 迁移 |
|----|------|------|
| **P2-1.1**（本轮） | 差距审计落盘；rules_snapshot 嵌入阈值；标准审计 action（create/assign/evidence/spatial/observations/rules）；冻结不可变回归测试 | 无 |
| P2-1.2 | 独立 `assign` 命令 + `work_order_assigned`；create 始终 draft | 无 |
| P2-1.3 | 转移失败可观测；禁止静默吞异常 | 无 |
| P2-1.4 | human_review / remediation 与 WO 状态/审计名对齐 | 无（优先复用 FindingCase） |
| P2-1.5 | 报告/追溯链显式引用 WO 快照与 ComplianceEvaluation | 视报告模型而定 |

---

## 7. 真实性边界

- 合成 GPS / `synthetic_demo` 不得冒充现场定位。
- AI 不得输出最终合规 verdict。
- 冻结快照是历史依据；当前 EO 仅用于新工单创建。
