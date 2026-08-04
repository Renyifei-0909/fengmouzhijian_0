# 远程算法桥接协议 v1

## 1. 定位与真实性边界

`remote_http` 用于把已经过本地格式检查、SHA-256 固化和项目/设计基线绑定的媒体发送给团队独立部署的算法服务。它解决“算法稍后嵌入”的工程接口问题，但不替代数据集和正式评测。

当前服务端强制：

- `evidence_grade=false`；
- `accuracy_claim=null`；
- `recommended_action=manual_review`；
- 人工批准后报告为 `reviewed_non_evaluated`，证据包 `purpose=review`；
- 只有以后独立实现并绑定冻结 `EvaluationRun`，才能升级正式指标或提交证据。

远程算法响应中若在任意层级出现 `evidence_grade`、`accuracy_claim`，或在已登记对象边界出现其他字段，会被严格拒绝，任务进入 `failed`，不会污染报告。`measurements`、`objects`、`events`、`differences` 仍允许领域 JSON，但会递归拦截受保护声明字段。

## 2. 启用配置

默认 `FENGMOU_REMOTE_ANALYZER_ENABLED=false`。启用时以下配置全部必填：

```dotenv
FENGMOU_REMOTE_ANALYZER_ENABLED=true
FENGMOU_REMOTE_ANALYZER_URL=https://algorithm.example.internal/v1/analyze
FENGMOU_REMOTE_ANALYZER_API_KEY=<独立的算法服务 Bearer 密钥>
FENGMOU_REMOTE_ANALYZER_MODEL_NAME=hidden-work-baseline
FENGMOU_REMOTE_ANALYZER_MODEL_VERSION=0.1.0
FENGMOU_REMOTE_ANALYZER_MODEL_SHA256=<64位小写十六进制模型制品摘要>
FENGMOU_REMOTE_ANALYZER_EXPECTED_RUNTIME_MODE=model
FENGMOU_REMOTE_ANALYZER_TIMEOUT_SECONDS=120
FENGMOU_REMOTE_ANALYZER_MAX_UPLOAD_BYTES=104857600
FENGMOU_REMOTE_ANALYZER_MAX_RESPONSE_BYTES=2097152
```

启动配置会 fail-closed：缺字段、摘要不合法、上限超过平台上传上限、URL 带用户名/密码/query/fragment、超时非正数都会直接报错。production 环境要求 HTTPS；development/test/demo 允许内网 HTTP。运行身份默认且应保持为 `model`；`stub` 只允许在 `FENGMOU_ENVIRONMENT=test|demo` 中显式配置，production/development 均拒绝启动。

平台的 `X-API-Key` 不会转发。算法服务使用单独 Bearer 密钥，`/meta` 也不会暴露 URL、密钥或完整模型摘要。

## 3. HTTP 请求

- 方法：`POST`；
- URL：只能来自部署配置，业务请求不能覆盖；
- `follow_redirects=false`；
- `multipart/form-data`；
- 文件发送前再次重算 SHA-256，变化则禁止外发；
- 媒体使用文件句柄流式发送，不一次性读入内存。

固定请求头：

```text
Authorization: Bearer <remote-only-secret>
Accept: application/json
X-Fengmou-Contract-Version: 1.0
X-Fengmou-Request-ID: <job_id>
X-Evidence-SHA256: <evidence_sha256>
X-Baseline-SHA256: <baseline_sha256>
Idempotency-Key: <稳定的64位摘要>
```

同一 job 的恢复或手动重试会得到相同 `Idempotency-Key`。算法服务应缓存或识别该 key，避免进程在“上游已完成、结果尚未落库”窗口崩溃后重复推理。

multipart 固定两个 part：

1. `evidence`：中性文件名 `evidence-<evidence_id>.<ext>`，内容为原始媒体；
2. `request`：文件名 `request.json`、`application/json`，字段以 [request JSON Schema](./remote-analyzer-request-v1.schema.json) 为准。

请求只包含 evidence id/摘要/大小/类型、白名单媒体探测字段，以及工点/工序/基线。`baseline.expected` 也不是任意透传：v1 只允许 `scene_type` 及 `measurements.min_depth_m/min_spacing_m/expected_quantity/expected_specification`，未知字段在联网前失败；`request.json` 另有 64 KiB 硬上限。不会发送项目名称、详细地址、经理、审核人、平台密钥或任意用户 metadata。但 `evidence` part 是入库媒体本体，桥接层不会自动清除 EXIF、模糊人脸/水印或遮挡画面文字；字段白名单不等于媒体已脱敏。任何真实数据外发仍必须先确认来源授权、脱敏和算法服务的数据保留政策。

## 4. 成功响应

只接受 `200` 与 `Content-Type: application/json`。响应大小不能超过配置上限，结构以 [response JSON Schema](./remote-analyzer-response-v1.schema.json) 为准。解析器拒绝 JSON 标准之外的 `NaN`/`Infinity` 及任意层级的无效 Unicode scalar，不能用静默转成 `null` 的方式改变测量语义。

最小示例：

```json
{
  "contract_version": "1.0",
  "request_id": "<必须等于 X-Fengmou-Request-ID>",
  "model": {
    "name": "hidden-work-baseline",
    "version": "0.1.0",
    "artifact_sha256": "<必须与部署配置完全一致>"
  },
  "runtime": {
    "mode": "model",
    "model_loaded": true,
    "capabilities": ["ppe_detection"]
  },
  "observations": {
    "measurements": {},
    "objects": [],
    "events": []
  },
  "alignment": {
    "status": "not_evaluated",
    "differences": []
  },
  "findings": [],
  "confidence": null,
  "limitations": [
    "not evaluated on the frozen competition dataset"
  ]
}
```

模型 name/version/artifact SHA-256、runtime mode 或 request id 不一致会被视为配置漂移。任务中持久化的是由 endpoint、桥/合同版本、模型 name/version、完整 artifact SHA-256 和期望 runtime mode 共同计算的 64 位配置指纹；任一项变化都会阻止旧任务恢复或重试到新配置。响应允许提供 `code_sha256`、`config_sha256`；建议真实 baseline 从第一版开始填写。

`runtime` 是强制的机器身份，而不是展示文本：`mode=model` 必须同时满足 `model_loaded=true` 且至少声明一个 capability；`mode=stub` 必须满足 `model_loaded=false`。capability 只能使用受长度限制的小写标识并且不得重复。业务后端默认只接受任务锁定的 `model`；显式 demo/test STUB 会固化为 `provenance.synthetic=true`、`kind=remote_contract_stub`，因此其 finding 只能进入 demo scope，不能形成 operational case。无论 runtime mode 如何，单次响应仍固定 `evidence_grade=false`、`accuracy_claim=null`。

业务服务会补入：输入/响应摘要、固定模型身份、基线版本、服务端真实性提示和 `REMOTE_RESULT_REQUIRES_VALIDATION` finding。响应正文、Bearer 密钥和 URL query 不写入审计。

媒体外发不会在 adapter 内重新按数据库路径打开文件。任务编排层先用受控 storage
规则完整核对路径、普通文件类型、magic bytes、大小和 SHA-256，并在保持该文件描述符
打开的上下文中调用 adapter；multipart 从同一个已验证 fd 读取。缺少、关闭或与当前
任务记录不绑定的 source 会在网络请求前 fail closed。即使校验后路径被替换，远程服务
也只能收到原先已验证 inode 的字节。

## 5. 失败语义

| 错误码 | 含义 | 可重试 |
|---|---|---|
| `REMOTE_INPUT_MISSING` | 已验证原件 source 缺失或已关闭 | 否 |
| `REMOTE_INPUT_TOO_LARGE` | 超出远程独立上限 | 否 |
| `REMOTE_INPUT_HASH_MISMATCH` | 已验证 source 与任务中的文件名、类型、大小或摘要绑定不一致 | 否 |
| `REMOTE_REQUEST_INVALID` | 本地请求元数据不在显式外发白名单内 | 否 |
| `REMOTE_REQUEST_TOO_LARGE` | `request.json` 超过 64 KiB | 否 |
| `REMOTE_TIMEOUT` | 上游超时 | 是 |
| `REMOTE_TRANSPORT_ERROR` | DNS/TCP/TLS/连接错误 | 是 |
| `REMOTE_HTTP_ERROR` | 上游非 200；429/5xx 标记可重试 | 视状态 |
| `REMOTE_RESPONSE_CONTENT_TYPE` | 成功响应不是 JSON | 否 |
| `REMOTE_RESPONSE_TOO_LARGE` | 响应超过上限 | 否 |
| `REMOTE_RESPONSE_INVALID` | JSON 或 schema 不合法 | 否 |
| `REMOTE_RESPONSE_PROTECTED_CLAIM` | 响应在任意层级注入服务端控制的声明字段 | 否 |
| `REMOTE_REQUEST_ID_MISMATCH` | 请求身份漂移 | 否 |
| `REMOTE_MODEL_IDENTITY_DRIFT` | 模型身份/摘要漂移 | 否 |
| `REMOTE_RUNTIME_MODE_MISMATCH` | 服务返回的 model/stub 身份与任务锁定配置不一致 | 否 |

MVP 不自动重试；operator 检查原因后显式调用 retry。若部署配置中的模型版本或摘要已变化，旧任务拒绝重试，必须新建任务，防止同一 job 偷换模型。

## 6. 契约维护与验证

算法组在真实模型完成前可以先运行 [`REFERENCE_ANALYZER_SERVICE.md`](./REFERENCE_ANALYZER_SERVICE.md)
中的独立 STUB 服务。它会严格实现本章鉴权、请求绑定、模型身份、幂等和响应限制，并允许
业务平台走真实 HTTP；默认输出为空且明确 `not_evaluated`，因此只能作为协议兼容性证据。

修改远程请求/响应模型后执行：

```bash
cd backend
python scripts/export_remote_contract.py
python scripts/export_remote_contract.py --check
python -m pytest tests/test_remote_http_analyzer.py \
  tests/test_remote_http_integration.py \
  tests/test_remote_contract_artifacts.py \
  tests/test_reference_remote_analyzer.py
```

算法同学接入前至少需要提供：

- 固定 endpoint 与健康检查方式；
- 模型 name/version/artifact SHA-256；
- 严格 runtime 身份：真实服务返回 `model/true/capabilities`，参考服务返回 `stub/false/[]`；
- 代码/配置摘要；
- 对 request/response schema 的契约测试；
- Idempotency-Key 行为；
- 超时、最大媒体、最大响应和错误状态约定；
- 数据保存、日志、第三方传输和删除策略；
- 明确说明当前输出是单样本推理，不是准确率评测。
