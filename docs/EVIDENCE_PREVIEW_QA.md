# 原始证据回看：安全威胁模型与 QA 验收清单

状态：实现完成并通过独立复验；本文件同时保留后续生产化测试矩阵  
适用范围：`GET /api/v1/evidence-assets/{evidence_id}/content`、前端原始图片/视频回看组件  
最后更新：2026-07-14

## 1. 验收结论口径

该功能只有在下列 P0 条件全部满足后，才能称为“可回看原始证据”：

1. 客户端只能提交不透明的 `evidence_id`，不能提交文件路径；
2. 服务端不信任数据库中的 `storage_path`、`stored_name`、`content_type`，文件必须落在配置的 `evidence` 根目录内；
3. 目录逃逸、符号链接、非普通文件、数据库路径篡改和校验后换包均不能产生任何证据字节泄露；
4. 返回任何正文前，必须用同一个已安全打开的文件描述符完成类型、大小和 SHA-256 核验；核验与发送不能重新按路径打开文件；
5. API key 必须通过请求头传递，所有成功和失败分支都经过服务端鉴权；
6. `Range` 语义、缓存头和内容类型是确定的，不由上传者或数据库中的任意字符串控制；
7. 前端切换证据、关闭弹窗、请求失败、请求被取消和组件卸载时，都不会保留旧画面或泄漏 object URL。

任一 P0 用例失败，结论必须写为“未通过”，不能用“仅本地演示”弱化。

## 2. 当前代码事实与边界

- `backend/app/models.py:66-80` 中 `EvidenceAsset` 同时保存 `stored_name`、`storage_path`、`content_type`、`size_bytes` 和 `sha256`；这些是持久化输入，不应直接成为文件系统或响应头的可信源。
- `backend/app/services/storage.py:101-173` 在受控目录内用服务端 UUID 文件名和 `xb` 创建上传文件，并执行扩展名、声明 MIME 和 magic bytes 校验；回看端应复用同一份扩展名到规范 MIME 的映射。
- `backend/app/auth.py:59-62` 的现有读权限模型是三个全局可信角色；当前没有项目成员或租户 ACL。因此本轮只能证明“已配置角色可读”，不能声称多租户对象级授权已经完成。
- `backend/app/main.py` 已显式暴露 `Accept-Ranges`、`Content-Length`、`Content-Range` 和 `ETag`；origin 仍使用配置 allowlist。
- `frontend/src/lib/api.ts` 已实现 header-authenticated fetch、AbortSignal、精确 MIME allowlist 和幂等 `revoke()`；`EvidencePreview` 在切换/卸载时 abort 并回收当前 Object URL。

本清单不把数据库本身当成防篡改账本。若攻击者能同时改写一行中的路径、文件名、SHA、大小和 MIME，数据库内字段间的一致性不能证明原始真实性；正式真实性仍依赖已经封存的外部摘要/证据包或独立锚定。本功能必须至少保证数据库路径篡改不能逃出证据根目录，也不能绕过已登记摘要后泄露字节。

## 3. 建议实现合同

### 3.1 安全文件打开

推荐流程如下，顺序也是验收要求：

1. 鉴权；
2. 按 `evidence_id` 查询 DB，未找到返回 404；
3. 校验 `stored_name` 是单一 basename、属于服务端允许扩展名，并从扩展名导出规范 MIME；
4. 计算候选路径 `storage.evidence_dir / stored_name`，要求 DB `storage_path` 规范化后与候选路径一致，且候选路径严格位于 `evidence_dir`；不能只做字符串 `startswith`；
5. 用 `os.open` 的只读、`O_NOFOLLOW`（平台可用时）方式打开最终文件；父目录不接受来自 DB 的任意子路径；
6. 对同一 fd 执行 `fstat`，只接受普通文件；建议拒绝 `st_nlink != 1`；
7. 从同一 fd 读取并核对 magic bytes、完整 `size_bytes` 和完整 SHA-256；
8. 核验失败时关闭 fd，返回统一的完整性错误，不返回文件正文、服务器路径、实际摘要或其他证据信息；
9. 核验成功后仍从该 fd 按完整或单区间读取，生成响应；生成器 `finally` 必须关闭 fd，客户端断开也不能泄漏描述符。

禁止模式：先 `resolve()/hash`，随后把字符串路径传给 `FileResponse`。这种实现会在检查和二次打开之间留下换包窗口。

### 3.2 HTTP 合同

| 场景 | 状态 | 必须响应头/正文 |
|---|---:|---|
| 无 `Range` | 200 | 完整文件；`Content-Length=登记大小`；`Accept-Ranges: bytes` |
| `bytes=0-0` 等单区间 | 206 | 精确字节；`Content-Range: bytes start-end/total`；区间长度的 `Content-Length` |
| 起点有效、终点超过 EOF | 206 | 终点截断到 `total-1` |
| 不可满足、空、多区间或畸形 Range | 416 | `Content-Range: bytes */total`；不得返回证据字节 |
| 记录不存在 | 404 | 通用错误；不得暴露文件系统信息 |
| 路径/类型/大小/SHA/文件类型完整性失败 | 409 | 相同的通用完整性错误；不得区分“外部路径”“摘要不符”等内部细节 |
| 未带/错误 API key | 401 | 在文件查询与打开前失败 |

当前 MVP 建议只支持一个 byte range，并显式拒绝多区间，避免 multipart 复杂度和 Range 合并型 DoS。至少支持 `bytes=N-M`、`bytes=N-` 和 `bytes=-N`；拒绝 `-0`、负数、非十进制、非 `bytes` 单位、逗号、多余符号和超长 header。

每个 200/206 响应必须包含：

```text
Cache-Control: private, no-store, max-age=0
Pragma: no-cache
X-Content-Type-Options: nosniff
Accept-Ranges: bytes
```

`Content-Type` 只能来自服务端扩展名到规范 MIME 的 allowlist；不能回显任意 DB 值。无需返回原始文件名；若使用 `Content-Disposition`，只使用服务端生成的安全名，不能直接拼接 `original_name`。跨源部署时还应显式暴露 `Accept-Ranges, Content-Range`，同时保持 CORS origin allowlist。

### 3.3 前端 object URL 合同

- fetch 使用 `X-API-Key` header 和 `cache: "no-store"`，密钥不得进入 URL、DOM、日志或 object URL；
- 每次打开/切换证据创建新的 `AbortController` 和请求代次；旧请求即使较晚完成也不得覆盖新结果；
- `URL.createObjectURL` 只能在成功且 MIME 精确属于允许集合后调用；不能用宽泛的 `image/*`/`video/*` 前缀接受 SVG、HTML 或未知编解码容器；
- 同一 URL 只能撤销一次；关闭、切换、失败和 unmount 都要撤销当前 URL；
- 若异步响应在 abort/unmount 后才创建 URL，必须立即撤销，不得进入 React state；
- 先让媒体元素停止引用旧 URL（视频应清空 `src`/source 并 `load()`），再撤销；不能在 `<video>`/`<img>` 仍使用时立即撤销；
- 新请求开始后应清空旧画面，错误不能回退到上一个证据、后端裸 URL或静态样例；
- 页面只显示 API 返回的通用错误，不显示后端路径、堆栈、API key；
- Blob 方案会把完整媒体保存在浏览器内存。大文件必须显示大小和加载状态；超过团队确定的前端预览阈值时应阻止自动加载或要求用户确认，不能无提示占用数百 MB。

## 4. 后端强制测试矩阵

### P0-AUTH：鉴权与枚举

- [ ] `AUTH-01` 无 key 请求一个存在 ID，401；磁盘打开函数未被调用。
- [ ] `AUTH-02` 错误 key 请求存在 ID，401；磁盘打开函数未被调用。
- [ ] `AUTH-03` 无 key 请求不存在 ID，仍为 401，不能借状态差异枚举记录。
- [ ] `AUTH-04` operator、reviewer、auditor 的读取策略与设计一致；当前若三者均允许，应分别 200。
- [ ] `AUTH-05` query string 中的 `api_key`/`token` 不能替代 header key。
- [ ] `AUTH-06` 响应、异常、审计日志均不包含 key。

### P0-PATH：路径逃逸、链接与文件类型

- [ ] `PATH-01` DB `storage_path=/etc/passwd`，409，正文中无 `/etc/passwd`，无文件字节。
- [ ] `PATH-02` DB `storage_path=../../outside.mp4`，409。
- [ ] `PATH-03` 前缀混淆目录（如 `evidence-evil/`）不能通过根目录判断。
- [ ] `PATH-04` DB path 指向根目录内另一个证据，但与该记录的 `stored_name` 不一致，409。
- [ ] `PATH-05` `stored_name=../outside.mp4`、绝对路径、含 `/` 或 `\`，409。
- [ ] `PATH-06` 最终文件是指向根外的 symlink，409，目标内容不泄露。
- [ ] `PATH-07` 最终文件是指向根内另一文件的 symlink，也必须 409。
- [ ] `PATH-08` 文件是目录、FIFO、socket 或设备，409；测试不能因 FIFO 阻塞。
- [ ] `PATH-09` 硬链接（若平台支持）被拒绝，或文档明确其剩余风险与文件系统写权限边界。
- [ ] `PATH-10` 缺失文件和权限不足均返回通用完整性错误，不回显绝对路径。
- [ ] `PATH-11` 在校验完成后替换路径目标，客户端仍只读取已校验 fd；不得读到替换后的字节。
- [ ] `PATH-12` fd 在正常结束、异常、取消和客户端中断后均关闭。

### P0-INTEGRITY：篡改后不泄露

- [ ] `INT-01` 修改一个字节但保持大小不变，409，响应不得包含原文件或篡改后文件的任何片段。
- [ ] `INT-02` 截断、追加分别 409。
- [ ] `INT-03` DB `content_type=text/html` 或与扩展名不符，409，不以活动内容返回。
- [ ] `INT-04` magic bytes 与登记格式不符，409。
- [ ] `INT-05` SHA 核验必须在首个响应字节前完成；不能边发边算后才发现错误。
- [ ] `INT-06` 带 `Range: bytes=0-0` 的篡改文件仍然完整核验后 409，不能泄露第一个字节。
- [ ] `INT-07` 错误正文只包含稳定、通用错误码/信息，不含 path、实际 SHA、期望 SHA、异常类型或相邻记录信息。

### P0-RANGE：单区间语义

- [ ] `RANGE-01` 无 Range：200，body 与原文件逐字节相同，长度/类型正确。
- [ ] `RANGE-02` `bytes=0-0`：206，恰好首字节，`Content-Range=bytes 0-0/total`。
- [ ] `RANGE-03` `bytes=1-3`：206，恰好三个字节。
- [ ] `RANGE-04` `bytes=N-`：206，到 EOF。
- [ ] `RANGE-05` `bytes=-N`：206，最后 N 字节；N 大于总长时返回完整文件的 206。
- [ ] `RANGE-06` 终点大于 EOF 时截断，响应头与正文长度一致。
- [ ] `RANGE-07` 起点等于或大于文件大小：416，`Content-Range=bytes */total`，无证据正文。
- [ ] `RANGE-08` `bytes=-0`、`bytes=`、反向区间、字母、符号、其他单位：416。
- [ ] `RANGE-09` `bytes=0-1,4-5` 多区间：416，不生成 multipart。
- [ ] `RANGE-10` 超长 Range header 被有界拒绝，响应时间不随逗号数量呈二次增长。
- [ ] `RANGE-11` Starlette 实际版本不低于已修复 Range DoS 的版本；当前锁定 `0.52.1`，测试环境应确认安装版本而非只看声明文件。
- [ ] `RANGE-12` 并发和客户端中断不会导致 fd 泄漏或后台持续读取整个文件。

### P1-HEADERS/OpenAPI：缓存、类型和合同

- [ ] `HDR-01` 200、206、409、416 均有预期的 no-store/nosniff 策略；至少所有证据内容响应不能被共享缓存。
- [ ] `HDR-02` 200/206 的 `Content-Type` 来自 allowlist，大小与 DB/正文一致。
- [ ] `HDR-03` 206/416 的 `Content-Range` 精确；200 不伪造 `Content-Range`。
- [ ] `HDR-04` 不回显服务器路径；不直接使用可含控制字符的原始文件名。
- [ ] `HDR-05` OpenAPI 明确 binary 200/206，以及 401/404/409/416；生成后的 `docs/openapi-v1.json` 同步更新。
- [ ] `HDR-06` 跨源模式下预检允许 `X-API-Key`/`Range`，响应暴露 `Content-Range`/`Accept-Ranges`，origin 仍是显式 allowlist。

## 5. 前端强制测试矩阵

项目当前没有 Vitest/Testing Library。可先用浏览器自动化 + `URL.createObjectURL`/`URL.revokeObjectURL` instrumentation 验收；若该组件继续演进，建议再补最小前端单测工具链。

- [ ] `FE-01` 打开图片：只发一次带 header key 的请求，加载真实 Blob，显示类型/大小/原始摘要。
- [ ] `FE-02` 打开视频：可播放、暂停、拖动本地 Blob；没有把 API key 放入 URL。
- [ ] `FE-03` 关闭弹窗：旧媒体消失，当前 object URL 恰好 revoke 一次。
- [ ] `FE-04` A 请求未完成时切到 B：A 被 abort；即使 A 最后才 resolve，也不能覆盖 B，A 创建的 URL 必须被 revoke。
- [ ] `FE-05` 连续快速切换 20 次：最终只显示最后一项；`createObjectURL` 数量等于最终已撤销数量加当前活跃数量（最多 1）。
- [ ] `FE-06` 组件 unmount/路由切换：pending fetch abort，活跃 URL revoke，无 React state-after-unmount 警告。
- [ ] `FE-07` 401/404/409/416：旧画面被清空，只显示稳定错误；不创建 URL，不回退静态素材。
- [ ] `FE-08` 后端返回 `text/html`、`image/svg+xml` 或其他非允许 MIME：不进入 `<img>/<video>`，object URL 若已创建立即撤销。
- [ ] `FE-09` 后端篡改检测 409：Network response 无媒体字节，页面无任何旧/新原始画面。
- [ ] `FE-10` fetch 显式 `cache: "no-store"`；页面刷新/切换后不从 Service Worker 或应用缓存回放旧证据。
- [ ] `FE-11` 大文件有明确大小、加载和取消反馈；取消后不会继续创建 Blob URL。
- [ ] `FE-12` 弹窗键盘焦点、Escape 关闭、图片 alt/视频 controls 可用；失败状态能被屏幕阅读器感知。

## 6. 独立 E2E 验收脚本顺序

1. 创建项目、基线并上传一个极小 MP4/PNG，记录 evidence ID、大小、SHA；
2. 用三类合法角色分别执行完整 GET，逐字节比对；
3. 执行全部单 Range 与畸形 Range 表；
4. 在 DB 中依次篡改 `storage_path`、`stored_name`、`content_type`，确认每次都是 fail closed，并恢复；
5. 依次测试 symlink、同大小单字节篡改、截断、追加；
6. 浏览器打开真实项目详情/任务详情，查看图片或视频，切换、关闭、快速重复 20 次并统计 object URL；
7. 回看过程中篡改后端文件，刷新或重新打开，确认 UI 不再显示内容；
8. 最后运行后端严格回归、OpenAPI 合同、前端 typecheck/build/audit，并记录准确的新测试总数与覆盖率，不沿用旧数字。

建议最终保存以下证据：pytest 输出、运行时响应头、恶意路径/链接用例结果、浏览器 Network 截图、object URL 计数日志，以及正常图片/视频回看的截图。日志和截图必须脱敏，不能包含 API key、绝对存储路径或真实现场隐私数据。

## 7. 合并阻断与剩余风险

### 7.1 本轮独立复验结果

- 原始证据接口与 OpenAPI 专项：56 passed，`-W error`；
- 本功能落地时历史全量基线：365 passed，覆盖率 90.34%，90% 门禁通过；当前 Alpha9 门禁请以
  `STATUS_2026-07-14_STAGE2.md` 为准；
- 前端 `npm run verify` 通过，0 vulnerabilities；
- 真实 HTTP：401/200/206、正文大小、SHA-256、`Content-Range` 和安全头通过；
- 浏览器：H.264/yuv420p Blob 视频 `readyState=4`、320x240、1 秒，无媒体错误；关闭组件后 Object URL 已不可读；
- 大文件门禁：模拟 `64 MiB + 1` 的登记大小，确认前内容请求为 0，确认后才产生 1 次请求；会话 key 绑定 evidence ID、登记大小、autoLoad 和阈值；
- 已验证的重点攻击包括 evidence 目录 symlink、最终成员 symlink/硬链接、`lstat -> open` 换链、摘要后消失/换包、Range 篡改请求、短读和 fd 关闭。
- `remote_http` 额外固化了两个并发 worker 的 ContextVar/fd 隔离，以及 client factory/timeout/上游失败后的描述符所有权与关闭行为。

未执行矩阵中的 20 次快速切换压力和真实大文件内存占用测试，因此这些仍是生产化 P1；64 MiB 的请求前阻断行为已经通过浏览器路由模拟验证。

必须阻断合并：`PATH-01/05/06/11`、`INT-01/05/06`、`AUTH-01/03`、`RANGE-07/09/10`、`FE-04/06/07/09` 任一失败。

可以作为明确的后续项但不能误报已解决：

- 当前角色是全局 API key，没有项目/组织级 ACL；
- 完整 SHA-256 先验核验会增加大文件读取成本，需要限流、并发上限或安全的 fd 身份缓存；不能为了性能跳过篡改核验；
- Blob 回看需要把完整文件放入浏览器内存，超大视频最终应改为不会把密钥放 URL 的受控流式授权方案；
- 本地 no-store 与哈希核验不等于可信时间、公链或第三方存证；
- 若数据库所有完整性字段都被同时重写，应用内自校验不能证明历史原始性，应以外部封存摘要/证据包为更高信任锚。

参考基线：FastAPI/Starlette 文件服务安全要求、OWASP File Upload Cheat Sheet、RFC 9110 Range Requests，以及 React/浏览器 Blob URL 生命周期要求。
