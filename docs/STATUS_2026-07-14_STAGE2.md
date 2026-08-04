# 第二阶段推进状态（2026-07-14）

## 本轮结论

第二阶段候选版本已把总览、项目、项目详情、报告和溯源查询从静态展示改为读取真实后端数据，并依据生成的“工业证据运营中心”概念稿完成界面重构。随后补齐了鉴权原始证据回看：服务端先验证受控路径、文件身份、规范 MIME、大小和完整 SHA-256，再从同一文件描述符发送完整内容或单 Range；前端只通过请求头鉴权并管理 Blob URL 生命周期。核心链路仍保持第一阶段的真实性边界。

## 本轮新增

- 新增 `GET /api/v1/projects/{project_id}/overview` 强类型聚合接口，返回项目、进度代理、任务状态、证据、传感器、报告、存证记录和最近活动。
- 监管总览改为真实后端项目数、证据数、待复核任务、档案数、最近任务、项目账本和最新指纹。
- 项目管理支持读取真实项目、真实创建项目和“已批准基线覆盖率（代理指标）”。
- 项目详情改为真实基线、任务、报告和档案视图。
- 报表中心只展示后端封存报告，并支持真实 JSON/HTML 下载。
- 溯源查询支持真实档案编号/摘要检索、8 项完整性重算、复制摘要和证据包下载。
- 告警与整改页已接真实 Finding Case/Attempt API；设备、数据分析、GIS、数据看板和模型服务等未接真实后端的页面继续保留“原型”提示。
- 生成并保存 `docs/design/stage2-operations-center-concept.png` 作为视觉参考稿。
- 新增 `docs/STAGE2_REVIEW_2026-07-14.md`，明确 P0/P1/P2、Gate A-E、Agent 职责和答辩红线。
- 新增 `GET /api/v1/evidence-assets/{evidence_id}/content`，支持 operator/reviewer/auditor、200/206/416、no-store/nosniff，并拒绝路径逃逸、软硬链接、非普通文件、元数据冲突和校验后换包。
- analysis、review 和 proof seal 统一复用安全文件验证；证据包从已验证的同一文件描述符写入，避免校验后按路径二次打开。
- 项目详情和真实闭环页新增服务端视频/图片预览；仅允许固定图片/视频 MIME，切换、关闭和卸载会中止请求并回收 Object URL。
- `remote_http` 外发媒体由编排层保持已验证 fd，并只在同步 analyzer 调用期间绑定；适配器复核 fd identity、名称、SHA、MIME 和大小后直接形成 multipart，不再读取 `storage_path`。
- 前端对登记大小超过 64 MiB 的证据在网络请求前暂停自动加载；必须明确确认完整 Blob 的内存风险，确认会随证据/策略切换原子失效。
- 新增独立 `app.reference_analyzer` 合同参考服务：Bearer 鉴权、模型身份、媒体/请求摘要、大小限制、受保护声明、进程内有界幂等和错误脱敏均 fail closed；默认 predictor 仅返回空 observations 和 STUB limitation。
- 新增远程算法真实性面板：复核备注、任务标签和报告状态只读取持久化任务/报告事实；`remote_http` 显示为“远程单样本推理（未评测）”，`reviewed_non_evaluated` 使用琥珀色状态，人工批准不会被表达为指标升级。
- 新增 `docs/EVIDENCE_PREVIEW_QA.md` 和 `docs/STAGE2_DEMO_GUIDE.md`，记录威胁模型、测试矩阵、演示步骤和当前边界。
- Alpha7 新增持久化 `SealOperation` 封存 Saga：复核快照冻结、同盘暂存/原子发布、进程间
  ledger 锁、幂等恢复、启动补偿和新鲜 readiness 扫描；移动端报告改为真实数据卡片，长报告
  Modal 通过 portal 和 `100dvh` 约束完整留在小屏视口。
- Alpha8 新增 Finding Case/Remediation Attempt 领域链：候选 observation 人工分诊、运营/demo 隔离、负责人/期限、幂等整改、原项目/基线复验、显式 resolved/not_resolved 和新 proof 关闭。
- 复验绑定改为 Attempt + Case 双 CAS；并发请求只有一个成功且失败事务不留下孤儿 job。readiness 新增 case → attempt → job → report → proof → ZIP 冻结快照校验，数据库报告内容也重新渲染并与已封存文件摘要核对。
- 前端告警整改页删除 mock 回退；Attempt 使用深链进入真实闭环页并锁定原项目/基线，关闭决定没有默认值；Dashboard 的在线状态和未加载统计不再伪装成功。
- Alpha9 新增封存提交结果协调、整改关闭跨重启恢复、完成态数据库图核对、稳定的新 proof UUID 与历史 ID 兼容、持久化失败重试分类、completed stale-error readiness 拒绝和统一 `recovery` 合同；前端据此区分分析重试、继续封存与完整性阻断，并对网络失败退避、对 401/403 暂停轮询。
- Alpha10 新增 Algorithm Readiness 0：固定 Construction-PPE ZIP 与 2,844 个解压文件逐字节核对，拒绝内容漂移、额外文件、软/硬链接和同名 stem 覆盖；未签名自述、未验证运行时与非原子 handoff 固定不能产生启动资格。
- 远程响应新增严格 runtime 身份；生产默认只接受 `model/true/capabilities`，参考服务固定 `stub/false/[]`，显式 test/demo STUB 固化为 `synthetic=true` 且不能形成 operational case。

## 本轮验证

- 后端全量：454 passed，`-W error` 通过，应用代码覆盖率 90.62%（门禁为 90%）。
- Algorithm Readiness 专项：27 passed；真实工作副本 2,844 个解压文件与固定 ZIP 逐字节一致，audit 通过；当前 preflight 仍为 `blocked`，未安装、下载、派生或训练。
- 远程、参考服务、集成和合同专项：103 passed；真实 socket STUB smoke 固化为 `remote_contract_stub/synthetic=true`，proof 8/8，运营案件计数为 0。
- 参考服务专项：24 passed；参考服务、远程桥、集成与合同制品组合：59 passed。
- 原始证据与 OpenAPI 专项：56 passed；覆盖 evidence 目录/成员软链接、硬链接、路径逃逸、`lstat -> open` 竞态、摘要后换包、短读、fd 关闭、Range 和鉴权。
- 远程算法、集成与任务恢复专项：37 passed；覆盖缺失/关闭 source、绑定不一致、路径替换后只外发原 fd、双线程 ContextVar/fd 隔离、client factory/上游失败后的 fd 关闭和稳定幂等重试语义。
- OpenAPI、远程算法请求/响应合同、`compileall`、`pip check` 通过。
- 固定 Evaluation v0 示例、development runner 和 development evidence 校验通过；示例 accuracy 仍为 0.50、阈值失败，这是合同负样例，不是模型指标。
- 前端 TypeScript、Vite 生产构建、npm audit 通过，0 vulnerabilities。
- 浏览器实测：总览、项目、项目详情、报告、溯源页面均读取真实 API；H.264/yuv420p 合成视频从服务端鉴权加载，浏览器 `readyState=4`、320x240、1 秒；关闭后 Blob URL 已回收；控制台无应用错误；8 项档案核验全部通过。
- 大文件门禁浏览器实测：把详情响应登记大小临时改为 `64 MiB + 1`，确认前证据内容请求数为 0，点击风险确认后为 1；模拟只改变浏览器响应，不修改数据库或证据文件。
- 真实 HTTP 实测：无 Key 为 401；完整 GET 为 200/3791 bytes 且重算 SHA-256 一致；`Range: bytes=0-15` 为 206/16 bytes，`Content-Range` 正确。
- 空数据库冷启动：在隔离的临时 SQLite/存储目录启动第二个后端，执行 `scripts/seed_stage2_demo.py`，成功新建项目、基线、H.264 上传、分析、复核、报告、档案和 8 项核验；在当前库重复执行则按 SHA-256 幂等复用。
- 参考服务真实进程联调：业务 FastAPI 向独立 Uvicorn 参考服务发送真实 multipart HTTP 请求，合成 H.264 经 `remote_http`、复核、报告、档案和 8/8 核验完成；持久化结果仍为 `remote_model_unvalidated`、`evidence_grade=false`、`accuracy_claim=null`，不计为真实算法 E2E。
- Alpha8 公开 API 整改 smoke：`scripts/seed_remediation_demo.py` 生成合成 finding、人工分诊、整改、复验、关闭与新 proof，8/8 核验为 true；重复执行复用既有闭环。
- Alpha8 浏览器验收：桌面和 375×812 均无横向溢出，真实案件 2 条（1 条待分诊、1 条已闭环），深链页读取并锁定 Case/Attempt/Project/Baseline，控制台 0 error。
- Alpha9 浏览器验收：真实已完成记录读取最新 `recovery=none`，手动刷新返回 200，错误 Key 真实触发 401 并暂停轮询，恢复 Key 后重新读取 200，8 项 proof 核验全部通过；恢复卡片用仅浏览器响应替换预览，未改数据库/制品/ledger；正常启动控制台 0 error，桌面和 375×812 无横向溢出，移动端 `scrollWidth=375`。

## 本地演示种子

当前本地库已创建两条明确标记为 synthetic demo 的完整链路：第一条保留原始闭环记录，第二条用浏览器兼容 H.264 视频复验上传、处理、复核、报告、存证、完整性核验和前端回看。

- 项目代码：`DEMO-STAGE2-001`
- 项目名称：滨江通信管线可信交付示范工程
- 浏览器兼容任务：`1d8996bc-0f39-4980-ad28-e205b1cbfa01`
- 浏览器兼容证据：`b4be4be7-1268-401e-973d-f1ace011c82b`
- 任务状态：2 条 approved
- 档案完整性：最新档案 8/8 检查为 true

这些记录均使用 `demo_fixture` 和无真人几何视频，只用于演示系统闭环与浏览器兼容性，不能作为真实模型结果、真实施工数据或竞赛指标。

Alpha8 整改演示记录：

- 项目：`d985286f-7566-4ec1-a734-37bcc16aa404`；
- 案件：`fca86d5b-fc9f-44ba-8f0a-9849d1ebbfa4`；
- Attempt：`cc5a846e-9101-4286-bac6-15a5db12e53d`；
- 复验任务：`1aa472ee-43db-4d0e-9d07-948261058838`；
- closure proof：`7e7f5823-7ec9-46df-918e-24fdd0041376`，8/8 为 true；
- 范围：`scope=demo`，不进入运营告警统计。

## 当前最高优先级

本轮完成的是工程与展示主链增强。项目的真实 P0 仍然是：合法数据集、唯一 PPE/违章主线、真实非 mock baseline、通信工程事件语义、真实 E2E 和可回溯冻结评测。未获得数据/模型/预算/周期/验收标准等批准前，不启动训练。

远程 STUB 参考服务、真实 HTTP smoke 与前端真实性增量详见
[`STAGE2_ALPHA4_DELIVERY.md`](./STAGE2_ALPHA4_DELIVERY.md)。该增量不改变上述真实 P0 判定。

并发安全显式重试、报告持久化真实性边界、Image 2.0 概念图和移动端修复详见
[`STAGE2_ALPHA6_DELIVERY.md`](./STAGE2_ALPHA6_DELIVERY.md)。Alpha 6 仍不改变真实数据与模型 P0。

可恢复封存 Saga、故障/并发/重启测试、移动报告卡片和当前真实 HTTP/浏览器验收详见
[`STAGE2_ALPHA7_DELIVERY.md`](./STAGE2_ALPHA7_DELIVERY.md)。Alpha 7 仍不改变真实数据、目标模型
和竞赛指标的外部阻塞状态。

Finding/整改/复验证据闭环、并发与篡改复验、真实前端和本机 Alpha8 留证详见
[`STAGE2_ALPHA8_REMEDIATION.md`](./STAGE2_ALPHA8_REMEDIATION.md)。Alpha8 仍不改变真实数据、目标模型和竞赛指标的外部阻塞状态。

整改关闭跨重启恢复、提交确认丢失协调、统一恢复态与显式恢复 UI 详见
[`STAGE2_ALPHA9_RECOVERY.md`](./STAGE2_ALPHA9_RECOVERY.md)。Alpha9 同样不改变真实数据、目标模型和竞赛指标的外部阻塞状态。

数据逐字节审计、只读 pilot 诊断、QA fail-open 修复和远程 model/STUB 身份隔离详见
[`STAGE2_ALPHA10_ALGORITHM_READINESS.md`](./STAGE2_ALPHA10_ALGORITHM_READINESS.md)。Alpha10 仍没有启动训练，也没有产生正式模型或正式指标。
