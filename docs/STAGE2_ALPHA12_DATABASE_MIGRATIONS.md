# 第二阶段 Alpha12：数据库迁移基线与启动门禁

日期：2026-07-28  
状态：`engineering candidate / SQLite verified / PostgreSQL offline-only`

> 历史说明：本文冻结 Alpha12 当时的 `20260728_0001` head 与验证计数。Alpha13 已将当前 head
> 推进到 `20260728_0002`，并新增 attempt/outcome 表、追加式触发器及 Alpha11 旧库的
> `0001 → 0002` 接管路径；当前状态见
> [`STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](./STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。

## 1. 结论

后端不再把 `Base.metadata.create_all()` 当作开发、演示或部署启动时的通用迁移方案。当前新增
Alembic 初始基线 `20260728_0001`，API 与独立 Worker 在进入业务恢复逻辑前执行同一套 schema
门禁：

- 空库或已版本化数据库可以迁移到 `heads`；
- 未版本化但已有业务表的旧库拒绝自动升级或自动 stamp；
- 旧库只能通过显式 `adopt-legacy` 接管，且必须先通过应用元数据、索引、外键、唯一约束和
  命名 `CHECK` 约束核对；
- `verify` 要求 revision 位于应用 head，且当前反射结构与应用元数据无已知漂移；
- `/api/v1/meta.database_schema` 如实公开启动时的 mode、期望/当前 head 与漂移状态。

SQLite 路径已经在隔离库、独立 Uvicorn 进程和真实浏览器中验证。PostgreSQL 仅完成 psycopg 3
驱动配置、迁移 SQL 离线编译和事务级 advisory lock 代码；本机没有 PostgreSQL 服务，
**不能据此声称 PostgreSQL 运行时、并发 Worker 或生产部署已经通过**。

## 2. 迁移合同

### 2.1 Schema mode

| 环境 | 默认 mode | 启动行为 |
|---|---|---|
| `test`、`openapi-export` | `create_all` | 只用于隔离测试和合同导出，不写 Alembic revision |
| `development`、`demo` | `upgrade` | 对空库或已版本化库执行 Alembic upgrade，再核对 head 与漂移 |
| 其他环境 | `verify` | 只核对，不在 API/Worker 启动时执行 DDL |

可用 `FENGMOU_DATABASE_SCHEMA_MODE=create_all|upgrade|verify` 显式覆盖。`create_all` 在非本地/
测试环境会被配置校验拒绝。

推荐的部署顺序是：

1. 创建并验证数据库备份；
2. 以单独 release job 执行 `python -m app.schema upgrade`；
3. API 与 Worker 设置 `FENGMOU_DATABASE_SCHEMA_MODE=verify`；
4. 检查 `/api/v1/meta.database_schema` 与 `/api/v1/readyz`。

当前 Compose 是本地演示配置，因此显式使用 `upgrade`。

### 2.2 命令

在 `backend/` 下执行：

```bash
# 空库或已版本化库升级
python -m app.schema upgrade

# 只核对 revision 与已知 metadata drift
python -m app.schema check

# 仅在已备份、未版本化且结构与 Alpha11 元数据一致时接管旧库
python -m app.schema adopt-legacy

# 不连接数据库，分别编译两种方言的升级 SQL
python -m app.schema sql --dialect sqlite
python -m app.schema sql --dialect postgresql
```

PostgreSQL URL 必须显式选择 psycopg 3：

```text
postgresql+psycopg://user:password@host/database
```

`Settings` 的字符串表示不会输出数据库 URL，避免把 URL 中的凭据带入普通调试日志。

### 2.3 旧库接管

普通 `upgrade` 遇到“存在业务表但没有当前 revision”的数据库会 fail closed，并提示
`adopt-legacy`。接管不会运行 DDL，而是在核对通过后 stamp 当前 head；已有业务数据保持不变。

接管门禁使用 Alembic metadata comparison，并补充 Alembic autogenerate 不稳定覆盖的命名
`CHECK` 约束：

- SQLite 比较约束名称与规范化 SQL 表达式；
- PostgreSQL 为避免数据库反射重写等价表达式，当前只比较命名约束集合。

因此，“接管通过”表示结构符合当前实现可反射的应用合同，不等于字节级数据库取证或任意触发器、
视图、扩展对象均相同。正式旧库接管仍必须先备份并在副本演练。

### 2.4 并发与回退

- 文件型 SQLite 使用与 worker/sealing 相同的跨平台文件锁实现；两个并发 upgrade 已在 Windows
  上验证为串行完成。
- PostgreSQL 使用 `pg_advisory_xact_lock(bigint)`，锁随迁移事务提交、回滚或连接关闭释放；
  当前只有 SQLAlchemy/psycopg 代码路径，尚无真实服务器并发证据。
- 初始基线的 `downgrade()` 主动拒绝执行。它包含证据、复核、报告和 proof 业务表，自动删除
  不符合数据保全边界；需要回退时应恢复经过验证的备份。

## 3. 代码与制品

- `backend/app/schema.py`：升级、核对、显式旧库接管、锁和 CLI；
- `backend/app/migrations/versions/20260728_0001_alpha11_baseline.py`：16 个业务表的初始基线；
- `backend/app/main.py`、`backend/app/worker.py`：统一启动门禁；
- `backend/app/config.py`：环境默认、方言和 psycopg 3 URL 校验；
- `backend/pyproject.toml`：固定 `alembic==1.18.5` 与 `psycopg[binary]==3.3.4`；
- `docs/openapi-v1.json`：加入 `CapabilityMeta.database_schema`；
- 前端真实闭环页：状态卡显示“开发直建”或“迁移已校验”。

构建 wheel 后已逐项检查，wheel 包含 `env.py`、`script.py.mako`、revision 文件与
`app/schema.py`。Docker 镜像同时复制 `alembic.ini`。

## 4. 本轮验证

### 4.1 迁移专项

```text
9 passed
```

覆盖：

- SQLite 空库升级、head/漂移核对和重复升级；
- 两个线程、两个 Engine 对同一 SQLite 的串行升级；
- `verify` 模式启动 FastAPI；
- 未版本化旧库自动升级拒绝；
- 完全匹配旧库显式接管、数据保留与幂等接管；
- 缺失索引的旧库拒绝接管且不 stamp；
- 已版本化库的索引漂移拒绝；
- 未知/未来 revision 拒绝；
- SQLite/PostgreSQL 离线 SQL 编译；
- 独立 `python -m app.schema upgrade/check` 子进程。

### 4.2 当前 Windows 回归

全量命令仍受既有 POSIX 安全打开和本机 symlink/FIFO/文件替换前提阻断：

```text
153 failed, 288 passed, 31 skipped
coverage 68.69%
```

失败数和类别相对本轮开始前没有增加；通过数增加 20，来自新增的 9 个迁移测试和 11 个数据库
配置参数化用例。明确排除原有 POSIX-only 模块及 10 个本机无法构造的文件系统攻击用例后：

```text
239 passed, 27 skipped, 10 deselected
```

该子集通过 `-W error`，但不是 Linux 全量测试或 90% 覆盖率证据。

其他验证：

- `compileall`、`pip check`、两种方言离线 SQL编译通过；
- OpenAPI 与远程 analyzer 合同制品检查通过；
- OpenAPI：97,707 bytes，
  SHA-256 `d7e4bf7ad17ce9b06a217f40d87d934e4ceef2845b037de8b9abc0d41b792ebc`；
- 独立 Uvicorn 以全新 SQLite 和 `upgrade` 启动，`readyz=ready`，
  `current_heads=["20260728_0001"]`、`at_head=true`、`drift_free=true`；停止服务后独立
  `check` 再次通过；
- 前端 TypeScript、Vite 116 modules 构建和 npm audit 通过，0 vulnerabilities；
  单文件 540.70 kB，gzip 145.56 kB；
- 真实浏览器确认“API 在线 / 结构 迁移已校验”，页面布局正常，控制台 warning/error 为 0。

## 5. 未解除的边界

1. 在 Linux/Docker/WSL 重新执行全量 `-W error` 与 `--cov-fail-under=90`。
2. 在真实 PostgreSQL 上执行空库升级、旧库副本接管、metadata check、API/Worker E2E、
   两个迁移进程竞争和多 Worker 压力/故障测试。
3. 增加数据库备份/恢复演练、迁移审计与部署告警；当前 CLI 不替操作者创建备份。
4. PostgreSQL runtime 尚未验证，不能把“离线 SQL 可编译”写成“应用已支持生产 PostgreSQL”。
5. Alpha12 当时尚未实现 PostGIS、对象存储、项目级身份/RBAC、不可变 attempt 结果暂存和部署级可观测性。

## 6. 设计依据

- [Alembic command API](https://alembic.sqlalchemy.org/en/latest/api/commands.html)
- [Alembic cookbook：检查数据库是否位于 head](https://alembic.sqlalchemy.org/en/latest/cookbook.html)
- [SQLAlchemy PostgreSQL psycopg 驱动 URL](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html)
