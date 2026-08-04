# MVP 0.2.0 历史验证记录（2026-07-10）

> 本文是 2026-07-10 阶段快照，其中 282 passed / 90.05% 不是当前候选版本门禁。
> 当前 Alpha9 结果请以 `STATUS_2026-07-14_STAGE2.md` 和 `STAGE2_ALPHA9_RECOVERY.md` 为准。

## 结论

后端 MVP 0.2.0 与 `/backend-workflow` 初版前端已形成可运行闭环，并新增独立 Evaluation v0 离线评分核、development-only 本地模型运行合同和 unsigned evidence bundle。当前验证覆盖工程/基线、视频输入、分析任务、人工复核、JSON/HTML 报告、证据 ZIP、8 项服务端完整性检查、独立离线检查、角色 API key、启动恢复、原子领取、适配器合同，以及冻结评分的分母/隔离/阈值/路径、本地进程生命周期和开发证据原子发布/离线校验场景。没有把合成适配器、远程桥、fixture predictions 或 development run 的分数计为模型能力或竞赛正式指标。

## 自动化回归

后端命令：

```bash
python -m compileall -q app scripts
python -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90
python -m pip check
```

结果：

- 282 个测试全部通过，其中评测内核、development runner、两类 evidence 与 registry 专项 197 项；
- Python 警告按错误处理后仍通过；
- 应用代码覆盖率 90.05%（两位小数门禁 90.00%）；
- Python 依赖检查无冲突。

契约制品已生成并纳入漂移检查：

- `docs/openapi-v1.json`；
- `docs/remote-analyzer-request-v1.schema.json`；
- `docs/remote-analyzer-response-v1.schema.json`。

最终漂移检查摘要：OpenAPI `f7c05fc73102a7f5d7f823df49ca20d0689143dd358d61fd0f76e54b9fa29459`，远程请求 schema `08b65038de51f3f350beb76661317a04ee83c0c956561e419531657dab34d05e`，远程响应 schema `84220d8ac548cbce36bd4abbec332d444a65945eaf983952f67e7d506d49ebb3`。

P1 自动化覆盖还包括：遗留 `running` 任务的启动恢复、条件更新原子领取、分析前原件大小/SHA-256 与基线规范摘要复算、统一严格 analyzer 结果合同、扩展名/MIME 防绕过，以及 `remote_http` 的完整 endpoint+模型配置指纹、外发白名单、幂等键、响应边界、嵌套声明/非标准 JSON 拒绝与结构化失败路径。`remote_http` 默认关闭，测试使用受控替身验证桥接合同，没有连接或验证真实目标算法。

Evaluation v0 自动化另覆盖：严格 UTF-8 JSON/JSONL、固定分母、canonical 字符串阈值、外部 manifest/model 摘要 pin、gate/final formal 限制、六类分组传递闭包、同源事件去重、所有结果固定不可生成合规声明，以及基于 root dirfd 的逐级 `openat + O_NOFOLLOW`。FIFO、同根/中间符号链接和检查后根外目录换包均被结构化拒绝；相对媒体的大小与摘要来自同一已打开文件描述符。

前端命令：

```bash
npm run verify
```

结果：

- TypeScript `tsc --noEmit` 通过；
- Vite 7.3.6 生产构建通过；
- `npm audit --audit-level=moderate` 为 0 个已知漏洞。

## 实际 API 冒烟

0.2.0 运行态重启后，`/api/v1/readyz` 返回 `ready`，`/api/v1/meta` 返回 `service_version=0.2.0` 且 `remote_http.enabled=false`，持久化 dashboard 可正常读取。

使用有效 1 秒 MP4、全新 SQLite 数据库和三类本地演示 key 验证：

- 未授权创建项目：401；
- 创建项目/设计基线/传感器事件：201；
- 上传真实 MP4：202，ffprobe 解析成功；
- 任务最终状态：`needs_review`；
- reviewer 批准：200；
- 报告状态：`reviewed_demo`；
- 证据包：`purpose=demo`、`evidence_grade=false`；
- 服务端 8 项检查全部为 true：archive 存在/整体摘要、manifest 摘要、成员摘要、Merkle Root、record hash、ledger chain、metadata consistency；
- JSON 报告和证据 ZIP 下载：200；
- 独立 `verify_bundle.py` 校验同一包：通过。

损坏/异常路径也已覆盖：扩展名/MIME 错配、伪装视频、无法被 ffprobe 解析的视频、分析前原件/基线变化、嵌套真实性声明、NaN/Infinity、无效 Unicode、封存前原件变化、报告变化、归档变化、证明元数据变化、畸形 manifest、未知档案和失败任务重试。

## 真实浏览器验证

使用 Playwright 在 `http://127.0.0.1:5173/backend-workflow` 执行了完整用户流程：

1. 填写 operator/reviewer key；
2. 创建匿名项目和设计基线；
3. 选择 `demo_fixture` 并上传真实 MP4；
4. 查看 `needs_review`、原件摘要和合成结果边界；
5. reviewer 批准并生成报告/证据包；
6. 重新校验，页面展示 8 项全部通过；
7. 下载 JSON 报告和 ZIP，两个请求均为 200。

桌面 1440×1000 与移动 390×844 均检查。移动端曾发现长 JSON 撑宽页面，已通过给 Grid 子列添加 `min-w-0` 修复；复测 `scrollWidth=innerWidth=390`。最终浏览器控制台 0 error、0 warning。

## 尚未验证或尚未实现

- 本机没有 Docker，`compose.yaml` 尚未做容器冷启动验证；
- 金山在线方案已在 2026-07-10 只读访问并核对目录、正文开头与五方向目标，但延迟渲染页面未逐段导出全文；具体参数仍需团队导出版本化文件复核；
- 真实视觉算法、授权冻结数据、受控模型执行隔离、可信 broker 与 85%/90% 正式 EvaluationRun 尚未完成；当前 development runner/unsigned evidence 仅可在 `train`/`validation` 以同 UID、未禁网方式生成、评分和封存 predictions。本地 registry 只约束单库状态，Ed25519 controlled-local verifier 只证明固定字节由外部 pin 的 key 签署；approval 仍自报、无可信时间和 replay 数据库，均不具备正式资格；
- 当前是批处理、本地文件/SQLite/进程内任务，不是直播、生产队列或多租户平台；启动恢复仅提供单进程 at-least-once 语义，多副本仍需独立 worker 与租约/心跳；
- `remote_http` 只是默认关闭的远程算法桥，真实目标服务、合法数据与指标尚未接入；
- 当前 key 只有全局读和写角色分离，不是用户/项目级 RBAC；
- 本地哈希链不是区块链、数字签名、司法存证或可信时间戳。
