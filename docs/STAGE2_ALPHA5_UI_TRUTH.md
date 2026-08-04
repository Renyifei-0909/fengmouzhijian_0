# Stage 2 Alpha 5：跨页面真实性一致性

## 结论

本增量只修复前端跨页面的真实性展示，不改变后端合同、数据库或算法能力。总览、项目任务、
项目报告、真实闭环和报告中心现在全部复用 `frontend/src/lib/truth.ts`，不再分别解释
`stub`、`demo_fixture` 和 `remote_http`。

## 修改

- 总览最近任务使用持久化 `job.result.analysis_mode / job.analyzer_name` 生成 TruthBadge；
- 项目任务列表显示“远程单样本推理（未评测）”等统一标签；
- 选中项目任务预览证据时，同时展示模型身份、摘要、limitations、证据等级和准确率声明；
- 项目报告列表把 `reviewed_non_evaluated` 显示为“已复核 · 未评测”；
- 项目底部最新任务说明不再只输出原始 adapter 名称；
- 总览运行边界显式纳入 remote_http 参考服务；
- 删除项目统计区重复的“已批准”卡片，并把四项指标改为四列布局。

## 验证

- `npm run verify`：TypeScript、Vite production build、npm audit 全部通过；
- production 单文件：502.10 kB，gzip 134.64 kB；
- npm audit：0 vulnerabilities；
- 浏览器总览：远程真实性标签出现 1 次；
- 浏览器项目任务：远程真实性标签出现 1 次；
- 浏览器项目报告：未评测报告标签出现 1 次；
- 浏览器控制台：0 error。

浏览器留证：

- `output/playwright/stage2-alpha5-dashboard-truth.png`；
- `output/playwright/stage2-alpha5-project-truth.png`。

后端应用代码在本增量中未修改；Alpha 4 的 365 passed / 90.34% 全量门禁仍是最近一次
后端严格验证，本轮另外复核了 `:8000/readyz`、`:8012/healthz` 和真实前端可达性。

## 真实性边界

统一标签只降低 UI 误导风险，不会让 STUB 变成真实模型。真实 non-mock predictor、冻结
授权数据、EvaluationRun 和 85%/90% 指标仍未完成。
