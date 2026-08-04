# Stage 2 Alpha9：整改关闭恢复与真实恢复态

日期：2026-07-14

## 1. 本轮结论

Alpha9 没有增加新的模型能力，也没有把演示夹具升级为竞赛指标证据。本轮只解决一件工程问题：
当报告、ZIP 和 ledger 已经发布，而最终数据库提交失败、提交确认丢失或进程重启时，系统必须
恢复同一份封存操作，且网页必须区分“可继续封存”和“完整性异常，禁止自动继续”。

## 2. 后端变更

- 最终提交前失败：事务回滚后，case 保持 `verification_pending`，Attempt 不绑定 report/proof；
  重启从 `ledger_appended` 继续，case version 只增加一次，关闭审计只写一次。
- 提交确认丢失：请求 Session 不再依据 identity map 推断失败，而是用新的只读 Session 核对
  job/review/report/proof/operation、唯一成功审计、整改闭环图和已发布制品。只有完成态数据库图
  全部一致才按成功返回，且不写 `seal_attempt_failed`。
- staging 清理是封存成功后的 housekeeping。普通清理错误或 staging 被替换为符号链接都不会
  把已完成业务重新分类为失败，且不会跟随链接删除目标；后续 replay/startup 可再次清理。
- Alpha9 新建的 proof 数据库 UUID 由固定 `archive_id` 中的 UUID 派生，因此最终提交前失败后
  report ID、archive ID、ledger row 和 proof ID 均稳定。Alpha8 及更早已完成 proof 保留原 UUID，
  不迁移、不重写。
- readiness 现在拒绝 `completed` 但仍保留 `last_error`、完成态数据库图漂移或未知 operation state，
  避免“成功状态携带失败事实”被静默接受。

本机制仍是单机数据库 + 本地文件系统的可恢复 Saga，不是跨数据库、对象存储和外部账本的
分布式原子事务。

## 3. 恢复状态 API

`GET /api/v1/verifications/{job_id}` 新增必填 `recovery`：

| action | 含义 | 可自动操作 |
|---|---|---|
| `none` | 当前没有显式恢复动作 | 否 |
| `retry_analysis` | 分析任务失败；是否可重试同时核对最新失败审计分类、持久化 analyzer 版本与当前配置 | 由 `retryable` 决定 |
| `resume_sealing` | SealOperation 已持久化且未完成 | reviewer 可继续同一封存 |
| `integrity_review` | operation 缺失、manual attention 或 job/operation 图不一致 | 否，先人工核查 |

响应同时包含 `operation_state`、`attempt_count`、`last_error` 和 `updated_at`。它们描述持久态，
不代表真实模型质量或现场结论。

## 4. 前端变更

- 分析失败按钮只在最新 `analysis_failed` 审计明确记录 `retryable=true` 且 analyzer 配置未漂移时
  允许点击；输入/完整性/确定性合同错误和缺少可信分类的历史失败均 fail closed。
- 封存未完成时明确显示“当前没有可交付的新报告或证据包”、operation state、尝试次数和失败
  原因，并提供 reviewer 专用“继续封存”。
- 整改复验恢复会重放 Attempt 已持久化的 `resolution_decision` 与原 resolution note，不重新默认
  选择“已解决”，也不生成新的业务结论。
- `integrity_review` 只展示阻断提示，不提供普通重试按钮。
- 有持久化封存错误时停止自动轮询；临时网络失败采用最长 15 秒指数退避；401/403 会暂停轮询，
  显示最后成功读取时间和手动刷新入口，避免错误 Key 持续高频请求。

页面继续沿用
[`STAGE2_ALPHA6_IMAGE2_CONCEPT.md`](./design/STAGE2_ALPHA6_IMAGE2_CONCEPT.md)
中的“失败必须给出原因和显式恢复入口”原则，本轮不重复生成新概念图。

## 5. 故障与重启证据

新增的整改跨重启集成测试真实走公开 API 和 lifespan 恢复顺序：

1. 先形成 source proof，使 ledger 已有 row 0；
2. 创建 operational candidate、人工分诊、整改 Attempt 和同项目/基线复验；
3. 在 ledger row 1 与三件制品已写、最终数据库 commit 尚未执行时注入失败；
4. 断言失败后 case/version/Attempt/report/proof 没有半提交；
5. 关闭第一个应用，用相同 SQLite 和 storage 启动第二个应用；
6. 断言启动恢复使用同一 operation/review/report/archive/proof ID，ledger、JSON、HTML、ZIP
   逐字节不变，case 只关闭一次；
7. 重放相同 review payload，不增加 attempt count、ledger row 或业务审计。

此外覆盖数据库已 commit 后丢失确认、完成态数据库图漂移、staging 清理错误/符号链接、历史随机
proof UUID 兼容、completed operation 残留错误、失败重试分类和 recovery 分类矩阵。

## 6. 当前验证

- 后端：414 tests collected；全量严格回归通过，`-W error` 通过，应用覆盖率 90.21%，门禁 90%。
- 前端：TypeScript、Vite 生产构建和 `npm audit --audit-level=moderate` 通过；70 modules，
  `dist/index.html` 540.79 kB，gzip 145.57 kB，0 vulnerabilities。
- OpenAPI：94,278 bytes；SHA-256
  `cd3d3ffd31af4c28c8bb3492ea456124d457b8714543788cbb6be8978e772dde`。
- 本机后端、前端和参考 STUB 分别运行在 `127.0.0.1:8000`、`:5173`、`:8012`；
  `readyz` 当前为 ready。
- 真实已完成整改记录在最新版页面加载，手动刷新真实返回 200 并更新时间；使用错误 Key 时
  后端真实返回 401、页面暂停轮询，恢复正确 Key 后再次返回 200；正常启动控制台 0 error。
- 浏览器点击“重新校验”后，当前 closure proof 的 archive/manifest/member/Merkle/record/ledger/
  metadata 共 8 项全部通过。
- 恢复卡片截图通过 Playwright 路由只修改浏览器响应生成，未修改数据库、报告、proof 或 ledger；
  它只验证 UI 状态，不是假装又发生了一次真实故障。375×812 下 `scrollWidth=375`，无横向溢出。

截图：

```text
output/playwright/stage2-alpha9-completed-desktop.png
output/playwright/stage2-alpha9-recovery-state-preview.png
output/playwright/stage2-alpha9-recovery-state-mobile.png
output/playwright/stage2-alpha9-latest-completed-desktop.png
output/playwright/stage2-alpha9-latest-completed-mobile.png
output/playwright/stage2-alpha9-latest-status-mobile.png
output/playwright/stage2-alpha9-final-desktop.png
output/playwright/stage2-alpha9-final-mobile.png
```

前端对 query generation 切换同时清空旧文件、任务与 busy，所有写操作由 action token 全局串行；
迟到响应不能覆盖新案件，也不能提前解除另一项操作的 busy。

## 7. 仍未完成

- `FindingCaseCommand.payload_sha256` 是幂等命令摘要和状态历史，不是独立第三方哈希链；数据库管理员
  仍可能同时重写数据库记录。对外只能宣传 proof ZIP + manifest + Merkle + 本地 append-only ledger
  的篡改检测边界。
- 多副本、外部对象存储、独立 worker、租约/心跳和提交结果协调仍未实现。
- 真实 PPE/违章或隐蔽工程模型、合法授权数据、冻结 holdout、受控 EvaluationRun 和 85%/90%
  指标仍未完成。

## 8. 安全交付包

从项目根目录运行：

```bash
./scripts/package_delivery.sh stage2-alpha9-2026-07-14
```

脚本拒绝覆盖既有包，排除运行数据库、上传与报告制品、环境文件、密钥/证书、模型权重、
`node_modules`、构建目录、coverage/cache、日志和旧 ZIP；同时检查 ZIP 路径、符号链接和必备成员，
并在 ZIP 外生成 `.sha256`。摘要不写回 ZIP 内部，避免自引用改变归档字节。
