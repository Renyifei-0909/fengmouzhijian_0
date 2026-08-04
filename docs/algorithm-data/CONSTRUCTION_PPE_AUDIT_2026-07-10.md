# Construction-PPE 下载与结构审计

审计日期：2026-07-10  
用途：判断其能否作为开发基线，不代表正式训练批准或现场指标  
结论：**可作为 person/helmet 检测的开发预训练候选；官方预定义 split 不可作为本项目可信独立测试集。**

## 1. 来源与制品

- 官方说明：[Ultralytics Construction-PPE](https://docs.ultralytics.com/datasets/detect/construction-ppe)
- 官方配置：[construction-ppe.yaml](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/construction-ppe.yaml)
- 官方下载：`https://github.com/ultralytics/assets/releases/download/v0.0.0/construction-ppe.zip`
- 下载压缩包 SHA-256：`bef8dcb599aa4e9d9f5e602cb6fa7143d3c84d7f6a0ff40463d7f2a4c2632ccc`
- 压缩包大小：约 170 MiB（官方页面标 178.4 MB）
- ZIP 条目数：2,852；`unzip -t` 全部通过；未发现绝对路径或 `..` 路径穿越条目。
- 内含 `LICENSE` SHA-256：`0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`
- 内含 `data.yaml` SHA-256：`bfbc2471c75a82beaca2c255b7814d7eaf3087f191f1c7e43c8d8e90a27e961a`
- LICENSE 文本为 GNU AGPL 3.0。官方页称数据集以 AGPL-3.0 发布并支持适当署名下的研究/商业应用；模型权重、底层图片的肖像/第三方权利及后续闭源交付仍不应自行推断。

原始数据保存在项目外层 `work/`，没有进入交付目录或交付 ZIP。

## 2. 结构与标注检查

| Split | 图片 | 标签文件 | 目标框 |
|---|---:|---:|---:|
| train | 1,132 | 1,142 | 9,098（与图片配对） |
| validation | 143 | 143 | 1,172 |
| test | 141 | 141 | 1,251 |
| 合计 | 1,416 | 1,426 | 11,521（与图片配对） |

检查结果：

- 1,416 张图片均可由 Pillow 解码；未发现损坏图片。
- 每张图片都有同名标签；未发现空标签文件。
- 所有读取到的 YOLO 行均为 5 列、类 ID 在 0–10、坐标 finite 且中心/宽高范围合法。
- train 中存在 10 个没有对应图片的孤立标签文件：`image940(1)`、`image941(1)`、`image944(1)` 至 `image950(1)`（缺 `image942/943`）以及 `image95(1)`。这些文件另含 93 个框，已从上表及下方类分布剔除；训练前必须删除或由来源方补图，不能静默计入样本统计。

## 3. 类分布

| 类 | train | val | test | 合计 |
|---|---:|---:|---:|---:|
| helmet | 1,341 | 201 | 192 | 1,734 |
| gloves | 1,146 | 136 | 163 | 1,445 |
| vest | 1,269 | 171 | 178 | 1,618 |
| boots | 1,235 | 151 | 211 | 1,597 |
| goggles | 419 | 47 | 52 | 518 |
| none | 651 | 81 | 65 | 797 |
| Person | 1,770 | 239 | 236 | 2,245 |
| no_helmet | 400 | 45 | 40 | 485 |
| no_goggle | 337 | 41 | 33 | 411 |
| no_gloves | 442 | 56 | 58 | 556 |
| no_boots | 88 | 4 | 23 | 115 |

孤立标签的 93 个框分布为 helmet 16、gloves 16、vest 14、boots 16、goggles 8、none 3、Person 20；这些数值不代表可训练目标，因为对应图片不存在。

限制：

- 官方明确 `vest` 没有对应 `no_vest`；不能把没有检出 vest 当成 `vest_off`。
- `none` 仅被官方称为 generic class，没有可执行语义定义。抽样观察显示它常落在未穿 PPE 的人体区域，但在没有官方标注指南前不得映射为 `no_vest` 或任何正式业务标签。
- `no_boots` 等类极少，若沿用随机图像划分会产生高方差；公开分数应逐类报告 support，不只报总体 mAP。

## 4. 泄漏与领域风险

### 4.1 精确/感知重复

- 原始字节 SHA-256：未发现完全相同图片，也未发现完全相同图片跨 split。
- 64-bit dHash：发现 8 个重复感知哈希组，其中 3 组跨 split。
- 对 3 组跨 split 候选逐图目视复核，均为相同人物、相同场景和几乎相同姿态的相邻/近重复画面：
  - train `image1050.jpg` ↔ val `image1049.jpg`
  - train `image1087.jpg` ↔ test `image1088.jpg`
  - train `image833.jpg` ↔ test `image834.jpg`

这不是仅靠摘要碰撞的推断；图像内容已人工目视确认。它表明官方 split 至少存在场景/连续帧泄漏。由于数据没有提供原始视频、现场或场景分组 ID，无法证明其他相邻帧已完整归组。

因此：

1. 官方 train/val/test 只用于与公开基线大致对照，不用于项目正式 85% 评估；
2. 如用于开发训练，应先做近重复聚类并按簇重分 train/dev；
3. 最终 gate/final 必须来自团队授权视频，并按原始视频、连续采集、摄像头、现场和工程实体隔离；
4. 不能用该数据集的 test 分数声称通信施工现场泛化。

### 4.2 领域抽样

本次不是随机抽样：先目视复核全部 3 组跨 split dHash 候选，再定向查看 3 个含 `none` 的 train 样本。可复核例子包括屋顶 PPE 摆拍 `train/image1050.jpg`、`val/image1049.jpg`、`train/image1087.jpg`、`test/image1088.jpg`，舞台演出 `train/image1336.jpg`，军人颁奖 `train/image1285.jpg`。这些例子只能证明“存在域外样本”，不能在未全量随机审阅前估计比例。官方“real construction environments”的总体描述不能替代本项目逐来源域审计。

## 5. 对路线决策的影响

下载前的路线 B 数据评分需要下调：该集仍可让 baseline 快速启动，但预定义测试集存在近重复泄漏，`none` 语义不明确、缺 `no_vest`，且存在明显域外图像。路线 B 的总门禁分由 75 调整为 **72/100**，仍暂时高于路线 A 的 48，但更依赖授权自采通信施工视频。

## 6. 下一步

- [ ] 生成近重复聚类和清洗后的开发 split；保留原始映射与摘要，不覆盖原数据。
- [ ] 人工审阅 `none` 类和域外样本；未定义前从正式标签映射中排除。
- [ ] 训练前移除 10 个孤立标签，生成清洗 manifest。
- [ ] 只做 `person/helmet_on/helmet_off` 首轮 baseline；不承诺 `vest_off`。
- [ ] 保存训练代码、配置、随机种子、权重和所用清洗 manifest 摘要。
- [ ] 用授权通信施工视频建立独立事件窗数据集，公开数据不进入 gate/final。

## 7. 可复现审计说明

本次审计脚本位于工作区 `work/audit_construction_ppe.py`，检查解码、图片/标签配对、YOLO 行、类分布、原始字节 SHA-256 和 dHash 候选。dHash 只用于发现候选，不是充分的近重复证明；跨 split 的 3 对候选另做了人工目视复核。尚未运行更强的图像嵌入聚类或全量场景人工审阅。
