# 基线复现与跨平台增量（2026-07-28）

状态：`development evidence / not release-qualified`

范围：当前工作副本审计、Windows 本地可运行性、前端依赖安全、真实浏览器闭环、数据库/文件/API 交叉核验。

同日后续新增的 Alembic 基线与数据库门禁见
[`STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`](./STAGE2_ALPHA12_DATABASE_MIGRATIONS.md)。本文按发生
顺序保留基线、Alpha12–Alpha16 的历史数字；依赖锁与构建边界以第 11 节及
[`STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](./STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)
为准；PostgreSQL 验收入口（static/unexecuted）以第 12 节及
[`STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`](./STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md)
为准。

## 1. 结论

本轮没有接入真实模型、训练数据或正式评测，也没有产生任何 85%/90% 指标。完成的是两类工程增量：

1. 前端从 React Router 7.18.1 迁移到 8.3.0，并将 React/ReactDOM 升级到 19.2.8；当前
   TypeScript、Vite 构建和 `npm audit` 均通过。
2. 后端的本地 worker 锁与封存锁增加 Windows `msvcrt` 实现，证据读取统一使用二进制
   descriptor，Windows 图片上传、任务执行、人工复核、报告/ZIP/ledger 封存主链已实际跑通。

当前工作副本仍不能判定为 Linux 发布候选：本机没有 Docker、WSL、`ffprobe` 或 `make`，
也没有可用的 Git 元数据。Evaluation v0、Algorithm Readiness 0 和完整的 POSIX 文件系统攻击
测试无法在当前原生 Windows 环境完成。

## 2. 审计环境与复现约束

| 项目 | 当前事实 |
|---|---|
| OS | Windows NT 10.0.26200 |
| Python | 3.12.13 |
| Node / npm | 24.18.0 / 11.16.0 |
| Git | 当前交付副本不含 `.git`，无法核对提交历史、分支和工作树差异 |
| Docker / WSL | 均不可用 |
| `ffprobe` / `make` | 均不可用 |
| 后端依赖 | uv 0.11.32 通用锁固定 47 个项目/registry package block；独立校验 PyPI 来源、制品哈希、项目 metadata 与构建约束；Windows/Python 3.12 clean sync 和 wheel smoke 已完成，Linux runtime 尚未验证 |
| 前端依赖 | `package-lock.json` lockfile v3；`npm ci` 可复现当前依赖图 |

README 和 Alpha11 文档中的 `476 passed / 90.12%` 是 2026-07-14 的历史候选证据，不是本轮
Windows 环境重新取得的结果。

## 3. 本轮代码增量

### 3.1 Windows 进程锁与二进制证据读取

- 新增 `backend/app/file_lock.py`：
  - POSIX 使用 `fcntl.flock`；
  - Windows 使用 `msvcrt.locking`；
  - 非阻塞竞争统一抛出 `FileLockBusyError`；
  - 两种后端都不可用时 fail closed。
- worker 的 SQLite 单进程锁与 sealing 的 operation/ledger 锁统一复用该实现。
- `FileStorage.validate_evidence_file` 的 `os.open` 增加 `O_BINARY`，避免 Windows CRT
  文本模式改变 `CRLF`/`0x1a` 字节语义。
- 封存临时 ZIP 以 `rb+` 打开后 `fsync`，避免 Windows 对只读 descriptor 执行 `_commit`
  返回 `EBADF`。
- Windows 不提供 `O_DIRECTORY` 时跳过目录 descriptor `fsync`；文件 `fsync` 和同卷
  `os.replace` 仍执行，但这不等价于 Linux 上的目录项持久化保证。
- POSIX `resource` supervisor 测试在不提供该模块的平台显式跳过，不伪造通过。

新增文件锁测试覆盖本机双 handle 竞争、POSIX/Windows fake backend、错误归一化和无后端
fail-closed。真实独立 Python worker 进程也已在同一 SQLite/存储上领取并完成任务。

### 3.2 前端依赖安全迁移

原 lockfile 使用 `react-router` / `react-router-dom` 7.18.1。GitHub 在 2026-07-22 发布
[`GHSA-qwww-vcr4-c8h2`](https://github.com/advisories/GHSA-qwww-vcr4-c8h2)，受影响范围为
`>=7.12.0 <8.3.0`，修复版本为 8.3.0。该问题位于不稳定 RSC API；当前项目使用
`BrowserRouter` 声明式路由，没有使用 RSC、loader、action 或相关 unstable API，因此没有
证据表明当前调用路径可触发漏洞，但原依赖图仍会被 `npm audit` 判为 2 个 high。

本轮完成：

- `react` / `react-dom` 固定为 19.2.8；
- `react-router` 固定为 8.3.0；
- 删除 v8 已移除的 `react-router-dom`，应用导入改为 `react-router`；
- 当前 Node 24.18.0 满足 React Router 8.3.0 的 Node `>=22.22.0` 要求；
- 迁移先在隔离临时副本验证，再应用到工作副本。

上游依据：

- [React Router v8.3.0 release](https://github.com/remix-run/react-router/releases/tag/react-router%408.3.0)
- [React Router changelog](https://raw.githubusercontent.com/remix-run/react-router/main/CHANGELOG.md)
- [React versions](https://react.dev/versions)

## 4. 当前验证结果

### 4.1 后端

当前原生 Windows 全量命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -W error --cov=app --cov-report=term-missing
```

实际结果：

- `153 failed, 288 passed, 31 skipped`；
- 当前覆盖率 `68.69%`，不能用于满足 90% Linux 门禁；
- 153 个失败按首要阻断聚类：
  - 129 个：Evaluation bundle/core/registry/CLI 依赖 `dir_fd/openat`、`O_DIRECTORY`、
    `O_NOFOLLOW` 等 POSIX 安全打开能力；
  - 14 个：Algorithm Readiness 0 使用同一安全根目录读取合同；
  - 10 个：当前 Windows 账户不能创建 symlink/FIFO，或 Windows 已打开文件的删除/替换
    共享语义与 POSIX 不同。

这些聚类说明当前平台不满足测试前提，不能据此宣称 Linux 回归失败，也不能把它们改写成
“通过”。必须在 Linux 容器或 WSL 中重新执行全量 `-W error` 与 `--cov-fail-under=90`，
由该结果判定是否存在真实回归。

排除上述 POSIX-only 模块，并明确 deselect 10 个无法在本机构造的文件系统攻击用例后，
Windows 可执行功能子集为：

- `239 passed, 27 skipped, 10 deselected`；
- `compileall` 通过；
- `pip check` 通过；
- OpenAPI 和远程 analyzer 请求/响应合同均与已提交制品一致。

这个子集只证明本机可执行主链，不是全量门禁，也没有覆盖率资格。

### 4.2 前端

```powershell
cd frontend
npm ci
npm run verify
```

实际结果：

- TypeScript `tsc --noEmit` 通过；
- Vite 7.3.6 构建通过，116 modules；
- 单文件产物 540.70 kB，gzip 145.56 kB；
- `npm audit --audit-level=moderate`：0 vulnerabilities。

`npm ci` 在 npm 11 下还会提示 `esbuild@0.28.1` 的 install script 未被 `allowScripts`
策略显式覆盖。本轮没有盲目批准脚本；构建可正常完成，但未来应在依赖治理中明确脚本策略。

### 4.3 合同制品

- `docs/openapi-v1.json`：97,707 bytes，
  SHA-256 `d7e4bf7ad17ce9b06a217f40d87d934e4ceef2845b037de8b9abc0d41b792ebc`；
- remote request schema：
  `08b65038de51f3f350beb76661317a04ee83c0c956561e419531657dab34d05e`；
- remote response schema：
  `e54bb4fb763be3ec6ef6c2bc41fdbfa3b3e3d5f66e3967af2bb6ab5d3c4e9248`。

## 5. 真实浏览器与持久化交叉核验

本轮在隔离的临时 SQLite/存储目录启动本地 FastAPI 与 Vite，通过 Codex 内置真实浏览器完成：

1. 创建匿名项目和设计基线；
2. 上传项目内的合成概念 PNG；
3. 显式选择 `demo_fixture`；
4. 等待任务进入 `needs_review`；
5. 使用 reviewer key 批准；
6. 生成 JSON/HTML 报告、ZIP 和本地 ledger；
7. 在工作流页和溯源页分别重新执行 8 项完整性核验；
8. 回归总览、项目、项目详情、报告、溯源和未知路由回退；
9. 浏览器控制台 warning/error 均为 0。

持久化对象：

| 对象 | ID / 状态 |
|---|---|
| Project | `8ee10bb2-946e-42eb-99a9-972afa902d3d` / `active` |
| Baseline | `7216c822-4397-417f-95d6-9980d3721ce8` |
| Evidence | `103584bd-e1e7-4199-8f85-9f2671ee949c` |
| Job | `fec09a32-9c96-4a47-85f5-ab364fb7436e` / `approved` / 100% |
| Report | `3311272b-2359-4b77-b13d-9f405e6e6488` / `reviewed_demo` |
| Proof | `ad212c9a-dd69-4964-b0e1-0abad1136d35` |
| Archive | `ARC-ad212c9a-dd69-4964-b0e1-0abad1136d35` |
| Seal operation | `completed` |
| Worker lease | 1 attempt，owner/lease 已释放 |

真实性字段保持为：

- `analyzer=demo_fixture`；
- `synthetic=true`；
- `accuracy_claim=null`；
- `purpose=demo`；
- `evidence_grade=false`。

原上传文件和服务端存储文件 SHA-256 均为
`24508010922aeb4586bfc46d1b35e4747553d4a0ad76369dc0ab9d55fcdde5dd`。
报告 JSON、HTML 和 ZIP 的磁盘摘要分别与 SQLite 封存值一致；ZIP 含 8 个 manifest 成员
和 `manifest.json`，本地 ledger 恰有 1 条对应记录。

API 反查同时验证：

- 无 Key 读取项目返回 401；
- 项目、任务、proof、报告和证据包读取返回 200；
- 完整原件返回 200，单 Range `bytes=0-31` 返回 206，字节与源文件一致；
- operator 与 auditor 均可执行只读 proof verify；
- `archive_exists`、`archive_sha256`、`manifest_sha256`、`member_hashes`、
  `merkle_root`、`record_hash`、`ledger_chain`、`metadata_consistency` 全部为 true。

这条链只证明软件闭环、摘要一致和当前本地文件未检测到篡改，不证明视觉模型能力、施工事实、
区块链、司法存证、可信时间戳或生产可靠性。

## 6. 未解除的关键阻断

1. 在 Linux/Docker/WSL 执行全量后端测试和 90% 覆盖率门禁；当前本机没有该运行环境。
2. 安装并固定 `ffprobe` 后再验证视频容器链；本轮真实 E2E 只使用图片。
3. 恢复 Git 提交元数据或取得带提交哈希的正式工作副本，建立可追溯变更基线。
4. Alpha16 已建立通用 lock、构建哈希约束并完成 Windows clean sync；仍需在 Linux
   clean runner 实际安装、执行全量门禁，并补 cold-cache/offline/CI 证据。
5. 保留 Linux 上的 symlink、hard-link、FIFO、TOCTOU 和目录 `fsync` 测试；不得因 Windows
   本机限制而删除或弱化。
6. 真实 P0 仍是合法授权数据、唯一任务主线、真实 non-mock baseline、冻结评测和独立 QA。

## 7. 同日数据库后续增量

Alpha12 已新增 `20260728_0001` 迁移基线、API/Worker schema mode、旧库显式接管、metadata
drift 门禁和前端“迁移已校验”状态。SQLite 与独立 Uvicorn 已验证；PostgreSQL 仍只有离线 SQL
编译，没有真实服务器证据。具体命令、测试和边界以
[`STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`](./STAGE2_ALPHA12_DATABASE_MIGRATIONS.md) 为准。

## 8. 同日 Worker 历史后续增量

Alpha13 已把 Alembic head 推进到 `20260728_0002`，新增数据库内追加式
`verification_attempts`/`verification_attempt_outcomes`、UPDATE/DELETE 拒绝触发器、触发器
body drift 核对、readiness 完整性扫描、API worker 假名与前端尝试账本。原生 Windows 当前全量为
`153 failed, 296 passed, 31 skipped`，明确排除 POSIX-only 前提后的子集为
`247 passed, 27 skipped, 10 deselected`；失败运行的诊断 coverage 为 `69.34%`，仍不是 Linux
90% 门禁证据。OpenAPI 为 102,785 bytes，SHA-256
`cd3f0ce400b36eb22988a1eba37ed5648f024cec77fcba5a101b64a1d5195fbf`。实现、测试、浏览器
复验和真实性边界以
[`STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](./STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md) 为准。

## 9. 同日 Worker 可观测性后续增量

Alpha14 新增鉴权的 `GET /api/v1/operations/verification-dispatch`：按数据库时钟聚合 queued、
active/expired lease、dead letter、attempt outcome 观察窗与既有完整性扫描，不返回任务或原始
Worker 标识。积压/近期波动只进入 `attention`，不会单独使 readiness 失败；持久态矛盾进入
`incident`，并由同一完整性门禁使 `/readyz` fail closed。`/backend-workflow` 已增加手动调度诊断
面板，并通过真实浏览器验证 healthy → queue attention → Worker 完成后恢复 healthy → 隔离故障
注入 incident/readyz 503，390×844 无横向溢出且最终 warning/error 为 0。

原生 Windows 当前全量为 `153 failed, 303 passed, 31 skipped`，明确排除 POSIX-only 前提后的
子集为 `254 passed, 27 skipped, 10 deselected`；失败运行的诊断 coverage 为 `69.80%`，仍不是
Linux 90% 门禁证据。OpenAPI 为 111,760 bytes，SHA-256
`ba83d94c8c8fea89afa4ba8cc77fe6f8a54f18cb6aa072526c04adf45d5f30ad`。具体口径、配置、测试、
浏览器证据和 PostgreSQL/外部监控边界以
[`STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`](./STAGE2_ALPHA14_WORKER_OBSERVABILITY.md) 为准。

## 10. 同日 Prometheus 指标后续增量

Alpha15 新增鉴权的
`GET /api/v1/operations/verification-dispatch/metrics`，把 Alpha14 数据库聚合映射为
Prometheus text 0.0.4 抓取时 gauge。标签只来自固定执行模式、任务状态、lease/attempt 状态、
outcome disposition、完整性组件和告警机器码；不返回任务/Worker/项目/证据 ID、错误原文或
告警说明。数据库出现不受支持的任务状态时只计入固定 `other`，同时触发完整性
`incident` 与 `/readyz` 503，原始状态不会成为指标标签。

针对性配置/观测/指标/OpenAPI 组合为 `45 passed`，指标渲染模块覆盖率 `100%`；
migration/Worker/recovery/observability/metrics/OpenAPI 组合为 `60 passed`。原生 Windows
当前全量为 `153 failed, 308 passed, 31 skipped`，明确排除 POSIX-only 前提后的子集为
`259 passed, 27 skipped, 10 deselected`；失败运行的诊断 coverage 为 `70.15%`，仍不是
Linux 90% 门禁证据。OpenAPI 为 113,391 bytes，SHA-256
`8a3d6c91bbb1c82773e5781cd0ad02f23dfe6a706cee3be4bb90fe3753a7cc42`。

当前没有 Prometheus Server、Alertmanager、Grafana、外部告警/值班路由、真实 PostgreSQL
抓取或容量测试，不能把 exporter 本身写成生产可观测性。指标清单、抓取草案、测试和部署边界以
[`STAGE2_ALPHA15_PROMETHEUS_METRICS.md`](./STAGE2_ALPHA15_PROMETHEUS_METRICS.md) 为准。

## 11. 同日通用依赖锁与干净环境后续增量

Alpha16 用 uv 0.11.32 生成 `backend/uv.lock`，固定运行时/dev extra 的跨平台 marker 图、
PyPI 制品 URL 与 SHA-256；最终为 47 个 package block，其中 1 个本地项目、46 个 registry
包，lock SHA-256 为
`ea6ce39184328f1c215aaedb77b819d5043546d84baf3001e3b504a88d30d7c8`。
`setuptools==83.0.0` 同时固定在 build-system、uv build constraint、lock manifest 和双哈希
约束文件中；`uv==0.11.32` 的当前 19 个 PyPI 制品也有独立哈希引导。标准库校验器与
9 个正/负向测试会拒绝来源、哈希、声明、extra 和构建约束漂移。

初次 OSV 审计发现 pytest 1 组、Starlette 5 组已知问题；升级为
FastAPI 0.140.7、Starlette 1.3.1、pytest 9.1.1，并为测试客户端加入 dev-only
httpx2 2.9.1。最终 Windows 3.12 与 Linux x86_64/3.12 的 uv marker 解析视图均为
46 packages / 0 known vulnerabilities；Linux 行不是实际 Linux 安装或测试。

在创建前不存在的 Windows/Python 3.12.13 虚拟环境中，locked sync、`pip check`、合同与
回归通过。当前全量仍为 `153 failed, 317 passed, 31 skipped`，失败分类保持
Algorithm Readiness 14 / Evaluation 129 / Windows 文件系统语义 10，诊断 coverage
`70.15%`；明确排除这些平台前提后的子集为
`268 passed, 27 skipped, 10 deselected`。最终哈希约束 wheel 又在仅安装锁定运行时依赖的
新环境中以 `--no-deps` 安装并通过 `pip check`，生成的 OpenAPI 仍为 113,391 bytes、
SHA-256 `8a3d6c91bbb1c82773e5781cd0ad02f23dfe6a706cee3be4bb90fe3753a7cc42`。

后端 Dockerfile 已从重新解析的 `pip install .` 改为：固定 Python 3.12.13 多架构 manifest
digest、哈希引导 uv、locked runtime sync、setuptools 哈希约束 wheel 构建和 `--no-deps`
安装；仓库测试锁定这些语句。但本机没有 Docker/Podman/WSL，尚未 build/run，Debian apt
ffmpeg 和前端基础镜像也未固定，不能写成 Compose 复现通过。

当前尚无 Linux runtime、空下载缓存的断网安装、签名/SBOM/SLSA、uv 可执行文件离线镜像、
Git/CI matrix 或位级可复现 wheel 证据。详细命令、漏洞处置、构建制品和边界见
[`STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](./STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)。

## 12. 同日 Alpha17 PostgreSQL 验收入口（portable 实跑通过）

Alpha17 审查并收口中断的 PostgreSQL 验收草案，随后在用户授权下使用 **EDB 便携
PostgreSQL 17.10 二进制**（非 Docker、非 Program Files 服务）完成真实服务器验收。

源码侧：

- 修正 metrics 合同：校验 Prometheus text `version=0.0.4` 与 required families（不再错误
  匹配不存在的 `fengmou_verification_dispatch_status`）；
- 无服务器单测 20 项；Makefile `postgres-acceptance-static|up|run|down`；缺 URL/docker → exit 2；
- `MANIFEST.in` 纳入 `scripts/postgres_acceptance.py`。

### 12.1 静态与相邻回归（仍有效）

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_acceptance.py -W error
# 20 passed
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_acceptance.py tests/test_database_migrations.py tests/test_worker_metrics.py tests/test_worker_observability.py tests/test_worker_leases.py tests/test_verification_recovery.py tests/test_openapi_contract.py tests/test_dependency_lock.py -W error
# 80 passed
```

OpenAPI 仍为 113,391 bytes / `8a3d6c91bbb1c82773e5781cd0ad02f23dfe6a706cee3be4bb90fe3753a7cc42`。

### 12.2 真实 PostgreSQL 实跑（2026-07-28）

| 项 | 值 |
|---|---|
| 二进制 | `postgresql-17.10-2-windows-x64-binaries.zip` |
| ZIP SHA-256 | `EF9B1E5E23D2E8A83914BA13D9DC536A72210FBA53FD1808FF1F7E06BB22B106` |
| 服务器 | PostgreSQL 17.10 / x86_64-windows / `127.0.0.1:55432` |
| 角色 | `fengmou_app` 非 superuser |
| 命令 | `python scripts/postgres_acceptance.py --jobs 8 --workers 4` |
| 返回码 | **0** |
| 墙钟 | **2.976 s** |
| 报告 | `output/postgres-acceptance-run.json` SHA-256 `F416CC4D…F2BB` |
| 结果 | 并发迁移 head `20260728_0002`；8 任务 / 8 独立 worker；fencing 与 append-only SQLSTATE 23000；metrics/readyz 通过；临时 schema 已删除（事后 `pg_namespace` 0 行） |

运行时位于 ASCII 路径 `E:\Workspaces\xtx\fengmou-tools\postgresql\`（中文路径下 initdb UTF-8
会失败；agent Job Object 内直接 `pg_ctl` 会被杀，需 WMI 启停脚本）。

### 12.3 仍未验证

- Docker Compose / 镜像 cold start；
- Linux 全量门禁与 90% coverage；
- 生产容量、SKIP LOCKED 决策、故障注入 soak；
- 浏览器主链绑 PostgreSQL 的 E2E。

**可以说：** 在 Windows portable PostgreSQL 17.10 上，Alpha17 安全验收 harness 的 21 项工程检查已实际通过。  
**不能说：** PostgreSQL 生产可用、Compose 已验证、或 Linux 发布候选。

完整命令、版本矩阵与边界见
[`STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`](./STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md)。

## 13. 同日本机工具链与 Windows 子集复验（用户授权后）

在不安装 Docker/系统服务的前提下，于 ASCII 路径部署便携工具：

| 工具 | 位置 | 校验/版本 |
|---|---|---|
| PostgreSQL 17.10 binaries | `E:\Workspaces\xtx\fengmou-tools\postgresql\` | ZIP SHA-256 `EF9B1E5E…B106` |
| FFmpeg/ffprobe 8.1.2 essentials | `E:\Workspaces\xtx\fengmou-tools\ffmpeg\` | ZIP SHA-256 `DB580001…A2EC` |

- 前端 `npm.cmd run verify` + `npm audit --audit-level=moderate`：TypeScript/Vite 通过，
  产物 557.80 kB / gzip 149.30 kB，**0 vulnerabilities**。
- Alpha17 静态 20 passed；便携 PG 上二次实跑 `--jobs 4 --workers 2` 返回码 **0**。
- 修复测试 helper `_inject_operational_finding`：原先只改 job analyzer/result、不改
  attempt/outcome，导致 Alpha13 完整性扫描在 restart/readiness 路径上 fail closed。现于
  SQLite 测试中短暂 drop 再重建 append-only trigger，协同改写 job+attempt+outcome；
  **不是**生产允许改历史，`tests/test_remediation_workflow.py` 全量 **14 passed**。
- Windows 可执行子集（忽略 Evaluation/Algorithm Readiness 模块，并 deselect 10 个需
  symlink/FIFO/替换特权的用例；PATH 含 ffprobe）：**315 passed, 10 deselected**，exit 0。
- 符号链接/硬链接/FIFO 攻击用例仍在 Linux 保留，未删除或永久 skip。
- 工具不在交付源码树内；≠ Linux 90% 门禁、≠ Docker 验证。

## 14. 长线工程增量（无 Linux 继续推进）

在确认 Linux/Docker 非本机硬前置后，按计划继续可执行工程项：

### 14.1 前端原型页真实性加固（WBS 3.1 局部）

- `Analytics` / `Devices` / `DataCockpit` / `GIS`：固定 **warning** 条说明 mock 边界；
- 假导出/诊断/巡检/GIS 刷新改为“原型动作：未调用后端/未落盘”；
- GIS 头部示意数字改为 `—`，避免 18/126/7 被当成运营指标；
- `Notice` 增加 `warning` 类型。
- 前端 verify：**通过**，产物 558.82 kB / gzip 149.63 kB，`npm audit` 0。

### 14.2 Windows 子集可复现入口

- `scripts/run_windows_backend_subset.ps1`：固定 ignore Evaluation/Algorithm Readiness +
  deselect 10 个 FS 特权用例；可选 prepend 便携 ffprobe。

### 14.3 PostgreSQL contention 观测（不实现 SKIP LOCKED）

- 新增 `backend/scripts/postgres_contention_observe.py`：复用 Alpha17 安全边界
  （回环 / `fengmou_acceptance` / 临时 schema / 清理）；
- 缺 URL → exit 2；**不**静默 skip；
- 2026-07-28 便携 PG 17.10 实测：

```text
--jobs 16 --workers 8 --waves 4
返回码 0，约 5.3 s
wave0: 8 launched / 8 processed / 0 idle
wave1: 8 launched / 8 processed / 0 idle
attempt_rows=16；generation 全为 1；multi-attempt jobs=0
idle_while_queue_nonempty=0
```

报告：`output/postgres-contention-observe.json`。  
结论：**当前短波观测未显示领取空转瓶颈**；`skip_locked_decision.implemented=false`，
仍保留 atomic claim + generation fencing，不改领取逻辑。

### 14.4 单测

- `tests/test_postgres_contention_observe.py` + 既有 acceptance 单测：22 passed（同机）。

## 15. 长线续作：原型页假成功路径收口（WBS 3.1）

继续在无 Linux/Docker/训练的前提下推进：

### 15.1 前端

| 页面 | 变更 |
|---|---|
| 隐蔽验真 AI | 固定 warning；去掉 ≥90% 示意数字；分数/置信度标“示意·非实测”；按钮改为原型动作文案 |
| 模型服务 | 固定 warning；导出/推理/版本切换诚实提示；Notice 改 info |
| 账户/系统设置 | 固定 warning；保存/备份/诊断/“上链”去假成功；退出登录标明无真实会话 |
| 侧栏 | 隐蔽 AI / 账户 / 系统 标「原型」；退出登录不再伪装成功 |

### 15.2 工程入口

- `scripts/run_local_quality_gates.ps1`：compileall / pip check / lock / OpenAPI / remote / SQL 离线 / PG 静态测 / 前端 verify
- 便携 PG：`start-acceptance-pg.cmd` / `stop-acceptance-pg.cmd`（不依赖 PowerShell ExecutionPolicy）

### 15.3 实测

- 前端 `npm.cmd run verify`：通过，audit 0
- remediation（PATH 含 ffprobe）：14 passed
- Windows 子集：`317 passed, 10 deselected`，exit 0
- OpenAPI 未变：`8a3d6c91…7cc42`

### 15.4 仍需人工才可推进

- Linux 全量 90% coverage
- Docker Compose 冷启动
- Git 远程 / CI
- 真实算法数据许可与训练
