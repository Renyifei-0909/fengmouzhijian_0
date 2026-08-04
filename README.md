# 烽眸智鉴 - 通信基建施工智能监管与可信交付

这是基于原始高保真前端 Demo 新建的后端优先 MVP，当前版本为 **0.2.0**。已经把“项目/设计基线 -> 视频或图片上传 -> 后台分析任务 -> 人工复核 -> 结构化报告 -> SHA-256/Merkle 证据包 -> 完整性核验”跑成真实数据链路。

> **2026-07-31 产品主线冻结（Alpha18）**：收口为  
> **「QGIS/GeoPackage 工程对象驱动的工单式施工合规验真」**。  
> 见 [`docs/algorithm-data/ADR_QGIS_WORKORDER_COMPLIANCE_2026-07-31.md`](docs/algorithm-data/ADR_QGIS_WORKORDER_COMPLIANCE_2026-07-31.md)  
> 与 [`docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md`](docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md)。  
> 已实现：设计包导入、工程对象、工单、GPS 空间校验、AI 观察与后端合规引擎分离。  
> 合成样例：`examples/design-package-demo/`（`synthetic=true`，非真实现场/非指标证据）。  
> **P0-6 前端**：`/gis-map` 接真实 API；`/work-orders/:id` 支持选工单→上传→空间校验→合规结论。

> 2026-07-28 当前工作副本的 Windows 复现、跨平台锁增量、前端依赖安全迁移和真实浏览器留证见
> [`docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md`](docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md)。
> 随后完成的 Alembic 基线、启动门禁、旧库显式接管和数据库真实性边界见
> [`docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`](docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md)。
> 当前 Alembic head `20260801_0004`（标准 GPKG 幂等导入列；其上为 `20260731_0003` / `20260728_0002`
> 不可变 worker attempt/outcome）见
> [`docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md`](docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md)
> 与 [`docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。
> 当前调度聚合快照、告警语义、真实浏览器故障注入与可观测性边界见
> [`docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`](docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md)。
> 当前低基数 Prometheus text 导出、抓取草案与尚未部署外部监控的边界见
> [`docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md`](docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md)。
> 当前后端通用依赖锁、漏洞升级、干净环境复现和哈希约束构建边界见
> [`docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)。
> PostgreSQL 安全验收入口（Windows portable 17.10 实跑通过；Docker/Linux 未验证）见
> [`docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`](docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md)。
> 下文 `476 passed / 90.12%` 是 Alpha11 的历史 Linux 候选证据；当前原生 Windows 环境没有
> Docker/WSL，不能替代全量 Linux 发布门禁。

## 当前已实现

- FastAPI + SQLite 后端、Alembic 初始基线与 revision/metadata 启动门禁、交互式 API 文档和
  已生成的 `docs/openapi-v1.json`；
- uv 0.11.32 通用后端依赖锁：运行时/dev 传递图固定到 PyPI 制品 SHA-256，uv 引导固定
  19 个发布制品哈希，构建后端 setuptools 83.0.0 另用双制品哈希约束；独立策略校验器会
  拒绝来源、哈希、metadata、extra 或构建约束漂移；
- 项目、工点语义化设计基线和版本摘要；
- MP4/MOV/AVI/MKV/WebM/JPG/PNG 上传、扩展名与 MIME 显式映射、大小限制、服务端 SHA-256；
- 视频通过 `ffprobe` 强制校验容器并提取元数据；图片上传不依赖 `ffprobe`；
- 原始证据鉴权回看：受控目录、普通文件/链接、规范 MIME、大小、magic 和完整 SHA-256 验证完成后，从同一文件描述符返回完整内容或单 Range；
- 基于 `X-API-Key` 的 operator/reviewer/auditor 最小职责分离，未配置时拒绝启动业务操作；
- 持久化任务状态和一对一 worker 租约：原子领取、数据库时钟、心跳续租、过期回收、单调 generation fencing、有限重试预算与内部死信；API 重启不再重排仍持有有效租约的任务；
- 数据库内追加式 worker 尝试与结果账本：领取时冻结输入/analyzer/预算快照，成功、失败、租约过期/丢失和 fenced write 各自保留终态；UPDATE/DELETE 触发器、schema drift 与 readiness 共同检查，API 仅公开 worker 假名和结果摘要；
- 鉴权的 Worker 调度聚合快照：按数据库时钟汇总排队、活跃/过期租约、死信、attempt outcome 与完整性问题，不返回任务/Worker 标识；积压和近期波动标记 `attention`，持久态矛盾标记 `incident` 并与既有 readiness fail-closed 保持一致；前端提供手动诊断面板；
- 鉴权的 Prometheus text 0.0.4 指标导出：只使用固定任务状态、attempt disposition、告警机器码和执行模式标签；未知任务状态收敛为 `other`，未知状态/disposition 均触发完整性事故，不输出任意值、任务/Worker 标识或告警原文；
- 统一严格 analyzer 结果合同，校验适配器身份、分析模式、输入/基线摘要和真实性字段，并拒绝嵌套声明注入、非有限数与无效 Unicode；
- 可插拔 `stub`、`demo_fixture` 与 `remote_http` 适配器；远程桥默认关闭，响应强制区分 `model/true/capabilities` 与 `stub/false/[]`，期望 runtime mode 纳入任务版本指纹，并已生成请求/响应 JSON Schema；
- 可独立启动的远程算法参考服务：固定 Bearer、模型制品身份、请求/媒体摘要、进程内幂等和响应合同均可直接联调；其默认 predictor 是空输出 STUB，不是实际视觉模型；
- 独立 Evaluation v0 离线评分核：冻结 manifest/cases/private labels/model statement/predictions、外部摘要 pin、六类分组隔离、严格覆盖、混淆矩阵与 P/R/F1/Wilson；所有结果仍固定为不具备合规声明资格；
- development-only 本地评测运行器：固定 run plan、数据/模型/入口/训练声明/evaluator 摘要，在 public 推理视图中执行 `train`/`validation` predictor，再把 predictions 交给 Evaluation v0 评分；
- development evidence bundle：从 runner 内原子发布固定五文件目录，公开 score 删除 private-label 摘要和原始日志，绑定 public-cases/case-id roster，并提供离线 `verify-dev-bundle`；
- 强制人工复核；
- 持久化 Finding Case 与整改闭环：analyzer finding 先作为候选 observation，经 reviewer 分诊后才进入案件；支持负责人/期限、整改 Attempt、锁定原项目与基线的复验、显式 resolved/not_resolved 判定和新 proof 关闭；`demo` 案件始终与运营统计隔离；
- 整改绑定使用版本/CAS 防止并发双绑定；readiness 会重新核对 case → attempt → re-verification job → report → proof → ZIP 冻结快照，原报告不会被整改流程反写；
- 持久化 `SealOperation` 封存 Saga：冻结复核快照、同盘暂存与原子发布、进程间 ledger 锁、幂等恢复、启动补偿和实时 readiness 完整性扫描；
- Alpha9 补齐封存提交结果协调：最终 commit 确认丢失时以新 Session 核对完整的 job/review/report/proof/audit/整改数据库图和已发布制品，暂存清理失败不再误报业务失败；任务详情依据持久化失败分类返回可重试分析、可继续封存或完整性阻断的统一恢复合同；
- 结构化 JSON 报告和可打印 HTML；
- 包含原始证据、分析结果、基线、传感器事件、复核和报告的 ZIP；
- 单文件摘要、Merkle Root、manifest 摘要和追加式本地哈希链；
- API 与独立命令行双重完整性校验，报告和证据包下载前再次校验封存摘要；
- 前端提供真实总览、项目、项目详情、报告、溯源和“真实闭环联调”页面；项目任务支持鉴权 Blob 视频/图片回看、64 MiB 自动加载保护和 Object URL 回收；
- 已形成算法/数据决策包：公开数据与许可筛选、A/B 路线评分、标签/指标 v0、数据卡和 48 小时门禁；当前只建议、尚未由团队确认；
- Algorithm Readiness 0 已实现固定 ZIP 与 2,844 个解压文件逐字节核对、标签/缺陷登记和 fail-closed pilot 诊断；未签名自述、未验证运行时和非原子 handoff 永远不能产生启动资格；
- Alpha11 历史全量门禁含 476 个后端测试，覆盖鉴权、正常闭环、Finding 分诊/整改/复验/关闭、整改最终提交故障后的跨进程恢复、提交确认丢失与完成态数据库图核对、暂存目录清理/符号链接、历史 proof ID 兼容、持久化失败重试分类、worker 双领取、有效租约跨 API 重启、过期接管、旧成功/旧失败写回 fencing、heartbeat、数据库时钟、预算耗尽死信、独立进程消费与 SQLite 单 worker 锁、并发绑定与闭环图篡改、项目聚合与跨项目隔离、封存 Saga、输入双摘要、适配器合同、远程 runtime 身份、参考服务、非法/伪装上传、原始证据安全回看、数据工作副本内容漂移、未签名批准 fail-closed，以及离线评分、development runner、evidence bundle、一次性 registry 和 Ed25519 controlled-local evidence 场景；2026-07-28 另增 4 个跨平台文件锁测试，当前 Linux 全量数量与覆盖率待重新验收。

## 必须保留的真实性边界

- `stub` 只验证工程链路，不输出物理量测或准确率。
- `demo_fixture` 是确定性的合成演示数据，默认关闭；即使人工批准，其报告仍标记 `reviewed_demo`，证据包仍标记 `purpose=demo`、`evidence_grade=false`。
- `remote_http` 只是受控的远程算法桥，默认关闭；生产配置默认只接受 `runtime.mode=model`，显式 test/demo STUB 固定为 `synthetic=true` 且不能进入运营案件。真实目标算法、授权冻结数据和受控模型执行尚未接入，离线评分核也不能据此声称识别能力或指标达标。
- `app.reference_analyzer` 是协议参考服务。它走真实 HTTP 并能完成报告/证据包闭环，但默认只返回空 observations 和 STUB limitation；“HTTP 已跑通”不能替代真实 non-mock baseline 或 EvaluationRun。
- development runner 不是正式盲测沙箱：模型入口仍以当前用户同一 UID 运行，网络也未隔离；`private_labels_copied=false` 只表示没有复制到临时推理视图，不能证明进程无法从其他本机路径读取 private labels。
- development evidence manifest 始终是 unsigned；外部摘要匹配只证明字节一致，不证明来源可信、模型隔离、隐私真实性或 score 已重新计算。
- `inline` 模式保留单命令本地演示，但同样经过租约和 fencing；`external` 模式由独立数据库轮询 worker 消费。SQLite external 只允许本机单 worker 开发/演示，生产多进程仍需 PostgreSQL、真实压力测试和部署级可观测性，不能由当前本机 smoke 外推。
- attempt/outcome 的数据库触发器能阻止普通应用 UPDATE/DELETE，但数据库管理员仍可修改 schema 或物理数据；它不是 WORM、外部签名、可信时间戳、区块链或 exactly-once 推理。
- `/operations/verification-dispatch` 是按请求执行的多查询数据库诊断聚合，不是原子时序快照；其
  `/metrics` 子路径只把同一抓取时快照映射为低基数 gauge，不提供 Prometheus Server、
  Alertmanager、仪表盘、外部告警、uptime SLA 或生产容量证明。
- PostgreSQL：psycopg 3 配置、迁移 SQL 离线编译、advisory-lock 代码路径，以及 Alpha17
  安全验收 harness。2026-07-28 在 **Windows 便携 PostgreSQL 17.10**（`127.0.0.1:55432`、
  非 superuser `fengmou_app`）上实跑通过：并发 `0001→0002`、8 worker 进程竞争、fencing、
  append-only SQLSTATE 23000、metrics/readyz、临时 schema 清理。**Docker Compose 路径未实跑**；
  非容量压测、非 Linux 发布门禁，不能声称生产可用。
- 当前是上传后的批处理，不是 RTSP/WebRTC 直播流或秒级实时监管。
- 当前设计基线由项目人员显式绑定，不是自动 QGIS/CAD 空间配准。
- 当前本地哈希链可以检测篡改，但不是区块链、司法存证或可信时间戳。
- 赛题要求的 85%/90% 指标尚未验证，必须由冻结数据集和真实非 mock 模型产生。
- Evaluation v0 与 Algorithm Readiness 0 的根目录约束读取依赖 `dir_fd/openat`、`O_DIRECTORY`
  与 `O_NOFOLLOW`；Linux/macOS 可用，不支持这些能力的原生平台会安全拒绝运行，应使用项目
  Docker/WSL 环境。
- Windows 本地 worker 与封存主链现使用 `msvcrt` 进程锁并已通过图片闭环；Windows 不提供
  `O_DIRECTORY` 时无法执行目录 descriptor `fsync`，因此不能把本机封存耐久性外推为 Linux
  发布或生产等价保证。
- `uv.lock` 是跨平台解析图，不是跨平台运行证据；当前只有 Windows x86_64 / Python 3.12.13
  完成 clean sync、测试、哈希约束构建和 wheel smoke。PyPI SHA-256 也不是发布者签名、
  SBOM、SLSA provenance 或恶意代码审计。

## 最快启动方式

### Docker Compose

```bash
docker compose up --build
```

启动后访问：

- 前端：http://127.0.0.1:5173/backend-workflow
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/readyz

空数据库可在后端启动后执行 `python backend/scripts/seed_stage2_demo.py`，通过公开 API 幂等生成一条带 H.264 无真人样例、报告、证据包和 8 项完整性核验的合成演示链路。

Compose 只为本地演示打开 `demo_fixture`。正式开发和评测环境应关闭该开关。
Alpha16 已让后端镜像改用固定 Python manifest digest、locked runtime sync 和 wheel 安装，
但当前 Windows 主机没有 Docker/WSL，本轮未重新 build/run Compose；前端基础镜像与 Debian
apt ffmpeg 也尚未固定，不能把 Dockerfile 静态检查写成容器复现通过。

算法组接入前可先按 [`docs/REFERENCE_ANALYZER_SERVICE.md`](docs/REFERENCE_ANALYZER_SERVICE.md)
启动独立参考服务，并运行 `backend/scripts/seed_remote_reference_demo.py`。这条命令会走真实
HTTP、人工复核、报告和证据包，但它只验证合同兼容性，不能作为模型能力证据。

### 本地开发

后端：

```bash
cd backend
# 视频上传前置条件；Docker 镜像已内置 ffmpeg/ffprobe
ffprobe -version
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
source .venv/bin/activate
FENGMOU_ALLOW_DEMO_ANALYZER=true \
FENGMOU_OPERATOR_API_KEY=local-operator-change-me \
FENGMOU_REVIEWER_API_KEY=local-reviewer-change-me \
FENGMOU_AUDITOR_API_KEY=local-auditor-change-me \
uvicorn app.main:app --reload --port 8000
```

`development/demo` 默认对空库或已版本化库执行 `python -m app.schema upgrade`；其他环境默认
只执行 `verify`。未版本化但已有业务表的旧库会拒绝自动 stamp，必须先备份并显式执行：

```bash
python -m app.schema adopt-legacy
python -m app.schema check
```

生产发布应把 `upgrade` 放在独立 release job，API 和 Worker 使用
`FENGMOU_DATABASE_SCHEMA_MODE=verify`。迁移合同见
[`docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`](docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md)，
当前 `0002` 增量见
[`docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。

需要演示 API 与 worker 分离时，API 和 worker 两个终端都设置
`FENGMOU_VERIFICATION_EXECUTION_MODE=external`，再从 `backend/` 启动：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.worker
```

此 SQLite 用法有进程锁，只允许一个本机 worker。任务详情会显示租约代际、尝试次数、
`unclaimed/leased/released/dead_letter`，以及不覆盖旧记录的 Worker 尝试账本；租约历史合同见
[`docs/STAGE2_ALPHA11_WORKER_LEASES.md`](docs/STAGE2_ALPHA11_WORKER_LEASES.md)，当前 attempt
合同见 [`docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。
输入 operator、reviewer 或 auditor key 后，可通过
`GET /api/v1/operations/verification-dispatch` 或联调页“调度健康”面板读取不含任务/Worker 标识的
聚合诊断；阈值和真实性边界见
[`docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`](docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md)。
抓取器可使用同一只读 Key 读取
`GET /api/v1/operations/verification-dispatch/metrics`；固定指标、密钥文件配置草案和未部署边界见
[`docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md`](docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md)。

前端（另一个终端）：

```bash
cd frontend
# React Router 8.3.0 要求 Node >=22.22.0
cp .env.example .env.local
# 将 .env.local 中的 operator/reviewer key 改成与后端一致
npm ci
npm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8000`。

Compose 中的 API Key 只用于本机演示且会出现在构建产物中，不具备保密性。正式环境必须改用真实身份系统/JWT，并轮换所有演示密钥。

## 测试

```bash
cd backend
python -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90
```

第二阶段 Alpha11 候选版本严格复验：476 个测试全部通过，`-W error` 通过，应用代码覆盖率 90.12%（门禁为 90%）；worker 专项 15 passed，包含真实独立进程消费。参考服务、原始证据与第一阶段冻结数据均保留真实性边界，不与真实模型指标混用。

2026-07-28 Alpha16 后的原生 Windows 复现没有重复取得上述 Linux 门禁：全量为
153 failed、317 passed、31 skipped，失败仍集中在 POSIX secure-open/readiness 合同和本机
无法构造的链接/FIFO/文件替换语义；明确排除这些平台前提后的可执行子集为
268 passed、27 skipped、10 deselected。
失败运行的诊断 coverage 为 70.15%，不是 90% 覆盖率证据。完整分类与历史真实浏览器/磁盘交叉核验见
[`docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md`](docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md)，
当前增量证据见
[`docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)。

Alpha17 无服务器单测与便携 PostgreSQL 17.10 实跑已通过（见
[`docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`](docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md)
与 `output/postgres-acceptance-run.json`）。另提供 **contention 观测**（不改领取逻辑、不实现
SKIP LOCKED）与 Windows 子集脚本：

```powershell
# Windows 可执行子集（非 Linux 90% 门禁）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_windows_backend_subset.ps1

# 本地质量门禁（无 make / 无 Linux）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_local_quality_gates.ps1

# 需验收库 URL；缺省 exit 2
$env:FENGMOU_POSTGRES_ACCEPTANCE_URL = 'postgresql+psycopg://fengmou_app:...@127.0.0.1:55432/fengmou_acceptance'
cd backend
.\.venv\Scripts\python.exe scripts\postgres_contention_observe.py --jobs 16 --workers 8 --waves 4
```

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_acceptance.py -W error
# 实跑需要本机验收库 + 显式 URL；缺失 URL 必须失败，不得 skip
# $env:FENGMOU_POSTGRES_ACCEPTANCE_URL = 'postgresql+psycopg://fengmou_app:...@127.0.0.1:55432/fengmou_acceptance'
# .\.venv\Scripts\python.exe scripts\postgres_acceptance.py --jobs 8 --workers 4
```

依赖、漏洞与构建检查：

```bash
cd backend
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv audit --locked --preview-features audit-command --no-python-downloads
uv build --build-constraints build-constraints.txt --require-hashes --no-python-downloads
```

契约制品：

- `docs/openapi-v1.json`
- `docs/remote-analyzer-request-v1.schema.json`
- `docs/remote-analyzer-response-v1.schema.json`
- `docs/REMOTE_ANALYZER_CONTRACT.md`
- `docs/REFERENCE_ANALYZER_SERVICE.md`

Evaluation v0 的可执行非正式合同示例位于 `examples/evaluation-v0-nonformal/`。它使用两段可完整解码的几何 MP4、public/private 目录分离、常量 fixture predictor 和一条自然错误的 prediction，只验证合同而不表示模型能力：

```bash
make evaluation-example-check
make evaluation-example-run-dev-check
make evaluation-example-evidence-check
```

预期 accuracy 为 0.50、阈值失败，并固定 `gate_status=not_eligible`、`compliance_claim_eligible=false`。

运行器合同、固定摘要与同 UID/未禁网威胁边界见 [`docs/DEVELOPMENT_EVALUATION_RUNNER.md`](docs/DEVELOPMENT_EVALUATION_RUNNER.md)，unsigned 证据包实现见 [`docs/DEVELOPMENT_EVIDENCE_BUNDLE_DESIGN.md`](docs/DEVELOPMENT_EVIDENCE_BUNDLE_DESIGN.md)，本地一次性消费状态机与 CLI 见 [`docs/HOLDOUT_REGISTRY.md`](docs/HOLDOUT_REGISTRY.md)，Ed25519 受控本地验签合同见 [`docs/CONTROLLED_SIGNED_EVIDENCE.md`](docs/CONTROLLED_SIGNED_EVIDENCE.md)；Construction-PPE 算力盘点与待批准 pilot 见 [`docs/algorithm-data/PPE_BASELINE_COMPUTE_PLAN_2026-07-10.md`](docs/algorithm-data/PPE_BASELINE_COMPUTE_PLAN_2026-07-10.md)，实际只读审计和永久不授权启动的诊断门禁见 [`docs/algorithm-data/ALGORITHM_READINESS_0.md`](docs/algorithm-data/ALGORITHM_READINESS_0.md)。当前仍未安装训练依赖、下载权重或启动训练。

第一阶段现场演示、启动、自测和故障排查见 [`docs/FIRST_STAGE_DELIVERY_GUIDE.md`](docs/FIRST_STAGE_DELIVERY_GUIDE.md)，冻结交付范围与真实性边界见 [`docs/STATUS_2026-07-11_STAGE1.md`](docs/STATUS_2026-07-11_STAGE1.md)。

第二阶段项目门禁与答辩价值评审见 [`docs/STAGE2_REVIEW_2026-07-14.md`](docs/STAGE2_REVIEW_2026-07-14.md)，本地演示和自测见 [`docs/STAGE2_DEMO_GUIDE.md`](docs/STAGE2_DEMO_GUIDE.md)，原始证据威胁模型见 [`docs/EVIDENCE_PREVIEW_QA.md`](docs/EVIDENCE_PREVIEW_QA.md)，前端“证据运营中心”概念稿见 [`docs/design/stage2-operations-center-concept.png`](docs/design/stage2-operations-center-concept.png)。

并发显式重试、模式化报告边界、Image 2.0 概念图、移动端真实性修复和最新浏览器留证见
[`docs/STAGE2_ALPHA6_DELIVERY.md`](docs/STAGE2_ALPHA6_DELIVERY.md)。

可恢复封存 Saga、故障注入矩阵、移动端报告卡片和 Alpha7 真实运行态验收见
[`docs/STAGE2_ALPHA7_DELIVERY.md`](docs/STAGE2_ALPHA7_DELIVERY.md)。

Finding 人工分诊、整改 Attempt、锁定原项目/基线复验、闭环证明双向校验、真实 API 前端和 Alpha8 浏览器留证见
[`docs/STAGE2_ALPHA8_REMEDIATION.md`](docs/STAGE2_ALPHA8_REMEDIATION.md)。

整改关闭跨重启恢复、提交确认协调、统一 recovery API、显式继续封存 UI 和 Alpha9 浏览器留证见
[`docs/STAGE2_ALPHA9_RECOVERY.md`](docs/STAGE2_ALPHA9_RECOVERY.md)。

数据工作副本逐字节审计、Readiness 0 fail-closed 诊断、远程 model/STUB 机器身份和真实 socket STUB 隔离验收见
[`docs/STAGE2_ALPHA10_ALGORITHM_READINESS.md`](docs/STAGE2_ALPHA10_ALGORITHM_READINESS.md)。

本轮基线复现、Windows 增量、前端依赖迁移和浏览器/磁盘交叉核验见
[`docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md`](docs/STATUS_2026-07-28_BASELINE_REPRODUCTION.md)。
Alembic 初始基线、API/Worker 启动门禁、旧库显式接管与 PostgreSQL 未验证边界见
[`docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`](docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md)。
不可变 worker attempt/outcome、迁移 head `20260731_0003`（含 `20260728_0002` attempt 账本）、API 隐私边界与浏览器尝试账本见
[`docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`](docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md)。
Worker 调度聚合快照、告警/readyz 分层、前端诊断面板与 Alpha14 浏览器故障注入见
[`docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`](docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md)。
Worker 低基数 Prometheus 指标、固定标签/隐私合同与 Alpha15 运维边界见
[`docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md`](docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md)。
后端通用锁、漏洞修复、干净环境与 wheel 安装证据见
[`docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)。
2026-07-14 阶段实现、验证和本地演示种子见 [`docs/STATUS_2026-07-14_STAGE2.md`](docs/STATUS_2026-07-14_STAGE2.md)。
远程算法参考服务、真实 HTTP smoke、前端真实性修复和当前本地留证见
[`docs/STAGE2_ALPHA4_DELIVERY.md`](docs/STAGE2_ALPHA4_DELIVERY.md)。
总览、项目任务和项目报告的跨页面真实性统一见
[`docs/STAGE2_ALPHA5_UI_TRUTH.md`](docs/STAGE2_ALPHA5_UI_TRUTH.md)。

前端验证：

```bash
cd frontend
npm ci
npm run verify
```

当前构建使用 React/ReactDOM 19.2.8、React Router 8.3.0；TypeScript、Vite 和
`npm audit --audit-level=moderate` 通过，0 vulnerabilities。

## 独立校验证据包

```bash
python backend/scripts/verify_bundle.py path/to/ARC-xxx.zip \
  --expected-archive-sha256 <API返回的archive_sha256>
```

返回码 0 表示包内摘要和可选的整体摘要一致；返回码 1 表示失败。该命令不验证身份、外部可信时间或区块链锚定。

## 目录

```text
backend/       FastAPI、数据模型、分析适配器、报告、证据包与测试
frontend/      原 Demo + 真实闭环联调页
docs/          项目计划、需求追踪、架构、接口交接和今日进度
compose.yaml   本地双服务演示
runtime-data/  运行时数据库、证据与报告（首次启动自动创建，不入库）
```

## 下一条关键路径

1. 立即确认报名审核状态、项目统筹、算法/数据、QA、材料和赛事联络负责人。
2. 按 `docs/algorithm-data/TWO_DAY_ROUTE_GATE.md` 完成 48 小时门禁；当前证据暂定推荐 PPE/违章路线，7 月 19 日前由团队正式冻结。
3. 8 月 9 日前接入首个真实非 mock baseline。
4. 用已实现的离线评分核、development runner、unsigned evidence bundle、本地一次性 registry 和 Ed25519 controlled-local verifier 接真实 baseline；再以独立低权限身份/隔离节点补禁网、QA 私有标签隔离、可信 broker、私钥托管/可信时间及数据库/API `EvaluationRun`。本地 registry 和签名包只证明单机状态、字节完整性与受信 key 签名，不能授权正式执行。禁止把本地开发运行结果或演示页面数值填成正式指标。
5. 用现有严格合同和默认关闭的 `remote_http` 桥接真实目标算法；在已有 Alembic/psycopg 3
   基础上，把数据库轮询 worker 放到真实 PostgreSQL 做 API/Worker E2E、多进程压力和故障测试，
   再扩展对象存储、权限与签名，不让基础设施扩展阻塞真实算法和评估。

详细任务见 `docs/PROJECT_PLAN.md`，算法/数据证据与路线门禁见 `docs/algorithm-data/README.md`，开发运行器边界见 `docs/DEVELOPMENT_EVALUATION_RUNNER.md`，unsigned 证据包合同见 `docs/DEVELOPMENT_EVIDENCE_BUNDLE_DESIGN.md`，一次性状态机见 `docs/HOLDOUT_REGISTRY.md`，签名包见 `docs/CONTROLLED_SIGNED_EVIDENCE.md`，算力/pilot 计划见 `docs/algorithm-data/PPE_BASELINE_COMPUTE_PLAN_2026-07-10.md`，Readiness 0 见 `docs/algorithm-data/ALGORITHM_READINESS_0.md`。当前 worker 数据合同与数据库 head 以 `docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md` 为准，本地可观测性状态以 `docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md` 为准，依赖/构建复现状态以 `docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md` 为准；Alpha11/Alpha12/Alpha14 分别保留租约、迁移基线和聚合 UI 的历史工程证据，算法门禁仍以 `docs/STAGE2_ALPHA10_ALGORITHM_READINESS.md` 为准；`docs/STATUS_2026-07-10.md`、`docs/FINAL_VERIFICATION.md` 仅保留早期阶段历史快照。
