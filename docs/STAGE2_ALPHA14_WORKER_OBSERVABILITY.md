# 第二阶段 Alpha14：Worker 调度聚合可观测性

日期：2026-07-28  
状态：`engineering candidate / SQLite verified / PostgreSQL unverified`

> 历史说明：本文保留 Alpha14 当轮的测试计数、OpenAPI 摘要和“尚无指标导出器”边界。
> 当前 Alpha15 已增加低基数 Prometheus text 导出；最新实现、数字与仍未部署外部监控的边界见
> [`STAGE2_ALPHA15_PROMETHEUS_METRICS.md`](./STAGE2_ALPHA15_PROMETHEUS_METRICS.md)。

## 1. 结论

Alpha14 在 Alpha11 的 lease/fencing 与 Alpha13 的追加式 attempt/outcome 之上，增加了一个鉴权的、
不返回任务或 Worker 标识的调度聚合快照：

`GET /api/v1/operations/verification-dispatch`

它回答的是“当前数据库里是否有排队积压、活跃或过期租约、死信、近期租约波动及持久态矛盾”，
并在 `/backend-workflow` 显示为 `健康 / 需要关注 / 完整性事故`。业务积压和重试波动不会冒充
readiness 故障；只有已有完整性扫描发现持久态矛盾时，快照进入 `incident`，`/readyz` 同时继续
fail closed。

这仍只是按请求计算的数据库诊断聚合，不是时序监控、指标导出器、外部告警、uptime SLA、
生产容量结论或生产就绪声明。

## 2. API 合同

端点接受已配置的 operator、reviewer 或 auditor key；无 key 或错误 key 返回 401。响应只含聚合：

- `generated_at`：数据库 `CURRENT_TIMESTAMP`；
- `execution_mode`：`inline` 或 `external`；
- `thresholds`：排队告警、近期观察窗、lease 与 heartbeat 配置；
- `jobs`：总数和按持久化状态分组的数量；
- `dispatch`：lease 行数、活跃 lease、过期 running lease、未领取 queued、超时 queued、
  死信、最老 queued 等待时长和最老活跃 heartbeat 龄；
- `attempts`：attempt 总数、未终结 attempt、累计/观察窗内 outcome 分布和近期不稳定次数；
- `integrity`：dispatch/attempt 扫描问题数；
- `alerts`：稳定机器码、严重度、数量与不带标识的说明；
- `truth_note`：明确真实性边界。

响应不包含 `job_id`、`worker_id`、`worker_ref`、项目 ID、证据 ID 或错误原文。需要调查单项任务时，
仍必须使用已有鉴权任务详情与追加式 attempt 账本；聚合端点不承担明细审计导出。

## 3. 状态与告警语义

| 快照状态 | 条件 | `/readyz` |
|---|---|---|
| `healthy` | 本次查询没有聚合告警或完整性矛盾 | 不因该快照改变 |
| `attention` | 有排队超时、死信或观察窗内 lease 不稳定，但无完整性矛盾 | 保持原就绪判定 |
| `incident` | dispatch 或 attempt 完整性扫描发现矛盾 | 同一既有扫描使 readiness fail closed |

告警机器码：

| code | severity | count 口径 |
|---|---|---|
| `INTEGRITY_INCIDENT` | `incident` | dispatch 与 attempt 完整性问题总数 |
| `DEAD_LETTER_PRESENT` | `warning` | `dead_lettered_at` 非空的 lease 行数 |
| `QUEUE_WAIT_EXCEEDED` | `warning` | **仅**创建时间早于排队阈值的 queued 任务数，不是全部 queued 数 |
| `RECENT_LEASE_INSTABILITY` | `warning` | 观察窗内 `lease_expired + lease_lost + write_fenced` outcome 数 |

`committed_failure` 不自动等于租约不稳定；业务分析失败继续由任务错误分类和 attempt outcome 表达。
死信是累计持久态，未被显式处置前会持续产生 warning。

## 4. 配置

新增配置：

| 环境变量 | 默认值 | 校验 |
|---|---:|---|
| `FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS` | `60` | 1–86,400 秒 |
| `FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS` | `900` | 60–604,800 秒 |

`/api/v1/meta` 同时公开这两个运行配置，并继续说明 SQLite external 只适合本机单 Worker
开发/演示。

## 5. 前端行为

`/backend-workflow` 新增深色工业控制台式“调度健康 · 数据库时点快照”：

- 2×2 移动端、4 列桌面端显示 queued、active lease、dead letter、近期不稳定；
- 独立显示 warning 与 incident，不用同一种“失败”样式混淆业务积压和持久态矛盾；
- operator key 改变后延迟读取一次，也可手动刷新；没有伪造的定时实时监控；
- 401/403 时清空旧快照并提示检查 key；
- 明示“数据库时点快照，不是 uptime SLA、外部监控或生产就绪声明”。

请求序号会丢弃过期响应，避免用户快速更换 key 时旧请求覆盖新状态。

## 6. 实现边界与扩展风险

1. `generated_at` 来自数据库时钟，但统计由多条查询完成，未使用 serializable/read-only
   transaction。并发状态迁移时，各字段可能有短暂读偏差，因此这里的“快照”是诊断语义，
   不是单事务不可分割快照。
2. 完整性扫描当前读取相关持久态并在应用层核对，适合当前本地规模；数据量增长后需要索引审计、
   增量一致性检查或独立巡检，不能让管理端请求线性扫描生产历史。
3. 没有时间序列、速率、分位数、容量基线、Prometheus/OpenTelemetry exporter、告警路由、
   值班确认、抑制/去重或恢复通知。
4. `attention` 阈值是运维配置，不是服务等级目标；本轮没有负载或容量试验来证明 60 秒默认值合理。
5. 本轮没有真实 PostgreSQL。SQLite 查询通过不证明 PostgreSQL 的隔离级别、锁竞争、执行计划、
   多进程一致性或性能。

生产下一步应在真实 PostgreSQL 做 `0001 → 0002`、API/Worker 多进程、lease/reaper/fencing
故障注入和压力测试，再将这些聚合映射为低基数指标与外部告警；不要把本端点本身当作监控系统。

## 7. 验证证据

### 7.1 后端

- 配置、可观测性与 OpenAPI 合同组合：`40 passed`；
- migration、Worker CLI/lease/recovery/observability 组合：`50 passed`；
- 明确排除 9 个 POSIX-only 评测/Readiness 模块及 10 个本机无法构造的文件系统攻击用例：
  `254 passed, 27 skipped, 10 deselected`；
- 原生 Windows 全量：`153 failed, 303 passed, 31 skipped`，诊断 coverage `69.80%`；
- 153 个失败分类未变化：129 个 Evaluation POSIX secure-open、14 个 Algorithm Readiness
  POSIX secure-open、10 个 Windows 无法构造或语义不同的 symlink/FIFO/文件替换用例；
- `app/services/observability.py` 在该失败全量运行中的诊断覆盖率为 `98.39%`；
- `compileall`、`pip check` 和 OpenAPI 漂移检查通过；
- wheel `fengmou_backend-0.2.0-py3-none-any.whl` 成功构建，包含
  `app/services/observability.py` 与 `20260728_0002` migration。

Windows 全量失败不是通过证据，69.80% 也不是 90% 门禁证据。当前 head 仍必须在
Linux/Docker/WSL 重跑全量 `-W error --cov-fail-under=90`。

### 7.2 前端与依赖

- TypeScript、Vite 7.3.6、116 modules 构建通过；
- 单文件产物 `557.80 kB`，gzip `149.30 kB`；
- `npm audit --audit-level=moderate`：0 vulnerabilities；
- 锁定 React Router 8.3.0。官方发布页把 8.3.0 标为 latest；GitHub Advisory
  [GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2)
  标明受影响范围为 `>=7.12.0, <8.3.0`、修复版本为 8.3.0，且只影响 unstable RSC API。
  当前前端未使用 RSC。

### 7.3 OpenAPI

- 文件：`docs/openapi-v1.json`
- 大小：111,760 bytes
- SHA-256：
  `ba83d94c8c8fea89afa4ba8cc77fe6f8a54f18cb6aa072526c04adf45d5f30ad`

### 7.4 真实浏览器

在隔离临时 SQLite/存储、Alembic head `20260728_0002`、external Worker、2 秒排队阈值下验证：

1. 空库快照为 `healthy`；
2. 浏览器创建匿名项目/基线，选择项目内概念 PNG 并提交真实 API 任务；
3. 任务等待领取超过阈值后显示 `attention` 与 `QUEUE_WAIT_EXCEEDED ×1`；
4. 独立 Worker 进程领取并完成后，任务进入 `needs_review`，attempt 账本出现，快照恢复
   `healthy`；
5. 仅在隔离临时库注入过期 running lease 与代际矛盾后，页面显示 `incident`，operations
   端点保持 200 供诊断，`readyz` 返回 503；
6. 响应与页面均未显示原始 Worker 标识；
7. 390×844 视口的实际 client width/scroll width 均为 375，无横向溢出；
8. 最终浏览器 warning/error 日志为 0。

故障注入只作用于临时数据库，不修改项目运行数据；浏览器验证不证明多进程 PostgreSQL 或生产负载。

## 8. 变更入口

- 后端聚合：`backend/app/services/observability.py`
- API/schema：`backend/app/api/router.py`、`backend/app/schemas.py`
- 配置：`backend/app/config.py`
- 后端测试：`backend/tests/test_worker_observability.py`
- 前端 API/UI：`frontend/src/lib/api.ts`、`frontend/src/pages/BackendWorkflowPage.tsx`
- 合同：`docs/openapi-v1.json`
