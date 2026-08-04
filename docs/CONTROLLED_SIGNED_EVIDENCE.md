# Ed25519 受控本地证据包 v0

## 1. 目的与结论边界

本合同在 unsigned development evidence 之上增加三类可验证事实：

1. 固定目录内五个成员的大小和 SHA-256 与 manifest 一致；
2. manifest 由外部固定 trust store 中具备 `controlled_run_bundle_signer` 角色的 Ed25519 公钥验证通过；
3. manifest、run plan、predictions、summary、score 与一次性 registry 的 consumed snapshot 绑定到同一个 run、attempt、dataset、模型和 core result commitment。

它仍是 `controlled_local`，不是正式盲测。成功输出固定为：

```json
{
  "verdict": "valid_nonformal_signed_evidence",
  "authorization_authenticity": "self_asserted_unsigned",
  "isolation_status": "unverified",
  "formal_execution_completed": false,
  "compliance_claim_eligible": false,
  "replay_status": "not_checked",
  "trusted_timestamp_status": "not_provided"
}
```

外层签名证明受信 key 对这些字节签过名，但不会把 registry 内自报的 capability、QA approval 或 actor 自动升级为可信身份，也不证明模型进程禁网、看不到私有标签或训练集无重叠。

## 2. 固定目录

```text
controlled-run-evidence/
├── bundle-manifest.json
├── bundle-manifest.ed25519
├── inputs/run-plan.json
├── public/predictions.jsonl
├── registry/attempt.json
└── results/
    ├── run-summary.json
    └── score.json
```

- signature 是恰好 64 字节的 raw Ed25519 detached signature；
- manifest 和 trust store 必须是唯一 canonical JSON：UTF-8、key 排序、无空白、结尾一个换行；
- 禁止额外文件、符号链接、FIFO、设备文件和路径跳转；
- development bundle 与 controlled-local bundle 使用不同 schema 和验证入口，不能互相冒充。

## 3. 无循环 commitment

registry 不能把最终 manifest 摘要作为 `result_sha256`，因为 manifest 又要包含 registry snapshot。v0 先对四个 core members 的描述符计算域分离摘要：

```text
inputs/run-plan.json
public/predictions.jsonl
results/run-summary.json
results/score.json
```

```text
core_result_sha256 = SHA256(
  "evaluation.controlled-run-core-member-set.v0\n"
  || canonical_json(core_member_descriptors)
)
```

然后按以下顺序封包：

1. 完成四个 core members；
2. 用 core digest 调用 registry finalize；
3. 导出带 `registry_instance_id`、`result_commitment_profile` 的 consumed attempt snapshot；
4. 构造并 canonicalize manifest；
5. 由仓库外 QA signer 对 domain-separated message 签名；
6. 原子发布固定目录。

私钥不进入本后端、仓库、证据包或日志。本项目只实现公钥验签器。

## 4. 外部 trust store

验证必须提供独立 trust store 和调用方预先保存的 trust-store SHA-256。包内不能自带并信任自己的公钥。

```json
{
  "schema_version": "evaluation.ed25519-trust-store.v0",
  "trust_store_id": "team-controlled-signers",
  "generation": 1,
  "keys": [
    {
      "key_id": "qa-bundle-signer-01",
      "algorithm": "ed25519",
      "public_key_encoding": "raw_base64",
      "public_key_base64": "<32-byte raw key, canonical base64>",
      "public_key_fingerprint_sha256": "<SHA256(raw key bytes)>",
      "roles": ["controlled_run_bundle_signer"],
      "status": "active"
    }
  ]
}
```

- key ID 和 fingerprint 必须分别唯一；
- `revoked` key 一律拒绝；因为当前没有可信时间戳，不能声称签名发生在吊销前；
- trust store 摘要不匹配时，验签在读取成员前失败；
- 临时传入一个任意公钥不等于组织身份可信，必须由团队在受控渠道固定 trust store digest。

## 5. 验证命令

```bash
cd backend
python scripts/evaluate.py verify-controlled-bundle \
  --bundle /path/to/controlled-run-evidence \
  --trust-store /path/to/trust-store.json \
  --expected-trust-store-sha256 <预先固定值> \
  --expected-manifest-sha256 <可选的预先固定值> \
  --expected-run-id <可选> \
  --expected-attempt-id <可选> \
  --expected-dataset-manifest-sha256 <可选>
```

返回码：0 表示签名、完整性和本地 registry snapshot 绑定通过；2 表示合同/格式错误；3 表示摘要、签名、身份或状态不一致；4 表示本机验证后端或 I/O 故障。成功不使用阈值退出码 6。

自动化回归：

```bash
cd backend
python -m pytest -W error tests/test_evaluation_controlled_bundle.py
```

测试覆盖 Ed25519 固定向量、错误签名、成员篡改、非 canonical manifest、trust-store pin、revoked key、非法正式/合规升级、额外文件、签名长度和外部 run ID pin。

## 6. 依赖和未完成项

- 验签后端固定为 `cryptography==49.0.0`；依赖缺失时结构化返回 `EVAL_CONTROLLED_SIGNING_BACKEND_UNAVAILABLE`，绝不降级为 unsigned；
- 当前没有 QA 私钥托管、HSM/KMS、可信时间戳或证书吊销历史；
- 当前没有独立低权限 worker、网络隔离、私有标签 broker 和 runtime/image pin；
- 当前没有训练清单与 holdout hash/group overlap 证明；
- 当前没有数据库/API `EvaluationRun` 去做 bundle replay/重复导入拒绝，因此离线 verifier 固定 `replay_status=not_checked`；
- 下一阶段完成这些能力前，不得称“正式 EvaluationRun”或生成 `ComplianceClaim`。
