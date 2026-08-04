# 子赛题 5 需求追踪矩阵

更新日期：2026-07-31

工程基线版本：0.2.0（Alpha18 叠加 QGIS 工单垂直切片骨架）

本矩阵把比赛 PDF、咨询回复、方案 PPT 和现有 Demo 映射到可运行功能、测试和材料证据。状态只使用“已实现、部分实现、未实现、外部阻塞”，避免把方案愿景当作完成成果。

> **2026-07-31 产品主线冻结**：见 `docs/algorithm-data/ADR_QGIS_WORKORDER_COMPLIANCE_2026-07-31.md`  
> 系统收口为「QGIS/GeoPackage 工程对象驱动的工单式施工合规验真」。  
> Alpha18 交付：设计包导入、EngineeringObject、WorkOrder、GPS 空间校验、观察/合规分离规则引擎（合成样例可演示）。  
> **不是** 真实算法 90% 达标，**不是** 完整 GIS 平台，**不是** 防作弊。

## 1. 官方与补充材料要求

| ID | 要求 | 来源 | 当前状态 | 已有证据 | 后续验收 |
|---|---|---|---|---|---|
| R-01 | 以通信工程施工场景为主 | 比赛 PDF 第 3 页；咨询 DOCX 第 1 页 | 已实现边界 | 项目、工点、工序和设计基线均为通信管线语义 | 真实数据集和最终 Demo 不得退化为普通道路/安防场景 |
| R-02 | 视频、图片等现场数据输入 | PDF 第 3 页；DOCX 第 2–3 页 | 已实现 MVP | Multipart 上传、扩展名/MIME/文件签名校验、大小限制、服务端 SHA-256；鉴权原件回看在路径/链接/大小/摘要验证后从同一 fd 发送，支持单 Range | 增加分片/断点续传、恶意文件扫描、对象存储、生产级受控流播放 |
| R-03 | 多源感知 | PDF 第 3 页 | 部分实现 | 视频/图片 + 设计基线 + 传感器事件 API | 向组委会确认最低定义；接入第二个真实来源或匿名化样例 |
| R-04 | 与数字化设计模型动态对齐 | PDF 第 3 页；DOCX 第 2 页强调 QGIS、不要求 CAD | 部分实现 | Alpha18：脱敏/合成设计包导入 → EngineeringObject → WorkOrder 冻结设计/几何/规则快照；工单上传绑定 task；CRS 25832↔4326 与可配置 GPS 容差；后端规则引擎比对 expected/observed；前端 `/gis-map` + `/work-orders/:id` 接真实 API（SVG 几何预览、空间卡片、合规结论） | 完整 QGIS 图层生态、自动配准、生产 GPKG 原件链路、瓦片地图引擎仍未完成 |
| R-05 | 施工进度实时跟踪 | PDF 第 3、6 页 | 部分实现 | 基于已批准基线的进度代理接口 | 当前不是直播流；需定义正式工序、延迟口径和进度事件 |
| R-06 | 工艺偏差与违章自动识别预警 | PDF 第 3 页；DOCX 第 3 页 | 未实现算法 | 统一严格 analyzer 合同与默认关闭的 `remote_http` 桥已就绪；外发媒体贯穿已验证 fd，不再按路径重开；PPE 数据/许可调研和标签/指标 v0 已完成 | 当前暂定推荐 person/PPE/轨迹级事件；仍需团队签署、授权现场视频和真实 baseline；不得用 fixture 替代 |
| R-07 | 隐蔽工程影像结构化分析与验真 | PDF 第 3、6 页 | 未实现正式算法 | stub 不生成物理量；demo_fixture 明确标记非证据；远程上游不能写入 `evidence_grade` 或 accuracy claim | 需要标签规范、相机/标尺条件、真实模型、人工复核和冻结评测 |
| R-08 | 结构化输出报告与告警统计 | DOCX 第 3 页；PPT 第 9–12 页 | 已实现本地整改、调度诊断与指标导出 MVP | JSON 事实源、可打印 HTML、真实任务/报告/证据统计 API；finding 候选持久化、reviewer 分诊、运营/demo 隔离、整改 Attempt、锁定原项目/基线复验、resolved/not_resolved、proof 关闭与闭环图完整性扫描；鉴权的 queued/lease/dead-letter/attempt 聚合按 `attention/incident` 分层且不返回标识；Prometheus text 0.0.4 只输出固定枚举标签，异常任务状态收敛为 `other`，异常状态/disposition 均 fail closed | 增加 PDF、证据帧、项目级真实身份/RBAC、逾期通知；在真实 PostgreSQL 上做抓取基准并部署 Prometheus、外部告警和值班/工单集成；候选 finding 不得称自动真实告警，单个 exporter 不得称 SLA/生产监控 |
| R-09 | 不可篡改数字化交付档案 | PDF 第 3 页 | 已实现本地完整性 MVP | 原件/分析/基线/传感器/复核/报告 ZIP；manifest、SHA-256、Merkle Root、本地哈希链；持久化 SealOperation、原子文件发布、幂等 ledger、启动恢复、提交确认协调与整改跨重启原子关闭测试；非正式 controlled-local Ed25519 验签 | 外部可信时间/第三方存证未实现；本机 Saga 不是分布式事务；不得称区块链、司法存证或可信时间戳 |
| R-10 | 档案真伪核验 | DOCX 第 2–3 页；PPT 第 13 页 | 已实现 MVP | API 和独立 CLI 均逐项重算；不存在档案返回 404/空列表；auditor API key 可只读核验；损坏归档拒绝下载 | 增加核验证书、Ed25519 签名和外部可信时间 |
| R-11 | 违章识别准确率 ≥85%，或影像结构化准确率 ≥90% | PDF 第 6 页 | 外部阻塞 | 系统明确不生成 accuracy claim；已实现独立事件窗 schema、完整性/隔离门禁、固定分母评分 CLI、Wilson 区间和 mock 拒绝；所有结果固定 `compliance_claim_eligible=false` | 仍需官方样本单元/公式答复、唯一主线签署、授权冻结数据、真实模型执行、受控盲测 registry 和 QA 签批 |
| R-12 | 可运行程序化成果 | PDF 第 4–6 页 | 已实现第二阶段 Alpha16 工程候选 + Alpha17 Windows portable PG 验收；未完成 Linux/Docker 发布复验 | FastAPI、真实数据总览/项目/报告/溯源/证据回看/告警整改页、可恢复封存 Saga、持久化 worker 租约、追加式 attempt/outcome、统一 recovery UI、不含标识的调度聚合面板与低基数 Prometheus exporter、Alembic `20260731_0003` head（含 0002 attempt 账本与 Alpha18 工单表）、API/Worker 启动门禁、Alpha11/Alpha13 旧 SQLite 显式接管、远程算法参考服务、Evaluation v0 CLI、development runner、unsigned evidence、一次性 registry、Ed25519 controlled-local verifier、OpenAPI 制品；后端 uv 0.11.32 通用锁与 Windows clean sync/wheel smoke；Alpha17：20 项无 PG 单测 + **便携 PostgreSQL 17.10 实跑返回码 0**（并发迁移、8 worker、fencing、append-only 23000、metrics/readyz、schema 清理；报告 `output/postgres-acceptance-run.json`）；Compose/Docker 与 Linux 90% 门禁仍未验证；Alpha11 历史 476 passed / 90.12%；Alpha16 Windows 全量 153 failed / 317 passed / 31 skipped；当前 Windows 子集（含 ffprobe、deselect 10 FS 特权用例）为 **317 passed / 10 deselected**；OpenAPI `8a3d6c…7cc42`；前端原型页假成功路径已工程收口 | Linux clean runner locked sync + 全量 `-W error` + 90% coverage；Docker Compose cold start；更长 soak 后再评估 SKIP LOCKED；Prometheus 外部栈；真实算法 E2E |
| R-13 | 技术方案文档 | PDF 第 4–5 页 | 部分实现 | 本目录中的审阅、架构、计划和交接文档 | 8 月 30 日前形成参赛版正文，需指导老师/企业导师复核 |
| R-14 | 验证报告 | PDF 第 5 页 | 未形成参赛版 | 当前测试与冒烟证据可作为框架输入 | 固定环境、对象、方法、指标口径、结果和失败案例 |
| R-15 | 对比实验与原始支撑 | PDF 第 5–6 页 | 外部阻塞 | 归档材料已盘点，但不是合法完整训练集 | 算法/数据负责人建立数据清单、许可证、真值、基线和测算脚本 |
| R-16 | 演示视频、操作说明和截图 | PDF 第 5 页 | 部分实现 | 可运行图形化联调页、`STAGE2_DEMO_GUIDE.md` 和浏览器证据回看截图 | 功能冻结后录制真实输入—处理—复核—报告—篡改失败故事线 |

## 2. PPT 愿景与实施降级

| PPT 能力 | PPT 页 | 当前处理 | 原因 |
|---|---:|---|---|
| 驾驶舱 | 9 | 总览、项目、报告、溯源、闭环与告警整改读取真实 API；设备、数据分析、GIS、模型服务、隐蔽 AI、账户/系统设置均为原型且已去假成功动作 | 继续按页面登记事实源，禁止用原型指标替代 API 数据 |
| 隐蔽工程验真 | 10 | P0 主链；正式算法未接入 | 与咨询回复的输入—处理—输出主线最一致 |
| GIS/CAD 图物对齐 | 11 | QGIS/基线关联列为 P1；不做 CAD 精准配准 | 咨询回复明确不需要 CAD，主要参考 QGIS |
| 多模态违章预警 | 12 | 暂定推荐为 85% 主线；等待两日门禁/团队签署 | 首版只做安全帽事件，必须绑定项目/工点/工序/基线；公开 mAP 不冒充现场 accuracy |
| 哈希/区块链溯源 | 13、17 | 只实现并宣传哈希完整性核验 | 原 PPT 术语和示例哈希错误，尚无真实链、签名或时间戳 |
| 模型编排/OTA/边缘盒子 | 14–15 | P2 | 不应抢占真实算法、评估和主链稳定性资源 |

## 3. 真实性纠偏

- 原 Demo 的 `90%+`、`3.2s`、`98%`、`60%+`、`100% 可信`均没有实验支撑，不能写入最终结果。
- 原 Demo 的短 `0x...` 字符串不是标准 64 位十六进制 SHA-256。
- 原溯源页把无效输入回退到第一条“可信档案”的逻辑已在交付副本中修正。
- 原 AI 页已增加静态原型标记；真实链路集中在 `/backend-workflow`。
- `SHA-256`是摘要函数，不是加密；当前实现是“篡改可检测”，不是“绝对不可篡改”。
- 当前已有独立数据库轮询 worker、租约/fencing、追加式 attempt/outcome、请求时聚合诊断与低基数指标导出；SQLite 仍只允许本机单 worker，外部调用仍是 at-least-once，且 Prometheus Server、外部告警、值班闭环和抓取容量验证尚未完成，不等于多副本生产队列、exactly-once 推理、SLA 或生产可观测性。
- uv 通用锁与 PyPI SHA-256 固定了解析图和下载字节，不等于已在所有平台运行，也不提供
  发布者签名、SBOM、SLSA provenance、恶意代码审计或位级可复现 wheel。
- `remote_http` 默认关闭且真实目标算法尚未接入；已生成合同制品只证明接口与边界可验证，不证明模型能力。
- 单目图像若没有相机标定、标尺或可信参照物，不能可靠输出工程级精确埋深/间距。
- 2026-07-28 原生 Windows 已跑通图片上传、任务、人工复核和本地封存，但 Evaluation v0、
  Algorithm Readiness 0、POSIX 链接/TOCTOU 测试和目录 `fsync` 仍必须在 Linux 环境验收；
  Windows 部分回归不能替代历史或未来的 Linux 覆盖率门禁。

## 4. 尚需书面确认

1. PDF 第 6 页“或”的最低合规解释；
2. 多源感知是否要求两种现场硬件；
3. 85%/90% 的样本单元和官方计算口径；
4. 是否提供施工视频、标签或评测数据；
5. “实时”延迟/并发/硬件要求；
6. 哈希 + 签名 + 时间戳是否满足“不可篡改”预期；
7. 纸质试点证明是否强制；
8. 作品运行、模型权重、源码和第三方许可证的提交边界。

在线金山文档链接在 2026-07-10 后续重试中已可只读访问：当时页面 UI 显示 6,780 字，本次只稳定核对了目录以及正文开头的背景、痛点和五方向目标。WPS 页面采用延迟渲染，没有逐段导出全文；观察摘要见 `docs/source-reviews/WPS_ONLINE_REVIEW_2026-07-10.md`，具体算法参数仍以团队后续导出的版本化文件和已逐页审阅的 PDF/PPTX/DOCX 为准。
