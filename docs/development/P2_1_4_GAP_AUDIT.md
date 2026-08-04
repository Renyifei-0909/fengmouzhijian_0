# P2-1.4 只读差距审计 — HumanReview / Remediation ↔ WorkOrder

**Date**: 2026-08-01  
**Mode**: **read-only**（本文件不实现；不新建表）  
**Depends on**: P2-1.3 通过

---

## 1. 产品要求（审计事件）

| action | 产品期望 |
|--------|----------|
| `human_review_completed` | 人审封口完成，可驱动 WO → approved / deviation / remediating / closed |
| `remediation_started` | 整改开始，WO → remediating |
| `remediation_evidence_submitted` | 整改复验证据提交 |
| `remediation_closed` | 整改闭环 |

状态期望（摘要）：

```
needs_review | deviation → (human review) → approved | remediating | closed
remediating → evidence_uploaded (复验) → … → closed
```

---

## 2. 现有模型（无需新表即可评估）

| 实体 | 能力 | 与 WorkOrder 关系 |
|------|------|-------------------|
| `HumanReview` | job 级 decision / reviewer / note | **仅** `job_id`；**无** `work_order_id` |
| `FindingCase` | 案例状态机 + triage/remediation | 经 `source_job_id` → job → evidence → `EvidenceCapture.work_order_id`（间接） |
| `FindingCaseCommand` | 命令审计（含 `remediation_started` 命令名） | 绑定 case，**非** `AuditEvent` 标准 action |
| `RemediationAttempt` | 整改提交 + re-verification job | 绑定 case，无直接 WO FK |
| `WorkOrder` 状态机 | 已有 remediating / needs_review / approved / deviation | 分析完成已写入；**人审/整改未驱动 WO** |
| `AuditEvent` | 通用审计 | 可复用写 `human_review_*` / `remediation_*` |

**迁移判定（只读）**：  
优先路径是 **服务层桥接**（job/capture → work_order_id），复用 `transition_work_order` + `AuditEvent`。  
仅当无法在不破坏 FindingCase 独立性的前提下关联 WO 时，才讨论加可空 `work_order_id` 列——**当前不构成充分理由**。

---

## 3. 差距矩阵

| # | 差距 | 严重度 | 建议最小修复（无新表） |
|---|------|--------|------------------------|
| H1 | 人审 API 只写 `HumanReview` + job，**不** `transition_work_order` | P0 | 人审命令：若 job 有 capture.work_order_id，则转移 WO 并发 `human_review_completed` |
| H2 | 无 `human_review_completed` 标准 AuditEvent | P0 | 与 H1 同事务 |
| H3 | FindingCase 整改命令名 `remediation_started` 存在于 CaseCommand，**未**映射 WO `remediating` | P0 | `start_remediation` 成功后桥接 WO |
| H4 | 整改证据提交未发 `remediation_evidence_submitted` 到 AuditEvent/WO | P1 | 提交 attempt 时写审计；可选 `remediating→evidence_uploaded` |
| H5 | 整改关闭未发 `remediation_closed` / WO `closed` | P1 | close 路径桥接 |
| H6 | FindingCase 与 WO 一对多（一单多 finding） | P1 | 定义聚合规则：任一 open→WO remediating；全部 closed→可 closed/approved |
| H7 | `HumanReview` 无 WO FK | P2 | 暂不迁移；查询经 job→capture |
| H8 | demo scope finding 不应污染运营 WO | P0 | 桥接时跳过 `scope=demo` 或仅 demo 标签 |

---

## 4. 最小验收契约草稿（实现前冻结）

1. 不新增表；尽量不新增列。  
2. 所有 WO 转移经 `transition_work_order` / 严格助手；禁止客户端写 status。  
3. 标准 AuditEvent action 精确字符串（上表）。  
4. 一 WO 多 Case：文档化聚合策略后再编码。  
5. AI 仍不写 verdict/人审决策。  
6. 定向测试 + 现有 remediation 套件；node-id 基线不增新失败。

---

## 5. 建议检查点切分

| ID | 内容 |
|----|------|
| **P2-1.4.0** | 本差距审计 + 聚合策略 ADR 短文（只读/文档） |
| P2-1.4.1 | 人审 → WO + `human_review_completed` |
| P2-1.4.2 | start_remediation → WO remediating + 标准审计 |
| P2-1.4.3 | remediation evidence / closed 对齐 |
| P2-1.4.4 | 报告/追溯链显式 WO 引用（可选） |

---

## 6. 停止条件（实现阶段）

- 需要破坏性迁移  
- 聚合策略改变冻结主线  
- 现有整改模型**证明**无法承载且需新表时：先报告，不自动建表  

---

## 7. 本轮结论

**只读审计完成。** 现有 `FindingCase` / `HumanReview` / `RemediationAttempt` / `AuditEvent` **足以**做桥接实现，**不建议**为 P2-1.4 新建平行领域表。  
**下一实现检查点**：P2-1.4.1（人审→WO），须先书面确认一单多 finding 聚合规则。
