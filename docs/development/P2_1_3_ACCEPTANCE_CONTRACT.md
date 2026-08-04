# P2-1.3 最小验收契约 — 分析完成状态转移可观测

**Checkpoint**: P2-1.3  
**No new tables / migrations.**

---

## 1. 正常路径语义

分析完成阶段 WorkOrder 仅允许：

| 入口状态 | 转移序列 | 终态来源 |
|----------|----------|----------|
| `evidence_uploaded` | → `analyzing` → `target` | `map_compliance_to_work_order_status(verdict)` |
| `analyzing` | → `target` | 同上 |

`target` ∈ `{needs_review, deviation, approved}`（当前规则映射：`compliant`/`insufficient_evidence`/`needs_review` → `needs_review`；`deviation_detected` → `deviation`；**不**由引擎直接写 `approved`）。

**非法入口**（不得静默修复）：`draft`、`assigned`、`approved`、`closed`、`remediating` 等。

## 2. 禁止静默吞错

`_complete_verification_job` 中与 WorkOrder 转移相关的：

```text
try: transition... except Exception: pass
try: transition... except: fallback needs_review except: pass
```

必须删除。失败不得隐藏为成功完成。

## 3. 事务原子性

成功路径（同一事务提交）：

- WorkOrder 状态更新到 target
- ComplianceEvaluation 写入
- VerificationJob → needs_review + result
- attempt outcome `committed_success`
- 审计：`analysis_observations_received`、`rule_evaluation_completed`、`analysis_completed`

任一 WorkOrder 转移/完整性失败 → **整事务回滚**，随后失败路径（独立事务）将 job 标 failed。

失败后不得残留：

- ComplianceEvaluation
- committed_success outcome
- job needs_review
- 上述成功类审计

## 4. 结构化异常与错误码

| 条件 | 异常 | error_code | retryable |
|------|------|------------|-----------|
| 非法入口或转移被状态机拒绝 | `WorkOrderTransitionError` | `WORK_ORDER_TRANSITION_FAILED` | false |
| compliance 引用的 WO 不存在 | `WorkOrderIntegrityError` | `WORK_ORDER_MISSING` | false |
| 远程分析器 | `RemoteAnalyzerError` | 既有 code | 既有 |
| 其他分析失败 | — | `ANALYSIS_FAILURE` | false |

异常字段：`work_order_id`、`current_status`、`requested_status`、`stage`、`error_code`。  
消息不含密钥、完整本地路径、照片内容。

## 5. 失败审计

失败落库事务中写入 `work_order_transition_failed`（或缺失时 `work_order_missing` 可并入同一 action + error_code 区分）。

Payload 仅：`job_id`、`current_status`、`requested_status`、`stage`、`error_code`、`worker_id`/`generation`/`attempt_id`（非敏感）。

禁止伪造：`rule_evaluation_completed`、`analysis_observations_received`、`human_review_completed`。

## 6. 无隐式回退

禁止 target 失败后自动 `needs_review` 再忽略。

## 7. 测试门槛

见用户清单 §五；定向套件必须绿；全量仅允许基线 failed node-id。

## 8. 非目标

- 不改 GPKG 清理容错
- 不重写 lease 系统
- 不进入 P2-1.4 实现
- 无前端/浏览器
