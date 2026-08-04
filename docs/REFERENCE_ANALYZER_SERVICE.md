# 远程算法参考服务（STUB）

`backend/app/reference_analyzer.py` 是一个可独立启动的 FastAPI 合同参考服务。它用于
验证业务后端到远程算法的鉴权、multipart、摘要、模型身份和幂等语义，不加载任何模型、
不做视觉识别、不输出物理测量，也不声明准确率。

## 启动

从 `backend/` 目录运行：

```bash
export FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN='replace-with-a-long-random-secret'
python -m uvicorn app.reference_analyzer:app --host 127.0.0.1 --port 8010
```

也可以从项目根目录执行：

```bash
export FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN='replace-with-a-long-random-secret'
make reference-analyzer-run
curl -fsS http://127.0.0.1:8010/healthz
```

完整环境变量样例在 `backend/.env.reference.example`。参考服务使用独立 Bearer，不能复用
平台 operator/reviewer/auditor 的 `X-API-Key`。

默认固定身份为：

```text
name: fengmou-reference-stub
version: stub-v0.1
artifact_sha256: 15695d7820543d1217651812af54e91e71a30d355f8a2b851734a4f2483e454e
```

该摘要绑定仓库中的
`examples/remote-analyzer-reference/reference-stub-artifact.json` 精确文件字节。这个文件是
STUB 行为声明，不是模型权重或性能证据。

将业务服务指向参考服务时，使用同一 Bearer token 和上述模型身份，并保持
`FENGMOU_REMOTE_ANALYZER_ENABLED=true`，同时必须在 `demo/test` 环境显式设置
`FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE=stub`。参考服务入口是
`http://127.0.0.1:8010/v1/analyze`；仅限本机联调，非本机环境仍应使用 HTTPS。

## 输入验证

`POST /v1/analyze` 要求：

- `Authorization: Bearer ...`；未配置服务端 token 时 fail closed 为 503；
- multipart 中恰好包含 `evidence` 和 `request` 两个文件；
- `request` 是受大小限制的 `RemoteAnalyzerRequest` 1.0 JSON；
- 合同版本、request ID、原件 SHA、基线 SHA 和 Idempotency-Key 请求头全部与 JSON 绑定；
- 请求模型 name/version/artifact SHA 与服务配置完全一致；
- 原件按块读取，扩展名、MIME、magic bytes、登记大小和完整 SHA-256 全部一致；
- 默认原件上限 100 MiB、请求 JSON 64 KiB、响应 2 MiB。

错误统一使用：

```json
{"detail":{"code":"REFERENCE_*","message":"安全且有界的错误说明"}}
```

错误不会回显 Bearer token、上传正文或内部堆栈。

## 幂等与预测器接口

`BoundedIdempotencyCache` 是进程内有界 LRU。完整规范请求和实际原件摘要共同形成
fingerprint：同一个 Idempotency-Key 和同一 fingerprint 直接重放字节完全相同的响应；
同 key 不同 fingerprint 返回 409。默认容量 256，进程重启即清空，因此它不是持久化任务
账本或分布式幂等方案。

可替换 predictor 的同步接口为：

```python
def __call__(
    request_document: RemoteAnalyzerRequest,
    evidence_stream: BinaryIO,
) -> Mapping[str, Any]: ...
```

返回值只能包含 `observations`、`alignment`、`findings`、`confidence` 和
`limitations`。服务端负责包装合同版本、request ID 和固定模型身份，并在规范化前后扫描
`evidence_grade`、`accuracy_claim`、非有限数字和非法 Unicode；额外字段或 protected claim
会 fail closed。

默认 `StubReferencePredictor` 始终返回空 observations、`not_evaluated` 和显式 STUB
limitation。替换 predictor 只提供工程接入点，不会自动构成真实模型、冻结评测或竞赛指标。

## 业务后端真实 HTTP 联调

先保持参考服务在 `8010` 运行，再在另一个终端从 `backend/` 启动隔离业务后端。下面的
SQLite 与存储路径只是本机 smoke 数据，不会覆盖默认演示库：

```bash
export FENGMOU_ENVIRONMENT=demo
export FENGMOU_DATABASE_URL=sqlite:////tmp/fengmou-reference-smoke.db
export FENGMOU_STORAGE_ROOT=/tmp/fengmou-reference-smoke-storage
export FENGMOU_MAX_UPLOAD_BYTES=10485760
export FENGMOU_OPERATOR_API_KEY=reference-operator-change-me
export FENGMOU_REVIEWER_API_KEY=reference-reviewer-change-me
export FENGMOU_AUDITOR_API_KEY=reference-auditor-change-me
export FENGMOU_REMOTE_ANALYZER_ENABLED=true
export FENGMOU_REMOTE_ANALYZER_URL=http://127.0.0.1:8010/v1/analyze
export FENGMOU_REMOTE_ANALYZER_API_KEY="$FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN"
export FENGMOU_REMOTE_ANALYZER_MODEL_NAME=fengmou-reference-stub
export FENGMOU_REMOTE_ANALYZER_MODEL_VERSION=stub-v0.1
export FENGMOU_REMOTE_ANALYZER_MODEL_SHA256=15695d7820543d1217651812af54e91e71a30d355f8a2b851734a4f2483e454e
export FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE=stub
export FENGMOU_REMOTE_ANALYZER_TIMEOUT_SECONDS=10
export FENGMOU_REMOTE_ANALYZER_MAX_UPLOAD_BYTES=8388608
export FENGMOU_REMOTE_ANALYZER_MAX_RESPONSE_BYTES=1048576
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```

第三个终端执行完整公开 API 链：

```bash
cd backend
python scripts/seed_remote_reference_demo.py \
  --base-url http://127.0.0.1:8011/api/v1
```

脚本会创建或复用匿名项目/基线，上传仓库内 H.264 合成视频，经真实 HTTP 调用参考服务，
再完成复核、结构化报告、证据包和 8 项完整性重算。成功输出必须同时满足：

- `analyzer_name=remote_http`；
- `proof_valid=true` 且全部 checks 为 true；
- 持久化结果是 `remote_contract_stub`、`provenance.synthetic=true`，且 runtime 为 `stub/false/[]`；
- `evidence_grade=false`；
- `accuracy_claim=null`；
- limitations 明确包含 STUB 或 not-evaluated 边界。

2026-07-14 本功能落地时历史验收：参考服务专项 24 passed，参考服务 + 远程桥 + 集成 + 合同
制品组合 59 passed，当时全量后端 365 passed / 90.34%；真实进程 smoke 的档案完整性为 8/8。
当前 Alpha9 全量门禁请以 `STATUS_2026-07-14_STAGE2.md` 为准。

## 真实算法服务的边界

本参考服务的 runtime 身份永久是 `stub`。仅替换进程内 predictor 不会、也不得把它升级成
真实模型服务；真实算法应部署独立服务，实现同一请求/响应合同并返回
`runtime={"mode":"model","model_loaded":true,"capabilities":[...]}`，业务后端则保持
`FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE=model`。以下事项不能由模板自动完成：

1. 获得数据来源、媒体外发、脱敏、保留和删除授权；
2. 冻结 model name/version/weight SHA、code SHA 和 config SHA；
3. 让 predictor 从同一 `evidence_stream` 读取输入并返回合同允许的五类字段；
4. 用授权数据单独执行 EvaluationRun，而不是把单次 `/v1/analyze` 当作准确率；
5. 生产部署使用 HTTPS、持久化/分布式幂等、反向代理总请求上限和受控日志。

当前 FastAPI multipart 会先把文件写入内存/临时文件再由端点执行分块上限校验。模板本身会
拒绝超限内容，但互联网部署还必须在反向代理或网关设置请求体上限，避免临时磁盘被超大
chunked multipart 消耗。

## 自动化验证

```bash
make reference-analyzer-test
make backend-contracts backend-quality backend-test
```

参考服务测试同时核对 `reference-stub-artifact.json` 的实际文件字节摘要，防止默认模型身份
与展示制品漂移。
