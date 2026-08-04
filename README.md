# 多源感知赋能端边云协同的智能监管与可信交付系统

**产品名：锋眸智鉴**　|　**版本：0.2.0（Stage2 Alpha18）**

---

## 一、项目背景与系统定位

随着 5G-A 规模化商用、算力网络全域布局与千兆光网深度覆盖，通信基础设施建设呈现出**点位分散、线路跨度大、野外场景多、隐蔽工序多、建设标准严苛**的新特征。传统通信工程监管模式过度依赖人工巡查、纸质记录与事后抽检，长期存在**监管盲区多、隐蔽工程数据黑箱、竣工资产账物不符、全生命周期溯源困难**等核心行业痛点。

本项目**《多源感知赋能端边云协同的智能监管与可信交付系统》**（锋眸智鉴）面向通信基建施工场景，构建覆盖**现场采信 → 后台智算核验 → 人工复核 → 结构化报告 → 哈希证据封存 → 审计追溯**的数字化监管与可信交付能力。

当前仓库为**后端优先的全栈 MVP（0.2.0）**：在原高保真前端 Demo 之上，落地了可运行的真实数据链路，并将产品主线收口为：

> **QGIS / GeoPackage 工程对象驱动的工单式施工合规验真**

系统统一采用**科技蓝（Tech Blue）**视觉规范，前端提供可交互监管界面，后端提供鉴权 API、任务调度、证据封存与完整性核验。

---

## 二、核心痛点与本系统解决方案

| 传统通信工程痛点 | 传统模式瓶颈 | 本系统应对策略 |
| :--- | :--- | :--- |
| **监管存在盲区与滞后** | 露天/野外长线路施工，固定监控难以全覆盖；人工上报易瞒报、误报与延迟。 | **多源采信 + 后台批处理核验**：支持视频/图片上传、任务排队与 Worker 执行；前端提供监管总览与联调闭环页。 |
| **隐蔽工程数据黑箱** | 管线敷设、沟槽开挖等工序完成后即被覆土，二次核查成本高、易造假。 | **影像结构化观察 + 后端规则合规引擎**：分析适配器输出观察结论，合规判定与模型观察解耦；支持人工复核与整改复验。 |
| **竣工资产失真、难溯源** | 普通电子文档可篡改、易替换、无唯一标识。 | **SHA-256 / Merkle 证据包 + 本地哈希链**：原始证据、分析结果、复核与报告一并封存，支持 API/CLI 双重完整性核验。 |
| **图物脱节、工单难闭环** | 设计图纸与现场施工对象割裂，过程资料难绑定工程对象。 | **设计包 / 工程对象 / 工单 / GPS 空间校验**：导入设计基线，冻结工单规则与容差，现场采集位置可解释比对。 |
| **弱网与系统可靠性要求高** | 野外网络波动导致任务中断、状态不可恢复。 | **租约 Worker、尝试账本、封存 Saga 与恢复合同**：任务可重试/可继续封存；演示环境支持 SQLite，验收路径含 PostgreSQL。 |

> **真实性说明**：当前为可运行 MVP，不是完整生产系统。默认 `stub` / `demo_fixture` 仅用于链路演示；真实目标视觉模型、赛题 85%/90% 指标、区块链司法存证与 RTSP 直播监管尚未作为已验证能力宣称。详见下文「能力边界」。

---

## 三、整体架构与 UI 设计规范

### 1. 统一全局科技蓝（Tech Blue）视觉标准

- **配色体系**：以深蓝色（`#07111f` ~ `#0d1b36`）为侧边栏基底，科技湛蓝（`#1677ff` / `#0958d9`）为主色调，天蓝/青蓝（`#38bdf8` / `#06b6d4`）为高亮点缀，辅以浅灰背景（`#f8fafc`）与纯白卡片。
- **布局层级**：统一的大圆角卡片、微阴影、毛玻璃顶栏与平滑页面过渡动画。
- **多端适配**：桌面端全屏布局；小屏下提供顶部快捷导航（`MobileQuickNav`），保证核心操作可点击可达。

### 2. 端—边—云协同目标架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                             感知端 (Sensing Layer)                       │
│  [现场影像/视频上传]   [设备 GPS 定位]   [传感器事件]   [设计包/GPKG 导入]  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ 证据入库 / SHA-256 摘要
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                             边缘/后台执行 (Execution Layer)              │
│  [Worker 租约调度]  [分析适配器 stub/demo/remote]  [合规规则引擎]         │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ 观察结论 / 任务状态 / 失败恢复
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                            核心云端 (Cloud / API Layer)                  │
│  [FastAPI 业务 API]  [人工复核/整改]  [报告生成]  [Merkle 证据封存]        │
│  [审计追溯]  [调度可观测性]  [Prometheus 指标导出]                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3. 当前工程技术栈

| 层级 | 技术选型 |
| :--- | :--- |
| 前端 | React 19、React Router 8、Vite、Tailwind CSS 4、MapLibre GL |
| 后端 | FastAPI、SQLAlchemy、Alembic、uv 依赖锁 |
| 数据 | 开发默认 SQLite；PostgreSQL 验收路径已具备 |
| 交付 | Docker Compose 本地双服务；结构化报告 + ZIP 证据包 |
| 版本 | 应用 **0.2.0**；数据库迁移 head **`20260801_0004`** |

---

## 四、系统功能模块详解

系统前端保留并演进了原 Demo 的主要业务页面，同时新增**核验中心、工单采集、GIS 真实 API**等闭环能力。商业主导航侧重：

| 导航 | 路由 | 定位 |
| :--- | :--- | :--- |
| 项目总览 | `/dashboard` | 工程进度与核验概况 |
| 项目管理 | `/projects` | 在建工程与工点管理 |
| 工程作业 | `/gis-map` | 工程对象、施工工单与现场核验 |
| 核验中心 | `/backend-workflow` | 资料提交、人工复核与交付归档 |
| 整改中心 | `/alarms` | 偏差分诊、整改与复验 |
| 报告中心 | `/reports` | 结构化报告与交付材料 |
| 审计追溯 | `/traceability` | 档案核验与链路查询 |

### 1. 项目总览（`/dashboard`）

- 展示在建项目、核验任务与关键运营概况。
- 提供进入项目管理、工程作业、核验中心等核心入口。
- 与后端 dashboard / 项目聚合接口对齐（非纯装饰看板）。

### 2. 项目管理与项目详情（`/projects`、`/projects/:id`）

- 创建与查看通信基建项目、设计基线版本摘要。
- 项目详情支持任务列表、媒体证据鉴权回看、报告与进度视图。
- 支持视频（MP4/MOV/AVI/MKV/WebM）与图片（JPG/PNG）上传；服务端校验 MIME/大小并计算 SHA-256。

### 3. 工程作业 / GIS 工作台（`/gis-map`）

- **MapLibre** 展示工程对象 GeoJSON（Point / LineString / Polygon）。
- 设计包导入（合成 JSON / 受控 GPKG 路径）。
- 创建工单：冻结设计、几何、规则与空间容差。
- 离线空白底图可运行；可选在线样式 `VITE_MAP_STYLE_URL`。

### 4. 工单采集（`/work-orders/:id`）

- 选择工单 → 提交现场资料 → GPS 空间校验 → 合规结论。
- 支持浏览器定位或合成演示定位（合成必须保留 `synthetic` 标识）。
- 空间规则：**距离 ≤ 容差** 且精度门禁通过；**不以“容差 + 精度”放宽通过条件**。

### 5. 核验中心（`/backend-workflow`）

真实闭环联调页，覆盖完整交付链路：

1. 创建项目 / 绑定设计基线  
2. 上传影像或视频  
3. 后台分析任务（Worker 租约领取与执行）  
4. 人工复核  
5. 结构化 JSON / 可打印 HTML 报告  
6. SHA-256 + Merkle 证据包封存  
7. 完整性核验与下载  

### 6. 整改中心（`/alarms` 及相关 API）

- Finding Case：观察候选 → 人工分诊 → 整改 Attempt → 锁定原项目/基线复验 → 关闭。
- 整改与运营统计隔离 demo 案件；并发绑定采用版本/CAS 防护。

### 7. 报告中心与审计追溯（`/reports`、`/traceability`）

- 报告列表、详情与下载前完整性校验。
- 证据包下载、哈希核验、审计事件与链路追溯视图。

### 8. 隐蔽验真 / 数据看板 / 设备 / 模型等页面

- 保留原 Demo 的交互与科技蓝视觉能力，部分页面仍含演示型文案或 mock 数据。
- 正式核验与交付请以**核验中心 + 工单/GIS + 后端 API**为准。

### 9. 后端核心能力（API）

- **鉴权**：`X-API-Key` 区分 operator / reviewer / auditor；未配置密钥拒绝业务操作。
- **任务与 Worker**：一对一租约、心跳续租、过期回收、generation fencing、有限重试与死信。
- **尝试账本**：追加式 attempt/outcome，防止旧结果覆盖。
- **分析适配器**：`stub`、`demo_fixture`、`remote_http`（默认关闭）。
- **封存 Saga**：暂存、原子发布、启动补偿与 readiness 完整性扫描。
- **可观测性**：调度聚合快照、Prometheus text 指标导出。
- **数据库**：Alembic 迁移与启动门禁；当前 head `20260801_0004`。

---

## 五、快速上手与部署指南

### 1. 环境准备

| 组件 | 建议版本 |
| :--- | :--- |
| Node.js | **≥ 22.22.0**（React Router 8.3.0 要求） |
| Python | **≥ 3.11**（推荐 3.12） |
| 其他 | Docker（可选）；视频分析链路需要 `ffprobe`/`ffmpeg` |

### 2. Docker Compose（最快演示）

```bash
docker compose up --build
```

启动后访问：

| 入口 | 地址 |
| :--- | :--- |
| 前端 | http://127.0.0.1:5173/backend-workflow |
| API 文档 | http://127.0.0.1:8000/docs |
| 健康检查 | http://127.0.0.1:8000/api/v1/readyz |

空库可执行演示种子（合成样例，非真实现场指标证据）：

```bash
python backend/scripts/seed_stage2_demo.py
# 商业化工单样例（可选）
python backend/scripts/seed_alpha18_commercial.py
```

> Compose 演示默认打开 `demo_fixture`，仅用于本地演示，正式环境应关闭。

### 3. 本地开发

**后端：**

```bash
cd backend
ffprobe -version
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads

# Windows
.\.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

# 设置演示密钥（务必替换）
set FENGMOU_ALLOW_DEMO_ANALYZER=true
set FENGMOU_OPERATOR_API_KEY=local-operator-change-me
set FENGMOU_REVIEWER_API_KEY=local-reviewer-change-me
set FENGMOU_AUDITOR_API_KEY=local-auditor-change-me

uvicorn app.main:app --reload --port 8000
```

如需 API 与 Worker 分离（`external` 模式）：

```bash
# 终端 1
uvicorn app.main:app --host 127.0.0.1 --port 8000
# 终端 2
python -m app.worker
```

**前端：**

```bash
cd frontend
cp .env.example .env.local
# 将 .env.local 中的 API Key 改成与后端一致
npm ci
npm run dev
```

Vite 默认将 `/api` 代理到 `http://127.0.0.1:8000`。

### 4. 常用验证

```bash
# 后端测试（Linux 全量门禁参考；Windows 见 scripts/run_windows_backend_subset.ps1）
cd backend
python -m pytest -q

# 前端类型检查 + 单测 + 构建 + audit
cd frontend
npm run verify

# 独立校验证据包
python backend/scripts/verify_bundle.py path/to/ARC-xxx.zip \
  --expected-archive-sha256 <API返回的archive_sha256>
```

---

## 六、完整项目目录结构

```text
.
├── README.md                          # 本说明文档
├── Makefile                           # 常用开发/契约/评测命令
├── compose.yaml                       # 本地前后端 Compose 演示
├── compose.postgres-acceptance.yaml   # PostgreSQL 验收辅助编排
├── .gitignore
│
├── backend/                           # FastAPI 后端
│   ├── app/
│   │   ├── main.py                    # 应用入口
│   │   ├── api/                       # 路由（含工单）
│   │   ├── services/                  # 分析、合规、GPKG、封存、工单等
│   │   ├── evaluation/                # Evaluation v0 离线评分核
│   │   ├── migrations/                # Alembic 迁移
│   │   ├── worker.py                  # 外部 Worker 进程
│   │   └── ...
│   ├── tests/                         # 后端测试
│   ├── scripts/                       # 种子、验收、依赖校验脚本
│   ├── pyproject.toml
│   ├── uv.lock
│   └── Dockerfile
│
├── frontend/                          # React 前端
│   ├── src/
│   │   ├── App.tsx                    # 路由与 AppShell
│   │   ├── pages/                     # 业务页面
│   │   ├── components/                # 侧栏、GIS、证据预览等组件
│   │   ├── lib/                       # API 客户端、文案、核验展示
│   │   └── ...
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
│
├── docs/                              # 设计、阶段交付、OpenAPI 与 ADR
│   ├── openapi-v1.json
│   ├── STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md
│   ├── PROJECT_PLAN.md
│   └── ...
│
├── examples/                          # 合成样例与非正式评测合同示例
│   ├── design-package-demo/
│   ├── evaluation-v0-nonformal/
│   ├── remote-analyzer-reference/
│   └── stage2-demo/
│
└── scripts/                           # Windows 子集门禁、交付打包等
    ├── run_local_quality_gates.ps1
    ├── run_windows_backend_subset.ps1
    └── package_delivery.sh
```

### 关键文件对照

| 路径 | 说明 |
| :--- | :--- |
| `backend/app/main.py` | 后端入口与生命周期 |
| `backend/app/api/router.py` | 主业务 API |
| `backend/app/api/work_order_routes.py` | 工单 / 设计包 / 空间校验 API |
| `backend/app/services/sealing.py` | 证据封存 Saga |
| `backend/app/services/compliance.py` | 后端合规规则引擎 |
| `backend/app/services/gpkg_*.py` | GeoPackage 导入与规范化 |
| `frontend/src/pages/BackendWorkflowPage.tsx` | 真实闭环联调页 |
| `frontend/src/pages/GISMapPage.tsx` | GIS / 工单工作台 |
| `frontend/src/pages/WorkOrderPage.tsx` | 工单采集页 |
| `frontend/src/lib/api.ts` | 前端 API 客户端 |
| `docs/openapi-v1.json` | 机器可读 API 合同 |
| `examples/design-package-demo/` | 合成设计包样例（`synthetic=true`） |

---

## 七、能力边界（必须保留）

为避免将工程演示误读为指标达标或司法级存证，系统明确以下边界：

1. **`stub`**：只验证工程链路，不输出物理量测或准确率。  
2. **`demo_fixture`**：确定性合成演示，默认应关闭；报告/证据包标记为 demo，不具备运营证据等级。  
3. **`remote_http`**：受控远程算法桥，默认关闭；不等于已接入真实目标模型。  
4. **哈希链 / Merkle 证据包**：可检测包内篡改，**不是**区块链、可信时间戳或司法存证。  
5. **当前为上传后批处理**，不是 RTSP/WebRTC 直播或秒级实时监管。  
6. **赛题 85%/90% 指标**尚未由冻结数据集 + 真实非 mock 模型验证，禁止用演示页数值填报。  
7. **合成 GPS / 合成设计包**必须保留 synthetic 标识，不得当作真实现场证据。

更细的阶段证据、迁移合同、Worker 租约与依赖锁说明见 `docs/` 目录（如 Alpha11–Alpha18 文档、`STATUS_2026-07-28_BASELINE_REPRODUCTION.md` 等）。

---

## 八、文档与协作索引

| 文档 | 内容 |
| :--- | :--- |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | 项目计划与任务 |
| [`docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md`](docs/STAGE2_ALPHA18_QGIS_WORKORDER_SLICE.md) | 工单合规主线切片 |
| [`docs/STAGE2_DEMO_GUIDE.md`](docs/STAGE2_DEMO_GUIDE.md) | 本地演示与自测 |
| [`docs/FIRST_STAGE_DELIVERY_GUIDE.md`](docs/FIRST_STAGE_DELIVERY_GUIDE.md) | 一阶段交付与排障 |
| [`docs/REMOTE_ANALYZER_CONTRACT.md`](docs/REMOTE_ANALYZER_CONTRACT.md) | 远程算法合同 |
| [`docs/algorithm-data/README.md`](docs/algorithm-data/README.md) | 算法/数据决策包 |
| [`frontend/README.md`](frontend/README.md) | 前端专项说明 |
| [`backend/README.md`](backend/README.md) | 后端专项说明 |

历史纯前端 Demo 备份分支：`archive/frontend-demo-v0`  
发布标签：`v0.2.0-alpha18`

---

## 九、下一条关键路径

1. 接入首个**真实非 mock** 算法 baseline，并走完整评测合同。  
2. 在 PostgreSQL 上完成 API/Worker 多进程压力与故障演练。  
3. 将演示密钥体系升级为正式身份认证（JWT/SSO），轮换所有演示 Key。  
4. 按 `docs/algorithm-data/` 门禁推进数据许可、标签规范与正式指标评测。  
5. 持续把 Demo 页面中的 mock 视图迁移到真实 API。

---

**锋眸智鉴** — 让通信基建施工过程可采信、可核验、可追溯、可交付。
