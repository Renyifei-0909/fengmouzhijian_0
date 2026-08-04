# Construction-PPE 开发基线算力与 Pilot 计划

日期：2026-07-10  
状态：只读盘点完成；**未安装依赖、未下载权重、未启动训练**  
用途：内部开发 baseline，不是正式成绩或通信施工现场泛化证据

## 1. Environment Summary

| 项目 | 当前实测 |
|---|---|
| 主机 | Intel MacBook Pro，8 核 16 线程 Intel i9 |
| 内存 | 16 GB；检查时内存压力正常，但历史/当前 swap 使用偏高 |
| GPU | AMD Radeon Pro 5500M 4 GB + Intel UHD 630；无 NVIDIA/CUDA |
| PyTorch | `pytorch_env` 与 `oanet` 均为 Torch 2.2.2，`mps.is_available()=true` |
| MPS 兼容性 | 小型张量运算通过；`torchvision::nms` 需 `PYTORCH_ENABLE_MPS_FALLBACK=1` 回退 CPU |
| 训练框架 | 现有环境均未安装 Ultralytics；无本地 YOLO 预训练权重 |
| 磁盘 | 约 248 GiB 可用；Construction-PPE 工作副本约 355 MiB |
| 容器/调度 | 当前无 Docker、Slurm、Kubernetes 或 Ray |
| 数据 | 1,416 张图、11 类；10 个孤立 train 标签和 3 对跨 split 近重复已另行审计 |

现有后端 Python 3.12 venv 不含 Torch/OpenCV，也不应混入训练依赖。若获准，必须新建独立 `fengmou-yolo` 环境。

## 2. 当前可行性结论

- 本机具备运行 nano 级 YOLO **短 pilot** 的技术条件，但 Intel + AMD MPS 不是 Ultralytics 重点验证的主路径，吞吐和算子回退必须先实测。
- 当前官方 `data.yaml` 不能原样训练：其 `path: construction-ppe` 与本地 `extracted/` 实际根目录不一致。
- 首个开发模型只能定位为帧级 `person / helmet / no_helmet` 检测基线；它不能直接证明单人轨迹事件窗分类，更不能代替授权通信施工视频盲测。
- 由于内存仅 16 GB、swap 使用偏高且 GPU 显存 4 GB，不应直接发起长训练。

## 3. Risk Classification

### Green：已执行的只读动作

- 系统、CPU、内存、GPU、磁盘和调度器盘点；
- conda/Torch/MPS/torchvision/Ultralytics 可用性检查；
- Construction-PPE 结构、规模和 `data.yaml` 路径检查；
- 极小 MPS 张量/NMS 兼容性诊断。

### Yellow：必须获得明确确认后才能执行

- 创建独立训练环境和安装固定依赖；
- 下载预训练权重；
- 生成派生数据清单、YAML、日志和 checkpoint；
- 占用 AMD GPU、CPU、内存和磁盘启动 pilot；
- 调整 batch、worker、精度或训练时长。

### Red：不会执行

- 关闭内存/温度保护、设置 `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0`；
- 超频、固件/电压修改；
- 覆盖原始数据或旧 run；
- 把开发分数写成正式 85% 结论；
- 未确认许可便公开原图、训练权重或含真人截图。

## 4. Allocation Plan

| Experiment | Priority | Device | CPU/RAM | 数据 | 预计时长 | Stop/adjust rule |
|---|---|---|---|---|---|---|
| `construction_ppe_mps_smoke` | P0 | 单 AMD MPS，NMS 可回退 CPU | workers=2，cache=false，保留系统内存余量 | 派生 train 的 10%，1 epoch，512 px，batch=2 | 5–30 分钟，需实测 | MPS OOM、NaN/Inf、热/性能告警、swap 接近耗尽、单 batch 持续变慢 5× 时停止 |

Pilot 通过后才评审是否运行 20–30 epochs。完整开发训练预估 1.5–8 小时，误差较大；应以 pilot 的 seconds/iteration 重算，不把估计当承诺。

## 5. Launch Package（获批后才执行）

### 5.1 环境和数据前置

1. 新建独立 `fengmou-yolo` 环境，固定 Python/Torch/Torchvision/Ultralytics/fsspec 版本；
2. 保存环境 lock、`pip check`、Torch/MPS 诊断结果；
3. 原始数据保持只读；生成带绝对根路径、无 `download` 字段的派生 YAML；
4. 只纳入 `helmet(0)`、`Person(6)`、`no_helmet(7)`，排除孤立标签；
5. 将已知近重复/同源场景绑定到同一开发 split；
6. 固定 nano 级预训练权重及其 SHA-256；
7. 所有运行写入 `work/runs/`，不进入源码交付 ZIP。

### 5.2 候选命令骨架

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 \
/path/to/fengmou-yolo/bin/yolo detect train \
  model=/absolute/path/to/pinned-nano-model.pt \
  data=/absolute/path/to/construction-ppe-derived-v0/data.yaml \
  device=mps \
  classes="[0,6,7]" \
  epochs=1 \
  fraction=0.1 \
  imgsz=512 \
  batch=2 \
  workers=2 \
  cache=False \
  amp=False \
  seed=20260710 \
  deterministic=True \
  project=/Users/xsp/Documents/Codex/2026-07-10/5-waddle/work/runs \
  name=construction_ppe_mps_smoke \
  exist_ok=False
```

该命令当前不可直接运行：独立环境、派生 YAML 和固定权重尚未创建。

## 6. Monitoring Plan

每 30 秒或每个 epoch 记录：

- batch 时间、loss、验证耗时和失败栈；
- `torch.mps.current_allocated_memory()`；
- CPU、RAM、swap、磁盘余量；
- `pmset -g therm` 的 thermal/performance warning；
- MPS fallback、OOM、NaN/Inf；
- 数据、权重、代码、配置和输出摘要。

停止条件：重复 MPS OOM、loss/metric 非有限、系统明显失去响应、热/性能告警、swap 接近耗尽、意外重新下载数据、读取未登记文件、覆盖已有 run。

## 7. Audit Log Plan

每个 run 必须单独保存：

- 命令、开始/结束时间、退出码和主机环境摘要；
- Python/依赖 lock；
- 原始 ZIP、派生 manifest/YAML、预训练权重、代码和配置 SHA-256；
- stdout/stderr、训练曲线、checkpoint 和验证输出；
- MPS fallback/OOM/热状态记录；
- 是否触发停止条件及人工调整原因。

## 8. 许可边界

- Construction-PPE 官方说明和下载包 LICENSE 标注 AGPL-3.0；框架许可与数据集许可是两层问题。
- 许可文本不自动证明底层图片肖像、截图展示、训练权重和企业交付的完整权利链。
- 原始数据继续只放 `work/`，不得进入交付 ZIP、公开仓库或远程服务。
- 官方 split 已发现跨集合近重复，只能做开发对照，不能支撑正式 85% 声明。
- 对外演示、提交权重或部署网络服务前，需数据负责人、项目统筹、指导老师及必要时权利人/法务确认。

参考：[Ultralytics 训练模式](https://docs.ultralytics.com/modes/train)、[PyTorch MPS 环境变量](https://docs.pytorch.org/docs/stable/mps_environment_variables.html)。

## 9. Next Confirmation

启动 pilot 前需要一次明确确认，覆盖以下六项：

1. 允许创建独立训练环境并安装固定依赖；
2. 允许下载并保存预训练权重；
3. 允许创建派生数据、配置、日志和 checkpoints；
4. 允许占用本机 AMD GPU 约 5–30 分钟执行 smoke；
5. 确认本次只作内部开发，不作为正式成绩或现场泛化声明；
6. 指定数据/许可批准人，并确认 Construction-PPE 的比赛用途边界。

在上述确认前，本计划停留在 green/read-only 阶段，不启动训练。
