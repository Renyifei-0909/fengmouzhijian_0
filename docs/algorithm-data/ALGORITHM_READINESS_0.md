# Algorithm Readiness 0：只读数据审计与 Pilot 前置门禁

更新日期：2026-07-14  
状态：`implemented / blocked-by-human-approval-and-model-artifact`  
范围：Construction-PPE 内部开发 baseline；**不执行训练**

## 1. 本阶段解决什么

`backend/scripts/algorithm_preflight.py` 提供两个只读命令：

- `audit`：核对工作副本、原 ZIP、LICENSE、`data.yaml`、split 数量、YOLO 行、已知孤立标签、重复行和人工确认的跨 split 近重复引用；
- `preflight`：在 audit 之上诊断六项书面记录、数据/模型摘要绑定、记录时效、四个独立角色、候选运行时文件、固定权重摘要，以及隔离且不覆盖旧结果的 run 目录。

两个命令都不会安装依赖、访问网络、下载权重、创建派生数据、写文件、启动子进程或占用 GPU。Readiness 0 故意**永远不返回启动授权**：未签名 JSON 不能证明真实批准，普通可执行文件不能证明 Python/依赖健康，只读检查也不能把已检查文件描述符和 run 目录原子交给后续进程。因此输出固定 `status=blocked`、`pilot_launch_eligible=false`；它只列出尚缺的门禁。

## 2. 当前真实结果

2026-07-14 在本机实际运行：

| 检查 | 结果 |
|---|---|
| 注册 ZIP SHA-256 / 大小 / 2,852 个安全条目 | 通过 |
| LICENSE 与 `data.yaml` SHA-256 | 通过 |
| 1,416 图片、1,426 标签、11,521 个配对框 | 通过 |
| YOLO 5 列、class 0–10、finite、坐标 `epsilon=1e-6` | 通过 |
| 解压树 2,844 个文件逐字节匹配固定 ZIP，且无额外文件/硬链接 | 通过 |
| 10 个孤立标签与 1 条精确重复标注行 | 与登记缺陷一致 |
| 3 组人工复核跨 split 近重复的路径仍存在 | 通过；本命令不重新计算 dHash |
| 六项 pilot 批准 | 缺失 |
| 独立、健康、可复现的 YOLO 环境 | 缺失 |
| 固定并经批准绑定的模型权重 | 缺失 |
| 可信授权、运行环境健康证明、同进程原子启动交接 | Readiness 0 未实现，固定阻断 |
| Pilot 资格 | `blocked`，退出码 2 |

因此当前口径是：**工作副本已落盘且只读审计通过；正式采用、派生数据和训练仍未批准。** Audit 通过不等于许可通过，也不等于数据质量足以支撑正式评测。

## 3. 复现命令

从项目根目录运行：

```bash
PYTHON=../../work/venvs/fengmou/bin/python
DATASET=../../work/datasets/construction-ppe/extracted
ARCHIVE=../../work/datasets/construction-ppe/construction-ppe.zip

"$PYTHON" backend/scripts/algorithm_preflight.py audit \
  --dataset-root "$DATASET" \
  --archive "$ARCHIVE" \
  | jq '{status,failed:[.checks[]|select(.ok==false)|.id],truth_boundaries}'
```

预期：退出码 0、`status=passed`。这只证明注册工作副本没有相对本审计合同发生漂移。

当前 fail-closed 演示：

```bash
set +e
"$PYTHON" backend/scripts/algorithm_preflight.py preflight \
  --dataset-root "$DATASET" \
  --archive "$ARCHIVE" \
  --run-root /Users/xsp/Documents/Codex/2026-07-10/5-waddle/work/runs/construction-ppe-pilot-001 \
  > /tmp/fengmou-preflight.json
code=$?
set -e

jq '{status,pilot_launch_eligible,failed:[.checks[]|select(.ok==false)|.id],truth_flags}' \
  /tmp/fengmou-preflight.json
test "$code" -eq 2
```

当前会明确阻断：`pilot.approval_present_and_valid`、`pilot.training_python_regular`、`pilot.weight_artifact_regular`、`pilot.weight_artifact_approval_binding`，以及固定的 `pilot.trusted_authorization_verified`、`pilot.runtime_health_verified`、`pilot.atomic_launch_handoff_available`。

## 4. 批准文件合同

批准文件必须是严格 JSON，禁止重复键、NaN/Infinity 和额外字段，并至少包含：

```json
{
  "schema_version": "fengmou.algorithm-pilot-approval.v1",
  "approval_id": "pilot-approval-001",
  "route_status": "accepted",
  "scope": "internal_development_only",
  "dataset": {
    "source_id": "ultralytics-construction-ppe-work-copy-2026-07-10",
    "archive_sha256": "bef8dcb599aa4e9d9f5e602cb6fa7143d3c84d7f6a0ff40463d7f2a4c2632ccc",
    "license_sha256": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
    "data_yaml_sha256": "bfbc2471c75a82beaca2c255b7814d7eaf3087f191f1c7e43c8d8e90a27e961a"
  },
  "model": {"artifact_sha256": "<64 lowercase hex>"},
  "confirmations": {
    "create_isolated_environment": true,
    "download_and_store_pinned_weights": true,
    "create_derived_data_and_run_artifacts": true,
    "use_local_compute_for_smoke_pilot": true,
    "internal_development_only": true,
    "dataset_usage_boundary_confirmed": true
  },
  "approvers": {
    "project_lead": "<name-or-team-id>",
    "data_license_owner": "<name-or-team-id>",
    "qa_owner": "<different-name-or-team-id>",
    "advisor": "<name-or-team-id>"
  },
  "issued_at": "2026-07-14T12:00:00+08:00",
  "expires_at": "2026-07-15T12:00:00+08:00",
  "authorization_authenticity": "self_asserted_unsigned"
}
```

该合同只能记录团队自述，当前没有数字签名、外部身份系统、撤销登记或防重放状态，所以无论静态字段是否齐全，输出仍固定：

- `authorization_cryptographically_verified=false`；
- `formal_metric_available=false`；
- `formal_dataset_adopted=false`；
- `compliance_claim_eligible=false`。

模型摘要必须在记录文件中预先绑定。`--weight-artifact` 必须是非软/硬链接普通文件且 SHA-256 一致；`--training-python` 也必须是非软/硬链接可执行普通文件，但该检查只证明“有这样一个文件”，不证明它是 Python 或依赖健康。Run 目录必须使用绝对路径、位于源码与数据根之外，并且不存在或为空；该观察仍不能消除检查后被抢占的竞态。

## 5. 已知数据缺陷不会被 Audit“洗白”

- 10 个孤立 train 标签包含 93 个无图框；
- `train/image187.txt` 有 1 条精确重复标注；另有高 IoU 候选需要人工裁决；
- 官方 split 含至少 3 组人工确认的跨集合近重复，不能作为可信独立测试；
- `none` 语义不明确，且没有 `no_vest`；
- 静态图片不含人员—PPE 归属、轨迹和事件窗真值；
- 通用 AGPL-3.0 文本不自动补足逐图片来源、肖像权、展示权、权重发布权和闭源交付权利链。

因此后续派生集只能叫 `development_train/development_validation`。正式 gate/final 仍必须来自授权通信施工视频，并按来源、采集会话、现场、相机、人员和工程实体分组隔离。

## 6. 下一门禁

只有以下事项全部由人类完成，才允许继续：

1. 项目统筹、算法/数据、QA 和指导老师签署唯一 PPE 主线；
2. 数据/许可负责人书面确认 Construction-PPE 的内部比赛用途、展示、训练与权重边界；
3. 确定项目是否完整 AGPL 开源，或取得所用训练软件/模型的适当许可；
4. 建立新的隔离且 `pip check` 健康的固定训练环境；
5. 固定模型制品及 SHA-256，并让批准文件绑定该摘要；
6. 实现受控启动器：在同一进程内验证可信授权、解释器与 lock、`pip check`、设备/解码 smoke、权重和数据摘要，并原子创建 run 目录；直接消费已验证文件描述符或不可变副本，不能消费旧 readiness JSON 后盲目启动；
7. Readiness 0 只能作为该启动器的诊断输入之一，不能单独解除训练门禁。

任何一项缺失时，应保持 `training_started=false`，不得把公开静态图 mAP 写成视频事件 accuracy，更不得写成正式 85%/90% 达标。
