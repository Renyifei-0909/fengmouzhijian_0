# 第二阶段 Alpha10：算法接入真实性与 Readiness 0

日期：2026-07-14  
状态：`engineering candidate / algorithm training blocked`  
范围：数据工作副本只读审计、pilot fail-closed 诊断、远程 model/STUB 身份隔离

## 本轮结论

Alpha10 没有启动训练，也没有把参考 STUB 改写成真实算法。它完成了真实算法接入前最容易被忽略的两层边界：

1. Construction-PPE 解压工作副本的每个字节必须与固定 ZIP 相同，不能只看文件数量和标签格式；
2. 远程服务必须机器可判定地说明自己是 `model` 还是 `stub`，业务后端不能再把 STUB 记录成非合成远程结果。

当前平台工程链可继续演示，但算法主线仍是 `blocked`：许可/肖像与展示权、唯一路线、健康训练环境、固定权重、可信授权、原子启动器、通信施工事件真值和正式 holdout 均未完成。

## Algorithm Readiness 0

新增：

- `backend/app/algorithm_readiness.py`；
- `backend/scripts/algorithm_preflight.py`；
- `backend/tests/test_algorithm_readiness.py`；
- [`algorithm-data/ALGORITHM_READINESS_0.md`](algorithm-data/ALGORITHM_READINESS_0.md)。

生产工作副本实测：

| 项目 | 结果 |
|---|---:|
| 固定 ZIP | 178,415,813 bytes / 2,852 entries / SHA-256 匹配 |
| 解压普通文件 | 2,844 |
| 与 ZIP 逐字节不一致 | 0 |
| 解压额外文件 | 0 |
| 图片 / 标签 / 配对框 | 1,416 / 1,426 / 11,521 |
| YOLO 非法行 | 0 |
| 已登记孤立标签 | 10 个 / 93 框 |
| 已登记精确重复标注 | 1 行 |
| Audit | `passed`，约 3.7 秒 |

Audit 读取固定归档和解压树的同一文件身份，拒绝软链接、硬链接、特殊文件、未知额外文件、路径漂移、非有限数和超出 `1e-6` 容差的坐标。标签按相对路径配对，不再用 basename 静默覆盖嵌套同名文件。

Readiness 0 的 `preflight` 仍固定返回：

```json
{
  "status": "blocked",
  "pilot_launch_eligible": false,
  "truth_flags": {
    "training_started": false,
    "subprocess_started": false,
    "network_accessed": false,
    "files_written": false,
    "formal_metric_available": false,
    "compliance_claim_eligible": false
  }
}
```

原因不是“代码还没把 false 改成 true”，而是 Readiness 0 只有未签名的自述 JSON和只读路径快照。它不能证明真实授权、Python/依赖健康，也不能消除检查后替换权重或抢占 run 目录的竞态。未来启动器必须在同一进程内验证可信授权、环境 lock、`pip check`、设备/解码 smoke、数据与权重摘要，并原子创建 run 目录；不能拿一份旧 readiness JSON 直接启动。

## 远程算法合同 P1 修复

远端响应现在强制包含：

```json
{
  "runtime": {
    "mode": "model",
    "model_loaded": true,
    "capabilities": ["construction_evidence_analysis"]
  }
}
```

或在显式 test/demo 中：

```json
{
  "runtime": {
    "mode": "stub",
    "model_loaded": false,
    "capabilities": []
  }
}
```

约束：

- 默认期望 `model`；production/development 配置 `stub` 会拒绝启动；
- `mode=model` 必须 `model_loaded=true` 且至少一个唯一 capability；
- `mode=stub` 必须 `model_loaded=false`；
- runtime mode 纳入任务 adapter 版本指纹，旧任务不能跨配置重试；
- model 配置收到 STUB 会返回 `REMOTE_RUNTIME_MODE_MISMATCH`；
- 显式 STUB 固化为 `remote_contract_stub`、`synthetic=true`，finding 只能进入 demo 语义；
- 两种模式都固定 `evidence_grade=false`、`accuracy_claim=null`，单次推理不能冒充 EvaluationRun。

参考服务永久声明 `stub/false/[]`。即使向模板注入另一个 predictor，也不会把它升级成真实 model 服务；真实 predictor 必须使用独立部署和真实模型制品身份。

## 验证证据

- 后端全量：`454 passed`，`-W error` 通过；
- 应用代码：6,599 statements / 619 miss / **90.62%**；
- Algorithm Readiness 专项：`27 passed`，模块覆盖率 95%；
- 远程、参考服务、集成和合同专项：`103 passed`；
- `compileall`、后端 `pip check`：通过；
- OpenAPI：94,278 bytes，SHA-256 `cd3d3ffd31af4c28c8bb3492ea456124d457b8714543788cbb6be8978e772dde`；
- remote request schema：`08b65038de51f3f350beb76661317a04ee83c0c956561e419531657dab34d05e`；
- remote response schema：`e54bb4fb763be3ec6ef6c2bc41fdbfa3b3e3d5f66e3967af2bb6ab5d3c4e9248`；
- 前端 TypeScript、70 modules 生产构建和 npm audit：通过，0 vulnerabilities。

真实 socket STUB smoke 使用隔离 SQLite/存储、仓库内无真人 H.264 视频和独立端口完成：

- job `c6dd8aa1-2282-499e-9572-b359026e6656`；
- proof `18766dec-dbbb-47b4-a69e-c759d918f377`；
- `runtime=stub/false/[]`；
- `provenance.kind=remote_contract_stub`；
- `synthetic=true`、`evidence_grade=false`、`accuracy_claim=null`；
- proof 8/8 为 true；
- `confirmed_open_operational=0`。

该隔离服务验收后已停止；上述 ID 位于 `/tmp` smoke 数据库，不属于默认演示库。当前保留运行的是 5173 前端、8000 默认演示后端和 8012 参考 STUB 服务。

## QA 对抗审查与修复

初版 Readiness 0 曾存在两个 fail-open：保持数量不变地替换解压图片/标签仍能 audit passed；虚构未签名审批加任意可执行文件能产生 launch eligible。QA 对抗用例复现后已修复，并新增：

- 解压树内容漂移；
- 未登记额外文件和硬链接；
- 嵌套同名 stem；
- 空白/重复审批角色；
- 祖先目录软链接；
- 过期、超 72 小时或摘要不匹配记录；
- 不安全/非空 run 目录；
- 未签名记录永不解除启动门禁。

仍需未来启动器解决的不是 Readiness 0 内部 bug，而是它有意不具备的能力：可信授权验证、解释器和依赖实测、制品 fd 持有、run 目录原子预留、超时/温度/内存监督与可终止训练 worker。

## 下一步人工门禁

1. 团队与指导老师冻结唯一 PPE 主线、负责人和内部 pilot 范围；
2. 书面确认 Construction-PPE 图片、人物展示、训练、权重、比赛提交与 AGPL/Ultralytics 使用边界；
3. 建立隔离 `fengmou-yolo` 环境和可复现 lock，实测 `pip check`、MPS/CPU、OpenCV 全量解码；
4. 固定模型权重、代码、配置和标签规范摘要；
5. 实现同进程原子启动器后，才允许生成派生 development 数据和 1 epoch 短 pilot；
6. 真实 model 服务返回 `model/true/capabilities` 后，用授权通信施工视频做事件窗 E2E；
7. QA 冻结独立 holdout 并执行正式 EvaluationRun，才可能讨论 85%/90%。

在此之前，静态图检测 mAP、STUB HTTP 成功、人工复核通过和证据包 8/8 都不能写成模型准确率或赛事达标。
