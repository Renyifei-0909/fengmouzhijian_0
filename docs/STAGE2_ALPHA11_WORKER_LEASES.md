# 第二阶段 Alpha11：独立 Worker 租约、故障接管与写回隔离

日期：2026-07-14  
状态：`engineering candidate / local external-worker verified`  
范围：分析任务的领取、心跳、过期回收、fencing、有限预算、API/前端状态展示

> 历史说明：本文冻结 Alpha11 当时的实现与证据，因此下文“没有独立 attempt 历史表”是当时事实。
> Alpha13 已补齐追加式 attempt/outcome、结果摘要核对和前端尝试账本；当前状态见
> [`STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](./STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。

## 本轮结论

旧实现只保证两个线程首次竞争 `queued → running` 时有一个获胜。QA 已动态复现：旧 worker
仍在分析时启动第二个 API 实例，第二实例会把活跃 `running` 无条件重排队；新 worker 先完成，
旧 worker 随后仍能覆盖结果。一次复现中出现 2 次分析开始、2 次完成，最终结果由晚到旧 worker
写入。这不是超时配置问题，而是缺少持有者身份和 fencing。

Alpha11 已把业务数据库本身作为持久队列，新增 `verification_job_leases` 一对一表，避免改写
已有 `verification_jobs` 表，也不引入“数据库提交后消息未投递”的 broker/outbox 窗口。当前不
宣称它已是生产分布式队列。

## 核心不变量

1. 每次领取原子写入 `owner_id`，递增 `generation` 和 `attempt_count`；同一代际只允许一个
   owner。
2. heartbeat、成功和失败都必须匹配 `job_id + owner_id + generation`，并且租约尚未过期。
3. 分析期间不持有长数据库事务；heartbeat 使用独立 Session。
4. 完成写回、Finding Case 物化、`needs_review` 和租约释放在同一短事务内提交。
5. 过期 owner 被回收后，旧 owner 的成功和失败写回都会得到零行 CAS，并只留下
   `analysis_write_fenced` 审计，不能覆盖新 owner。
6. 尝试次数达到 `FENGMOU_VERIFICATION_MAX_ATTEMPTS` 后，对外保持兼容的 `failed`，内部
   dispatch 状态为 `dead_letter`；启动恢复和普通重试都不会复活它。
7. 租约时间以数据库 `CURRENT_TIMESTAMP` 为权威，不采用 worker 主机墙钟。

## 两种执行模式

### `inline`（默认）

适合当前一条命令的本地答辩演示。FastAPI `BackgroundTasks` 负责触发，但实际执行仍走同一套
claim、heartbeat、generation 和 fenced commit，不再保留无 token 的旧 runner。

### `external`

API 只在同一事务中保存证据、任务和初始 lease，返回 `202 queued`，不调用 analyzer。独立
worker 轮询数据库并执行：

```bash
cd backend
export FENGMOU_VERIFICATION_EXECUTION_MODE=external
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个终端使用相同数据库和存储配置：

```bash
cd backend
export FENGMOU_VERIFICATION_EXECUTION_MODE=external
python -m app.worker
```

一次性 smoke：

```bash
python -m app.worker --once --worker-id local-smoke
```

worker 收到 `SIGINT/SIGTERM` 后停止领取；当前正在执行的同步 analyzer 会在返回后再退出。远程
HTTP 已有固定超时，未来本地长模型应进一步放入可终止子进程。

## 配置

| 环境变量 | 默认 | 约束 |
|---|---:|---|
| `FENGMOU_VERIFICATION_EXECUTION_MODE` | `inline` | `inline/external` |
| `FENGMOU_VERIFICATION_LEASE_SECONDS` | `30` | 1–3600 秒 |
| `FENGMOU_VERIFICATION_HEARTBEAT_SECONDS` | `10` | ≥0.1 且小于 lease |
| `FENGMOU_VERIFICATION_MAX_ATTEMPTS` | `3` | 1–20 |
| `FENGMOU_VERIFICATION_WORKER_POLL_SECONDS` | `1` | 0.05–60 秒 |

`/api/v1/meta.verification_execution` 公布当前模式和参数；任务详情新增 `dispatch`，前端显示模式、
租约状态、generation、尝试次数/上限和有效租约截止时间。`readyz` 会拒绝 `running` 无 owner、
`queued` 仍持 owner 等持久态矛盾，但不会把正常 backlog 当作服务故障。

## SQLite 与生产边界

- SQLite `external` 仅允许 `development/test/demo`；生产配置会拒绝启动。
- 本机 SQLite worker 使用进程文件锁：POSIX 为 `flock`，Windows 为 `msvcrt.locking`；
  第二个 worker 进程会拒绝启动。Windows 支持证据见
  [`STATUS_2026-07-28_BASELINE_REPRODUCTION.md`](./STATUS_2026-07-28_BASELINE_REPRODUCTION.md)。
- 线程竞态测试能证明条件更新和 fencing 合同，但不能替代 PostgreSQL 多进程压力测试。
- Alpha11 当时没有每次 attempt 的独立不可变历史表；代际和预算由 lease 行、结果由 job、历史由
  `AuditEvent` 记录。若进入正式多节点部署，应增加 attempt 结果暂存、PostgreSQL
  `SKIP LOCKED`、指标/告警和故障注入。
- 当前 sealing 仍是独立 Saga 和本地 ledger 锁；多主机部署前还需数据库级分布式协调。

## 验证证据

新增专项覆盖：

- 双 worker 并发领取只产生一个 generation 1 owner；
- 活跃租约跨 API 启动保持 `running`，不会被重排队；
- 过期 A 被回收、B 获得更高 generation；
- B 完成后 A 的晚到成功被 fencing，最终结果仍属于 B；
- B 运行时 A 的晚到失败不能把任务改成 `failed`；
- heartbeat 只能续当前未过期代际；
- 人为偏移 worker `utcnow` 不影响数据库时钟租约；
- 连续租约丢失耗尽预算后进入内部死信，重启不复活；
- external API 只入队，任务详情如实返回 `unclaimed`；
- 真实 Python 子进程运行 `app.worker --once`，从同一 SQLite/存储领取并完成任务；
- 第二个 SQLite worker 进程锁被拒绝；
- inline/非法预算/生产 SQLite external 配置 fail closed。

全量门禁命令：

```bash
cd backend
python -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90
cd ../frontend
npm run verify
```

本次实际结果：后端 **476 passed**，`-W error` 通过，7,033 statements / 695 miss /
**90.12%**；worker 专项 **15 passed**。OpenAPI 96,231 bytes，SHA-256
`a7844c9c9fb9c007a97d9be5430cf4d8c657faa93efb65eb9b96bbf3966d8d2f`；
`compileall` 与 `pip check` 通过。前端 TypeScript、70 modules 生产构建和 `npm audit`
通过，0 vulnerabilities，单文件构建 541.90 kB（gzip 145.96 kB）。

本阶段只证明任务执行基础设施和演示链路。它没有改变算法真实性：`stub` 仍无物理结论，参考
服务仍是 STUB，真实模型训练、授权现场 E2E 和 85%/90% 正式指标仍未完成。
