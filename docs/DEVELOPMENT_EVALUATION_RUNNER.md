# Development-only 模型评测运行器

状态：已实现开发期本地执行合同；**不是正式盲测执行环境，不生成 `ComplianceClaim`**  
协议：`evaluation.run-plan.v0` / `evaluation.predictor-cli.v0` / `evaluation.development-run.v0`

## 1. 用途与硬边界

该运行器把“已固定摘要的模型入口在 public 推理视图上生成 predictions，再交给 Evaluation v0 评分”变成可重复的开发流程。它只允许：

- `mode=development`；
- `runner=local_process`；
- `formal_requested=false`；
- `split=train` 或 `split=validation`；
- `network_policy=uncontrolled_development`；
- `environment_policy=minimal_allowlist`。

它不能运行 `gate_holdout` 或 `final_holdout`，也不能把开发分数升级为正式 85%/90% 结论。成功输出始终包含：

```text
gate_status=not_eligible
compliance_claim_eligible=false
formal_requested=false
```

## 2. Run plan 合同

`run-plan.json` 必须以严格 schema 声明并固定以下输入：

| 字段 | 约束 |
|---|---|
| `dataset_manifest` | 相对 POSIX 路径、SHA-256、字节数 |
| `model_statement` | 相对 POSIX 路径、SHA-256、字节数；其 `artifact_sha256` 必须绑定模型制品 |
| `model_artifact` | 相对 POSIX 路径、SHA-256、字节数，单文件上限 2 GiB |
| `entrypoint` | 一个自包含 `.py` 文件，固定 SHA-256/字节数，上限 2 MiB |
| `training_data_manifest` | 固定 SHA-256/字节数；其 `model_artifact_sha256` 必须绑定同一模型制品 |
| `evaluator_source_sha256` | 对 Evaluation v0 执行与评分 Python 源文件清单计算的规范摘要 |
| 运行参数 | 固定 seed、1–300 秒 timeout、predictions/log 字节上限 |

调用者还必须通过 `--expected-run-plan-sha256` 从 plan 外部传入整个 run plan 的预期摘要。运行器在执行前后都复算该值；不能在同一条命令中从待验证的 plan 动态生成“预期值”，否则会失去外部固定的意义。

## 3. Predictor CLI

运行器使用当前 Python 解释器和固定参数启动入口：

```text
python -I -B -X utf8 entrypoint.py \
  --cases <staged-public-cases.jsonl> \
  --model <staged-model-artifact> \
  --output <predictions.jsonl> \
  --seed <run-plan-seed>
```

入口必须只把 predictions 写入 `--output`。结果需满足 Evaluation v0 的严格 JSONL、类别、ID 唯一和目标 split 精确覆盖规则；缺失、额外、重复或不可安全读取的输出会失败。

## 4. 执行顺序与可复算证据

1. 快照 run plan，并与外部 `expected-run-plan-sha256` 比较。
2. 核验 evaluator、dataset manifest、model statement、model artifact、entrypoint 和 training manifest 的摘要/大小及绑定关系。
3. 先用 Evaluation v0 校验数据集，再只为目标 `train`/`validation` split 生成 public cases。
4. 只复制 `public/` 命名空间中的目标媒体、模型制品、入口脚本和受信 supervisor 到临时推理目录；不复制 private labels。
5. 以最小环境变量白名单、隔离 Python 标志、POSIX 新进程组、CPU/文件描述符/文件大小限制和 timeout 执行入口。
6. 终止同进程组残留进程，记录 stdout/stderr 的摘要与字节数；把 predictions 快照复制到模型未知的受信路径。
7. 再次核验 run plan、五项声明制品和 evaluator 源码，然后以 `formal=false` 调用 Evaluation v0 评分。
8. 若传入 `--evidence-dir`，在临时 predictions 删除前生成公开 score 投影，绑定 public-cases/case-id roster 摘要，并以原子 no-replace 方式发布固定五文件证据目录。

输出记录 run plan/evaluator/training manifest/predictions/public cases/log 摘要、进程返回码与耗时、推理视图样本/媒体数量、运行时版本及明确的 assurance limitations。摘要只能证明本次读取到的字节身份，不能证明来源合法、运行环境可信或模型具有业务能力。

## 5. 当前非正式示例固定值

以下值对应 `examples/evaluation-v0-nonformal/` 当前提交，用于开发合同回归，不是模型成绩：

| 制品 | SHA-256 | 字节数 |
|---|---|---:|
| `run-plan.json`（外部固定） | `1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739` | 1211 |
| `dataset.manifest.json` | `aa763500859765a41260d2c9d1a193ef3e496483b5a9fe20bf8f0c1a3835a6b3` | 2006 |
| `model/model-statement.json` | `9a35086820803a16866c0890770bdfd170a59f1d2fe2edeb3f5b0ffc293924bd` | 310 |
| `model/constant-label-baseline.json` | `b674e25954898d7f9e92742004bf25dc8b269f0cef6932213bcd7338880a03e3` | 83 |
| `tools/generate_predictions.py` | `9c2d73002c16635e99fb98431c05b25af3adf91c408608763cacda77434461f5` | 2796 |
| `model/training-data-manifest.json` | `2c4a06cd73a99e056a44ae98eb7d6f749fc1758fc937b00f1408b251ed92617b` | 144 |
| evaluator source | `a40c65173aa753b2b6b4ea9b7e1cccfa025f4e3fb63806bab1e74beb90371318` | 规范文件清单摘要 |

从项目根目录重放：

```bash
cd backend
python scripts/evaluate.py run-dev \
  --plan ../examples/evaluation-v0-nonformal/run-plan.json \
  --expected-run-plan-sha256 1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739
```

或运行：

```bash
make evaluation-example-run-dev-check
```

预期进程成功、score accuracy 为 0.50、阈值失败，并保持 `not_eligible`。示例本来就有一条自然错误；不要为得到绿色阈值而改标签、分母或 formal 标记。若在命令后加 `--require-threshold-pass`，该示例应返回 exit 6，这仅表示阈值未通过。

生成和核验公开 development evidence：

```bash
python scripts/evaluate.py run-dev \
  --plan ../examples/evaluation-v0-nonformal/run-plan.json \
  --expected-run-plan-sha256 1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739 \
  --evidence-dir /path/to/new.dev-evidence

python scripts/evaluate.py verify-dev-bundle \
  --bundle /path/to/new.dev-evidence \
  --expected-manifest-sha256 <receipt 中的 manifest_sha256>
```

仓库固定示例的 manifest SHA-256 为 `ef91b6f5062f8bc7718f0fec2fe31eccc666be4d73be11eca3a029637bbec5de`，可运行 `make evaluation-example-evidence-check`。摘要匹配只表示字节与调用方给定值一致；manifest 仍为 unsigned，verifier 固定返回来源未验证、无隐私真实性声明、不能离线复算 score。

## 6. 威胁模型：同 UID 本地进程不是安全沙箱

临时目录、只复制 public 文件、`-I` 和最小环境变量是降低误读与环境漂移的措施，**不是针对恶意入口的机密性隔离**。模型入口仍以启动者的同一操作系统 UID 运行，因此当前实现不能阻止它：

- 枚举或读取该 UID 原本可读的本机文件，包括原数据集根和 private labels；
- 使用本机网络、DNS 或已允许的系统能力进行外联；
- 探测同 UID 进程和其他未由操作系统隔离的资源。

因此 `private_labels_copied=false` 只证明 private labels 没有被复制进临时推理视图，不能证明同 UID 模型进程从未在其他路径读取它们。`network_policy=uncontrolled_development` 也明确表示当前没有禁网证明。环境白名单会避免直接继承常见 secret/proxy 变量，但不能替代容器、独立低权限 UID、只读挂载、网络 namespace/firewall、seccomp 或外部执行节点。

其他已知限制：

- Python、第三方依赖、动态库、内核和硬件运行时尚未作为完整 runtime artifact 固定；
- 资源限制只覆盖单文件大小、文件描述符、单进程 CPU 时间和墙钟 timeout；尚未限制地址空间/RSS、进程数量或临时目录总量，不能作为防 OOM、fork 风暴或磁盘耗尽的正式隔离；
- supervisor 会在继承 hard limit 内单调收紧资源限制；本地 staging/落盘故障统一返回机器可读执行错误，发布完成但 parent `fsync` 失败会显式标为 durability unconfirmed；
- development evidence 已封存固定成员和摘要，但仍是 unsigned、不可重算私有 score 的开发包，不是签名或受控正式运行包；
- 训练清单目前只绑定模型制品，尚未实现与 holdout 的 hash/group overlap 复核；
- 本地一次性 gate/final registry、暴露前 durable commit、事故锁定和 CLI 已实现；但 approval/actor 仍是 self-asserted unsigned，不能授权 broker，QA 私有执行、签名审批和数据库/API `EvaluationRun` 尚未实现；
- 入口只适合团队信任的开发代码，不应接收任意第三方脚本；
- supervisor 依赖 POSIX 进程组与 resource limits；缺少这些能力的平台会安全拒绝。

## 7. 升级到受控正式执行前必须补齐

至少需要：独立低权限身份或隔离执行节点、只读 public/model 挂载、不可见的 QA private 根、默认禁网并可审计、完整 runtime/镜像摘要、训练数据与 holdout 重叠检测、可信 broker、签名受控运行包、QA 签署和可复算审计记录。本地 registry 只完成单机机械状态约束。完成这些条件前，不得把本开发运行器描述为“正式盲测”“防泄漏容器”或“可信执行环境”。
