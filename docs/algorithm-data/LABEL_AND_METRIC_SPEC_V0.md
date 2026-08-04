# 标签与指标规范 v0（讨论稿）

版本：0.1.0-proposed  
日期：2026-07-10  
状态：未冻结、未产生任何正式指标  
首选任务：`violation_event_classification`

## 1. 为什么单独建立评估域

现有 `AnalyzerResult` 表示一次上传任务的推理输出，不拥有正式 accuracy。一个样本、一次人工复核或一份演示报告都不能生成比赛指标。正式指标只能来自独立冻结的 `EvaluationDataset + ModelFreeze + EvaluationRun`，并保留样本级结果和完整摘要。

`stub`、`demo_fixture`、mock、fixture、placeholder 和人工预填结果即使 `synthetic=false`，也不能进入正式评估。

## 2. 产品推理层与正式评估层

### 2.1 检测/归属层标签

统一语义标签：

| 标签 | 定义 | 主要来源/取得方式 |
|---|---|---|
| `person` | 可跟踪的施工人员主体 | 公共检测集 + 自采 |
| `helmet_on` | 安全帽可见且正确佩戴 | 公共映射 + 自采 |
| `helmet_off` | 人头可见且明确未戴安全帽 | 公共映射 + 自采 |
| `helmet_wrong` | 安全帽存在但佩戴方式不符合冻结规则 | 获授权 HWD2024 或自采 |
| `vest_on` | 反光/安全背心明确可见且属于该人员 | 公共正类 + 自采 |
| `vest_off` | 人体相关区域可观察且明确未穿要求的背心 | 必须自采/授权标注；不可由“没检测到 vest”反推 |
| `unknown_or_occluded` | 遮挡、画质、视角或距离不足，无法可靠判定 | 质量规则/模型输出，进入人工复核 |

目标框采用归一化或像素坐标时必须在 manifest 固定格式。每个 PPE 实例需通过冻结的空间关联/跟踪规则绑定 `person_track_id`；孤立 helmet/vest 不构成人员合规结论。

### 2.2 正式 v0 样本单元

`case_unit = event_window`：从一个原始授权视频中预先生成并冻结的**单人轨迹裁剪窗**。传给分类器的媒体本身只对应一个目标人员，不允许把同一段多人画面原样复制成多个不同标签 case。原始全景视频、裁剪参数和派生关系另存 provenance manifest 并计算摘要；裁剪和候选生成必须在看待评模型输出前完成。

首轮冻结类表建议仅为：

- `helmet_compliant`：目标人员在窗口内满足本轮冻结的安全帽要求；它不表示没有其他工艺或行为违章；
- `helmet_missing`：该人员在满足可观察条件的连续证据中明确未戴安全帽。

`vest_missing` 和 `helmet_and_vest_missing` 只有在取得足够授权真值后通过新数据集版本加入，不能原位改 0.1.0 类表。

### 2.3 事件窗边界

- `start_ms/end_ms`、目标轨迹和裁剪 ROI 在标注前固定，`0 <= start_ms < end_ms`；
- 多人原视频必须先生成不同的单人轨迹裁剪资产；若裁剪后仍有多个可能目标或无法保持同一身份，该候选标记为不合格，不产生含歧义的评分 case；
- 在筛除任何样本前，先冻结完整候选窗口清单、确定性生成/抽样规则、随机种子和原始视频分组；
- 进入评分集前按冻结的画质、遮挡和持续时间规则判断是否合格，并保存每个排除原因；
- 一旦数据集冻结，不能因为模型失败把 case 改成不可评分或从分母移除；
- 相邻帧、重编码、裁剪、增强和同源片段共享 `source_lineage_id`；
- 同一连续拍摄共享 `capture_session_id`，同一工点对象共享 `engineering_entity_id`；正式 v0 还需显式提供并分组隔离现场、摄像头和人员身份；
- 组的传递闭包只能落在一个 split，禁止相邻片段随机分 train/test。

## 3. 工程语义绑定

每个 case 至少带：

- `project_key`：脱敏项目标识；
- `site_key`：脱敏工点标识；
- `procedure_code`：工序，例如开挖、敷设、熔接、登高；
- `baseline_version` 与 `baseline_sha256`；
- 原始媒体 ID、片段起止、媒体 SHA-256；
- `source_lineage_id/capture_session_id/engineering_entity_id/site_group_id/camera_group_id/person_group_id`；
- 可选 `project_group_id`，当声称跨项目泛化时必须提供。

这样输出的是通信施工事件，而不是脱离工程对象的普通安防框。

## 4. 标注规则与 QA

1. 两名标注者独立标注，不能看模型预测；冲突由第三人仲裁。
2. 标注指南必须包含正例、负例、遮挡、远距离、帽子相似物、画面外人员、多人交叉等示例。
3. `helmet_compliant` 不是“模型没发现问题”，而是人工确认在冻结可观察条件下满足安全帽规则；不得在报告中显示为“无违章”。
4. 看不清的候选在冻结前依据质量规则转入非评分采集质量集；冻结后不得追加 ignore。必须同时保存候选总数、eligible 数、逐例排除原因、遮挡排除率和 `eligible/total` coverage，不能只展示容易样本上的 accuracy。
5. public `cases.jsonl` 不含真值；QA 单独保管 `labels.private.jsonl`，模型进程只读 public cases/assets。
6. case ID 与标签无关；预测必须恰好覆盖目标 split，缺失、额外、重复均拒绝整次正式运行。
7. 数据集、标注规范、metric spec、split assignment 和模型冻结声明都以 SHA-256 锁定。

## 5. 主指标与辅助指标

### 5.1 官方候选主指标

在组委会确认前，候选主指标为事件窗闭集单标签 accuracy：

`accuracy = 正确分类的事件窗数 / 冻结目标 split 的事件窗总数`

阈值暂按 `accuracy >= 0.85`。比较时使用整数或 Decimal；例如 17/20 恰好通过，84/100 不通过，避免二进制浮点边界误差。

这只能证明预切事件窗分类，不证明长视频中的自动检出、时序定位和全量漏检性能。若产品声明后者，v1 必须增加事件匹配、temporal-IoU、漏检事件数、重复告警和每小时误报。

### 5.2 必须同时输出

- 固定类序混淆矩阵与每类 support；
- 每类 precision、recall、F1；
- macro/micro precision、recall、F1；
- balanced accuracy（闭集单标签下等于各类 recall 的宏平均）；
- weighted F1；
- `helmet_missing` 的 recall 和 false-negative rate；
- accuracy 的 95% Wilson 区间；
- case 数、原始视频数、现场/摄像头/工程实体组数；
- 候选窗口总数、eligible 数、coverage、逐类样本量和排除原因分布；
- 小目标、遮挡、低光和设备等预先声明切片结果；
- 推理硬件、端到端延迟和模型制品摘要。

闭集单标签下 micro P/R/F1 等于 accuracy，但仍显式输出。若某类 support 为 0，则正式数据集无效；不能从 macro 中静默排除。模型从未预测某类时，该类 precision 按 0 并记录零分母标记。

### 5.3 Wilson 区间

对 `N` 个 case、正确率 `p`，使用 `z = 1.959963984540054`：

```text
denom  = 1 + z²/N
center = (p + z²/(2N)) / denom
delta  = z * sqrt(p(1-p)/N + z²/(4N²)) / denom
CI95   = [center-delta, center+delta]
```

比赛材料同时写 point estimate 和区间。官方材料目前只见 point accuracy 门槛，不能声称官方要求 Wilson 下界也达到 0.85；是否用下界作内部门禁必须在看结果前冻结。普通 Wilson 依赖近似独立 Bernoulli 假设，在这里仅作为描述性区间；正式报告还应按 capture session/site/project 做聚类 bootstrap 或组级分析，并报告 group 数。

### 5.4 底层检测指标

检测器单独报告 mAP@50、mAP@50:95、各类 AP、precision、recall、小目标/遮挡分层与速度。这些指标用于诊断，不替代事件窗 accuracy。

## 6. 冻结数据文件约束

### 6.1 `cases.jsonl`

```json
{
  "schema_version": "evaluation.case.v0",
  "case_id": "case_stable_id",
  "task_type": "violation_event_classification",
  "split": "gate_holdout",
  "source_id": "source_authorized_01",
  "inputs": [{
    "role": "primary_media",
    "asset_id": "asset_01",
    "relative_path": "assets/clip-001.mp4",
    "sha256": "64-lowercase-hex",
    "size_bytes": 123456,
    "content_type": "video/mp4",
    "segment": {"start_ms": 1200, "end_ms": 5200}
  }],
  "engineering_context": {
    "project_key": "project-pseudonym",
    "site_key": "site-pseudonym",
    "procedure_code": "PPE-HELMET",
    "baseline_version": "v1",
    "baseline_sha256": "64-lowercase-hex"
  },
  "groups": {
    "source_lineage_id": "lineage-01",
    "capture_session_id": "capture-01",
    "engineering_entity_id": "entity-01",
    "site_group_id": "site-group-01",
    "camera_group_id": "camera-group-01",
    "person_group_id": "person-group-01"
  }
}
```

恰好一个 `primary_media`，且它是单人轨迹裁剪窗；路径必须位于数据集根目录内，禁止绝对路径、`..` 和 symlink escape；文件大小与原始字节摘要必须匹配。正式评分的 split policy 必须把上例六个 group key 全部纳入传递闭包检查。

### 6.2 `labels.private.jsonl`

```json
{
  "schema_version": "evaluation.label.v0",
  "case_id": "case_stable_id",
  "annotation": {
    "spec_version": "ppe-event-labels-v0",
    "status": "adjudicated",
    "record_sha256": "64-lowercase-hex"
  },
  "truth": {"kind": "violation_single_label", "label": "helmet_missing"}
}
```

### 6.3 `predictions.jsonl`

```json
{
  "schema_version": "evaluation.prediction.v0",
  "case_id": "case_stable_id",
  "output": {
    "kind": "violation_single_label",
    "label": "helmet_missing",
    "confidence": 0.83
  }
}
```

所有 schema `extra=forbid`；JSON 只接受 UTF-8，递归拒绝 NaN/Infinity、无效 Unicode，以及 `accuracy_claim`、`evidence_grade`、`ground_truth`、`metrics` 等受保护字段。

## 7. 正式运行硬门禁

运行必须同时满足：

- 数据集状态 `frozen`，manifest/cases/labels/asset 摘要和行数正确；
- task、类序、metric spec 与 split assignment 一致；
- 分组传递闭包和相同 asset hash 不跨 split；
- 每个来源明确允许 evaluation，远程推理还要允许 remote_processing；
- 个人信息/精确位置已按批准流程处理；
- QA 隔离声明存在且 final holdout 未被消费；
- ModelFreeze 中模型、代码、配置、环境、训练数据清单与运行一致；
- adapter 为真实 model/rule_engine/hybrid，不是 stub/fixture/placeholder；
- 训练清单与 holdout 无声明的 hash/group 重叠；
- 预测 ID 与 split 完全一致，评分脚本可复算。

只有硬门禁全部通过后才比较 0.85。当前离线 v0 尚未实现模型执行隔离、一次性 holdout、训练清单重叠和外部签批，因此只输出结构校验与阈值状态，`compliance_claim_eligible=false`；不得称正式合规通过。未来 `EvaluationRun.succeeded` 仅表示运行可信可复算，也不等于达标；未达标不是执行故障。

## 8. 隐蔽工程预留规范

若未来切换/扩展路线 A：

- `task_type=hidden_field_extraction`，`case_unit=inspection_record`；
- 每个字段提前冻结 type 和 matcher（exact、normalized_text、numeric_tolerance 白名单）；
- 真值状态只有 `value/not_present/ignore`，预测只有 `value/not_present/abstain`；
- `ignore` 在冻结前确定，预测缺失按错误，不能从分母删；
- 主指标候选 `field_micro_accuracy >= 0.90`，同时报告每字段 accuracy、field macro 和 record exact；埋深、间距等关键字段还必须有单项门槛，不能由大量简单字段把 micro 平均稀释；
- 对埋深/间距还必须报告 MAE、RMSE、P95、覆盖率、检查点误差与人工复核率；
- 人工真值掩码输入几何模块和模型预测掩码输入全链路要分开评估。

## 9. 未解决问题

1. 组委会是否接受安全帽/PPE 作为“违章行为”；
2. 85% 的官方样本单元、类别集合、是否按事件/帧/视频和是否有官方数据；
3. 安全帽任务是否需要按工序细分，以及何时才有资格扩展为综合 `no_violation`；
4. 正式最小样本量、各类最小 support 和 gate/final 比例；
5. 多人、同时多种违规是否需 v1 多标签；
6. 真实长视频检出何时升级为正式任务。

这些问题必须在看最终结果前书面冻结。任何最小样本数都不能由当前模板擅自填成“官方要求”。

## 10. AI 使用披露

本规范由 Codex 根据比赛材料、公开数据说明和评测工程原则辅助起草。它是待算法/QA/指导老师确认的技术草案，不是官方指标解释，也没有产生算法成绩。
