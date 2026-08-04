# Stage 2 Alpha 7 交付说明（2026-07-14）

## 1. 本增量结论

Alpha 7 把“人工批准后生成报告与证据包”从一次不可恢复的数据库/文件双写，改为本机可检测、
可恢复、幂等的封存 Saga；同时把移动端报告表格替换为报告卡片，并修复长报告弹窗超出小屏
视口的问题。

本增量证明：在当前单机 SQLite + 本地文件系统部署下，JSON、HTML、ZIP、哈希台账或最终数据库
提交发生可重试故障后，系统能保留固定制品 ID 并继续完成，不静默生成第二套档案；并发批准只
允许一个请求取得封存意图。它不等于数据库、文件系统与 ledger 之间存在分布式原子事务，也不
证明真实视觉模型、授权数据或竞赛 85%/90% 指标已经完成。

## 2. 后端封存 Saga

批准路径现在使用持久化 `SealOperation`：

```text
needs_review
  -> sealing / requested
  -> artifacts_staged
  -> files_published
  -> ledger_appended
  -> approved / completed

任一摘要、路径或链冲突 -> manual_attention + readiness 503
暂时性 I/O/DB 故障     -> 保留最近成功状态与 last_error，重试或启动恢复
```

关键约束：

- 条件更新抢占 `needs_review -> sealing`，并在首个事务内持久化 review、固定 report/archive ID、
  报告输入快照和 `seal_requested` 审计；
- 报告快照在复核时冻结，传感器按 `(captured_at, id)` 排序，恢复时不会混入之后新增的事件；
- 同盘 `.seal-staging` 使用临时文件、`fsync` 和 `os.replace`，最终路径存在时必须摘要相同；
- ZIP 内原件使用固定安全成员名，原始文件名只保留在结构化元数据，避免跨平台解压路径歧义；
- ledger 使用 `flock` 协调本机多进程，先验证全链，再整文件临时写入、`fsync`、原子替换；相同
  archive ID 只允许复用完全一致的记录；
- `ledger_appended -> approved` 前再次核对最终 JSON/HTML/ZIP、operation 摘要、完整 ledger 行和
  8 项证明，随后用条件更新完成 `sealing -> approved`；
- 已有 report/proof/audit 只有全部稳定字段一致时才能幂等复用；
- operation/report/archive ID、staging/lock/ledger 路径和符号链接均 fail closed；
- 启动时恢复非终态 operation，再扫描新旧全部报告、证明、ledger 与孤儿制品；`readyz` 每次执行
  新鲜扫描，异常只对外返回问题数量，详细问题留在应用状态/审计中。

`SealOperation` 当前由 `Base.metadata.create_all()` 创建，适合本地 Alpha 升级；正式保留数据的部署
仍需补 Alembic migration，不能把 `create_all` 当生产迁移系统。

## 3. 故障、并发与恢复测试

`backend/tests/test_sealing_saga.py` 覆盖 12 个测试函数/场景组合：

1. 正常批准只生成一套 operation/review/report/proof/ledger/制品；
2. staging 首次失败后，同一 review API 用固定 ID 恢复；
3. ledger 已发布、最终 DB commit 失败后，重试不改变 ledger 字节；
4. 两个并发 approve 稳定得到一个 200、一个 409；
5. 重启自动恢复未完成 operation；
6. staging 丢失后从冻结快照确定性重建；
7. 已存在的匹配 ledger/report/proof/audit 可幂等复用；
8. ZIP 在最终批准前被修改时进入 `manual_attention`；
9. completed 报告或 ZIP 丢失后，当前进程和重启后的 readiness 均为 503；
10. 非法 ID、目录/ledger/staging 符号链接和同 operation 并发锁被拒绝；
11. 坏 ledger 索引或记录哈希被拒绝；
12. 冻结分析、基线、证据或人工复核快照发生漂移时被拒绝。

最终严格门禁：

- 381 passed；
- `-W error` 通过；
- 应用代码覆盖率 90.13%，门禁 90%；
- `compileall`、`pip check` 通过；
- OpenAPI 与远端请求/响应 Schema 逐字节校验通过。

## 4. 真实运行态验收

业务后端已用最新代码在本机重启；启动扫描兼容 Alpha6 旧记录，历史最新证明仍为 8/8 有效。
随后通过真实 HTTP 创建一条明确标注为 workflow-only 的 `stub` 验收链：

- 项目：`cfce5c56-74b9-4c8e-94b1-721413a7f6a0`；
- 任务：`65c88fd6-dcbc-4097-a230-57f8fb36be24`；
- SealOperation：`completed`，一次尝试；
- 报告：`0cf8a441-3790-49cd-aa6a-dc8c2476b42a`；
- 证明：`ebb100e2-308a-4f97-a55d-53f83afc527e`；
- 档案：`ARC-9e202944-44f1-4de3-9b6b-df2bf7f66823`；
- 证明核验：8/8 为 true；
- readiness：`ready`；
- 真实性字段：`analysis_mode=stub`、`evidence_grade=false`、`accuracy_claim=null`。

这条记录只证明 Alpha7 工程封存链可运行；它没有视觉识别或物理量测结论。

## 5. 前端与浏览器验收

Alpha6 的 Image 2.0 概念图和提示词继续作为视觉参考；Alpha7 实际落地：

- 小于 `md` 的报告清单使用可操作卡片，显示后端持久化摘要、项目、时间、Schema 和真实性徽标；
- 每张卡片直接提供预览与 JSON 下载，桌面端继续使用高密度表格；
- `sealing` 状态在闭环页和项目详情持续轮询，不会停在旧状态；
- Modal 通过 portal 脱离页面 transform 上下文，最大高度受 `100dvh` 约束；标题/底部操作固定，
  正文独立滚动，并用 `preventScroll` 保持初始焦点不移动遮罩层。

前端门禁：TypeScript、Vite production build、`npm audit --audit-level=moderate` 全部通过；
70 modules，512.75 kB，gzip 137.31 kB，0 vulnerabilities。

真实浏览器：

- 375×812：页面 `scrollWidth=innerWidth=375`，6 张移动报告卡片，桌面表格隐藏；
- 移动 Modal：`top=16`、`bottom=796`、`height=780`，完整位于 812px 视口；
- 1440×1024：页面 `scrollWidth=innerWidth=1440`，桌面表格显示 6 行；
- Modal 保留 `role=dialog`/`aria-modal`、Escape 关闭和焦点返回；
- 最新报告可读取 stub 与人工批准不升级证据资格两条持久化边界；
- 浏览器控制台 0 error。

截图位于工作区上层：

- `output/playwright/stage2-alpha7-reports-mobile.png`；
- `output/playwright/stage2-alpha7-reports-desktop.png`；
- `output/playwright/stage2-alpha7-report-modal-viewport.png`。

## 6. 当前本机入口

- 前端：<http://127.0.0.1:5173>；
- 报表中心：<http://127.0.0.1:5173/reports>；
- 真实闭环：<http://127.0.0.1:5173/backend-workflow>；
- 业务 API：<http://127.0.0.1:8000/docs>；
- 业务就绪：<http://127.0.0.1:8000/api/v1/readyz>；
- 参考服务：<http://127.0.0.1:8012/healthz>（当前 predictor 仍为 STUB）。

## 7. 仍未完成/不能声称完成

1. Saga 是本机可恢复的最终一致性机制，不是跨数据库/文件/对象存储的真正原子事务；
2. `flock` 只协调同一主机；NFS、多节点或对象存储部署需要数据库锁/租约、持久 outbox 和条件写；
3. 大量未完成的 500 MB operation 可能拉长同步启动恢复时间，生产化需要恢复预算与后台 worker；
4. 参考分析器幂等缓存仍是进程内 LRU，任务执行仍以单 Uvicorn worker 为主要部署边界；
5. 本地全局 API Key、SQLite、本地文件和 `create_all` 都不是生产身份/存储/迁移方案；
6. 真实 PPE/隐蔽工程算法、合法授权数据、冻结 holdout、可信 EvaluationRun 和 85%/90% 指标仍未完成；
7. 在团队书面冻结数据、标签和唯一模型路线前，不下载权重、不启动训练，也不把 stub/demo 数值写成成绩。

## 8. Alpha7 交付包

- 源码交付包：`fengmou-zhijian-stage2-alpha7-2026-07-14.zip`；
- 摘要伴随文件：`fengmou-zhijian-stage2-alpha7-2026-07-14.zip.sha256`；
- 包内不含本地数据库、上传原件、运行期报告/证据包、API Key、`node_modules`、构建目录、
  coverage 数据和 Python 缓存；
- 包内保留完整源码、测试、契约、Image 2.0 概念图/提示词及阶段文档。

摘要不能写回 ZIP 内部，否则会形成自引用并改变 ZIP 自身摘要；因此以同目录的 `.sha256`
文件和交付时公布的摘要为准。解包后仍需按本说明第 7 节理解真实性边界。
