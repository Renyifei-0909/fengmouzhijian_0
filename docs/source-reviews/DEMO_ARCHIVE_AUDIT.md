# Demo 与归档资料审计报告

审计日期：2026-07-10  
审计范围：`work/extracted/demo`、`work/extracted/archive` 及归档内嵌的 ZIP、RAR、QGZ、GeoPackage、PDF、JPEG。  
约束：只读检查原 Demo 与原归档；临时解包、OCR、渲染和构建均在 `tmp/archive_audit`、`tmp/demo_build_audit` 中完成；未修改主项目代码。

## 0. 结论先行

1. 当前 Demo 是一个可正常打包的 React/Vite 单页前端原型，不是已有系统的前后端项目。14 个路由页面都能展示，导航、筛选、Tab、Modal 等 UI 交互大多可用；但没有任何 HTTP、WebSocket、SSE、文件上传、数据库或浏览器持久化代码。
2. 竞赛要求最核心的闭环“视频输入 -> 算法处理 -> 结构化报告 -> 哈希存证/核验”目前全部未实现。`HiddenAIPage` 只是定时器播放五步动画并展示预置结果；哈希不是合法 SHA-256；报表下载、上链、复制哈希、模型推理等多数按钮只显示成功提示。
3. 溯源页面存在必须立即纠正的逻辑：任意不存在的编号或哈希都会回退到第一条档案，并显示“可信有效”。这会在答辩中形成错误演示，也不能作为后端验收逻辑。
4. 归档资料有实际价值，但不是可直接训练的完整数据集：
   - 6 份规范/流程 PDF，可提炼验收规则、字段和工作流；
   - 13 张现场照片，可做 OCR、分类、结构化抽取和界面演示，但数量太少、只有目录级弱标签，没有视频或框/掩码标注；
   - 一套真实 QGIS/QField 工程，含 14 个 GeoPackage、12,406 条记录、离线采集表单和照片引用，可作为 GIS/现场采集数据模型参考；但含真实地址、姓名、邮箱、电话、精确空间信息和附件路径，不能直接进入公开 Demo、代码仓库或训练集。
5. 后端不应照着 14 个页面平铺开发。第一阶段只做一条可验证的核心链路：项目/点位 -> 视频上传 -> 异步分析 -> 人工复核 -> 报告生成 -> SHA-256 证据包 -> 真伪核验。设备、GIS、告警和驾驶舱是第二阶段；模型编排、弱网边缘同步和真实区块链锚定是第三阶段。

---

## 1. Demo 技术栈与构建状态

| 项目 | 现状 |
|---|---|
| 前端框架 | React 19.2.6 + React DOM 19.2.6 |
| 语言 | TypeScript 5.9.3，`strict: true` |
| 路由 | React Router DOM 7.16，`BrowserRouter` |
| 样式 | Tailwind CSS 4.1.17，通过 `@tailwindcss/vite`；科技蓝自定义主题 |
| 构建 | Vite 7.3.2 |
| 产物模式 | `vite-plugin-singlefile`，CSS/JS 内联进单个 `dist/index.html` |
| UI 基础件 | 自制 SVG 图标、`Modal`、`Notice`、Sidebar、Header；`clsx` + `tailwind-merge` |
| 状态管理 | 仅组件内 `useState`/`useMemo`，无全局状态、查询缓存或 API client |
| 测试/质量 | 无 test、lint、format 脚本；无单元、集成、E2E 测试 |

### 构建验证

- 在临时副本执行 `npm ci && npm run build` 成功。
- 生产产物为单文件 `dist/index.html`，449.82 kB，gzip 119.42 kB。
- `npm audit` 报告 2 个开发工具链问题：1 个 high（Vite 7.3.2 受影响，7.3.6 可修复）和 1 个 low（esbuild 间接依赖）。这些主要影响开发服务器/Windows 路径处理，但仍应在继续开发前升级锁文件并复核。
- `lucide-react` 被声明为依赖但源码未使用；当前图标来自 `src/components/Icons.tsx`。

### 架构证据

- 14 个业务路由集中在 `src/App.tsx:91-104`。
- 项目、设备、告警、报表和看板数据全部来自 `src/data/mock.ts:3-227`。
- 全仓未发现 `fetch`、Axios、WebSocket、EventSource、localStorage、sessionStorage 或文件输入控件。
- 图片依赖多个 Unsplash 热链；断网、域名策略、限流或图片下架都会破坏演示。

---

## 2. 页面与交互逐项审计

Demo 实际有 14 个路由视图；README 所说“13 个页面”是把账户设置和系统设置合并计数。

| 页面/路由 | 当前可用交互 | 实际数据与处理 | 需要接入的后端能力 |
|---|---|---|---|
| 全局 Header / Sidebar | 路由跳转、展开搜索框、通知下拉、侧栏快捷入口 | 搜索无查询逻辑；通知、37 条告警角标、用户信息均写死；账户和退出只 `alert`；时间只在首次渲染计算一次 | 登录态/RBAC、全局检索、未读通知、当前用户、真实退出、聚合角标 |
| `/dashboard` 监管总览 | 跳转 AI、GIS、溯源、项目、告警等页面 | 指标、项目排名、告警流全部 mock；“90%+、3.2s”等能力指标无实验支撑 | `GET /dashboard/summary`、项目进度聚合、告警聚合、任务统计 |
| `/ai-verification` 隐蔽验真 | 三个样例切换、原图/叠加图 Modal、约 3 秒五步动画、数据预览、浏览器打印 | 没有视频/图片上传；没有帧抽取或模型；`setInterval` 仅推进步骤；分析前已显示预置结果；所有结论、测量、置信度、风险、建议和哈希均写死 | 分片/直传上传、媒体元数据、异步任务、进度事件、模型输出、规则判定、人工复核、报告、证据包 |
| `/projects` 项目管理 | 状态筛选、新建项目表单和必填校验、进入详情 | “保存项目”只关闭 Modal 并提示成功，不会把项目加入列表，也不持久化 | 项目 CRUD、分页/筛选、权限、并发版本控制 |
| `/projects/:id` 项目详情 | Tab、设备/报表详情 Modal、打印、跳转 AI | 阶段进度中的设计/验真/验收/交付比例写死；导出、采集上报、生成档案、核验、上链均只提示 | 项目阶段、点位/工序、关联设备/任务/报告/证据、导出任务 |
| `/alarms` 告警中心 | 关键词搜索、已处理筛选、详情 Modal、确认处理 | 确认可修改当前组件内数组，但刷新即丢失；没有处置人、处置时间、意见、整改工单或审计记录 | 告警分页、确认/分派/关闭、处置记录、整改任务、通知 |
| `/devices` 设备监控 | 类型筛选、详情 Modal | 设备状态和心跳固定；批量诊断、导出、远程巡检只提示 | 设备注册、凭证、心跳/遥测、诊断任务、远程命令和结果 |
| `/analytics` 数据分析 | 展示柱状/进度图 | 图表是普通 DIV 根据固定数组绘制；导出只提示 | 时序聚合查询、筛选维度、异步分析导出 |
| `/reports` 报表中心 | 列表、预览 Modal、浏览器打印 | 没有真实 PDF/JSON 文件；下载、导出全部只提示 | 报告生成任务、模板版本、制品存储、带权限下载 URL |
| `/gis-map` GIS 地图 | 点击百分比定位的 6 个点、切换项目、页面跳转 | 背景是 CSS 伪地图；无地图引擎、坐标、矢量层、空间查询或坐标系转换；刷新和快照只提示 | PostGIS/GeoJSON、bbox 查询、图层权限、EPSG 转换、点位/管线/工坑关联 |
| `/data-cockpit` 数据看板 | 日/周/月切换 | 各周期数组、设备占比和告警占比均固定；快照只提示 | 聚合指标、时间范围、快照/报表任务 |
| `/traceability` 溯源查询 | 输入编号/哈希、查看时间线 | 仅 2 条本地档案；不存在的关键词会 `|| archives[0]` 回退，因此任何输入都“命中”；核验不重算摘要；复制按钮没有 Clipboard API；哈希是带 `0x` 和短横线的短字符串，不是 64 位十六进制 SHA-256 | 严格查找、404/未命中、重新计算摘要、签名/时间戳验证、证据时间线、真实下载 |
| `/model-service` 模型服务 | 本地切换模型、启停状态、结果 Modal | 模型版本、精度、吞吐、显存、实例数和推理结果均写死；启停只改本地数组 | 模型注册表、版本、部署、推理端点、健康检查、性能实验记录；MVP 可暂缓 |
| `/settings/account` 账户设置 | 编辑表单、通知开关 | 保存、头像、重置密码仅提示；没有认证和安全流程 | 用户资料、头像对象、密码重置、MFA、通知偏好 |
| `/settings/system` 系统设置 | 模式/日志/保留期和开关可本地修改 | 数据库测试、边缘测试、备份、日志导出、健康检查均返回写死成功文本；保留天数无数值范围校验 | 配置版本、权限、健康检查、审计日志、备份任务；多数属于运维后台，非首版核心 |

### 两个必须在前端也同步修正的行为

1. AI 结果必须只在任务成功后展示，不能在“开始分析”前就展示预置结论。
2. 溯源查询必须有明确的 `not_found` 状态；绝不能把无效输入回退为第一条可信档案。

---

## 3. 现有 Mock 数据模型与缺口

### 当前类型

`src/types/index.ts` 只有以下核心类型：

- `Project`：项目、地点、负责人、状态、进度、日期、参与人数、相机数、告警数、说明。
- `Device`：设备、类型、状态、位置、最后心跳、电量、项目 ID。
- `Alarm`：类型、设备、项目、级别、文本、时间、图片 URL、是否确认。
- `Report`：名称、类型、项目、周期、创建人/时间、字符串文件大小、生成状态。
- `DashboardStats`、`ChartData`：展示聚合值。

这些类型可以保留为前端 DTO 草稿，但不够描述核心验真链路，且部分字段不适合作为数据库事实：

- `Project.progress`、`cameras`、`alerts` 应由阶段/点位/设备/告警聚合，不应多处手工维护。
- `Alarm.image` 应改成 `media_asset_id` 或证据关联，不能长期存外部 URL。
- `Report.size` 应存整数 `size_bytes`；报告内容、模板版本、对象存储 key、SHA-256、生成失败原因均缺失。
- 日期目前是无时区字符串；后端应统一 UTC 时间戳并在前端本地化。
- 负责人、创建人目前是姓名字符串；应引用用户 ID，同时快照保存展示名。
- 所有状态都缺少状态迁移、操作者、原因、版本号和审计事件。

### 必须新增的核心领域对象

| 实体 | 关键字段 | 关系/用途 |
|---|---|---|
| `User`, `Role`, `ProjectMember` | id、tenant_id、role、status、MFA 状态 | 项目级 RBAC；管理员、上传人员、审核人员、只读评委 |
| `Project` | id、code、name、status、owner_id、version、start/end_at | 业务根对象 |
| `Site` / `WorkPoint` | project_id、code、name、geometry、address_masked、work_type、stage | 一次施工点位/工序；媒体、规则和 GIS 的关联锚点 |
| `GISFeature` | layer、geometry、properties、source_crs、source_ref、version | 管线、工坑、设施、地址等空间对象；生产建议 PostGIS |
| `Device`, `EdgeNode`, `DeviceHeartbeat` | serial、type、protocol、site_id、last_seen_at、firmware、status | 感知端和边缘端接入 |
| `UploadSession` | target、parts、expires_at、expected_size、client_digest | 大文件直传/分片、断点续传、幂等 |
| `MediaAsset` | object_key、mime、size_bytes、sha256、duration、width/height、captured_at、GPS、EXIF 清洗状态 | 原视频、关键帧、叠加图、附件；原件只读保存 |
| `AnalysisJob` | input_asset_id、pipeline_version、model_version、status、progress、queued/started/ended_at、error_code | 异步处理任务和可追踪进度 |
| `Finding` / `Measurement` | class、bbox/mask、value、unit、confidence、frame_timecode、source_model | 模型原始结构化输出 |
| `RuleSet`, `Rule`, `RuleEvaluation` | version、jurisdiction、operator、threshold、evidence_refs、result | 把规范规则和模型输出解耦，支持版本化、人工覆核 |
| `VerificationCase` | job_id、overall_result、score、review_status、reviewer、review_note | 一次验真的业务结论，区分模型结果与最终审核结论 |
| `Alarm` | source_type/id、severity、status、assignee、ack_at、closed_at | 从分析/设备/规则自动生成事件 |
| `RectificationTask`, `Reinspection` | finding_id、assignee、deadline、before/after assets、status | 整改闭环和复检 |
| `Report`, `ReportArtifact` | template_version、format、object_key、sha256、status | JSON/PDF/可打印验真单 |
| `EvidencePackage`, `EvidenceItem`, `IntegrityProof` | archive_no、manifest_sha256、signature、timestamp_token、anchor_type/ref、verified_at | 可信交付与真伪核验；链上锚定是可选实现 |
| `ModelVersion`, `Deployment` | artifact digest、metrics、dataset_version、target、status | 模型可追溯；首版可以只读配置 |
| `AuditEvent` | actor、action、target、before/after digest、request_id、occurred_at | 所有关键状态变化不可抵赖审计 |
| `EdgeSyncBatch` | edge_id、sequence、payload_sha256、cursor、status、retry_count | 后续弱网缓存和幂等同步 |

---

## 4. 归档资料递归盘点

### 4.1 顶层及内嵌归档

| 顶层资产 | 递归内容 | 审计判断 |
|---|---|---|
| `ODF NAP documents.zip` | 3 个 PDF | ODF/NAP 规格、布线规范、验收程序；适合规则库和报告字段设计 |
| `Work Instruction Procedures.zip` | 2 个 PDF | 土建/铁塔施工工作指引和 MW-EMS 安装安全指引 |
| `BAKTI项目现场图片.rar` | 13 个 JPEG，BTS 3、Power 4、Tower 3、VSAT 3 | 可做小型演示/测试集；不是训练集；含精确时间位置和站点标识 |
| `ZTV-TKNetz%2021_April.pdf` | 244 页德文 PDF | 德国电信 ZTV-TKNetz 21 规划规范，包含 FTTH、GIS、照片需求点、开挖/Trenching 等内容 |
| `PushPK_Dreieich Mitte_Handwerk.zip` | 14 个 GPKG、1 个 QGZ、1 个冲突副本 QGZ~、1 个 `mergin-config.json` | 完整 QGIS/QField 离线采集工程；价值高但真实数据和隐私风险高 |
| `__MACOSX/*` | AppleDouble 元数据 | 无业务价值，可在后续清理副本中忽略 |

继续递归检查 QGZ：

- 正常 QGZ 内含 1 个约 1.56 MB 的 `.qgs` 项目 XML 和 2 个样式数据库。
- 冲突副本 QGZ~ 也包含同名 `.qgs` 与 2 个样式数据库，是旧版本冲突文件，不应作为权威版本。
- 未发现更深层 ZIP/RAR；QGZ 是本归档的最后一层压缩容器。
- 整个归档没有视频文件。

### 4.2 PDF 资产与可提炼内容

#### A. `3.12 - LCP-NAP Specification.pdf`，16 页

- 元数据标题为 `FTTH ODN Specifications_Issue8.pdf`，页面主要是扫描图，普通文本提取为空，需 OCR + 人工校核。
- 内容是室外光分配箱 LCP/NAP 的结构和材料要求：进缆口、分纤口、熔纤盘位置、主干纤和 drop fiber 独立路由、密封/防水防尘防虫、阻燃/抗 UV、分光器/适配器容量、SC-UPC、色谱、尺寸、保护等级等。
- 可转成“箱体外观/布线检查”规则和标注任务，但 OCR 中的数值、符号和单位存在识别错误，不能未经人工复核直接进入验收规则。

#### B. `3.17 - Technical Guidelines ODF Patching and Cabling.pdf`，6 页

- 明确 ODF 标准容量 216/144/72/24。
- Rack Mount：容量应与线缆容量匹配，布线/跳接方向为从下到上、从左到右。
- Built-in：从上到下、从左到右；新机柜要求前维护、内置理线和紧凑结构。
- 很适合转成结构化检查项：`odf_type`、`capacity`、`cable_capacity`、`patching_vertical_direction`、`patching_horizontal_direction`、`front_accessible`、`cable_management_present`。

#### C. `4 - Acceptance Test Program.pdf`，4 页

- 给出预验收、整改、预测试、正式验收、随机 QA、技术资料提交的完整流程。
- 输入/输出字段包括光功率、损耗、反射、接地、物理布置、竣工图、实际完成量、验收报告、Checklist、OTDR、许可和 GIS 模板。
- 文档给出 feeder/uplink 接续损耗允许值 0.04 dB，以及 ODN 对最远 NAP 的测量示例。原文中的 `22.8 dBm` 表述需由通信专业成员确认量纲和适用上下文，不能机械转写成通用阈值。
- 可直接启发 `VerificationCase` 状态机与报告目录。

#### D. `Work Instruction Procedures-Civil Works.pdf`，35 页

- 覆盖塔基施工和塔体安装：开工交底、定位放样、基坑开挖/验槽、材料、垫层、钢筋、混凝土、预埋件、接地、养护、回弹测试、塔体垂直度等。
- 存在大量可机器化的检查事实，例如：
  - 基坑标高允许偏差 -50 mm，长宽 +200/-50 mm，表面平整度 20 mm；
  - 土方距坑边至少 1 m，堆高不超过 1.5 m；
  - 接地电阻小于等于 1 ohm；
  - 多项尺寸、平整度、垂直度、养护和混凝土工艺阈值。
- 这是最适合构建“规则版本 + 测量值 + 判定 + 证据帧”的资料之一，但它面向特定塔基场景，仍需比赛方/企业导师确认是否属于子赛题 5 的正式验收依据。

#### E. `Work Instruction Procedures-MW EMS.pdf`，113 页

- 主要是 PNMSj+ 在 Windows Server 2008/2008 R2/2012 R2 上的 LAN、SNMP、安装、Firewall、VPN 和系统安全设置。
- 可借鉴最小权限、数据加密、密码策略、端口限制、会话超时、证书、VPN 和 SSH 等原则。
- 具体操作系统和软件版本已明显陈旧，不应作为本项目现代云后端的实施手册或安全基线。

#### F. `ZTV-TKNetz%2021_April.pdf`，244 页

- 确认为 Deutsche Telekom `ZTV-TKNetz 21`，适用于连接网和接入网规划，涵盖 FTTH 规划、PON、光交、微管、入户、Trenching、GIS 和交付文档。
- 明确提到：规划完成以 GIS layers/QGIS 交付；施工前建立带经纬度的“照片需求点”；现场和路径要有照片/草图；非数字化存量管道需要补录。
- 给出德国场景下的高位浅埋开放施工标准深度 45 cm、沟宽不超过 15 cm，以及 Trenching 典型槽宽范围等信息。
- 适合提炼 GIS/照片采集流程与领域词表，但它是德国特定合同规范，不能替代中国法规、国标、行标或本赛题企业标准。

### 4.3 BAKTI 现场照片

文件特征：

- 共 13 张 JPEG；多数为 960x1280，另有 2448x2945、3264x2448、1280x960。
- 场景覆盖 BTS 射频设备、供电柜/仪表读数、塔基/工坑、VSAT 天线和线缆。
- 多张图带肉眼可见的时间、经纬度、方向、站点编号水印；部分文件 EXIF 也保留 GPS、拍摄时间、手机型号和描述。
- 目录名可以作为四类弱标签，但没有逐目标 bbox、mask、关键点、缺陷标签、合格/不合格标签或视频时间段标注。

可用方式：

- UI 和端到端演示的本地样例，替换不稳定的 Unsplash 热链；
- OCR：仪表数值、站点编号、时间/GPS 水印；
- 场景分类/目标检测接口的 smoke test；
- 结构化报告样例的证据图片。

不可直接做的事：

- 13 张图不足以训练或证明模型精度；
- 未提供授权/许可证，不能默认公开、上传第三方模型服务或用于训练；
- 未脱敏前不能进入公开答辩包；站点 ID、坐标、水印、EXIF 均需处理；
- 原图应保留为受控证据，公开展示副本另行去 EXIF、模糊敏感文字并记录派生关系。

### 4.4 QGIS/QField 工程

项目属性：

- QGIS 3.34.15，项目坐标系 EPSG:25832（ETRS89 / UTM zone 32N）。
- 14 个本地 GeoPackage：10 个空间图层、4 个属性表，共 12,406 条记录。
- 另有 OpenStreetMap-DE 与 Hessen WMS 底图。
- QFieldSync 配置为离线采集，存在附件命名规则、`ExternalResource` 图片控件、自动时间/作者、30 秒追踪间隔等移动采集配置。
- 多个值关系仍保存 `C:/Mergin Map/...` 绝对 Windows 路径；照片/文档字段大量指向未打包的相对文件或外部盘符，归档不是自包含数据包。

| GPKG / 图层 | 类型 | 记录数 | 主要字段/业务含义 |
|---|---:|---:|---|
| `Bereich` | Polygon | 14 | NVT/HK 区域、面积、周长 |
| `address` | Point | 843 | 地址、住户/商业数量、施工/线缆状态、材料、住户联系人、入户/竣工附件 |
| `Einmessung` | LineString | 1,155 | 测量线、类型、长度、NVT 区域 |
| `Site_Nummer` | 属性表 | 13 | Site 编号、名称、WBS、单据号 |
| `GBGS` | 属性表 | 839 | 地址状态、下一步、联系人和 SNR 信息 |
| `Infrastructure` | Point | 32 | 设施类型、熔接/预留、施工方、状态、照片 |
| `Fotos` | Point | 18 | RT_ID、三张照片、日期、作者、类型、备注 |
| `PitWork` | Point | 579 | 工坑工法、长宽深、表面、基层、换土、施工方、三张照片 |
| `DT_Materials` | 属性表 | 55 | 材料编号、类型、名称、别名、色码 |
| `Hauseinführung` | LineString | 434 | 入户线、长度、状态、管材、地址 |
| `PO_Liste` | 属性表 | 47 | 分包商、采购单、站点、WBS |
| `Rotberichtigung` | Polygon | 92 | 修正区、页码/区域信息 |
| `Pipes` | LineString | 420 | 工法、表面、槽深/槽宽、材料、管束、状态、文档标记 |
| `Fremdleitung` | LineString | 7,865 | 第三方管线名称和类别 |

重要统计与风险：

- `address`/`GBGS` 中数百条记录含姓名、邮箱、手机和固定电话；这是实质性个人信息，不只是空字段。
- `Fotos` 有 33 个图片引用，`PitWork` 至少有 268 个图片字段引用；大部分实际文件不在 ZIP 内，部分还是外部盘符路径。
- `address` 还有数千个 HBP/EBP/MD/OB/FMT/GBGS/ABP 附件引用，但附件未随包提供。
- `PitWork` 具有真实长宽深数据；`Pipes` 具有槽宽/槽深、工法和文档状态，是很好的 schema/接口样板，但不能直接公开原记录。
- QGZ 含保存用户信息和特定账号可见性表达式；冲突副本说明协同数据治理不完整。

建议用法：

1. 把图层、字段、移动表单和离线同步配置当作领域建模参考。
2. 如果要做导入 Demo，先生成一份完全匿名、裁剪、重投影且附件自包含的派生样例，不要直接导入原包。
3. 后端生产空间数据建议落 PostGIS，保留 `source_crs`、`source_feature_id` 和导入批次；API 面向前端输出 EPSG:4326 GeoJSON 或 vector tiles。
4. 真实联系人/精确位置只能在获得明确授权、确定用途、访问控制和保留期后使用。

---

## 5. 建议后端边界和接口映射

### 5.1 推荐的最小技术边界

适合该项目的首版组合：

- API：Python FastAPI（便于与 FFmpeg/OpenCV/模型推理同语言整合），OpenAPI 作为前后端契约。
- 数据库：PostgreSQL + PostGIS。
- 文件：S3 兼容对象存储（本地可用 MinIO），前端使用预签名 URL 分片直传，API 不转发大视频。
- 异步任务：Redis + Celery/RQ/Dramatiq 中任一成熟队列；任务状态持久化，不用内存定时器。
- 媒体处理：FFmpeg/ffprobe 生成元数据、关键帧和规范化代理文件。
- 报告：结构化 JSON 是事实源，PDF 是版本化模板渲染的派生产物。
- 可信层：SHA-256 + 规范化 manifest + 数字签名/可信时间戳 + append-only 审计；真实区块链仅作为可选 root anchor。

“SHA-256 哈希加密”这一表述应改为“SHA-256 摘要/指纹”。哈希不是加密；文件哈希本身也不能证明是谁、何时提交。可信交付至少还需要：

- 对原始媒体、派生帧、分析 JSON、规则版本、人工审核和报告逐项计算摘要；
- 使用稳定的规范化 JSON（例如 JCS/RFC 8785）生成 manifest；
- 对 manifest root 进行服务端签名，并保存时间戳/锚定回执；
- 每次状态变化写入不可覆盖的审计事件；
- 核验时重新计算摘要并验证签名、时间戳、对象存在性和 manifest 完整性。

### 5.2 核心处理时序

1. 前端创建项目和施工点位。
2. `POST /uploads` 创建上传会话，后端校验文件类型/大小并返回预签名分片信息。
3. 前端直传对象存储，`POST /uploads/{id}/complete`；服务端重新读取对象并计算可信 SHA-256，不接受客户端摘要作为最终事实。
4. `POST /verification-jobs` 创建任务；任务队列执行探测、转码、关键帧、模型推理、OCR、测量和规则评估。
5. 前端用 `GET /verification-jobs/{id}` 轮询或 `GET /verification-jobs/{id}/events` SSE 获取真实进度。
6. 成功后读取结构化输出；审核员可接受、驳回、修改结论并说明依据，所有修改保留模型原值和人工覆核值。
7. `POST /reports` 生成 JSON/PDF；`POST /evidence-packages` 固化原件、结果、规则、审核和报告，生成真实证据编号及 manifest root。
8. `POST /evidence/verify` 必须返回 `valid`、`invalid` 或 `not_found` 以及逐项校验结果，绝不回退到任意档案。

### 5.3 REST 接口草案

#### P0：必须先完成的核心链路

| 方法与路径 | 用途 | 对应页面 |
|---|---|---|
| `POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET /api/v1/me` | 登录、退出、当前用户/RBAC | 全局、设置 |
| `GET/POST /api/v1/projects` | 项目列表和创建 | 项目管理、总览 |
| `GET/PATCH /api/v1/projects/{project_id}` | 详情、状态、版本化修改 | 项目详情 |
| `GET/POST /api/v1/projects/{project_id}/sites` | 施工点位/工序 | 项目详情、GIS、上传 |
| `POST /api/v1/uploads` | 创建上传会话，声明视频/图片/文档 | AI 验真 |
| `POST /api/v1/uploads/{upload_id}/complete` | 完成上传并由服务端校验对象、MIME、大小和 SHA-256 | AI 验真 |
| `GET /api/v1/media/{media_id}` | 媒体元数据；内容使用短期签名 URL | AI 预览、告警证据 |
| `POST /api/v1/verification-jobs` | 基于 media/site/rule_set 创建异步分析 | AI 验真 |
| `GET /api/v1/verification-jobs/{job_id}` | 状态、进度、失败原因 | AI 验真 |
| `GET /api/v1/verification-jobs/{job_id}/events` | SSE 进度与阶段事件 | AI 五步流程 |
| `GET /api/v1/verification-cases/{case_id}` | 结构化结果、证据帧、测量、规则判定 | AI 结果页 |
| `POST /api/v1/verification-cases/{case_id}/review` | 人工接受/驳回/覆核，需 reason 和版本号 | AI 审核 |
| `POST /api/v1/verification-cases/{case_id}/rectifications` | 发起整改 | AI、告警 |
| `POST /api/v1/reinspections` | 复检并关联前后证据 | AI、项目详情 |
| `POST /api/v1/reports`、`GET /api/v1/reports/{id}` | 生成和查询结构化/PDF 报告 | 报表中心 |
| `GET /api/v1/reports/{id}/download` | 权限校验后返回短期下载 URL | 报表中心 |
| `POST /api/v1/evidence-packages` | 固化证据清单、签名和可选锚定 | AI、项目详情 |
| `POST /api/v1/evidence/verify` | 按档案编号、manifest 或上传文件严格核验 | 溯源查询 |
| `GET /api/v1/evidence/{archive_no}/timeline` | 读取真实审计/处理时间线 | 溯源详情 |

#### P1：业务闭环增强

| 方法与路径 | 用途 | 对应页面 |
|---|---|---|
| `GET /api/v1/dashboard/summary?from=&to=` | 项目、设备、任务、告警聚合 | 总览/驾驶舱 |
| `GET /api/v1/alarms` | 分页、筛选、关键字、级别、项目 | 告警中心 |
| `POST /api/v1/alarms/{id}/acknowledge` | 确认，记录操作者、时间、备注和幂等键 | 告警中心 |
| `POST /api/v1/alarms/{id}/close` | 完成整改后关闭 | 告警中心 |
| `GET/POST /api/v1/devices` | 设备注册与列表 | 设备监控 |
| `POST /api/v1/devices/{id}/diagnostics` | 异步诊断任务 | 设备监控 |
| `POST /api/v1/devices/{id}/commands` | 授权远程命令 | 设备监控 |
| `GET /api/v1/gis/features?project_id=&layers=&bbox=` | GeoJSON/bbox 查询 | GIS 地图 |
| `POST /api/v1/gis/imports` | 受控导入 GPKG/GeoJSON，返回校验报告 | GIS 管理 |
| `GET /api/v1/analytics/*` | 时序和分类聚合 | 数据分析/驾驶舱 |
| `GET /api/v1/search?q=` | 项目、设备、告警、档案统一检索 | Header |
| `GET /api/v1/notifications`、`POST /notifications/{id}/read` | 通知和未读数 | Header |

#### P2：展示性强但不应抢占核心链路

| 方法与路径 | 用途 |
|---|---|
| `/models`、`/model-versions`、`/deployments` | 真实模型注册、评估和部署；必须绑定可复现实验指标 |
| `/edge-nodes`、`/edge-sync-batches` | 边缘节点、离线批次、游标、重试和冲突处理 |
| `/system/health`、`/backups`、`/audit-exports` | 受管理员权限保护的运维能力 |
| `/anchors` | 区块链/时间戳服务锚定；失败不能影响本地证据包生成 |

所有改变状态的 POST/PATCH 应支持 `Idempotency-Key`；资源更新应使用 `version`/ETag 避免覆盖；任务和上传均应有配额、超时、取消、重试和失败原因。

---

## 6. 可复用、需改造与应重写

### 可直接复用

- 全局科技蓝视觉、Sidebar/Header、响应式框架、路由层级。
- `Modal`、`Notice`、自制图标、类名工具和大部分展示卡片。
- 项目、设备、告警、报表的页面信息架构。
- AI 五阶段可视化骨架；改为由后端任务事件驱动即可。
- `Project`/`Device`/`Alarm`/`Report` 类型可作为 OpenAPI DTO 命名草案。
- QGIS 表字段、现场照片附件表单、离线采集思想可作为后端实体设计参考。

### 需要中度改造

- 所有页面从直接 import `mock.ts` 改为统一 API client + Query cache；不要在页面里散落 fetch。
- 表格增加服务端分页、加载/空/失败/权限态；大表在移动端需要横向滚动或卡片布局。
- Header 搜索、通知、账户入口改为真实路由和接口。
- 项目创建、告警确认、模型启停等乐观更新必须支持回滚和错误提示。
- 把外部图片替换为授权的本地/对象存储资产，并为证据图片建立原件/脱敏派生关系。
- 前端 DTO 建议从 OpenAPI 自动生成，减少状态枚举漂移。

### 应重写或替换

- `HiddenAIPage` 的 `sceneConfig`、定时器伪推理、硬编码测量和短哈希。
- `TraceabilityPage` 的档案数组、fallback 查询、无条件“校验通过”和假复制。
- `GISMapPage` 的 CSS 地图和百分比点位；替换为 MapLibre GL/Leaflet 等真实地图与空间 API。
- 所有“下载/导出/备份/推送/上链成功”但没有制品或回执的按钮。
- 模型精度、时延、吞吐、显存等没有实验来源的数字；必须来自版本化评测记录，否则删掉或显式标注“目标值”。
- 账户/系统安全功能不能用纯前端开关模拟真实生效。

---

## 7. 风险清单

### P0：影响可信性、隐私或核心演示

1. **无核心数据链路**：无视频输入、处理任务、结构化输出文件、真实哈希或核验。
2. **伪 SHA-256/伪上链**：当前指纹长度和格式不合法，按钮也没有存证回执。答辩前必须删除虚假表述或接入真实实现。
3. **任意查询都有效**：溯源 fallback 会把无效编号显示成可信档案，属于严重业务逻辑错误。
4. **真实数据泄露**：QGIS 包含大批联系人、地址、电话、邮箱、精确空间数据；BAKTI 图片含 GPS/时间/站点水印和 EXIF。
5. **来源和授权未知**：现场照片、企业规范、德国电信资料和 QGIS 工程没有随包提供可公开/可训练许可证。
6. **模型指标无证据**：90%+、3.2s、吞吐等是硬编码；在比赛材料里必须标为“目标/示意”或用可复现实验替换。
7. **归档附件缺失**：QGIS 中大量照片/PDF 只有路径引用，无法构成完整证据链或可复现实验包。

### P1：上线和后续开发风险

1. 无认证、租户/项目隔离、RBAC、审计、限流、上传配额、病毒/恶意文件检查。
2. BrowserRouter + 单 HTML 在非根路径或未配置 SPA fallback 的服务器上刷新可能 404；部署时必须配置回退或评估 HashRouter。
3. 视频上传若经过 API 进程会阻塞并放大内存/带宽风险；应对象存储直传。
4. 无幂等和并发控制，重复点击/重试会重复创建任务、报告或证据包。
5. 无任务失败、取消、重试、超时和可观察性；不能只展示永远成功的五步动画。
6. 外部 Unsplash 热链使离线答辩和可信证据不可控。
7. 归档规范来自不同国家、项目和年份；只能作为资料样例，不能混成一个“统一验收标准”。规则必须记录来源、版本、适用地区和批准人。
8. 扫描 PDF OCR 存在符号/小数/单位错误；量化阈值必须双人复核并保存页码证据。
9. MW-EMS 文档基于旧版 Windows Server 和旧软件，照抄会形成过时安全配置。

### P2：产品完整性与维护性

1. README 声称“每个页面完整开发、无死链、所有按钮可用”与事实不一致，应改成“高保真交互原型”。
2. 项目/设备/告警日期集中在 2026-01，与当前 2026-07 不一致；答辩数据要有统一可解释的演示时间轴。
3. 表格缺少分页和移动端处理，部分窄屏会溢出。
4. 成功和校验失败共用 `Notice type="success"` 等语义问题；需要 error/warning/loading/empty/not-found 状态。
5. Header 时间不会自动刷新，账户入口文案与实际行为不一致。
6. 未使用依赖、无测试和缺失 lint 会增加快速并行开发中的回归概率。

---

## 8. 建议执行顺序

### 第 1 个可交付切片：真实的“单视频验真”

验收标准：

1. 可创建一个项目和点位；上传一个本地 MP4。
2. 后端保存原视频、提取元数据/关键帧并计算真实 SHA-256。
3. 即使算法还未完成，也先接一个确定性 baseline 处理器，输出有 schema 校验的 JSON；不能用页面硬编码结果。
4. 前端五步进度来自任务事件；失败可见并可重试。
5. 审核员确认后生成 JSON + PDF；两个制品都有摘要。
6. 生成 manifest 和档案编号；篡改任意文件后核验必须失败；不存在编号必须返回未找到。
7. 自动测试覆盖正常上传、重复请求、非法类型、任务失败、无效档案、篡改文件。

### 第 2 个切片：整改闭环 + 报表/告警

- 规则命中生成告警；分派整改；上传整改后视频；复检；报告更新但保留旧版本；证据时间线可追溯。

### 第 3 个切片：GIS 和现场采集

- 用完全脱敏的小型 GeoJSON/GPKG 派生数据演示点位、管线、工坑和媒体关联；再实现移动端离线队列与恢复同步。

### 暂缓

- 真实模型在线编排、多集群边缘下发、全功能系统设置、真实公链。除非核心链路稳定且有明确答辩必要，否则这些页面保留为“规划能力”而不是伪装成已实现能力。

---

## 9. 本次审计未做的事情

- 未修改或删除原 Demo、原 ZIP/RAR/PDF/GPKG/QGZ。
- 未把任何真实联系人、完整地址或精确坐标复制到本报告。
- 未验证资料的版权授权、企业保密级别或是否允许用于训练；这需要项目负责人向提供方书面确认。
- 未对 13 张图做模型训练，也未把 PDF 中的阈值认定为本赛题的最终技术标准。
- 未修改主项目代码；本报告仅给出可执行的接口和数据建模依据。
