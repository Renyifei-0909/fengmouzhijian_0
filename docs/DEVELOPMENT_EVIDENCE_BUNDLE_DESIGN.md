# Development run 证据包 v0 合同与实现

状态：publisher、verifier、runner/CLI 接入和固定示例均已实现。该包只保存开发运行的完整性与内部绑定证据，不能证明模型未见私有真值、不能复算分数，也不能获得正式评测或合规资格。

## 1. 固定文件树

```text
<run-id>.dev-evidence/
├── bundle-manifest.json
├── inputs/run-plan.json
├── public/predictions.jsonl
└── results/
    ├── score.json
    └── run-summary.json
```

校验器必须拒绝缺失、额外文件、符号链接和额外目录。v0 不包含数据集 manifest、private labels、annotations、原始 stdout/stderr、模型大文件或 runtime；避免把私有标签路径/摘要或入口主动写出的敏感日志发布到公开包。

## 2. 公开 score 投影

当前内部 `evaluation.score.v0` 含 `labels_private_sha256` 与字节数，不能原样发布。`results/score.json` 应使用新的 `evaluation.development-public-score.v0` 白名单投影：保留数据集 ID/版本、public cases/manifest/split/metric 摘要、模型身份、predictions 身份、聚合指标和阈值状态；删除 private label 路径、摘要和大小，并固定：

```text
formal_requested=false
gate_status=not_eligible
compliance_claim_eligible=false
private_label_records_included=false
offline_rescore_supported=false
```

聚合指标本身仍由私有真值导出，可能泄露类别 support，不能用于正式 holdout 的公开发布。

## 3. 发布与验证接口

已实现：

```python
publish_development_evidence_bundle(
    destination,
    *,
    run_plan_snapshot,
    predictions_snapshot,
    run_result,
) -> DevelopmentEvidenceReceipt

verify_development_evidence_bundle(
    bundle_root,
    *,
    expected_manifest_sha256=None,
) -> dict
```

`run_development_plan()` 会在临时 predictions 被删除前调用 publisher；CLI 已增加 `run-dev --evidence-dir` 和 `verify-dev-bundle`。验证只检查成员 SHA/size、固定文件集、严格 predictions、public cases/case-id roster 摘要和字段交叉绑定，不重新调用私有评分器，也不要求当前 evaluator 版本仍与旧包相同。

## 4. 原子发布要求

1. destination parent 必须已存在，目标路径必须不存在；
2. 在同一 parent 创建 staging 目录，固定权限写入四个成员并逐文件 `fsync`；
3. 计算按路径排序的成员描述符摘要，最后写 manifest；
4. 在 staging 上执行完整 verifier；
5. 锁内再次检查目标不存在；macOS 使用 `renamex_np(RENAME_EXCL)`，Linux 使用 `renameat2(RENAME_NOREPLACE)`，不安全的平台不会静默退化；随后 `fsync` parent；
6. 普通失败清理 staging；清理失败附着原错误；若目录已发布但 parent `fsync` 失败，返回 `DURABILITY_UNCONFIRMED`、manifest 摘要和 `published=true`。

## 5. 已通过的验收测试

- canonical fixture 可发布并在不含原数据集/private 根的目录离线校验；
- 文件树恰好五个文件，四个成员 SHA/size 与 manifest 一致；
- score 投影不含 private label 路径、摘要、大小，包中不含 raw logs；
- predictions 身份在 manifest、summary、score 三处一致；
- 0.50/阈值失败的合法开发运行仍能发布，全部正式资格字段保持 false/not_eligible；
- 目标已存在时拒绝且原内容不变；写入/rename 故障不产生最终目录；
- 修改、删除或额外加入任一成员均校验失败；
- CLI 可从任意工作目录创建和验证。
- 非合作创建者在 rename 前抢占空目标目录时不会被覆盖；
- ground-truth/annotation、private artifact path、嵌入式本机路径和 case-id roster 改写均被拒绝；
- receipt/CLI 不返回本机 bundle 绝对路径；
- 旧 evaluator 摘要的完整包不依赖当前源码版本，仍可离线验证。

当前固定示例 manifest SHA-256：`ef91b6f5062f8bc7718f0fec2fe31eccc666be4d73be11eca3a029637bbec5de`。验证结果明确区分 `manifest_authenticity=unsigned` 与 `expected_manifest_sha256_status=matched/not_supplied`，并固定 `content_origin_status=unverified`、`privacy_claim_status=not_provided`。

## 6. 明确留给后续受控执行

一次性 gate/final holdout registry、失败重跑审批、QA 私有标签隔离、独立低权限/容器/禁网、完整 runtime/image/hardware 摘要、训练集重叠检测、QA 签名与可信时间戳、正式 `EvaluationRun` 数据库/API 和 `ComplianceClaim` 均不属于本开发证据包。
