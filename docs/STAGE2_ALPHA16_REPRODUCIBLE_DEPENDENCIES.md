# 第二阶段 Alpha16：后端通用依赖锁与干净环境复现

日期：2026-07-28  
状态：`engineering candidate / Windows clean install verified / Linux runtime unverified`

## 1. 结论

Alpha16 把后端从“只固定直接依赖、安装时仍重新解析传递依赖”推进到以下状态：

- `backend/uv.lock` 使用 uv 的通用锁格式，锁定运行时和 `dev` extra 的完整解析图、制品 URL、
  SHA-256 与平台/Python marker；
- uv 本身固定为 `0.11.32`，`uv-bootstrap.txt` 固定该发布当前 19 个 PyPI 制品 SHA-256；
  项目只接受系统已有的兼容 Python，并在仓库命令中禁止自动下载 Python；
- PEP 517 构建后端固定为 `setuptools==83.0.0`，独立约束文件保存 wheel/sdist 两个上游
  SHA-256；正式构建使用 `--require-hashes`；
- 仓库自带的独立校验器同时核对声明、锁内项目 metadata、PyPI 来源、制品哈希、构建约束和
  依赖边，不依赖 uv 自己给出同源判断；
- 后端 Dockerfile 不再执行重新解析的 `pip install .`：builder 固定 Python 3.12.13
  多架构 manifest digest，哈希引导 uv，只按 lock 安装运行时图，再安装哈希约束构建的 wheel；
  当前机器没有 Docker，因此这仍是未运行的容器候选；
- 在一个此前不存在的 Windows 临时目录中，用系统 Python 3.12.13 按锁创建环境、安装
  `dev` 图、执行 `pip check` 和回归；另用仅安装锁定运行时依赖的环境安装最终 wheel 并核对
  OpenAPI；
- 首次 OSV 审计发现 pytest 1 组、Starlette 5 组已知问题后，没有忽略告警，而是升级到
  `pytest==9.1.1`、`FastAPI==0.140.7`、`Starlette==1.3.1`，并为 Starlette 新测试客户端增加
  dev-only `httpx2==2.9.1`；升级后的 Windows 3.12 和 Linux x86_64/3.12 **解析视图**均为
  0 known vulnerabilities。

这消除了“传递 Python 包每次安装重新漂移”的已知缺口，但不是 Linux 发布验收、冷缓存离线
安装、签名供应链、SBOM、SLSA provenance 或位级可复现构建证明。

## 2. 为什么直接固定版本仍不够

原 `pyproject.toml` 已把 FastAPI、SQLAlchemy 等直接依赖写成 `==`，但 pip 仍会在每次安装时
重新选择其传递依赖。上游新发布、撤回、marker 分支或构建依赖变化，都可能让两次
`pip install -e '.[dev]'` 得到不同环境。

`uv.lock` 解决的是解析图：它记录跨平台/Python marker 的精确包版本和候选制品哈希；
`uv sync --locked` 只从该图选择当前环境所需子集，锁过期时拒绝修改。构建系统是另一条依赖
路径，因此又单独固定并哈希约束 setuptools。两者不能互相替代。

## 3. 锁与失败关闭策略

### 3.1 项目配置

`backend/pyproject.toml` 当前包含：

```toml
[build-system]
requires = ["setuptools==83.0.0"]
build-backend = "setuptools.build_meta"

[tool.uv]
required-version = "==0.11.32"
python-preference = "only-system"
build-constraint-dependencies = ["setuptools==83.0.0"]
```

运行时、测试依赖仍全部使用精确版本。`httpx2` 只在 `dev` extra 中，正式运行时仍使用项目远程
analyzer 所需的 `httpx==0.28.1`；wheel smoke 也确认运行时环境没有安装 `httpx2`。

### 3.2 通用锁

最终 `backend/uv.lock`：

- 306,098 bytes；
- lock format `version = 1`、`revision = 3`；
- `requires-python = ">=3.11"`；
- 47 个 package block：1 个本地 editable 项目、46 个 PyPI registry 包；
- 唯一 registry 为 `https://pypi.org/simple`；
- registry 制品 URL 只允许 `https://files.pythonhosted.org/`，每个候选制品都有小写
  SHA-256 和正整数大小；
- SHA-256：
  `ea6ce39184328f1c215aaedb77b819d5043546d84baf3001e3b504a88d30d7c8`。

“通用”表示锁文件包含 marker 分支，并不表示本轮实际在每个 OS、架构和 Python 版本安装。

### 3.3 引导与构建输入

`backend/uv-bootstrap.txt` 为 `uv==0.11.32` 保存该版本在 PyPI 当前发布的 19 个 wheel/sdist
SHA-256。pip 的 `--require-hashes` 会拒绝不在集合中的 uv 制品；Windows amd64 选择
`win_amd64` wheel 的实际安装已通过。

`backend/build-constraints.txt` 为 `setuptools==83.0.0` 保存官方 wheel 和 sdist 的两个
SHA-256。`uv build --require-hashes` 会在构建前强制匹配；`pyproject.toml` 与 lock manifest
也必须包含相同精确约束。

### 3.4 独立仓库校验器

`backend/scripts/verify_dependency_lock.py` 只使用 Python 标准库，当前检查：

1. uv 必须是精确三段版本，bootstrap 声明必须与之相同且每个制品带 SHA-256，
   Python preference 必须为 `only-system`；
2. 项目、optional extra 和构建依赖必须是 marker-free 的精确 `==` 声明；
3. `pyproject.toml`、lock 内 editable 项目 metadata/依赖边、optional extra 必须逐项一致；
4. lock 只能含一个 `editable = "."` 项目，其余只能来自规范 PyPI；
5. 每个 registry 包至少有一个带 SHA-256 和大小的 PyPI 制品；
6. 所有依赖边都必须指向锁中包；
7. 构建声明、uv build constraint、lock manifest 和哈希约束文件必须一致；
8. 规范化后的 extra/group 不得碰撞或重复。

9 个测试含真实锁正例，以及非 PyPI 源、缺失制品哈希、项目 metadata 漂移、非精确 uv
版本、自动 Python 下载偏好、构建约束/哈希/lock manifest 漂移等负例。

## 4. 标准命令

uv 不在自身 lock 中，使用独立哈希约束引导。本轮在隔离临时环境验证了 Windows amd64
wheel，并把项目已有、被忽略的 `.venv` 同步到最终锁；没有修改系统 Python：

```bash
python -m pip install --require-hashes -r uv-bootstrap.txt
```

从 `backend/` 执行：

```bash
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
uv audit --locked --preview-features audit-command --no-python-downloads
uv build --build-constraints build-constraints.txt --require-hashes --no-python-downloads
```

项目根目录提供对应目标：

```bash
make backend-lock-check
make backend-sync
make backend-audit
make backend-build
```

当前 Windows 主机没有 GNU Make，因此本轮逐条执行并验证了这些目标的底层命令，没有把
“Makefile 已写入”当成目标实际运行证据。

更新依赖时必须显式修改精确声明，运行 `uv lock`，审阅 lock 差异，再执行校验、漏洞审计、
干净环境同步和回归。常规检查使用 `--locked`，不允许隐式改锁。

## 5. 漏洞处理

初次 `uv audit --locked` 对 43 个 registry 包返回 12 条 OSV 记录；别名合并后是：

- pytest 临时目录处理 1 组，修复门槛为 9.0.3；
- Starlette Host/path/form/StaticFiles/HTTPEndpoint 5 组，最高修复门槛为 1.3.1。

采用当前兼容版本后：

| 解析视图 | 审计包 | 已知漏洞 | adverse status |
|---|---:|---:|---:|
| Windows x86_64 / Python 3.12 | 46 | 0 | 0 |
| Linux x86_64 / Python 3.12 | 46 | 0 | 0 |

`uv audit` 在 0.11.32 中仍标为 experimental；结果是 2026-07-28 对 OSV 当前数据的一次查询，
不是持续监控或“无漏洞”证明。Linux 行只让 uv 选择 Linux marker 分支再查询，没有启动 Linux
解释器、安装 wheel 或执行测试。

前端没有代码/依赖变更；同轮复核保持 React Router 8.3.0，
`npm audit --audit-level=moderate` 为 0 vulnerabilities。

## 6. 验证证据

### 6.1 干净环境

最终环境路径在创建前明确断言不存在。使用系统 CPython 3.12.13：

- `uv sync --extra dev --locked --no-python-downloads`：resolved 47，当前 marker 实际安装
  45 distributions；
- `uv pip check`：45 packages compatible；
- 版本核对：FastAPI 0.140.7、Starlette 1.3.1、pytest 9.1.1、httpx2 2.9.1；
- lock 独立校验：47 packages、46 registry packages、1 build requirement、2 build hashes、
  19 uv bootstrap hashes；
- `uv-bootstrap.txt` 摘要集合与 PyPI 0.11.32 JSON 的 19 个发布制品逐项相等，
  missing/extra 均为 0；
- 独立 bootstrap venv：pip 按哈希安装 `uv 0.11.32`，`pip check` 通过。

这是新虚拟环境复现；uv 的全局下载缓存不是空的，不能写成 cold-cache/offline 复现。

### 6.2 后端测试与合同

- 依赖锁策略：`9 passed`；
- 依赖/配置/OpenAPI/远程合同/系统/工作流/Worker 指标与观测组合：
  `64 passed, 12 skipped`；
- 明确排除 9 个 POSIX-only Evaluation/Readiness 模块和 10 个原生 Windows 无法构造的
  文件系统攻击用例：`268 passed, 27 skipped, 10 deselected`；
- 原生 Windows 全量诊断：
  `153 failed, 317 passed, 31 skipped`，coverage `70.15%`；
- 153 个失败仍精确分为 Algorithm Readiness 14、Evaluation 129、链接/FIFO/打开文件替换
  10，没有因依赖升级新增类别；
- OpenAPI 与远程请求/响应制品漂移检查、SQLite/PostgreSQL schema SQL 离线编译、
  `compileall` 和 `pip check` 通过；
- Evaluation dataset 的 `validate/score` 在原生 Windows 仍以
  `EVAL_SECURE_OPEN_UNAVAILABLE` 安全拒绝；unsigned development-evidence 静态完整性校验
  通过，但 `threshold_status=failed`，不能改写为评测通过。

OpenAPI 未因 FastAPI/Starlette 安全升级发生字节漂移：

- 113,391 bytes；
- SHA-256
  `8a3d6c91bbb1c82773e5781cd0ad02f23dfe6a706cee3be4bb90fe3753a7cc42`。

### 6.3 构建与 wheel 安装

哈希约束构建产出：

| 制品 | 大小 | 本次构建 SHA-256 |
|---|---:|---|
| `fengmou_backend-0.2.0-py3-none-any.whl` | 173,312 bytes | `d933ea356ece2ddb02e5954181c66d7c6a6c5ac0765d9a7b57b135c504d8843d` |
| `fengmou_backend-0.2.0.tar.gz` | 250,405 bytes | `cd5ecd06ad811869756fcc369a4d474e7f45adf4febc6f1696230dcd28d546cc` |

独立复核发现旧 sdist 自动带入 tests，却遗漏锁校验入口和包级 README。本轮新增
`MANIFEST.in` 与 `backend/README.md` 后，最终 sdist 明确包含 README、`uv.lock`、
`uv-bootstrap.txt`、`build-constraints.txt` 和 `scripts/verify_dependency_lock.py`，排除
tests/缓存；构建日志不再
出现缺少标准 README 的警告。从解压后的 sdist 根目录运行独立策略校验和
`uv lock --check` 均通过。最终 wheel 保持纯运行时：57 个成员，包含 metrics、迁移和
dist-info metadata，不包含 lock、构建约束、校验脚本或测试。

随后在另一个新环境中执行 `uv sync --locked --no-install-project`，只安装当前平台的 34 个
锁定运行时包，再以 `--no-deps` 安装 wheel：

- wheel 安装后 `pip check`：35 packages compatible；
- 安装版本 `fengmou-backend==0.2.0`；
- FastAPI 0.140.7、Starlette 1.3.1；
- `httpx2_installed=False`，证明测试客户端依赖未泄漏到运行时；
- 从已安装 wheel 生成 OpenAPI：113,391 bytes，摘要与提交制品一致。

上述两个制品摘要只标识本次产物。未设置并验证 `SOURCE_DATE_EPOCH` 等位级可复现构建条件，
不得声称不同时间构建会得到相同 wheel/tarball 字节。

### 6.4 Dockerfile 静态候选

后端 Dockerfile 当前：

- builder/runtime 都固定
  `python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`
  多架构 OCI index；
- builder 以 `uv-bootstrap.txt --require-hashes` 安装 uv；
- 先执行独立策略校验和 `uv lock --check`，再以
  `uv sync --locked --no-install-project` 安装运行时依赖；
- wheel 使用 setuptools 哈希约束构建，再以 `--no-deps` 安装进 runtime venv 并执行
  `uv pip check`；
- runtime stage 不携带 uv、编译源码或 dev extra。

仓库测试锁定以上关键语句并拒绝重新引入 `pip install .`。但本机没有 Docker/Podman/WSL，
没有执行 build、启动、healthcheck、非 root、ffprobe 或 Compose smoke。Debian apt 的 ffmpeg
版本也没有锁定，前端 Node/nginx 基础镜像仍是浮动 tag；因此不能声称整个 Compose 可复现。

### 6.5 前端

- TypeScript 通过；
- Vite 7.3.6：116 modules；
- 单文件产物 557.80 kB，gzip 149.30 kB；
- npm audit：0 vulnerabilities。

本轮没有改 UI 或 API 交互，也没有新增浏览器行为，因此没有重复把浏览器操作包装成 Alpha16
证据；Alpha14 的真实浏览器故障注入仍是当前 UI 证据。

## 7. 未解除边界

1. 只有 Windows x86_64 / Python 3.12.13 完成真实 clean sync、测试、构建和 wheel smoke；
   Linux、macOS、Python 3.11/3.13+ 未实际安装/运行。
2. Linux marker 漏洞审计不是 Linux runtime 证据；Linux 全量 `-W error` 与 90% coverage
   门禁仍未执行。
3. PyPI HTTPS 与 SHA-256 固定下载字节，不证明发布者身份、包无恶意代码或上游账户未被攻破；
   当前没有签名验证、TUF/Sigstore、SBOM、SLSA provenance 或私有镜像。
4. uv 的 bootstrap 不在自身 lock 中；19 个 PyPI 制品哈希只固定下载字节，首次取得
   `uv-bootstrap.txt`、Python/pip、CA 与 PyPI metadata 仍需外部信任。本轮没有镜像保存 uv
   可执行文件，也没有签名/attestation。
5. `build-constraints.txt` 在仓库构建命令中强制生效；单独拿走 sdist 的消费者若不用该文件，
   只能得到精确 setuptools 版本约束，不能继承本轮哈希门禁。部署应优先使用已审阅 wheel。
6. 没有做空 uv cache 的断网安装；操作系统包、CA 信任、C runtime、`ffprobe`、PostgreSQL
   和 Debian apt 仓库不属于 Python lock。后端 Python 基础镜像虽固定 manifest digest，
   Dockerfile/Compose 仍未实际运行，前端基础镜像仍未固定。
7. 当前副本无 `.git`、无 CI，不能提供提交哈希、lock diff 审批或 Linux matrix 记录。
8. 真实 PostgreSQL、Docker/WSL、Prometheus、真实算法和正式评测仍未获得新证据。

## 8. 上游依据

- [uv lockfile layout](https://docs.astral.sh/uv/concepts/projects/layout/)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv project configuration and build isolation](https://docs.astral.sh/uv/concepts/projects/config/)
- [uv settings: build constraints and required version](https://docs.astral.sh/uv/reference/settings/)
- [uv hash-constrained builds](https://docs.astral.sh/uv/concepts/projects/build/)
- [uv 0.11.32 release artifacts](https://pypi.org/project/uv/0.11.32/)
- [Docker Official Image: Python](https://hub.docker.com/_/python)
- [FastAPI 0.140.7](https://pypi.org/project/fastapi/0.140.7/)
- [Starlette 1.3.1](https://pypi.org/project/starlette/1.3.1/)
- [pytest 9.1.1](https://pypi.org/project/pytest/9.1.1/)
- [httpx2 2.9.1](https://pypi.org/project/httpx2/2.9.1/)

## 9. 关键文件

- 项目与 uv 配置：`backend/pyproject.toml`
- 通用锁：`backend/uv.lock`
- uv 哈希引导：`backend/uv-bootstrap.txt`
- 构建哈希约束：`backend/build-constraints.txt`
- 独立校验器：`backend/scripts/verify_dependency_lock.py`
- 负向策略测试：`backend/tests/test_dependency_lock.py`
- source distribution 清单：`backend/MANIFEST.in`
- 包级说明：`backend/README.md`
- 锁定安装容器候选：`backend/Dockerfile`
- 统一命令：`Makefile`
