# 一次性 Holdout Registry v0

## 1. 已实现范围

仓库已经实现一个**单机、本地、失效关闭**的 holdout 消费状态机，用来把“同一留出集不能靠换模型或换 run 重跑”落实为可测试的数据库约束。它提供：

- 由 `dataset_manifest_sha256 + split + policy_generation` 计算的域分离消费键；
- 消费键摘要和三元组两道 `UNIQUE` 约束，模型摘要不参与 key；
- SQLite `WAL + synchronous=FULL + foreign_keys=ON + busy_timeout`，并在连接后读回检查；
- `BEGIN IMMEDIATE` 下的原子 reservation，多进程竞争只允许一个成功；
- `reserved → exposure_committed → consumed` 三阶段状态；
- `gate_holdout` 的事故锁定和受限 replacement generation；
- `final_holdout` 永久禁止同一数据摘要开新 generation；
- append-only 事件记录、固定 JSON 回执和查询 CLI。

每个 registry 首次成功初始化时会生成并永久保存 `registry_instance_id`；每条 attempt snapshot 都携带该值。`finalize` 还固定 `result_commitment_profile=evaluation.controlled-run-core-member-set.v0`，防止把一个裸 SHA-256 误解为不同类型的哈希对象。复制数据库会复制 instance ID，因此它不能替代固定唯一权威路径或 replay 数据库。

这只是协调 primitive，不是正式评测执行器。当前 capability、QA approval 和 actor 都是调用方提交的**未签名声明**，因此所有输出固定包含或继承以下边界：

```json
{
  "authorization_authenticity": "self_asserted_unsigned",
  "formal_execution_completed": false,
  "compliance_claim_eligible": false
}
```

`holdout-commit-exposure` 也固定返回 `trusted_broker_release_authorized=false`。它只证明本机状态已落盘，不能授权真正的 broker 发放私有样本或标签。

## 2. 消费边界

消费不是“分数算完”的时刻。边界取下面两件事中更早的一件：

1. 执行器第一次可能读取 gate/final 的 case、媒体或其他输入；
2. scorer 第一次可能读取 private labels。

受控系统未来必须按以下顺序调用：

```text
reserve
  → 准备模型、runtime、只读挂载与禁网环境
  → durable commit exposure
  → broker 才能发放 holdout capability
  → 封存 predictions 与受控结果包
  → finalize(result bundle SHA-256)
```

本仓库尚无可信 broker，所以不会自动执行第三步。

## 3. 状态与事故规则

```text
reserved
  ├─ exposure_committed ── consumed
  └─ incident_review

exposure_committed
  └─ incident_review
```

- 没有 lease、TTL、reset、release、unlock 或 delete；任何崩溃都不会让 key 回到 available。
- `consumed` 是正常终态；阈值没通过仍是合法结果，不能改写为事故。
- `final_holdout` 可以锁定事故供审计，但永不授权同一 dataset digest 重跑。
- `gate_holdout` 只有在前序仍是 `reserved`、即 registry 明确尚未提交 exposure 时，才可用单独持久化的 `incident_retry` approval 创建 `generation + 1`。
- 已经 `exposure_committed` 的 gate 只能 `incident_lock`，不能用同一数据摘要重跑。
- 历史 attempt、事件和 incident authorization 永不清除。

## 4. CLI

入口为 `backend/scripts/evaluate.py`：

```bash
python scripts/evaluate.py holdout-reserve \
  --registry /trusted/private/root/holdout.sqlite3 \
  --request reservation.json

python scripts/evaluate.py holdout-commit-exposure \
  --registry /trusted/private/root/holdout.sqlite3 \
  --attempt-id attempt-001 \
  --actor local-broker-state-writer

python scripts/evaluate.py holdout-finalize \
  --registry /trusted/private/root/holdout.sqlite3 \
  --attempt-id attempt-001 \
  --result-sha256 <完整受控结果包摘要> \
  --actor local-result-writer

python scripts/evaluate.py holdout-lock-incident \
  --registry /trusted/private/root/holdout.sqlite3 \
  --attempt-id attempt-001 \
  --approval incident-approval.json

python scripts/evaluate.py holdout-get \
  --registry /trusted/private/root/holdout.sqlite3 \
  --attempt-id attempt-001

python scripts/evaluate.py holdout-list \
  --registry /trusted/private/root/holdout.sqlite3
```

请求和 approval 使用严格 UTF-8 JSON：拒绝重复 key、NaN/Infinity、额外字段、符号链接输入和超限文件。CLI 成功返回 0；合同错误返回 2；重复 key、非法状态或重跑返回 3；数据库持久化错误返回 4。registry 不使用阈值退出码 6。

## 5. 部署边界

registry 只允许位于当前 OS 用户拥有、权限为 `0700` 或更严格、且祖先路径不经过符号链接的目录；数据库以 `0600`、`O_EXCL/O_NOFOLLOW` 创建，并在 SQLite 打开后复核设备号和 inode。

这些检查仍然**不防恶意 same-UID 进程**，也不证明 NFS、共享卷或多机锁语义安全。正式 controller 必须：

- 固定唯一 registry 路径，不允许算法运行方通过参数换库；
- 把 registry service、QA signer、holdout broker 与模型执行用户分离；
- 仅在受控私有根上运行，不把 SQLite 文件放到 NFS/多机共享卷；
- 引入可验证签名、可信公钥目录、运行镜像/runtime pin、禁网、只读挂载和训练集重叠报告；
- 让 broker 只接受通过签名验证的授权，而不是本 v0 的 unsigned receipt。

在这些条件完成前，不得使用“正式盲测”“QA 已签署”“可信执行环境”或“可生成 ComplianceClaim”等表述。
