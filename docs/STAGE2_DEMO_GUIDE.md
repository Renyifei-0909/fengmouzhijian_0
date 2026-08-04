# 第二阶段本地演示与自测手册

> 本文的功能与测试数字保留对应阶段快照；当前依赖安装必须使用
> `STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md` 的 locked sync，当前测试数以该文为准。

## 1. 现在可以演示什么

当前候选版本可以连续演示：

```text
项目/设计基线
  -> 视频或图片上传
  -> 服务端格式、大小与 SHA-256
  -> 持久化处理任务
  -> 原始证据鉴权回看
  -> 人工复核
  -> JSON/HTML 结构化报告
  -> 可恢复封存 Saga
  -> ZIP 证据包、Merkle Root、本地链式哈希台账
  -> 8 项独立完整性复算

候选 finding
  -> reviewer 人工分诊
  -> 负责人/期限与整改 Attempt
  -> 锁定原项目、原设计基线的复验证据
  -> reviewer 显式判定 resolved / not_resolved
  -> 新报告与新 proof 关闭；原报告字节不变
```

总览、项目、项目详情、报告、溯源、真实闭环和告警整改页读取真实 API。设备、数据分析、GIS、数据看板和模型服务仍是显式标记的原型页。

算法边界没有变化：本地种子使用 `demo_fixture`，输出是确定性合成夹具，`evidence_grade=false`、`accuracy_claim=null`。它不是真实视觉推理，不证明 85%/90% 指标。哈希链用于本地篡改检测，不是区块链、司法存证或可信时间戳。

## 2. 当前本机地址

服务运行时直接打开：

- 监管总览：<http://127.0.0.1:5173/dashboard>
- 项目列表：<http://127.0.0.1:5173/projects>
- 真实闭环：<http://127.0.0.1:5173/backend-workflow>
- 告警与整改：<http://127.0.0.1:5173/alarms>
- 溯源查询：<http://127.0.0.1:5173/traceability>
- API 文档：<http://127.0.0.1:8000/docs>
- 后端就绪检查：<http://127.0.0.1:8000/api/v1/readyz>

本地演示 Key：

```text
operator: local-operator-change-me
reviewer: local-reviewer-change-me
auditor:  local-auditor-change-me
```

这些 Key 只用于本机演示。前端开发环境会读取它们，不能用于公网或正式部署。

## 3. 从零启动

### 3.1 后端

```bash
cd fengmou-zhijian
cd backend
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
source .venv/bin/activate
FENGMOU_ALLOW_DEMO_ANALYZER=true \
FENGMOU_OPERATOR_API_KEY=local-operator-change-me \
FENGMOU_REVIEWER_API_KEY=local-reviewer-change-me \
FENGMOU_AUDITOR_API_KEY=local-auditor-change-me \
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

视频上传依赖 `ffprobe`。启动前可运行 `ffprobe -version` 检查。

### 3.2 前端

另开终端：

```bash
cd fengmou-zhijian/frontend
npm ci
VITE_OPERATOR_API_KEY=local-operator-change-me \
VITE_REVIEWER_API_KEY=local-reviewer-change-me \
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

### 3.3 空数据库生成演示种子

交付包不携带运行时数据库和上传目录。后端以 `demo_fixture` 启动后，在另一个终端运行：

```bash
cd fengmou-zhijian/backend
source .venv/bin/activate
python scripts/seed_stage2_demo.py
python scripts/seed_remediation_demo.py
```

两个脚本只调用公开 API。`seed_stage2_demo.py` 生成基础报告/存证链；`seed_remediation_demo.py` 生成 finding、分诊、整改、Attempt、锁定原项目/基线复验、新报告/proof 和关闭状态。二者都使用无真人几何视频与 `demo_fixture`，重复运行会复用既有已完成记录。输出会给出本机 URL、ID 和 8 项核验结果。

## 4. 五分钟演示路径

1. 打开“监管总览”，说明统计来自后端持久化数据；“已批准基线覆盖率”只是“已批准唯一基线/登记基线”的 MVP 代理，不是施工形象进度。
2. 进入“项目管理”，打开 `DEMO-STAGE2-001`。
3. 在“验真任务”中点击最新一条任务的“预览证据”。页面应出现 `event-browser-compatible.mp4`、服务端原件/摘要已校验、证据 ID、SHA-256、MIME 和大小；视频可播放、暂停和拖动。
4. 切换到“结构化报告”，展示报告状态与下载入口；说明合成夹具报告始终是 `reviewed_demo`。
5. 切换到“可信档案”或打开“溯源查询”，输入档案号或摘要并执行在线核验。应看到 archive、manifest、成员摘要、Merkle、record hash、ledger chain 和 metadata 共 8 项通过。
6. 打开“告警与整改”，先指出“待人工分诊”不算运营告警，`demo` 数量单独展示。打开已闭环案件，展示 Attempt、复验任务和 closure proof。
7. 对一个未绑定的 Attempt，使用“使用原项目与基线上传复验证据”进入深链。页面应锁定 Case/Attempt/Project/Baseline；复核员必须显式选择“已解决”或“未解决”，没有默认关闭值。
8. 打开“真实闭环联调”，展示从新上传到人工复核、报告和证据包的完整操作入口。如现场时间有限，不必再创建重复数据。
9. 最后说明：真实算法、授权通信施工数据和正式指标是下一门禁；当前页面不会把夹具数值冒充竞赛成绩。

## 5. 后端全量测试

```bash
cd fengmou-zhijian/backend
source .venv/bin/activate
python -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90
```

当前 Alpha9 候选版本结果：414 passed，覆盖率 90.21%（门禁为 90%）。不要把这个数字与
第一阶段冻结版本或 Alpha6 的历史结果混用。

原始证据安全专项：

```bash
python -m pytest -W error tests/test_evidence_content.py tests/test_openapi_contract.py -q
```

当前结果：56 passed。用例包含无 Key、三角色、路径逃逸、目录/成员 symlink、硬链接、magic/MIME/大小/SHA 冲突、同 fd 流式发送、换包竞态、短读、fd 关闭和单 Range。

## 6. 前端测试

```bash
cd fengmou-zhijian/frontend
npm ci
npm run verify
```

它依次运行 TypeScript、Vite 生产构建和中高风险依赖审计。当前候选版本构建通过，0 vulnerabilities。

手工检查原始证据预览：

1. 打开最新 H.264 证据，确认可播放并显示 320x240、1 秒样例；
2. 在浏览器 Network 中确认请求 URL 只有 evidence ID，Key 位于请求头而不在 URL；
3. 关闭预览后旧画面消失；
4. 用错误 Key 重启前端或后端时，应显示 401 错误而不是回退静态视频；
5. 登记大小超过 64 MiB 时，页面必须先显示风险确认且确认前不发起内容请求。生产视频仍需要短期播放票据/受控流式授权来保留 Range，当前前端确认后仍会完整载入 Blob。

已保存的浏览器验收截图：

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

Alpha9 两张 recovery-state 图片只通过浏览器路由替换任务详情响应来验证界面状态，未修改数据库、
报告、proof 或 ledger；真实故障与重启原子性由后端故障注入测试证明，不能把 UI 预览说成又一次
现场故障。

最新三张 completed/status 图片来自当前真实数据库：手动刷新返回 200，重新校验显示 8/8 通过；
错误 Key 的 401 暂停态也已在浏览器真实触发并恢复，但未保存含故意错误状态的最终交付截图。

## 7. 直接验证 HTTP Range

当前已运行本地库的 H.264 演示证据 ID：

```text
b4be4be7-1268-401e-973d-f1ace011c82b
```

若在空数据库中运行了种子脚本，应改用脚本输出的 `evidence_id`。

完整读取：

```bash
curl -i \
  -H 'X-API-Key: local-operator-change-me' \
  http://127.0.0.1:8000/api/v1/evidence-assets/b4be4be7-1268-401e-973d-f1ace011c82b/content
```

单 Range：

```bash
curl -i \
  -H 'X-API-Key: local-operator-change-me' \
  -H 'Range: bytes=0-15' \
  http://127.0.0.1:8000/api/v1/evidence-assets/b4be4be7-1268-401e-973d-f1ace011c82b/content
```

预期分别是 200 和 206；206 应包含 `Content-Range`、`Content-Length: 16`、`Accept-Ranges: bytes`、`Cache-Control: private, no-store` 和 `X-Content-Type-Options: nosniff`。无 Key 应为 401。

## 8. 当前剩余风险

- 全局角色 API Key 不是项目级、组织级或租户级 ACL；正式部署需真实登录/JWT/RBAC。
- 每个 Range 请求会先完整重算 SHA-256，安全但对大文件有 I/O 成本；生产环境需限流、并发上限和受控播放授权设计。
- `remote_http` 已由编排层把已验证 fd 贯穿到 multipart，不再按路径重新打开媒体；它仍是默认关闭的工程桥，未接入真实模型、授权数据或正式指标。
- Blob 回看会把完整媒体放入浏览器内存；当前只用于本地小样例。
- 真实 PPE/违章或隐蔽工程模型、授权数据、冻结 holdout 和正式 85%/90% 指标仍未完成。

在团队书面确认数据许可、唯一算法主线、指标口径、算力预算和负责人前，不应自动下载权重或启动训练。
