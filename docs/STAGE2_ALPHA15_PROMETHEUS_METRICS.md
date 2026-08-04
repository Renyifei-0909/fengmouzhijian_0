# 第二阶段 Alpha15：Worker 低基数 Prometheus 指标导出

日期：2026-07-28  
状态：`engineering candidate / SQLite verified / Prometheus and PostgreSQL unverified`

> 本文测试数与依赖状态是 Alpha15 历史快照。当前计数、通用依赖锁、漏洞升级和构建证据以
> [`STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md`](./STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md)
> 为准；Alpha15 的指标合同与外部监控边界没有被 Alpha16 改写。

## 1. 结论

Alpha15 在 Alpha14 的鉴权调度聚合快照之上，增加了 Prometheus text format 0.0.4 导出端点：

`GET /api/v1/operations/verification-dispatch/metrics`

端点接受已配置的 operator、reviewer 或 auditor `X-API-Key`，返回 UTF-8、LF 结尾且带最终换行的
`text/plain; version=0.0.4; charset=utf-8`。所有标签都来自固定枚举；任务 ID、Worker ID/引用、
项目/证据 ID、任意数据库状态、错误原文、告警说明和 API Key 均不会成为标签或样本内容。

这完成的是“可抓取的指标导出面”，不是 Prometheus Server、Alertmanager、Grafana、远程存储、
告警投递、值班流程、TLS、容量基线或 uptime SLA。当前环境没有安装或运行 Prometheus，
本文抓取配置和 PromQL 只是待部署时验证的配置草案。

## 2. HTTP 与鉴权合同

| 项目 | 合同 |
|---|---|
| 方法与路径 | `GET /api/v1/operations/verification-dispatch/metrics` |
| 认证 | `X-API-Key`；operator/reviewer/auditor 均可读 |
| 无 Key/错误 Key | `401` |
| Content-Type | `text/plain; version=0.0.4; charset=utf-8` |
| 缓存 | `Cache-Control: private, no-store, max-age=0`、`Pragma: no-cache` |
| 内容嗅探 | `X-Content-Type-Options: nosniff` |
| 时间基准 | 聚合值使用数据库 `CURRENT_TIMESTAMP`；采集耗时使用 API 进程单调时钟 |
| 空集合 | 计数固定输出 0；“最老排队/心跳年龄”在无对象时不输出样本，不伪造 0 或 NaN |

本地手工读取示例：

```bash
curl -fsS \
  -H "X-API-Key: ${FENGMOU_AUDITOR_API_KEY}" \
  http://127.0.0.1:8000/api/v1/operations/verification-dispatch/metrics
```

## 3. 指标合同

本轮所有指标都是 `gauge`。即使某些值是数据库历史行总数，也不标成 `counter`：数据库恢复、
快照回滚或管理员替换可以让抓取值倒退，进程内 counter 会对这种事实作出错误承诺。

| 指标 | 固定标签 | 含义 |
|---|---|---|
| `fengmou_verification_operations_info` | `execution_mode=inline|external` | 当前执行模式，值固定为 1 |
| `fengmou_verification_operations_snapshot_timestamp_seconds` | 无 | 本次聚合使用的数据库时间 |
| `fengmou_verification_operations_collection_duration_seconds` | 无 | API 本地收集本次快照的耗时 |
| `fengmou_verification_operations_status` | `status=healthy|attention|incident` | 当前状态 one-hot gauge |
| `fengmou_verification_jobs` | `status=queued|running|needs_review|sealing|approved|rejected|failed|other` | 持久化任务数 |
| `fengmou_verification_dispatch_leases` | `state=stored|active|expired_running` | 租约行、活跃租约和过期 running 租约 |
| `fengmou_verification_queue_unclaimed_jobs` | 无 | 未被领取的 queued 任务数 |
| `fengmou_verification_queue_over_warning_jobs` | 无 | 超过排队告警阈值的 queued 任务数 |
| `fengmou_verification_dead_letter_jobs` | 无 | 已标记死信的任务数 |
| `fengmou_verification_queue_oldest_age_seconds` | 无 | 最老 queued 任务年龄；空队列时无样本 |
| `fengmou_verification_active_lease_oldest_heartbeat_age_seconds` | 无 | 最老活跃租约心跳年龄；无活跃租约时无样本 |
| `fengmou_verification_attempts` | `state=stored|open` | attempt 总数和未终结数 |
| `fengmou_verification_attempt_outcomes` | `window=all|recent`、`disposition=committed_success|committed_failure|lease_expired|lease_lost|write_fenced` | 固定处置类型的累计/观察窗内数量 |
| `fengmou_verification_recent_lease_instability` | 无 | recent 窗口内 expiry/loss/fenced write 总数 |
| `fengmou_verification_integrity_issues` | `component=dispatch|attempt` | 当前完整性矛盾数 |
| `fengmou_verification_alerts` | `code=INTEGRITY_INCIDENT|DEAD_LETTER_PRESENT|QUEUE_WAIT_EXCEEDED|RECENT_LEASE_INSTABILITY` | 固定机器码的当前告警数量 |
| `fengmou_verification_queue_warning_threshold_seconds` | 无 | 当前排队告警阈值 |
| `fengmou_verification_observability_window_seconds` | 无 | outcome recent 观察窗 |
| `fengmou_verification_lease_duration_seconds` | 无 | 当前 lease 时长 |
| `fengmou_verification_heartbeat_interval_seconds` | 无 | 当前 heartbeat 间隔 |

`all` 表示当前数据库中保存的 outcome 行，并不等于不可丢失的全局生命周期累计量；
`recent` 使用数据库时间与已配置观察窗。端点不输出每次抓取的显式样本时间戳，由抓取系统记录。

## 4. 基数、隐私与 fail-closed

- 指标命名使用 `fengmou_verification` 前缀，时间统一使用秒。
- 标签集合由代码常量和响应 schema 同时约束；没有按项目、任务、Worker 或错误消息切分。
- 固定的任务状态、attempt disposition 和告警 code 即使当前值为 0 也会输出，便于规则稳定。
- 数据库若出现不受支持的任务状态，JSON 聚合只计入 `other`，指标也只输出
  `status="other"`；原始状态不会被传播。
- 不受支持的任务状态或 attempt disposition 同时属于完整性问题，因此快照和指标进入
  `incident`，`/readyz` 返回 503。测试已使用形似私有标识的异常值验证原文、任务 ID 和
  attempt ID 均不泄露；异常 disposition 不会进入固定 outcome 序列。
- 指标渲染拒绝非有限采集耗时、负采集耗时和任务总数/分组数不一致的内部快照。

这只是应用层最小暴露原则。当前 `X-API-Key` 仍是共享密钥，不具备正式身份、细粒度 RBAC、
密钥轮换审计或 mTLS；正式环境必须由密钥管理和网络访问控制共同保护端点。

## 5. Prometheus 抓取草案

Prometheus 当前配置文档允许在 scrape HTTP 配置中设置自定义 `http_headers`，并从文件读取
header 值。以下示例避免把 auditor key 直接写入主配置：

```yaml
scrape_configs:
  - job_name: fengmou-verification
    metrics_path: /api/v1/operations/verification-dispatch/metrics
    scheme: http
    static_configs:
      - targets:
          - backend:8000
    http_headers:
      X-API-Key:
        files:
          - /run/secrets/fengmou_auditor_api_key
```

该文件应只包含 Key 本身，并使用最小文件权限。生产应改用内部 HTTPS，并在选定的 Prometheus
版本上用 `promtool check config` 和一次真实抓取验证；本轮没有执行这两项。

候选 PromQL 观察式：

```promql
fengmou_verification_operations_status{status="incident"} == 1
fengmou_verification_queue_over_warning_jobs > 0
fengmou_verification_dead_letter_jobs > 0
fengmou_verification_recent_lease_instability > 0
```

这些只是查询表达式，不是已经启用的告警规则。阈值、`for` 持续时间、严重度、抑制、路由和
值班责任必须在 PostgreSQL 压测取得容量基线后单独评审。

## 6. 性能与扩展边界

每次抓取都会执行 Alpha14 聚合及 dispatch/attempt 完整性扫描，属于多查询、非原子数据库
快照。当前只在小型 SQLite 测试库验证，没有基准测试、缓存、采集超时预算、真实数据规模或
并发抓取证据。它不能证明生产抓取开销可接受，也不能把不同 SQL 时点伪装成单事务一致视图。

下一阶段应在真实 PostgreSQL 上完成 `0001 → 0002`、API/多 Worker、reaper/fencing 故障注入、
目标数据量基准和并发抓取，再决定索引、扫描频率、缓存/预聚合与告警阈值。

## 7. 验证证据

### 7.1 后端

- 配置、观测、指标与 OpenAPI 针对性组合：`45 passed`；
- 指标渲染模块覆盖率：`100%`；观测聚合模块：`98.46%`；
- migration、Worker CLI/lease/recovery/observability/metrics/OpenAPI 组合：`60 passed`；
- 明确排除 9 个 POSIX-only 评测/Readiness 模块及 10 个本机无法构造的文件系统攻击用例：
  `259 passed, 27 skipped, 10 deselected`；
- 原生 Windows 全量：`153 failed, 308 passed, 31 skipped`，诊断 coverage `70.15%`；
- 153 个失败分类未变化：129 个 Evaluation POSIX secure-open、14 个 Algorithm Readiness
  POSIX secure-open、10 个 Windows 无法构造或语义不同的 symlink/FIFO/文件替换用例；
- `compileall`、`pip check` 和 OpenAPI 漂移检查通过；
- 隔离临时 SQLite/存储上的真实 Uvicorn TCP smoke：无 Key 返回 401，有效 auditor key 返回
  200，Content-Type 精确匹配 text 0.0.4，正文 6,021 bytes、以 LF 结束且不含测试 Key/标识字段；
- wheel `fengmou_backend-0.2.0-py3-none-any.whl` 成功构建，并核对包含
  `app/services/metrics.py`、`app/services/observability.py` 与 `20260728_0002` migration。

Windows 全量失败不是通过证据，70.15% 也不是 90% 门禁证据。当前机器虽有 `wsl.exe`，
但 WSL 功能/发行版未安装，也没有 Docker、Podman 或真实 PostgreSQL，因此仍必须在 Linux
环境重跑全量 `-W error --cov-fail-under=90`。

### 7.2 前端与依赖

- TypeScript、Vite 7.3.6、116 modules 构建通过；
- 单文件产物 `557.80 kB`，gzip `149.30 kB`；
- `npm audit --audit-level=moderate`：0 vulnerabilities；
- 本轮只把聚合状态的 TypeScript 键收紧为固定状态加 `other`，没有新增指标 UI；
- React Router 保持 8.3.0，未重新引入 `react-router-dom` 或 RSC。

### 7.3 OpenAPI

- 文件：`docs/openapi-v1.json`
- 大小：113,391 bytes
- SHA-256：
  `8a3d6c91bbb1c82773e5781cd0ad02f23dfe6a706cee3be4bb90fe3753a7cc42`
- 契约包含 metrics 路径、`text/plain` 200 响应、API Key 安全要求，以及聚合状态键和
  outcome 键的固定枚举/非负值约束。

### 7.4 浏览器边界

Alpha14 已对 JSON 聚合和 UI 做过真实浏览器故障注入。本轮新增的是给抓取器使用的文本端点，
没有新增浏览器 UI；因此使用 FastAPI/TestClient 覆盖异常数据，并另起真实 Uvicorn/TCP 验证
认证、header、内容类型、最终换行和隐私边界，没有把重复浏览器操作写成新增生产证据。

## 8. 上游依据

- [Prometheus exposition formats](https://prometheus.io/docs/instrumenting/exposition_formats/)：
  text 0.0.4 的 UTF-8、LF、Content-Type、最终换行、HELP/TYPE 与唯一序列要求。
- [Prometheus metric and label naming](https://prometheus.io/docs/practices/naming/)：
  应用前缀、基础单位和一致量纲建议。
- [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)：
  scrape HTTP 自定义 header 与文件取值合同。

## 9. 关键文件

- 指标渲染：`backend/app/services/metrics.py`
- 聚合与边界：`backend/app/services/observability.py`
- 完整性扫描：`backend/app/services/analysis.py`
- API：`backend/app/api/router.py`
- 响应 schema：`backend/app/schemas.py`
- 指标测试：`backend/tests/test_worker_metrics.py`
- 聚合/异常状态测试：`backend/tests/test_worker_observability.py`
- OpenAPI 合同测试：`backend/tests/test_openapi_contract.py`
- 契约制品：`docs/openapi-v1.json`
