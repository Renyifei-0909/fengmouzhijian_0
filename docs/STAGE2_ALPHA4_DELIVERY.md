# Stage 2 Alpha 4 交付说明（2026-07-14）

## 1. 本增量结论

Alpha 4 完成了“真实算法尚未到位时，算法组如何接入”的可运行边界：业务后端已经能向
独立算法进程发送真实 HTTP multipart 请求，参考服务严格核对身份、摘要、大小、媒体和
幂等语义，再把合同内结果送回人工复核、结构化报告和哈希档案。

参考服务的默认 predictor 是空输出 STUB。它证明协议和交付链可以运行，不证明已有 PPE、
违章或隐蔽工程识别能力，也不通过真实算法 Gate。

## 2. 新增与修复

- `backend/app/reference_analyzer.py`：独立 FastAPI 参考服务；
- `examples/remote-analyzer-reference/reference-stub-artifact.json`：实际字节摘要固定的 STUB 身份制品；
- `backend/scripts/seed_remote_reference_demo.py`：公开 API 全链 smoke；
- `backend/tests/test_reference_remote_analyzer.py`：24 个合同与对抗场景；
- `frontend/src/lib/truth.ts`、`frontend/src/components/ui/TruthStatus.tsx`：共享真实性分类与展示；
- 联调页复核备注不再读取可变下拉框，只读取持久化任务事实；
- 远程任务结构化显示模型/代码/配置/请求/响应摘要与 limitations；
- 报告页把 `reviewed_non_evaluated` 显示为琥珀色“已复核 · 未评测”，不再把制品真实混同为算法已评测；
- 新增参考服务环境样例、Makefile 入口和完整接入指南。

测试过程中发现并修复：注入的空 `BoundedIdempotencyCache` 因实现 `__len__` 而在布尔判断
中为 false，曾被工厂静默替换。工厂现对 settings、predictor、cache 全部使用显式
`is not None`，测试锁定同 key 重放、异 identity 冲突和共享 cache 行为。

## 3. 严格验证

2026-07-14 在项目虚拟环境中复跑：

- 后端全量：365 passed，`-W error` 通过，应用代码覆盖率 90.34%；
- 参考服务专项：24 passed；
- 参考服务 + remote bridge + integration + contract artifacts：59 passed；
- OpenAPI 与远程请求/响应 Schema 均逐字节校验通过；
- `compileall`、`pip check` 通过；
- 前端 TypeScript、Vite 单文件生产构建通过，npm audit 为 0 vulnerabilities；
- 浏览器实测报告页能看到“已复核 · 未评测”和“制品真实存在不等于算法已经评测”，控制台无错误。

前端构建：501.84 kB，gzip 134.52 kB。

## 4. 真实进程 smoke 留证

当前主演示库新增一条明确标注的远程参考链：

- 项目代码：`REMOTE-REFERENCE-001`；
- 项目 ID：`d5c7a2e6-bb8b-43bb-b378-19b44f0ce25c`；
- 任务 ID：`96203d2e-9a4a-4e05-8fd3-c9e4d5f61449`；
- 任务状态：`approved`；
- analyzer：`remote_http`；
- 档案 ID：`9f5828ea-9a0b-4fd2-a3a5-a9a36ba64bb5`；
- 档案核验：8/8 为 true；
- 参考服务模型身份：`fengmou-reference-stub / stub-v0.1`；
- STUB artifact SHA-256：`15695d7820543d1217651812af54e91e71a30d355f8a2b851734a4f2483e454e`。

实际网络路径：

```text
合成 H.264
  -> 业务 FastAPI :8000
  -> fd-bound remote_http multipart
  -> 独立参考服务 :8012
  -> remote_model_unvalidated
  -> 人工复核
  -> reviewed_non_evaluated 报告
  -> purpose=review / evidence_grade=false 档案
  -> 8 项完整性重算通过
```

持久化结果固定为 `evidence_grade=false`、`accuracy_claim=null`、空 observations，并带
STUB limitation。这条记录只用于本机协议与展示验收。

## 5. 当前本地服务

- 前端：[http://127.0.0.1:5173/reports](http://127.0.0.1:5173/reports)
- 真实闭环：[http://127.0.0.1:5173/backend-workflow](http://127.0.0.1:5173/backend-workflow)
- 远程参考项目：[http://127.0.0.1:5173/projects/d5c7a2e6-bb8b-43bb-b378-19b44f0ce25c](http://127.0.0.1:5173/projects/d5c7a2e6-bb8b-43bb-b378-19b44f0ce25c)
- 业务 API：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 参考服务健康检查：[http://127.0.0.1:8012/healthz](http://127.0.0.1:8012/healthz)

浏览器证据：`output/playwright/stage2-alpha4-reports-truth.png`（位于项目工作区上层的
`output/` 验收目录，不打入源码包）。

## 6. 你可以直接操作

1. 打开报告中心，第一条报告应为琥珀色“已复核 · 未评测”；
2. 点击眼睛图标，核对证据等级为非指标、准确率声明为无；
3. 打开真实闭环页，选择远程单样本推理；当前参考服务启用，因此选项可用；
4. 上传仓库内 `examples/stage2-demo/event-browser-compatible.mp4`，走一条新任务；
5. 用复核员 Key 批准后，下载报告/证据包并点击重新校验；
6. 按 `docs/REFERENCE_ANALYZER_SERVICE.md` 在隔离端口复现完整 smoke。

本地演示 Key 与此前相同，只用于本机：operator `local-operator-change-me`、reviewer
`local-reviewer-change-me`、auditor `local-auditor-change-me`。参考服务 Bearer 不进入前端，
由业务后端持有。

## 7. 仍未完成的 P0

- 团队正式确认算法/数据、QA/评估、项目统筹和材料负责人；
- 书面确认数据授权、现场媒体外发、脱敏、保存和删除策略；
- 冻结唯一 PPE/违章或隐蔽工程路线、标签口径和指标定义；
- 接入第一个真实 non-mock predictor 与实际权重/代码/配置摘要；
- 用冻结授权数据执行独立 EvaluationRun，得到可回溯样本级结果；
- 真实算法 E2E、失败案例、性能和指标报告。

在这些门禁完成前，不能把参考服务、合成视频、人工批准或漂亮 UI 写成“算法已实现”或
“达到 85%/90%”。
