# EvaluationDataset 数据卡模板

> 为每个版本复制本模板，文件名建议 `DATASET_CARD_<dataset-id>_<version>.md`。未填项写 `unknown/blocking`，不能留空后默认通过。测试 fixture 必须标 `formal_eligible=false`。

## A. 身份与冻结

- Dataset ID：
- Version：
- Task type：`violation_event_classification | hidden_field_extraction`
- Case unit：
- Status：`draft | frozen | retired`
- Created at / Frozen at：
- Owner：
- QA custodian：
- `dataset.manifest.json` SHA-256：
- `cases.jsonl` SHA-256 / 行数：
- `labels.private.jsonl` SHA-256 / 行数：
- Split assignment SHA-256：
- Label spec version / SHA-256：
- Metric spec version / SHA-256：
- Formal eligible：`true | false`
- Claim scope：`field | historical | staged_real | authorized_simulation | sample_scenario`

## B. 任务与声明边界

- 目标问题：
- 类别或字段表：
- Primary metric / threshold：
- 辅助指标：
- 明确能证明：
- 明确不能证明：
- 是否仅为预切事件窗，还是包含未剪辑长视频检出：
- 是否包含仿真/样例；若是，如何防止冒充真实现场：

## C. 来源逐项登记

| source_id | origin | 权利人/取得方式 | license 文件+SHA | training | evaluation | submission | remote | redistribution | 个人/位置 | 批准单 |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |

- 上游第三方素材追溯：
- 公开下载页快照/日期：
- 授权邮件/合同位置和摘要：
- 归属/citation 要求：
- 保留期限和删除责任人：
- 脱敏方法、派生物 manifest 和审批：
- 不允许上传的外部服务：

## D. 采集与领域

- 国家/地区和场景：
- 通信工程工序/对象：
- 采集设备、分辨率、帧率、时间段：
- 现场/项目/摄像头/操作员分布：
- 合格/违规或字段分布：
- 与目标部署的域差：
- 已知选择偏差：
- 个人信息、精确位置和基础设施敏感性：

## E. 标注与真值

- 标注指南路径/SHA：
- 标注工具和版本：
- 标注者数量与资质：
- 是否独立双标：
- 冲突率与仲裁流程：
- 抽检比例、错误率和修订记录：
- 可观察性/ignore 规则：
- 是否在看模型预测前冻结：
- 隐蔽尺寸真值的仪器、校准、控制点、独立检查点和不确定度：

## F. 切分与防泄漏

- Splits 与 case 数：
- 每类/字段 support：
- 正式 v0 强制 group keys：`source_lineage_id, capture_session_id, engineering_entity_id, site_group_id, camera_group_id, person_group_id[, project_group_id]`
- 是否按传递闭包切分：
- 原始/裁剪/重编码/增强如何共享 lineage：
- 相同 asset SHA 是否跨 split：
- 近重复检查方法和人工审计人：
- 已声明训练 manifest 与 gate/final 的 hash/group 交叉检查：
- Gate holdout 使用次数策略：
- Final holdout 保管和一次性消费策略：

## G. 质量与统计

- 文件存在、大小、SHA、媒体可解码检查结果：
- 坏图/坏视频/重复/近重复：
- 类别/字段/设备/场景/画质分布：
- 样本量决策人及在看结果前的依据：
- 小样本不确定性报告方式：
- 已知标签噪声、盲点和不代表的分布：

## H. 训练、评估与隔离

- 模型可见的 public cases/assets 挂载：
- QA private labels 存储和权限：
- 是否禁网/独立容器：
- 训练数据 manifest 列表和 SHA：
- 评估器代码/环境摘要：
- ModelFreeze 路径和 SHA：
- 运行失败、未达标和 final 重跑策略：

## I. 审批

| 角色 | 结论 | 人员 | 日期 | 签署 statement SHA |
|---|---|---|---|---|
| 数据负责人 | | | | |
| QA/评估负责人 | | | | |
| 项目统筹 | | | | |
| 隐私/授权复核 | | | | |
| 指导老师 | | | | |

## J. 变更记录

冻结后不原位改数据、标签、split 或 metric。任何变化创建新版本并说明：

| 新版本 | 变化 | 原因 | 影响 | 批准人 |
|---|---|---|---|---|
| | | | | |

## K. AI 使用披露

- 是否使用 AI 生成/辅助标注：
- 使用的模型、版本和提示/配置摘要：
- 人工复核比例和规则：
- 合成数据的生成与筛选：
- 为什么不会把模型输出循环当真值：
