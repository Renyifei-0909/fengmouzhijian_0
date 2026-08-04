# ADR-002：EngineeringObject 身份、设计版本与标准 GPKG 幂等导入

- **状态**：Accepted（P1-3 前置；迁移实现另检）
- **日期**：2026-08-01
- **关联**：`docs/GPKG_IMPORT_CONTRACT.md`；`ADR-001`；现有 `uq_engineering_object_code (project_id, object_code)`

## 1. 背景

当前模型：

- `DesignPackage`：项目下的设计包快照（含 `package_code`、`source_sha256` 等）
- `EngineeringObject`：`UniqueConstraint(project_id, object_code)` — **每个 object_code 在项目内唯一一行**
- `WorkOrder` 外键指向 `EngineeringObject`

标准 GPKG 导入需要回答：

1. EngineeringObject 是**稳定工单身份**还是**设计版本快照**？
2. 同 `object_code` 的新设计如何保存？
3. 已冻结工单如何继续指向旧设计？
4. 相同文件摘要如何幂等？新摘要同 `package_code` 如何处理？

## 2. 决策（推荐并采纳）

### 2.1 身份模型：稳定身份 + 设计快照字段（不引入 Revision 表于 P1-3 首迁）

| 概念 | 决策 |
|---|---|
| `object_code` | **项目内稳定业务身份**（工单、复核、整改锚定此码） |
| EngineeringObject 行 | 表示该身份的 **当前有效设计快照** |
| 历史设计 | 不删除旧几何：通过 **DesignPackage 不可变归档** + 对象上的 `design_package_id` / `design_version` 追溯；工单创建时 **冻结** 引用的 package 与对象几何快照副本（若工单表尚无快照列，P1-3 可增加 `design_package_id` 冻结与可选 `geometry_snapshot_json`） |
| EngineeringObjectRevision | **P1-3 不强制引入**；若产品后续需要并排多版本几何，再开 ADR-002a |

理由：现有唯一约束与工单外键已按稳定身份设计；引入 Revision 表会牵动 WorkOrder/Compliance 全链路，超出“事务化导入”最小门。

### 2.2 同 object_code 新设计版本

| 场景 | 行为 |
|---|---|
| 显式“替换设计”操作 | 在**单事务**内更新 EngineeringObject 的几何/属性/`design_package_id`/`design_version`；写审计事件 |
| 非显式自动覆盖 | **禁止** |
| 已有 `in_progress` / 已提交核验的工单 | 默认 **阻止静默替换**；须业务确认策略（阻断或仅影响新工单）——P1-3 实现为：**存在非终态工单时拒绝替换并返回冲突列表** |

### 2.3 已冻结工单与旧设计

- 工单在创建时记录 `engineering_object_id` + **当时的** `design_package_id`（及必要几何快照）。
- 后续设计包替换 **不得** 改写历史工单的冻结字段。
- SpatialCheck / 合规规则以工单冻结数据为准，不以“对象当前设计”覆盖历史判定（新工单使用新设计）。

### 2.4 幂等键

```
idempotency_key = project_id + source_sha256 + import_contract_version
```

| 场景 | 行为 |
|---|---|
| 相同幂等键再次提交 | 返回既有 `DesignPackage` 与对象集合，**不**新建 |
| 同 `package_code`、不同 `source_sha256` | **不得**自动覆盖；返回冲突，要求新 `package_code` 或显式 replace 流程 |
| 导入失败 | 事务回滚；staging 文件删除；无孤立 DesignPackage / EngineeringObject |

### 2.5 source_type 与契约

- 新增 `source_type = standard_gpkg`（与 json / gpkg_derivative 区分）
- 持久化 `import_contract_version`（当前 `gpkg-import-contract-v0.1.1`）
- JSON 主路径行为不变

## 3. 明确不做（本 ADR 范围外）

- 不在未实现幂等与事务前开放公共上传 API（仍属 P1-4）
- 不引入区块链 / 通用大屏
- 不把预检/规范化误称为导入完成

## 4. 迁移原则（实现检查点）

1. 先扩列（可空）→ 回填 → 再加唯一约束（幂等键）
2. SQLite + PostgreSQL upgrade/downgrade 均需测试
3. **不得**用当前 8001 业务库做破坏性试验；使用隔离临时库
4. 未实现工单冻结字段前，替换设计默认 fail-closed

## 5. 后果

- P1-3 可在 **不拆除** `uq_engineering_object_code` 的前提下落地标准 GPKG 事务导入
- 多版本并排需要未来 ADR-002a（Revision）
- 操作员“替换设计”必须是显式、可审计动作
