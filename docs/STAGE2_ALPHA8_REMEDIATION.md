# Stage 2 Alpha 8：Finding 分诊、整改复验与证据关闭（2026-07-14）

## 1. 交付结论

Alpha8 把原告警原型替换为真实数据库驱动的 Finding Case/Remediation Attempt 链，并把
“案件 → 整改 → 原项目/基线复验 → 人工结论 → 新报告/proof → 关闭”跑通到公开 API 和
前端页面。

本版本可作为内部功能演示版：软件状态机、报告/ZIP 冻结、并发绑定、篡改检测和浏览器操作
已经验证。它不是最终生产告警平台，也不是算法验收版：当前可见的整改案例来自
`demo_fixture` 合成视频；真实目标模型、授权现场数据和 85%/90% 指标仍未完成。

## 2. 领域与真实性规则

```text
analyzer finding
  -> candidate observation / pending_triage
  -> reviewer confirm --------------------------> open
  -> reviewer dismiss --------------------------> dismissed
  -> operator/reviewer assign + plan -----------> remediation_in_progress
  -> immutable RemediationAttempt
  -> upload new evidence with same project/baseline
  -> verification_pending
  -> reviewer approve + resolved --------------> seal new report/proof -> closed
  -> reviewer approve + not_resolved ----------> seal evidence -> remediation_in_progress
  -> reviewer reject ---------------------------> remediation_in_progress
```

强制边界：

- system notice 和 `severity=info` 不物化案件；finding 首先只是候选，不是自动现场事实；
- 只有 reviewer 确认且 `scope=operational` 的案件进入运营告警数；
- `analysis_mode=demo_fixture` 或 synthetic provenance 产生的案件固定为 `scope=demo`；
- 人工确认 demo 案件不会升级其真实性，也不会进入模型指标；
- 关闭必须绑定新的复验 job/report/proof；源报告 JSON、HTML、ZIP、proof 和 ledger 行保持字节不变；
- 当前 actor 来自全局角色 API Key，不是用户/组织/项目 ACL；本地 ledger 不是区块链或可信时间戳。

## 3. 后端实现

新增/接入：

- `FindingCase`：源 job/evidence/baseline、finding/result 摘要、分析来源、scope、人工决定、负责人、期限、关闭 proof 和 CAS version；
- `FindingCaseCommand`：分诊/启动/Attempt 的幂等键、payload 摘要、from/to 状态和 result version；
- `RemediationAttempt`：不可变整改动作、复验 job、人工 resolution、report/proof 绑定；
- analyzer 完成事务内按稳定 finding identity 物化候选；
- 报告冻结 `finding_cases`，复验报告额外冻结 `remediation_context`；
- ZIP 增加 `findings/cases-at-seal.json`、`remediation/case.json` 和
  `remediation/attempt.json`，成员进入 manifest 与 Merkle；
- `/readyz` 同时扫描封存 Saga 和整改图完整性。

复验绑定使用两个条件更新：Attempt 必须仍是 pending/unbound，Case 必须仍为
`remediation_in_progress`、active attempt 一致且 version 未变化。任一步竞争失败，上传事务回滚，
新 evidence/job 不落库，上传文件删除。

关闭后的完整性检查重新验证：

```text
case.closure_proof_id == attempt.proof_id
proof.report_id == attempt.report_id
report.job_id == attempt.verification_job_id
case.project/baseline == job.project/baseline == report.project
resolution_decision == resolved
case.closed_by/closed_at == attempt.resolved_by/resolved_at
database report remediation_context == ZIP remediation snapshots
ZIP member hash/Merkle/ledger/proof checks == valid
```

封存报告 API 的数据库 `content_json` 也会重新渲染，并与 JSON/HTML 文件摘要及
`SealOperation.report_content_json` 比较，避免只改数据库内容却绕过 readiness。

## 4. 前端实现

`/alarms` 现在只使用真实 API：

- 运营、demo、待分诊、整改中、复验中和已关闭分开统计；
- 错误时明确显示失败，不回退旧 mock；
- 卡片显示来源模式、severity、状态和真实性标签；
- Modal 提供 reviewer 分诊、负责人/期限、整改计划、实际 Attempt、复验 job、closure proof 和命令历史；
- 整改计划与实际完成动作使用不同状态，避免误提交；
- `dismissed` 显示为“人工排除候选”，不再误标为人工确认告警。

未绑定 Attempt 深链到：

```text
/backend-workflow?caseId=<case-id>&attemptId=<attempt-id>
```

真实闭环页会读取案件、Attempt、项目和基线，锁定原 `project_id/baseline_id`，禁止初始化匿名
项目或手填 Attempt；已经绑定的 Attempt 会恢复其任务详情并禁止重复上传。复核结论初始为空，
reviewer 必须显式选择“已解决”或“未解决”，按钮分别写明“关闭案件”或“继续整改”。

Dashboard 加载前显示 `—`，只有真实汇总成功后才显示“后端数据已同步”；Modal 增加 Tab 焦点
约束，通用 Notice 增加 `role=status/aria-live`。

前端视觉继续使用 Image 2.0 概念图及提示词
[`design/STAGE2_ALPHA6_IMAGE2_CONCEPT.md`](./design/STAGE2_ALPHA6_IMAGE2_CONCEPT.md) 作为信息层级参考；
实际页面不复制概念图中的施工画面、示例数值或存证主张。

## 5. 测试与复审

严格门禁：

- 后端：395 passed，`-W error` 通过；
- 应用代码覆盖率：90.00%，门禁 90%；
- Finding/整改 + 封存 Saga 专项：26 passed；
- `compileall`、`pip check` 通过；
- OpenAPI：92566 bytes，SHA-256
  `758f04938d01f287d9b8d228ac78278fe0daf318100b1c808e09d984ff167fcf`；
- 远端请求/响应 Schema 逐字节检查通过；
- 前端：TypeScript、Vite production build、npm audit 通过；70 modules，
  `dist/index.html` 534.00 kB，gzip 143.55 kB，0 vulnerabilities。

高风险用例包括：

- operational 候选未经分诊不能批准；demo 与运营统计隔离；
- triage CAS、Attempt 幂等、跨项目/错误基线、Attempt 复用；
- 两个并发复验绑定只有一个 202，另一个 409，数据库无孤儿 job；
- resolved、not_resolved、reject 三种回路；
- 源 finding 篡改、closure proof 替换、Attempt 封存后漂移、数据库报告内容漂移均令 readiness 503；
- 新 ZIP 每个成员的 size/SHA、manifest、Merkle、ID 绑定和 8 项 proof 检查；
- 关闭前后的旧 JSON/HTML/ZIP/proof/ledger 行逐字节不变。

本轮由后端完整性、前端产品契约和 QA 三个角色先做只读复审，再分别修复 CAS/闭环图、复验深链
和证据包/旧产物测试。复审仍保留后续项：Alembic、项目级身份/RBAC、生产工单/SLA、整改专属
Saga 故障注入、正式算法与数据评测。

## 6. 本机真实运行态

当前入口：

- 告警与整改：<http://127.0.0.1:5173/alarms>；
- 真实闭环：<http://127.0.0.1:5173/backend-workflow>；
- API 文档：<http://127.0.0.1:8000/docs>；
- readiness：<http://127.0.0.1:8000/api/v1/readyz>；
- 参考服务：<http://127.0.0.1:8012/healthz>，仍是 STUB。

公开 API 演示命令：

```bash
cd backend
python scripts/seed_remediation_demo.py
```

本次运行结果：

- project：`d985286f-7566-4ec1-a734-37bcc16aa404`；
- baseline：`39af28a0-6fd1-43f1-be8b-9c2632300f6d`；
- closed case：`fca86d5b-fc9f-44ba-8f0a-9849d1ebbfa4`；
- Attempt：`cc5a846e-9101-4286-bac6-15a5db12e53d`；
- re-verification job：`1aa472ee-43db-4d0e-9d07-948261058838`；
- closure proof：`7e7f5823-7ec9-46df-918e-24fdd0041376`；
- proof：8/8 true；readiness：ready；
- 页面：2 条 demo 案件，1 条待分诊、1 条已闭环，运营计数均为 0。

浏览器验收：

- 1440×1024 与 375×812 均 `scrollWidth == innerWidth`；
- 深链页同时显示并锁定 Case/Attempt/Project/Baseline；
- 案件详情显示 resolved Attempt、复验 job 和 sealed proof；
- 控制台 0 error。

截图：

- `output/playwright/stage2-alpha8-remediation-desktop.png`；
- `output/playwright/stage2-alpha8-remediation-mobile.png`；
- `output/playwright/stage2-alpha8-remediation-detail.png`；
- `output/playwright/stage2-alpha8-reverification-context.png`。

## 7. 仍未完成

1. 真实 PPE/违章或隐蔽工程算法、授权现场数据和冻结 holdout；
2. 赛题 85%/90% 指标及其正式验证报告；
3. Alembic、PostgreSQL、对象存储、独立 worker 和多副本协调；
4. 真实登录、用户身份、项目/组织 ACL 和审计主体；
5. 通知、SLA、逾期升级和外部工单系统；
6. PDF、证据帧、外部签名/可信时间或第三方存证；
7. 对整改最终化路径执行与 SealOperation 同等级的崩溃/重启故障注入。

因此当前准确表述是：“已完成可运行、可人工分诊、可复验、可检测篡改的本地整改证据闭环”；
不得表述为“自动真实告警已验证”“生产整改平台完成”“区块链存证”或“算法指标达标”。

## 8. 交付包

- `fengmou-zhijian-stage2-alpha8-2026-07-14.zip`；
- 同目录伴随文件：`fengmou-zhijian-stage2-alpha8-2026-07-14.zip.sha256`；
- ZIP 不包含运行数据库、上传证据、运行期报告/proof、API Key、`node_modules`、构建目录、coverage、Python 缓存或日志；保留源码、测试、契约、Image 2.0 概念图/提示词与阶段文档。
- ZIP 摘要不写回 ZIP 内部，避免自引用改变自身字节；以外部 `.sha256` 文件和交付汇报为准。
