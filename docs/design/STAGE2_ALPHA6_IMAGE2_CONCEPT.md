# Stage 2 Alpha 6：Image 2.0 前端概念图

## 用途

本图是“真实闭环联调”页的视觉方向参考，不是页面截图，也不是功能完成证明。实现时只吸收
可落地的信息层级、状态语义和响应式布局，不照搬生成图中的示例数据、施工画面或文案。

概念图：[`stage2-alpha6-image2-concept.png`](./stage2-alpha6-image2-concept.png)

SHA-256：

```text
a5377522d4c6a5e727d26b01bb74de9a79731c81b9285c22447e7c06e2499266
```

## 生成提示词

```text
Use case: ui-mockup
Asset type: shippable desktop web application concept for a Chinese construction-infrastructure intelligent supervision platform
Primary request: design a high-fidelity operations console for “烽眸智鉴”, centered on the real workflow 视频证据输入 → 远程分析 → 人工复核 → 结构化报告 → 哈希存证. This is a practical engineering product UI, not science-fiction concept art.
Scene/backdrop: 16:10 desktop browser canvas with a compact deep-navy left navigation rail and a restrained pale blue-gray working surface.
Subject: one complete “验真闭环” workspace. The upper area shows a real construction-site video frame with a small timeline and evidence metadata. Beside it is a vertical five-step pipeline with clear state indicators. The lower area contains a structured findings table, a highlighted truth-boundary notice, and a cryptographic proof card with a readable shortened SHA-256 hash. Include an explicit failure state area with a single “显式重试” action so recovery is operationally clear.
Style/medium: realistic production-grade enterprise UI screenshot; industrial/utilitarian editorial composition; dense but calm; strong information hierarchy; precise spacing; crisp flat icons; no glassmorphism.
Composition/framing: full-page desktop view, left navigation approximately 18 percent width; asymmetric main grid; video evidence is the dominant focal area; all panels aligned to a disciplined 8-point grid.
Lighting/mood: bright control-room clarity, trustworthy and sober.
Color palette: deep navy #071629, ink #14243B, pale steel #EDF3F8, cyan #00B8D9 for actions, amber #F2A93B for unverified/review states, red only for actual failures, green only for verified integrity.
Typography: contemporary Chinese sans-serif, compact headings, tabular numerals, high contrast and readable.
Text (verbatim, render only these important labels): “烽眸智鉴”, “验真闭环”, “视频证据”, “远程分析”, “人工复核”, “结构化报告”, “哈希存证”, “未评测”, “显式重试”.
Constraints: preserve truthful status semantics; visually distinguish “链路已验证” from “算法未评测”; no accuracy percentages; no fake model performance metrics; no blockchain coin imagery; no holograms; no neon cyberpunk; no purple gradients; no marketing landing page; no logos or trademarks; no watermark; no decorative 3D objects; keep interface feasible to implement in React and responsive CSS.
```

生成方式：Codex 内置 `image_gen`（Image 2.0 路径），单张新图生成，无输入参考图。

## 本轮采用的设计原则

- 原始视频/图片证据应比装饰性图表更突出；
- 任务状态、人工复核、报告和哈希核验应形成一条可扫描的闭环；
- 琥珀色只表示“未评测/待复核”，绿色只表示完整性核验或正式评测证据；
- 失败必须展示原因和显式重试入口，不做自动静默重试；
- “链路可运行”和“算法已经评测”必须在同屏中分开表达；
- 移动端优先保留项目、验真、报告和溯源入口，原型页不抢占核心导航。

## 不采用的生成内容

- 图中施工现场、设备编号、发现项、哈希和时间均为视觉示例，不进入演示数据库；
- 图中的“链路已验证”只可映射到真实 8 项完整性核验结果，不可泛化为算法有效；
- 不采用任何准确率、区块链、司法存证或实时识别能力暗示。
