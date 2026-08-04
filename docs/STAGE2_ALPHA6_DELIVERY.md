# Stage 2 Alpha 6 交付说明（2026-07-14）

## 1. 本增量结论

Alpha 6 收口了“远程任务失败后如何安全恢复”和“报告封存后如何继续保持真实性边界”两条链路，
同时修正比赛演示中会造成直接误解的前端指标与移动端入口。

本增量证明：已登记证据可以在远端服务失败后由操作者显式重试；并发重试只允许一次入队；
远程单样本结果即使经过人工批准、报告封存和 8 项完整性核验，也仍保持
`evidence_grade=false`、`accuracy_claim=null` 和“未冻结 EvaluationRun”的持久化说明。

它不证明参考服务已经加载真实视觉模型，不证明竞赛 85%/90% 指标，也不把本地哈希链表达为
区块链、司法存证或可信时间戳。

## 2. 后端修复

### 2.1 并发安全的显式重试

`POST /api/v1/verifications/{job_id}/retry` 现在用单条条件更新执行
`failed -> queued`：

- 只有 `WHERE id=:id AND status='failed'` 命中的请求返回 200；
- 成功请求清空旧 `result_json`、错误、开始/完成时间并把进度重置为 0；
- 只有成功请求写入一条 `retry_queued` 审计并调度一个 worker；
- 并发竞争失败者返回 409，并带当前任务状态；
- analyzer disabled 的 403、配置版本漂移的 409 和稳定远端幂等键保持不变。

同一并发探针对比：

```text
修复前：HTTP 200 + 200，调度 2 次，retry_queued 2 条
修复后：HTTP 200 + 409，调度 1 次，retry_queued 1 条
```

### 2.2 报告真实性边界

报告服务按 `stub`、`demo_fixture`、`remote_http`、未知适配器和正式评测证据分别生成
`truth_boundary`，并把它持久化到数据库、JSON、HTML 和证据 ZIP。人工批准只记录复核决定，
不改变 analyzer mode 或证据资格。

最新真实进程记录：

- 任务：`07e98893-8271-4007-8d7f-71b21d2ff12e`；
- 报告：`5cbc6f23-61bf-486b-9c44-18c0582048bf`；
- 证明：`03500d8e-9d49-4409-878c-4458ab81aade`；
- 档案：`ARC-b3c358fd-46a4-4de2-a00b-876e0dc49b6b`；
- 状态：`reviewed_non_evaluated`；
- 结果：`analysis_mode=remote_http`、`evidence_grade=false`、`accuracy_claim=null`；
- 证明校验：8/8 为 true。

报告实际写入：单次固定远程响应不是冻结 EvaluationRun 或已验证准确率；人工批准不会升级
证据资格。

## 3. 前端与 Image 2.0 概念图

Image 2.0 概念图、完整提示词、SHA-256 和采用/拒绝的设计原则见
[`design/STAGE2_ALPHA6_IMAGE2_CONCEPT.md`](./design/STAGE2_ALPHA6_IMAGE2_CONCEPT.md)。概念图只作视觉参考，
图中的施工现场、设备、发现项和哈希没有写入演示数据库。

本轮实际落地：

- 移动端核心入口改为“总览 / 项目 / 验真 / 报表 / 溯源”；
- “闭环率”统一改为“已批准基线覆盖率（代理指标）”，后端 `metric_note` 就近展示；
- 失败原因只在任务卡内展示，保留检查原因后的显式重试；
- 闭环页状态区、鉴权输入和复核按钮适配小屏；
- 真实性面板按占位、未评测、演示、正式证据和失败使用不同语义色；
- 远程模型/请求/响应哈希默认收进“展开技术溯源”；
- 报告与证据包区直接显示服务端持久化的 `truth_boundary`；
- 通用弹窗增加 dialog/ARIA、Escape 关闭、初始焦点和焦点恢复；
- 修复移动端长 analyzer 版本摘要导致的页面级横向溢出。

## 4. 失败与恢复实测

独立参考服务停止时，通过真实浏览器提交远程任务：

- 失败任务：`c7719c74-35bc-4aae-a69c-324d9b36ce38`；
- 原始证据：`3bf27c93-5331-4d0f-872e-1ffff3bf44c7`；
- 安全错误：`REMOTE_TRANSPORT_ERROR: Remote analyzer transport failed: ConnectError`。

恢复参考服务后，项目页显示失败原因和“显式重试”。点击后同一任务进入 `needs_review`，随后被
人工批准并生成证明 `5e1696d7-70df-4cb4-9c5e-90a463295855`；对应档案 8/8 校验通过。

浏览器证据位于工作区上层：

- `output/playwright/stage2-alpha6-retry-failed.png`；
- `output/playwright/stage2-alpha6-retry-recovered.png`。

## 5. 严格验证

后端：

- 全量：369 passed；
- `-W error`：通过；
- 应用代码覆盖率：90.37%，门禁 90%；
- `reporting.py`：97.92%；
- OpenAPI 与远端请求/响应 Schema：逐字节校验通过；
- `compileall`、`pip check`：通过；
- 新增回归覆盖：503 后稳定幂等键重试成功、并发 retry 单次入队、模式化报告边界、恶意报告
  文本 HTML 转义。

前端：

- TypeScript：通过；
- Vite production build：70 modules，509.01 kB，gzip 136.55 kB；
- `npm audit --audit-level=moderate`：0 vulnerabilities。

真实浏览器：

- 1440x1024 项目页和闭环页无页面级横向溢出；
- 375x812 项目页与闭环页均为 `scrollWidth=innerWidth=375`；
- 移动导航五个核心入口全部存在；
- 报告表格在移动端使用局部横向滚动，页面本身不溢出；
- 报告弹窗存在 `role=dialog`/`aria-modal`，Escape 可关闭且焦点返回触发按钮；
- 最新报告弹窗能读取“not a frozen EvaluationRun”和“does not change the analyzer mode”；
- 全流程浏览器控制台 0 error。

本轮截图：

- `output/playwright/stage2-alpha6-project-desktop.png`；
- `output/playwright/stage2-alpha6-project-mobile.png`；
- `output/playwright/stage2-alpha6-workflow-desktop.png`；
- `output/playwright/stage2-alpha6-workflow-mobile.png`；
- `output/playwright/stage2-alpha6-report-truth-modal.png`。

## 6. 当前本机服务

- 前端：<http://127.0.0.1:5173>；
- 业务 API：<http://127.0.0.1:8000/docs>；
- 业务就绪：<http://127.0.0.1:8000/api/v1/readyz>；
- 参考服务健康：<http://127.0.0.1:8012/healthz>；
- 远程参考项目：<http://127.0.0.1:5173/projects/d5c7a2e6-bb8b-43bb-b378-19b44f0ce25c>；
- 真实闭环：<http://127.0.0.1:5173/backend-workflow>；
- 报表中心：<http://127.0.0.1:5173/reports>。

## 7. 未解决风险

1. 参考分析器仍是 STUB，空 observations 不表示真实识别或量测能力。
2. 报告文件、ZIP、ledger 和数据库提交尚不是一个原子事务；进程崩溃时需要 staging、outbox 和
   启动补偿扫描，这是生产化 P1。
3. 参考服务幂等缓存是进程内有界 LRU；重启或淘汰后不会保留结果，接真实模型前应改为
   数据库或 Redis 持久幂等。
4. 当前任务恢复按单 Uvicorn worker 设计；多 worker 部署前需要 lease、心跳和独立 worker。
5. 本地全局 API Key 只适合演示；正式部署需要登录、JWT、项目/组织级 RBAC 和短期媒体票据。
6. 移动端报告清单目前是局部横向滚动，后续可改为卡片列表提升易用性。
7. 合法数据集、标签体系、唯一算法路线、真实 non-mock predictor、冻结 EvaluationRun 和竞赛
   指标仍未完成；在团队书面冻结这些输入前，不下载权重或启动训练。
