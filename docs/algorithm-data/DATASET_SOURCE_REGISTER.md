# 数据集来源登记与候选筛选

更新日期：2026-07-10  
状态：候选登记，不代表已下载、已获授权或已进入训练  
目的：阻止“公开可下载 = 可用于企业比赛/可再分发”的错误推断

## 1. 状态定义

- `candidate-now`：来源和许可文字足以进入技术试验，但采用前仍须保存许可证快照、摘要和用途审批。
- `auxiliary-only`：只验证某一子模块、域外压力测试或演示管线，不能支持主指标。
- `permission-required`：未发现数据独立许可，或 NC/仅研究限制与企业命题比赛存在冲突；书面许可前不进入正式训练/评估。
- `exclude-formal`：上游权利链、任务语义或真值不足，不得进入正式数据集。
- `research-only`：可用于受限预研，但不能支持对外工程性能声明。

任何候选进入本地数据目录时，必须生成自己的 Data Card；本文件不是其授权凭证。

## 2. 路线 B：PPE/违章候选

| 来源 | 一手来源与许可 | 规模/任务 | 当前状态 | 允许的项目用途 | 关键缺口/红旗 |
|---|---|---|---|---|---|
| Construction-PPE | [官方说明](https://docs.ultralytics.com/datasets/detect/construction-ppe)、[官方 YAML](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/construction-ppe.yaml)；官方页标 AGPL-3.0 | 1,416 图像，11 类 YOLO bbox；1132/143/141 | `candidate-now` | person/helmet/vest 检测基线与预训练 | 已审计：无 `no_vest`，`none` 语义不明确，10 个孤立标签，至少 3 对近重复跨 split，且存在域外图片；官方 test 不作正式评估 |
| Safe and Unsafe Behaviours | [Mendeley Data](https://data.mendeley.com/datasets/xjmtb22pff/1)、[原始论文](https://doi.org/10.1016/j.dib.2024.110791)；CC BY 4.0 | 691 个工业 MP4，8 类 clip 分类 | `auxiliary-only` | 验证真实视频输入、分类 adapter、结构化输出 | 制造业室内域；无 PPE 类、bbox 或时序真值；类别不均衡 |
| SynthSite | [GovTech 官方页](https://huggingface.co/datasets/govtech/SynthSite)、[LICENSE](https://huggingface.co/datasets/govtech/SynthSite/resolve/main/LICENSE) | 227 个合成施工视频，悬吊物危险二分类 | `auxiliary-only` | 合成链路 smoke、置信度/分歧测试 | 全合成；Tier 1 的 150 个明确样本全体一致，低一致性主要来自刻意保留歧义的 Tier 2；两层不能混算或冒充真实现场 |
| HVSA | [Mendeley](https://data.mendeley.com/datasets/bnr8yypvsb/1)、[论文记录](https://zenodo.org/records/4573301)；CC BY 4.0 | 45 图像，worker bbox | `auxiliary-only` | 极小外部人员检出压力测试 | 不是穿/未穿背心分类；样本太小，不能训练或作为主指标 |
| HWD2024 | [官方页](https://icnc-fskd.fzu.edu.cn/hwd/)、[原始论文](https://doi.org/10.1002/cpe.70692) | 5,416 施工图像，23,760 实例，COCO；含正确/错误/未戴帽，官网将 `none` 定义为未佩戴安全帽 | `permission-required` | 获书面许可后补安全帽状态 | “openly available”不是许可；站点 CC 条款看似仅覆盖网站源码；下载后仍需核对 COCO category ID 与官网语义一致 |
| SH17 | [作者仓库](https://github.com/ahmadmughees/SH17dataset)、[原始论文](https://doi.org/10.1016/j.jnlssr.2024.09.002)；CC BY-NC-SA 4.0 | 8,099 图像，75,994 实例，17 类 PPE | `permission-required` | 获许可后补 helmet/vest/person 正类 | NC-SA 与企业命题、奖金、成果转让存在较高兼容性风险，具体是否构成商业使用需权利人/法务书面确认；Pexels 人像作“违规”展示另有人格/肖像风险；没有显式负类 |
| CIS v1/v2 | [作者仓库](https://github.com/XZ-YAN/CIS-Dataset)、[原始论文](https://doi.org/10.1016/j.autcon.2023.105083)；CC BY-NC 4.0 | 5 万/6.1 万施工图像，实例分割；戴帽/未戴帽 | `permission-required` | 获许可后补真实施工安全帽 | 仅非商业研究；数据大且需解码；静态图像、无背心、无现场级划分保证 |
| SFCHD | [作者仓库](https://github.com/lijfrank/SFCHD-SCALE)、[原始论文](https://arxiv.org/abs/2306.02098) | 12,373 化工厂图像，约 50,558 实例 | `permission-required` | 获许可后做小目标/低光 PPE 压测 | 未发现独立数据许可；服装类语义需人工核验；化工厂域 |
| CMA Construction Meta Action | [作者仓库](https://github.com/S1mpleyang/ConstructionActionRecognition)、[原始论文](https://doi.org/10.1016/j.autcon.2022.104703)；README 限研究/非商业 | 1,595 施工动作视频，7 类 | `permission-required` | 获许可后做真实施工行为预研 | YouTube 上游版权链；无 bbox；时空模型成本高，不服务第一阶段 PPE 核心 |
| Split-UBR | [原始论文](https://doi.org/10.3390/s25216525)、[PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12610213/) | 3,000 真实施工图片，6 类 | `permission-required` | 作者授权且类表核验后再评估 | 仅按请求提供；数据无独立许可；不是视频或检测标注 |
| SHWD | [作者仓库](https://github.com/njvisionpower/Safety-Helmet-Wearing-Dataset)、[SCUT-HEAD 上游](https://github.com/HCIILAB/SCUT-HEAD-Dataset-Release) | 7,581 图像，hat/person 头部框 | `exclude-formal` | 不进入正式训练、评估或提交包 | 网络抓图 + SCUT-HEAD 仅学术研究；仓库 MIT 不能证明图像权利；`person` 实为普通头部 |
| SHEL5K | [Mendeley](https://data.mendeley.com/datasets/9rcv8mm682/4)、[原始论文](https://doi.org/10.3390/s22062315) | 约 5,000 二次重标图片 | `exclude-formal` | 只有补齐上游原图权利证明才重审 | 页面 CC BY 不能自动补齐上游 Kaggle 图片版权链 |

### 2.1 主线数据组合

1. Construction-PPE：只做检测预训练/基线，首次采用限定 `Person → person`、`helmet → helmet_on`、`no_helmet → helmet_off`、`vest → vest_on`。
2. `vest_off` 不得由“未检测到 vest”推断，必须由团队授权数据人工标注。
3. HWD2024、SH17、CIS、SFCHD 均在许可回函前隔离，不先训练后补手续。
4. 正式 gate/final 只用取得授权的通信施工视频，按现场、摄像头、原始视频、连续采集和工程实体分组隔离。
5. 公开数据测试分数只能描述相应公开域，不能写成现场泛化成绩。

## 3. 路线 A：隐蔽工程候选

| 来源 | 一手来源与许可 | 规模/任务 | 当前状态 | 可验证环节 | 不能证明什么 |
|---|---|---|---|---|---|
| OpenTrench3D | [仓库](https://github.com/SimonBuusJensen/OpenTrench3D)、[CVPRW 论文](https://openaccess.thecvf.com/content/CVPR2024W/USM/html/Hansen_OpenTrench3D_A_Photogrammetric_3D_Point_Cloud_Dataset_for_Semantic_Segmentation_CVPRW_2024_paper.html)；CC BY-NC 4.0 | 310 点云、约 5.28 亿点、5 类语义 | `research-only` | 沟槽/公用管线三维语义分割、摄影测量流程 | 无实例、直径、材质、规格和独立尺寸真值；不能单独测数量/埋深/间距准确率 |
| RWTH OHT2 | [机构记录](https://publications.rwth-aachen.de/record/1035812)、[说明](https://publications.rwth-aachen.de/record/1035812/files/Dataset_description.md?subformat=icon-180&version=1)；CC BY-NC 3.0 | 约 1,040 合成施工 RGB+语义掩码 | `auxiliary-only` | 二维沟槽/塑料管/支护分割预训练 | 无深度、实例、标定或测量真值，且合成域差明显 |
| DUT S3DSS | [作者数据页](https://www.kaggle.com/datasets/liminghao123/dut-sewer3d-semantic-segmentation-s3dss-dataset)、[原始论文](https://doi.org/10.1016/j.measurement.2025.117434)；CC BY 4.0 | 1,300 点云、约 9.17 亿点，管道内壁缺陷 | `auxiliary-only` | 点云编码器预训练/鲁棒性 | 不是开挖沟槽、埋深或间距任务 |
| Morocco GPR | [Mendeley](https://data.mendeley.com/datasets/ww7fd9t325/1)、[原始论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC11847285/)；CC BY 4.0 | 2,239 雷达图，管线/空洞/完整地层 bbox | `auxiliary-only` | 回填后异常检测通道 | 无目标深度/类型真值；深度依赖介电常数和现场标定；增强图要防泄漏 |
| DIODE | [官方页](https://diode-dataset.org/)、[原始论文](https://arxiv.org/abs/1908.00463)；MIT | 27,858 RGB-D，深度/法线 | `auxiliary-only` | 通用深度预训练、质量诊断 | 不含沟槽/管道标签；通用深度分数不能证明现场米制精度 |
| ETH3D | [官方页](https://eth3d.ethz.ch/)、[数据概览](https://eth3d.ethz.ch/overview)；CC BY-NC-SA 4.0 | 多相机/双目/激光真值 | `auxiliary-only` | COLMAP、双目/MVS、标定与重建正确性 | 不能证明目标领域尺寸能力 |
| ConSLAM | [作者仓库](https://github.com/mac137/ConSLAM)、[机构论文记录](https://www.repository.cam.ac.uk/items/693c260b-e145-4f01-ad88-d2031e4c0590)；仅学术使用 | 5 个施工现场时序，图像/LiDAR/IMU/TLS | `permission-required` | 施工 SLAM 与多传感时序配准 | 无沟槽/管道实例或尺寸标签；一条序列已知有问题 |

### 3.1 自采真值最低字段

- 原始视频、设备型号、内参/外参、位姿、时间和图像质量；
- 控制点与不参与配准的独立检查点，仪器、检定和测量不确定度；
- 每根管道的 2D mask、3D 点、实例 ID、中心线、外径；
- 现状地面/设计完成面、沟槽顶底、管顶/管中心埋深、中心/净间距、可见/设计数量；
- 规格/材料/产品编码及其来源，不可仅凭颜色推断；
- 项目、地点、设备、操作员和连续采集分组；
- 真值测量人、方法、时间、审批和文件哈希。

## 4. 每个真实来源的机器/人工登记字段

每个 `source_id` 至少记录：

```yaml
source_id: stable-id
title: human-readable name
origin: field_real | historical_real | staged_real | authorized_simulation | sample_scenario | mock | demo_fixture
landing_page: https://...
rights_holder: ...
acquisition_method: download | partner_transfer | self_capture
downloaded_at: RFC3339 or null
license_kind: SPDX-like id or custom/unknown
license_document_path: controlled/path
license_document_sha256: 64 lowercase hex
allowed_uses:
  training: true | false | unknown
  evaluation: true | false | unknown
  competition_submission: true | false | unknown
  remote_processing: true | false | unknown
  redistribution: true | false | unknown
contains_personal_information: true | false | unknown
contains_precise_location: true | false | unknown
deidentification_record: controlled/path or null
retention_until: RFC3339 date or null
data_approver_ref: approval id or null
raw_archive_sha256: 64 lowercase hex or null
derived_manifest_sha256: 64 lowercase hex or null
notes: domain gap, class mapping, upstream rights
```

正式本地评估至少需要 `evaluation=true`；提交样本或截图还需 `competition_submission=true`；调用 `remote_http` 还需 `remote_processing=true`。`unknown` 一律不按允许处理。

## 5. 许可询问模板要点

向数据作者/权利人询问时应一次性覆盖：

1. 是否允许用于由烽火通信命题或指导的高校比赛训练和内部评估；
2. 是否允许提交模型权重、源码、指标和演示视频；
3. 是否允许在网页、答辩 PPT 和报告展示少量标注帧；
4. 是否允许发布衍生标注、统计或模型权重，但不再分发原始数据；
5. 企业试用或商业化是否需另签授权；
6. 对可识别人物、位置、第三方素材和上游数据是否还有附加限制。

收到回函后保存原始邮件/网页、时间、收件人/发件人和 SHA-256；不要只在任务板写“已问过”。

## 6. 当前动作清单

- [x] 已保存并审计 Construction-PPE 官方下载制品：ZIP SHA-256 `bef8dcb5...32ccc`，数据未进入交付 ZIP。
- [x] 已核查解码、标签结构、类分布、精确重复与 dHash；发现 10 个孤立标签和 3 对目视确认的跨 split 近重复，`none` 仍无可执行官方定义。
- [ ] 发出 HWD2024、SH17、CIS、SFCHD 的统一许可询问。
- [ ] 建立自采通信施工视频授权书、采集说明和脱敏流程。
- [ ] QA 建立 public cases 与 private labels 的物理隔离。
- [ ] 每次训练保存所用数据 manifest 摘要，禁止未申报训练数据。
- [ ] 主线之外的数据不得因为“已经下载”自动进入训练。

## 7. 局限与披露

本登记基于截至 2026-07-10 的作者仓库、官方数据页、原始论文和许可文字。它没有替每个数据源完成法律审查，也没有逐文件确认所有第三方素材权利。Codex 辅助检索与起草；正式采用决策必须由数据负责人、项目统筹和指导老师签署。
