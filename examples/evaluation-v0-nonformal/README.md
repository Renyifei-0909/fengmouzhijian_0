# Evaluation v0 非正式合同示例

这个目录只用于验证文件合同、摘要、分组、固定分母和 CLI 输出。它不是数据集、不是算法 baseline，也不是比赛指标。

## 真实性边界

- 两段 MP4 都是 FFmpeg 生成的 1 秒、320×240、5 fps 几何人形视频，可由 ffprobe/ffmpeg 完整解码；不包含真人、现场信息或隐藏类别 metadata，source 明确为 `demo_fixture`。
- `model/model-statement.json` 声明 `implementation_kind=fixture`、`synthetic=true`，其 artifact 摘要绑定 `constant-label-baseline.json`，不是视觉模型。
- `tools/generate_predictions.py` 只读取 public cases 和常量 fixture model，不读取 private labels、不从文件名猜类别，也不输出伪造 confidence。
- 常量 fixture 对两例都输出 `helmet_compliant`，因此第二例自然错误，示例 accuracy 为 0.50、阈值失败；这只是合同 smoke 结果。
- 所有输出必须保持 `gate_status=not_eligible`、`compliance_claim_eligible=false`。
- 本目录为了可复现而同时附带 `private/`；这个文件夹名本身不是安全边界。真实运行时，模型容器只能挂载 `public/` 和冻结模型，private labels 必须由 QA 放在不同只读根中隔离保管。
- `record_sha256` 分别绑定 `private/annotations/` 中的两条占位记录；label/metric/baseline/model 摘要也各自绑定对应文件。

## 重放 fixture prediction

从本目录执行：

```bash
python tools/generate_predictions.py \
  --cases public/cases.jsonl \
  --model model/constant-label-baseline.json \
  --output runs/predictions.validation.jsonl
```

回归测试会在一个完全不含 `private/` 的临时 sandbox 中重放，并要求输出与提交版本逐字节一致。

可先核对本目录所有固定制品：

```bash
shasum -a 256 -c CHECKSUMS.sha256
```

## 通过 development runner 重放

当前 `run-plan.json` 的外部固定 SHA-256 为：

```text
1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739
```

这个值必须由调用方预先保存并作为独立参数传入，不能在同一命令中从待验证的 plan 现算“预期值”。从项目根目录执行：

```bash
cd backend
python scripts/evaluate.py run-dev \
  --plan ../examples/evaluation-v0-nonformal/run-plan.json \
  --expected-run-plan-sha256 1950c7887c560c3f8d494417fbdc4353a0b918ce9a4d5ea7995bd79c38faa739
```

也可以从项目根目录运行 `make evaluation-example-run-dev-check`。预期输出协议为 `evaluation.development-run.v0`，score accuracy 为 0.50、阈值失败，并固定 `formal_requested=false`、`gate_status=not_eligible`、`compliance_claim_eligible=false`。

运行器只把目标 split 的 public cases/media、固定模型制品和入口复制到临时推理视图；它不复制 private labels。但入口仍以当前用户的同一 UID 在本机运行，且网络策略明确为 `uncontrolled_development`，所以这不是机密性沙箱，也不能证明入口没有从其他本机路径读取 private labels。完整合同与威胁边界见 `../../docs/DEVELOPMENT_EVALUATION_RUNNER.md`。

## 固定 development evidence 示例

`development-evidence/` 是上述运行的公开、无日志、无逐条真值证据目录。它固定包含 manifest、run plan、predictions、public score 和 run summary；public score 已删除 private-label 路径、摘要和大小。当前 manifest SHA-256 为：

```text
ef91b6f5062f8bc7718f0fec2fe31eccc666be4d73be11eca3a029637bbec5de
```

从项目根目录离线核验：

```bash
cd backend
python scripts/evaluate.py verify-dev-bundle \
  --bundle ../examples/evaluation-v0-nonformal/development-evidence \
  --expected-manifest-sha256 ef91b6f5062f8bc7718f0fec2fe31eccc666be4d73be11eca3a029637bbec5de
```

预期 `integrity_status=passed`、`internal_consistency_status=passed`，同时保持 `manifest_authenticity=unsigned`、`content_origin_status=unverified`、`privacy_claim_status=not_provided`、`score_recomputed=false` 和正式资格为 false。外部摘要匹配只证明与调用方给定字节一致，不自动证明调用方可信、执行环境隔离或分数真实来源。

## 运行

从项目根目录执行：

```bash
cd backend
python scripts/evaluate.py validate \
  --manifest ../examples/evaluation-v0-nonformal/dataset.manifest.json

python scripts/evaluate.py score \
  --manifest ../examples/evaluation-v0-nonformal/dataset.manifest.json \
  --predictions ../examples/evaluation-v0-nonformal/runs/predictions.validation.jsonl \
  --model-statement ../examples/evaluation-v0-nonformal/model/model-statement.json \
  --split validation
```

预期：validate 返回 2 个 case；score 返回 `accuracy=0.5`、`threshold_status=failed`、`gate_status=not_eligible` 和 `compliance_claim_eligible=false`。

如果误加 `--formal`，必须被 `EVAL_DATASET_NOT_FORMAL` 拒绝。这是示例的核心安全断言，不应把 manifest 改成 formal 以追求“通过”。
