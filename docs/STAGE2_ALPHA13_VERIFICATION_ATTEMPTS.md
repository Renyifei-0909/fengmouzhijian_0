# 第二阶段 Alpha13：不可变 Worker 尝试与结果账本

日期：2026-07-28  
状态：`engineering candidate / SQLite verified / PostgreSQL offline-only`

> 历史说明：本文保留 Alpha13 的 attempt/outcome 数据合同与当时验证数字。后续 Alpha14
> 没有改变 migration head 或追加式账本语义；当前调度可观测性、测试计数和 OpenAPI 摘要以
> [`STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`](./STAGE2_ALPHA14_WORKER_OBSERVABILITY.md) 为准。

## 1. 结论

Alpha13 在 Alpha11 的租约与 generation fencing、Alpha12 的 Alembic 启动门禁之上，增加了
`verification_attempts` 与 `verification_attempt_outcomes` 两张追加式表。每次成功领取任务都会在
同一事务内冻结一次 claim-time 快照；每次成功、失败、租约过期、租约丢失或陈旧写回都会至多追加
一个终态结果。后续 retry 会创建新 attempt，不会覆盖旧 attempt 或旧 outcome。

这解决的是“某次执行由谁在什么输入、算法版本和预算下领取，最后发生了什么”的数据库内可审计性，
并不把 at-least-once 外部调用变成 exactly-once 推理，也不等于 WORM 存储、外部签名、可信时间戳、
区块链或司法存证。

## 2. 数据合同

### 2.1 `verification_attempts`

每次领取时冻结：

- `job_id + generation` 与 `job_id + attempt_no` 双重唯一；
- 原始 `worker_id`、`execution_mode`；
- analyzer 名称和版本；
- evidence 与 baseline 的 SHA-256；
- 本次领取时的 `max_attempts`；
- 数据库时钟产生的 `claimed_at`。

attempt 行只描述领取时事实，不承担当前 owner、heartbeat 或 lease expiry；这些可变调度状态继续由
`verification_job_leases` 管理。

### 2.2 `verification_attempt_outcomes`

每个 attempt 最多一个 outcome：

| disposition | 含义 | 结果载荷 |
|---|---|---|
| `committed_success` | 结果、Finding Case、`needs_review` 和租约释放已在同一事务提交 | 不可变 `result_json` 与规范 JSON SHA-256 |
| `committed_failure` | 失败状态、错误分类、死信判定和租约释放已在同一事务提交 | 仅错误元数据 |
| `lease_expired` | reaper 或启动恢复确认租约已过期 | 仅错误元数据，可标记死信 |
| `lease_lost` | worker 在执行期间失去 lease 后停止 | 仅错误元数据 |
| `write_fenced` | 陈旧 worker 的终态写回被 owner + generation + expiry 条件拒绝 | 仅错误元数据 |

数据库 `CHECK` 约束要求只有 `committed_success` 能同时持有 `result_json/result_sha256`，其他
disposition 必须保持两者为 SQL `NULL`。成功结果摘要使用规范 JSON 字节计算，不依赖数据库 JSON
字段的展示顺序。

### 2.3 追加式约束

SQLite 与 PostgreSQL DDL 都为两张表安装 `UPDATE`、`DELETE` 拒绝触发器。schema 门禁不只核对
触发器名称，还检查触发器目标、操作和拒绝逻辑；同名空触发器不能通过 drift check。

该约束能阻止应用账号的普通改写。拥有修改 schema、禁用/删除触发器或直接恢复物理备份权限的
数据库管理员仍能重写历史，因此这里使用“数据库内追加式/应用级不可变”，不使用“绝对不可篡改”。

## 3. 原子性与竞态

- 领取条件更新、generation/attempt 计数递增和 attempt 快照在同一事务完成；并发领取只有胜者产生
  attempt。
- 成功 outcome、任务结果、Finding Case、`needs_review` 与租约释放在同一事务提交。
- 失败 outcome、任务失败状态、错误分类、死信状态与租约释放在同一事务提交。
- 过期回收通过一 outcome 唯一约束竞争；并发 observer 只能有一个追加成功。
- `allow_existing` 只接受已经存在真实 winner 的唯一冲突；其他完整性错误继续 fail closed。
- retry 创建更高 generation 的新 attempt；此前的失败或过期 outcome 保留。
- 陈旧 worker 不能覆盖新 owner 的结果。若目标 attempt 已有终态，既有 outcome 保持权威。

外部 analyzer 调用仍位于数据库事务之外。worker 在远端已接收请求、但本地提交前崩溃时，接管者
可能重发相同请求；远端仍必须按稳定 `Idempotency-Key` 幂等。

## 4. 迁移与旧库接管

当前 Alembic head 为 `20260728_0002`：

- `20260728_0001`：Alpha11 的 16 张业务表基线；
- `20260728_0002`：attempt/outcome 表、索引、约束和追加式触发器。

显式 `adopt-legacy` 现在区分两种精确结构：

1. 未版本化但已完全符合 Alpha13：直接 stamp 到当前 heads；
2. 未版本化且精确符合 Alpha11：先 stamp `20260728_0001`，再执行 `0002` 升级。

任何其他结构漂移仍拒绝接管。接管前必须由操作者创建并验证备份；CLI 不代替备份流程。

## 5. API、隐私与前端

`GET /api/v1/verifications/{id}` 新增按 attempt 顺序返回的 `attempts`：

- API 不返回原始 `worker_id`，只返回 `sha256:<64 hex>` 的 `worker_ref`；
- API 返回 result digest 与错误元数据，不返回 outcome 内冻结的 `result_json` 副本；
- SQLite 反射出的 naïve 时间按 UTC 解释后再序列化，浏览器显示不再产生 8 小时时区偏移；
- `/backend-workflow` 新增“Worker 尝试账本”，在桌面和 390 × 844 移动视口展示 attempt、
  disposition、预算、时间、结果摘要与输入快照。

`worker_ref` 是确定性假名，不是匿名化保证；知道候选 worker ID 的人可以离线枚举摘要。若 worker
名称本身敏感，生产环境应改用随机内部标识或带密钥的映射。

## 6. Readiness 完整性扫描

`/api/v1/readyz` 现在同时检查：

- attempt 的 digest 形状、预算与时间顺序；
- attempt 输入摘要与当前 evidence/baseline、analyzer pin 是否一致；
- 成功 outcome 的规范 JSON 摘要及其与任务当前结果的一致性；
- 非成功 outcome 不得夹带结果；
- failure/expiry/lost/fenced 各自的错误语义；
- lease generation/attempt_count 与最新 attempt 是否一致；
- 只有当前仍持有效 lease 的最新 attempt 可以暂时没有 outcome；
- 终态 outcome 不能与活跃 lease 并存。

Alpha12 之前已经存在、但没有 attempt 历史的非活跃数据允许保留；如果这类旧任务仍声称持有活跃
lease，readiness 会 fail closed。

## 7. 验证证据

本轮在原生 Windows 工作副本完成：

- 迁移专项：`11 passed`；
- worker 租约与 attempt 专项：`17 passed`；
- OpenAPI 合同：`5 passed`；
- 合并专项：`33 passed`；
- job recovery：`5 passed`；
- system：`4 passed`；
- remote HTTP integration：`5 passed`；
- 明确排除 POSIX-only 模块和 10 个本机无法构造的文件系统攻击用例后：
  `247 passed, 27 skipped, 10 deselected`；
- 原生 Windows 全量：`153 failed, 296 passed, 31 skipped`，诊断 coverage `69.34%`；
- `compileall`、`pip check`、SQLite/PostgreSQL 离线迁移 SQL 与 wheel 内容检查通过；
- wheel 使用本机已有 `setuptools 83.0.0` 的工作区 Python 离线构建；项目 `.venv` 本身未安装
  build backend，首次 `--no-build-isolation` 尝试按预期失败，本轮没有联网补装；
- 前端 TypeScript、Vite 116 modules 构建和 npm audit 通过，0 vulnerabilities；
  单文件 `546.60 kB`，gzip `146.82 kB`；
- OpenAPI `102,785 bytes`，SHA-256
  `cd3f0ce400b36eb22988a1eba37ed5648f024cec77fcba5a101b64a1d5195fbf`；
- 隔离 SQLite、独立 Uvicorn 与真实浏览器完成图片上传、任务执行和 attempt 账本检查；
  桌面/移动布局正常，控制台 warning/error 为 0，时区偏移问题已在复验中修正。

Windows 全量的 153 个失败仍集中在 POSIX secure-open/readiness 合同以及本机无法构造的
symlink/FIFO/文件替换语义。`69.34%` 是失败运行的诊断覆盖率，不是 90% 发布门禁证据。当前机器
没有 Docker/WSL，Alpha11 历史 `476 passed / 90.12%` 也不能冒充 Alpha13 的重新验收结果。

## 8. 未解除的边界与下一步

1. 在 Linux/Docker/WSL 对当前 head 重跑全量 `-W error` 和 `--cov-fail-under=90`。
2. 在真实 PostgreSQL 验证 `0001 → 0002`、空库/旧库副本、触发器、API/Worker E2E、并发迁移、
   多 worker 领取与故障注入。
3. 评估 PostgreSQL `FOR UPDATE SKIP LOCKED`，并建立队列深度、lease expiry、fenced write、
   dead letter 和 attempt integrity 告警。
4. 若合规目标要求独立抗管理员篡改，再引入只写对象存储、外部签名/可信时间与独立审计导出；
   不把当前数据库触发器包装成这些能力。
5. 继续推进真实获授权数据、non-mock baseline、冻结评测和独立 QA；attempt 账本只提升执行可审计
   性，不证明算法能力或 85%/90% 指标。
