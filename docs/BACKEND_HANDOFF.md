# 后端实现与联调交接

## 1. 当前架构

```text
React 联调页
    |
FastAPI / OpenAPI
    |
SQLite（开发事实库/任务队列） ---- 本地证据存储
    |                         |
任务状态机 + lease       原件 / 报告 / ZIP
    |
追加式 attempt/outcome
    |                         |
Analyzer Adapter        manifest + Merkle
    |                         |
人工复核 -------------- 本地链式哈希台账
```

当前选用 SQLite、本地文件和数据库轮询任务是为了尽快得到可运行闭环，不是生产架构承诺。`inline` 与独立 `external` worker 都走同一租约/fencing 服务；SQLite external 仅限本机单 worker 演示。数据库已有 Alembic 基线、`20260728_0002` attempt 增量、启动门禁、不含标识的调度聚合诊断和低基数 Prometheus text 导出。Alpha17 提供仅针对 `fengmou_acceptance` 回环库的安全验收 harness：20 项无服务器单测，并已在 **Windows 便携 PostgreSQL 17.10** 上实跑通过（见 `docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md` 与 `output/postgres-acceptance-run.json`）。Docker Compose 路径与外部监控栈仍未部署。适配层已经把后续 PostGIS、S3/MinIO、分布式 worker 和真实算法隔离出来。

API 与 Worker 都通过 `FENGMOU_DATABASE_SCHEMA_MODE` 管理结构：本地开发可 `upgrade`，部署实例
应在独立 release job 迁移后使用 `verify`。未版本化旧库不会自动 stamp；先备份，再执行
`python -m app.schema adopt-legacy`。完整合同见
`docs/STAGE2_ALPHA12_DATABASE_MIGRATIONS.md`；当前 attempt/outcome 增量见
`docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`。

### 1.1 依赖、安装与构建

后端不再用 `pip install -e '.[dev]'` 重新解析传递依赖。uv 固定为 0.11.32，
`backend/uv.lock` 保存运行时/dev extra 的通用 marker 图和 PyPI 制品哈希；标准安装为：

```bash
cd backend
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
```

`pyproject.toml` 设置 `python-preference = "only-system"`，命令再显式禁止自动下载 Python；
机器必须预先提供满足 `>=3.11` 的解释器。更新锁必须使用显式 `uv lock` 并审阅差异，常规
安装/检查只使用 `--locked`。

构建后端固定为 setuptools 83.0.0，仓库同时保存其 wheel/sdist SHA-256：

```bash
uv build --build-constraints build-constraints.txt --require-hashes --no-python-downloads
```

漏洞点时检查为：

```bash
uv audit --locked --preview-features audit-command --no-python-downloads
```

uv bootstrap 文件覆盖该版本当前 19 个 PyPI 制品哈希；Windows amd64 的哈希安装已验证。
后端 Dockerfile 也改为固定 Python 3.12.13 多架构 manifest digest、哈希引导 uv、locked
runtime sync 和 wheel 安装，但当前没有 Docker，尚未实际 build/run。当前
Windows/Python 3.12 clean sync、哈希约束构建和 wheel runtime smoke 已完成；Linux只有
marker 解析漏洞审计。锁和哈希不提供发布者签名、SBOM/SLSA、cold-cache offline 或操作系统
依赖复现。完整证据见
`docs/STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`。

## 2. 核心状态机

```text
queued -> running -> needs_review -> sealing -> approved
                    |                 |           |
                    +-> rejected      |           +-> completed report/proof
                                      +-> requested -> staged -> files -> ledger
                                      +-> manual_attention（完整性事故）

queued/running -> failed -> retry -> queued
                           +-> internal dead_letter（对外仍为 failed）

finding candidate -> pending_triage -> open -> remediation_in_progress
                                      |                 |
                                      +-> dismissed     +-> verification_pending
                                                           | resolved + sealed proof -> closed
                                                           + not_resolved/rejected -> remediation_in_progress
```

- `failed`表示系统/算法失败，不表示“没有发现问题”。
- 所有当前适配器输出都进入人工复核。
- `approve`用条件更新取得封存意图，冻结审核人、报告输入与制品 ID，再由持久化 SealOperation 发布报告、证据包和 ledger；故障可由同一 API 或启动恢复继续。
- demo/stub 被批准后仍保持 `evidence_grade=false`。
- analyzer finding 只是候选 observation；system notice 和 info finding 不物化案件，只有 reviewer 确认的 `scope=operational` 案件计入运营告警，`scope=demo` 永久排除在运营统计和模型指标之外。
- 整改复验必须绑定原案件项目与设计基线。Attempt 与 case 均使用条件更新/CAS；并发上传只有一个请求可以绑定，失败事务会回滚新 job/evidence 并删除上传文件。
- 关闭不是改写源报告：新复验产生新 report/proof，case 仅引用 closure proof。`readyz` 重新核对 case、attempt、job、report、proof、ZIP 中 remediation 快照和原 finding 快照。

### 2.1 Worker 租约、启动恢复与写回隔离

- `verification_job_leases` 为每个任务保存 owner、单调 generation、尝试次数、heartbeat、租约截止和内部死信；数据库 `CURRENT_TIMESTAMP` 是时间权威。
- worker 在同一事务内领取 lease、执行 `queued -> running` 并冻结 `verification_attempts`；heartbeat、成功和失败都必须匹配 owner + generation 且租约未过期。
- 应用启动只回收遗留无租约或已过期的 `running`；有效租约不会因另一个 API 实例启动而被重排队。过期回收与 heartbeat 使用条件更新竞争，只能一方成功。
- 成功 outcome、结果、Finding Case 物化、`needs_review` 和租约释放同事务提交；失败 outcome、任务错误/死信和租约释放也同事务提交。旧 worker 在新 generation 产生后返回，只追加 `lease_lost/write_fenced` 或记录已有终态 winner，不能覆盖新结果或失败状态。
- 每个 attempt 至多一个 `verification_attempt_outcomes`；数据库触发器拒绝两张历史表的普通 UPDATE/DELETE，readiness 重算结果摘要并核对输入、代际、预算和时间顺序。
- 尝试预算耗尽后保持对外兼容的 `failed`，内部 `dispatch.state=dead_letter`，启动恢复与普通 retry 均不会复活。
- 仍是 **at-least-once 外部调用**，不是 exactly-once 推理。如果远端已收到请求而 worker 在结果提交前崩溃，接管后可能重发；远程服务必须按稳定 `Idempotency-Key` 幂等。
- API 只公开 SHA-256 `worker_ref` 与 outcome 摘要，不公开原始 worker ID 或冻结的结果副本；该假名不是匿名化保证。
- `external` 已通过真实独立 Python 进程 smoke；SQLite 有进程锁，仅允许一个本机 worker。生产多进程仍需真实 PostgreSQL 验收、压力/故障测试，并在有 contention 证据后再评估 `SKIP LOCKED`。租约历史合同见 `docs/STAGE2_ALPHA11_WORKER_LEASES.md`，当前数据合同见 `docs/STAGE2_ALPHA13_VERIFICATION_ATTEMPTS.md`；PostgreSQL 验收入口见 `docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`（static/unexecuted）。

### 2.2 Worker 调度聚合诊断

- `GET /api/v1/operations/verification-dispatch` 接受 operator/reviewer/auditor key，返回数据库时钟、
  job/lease/dead-letter/attempt outcome 数量、观察窗与完整性问题；不返回任务、Worker、项目或证据标识。
- `QUEUE_WAIT_EXCEEDED` 的 count 只统计超过阈值的 queued 任务；死信和近期
  `lease_expired/lease_lost/write_fenced` 是 warning/`attention`，不会单独使 readiness 失败。
- dispatch/attempt 完整性矛盾是 `incident`；端点保持 200 供诊断，既有 `/readyz` 同时 fail closed。
- 默认排队告警为 60 秒，近期观察窗为 900 秒，可分别通过
  `FENGMOU_VERIFICATION_QUEUE_WARNING_SECONDS` 和
  `FENGMOU_VERIFICATION_OBSERVABILITY_WINDOW_SECONDS` 配置。
- 这是按请求执行的多查询数据库聚合，不是单事务不可分割快照、时序存储、外部告警、SLA 或
  生产容量证据。当前完整性扫描只验证了本地小规模，大数据量前必须评估查询计划并决定增量巡检/预聚合。
- 实现、告警机器码、真实浏览器验证和边界见
  `docs/STAGE2_ALPHA14_WORKER_OBSERVABILITY.md`。

### 2.3 Worker Prometheus 指标导出

- `GET /api/v1/operations/verification-dispatch/metrics` 接受同一 operator/reviewer/auditor key，
  返回 `text/plain; version=0.0.4; charset=utf-8`。
- 指标只使用固定的执行模式、任务状态、lease/attempt 状态、outcome disposition、完整性组件和
  告警机器码标签；不包含任务/Worker/项目/证据 ID、任意状态、错误原文或告警说明。
- 未知数据库任务状态收敛为 `other`；未知任务状态或 attempt disposition 都会触发完整性事故、
  指标 `status="incident"` 和 `/readyz` 503，原始值不会成为高基数标签。
- 当前所有值都是抓取时 gauge；数据库历史行数也不是 counter，因为恢复或管理员替换可能让快照倒退。
- 尚未部署 Prometheus Server、Alertmanager、Grafana、远程存储、TLS 或值班路由。每次抓取仍会
  执行完整聚合/扫描，必须在真实 PostgreSQL 数据规模上基准测试。指标清单、密钥文件抓取草案和
  OpenAPI 摘要见 `docs/STAGE2_ALPHA15_PROMETHEUS_METRICS.md`。
- Alpha17 验收 harness 使用与 renderer 一致的 required family 集合（首个 family 为
  `fengmou_verification_operations_info`）做合同检查；**不是**对生产抓取栈的验证。

### 2.4 PostgreSQL 验收 harness（Alpha17）

- 入口：`backend/scripts/postgres_acceptance.py`，**只**读取环境变量
  `FENGMOU_POSTGRES_ACCEPTANCE_URL`（禁止落入应用默认库或 argv 明文依赖）。
- 目标必须是 `postgresql+psycopg`、库名 `fengmou_acceptance`、回环主机、显式用户名密码，且
  不得带外部 query。
- 每次运行创建严格命名的临时 schema，升级到 head，external API + 多 worker 竞争、fencing、
  append-only 拒绝与 metrics/readiness 检查后删除自建 schema。
- Compose 候选：`compose.postgres-acceptance.yaml`（digest 固定、`127.0.0.1:55432`、tmpfs）。
- 无服务器单测：`tests/test_postgres_acceptance.py`；Makefile：
  `postgres-acceptance-static|up|run|down`（缺 URL/docker → exit 2）。
- 2026-07-28 Windows portable 17.10 实跑：返回码 0，约 3 s，8 jobs/8 workers，fencing 与
  append-only 通过，schema cleanup 确认。Compose 路径仍未跑。详见
  `docs/STAGE2_ALPHA17_POSTGRESQL_ACCEPTANCE.md`。
- 同日另增 `scripts/postgres_contention_observe.py`：在专用库上观测多 worker 领取空转；
  16×8 短波实测 `idle_while_queue_nonempty=0`，**明确不实现 SKIP LOCKED**。缺 URL 时 exit 2。
- Windows 子集复现入口：仓库根 `scripts/run_windows_backend_subset.ps1`。

## 3. 主要接口

除 `/healthz`、`/readyz`、`/meta` 外，业务接口均要求 `X-API-Key`。当前最小角色模型为：

- `operator`：创建项目/基线/传感器事件、上传媒体、重试任务；
- `reviewer`：批准或驳回待复核任务；
- `auditor`：只读核验和审计。当前三类有效 key 均可全局读取业务数据，只有写操作做了角色分离；尚未实现用户、项目或资源所有权隔离。

前端联调页分别保存 operator/reviewer 的本地演示 key。Vite 环境变量会进入浏览器构建产物，因此它们不是生产密钥方案。

视频上传采用 fail-closed 校验：扩展名必须与允许的 MIME 显式匹配，服务端再按扩展名固化规范媒体类型；所有视频都必须通过 `ffprobe`，不能靠客户端把 `.mp4` 声明成图片绕过。运行环境没有 `ffprobe` 时视频返回 422；JPG/PNG 图片不需要 `ffprobe`。Docker 镜像已安装 ffmpeg，本地开发先执行 `ffprobe -version` 检查。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/healthz`、`/readyz` | 存活/就绪 |
| GET | `/api/v1/meta` | 能力和真实性边界 |
| GET | `/api/v1/operations/verification-dispatch` | 鉴权的 Worker 调度聚合诊断；不返回任务/Worker 标识 |
| GET | `/api/v1/operations/verification-dispatch/metrics` | 鉴权的低基数 Prometheus text 0.0.4 抓取面 |
| GET | `/api/v1/dashboard/summary` | 真实数据库汇总 |
| POST/GET | `/api/v1/projects` | 项目创建/列表 |
| GET | `/api/v1/projects/{id}/overview` | 项目级进度、任务、报告和存证记录聚合；存证有效性仍须显式核验 |
| POST/GET | `/api/v1/projects/{id}/baselines` | 工点/工序/设计基线 |
| POST/GET | `/api/v1/sensor-events` | 第二来源的结构化传感器事件 |
| POST | `/api/v1/verifications` | 上传媒体并创建任务 |
| GET | `/api/v1/verifications/{id}` | 任务、原件、结果、追加式 worker 尝试历史、报告和证据包 |
| GET | `/api/v1/evidence-assets/{id}/content` | 鉴权读取已登记原始图片/视频；支持一个字节区间 |
| POST | `/api/v1/verifications/{id}/retry` | 显式重试失败任务 |
| POST | `/api/v1/verifications/{id}/review` | 人工批准/驳回 |
| GET | `/api/v1/finding-cases/summary` | 运营/demo 分离的案件统计 |
| GET | `/api/v1/finding-cases`、`/{id}` | 持久化候选、案件、Attempt、命令历史和关闭证明 |
| POST | `/api/v1/finding-cases/{id}/triage` | reviewer 确认或排除候选 |
| POST | `/api/v1/finding-cases/{id}/start-remediation` | operator/reviewer 指派并启动整改 |
| POST | `/api/v1/finding-cases/{id}/remediation-attempts` | 创建一次幂等整改提交；复验上传时把 ID 传入 `/verifications` |
| GET | `/api/v1/projects/{id}/progress` | 基于已批准基线的进度代理 |
| GET | `/api/v1/reports/{id}/download` | JSON/HTML 报告 |
| GET | `/api/v1/proofs?fingerprint=` | 严格档案/摘要查找 |
| GET | `/api/v1/proofs/{id}/verify` | 服务端完整性核验 |
| GET | `/api/v1/proofs/{id}/archive` | 下载证据 ZIP |
| GET | `/api/v1/audit-events` | 关键动作时间线 |

完整字段和请求样例以版本化契约 `docs/openapi-v1.json` 为准；服务运行时也可在
`http://127.0.0.1:8000/docs` 交互查看，在 `http://127.0.0.1:8000/openapi.json`
读取同源 schema。

### 3.1 原始证据安全回看

`GET /api/v1/evidence-assets/{id}/content` 只接收不透明记录 ID，并允许当前三个已配置
角色读取。服务端不直接信任数据库路径、文件名或 MIME：候选文件必须是
`storage/evidence/<stored_name>` 的直接普通文件，不能是软链接或多硬链接；扩展名映射、
magic bytes、登记大小和完整 SHA-256 必须全部一致。

完整性核验和响应读取绑定在同一个以 `O_NOFOLLOW` 打开的文件描述符上。服务端先从该
描述符读完整文件并核验，通过后才从同一描述符返回完整内容或一个 `bytes` 区间，避免
“检查路径后换包再重新打开”的 TOCTOU 窗口。成功响应固定使用规范 MIME，并包含
`Cache-Control: private, no-store, max-age=0`、`Pragma: no-cache`、
`X-Content-Type-Options: nosniff` 和 `Accept-Ranges: bytes`。

- 未鉴权为 `401`，记录不存在为 `404`；
- 路径、文件类型、链接、MIME、magic bytes、大小或摘要冲突统一为通用 `409`；
- 记录存在但文件已消失为通用 `410`；
- 空、畸形、多区间、超长或不可满足 Range 为 `416`，并返回
  `Content-Range: bytes */<total>`。

当前 API key 是全局角色密钥，不是项目成员或租户 ACL。每次 Range 都先全量计算摘要，
这是完整性优先的 MVP 选择；大文件生产化仍需限流、并发约束和不会降低校验强度的安全
对象存储方案。专项威胁模型和验收矩阵见 `docs/EVIDENCE_PREVIEW_QA.md`。

### 3.2 重新生成和校验 OpenAPI 契约

路由、请求/响应模型、认证依赖或 API 版本变化后，从项目根目录运行：

```bash
cd backend
python scripts/export_openapi.py
python scripts/export_openapi.py --check
```

导出器采用固定键排序、UTF-8 和稳定缩进；相同代码应生成逐字节一致的 JSON。
`--check` 不写文件，只在 `docs/openapi-v1.json` 缺失或与当前 FastAPI schema
不一致时返回非零状态。提交 API 变更时必须同时审阅并提交该 JSON 的差异。
契约测试还会锁定核心业务路径、`X-API-Key` 安全方案、上传/复核请求和报告/存证
关键 schema：

```bash
cd backend
python -m pytest tests/test_openapi_contract.py
```

导出过程只读取代码 schema，不启动 lifespan、不建表，也不会把任何配置密钥写入
JSON。OpenAPI 只能表达统一的 API key 机制，不能表达本项目的 operator、reviewer、
auditor 角色差异；角色权限仍以本节上方说明和后端依赖实现为准。

项目根目录的统一静态契约检查为：

```bash
make backend-contracts
```

该目标同时检查 `docs/openapi-v1.json`、
`docs/remote-analyzer-request-v1.schema.json` 和
`docs/remote-analyzer-response-v1.schema.json` 是否与当前代码一致；只校验、不覆写制品。

## 4. 算法适配器

位置：`backend/app/services/analyzers/`

每个适配器实现：

```python
class Analyzer(Protocol):
    name: str
    version: str

    def analyze(self, evidence: EvidenceAsset, baseline: DesignBaseline) -> dict: ...
```

返回值先经过服务端统一的 `AnalyzerResult` 1.0 严格合同，必须包含：

- `schema_version`；
- `analysis_mode`、`evidence_grade`；
- adapter 名称/版本和 provenance；
- 原件/基线摘要；
- observations、alignment、findings；
- confidence 或明确的 `null`；
- accuracy claim 或明确的 `null`；
- recommended action。

服务端还会校验 adapter 名称/版本、`analysis_mode`、原件与基线摘要、
synthetic 标记是否与任务绑定信息一致。未登记的顶层字段、任何非 `null` 的
`accuracy_claim` 或未经服务端评测门禁产生的 `evidence_grade=true` 都会使任务
fail closed，不会污染报告或存证。

任务调用任何 adapter 前都会重新校验原件文件存在性、字节数、SHA-256 和设计基线规范摘要，并在 analyzer 调用结束前保持该已验证 fd 打开。`remote_http` multipart 直接读取该 fd，不重新信任或打开数据库路径；调用成功或失败后均由编排层关闭。结果合同在 Pydantic 规范化前后各执行一次递归 guard，领域扩展 JSON 也不能嵌套受保护声明、NaN/Infinity 或无效 Unicode。

当前适配器：

- `stub-v0.1`：真实接收文件、摘要、任务和报告链路，但不做视觉识别。
- `fixture-v0.1`：基于输入摘要生成确定性合成结果，只为联调；默认关闭。
- `remote_http`：与外部算法服务联调的 HTTP 桥接；默认关闭，当前没有配置或接入真实目标算法。

另提供可独立启动的 `backend/app/reference_analyzer.py`。它严格实现远程合同并已通过真实
HTTP 全链 smoke，但默认是空输出 STUB；用途是让算法同学先验证接入方式，不计为真实目标
算法。启动、替换 predictor 和完整联调命令见 `docs/REFERENCE_ANALYZER_SERVICE.md`。

当前交付分类由服务端统一决定：

| 结果类型 | 报告 `status` | 证据包 `purpose` | 含义 |
|---|---|---|---|
| `demo_fixture` | `reviewed_demo` | `demo` | 合成联调输出 |
| `stub` | `reviewed_placeholder` | `workflow` | 只证明工作流可运行 |
| `remote_http` 且未评测 | `reviewed_non_evaluated` | `review` | 远程单样本输出，须人工复核 |
| 经服务端评测门禁批准的输出 | `final` | `validation` | 当前未实现该批准路径 |

`remote_http` 使用固定 endpoint、独立 Bearer 密钥和锁定的模型
name/version/artifact SHA-256；任务版本是 endpoint、桥/合同版本和完整模型身份的 64 位配置指纹，配置变化后旧任务不能静默重试。发送前还会校验外发字段白名单和 64 KiB 请求上限；客户端不读取环境代理、不跟随重定向，并限制上传/响应体积、核对 request ID 与模型身份。上游响应在任意层级都不能注入 `evidence_grade`、`accuracy_claim`，后端始终强制人工复核。具体请求、响应和失败语义见
`docs/REMOTE_ANALYZER_CONTRACT.md` 及两份版本化 schema。

接真实模型时必须在不放宽上述合同的前提下配置并验证远程服务，固定模型/代码/配置摘要；
不得修改 stub、把 fixture 改名或把桥接层本身冒充为真实算法成果。启用远程外传前还必须完成
`docs/DATA_AND_PRIVACY.md` 要求的授权审查。

## 5. 报告与证据包

证据 ZIP 包含：

```text
evidence/<original-file>
analysis/result.json
design/baseline.json
review/human-review.json
sensors/events.json
report/report.json
report/report.html
manifest.json
```

`manifest.json`记录每个成员的逻辑路径、字节数和 SHA-256，并给出确定性的 Merkle Root。外部 `proof-ledger.jsonl` 按 `previous_record_hash -> record_hash` 串联记录，防止静默重排。

Alpha7 不再直接 append ledger：本机使用进程间文件锁验证全链，把完整 JSONL 写入同盘临时文件并
`fsync + os.replace`。数据库、报告文件、ZIP 和 ledger 仍没有共同 commit point，因此通过
`SealOperation` Saga 保留中间状态并恢复，而不是宣称跨介质原子事务。`readyz` 会重新扫描全部
新旧报告、证明、ledger 和孤儿制品；发现缺失、摘要冲突或 manual attention 时返回 503。

当前存证能证明：

- 当前 ZIP 是否与封存摘要一致；
- manifest 是否变化；
- 任一成员是否缺失或变化；
- Merkle Root 是否一致；
- 本地 ledger 顺序是否一致。

当前不能证明：

- 谁在法律意义上签发；
- 真实可信时间；
- 数据库管理员无法同时重写文件和本地账本；
- 已锚定公链/联盟链。

## 6. 前端接入

- API client：`frontend/src/lib/api.ts`
- 初版联调页：`frontend/src/pages/BackendWorkflowPage.tsx`
- 路由：`/backend-workflow`
- Vite 本地代理：`/api -> http://127.0.0.1:8000`

`docs/openapi-v1.json` 是前后端协作的机器可读边界。当前 `api.ts` 是手写客户端；
若改用类型生成器，应先把所选生成器及版本固定在 `frontend/package.json`，再以该文件
作为唯一输入。例如安装并固定 `openapi-typescript` 后可运行：

```bash
cd frontend
npx openapi-typescript ../docs/openapi-v1.json --output src/generated/openapi.d.ts
```

生成类型后仍须保留以下传输约定：

- `POST /api/v1/verifications` 使用 `multipart/form-data`；`metadata` 是 JSON 编码的
  字符串字段，媒体本体放在 `file` 字段；
- 除 health/ready/meta 外均发送 `X-API-Key`，复核写操作必须使用 reviewer key；
- `/reports/{id}/download` 与 `/proofs/{id}/archive` 在浏览器中按 `Blob` 下载，不要
  使用 JSON 解码；FastAPI 当前对 `FileResponse` 只生成通用响应 schema；
- API 变更后先重新导出并通过契约测试，再刷新前端生成类型和实现。
- 任务详情的 `recovery.action` 是恢复 UI 的唯一事实源：`retry_analysis` 还要检查
  `retryable`，`resume_sealing` 只能由 reviewer 重放同一 review，`integrity_review` 不得自动
  重试。`retryable=true` 只在最新失败审计明确允许且 analyzer 配置未漂移时成立；未知失败分类
  必须 fail closed。整改复验恢复必须复用 Attempt 中已持久化的 resolution 和 note。

后续前端可以替换布局、表单和状态管理，但不要改变以下语义：

1. 结果只在后端任务完成后展示；
2. mock/fixture/真实模型必须显著区分；
3. 未找到档案不能回退为“可信”；
4. 失败、低置信度和待复核必须是独立状态；
5. 报告和存证按钮只有在真实制品存在时才显示成功。
6. `sealing` 不等于后台仍在正常推进；存在 `last_error` 时必须停止假进度并给出显式恢复入口。
7. 轮询临时失败必须退避；401/403 必须暂停并提示检查对应 API Key，不得无限高频重试。
8. attempt 历史必须按后端事实展示；不得从 `worker_ref` 反推或显示原始 worker ID，也不得把数据库追加式约束描述成外部 WORM/区块链。
9. 调度面板必须区分 `attention` 与 `incident`，并保留“数据库诊断快照、非 SLA/外部监控”的
   明示；不得从聚合数自行拼出任务标识或伪造实时刷新。

## 7. 生产化差距

优先顺序：

1. 在已完成的 Alembic/psycopg 3 基础上验证真实 PostgreSQL，随后评估 PostGIS；
2. S3/MinIO 分片上传和不可覆盖对象；
3. 将已实现的数据库轮询独立 worker、lease/心跳、超时接管、死信、追加式 attempt/outcome
   和本地聚合诊断迁到真实 PostgreSQL，补 `SKIP LOCKED`、多进程压力、故障注入和抓取开销基准；
   当前已有低基数 exporter，但仍需部署/验证 Prometheus、Alertmanager、仪表盘、外部告警路由与
   值班闭环。若需要抵抗数据库管理员改写，再增加外部只写存储/签名；只有需要跨系统投递时再引入
   broker/outbox；
4. 用真实身份系统/JWT、项目级 RBAC 和短期凭证替换当前本地 API key；当前 actor 已由服务端角色注入，但还没有真实用户身份；
5. 报告 PDF、版本化模板和证据帧；
6. Ed25519 签名、密钥管理和外部可信时间戳；
7. 在已实现的离线 Evaluation v0 schema/门禁/评分 CLI 上补真实冻结数据、模型执行隔离、一次性 holdout、不可变 Run、数据库/API 和 ComplianceClaim；
8. 真实 QGIS/GeoJSON 对齐、项目级告警通知、SLA/逾期升级和外部工单集成；本地 finding/整改/复验证据闭环已实现，不等于生产告警平台；
9. 真实目标算法服务、获授权数据集、冻结评测集与可复现指标。`remote_http` 和参考 STUB 已完成安全合同与联调，不表示真实算法或指标已经完成。
10. 在 Linux clean runner 执行 locked sync、全量 90% 门禁和 wheel smoke，再补 Docker
    cold start、空 cache/offline 镜像、CI matrix、SBOM/签名 provenance 与非开发者复现；
    当前通用锁只完成 Windows 工程候选。
