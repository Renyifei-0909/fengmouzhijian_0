# 第一阶段交付、演示与自测手册

> 本文保留第一阶段功能与测试快照；当前依赖安装、漏洞状态和测试数以
> `STAGE2_ALPHA16_REPRODUCIBLE_DEPENDENCIES.md` 为准。

## 1. 这一阶段可以交付什么

当前版本适合作为挑战杯项目的第一阶段工程底座和团队联调版本，已经具备：

- 浏览器图形界面与 FastAPI 后端；
- 视频/图片输入、服务端 SHA-256、视频容器检查；
- 项目与设计基线；
- 持久化分析任务、状态恢复、人工复核；
- 结构化 JSON/HTML 报告；
- ZIP 证据包、Merkle Root、manifest 摘要、追加式本地哈希链和独立核验；
- 默认关闭的真实远程算法桥；
- Evaluation v0、development runner、unsigned evidence verifier；
- 一次性 holdout registry；
- Ed25519 controlled-local evidence verifier。

真实视觉模型、正式数据、准确率、隔离盲测、可信时间戳和区块链没有完成，也没有伪造。演示时应明确：`demo_fixture` 是合成数据，`stub` 只验证流程，本地哈希链不是公链。

## 2. 当前可直接访问

若本机服务仍在运行：

- 真实闭环页面：<http://127.0.0.1:5173/backend-workflow>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/readyz>

本地演示 key：

```text
operator: local-operator-change-me
reviewer: local-reviewer-change-me
auditor:  local-auditor-change-me
```

这些 key 只用于本机演示，会进入浏览器环境，不得用于正式部署。

## 3. 从零启动

项目根目录：

```bash
cd /Users/xsp/Documents/Codex/2026-07-10/5-waddle/outputs/fengmou-zhijian
```

### 3.1 后端

当前机器已有验证过的环境：

```bash
source /Users/xsp/Documents/Codex/2026-07-10/5-waddle/work/venvs/fengmou/bin/activate
cd backend
ffprobe -version
FENGMOU_ALLOW_DEMO_ANALYZER=true \
FENGMOU_OPERATOR_API_KEY=local-operator-change-me \
FENGMOU_REVIEWER_API_KEY=local-reviewer-change-me \
FENGMOU_AUDITOR_API_KEY=local-auditor-change-me \
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

若环境被删除，重新创建：

```bash
cd backend
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
source .venv/bin/activate
```

### 3.2 前端

另开终端：

```bash
cd /Users/xsp/Documents/Codex/2026-07-10/5-waddle/outputs/fengmou-zhijian/frontend
VITE_OPERATOR_API_KEY=local-operator-change-me \
VITE_REVIEWER_API_KEY=local-reviewer-change-me \
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

首次运行先执行 `npm ci`。

## 4. 推荐现场演示流程

打开 `/backend-workflow` 后按顺序操作：

1. 检查页面顶部后端状态为 ready，填入 operator/reviewer key。
2. 创建一个匿名演示项目，例如代码 `DEMO-001`、名称“通信管线隐蔽工程演示”。
3. 创建设计基线：工点 `SITE-A01`、工序 `TRENCH-BEFORE-BACKFILL`、版本 `design-v1`。
4. 上传真实可解码 MP4。仓库自带无真人几何样例：

   ```text
   examples/evaluation-v0-nonformal/public/assets/event-001.mp4
   ```

5. 算法选择：
   - `demo_fixture`：展示完整 UI/报告效果，但必须说明是合成结果；
   - `stub`：只走工程流程，不产生物理量测；
   - `remote_http`：默认禁用，未接真实算法时不要启用。
6. 提交后观察任务从 queued/running 进入 `needs_review`。
7. 用 reviewer key 打开结果，核对原始文件 SHA、基线 SHA、分析类型和真实性标记。
8. 人工批准，生成结构化报告和证据包。
9. 点击完整性核验，应显示 8 项检查通过。
10. 下载 JSON 报告和 ZIP；修改 ZIP 内任一字节后再独立校验应失败。

演示叙事建议固定为：

```text
视频输入 → 服务端摘要/格式检查 → 算法适配器 → 人工复核
→ 结构化报告 → 证据包/Merkle/哈希链 → 独立核验
```

不要演示或口头承诺未经验证的 85%/90% 准确率、实时直播、公链上链或自动 GIS 对齐。

## 5. 自动化测试

### 5.1 全量后端

```bash
source /Users/xsp/Documents/Codex/2026-07-10/5-waddle/work/venvs/fengmou/bin/activate
cd /Users/xsp/Documents/Codex/2026-07-10/5-waddle/outputs/fengmou-zhijian/backend
python -m pytest -W error --cov=app --cov-report=term-missing --cov-fail-under=90
```

当前基线：282 tests passed，应用代码覆盖率 90.05%。

### 5.2 前端

```bash
cd /Users/xsp/Documents/Codex/2026-07-10/5-waddle/outputs/fengmou-zhijian/frontend
npm run verify
```

它依次执行 TypeScript 检查、Vite 生产构建和 `npm audit --audit-level=moderate`。当前 0 vulnerabilities。

### 5.3 契约和固定评测示例

```bash
cd /Users/xsp/Documents/Codex/2026-07-10/5-waddle/outputs/fengmou-zhijian
PYTHON=/Users/xsp/Documents/Codex/2026-07-10/5-waddle/work/venvs/fengmou/bin/python
make PYTHON="$PYTHON" backend-contracts backend-quality
make PYTHON="$PYTHON" evaluation-example-check
make PYTHON="$PYTHON" evaluation-example-run-dev-check
make PYTHON="$PYTHON" evaluation-example-evidence-check
```

固定示例故意只有 accuracy 0.50、阈值失败，用于证明评分合同没有通过改标签或改分母制造绿色结果。

### 5.4 Registry 与签名包

```bash
cd backend
python -m pytest -W error \
  tests/test_evaluation_registry.py \
  tests/test_evaluation_registry_cli.py \
  tests/test_evaluation_controlled_bundle.py
```

这些测试覆盖：同一 holdout 多进程只能一个 reserve 成功、换模型不能绕过、崩溃不释放、暴露前 durable commit、final 禁重跑、Ed25519 固定向量、成员/签名篡改、revoked key 和外部 trust-store pin。

## 6. 常用 CLI

```bash
cd backend
python scripts/evaluate.py --help
```

主要命令：

- `validate`：检查冻结数据集合同；
- `score`：严格覆盖评分；
- `run-dev`：运行固定 development predictor；
- `verify-dev-bundle`：验证 unsigned 开发证据；
- `holdout-reserve / holdout-commit-exposure / holdout-finalize`：本地一次性状态机；
- `holdout-lock-incident / holdout-get / holdout-list`：事故和审计；
- `verify-controlled-bundle`：用外部 pin 的 trust store 验证 Ed25519 controlled-local 包。

Registry 操作会永久消耗 key，没有 reset/force/release。只在临时目录或正式受控流程中运行，不要对计划保留的真实 holdout 随意试命令。

## 7. 独立校验下载的证据包

```bash
cd backend
python scripts/verify_bundle.py /path/to/ARC-xxx.zip \
  --expected-archive-sha256 <API 返回的 archive_sha256>
```

返回 0 表示摘要/Merkle/manifest 等内部一致；不表示区块链、签名身份或可信时间。

## 8. 常见问题

- `ModuleNotFoundError`：没有激活项目虚拟环境；先执行 `source .../work/venvs/fengmou/bin/activate`。
- `ffprobe` 错误：安装 ffmpeg，或先用 JPG/PNG 测试图片链路。
- 端口被占用：运行 `lsof -nP -iTCP:8000 -sTCP:LISTEN` 和 `lsof -nP -iTCP:5173 -sTCP:LISTEN`。
- 401：前端 key 与后端环境变量不一致。
- 403 demo disabled：启动后端时没有设置 `FENGMOU_ALLOW_DEMO_ANALYZER=true`。
- 视频 422：文件扩展名/MIME 不匹配或容器无法被 ffprobe 解码。
- `make check` 使用了系统 Python：先激活 venv，或显式传 `PYTHON=/absolute/path/to/venv/bin/python`。

## 9. 下一阶段建议

第一阶段到此停下。下一阶段按优先级建议：

1. 团队确认算法/数据负责人和合法数据源，执行已写好的 48 小时路线门禁；
2. 接第一个真实非 mock baseline；
3. 增加独立 worker、租约/心跳和多副本所有权；
4. 实现数据库/API `EvaluationRun`、bundle replay 拒绝和前端评测面板；
5. 最后补独立低权限/禁网执行、可信 holdout broker、QA 私钥托管和可信时间戳；
6. 获得明确批准后再下载权重、安装训练栈和启动 baseline 训练。
