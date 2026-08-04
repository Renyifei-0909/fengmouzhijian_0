# 标准 GeoPackage 受控导入数据契约

- **契约版本**：`gpkg-import-contract-v0.1.1`
- **状态**：P1-1 / P1-1.1 冻结（只读预检）；**写入与公共上传未实现**
- **关联**：`docs/adr/ADR-001-STANDARD-GPKG-IMPORT.md`

本文件定义未来标准 GPKG 导入的规则。当前代码仅提供 **只读预检**（`inspect_standard_gpkg`），**不** 创建 `DesignPackage` / `EngineeringObject`，**不** 提供上传 API。

预检通过 **不等于** 导入完成。

## 1. 图层白名单

| GPKG 图层名 | 工程对象类型 | 说明 |
|---|---|---|
| `pipe_routes` | `pipe_route` | 管线路由 |
| `trenches` | `trench` | 沟槽 |
| `infrastructure_points` | `infrastructure_point` | 设施点 |

- 其他图层：**只报告，不导入**。
- 非 `features` 的 `gpkg_contents.data_type`（如 tiles）：不作为工程对象来源。

### 1.1 空图层策略

| 情况 | 行为 |
|---|---|
| 空的非目标（非白名单）图层 | 可报告，不导入 |
| 白名单目标图层 `feature_count = 0` | **不得** 作为可导入图层（`empty_whitelisted_layer`） |
| 所有白名单图层均为空 | 整个预检失败（`all_whitelisted_layers_empty`） |
| 正式导入 | **不** 创建零对象 `DesignPackage` |

## 2. 几何约束（第一阶段）

| 图层 | 允许 geometry type（二维） |
|---|---|
| `pipe_routes` | LineString |
| `trenches` | Polygon **或** LineString |
| `infrastructure_points` | Point |

**拒绝或标记不支持（不得静默降维）**：

- Multi\* 几何
- GeometryCollection
- 曲线 / 扩展几何（含 ExtendedGeoPackageBinary，`X=1`）
- 空几何（header 空标志 `Y=1`）
- Z 或 M 维（`z=1` 或 `m=1`）

### 2.1 GeoPackageBinary 头预检（非完整 WKB 解码）

预检可对有限样本 BLOB 校验 StandardGeoPackageBinary 头（OGC 12-128r15）：

| 位 | 含义 | 本契约 |
|---|---|---|
| bit 0 `B` | 头部数值字节序（0=大端，1=小端） | 必须与 `srs_id` 四字节序一致可读 |
| bits 1–3 `E` | envelope 类型 | 仅允许 0–4；**5–7 拒绝** |
| bit 4 `Y` | 空几何 | **拒绝** |
| bit 5 `X` | 0=Standard / 1=Extended | **仅接受 X=0** |
| bits 6–7 | 保留 | **必须为 0**，否则拒绝 |

- `version` 必须为 `0`
- 头长度 = 8 + envelope 字节数；长度不足 → `geometry_blob_truncated_header`
- **完整 WKB 解码留给 P1-2 成熟库**；测试 fixture 构造器不得升格为产品解析器

### 2.2 元数据一致性

- `gpkg_contents.srs_id` 与 `gpkg_geometry_columns.srs_id` **必须一致**
- `geometry_column` 必须存在于 feature 表
- 几何列 SQLite 声明不得与 BLOB 使用明显矛盾（如 TEXT/INTEGER）
- 几何样本查询失败 → **图层拒绝**（不得仅 warning）
- `gpkg_geometry_columns` 行缺失 → 图层拒绝

## 3. CRS 约束

首批允许解析为：

- `EPSG:4326`
- `EPSG:25832`

### 3.1 解析规则（fail-closed）

**禁止** 把 `gpkg_contents.srs_id` 或几何头内 `srs_id` 直接当作 EPSG 编号。

必须读取 `gpkg_spatial_ref_sys` 对应行：

- `srs_id`（包内键）
- `organization`
- `organization_coordsys_id`
- `definition`（不得为 null、空字符串或 `undefined` / `unknown`）

仅当 `organization` 规范化后为 `EPSG`，且 `organization_coordsys_id` 为允许列表中的整数时，得到 `resolved_epsg`。

失败场景：

- organization 不是 EPSG
- definition 缺失 / 空 / undefined
- CRS 无法识别或不在允许列表
- 多个 **已接受白名单图层** 的 `resolved_epsg` 不一致

## 4. 字段映射

**禁止** 模糊猜测自动映射。

### 4.1 必需字段

- `object_code`
- `name`

### 4.2 可选白名单

- `expected_pipe_count`
- `expected_trench_stage`
- `expected_specification`
- `material`
- `specification`
- `procedure_code`
- `design_version`
- `notes`

### 4.3 禁止进入 EngineeringObject 快照

字段名命中（大小写不敏感的子串规则见实现）的 **PII / 敏感类** 不得进入快照。预检只报告字段名进入 `dropped_fields`，**不读取字段值**。

## 5. 幂等与事务策略（文档冻结；本轮不迁移 DB）

| 项 | 策略 |
|---|---|
| 建议幂等键 | `project_id + source_sha256 + import_contract_version` |
| 同摘要重复提交 | 返回既有导入结果，不重复创建对象 |
| 同 `package_code` 但摘要变化 | **不得** 自动覆盖 |
| `object_code` 冲突 | 预检/导入前明确报告 |
| 替换设计版本 | 必须显式操作 |
| 正式写入 | 单数据库事务；失败不残留 DesignPackage / EngineeringObject / 孤立文件 |

`import_contract_version` 当前值：`gpkg-import-contract-v0.1.1`。

## 6. 与 JSON / 派生路径的关系

| 路径 | 状态 |
|---|---|
| JSON 设计包 `import-json` | **当前主路径**（可写库） |
| `import_gpkg_derivative` | **legacy**：需 `geom_geojson`，不是标准 GPKG，不作为公共上传入口 |
| 标准 GPKG 预检 | **P1-1 / P1-1.1**：只读报告 |
| 标准 GPKG 几何规范化 | **P1-2B**：候选对象，不写库 |
| 标准 GPKG 事务导入 | **P1-3A**：库路径 `import_standard_gpkg`（无公共上传 API） |
| 导入审计 | **P1-3B**：`standard_gpkg_imported` / `standard_gpkg_import_idempotent`（摘要、契约版本、对象数；无 PII 值） |
| 预览—确认式上传 | **P1-4 / P1-4.1**：preview → confirm；独立签名密钥；confirm 私有快照 + `expected_source_sha256`；服务端强制 `synthetic=true` / `purpose=controlled` / `sample_or_unverified`；staging TTL/容量/并发 |

## 7. 限制与完整性探测

| 限制 | 默认（实现可调） |
|---|---|
| 最大文件大小 | 32 MiB |
| 最大图层数（contents 行） | 64 |
| 单图层最大记录数 | 50_000 |
| 总记录数（白名单图层） | 100_000 |
| 最大字段数 / 图层 | 64 |

### 7.1 文件路径安全

- 打开前检查存在、普通文件；**默认拒绝符号链接 / reparse point**
- SHA-256 流式计算；hash 前后比较 size / mtime / 文件标识
- hash 与 SQLite 打开之间文件变化 → 拒绝
- 错误信息使用稳定错误码，**不泄漏完整本地路径**

### 7.2 `PRAGMA quick_check`

- `PRAGMA quick_check(N)` 限制的是 **返回的错误条数**，**不** 保证固定计算时间
- 本预检在 `max_file_bytes`（默认 32 MiB）约束下可启用；成本受文件大小上限约束
- 可通过策略 `run_quick_check=False` 关闭
- **不得** 将其描述为“浅层 / 固定成本检查”

## 8. 真实性声明

- 预检通过 **不等于** 导入完成。
- 不构成生产级 GPKG 能力声明。
- 不读取真实敏感归档；测试仅使用合成 fixture。
