# 第二阶段 Alpha17：PostgreSQL 安全验收入口

日期：2026-07-28  
状态：`executed on portable PostgreSQL 17.10 (Windows) / engineering candidate`  
**不是** release candidate，**不是**生产部署证明。Compose/Docker 路径仍未实跑。

## 1. 结论

Alpha17 完成两层证据：

| 层级 | 状态 |
|---|---|
| 安全验收入口源码 + 无服务器静态门禁 | **已完成**（20 unit tests） |
| 真实 PostgreSQL 上 21 项工程验收 | **已完成**（portable 17.10 / Windows / 非 superuser） |
| Docker Compose 验收路径 | **未执行**（本机无 Docker；用户将 Docker 列为次优先） |
| Linux clean runner / 90% coverage | **未执行** |
| 生产可用性、容量、SKIP LOCKED | **未评估**（多 worker 竞争 smoke 已有，但非压测） |

## 2. 交付物

| 路径 | 角色 | SHA-256 |
|---|---|---|
| `backend/scripts/postgres_acceptance.py` | 验收 harness | `27630909523cf2d2525d6d6e11e1fa7971d39124e5b4927c6b8b0049551d33c7` |
| `backend/tests/postgres/init.sql` | 非 superuser 初始化 | `394751b74a8e2b51ef5304ac5751ae37288f753765c489f0c68c95732e51b23a` |
| `compose.postgres-acceptance.yaml` | Docker 候选（未实跑） | `283e77070f63bd7e3107883130bd1cd6b3b870e77da1584d180e1acaefbab56a` |
| `backend/tests/test_postgres_acceptance.py` | 无 PG 门禁 | （随测试变更） |
| `output/postgres-acceptance-run.json` | 本轮实跑 JSON 报告 | `F416CC4D441129A080BC12739573A6CABD0D2749D11B4D9845853469F23FB2BB` |

便携运行时（**不在**交付源码树内，位于 ASCII 路径以免中文路径 initdb 失败）：

| 项 | 值 |
|---|---|
| 二进制包 | EDB `postgresql-17.10-2-windows-x64-binaries.zip` |
| 下载 URL | `https://get.enterprisedb.com/postgresql/postgresql-17.10-2-windows-x64-binaries.zip` |
| ZIP SHA-256 | `EF9B1E5E23D2E8A83914BA13D9DC536A72210FBA53FD1808FF1F7E06BB22B106` |
| 解压/数据根 | `E:\Workspaces\xtx\fengmou-tools\postgresql\` |
| 启停脚本 | `start-acceptance-pg.ps1` / `stop-acceptance-pg.ps1`（WMI 脱离 Job Object） |

## 3. 本轮实跑环境

| 项 | 值 |
|---|---|
| OS | Windows 11，build 10.0.26200 |
| Python | 3.12.13 |
| psycopg | 3.3.4 |
| SQLAlchemy | 2.0.51 |
| Alembic | 1.18.5 |
| PostgreSQL | **17.10** on x86_64-windows（MSVC 19.44.35227） |
| 监听 | `127.0.0.1:55432` only |
| 数据库 | `fengmou_acceptance` |
| 应用角色 | `fengmou_app`：NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOREPLICATION / NOBYPASSRLS；`can_create=true` |
| 启动方式 | portable binaries + `initdb --data-checksums` + WMI `Win32_Process.Create` 脱离 agent Job Object |
| Docker | 未使用 |

> 说明：在当前 agent shell 的 Job Object 内直接 `pg_ctl start` 会在命令结束后被杀掉子进程；必须用 WMI/独立进程启停。见 `fengmou-tools/postgresql/start-acceptance-pg.ps1`。

## 4. 实跑命令与结果

```powershell
# 确保实例在听（若已 stop 则先 start-acceptance-pg.ps1）
$env:FENGMOU_POSTGRES_ACCEPTANCE_URL = `
  'postgresql+psycopg://fengmou_app:local-postgres-app-acceptance-only@127.0.0.1:55432/fengmou_acceptance'
cd backend
.\.venv\Scripts\python.exe scripts\postgres_acceptance.py --jobs 8 --workers 4
```

| 观测 | 值 |
|---|---|
| 返回码 | **0** |
| 墙钟 | **2.976 s** |
| 报告 `ok` | **true** |
| 迁移 head | `20260728_0002` |
| 并发 upgrade | 2 |
| 幂等 upgrade + verify | 1 + 1 |
| 进程 worker 任务 | 8 jobs / 8 distinct processes with jobs / wave size 4 |
| fencing | stale gen=1 renew/write 拒绝；fresh gen=2 成功；outcomes `lease_expired` → `committed_success` |
| append-only | 4 次 UPDATE/DELETE 拒绝，SQLSTATE **23000**，行保留 |
| metrics | HTTP 200，Prometheus 合同通过 |
| readiness | before/after 均为 200 |
| schema | `fengmou_acceptance_878661ea8ff3498388879b1f` 创建后 **cleanup=dropped** |
| 事后 `pg_namespace` 查询 | **0** 个 `fengmou_acceptance_%` 残留 |
| 未触碰 | 未对其他数据库操作；`public` 未 drop |

### 4.1 报告摘要（字段）

```json
{
  "ok": true,
  "server": {
    "version": "17.10",
    "major": 17,
    "role": "fengmou_app",
    "role_flags": {
      "superuser": false,
      "createdb": false,
      "createrole": false,
      "replication": false,
      "bypassrls": false
    }
  },
  "migration": {
    "heads": ["20260728_0002"],
    "concurrent_upgrade_runs": 2
  },
  "application": {
    "process_workers": {
      "jobs": 8,
      "distinct_processes_with_jobs": 8
    },
    "fencing": {
      "stale_renewal_rejected": true,
      "stale_terminal_write_rejected": true
    },
    "append_only": {
      "mutations_rejected": 4,
      "sqlstate": "23000"
    }
  },
  "schema": { "cleanup": "dropped" }
}
```

完整 JSON：`output/postgres-acceptance-run.json`（及副本 `postgres-acceptance-run-2026-07-28.json`）。

## 5. 与提示词第七节 21 项对照

| # | 要求 | 结果 |
|---:|---|---|
| 1 | 专用可丢弃 `fengmou_acceptance` | 是 |
| 2 | 应用角色非 superuser | 是（flags 全 false） |
| 3 | 每次唯一临时 schema | 是 |
| 4 | 空 schema 双 Alembic upgrade | 是（2 concurrent） |
| 5 | `0001 → 0002` | 是（head=`20260728_0002`） |
| 6 | 重复 upgrade 幂等 | 是 |
| 7 | head/metadata/check/trigger drift | verify 通过 |
| 8 | append-only 四项拒绝 | 是，SQLSTATE 23000 |
| 9 | API verify mode | 是 |
| 10 | external API 只入队 | 是（external + workers 消费） |
| 11 | ≥2 独立 worker 进程竞争 | 是（8 distinct） |
| 12 | 每任务 generation=1 + committed_success | 是（worker_history） |
| 13 | 租约超时 reaper 重排 | 是（fencing 路径） |
| 14 | stale renew 失败 | 是 |
| 15 | stale 终态写回失败 | 是 |
| 16 | fresh 成功提交 | 是 |
| 17 | metrics 正确且无标识泄漏 | 合同检查通过 |
| 18 | readiness 最终 200 | 是 |
| 19 | 完整性扫描 0 issue | 是 |
| 20 | 自建 schema 删除 | 是 + 事后 SQL 复核 |
| 21 | 不触碰 public/未知 schema/其他库 | 设计保证 + 残留 0 |

## 6. 静态门禁（仍保留）

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_acceptance.py -W error
```

- 返回码 0；**20 passed**（实跑后复跑仍通过）

覆盖：缺 URL exit 2、驱动/库名/回环/凭据/query 拒绝、schema cleanup 命名、密钥 redact、worker 环境去污染、Compose digest/回环/tmpfs、init 角色 flags、metrics 0.0.4 合同、禁止 DROP DATABASE 等。

## 7. 草案审查中已修复项

- metrics 首行错误（`fengmou_verification_dispatch_status` → 全 family 合同）
- 缺测试 / Makefile / 文档
- Windows Job Object 下长驻 PG 需 WMI 脱离（运维脚本，非应用代码缺陷）

## 8. 真实性边界（不变）

- 实跑使用 **stub** analyzer，只证明 PostgreSQL 上的任务/租约/账本/迁移/指标工程行为。
- 不启用 `demo_fixture`，无真实远程模型密钥。
- 哈希链语义仍是完整性/篡改可检测，不是区块链或司法存证。
- metrics 验收不是 Prometheus 生产栈。
- **一次 Windows portable 实跑 ≠ Linux 发布门禁，≠ Compose 验证，≠ 容量/SLA。**
- winget 系统级安装曾尝试失败（UAC `0x800704c7`）；本轮采用便携 ZIP，未写入 Program Files 服务。

## 9. 本地复现（便携）

```powershell
# 1) 二进制已在 E:\Workspaces\xtx\fengmou-tools\postgresql\
# 2) 启动（脱离 Job Object）
powershell -File E:\Workspaces\xtx\fengmou-tools\postgresql\start-acceptance-pg.ps1

# 3) 验收
$env:FENGMOU_POSTGRES_ACCEPTANCE_URL = 'postgresql+psycopg://fengmou_app:local-postgres-app-acceptance-only@127.0.0.1:55432/fengmou_acceptance'
cd E:\Workspaces\xtx\项目\锋眸智鉴\code\backend
.\.venv\Scripts\python.exe scripts\postgres_acceptance.py --jobs 8 --workers 4

# 4) 停止
powershell -File E:\Workspaces\xtx\fengmou-tools\postgresql\stop-acceptance-pg.ps1
```

演示密码仅本地验收，视为公开；禁止用于生产。

Makefile 目标（Linux/make 环境）：`postgres-acceptance-static|up|run|down`。  
缺 URL 或 docker 时 **exit 2**，禁止静默 skip。

## 10. 未验证 / 下一步

1. Docker Compose 路径与镜像 digest 冷启动（次优先）。  
2. Linux clean runner：locked sync、全量 `-W error`、coverage ≥ 90%。  
3. 在更高 contention 下观测无效 claim/吞吐，再决定是否设计 `SKIP LOCKED`。  
4. 故障注入（worker kill -9、磁盘满）与更长 soak。  
5. 前端/浏览器主链在 PostgreSQL 上的 E2E（非本 Alpha 范围）。  

## 11. 密码与安全提示

- 仓库与本文出现的 `local-postgres-*-acceptance-only` 是**故意公开的演示凭据**。  
- 实跑报告 JSON **不含**密码；stderr 在失败路径会 redact。  
- 请勿把该实例暴露到非回环地址。
