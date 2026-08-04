# Evaluation v0 离线评分核

状态：已实现独立离线评分核、development-only 本地执行合同和 unsigned evidence bundle；尚不是数据库化或受控正式 `EvaluationRun`，不生成 `ComplianceClaim`  
支持任务：`violation_event_classification` / 预切 `event_window` / 闭集单标签

## 1. 已实现边界

评测代码位于 `backend/app/evaluation/`，与上传任务的 `AnalyzerResult`、数据库和 HTTP API 分离。离线评分核只读取冻结数据声明、私有真值、模型声明和 predictions；development runner 可先执行固定的本地 predictor，但仍不能把一次推理响应、人工复核或开发运行自动升级为比赛 accuracy。

当前实现：

- 严格 UTF-8 JSON/JSONL，拒绝重复 JSON key、空行、NaN/Infinity、无效 Unicode 和 schema 额外字段；
- cases/labels/predictions ID 精确覆盖，缺失、额外、重复预测均拒绝，不缩分母；
- 数据文件与媒体的大小/SHA-256 复算，路径必须留在数据集根目录；
- JSON/JSONL 只接受稳定普通文件；FIFO、目录、符号链接和打开时换包会在读取前拒绝，冻结制品缺失归类为完整性失败；
- 按 lineage、连续采集、工程实体的 union-find 传递闭包检查跨 split 泄漏，并拒绝相同媒体 SHA 跨 split；
- formal 结构门禁只允许 gate/final holdout，强制由 QA 外部提供 manifest/model-statement 摘要，拒绝非 frozen、formal_eligible=false、mock/demo_fixture/placeholder/仿真样例来源、无 evaluation 用途和 stub/fixture/placeholder/synthetic 模型声明；
- 固定类序混淆矩阵、accuracy、每类 P/R/F1、macro/micro/weighted、95% Wilson；
- 正式 split 必须按 lineage、连续采集、工程实体、现场、摄像头和人员六组做传递闭包隔离；
- development runner 只允许 `train`/`validation`、`formal_requested=false` 和 `local_process`；固定 run plan、模型/入口/训练声明/evaluator 摘要，构造 public 推理视图后执行 predictor，再由本评分核复算结果；
- 可选 `--evidence-dir` 在临时 predictions 删除前原子发布公开固定五文件包；`verify-dev-bundle` 严格核验成员、public-cases/case-id roster、隐私投影和内部绑定，但不重算 private score；
- manifest 只接受 canonical 字符串阈值 `"0.85"` 和区间级别 `"0.95"`，点门槛用整数关系判断，17/20 恰好通过、84/100 失败；
- 失败结构化输出：合同错误 exit 2、完整性错误 exit 3、开发执行错误 exit 4；指标未达标默认仍 exit 0，也可显式 `--require-threshold-pass` 得到 exit 6。

根目录约束读取依赖 Python/操作系统提供 `dir_fd/openat`、`O_DIRECTORY` 和 `O_NOFOLLOW`。当前在 Linux/macOS 受支持；缺少任一能力时 evaluator 以 `EVAL_SECURE_OPEN_UNAVAILABLE` 安全拒绝，不降级为可跟随符号链接的读取。原生 Windows 若触发该错误，应在项目 Docker/WSL 环境运行。

## 2. 命令

先按 `docs/algorithm-data/LABEL_AND_METRIC_SPEC_V0.md` 建立 manifest、cases、private labels、predictions 和 model statement。安装后端依赖后，从 `backend/` 运行：

```bash
python scripts/evaluate.py validate \
  --manifest /path/to/dataset.manifest.json \
  --formal

python scripts/evaluate.py score \
  --manifest /path/to/dataset.manifest.json \
  --predictions /path/to/predictions.jsonl \
  --model-statement /path/to/model-freeze.json \
  --split gate_holdout \
  --formal \
  --expected-manifest-sha256 <QA预先保存的manifest摘要> \
  --expected-model-statement-sha256 <QA预先保存的模型声明摘要>
```

合法但 accuracy 未达阈值的输出仍为 `ok=true`，同时 `threshold_status=failed` 和 `EVAL_THRESHOLD_NOT_MET`。如流水线需要硬失败，在 score 命令追加 `--require-threshold-pass`，返回 exit 6；这仍与执行/合同失败分开。

当前所有结果固定返回 `gate_status=not_eligible`、`compliance_claim_eligible=false`。`structural_gate_status=passed` 只说明代码可复算的结构门禁通过，`threshold_status=passed` 只说明冻结点阈值通过；二者都不是赛事或法务合规通过。

仓库提供 `examples/evaluation-v0-nonformal/` 可执行合同示例：两段无真人、可完整解码的 1 秒 MP4，分开的 public/private 命名空间，以及只读取 public cases 和常量 fixture model 的 predictor。回归测试在完全不含 private labels 的临时目录重放 predictions 并逐字节比对。运行 `make evaluation-example-check` 或 `make evaluation-example-run-dev-check` 会得到 accuracy 0.50 和阈值失败；它只用于前后端/算法联调，formal 模式必须以 `EVAL_DATASET_NOT_FORMAL` 拒绝。

development runner 的命令、固定摘要、predictor 参数和威胁边界见 [`DEVELOPMENT_EVALUATION_RUNNER.md`](DEVELOPMENT_EVALUATION_RUNNER.md)。最小命令是：

已实现的 development run 证据包固定文件树、隐私投影、原子发布、离线校验和真实性边界见 [`DEVELOPMENT_EVIDENCE_BUNDLE_DESIGN.md`](DEVELOPMENT_EVIDENCE_BUNDLE_DESIGN.md)。它是 unsigned 开发证据，不是受控正式运行包。

```bash
python scripts/evaluate.py run-dev \
  --plan ../examples/evaluation-v0-nonformal/run-plan.json \
  --expected-run-plan-sha256 1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739
```

外部预期摘要必须由调用者预先保存，不能从同一个待验证 plan 动态推导。Construction-PPE 的只读算力盘点与未获批 pilot 见 [`algorithm-data/PPE_BASELINE_COMPUTE_PLAN_2026-07-10.md`](algorithm-data/PPE_BASELINE_COMPUTE_PLAN_2026-07-10.md)。

## 3. `formal` 的准确含义

当前 `--formal` 只执行代码已经能够复算的结构、外部摘要 pin、声明、分组和阈值门禁。以下事实仍不能由这版 CLI 独立证明：

- 许可证/授权文件法律有效、签字人有权限；
- 人像/位置同意与脱敏实际充分；
- 标注者真正独立、真值没有系统偏差；
- 所有近重复和真实工程关联都已完整填入 group ID；
- 每个 `event_window` 输入确实来自单人轨迹裁剪，而不是同一多人画面的重复标签；
- 模型进程没有看过 private labels，也没有使用未申报训练数据；
- 声明的模型摘要对应远程服务实际权重；
- final holdout 从未在系统外运行，且只被消费一次；
- 样本代表目标通信施工分布。

因此 v0 评分结果即使结构门禁与数值阈值通过，也不能单独生成正式对外合规声明。development runner 同样不能补足这些证明：入口以启动者同一 UID 运行，可能读取该 UID 可访问的其他本机路径，网络也明确未隔离。仓库现有一次性 registry 只提供 unsigned、单机状态落盘，不验证 QA 身份，也不授权 broker。它必须进入后续受控 `EvaluationRun`：独立低权限身份或隔离节点、只读挂载、禁网或受控网络、完整 runtime 摘要、训练数据清单、可信 holdout broker、QA 签名运行包和审计签名。

## 4. 当前未实现

- 正式隔离运行模型；当前 development runner 已核验并执行固定的单文件模型 artifact/entrypoint，但同 UID、未禁网且 runtime 未完整固定；
- 隐蔽工程字段评分；
- 未剪辑长视频的事件检出、时序定位、temporal-IoU、每小时误报和漏检；
- 感知近重复检测；代码只能验证精确 SHA 和已声明 group，Construction-PPE 的 dHash 审计是单独数据流程；
- 训练清单与 holdout 的 hash/group overlap；
- QA 私有标签容器隔离、可信 broker 和签名事故审批；本地 gate/final registry、暴露前 durable commit、事故锁定与 CLI 已实现，但 approval 仍是 self-asserted unsigned；
- EvaluationRun 数据库/API、签名受控正式运行包、ComplianceClaim；unsigned development evidence 已实现但不替代这些能力；
- 签名、可信时间戳或外部审计。

## 5. 测试证据

最终评测内核、development runner、两类 evidence 与 registry 专项 197 项、全后端 282 项通过，覆盖率 90.05%（两位小数门禁），统一证据见 `docs/FINAL_VERIFICATION.md`。测试覆盖阈值精度攻击、formal 训练集冒充、外部摘要 pin、真值/分母改写、非有限数、缺/多/重复预测、未知类、零 support、嵌套受保护声明、六类分组泄漏、相同 SHA/asset identity、stub 名称伪装、mock/仿真来源、非 frozen、无 evaluation 权限、UTF-8、NUL 路径、FIFO/符号链接、检查后父目录根外换包、异常 fd 关闭、冻结文件篡改、运行超时、进程清理、资源上限、固定 evidence 文件树、原子 no-replace、一次性 key 并发/崩溃、暴露前提交、Ed25519 签名/trust-store pin、隐私字段/本机路径拒绝、case roster 绑定、外部摘要与旧 evaluator 离线兼容。

## 6. 下一阶段

1. 用已实现的 run plan/model artifact/entrypoint/evaluator 合同接入首个真实 baseline；训练前按计算计划完成团队授权；
2. 扩展训练清单内容并实现与 holdout 的 hash/group overlap 核验；
3. 用独立低权限身份或隔离节点实现默认禁网、QA private 根不可见和完整 runtime 固定；
4. 在现有 unsigned development evidence 和本地一次性 registry 之上实现签名受控正式运行包、私有复算流程、可信 QA signer 与 broker；
5. 最后才接数据库/API 和 ComplianceClaim。
