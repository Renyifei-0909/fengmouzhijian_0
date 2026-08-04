# ADR-001：标准 GeoPackage 受控导入技术路线

- **状态**：Accepted（P1-1 预检与契约；P1-1.1 标准头/元数据一致性强化；几何读取栈待 P1-2 落地）
- **日期**：2026-08-01
- **范围**：通信工程施工合规管理平台 — 设计包导入链路
- **关联**：Alpha18 工单合规主线；`docs/GPKG_IMPORT_CONTRACT.md`（契约 `gpkg-import-contract-v0.1.1`）

## 1. 背景

现有 `import_gpkg_derivative()` 是 **受限派生路径**：

- 要求自定义 `geom_geojson` TEXT 列；
- **拒绝** 标准 GeoPackageBinary 几何；
- 将 `gpkg_contents.srs_id` 当作可直接使用的 CRS 标识（不正确，srs_id 是包内标识符，不是 EPSG）；
- **无** 公共 GPKG 上传 API；
- **不能** 代表“标准 GPKG 导入已完成”。

产品主线仍是：设计包 → EngineeringObject → WorkOrder → 采集/GPS → SpatialCheck → observations → 规则初判 → 人工复核 → 整改 → 报告存证。

## 2. 决策问题

在 Windows 开发机、后续 Linux/容器与 CI 上，如何安全、可维护地读取 **OGC 标准 GeoPackage**，并在导入前做 fail-closed 预检，同时避免：

- 手写完整 WKB / GeoPackageBinary 解析；
- 把未验证的“上传即导入”接口包装成生产能力；
- 引入不必要的 GeoPandas 重量级依赖。

## 3. 方案比较

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. sqlite3 元数据预检 +（P1-2）GDAL 系几何读取** | 预检无原生依赖；几何用成熟栈；分层清晰 | 完整导入分两阶段 | **推荐** |
| **B. Fiona/GDAL** | 生态成熟、CRS 能力强 | 原生库与 wheel 矩阵需验证；API 偏矢量通用 | P1-2 备选入口 |
| **C. osgeo/GDAL 直接绑定** | 能力最全 | 安装/版本分叉重；绑定风格老 | 不作为首选应用层 API |
| **D. 手写 GeoPackageBinary/WKB** | 无外部依赖 | 高维护成本、安全与边界 bug 多、复用价值低 | **拒绝作为产品解析器** |

### 3.1 推荐栈（分阶段）

| 阶段 | 职责 | 技术 |
|---|---|---|
| **P1-1 / P1-1.1** | 只读元数据预检、契约、安全限制、合成 fixture | 标准库 `sqlite3` + 流式 SHA-256 |
| **P1-2A** | 依赖可行性验证 | optional extra `gpkg`：**pyogrio + Shapely + pyproj** |
| **P1-2B** | 标准几何规范化服务（仍不写库） | 同一栈；无 GeoPandas 必需；Fiona 非主路径 |

### 3.1.1 P1-2A 实测锁定（Windows 开发机）

| 组件 | 版本 |
|---|---|
| pyogrio | 0.13.0 |
| shapely | 2.1.2 |
| pyproj | 3.7.2 |
| numpy（传递） | 2.5.1 |
| GDAL（via pyogrio） | 3.12.4 |
| GEOS | shapely 3.13.x / GDAL-reported 3.14.1 |

- 安装：`uv sync --extra gpkg`（`pyproject.toml` + `uv.lock`）
- GPKG driver 可用；合成 fixture `list_layers` / `read_info` / `raw.read` → Shapely WKB 成功
- CRS：`always_xy=True` 下 EPSG:25832→4326 成功
- 栈不可用：`GpkgGeometryStackError`，**禁止**回退手写解析器
- Linux/CI 必须实跑；个别 Windows 策略可能阻断原生 DLL

### 3.2 为什么不手写完整 WKB/GeoPackageBinary

1. 规范包含 envelope 标志、字节序、扩展几何、Z/M、空几何与版本演进；自研等于长期安全债。
2. 测试 fixture 中可构造 **最小 GP header 样本** 用于 magic/version/flags/envelope 长度检查，**不得** 升格为产品解析器。
3. CRS 与拓扑处理应交给 PROJ/GEOS 成熟实现。
4. **P1-1.1**：产品预检仅按 OGC 标志位做有限头校验（B/E/Y/X/reserved、envelope 长度、version）；**不**解码 WKB 坐标。

### 3.3 为什么 geom_geojson 派生路径不能代表标准 GPKG

- 它绕过了标准几何列与 GeoPackageBinary；
- CRS 解析语义不完整；
- 无公共 API、无标准 fixture 回归；
- 仅适用于内部受控、已脱敏的派生制品。

## 4. 部署与工程影响

| 维度 | P1-1 预检 | P1-2A/B GDAL 系读取 |
|---|---|---|
| Windows 本地 | 仅 Python 标准库 | wheel：`uv sync --extra gpkg`（已在本机验证） |
| Linux 容器 / CI | 同左 | 同 extra；优先官方 wheel，避免系统 GDAL 分叉 |
| 许可证 | 无新增 | GDAL/PROJ/GEOS 等需记入 SBOM |
| 包体积 / 冷启动 | 可忽略 | 增大（numpy+pyogrio 等）；仅 optional extra |
| 流式/分批 | 元数据查询可限制 | `max_features` / 游标分批；P1-2B 强制 |
| CRS | 仅解析元数据中的 EPSG | pyproj `always_xy=True` |
| 异常几何 | 标记 unsupported | P1-2B fail-closed |

P1-2A 已锁定上表实测版本；后续升级须重跑可行性测试。

## 5. 安全与真实性边界

- 预检 **只读**，不写 DesignPackage / EngineeringObject，不复制源文件。
- 不得读取或返回 PII 字段 **内容**；仅报告字段名进入 dropped_fields。
- 不得提交真实归档 GPKG 或敏感坐标。
- 不得宣称生产级 GPKG 导入、真实模型指标或绝对防作弊。
- 默认拒绝符号链接 / reparse；hash 与打开之间文件变化 fail-closed。
- `PRAGMA quick_check(N)` 限制错误返回条数而非固定耗时；仅在文件大小上限内启用，注释与契约必须准确。

## 6. 后果

| 做 | 不做 |
|---|---|
| 契约文档 + ADR | 公共上传 API |
| `inspect_standard_gpkg` 预检 | 本轮 DB 迁移 |
| 合成 fixture 与测试 | 手写完整几何解码 |
| 保留 `import_gpkg_derivative` 并标注 legacy | 用派生路径冒充标准导入 |

## 7. 后续（P1-2 建议）

在选定 pyogrio/Shapely/pyproj（或 Fiona）后：

1. 读取标准几何 → 规范化 WGS84；
2. 仍先 library/service 层，**不急于**开放公共上传；
3. 幂等键与事务写入按契约实现；
4. 再评估前端导入与进度 UX。

## 8. 决策

**接受方案 A**：P1-1 用 sqlite3 做标准元数据预检；P1-2 用 GDAL 系成熟栈读取标准几何；拒绝手写完整解析器作为产品能力。
