# Stage2 Alpha18：QGIS 工单合规验真最小垂直切片

日期：2026-07-31  
版本目标：在 **0.2.0** 可信交付闭环上叠加工程对象/工单/GPS 空间校验/观察—合规分离骨架，形成可运行、可核查的主业务切片。

## 1. 本轮交付

| 能力 | 说明 |
|---|---|
| DesignPackage | 设计包导入记录：源文件摘要、CRS、图层/字段白名单、脱敏策略、导入结果 |
| EngineeringObject | 工程对象：规范化 WGS84 几何快照 + 白名单属性 |
| WorkOrder | 工单：冻结设计/几何/规则/容差；状态机 |
| EvidenceCapture | 证据采集：GPS、精度、时间、空间校验可解释记录 |
| ComplianceEvaluation | 后端规则引擎结论；与 analyzer 观察分离 |
| 合成样例 | `examples/design-package-demo/` 明确 `synthetic=true` |

## 2. 明确不做

- 不解析完整 QGIS 编辑器能力；
- 不引入 PostGIS/geopandas 硬依赖（本轮纯 Python CRS + 距离）；
- 不宣称真实 GPKG 生产导入、90% 指标或防作弊。

## 3. 真实性边界

- 样例设计包坐标为合成演示坐标，不是真实工地；
- 模拟 GPS 必须 `location_source=synthetic_demo` 且 `is_synthetic_location=true`；
- 合规结论是规则引擎对观察字段的确定性比对，不是模型准确率；
- 深度量测字段若出现在规则中且观察缺失，必须 `insufficient_evidence`。

## 4. 迁移

- 当前应用 Alembic head：`20260801_0004`（标准 GPKG 幂等列；依赖 `20260731_0003` → `20260728_0002`）
- 测试环境仍可用 `create_all`
- Alpha11 旧库接管：真实 0001 结构 → stamp `20260728_0001` → upgrade `0002` → `0003`
- 迁移保持严格失败关闭：表已存在时 **不会** 跳过

## 5. GPS 空间校验公式（检查点修复）

```text
pass  ⇔  distance_to_target_m <= spatial_tolerance_m
         AND accuracy quality gate ok
```

- **不得** 使用 `tolerance_m + accuracy_m`（精度越差不得越容易通过）
- `gps_accuracy_threshold_m` 冻结在 WorkOrder，并写入 EvidenceCapture
- `accuracy_m` 必须 `> 0`；超过阈值 → `unavailable`
- `device_gps` 缺少 `accuracy_m` → 不得通过
- 经纬度范围：lat ∈ [-90, 90]，lon ∈ [-180, 180]
- `synthetic_demo` 可计算距离，但必须保留合成标识

## 6. 设计包导入边界

- `import-json`：仅 `synthetic=true`；`source_type` 恒为服务端 `synthetic_json`
- 独立上限 `FENGMOU_DESIGN_PACKAGE_MAX_UPLOAD_BYTES`（默认 2 MiB）
- 受控 GPKG 派生为库路径：强制 CRS、拒绝原始 GeoPackageBinary、流式 SHA-256；无公网任意路径 API

## 7. P0-6 前端真实 API（2026-07-31）

| 页面 | 路由 | 数据源 |
|---|---|---|
| GIS / 工单工作台 | `/gis-map` | 项目列表、`gis-summary`、设计包列表/import-json、创建工单、MapLibre GeoJSON |
| 工单采集 | `/work-orders/:id` | 同工作台采集面板；浏览器 geolocation / 合成定位 |

### 地图

- **MapLibre GL JS** + **离线空白 Style**（无网络瓦片也能显示几何）；
- 可选 `VITE_MAP_STYLE_URL` 在线底图（增强项，失败不白屏）；
- 数据：`geometry_wgs84` Point/LineString/Polygon；无本地 mock marker。

### 隔离演示端口（不停止 8000/5173）

```text
后端 8001：临时 SQLite + FENGMOU_*_API_KEY=alpha18-*
前端 5174：VITE_API_PROXY_TARGET=http://127.0.0.1:8001
           mode=alpha18.local（.env.alpha18.local）
```

### 边界

- 不是完整 QGIS 编辑器 / 自动配准 / 防作弊；
- `synthetic_demo` / `demo_fixture` / 合成设计包醒目标识；
- 合规仅服务端规则引擎；无 mock 默认回退。

## 8. P0-7 商业化硬化（续）

- 统一文案：`frontend/src/lib/productCopy.ts`
- 核验显示逻辑：`frontend/src/lib/verificationDisplay.ts`（含 vitest）
- 可选择核验记录：`WorkOrderCapturePanel`（`selectedCaptureId` + 序号防竞态）
- 商业化样例种子：`backend/scripts/seed_alpha18_commercial.py` + `examples/design-package-demo/commercial-pipe-route-package.json`
- 默认优先选择项目编号 `ALPHA18-COMMERCIAL`
