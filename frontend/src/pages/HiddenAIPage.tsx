import React, { useMemo, useState } from "react";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";
import { cn } from "../utils/cn";
import {
  CameraIcon,
  ShieldIcon,
  AnalyticsIcon,
  CheckIcon,
  XIcon,
  DownloadIcon,
  PrintIcon,
  DatabaseIcon,
  BlockchainIcon,
  EyeIcon,
} from "../components/Icons";

type SceneKey = "duct" | "trench" | "cable";

type AnalysisResult = {
  score: number;
  status: "pass" | "warn" | "fail";
  summary: string;
  confidence: number;
  depth: string;
  spacing: string;
  specification: string;
  quantity: string;
  riskPoints: string[];
  suggestions: string[];
  hash: string;
};

const sceneConfig: Record<SceneKey, { title: string; location: string; cover: string; result: AnalysisResult }> = {
  duct: {
    title: "通信管道排布验真",
    location: "杭州余杭段 · 管道井 A-12",
    cover: "https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=1200&q=80",
    result: {
      score: 92,
      status: "pass",
      summary: "管道数量、间距与设计图基本一致，满足交付前验真要求。",
      confidence: 95,
      depth: "1.18m",
      spacing: "0.26m",
      specification: "PE110 × 4 孔",
      quantity: "4 孔 / 1 组",
      riskPoints: ["局部回填前需补充近景测量照", "西南角阴影区域建议二次采集"],
      suggestions: ["上传补光照片", "补录尺量标识", "归档后发起最终交付核验"],
      hash: "0x9f2a-11ce-6b08-a771-3f6d-88b1-cc21",
    },
  },
  trench: {
    title: "沟槽开挖深度验真",
    location: "上海浦东段 · 隐蔽沟槽 B-07",
    cover: "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?auto=format&fit=crop&w=1200&q=80",
    result: {
      score: 81,
      status: "warn",
      summary: "沟槽深度达到下限要求，但局部边坡存在不规整区域，建议整改复检。",
      confidence: 90,
      depth: "0.92m",
      spacing: "0.31m",
      specification: "单沟槽开挖",
      quantity: "1 段 / 18m",
      riskPoints: ["东侧边坡切面不平整", "标尺入镜角度偏移 6°", "局部泥水遮挡影响精度"],
      suggestions: ["清理沟槽积水后复拍", "补采垂直视角照片", "发起整改闭环"],
      hash: "0x3bc2-90af-c912-66dd-ef90-4421-72a9-a1f7",
    },
  },
  cable: {
    title: "线缆预埋与覆土验真",
    location: "南京江宁段 · 预埋点 C-03",
    cover: "https://images.unsplash.com/photo-1581092335871-3c3be3cd5f4d?auto=format&fit=crop&w=1200&q=80",
    result: {
      score: 68,
      status: "fail",
      summary: "AI 检测到覆土厚度不足且警示带缺失，不满足当前验收标准。",
      confidence: 88,
      depth: "0.54m",
      spacing: "0.18m",
      specification: "48 芯光缆",
      quantity: "2 条 / 1 段",
      riskPoints: ["覆土厚度低于设计值 0.12m", "警示带缺失", "照片缺少 GPS 水印"],
      suggestions: ["立即停用该交付批次", "完成覆土补强后重新采集", "重新生成可信档案"],
      hash: "0x812e-1f0f-7d30-cf14-55d1-72f0-998c-e212",
    },
  },
};

const steps = ["影像接入", "语义分割", "参数量测", "规则比对", "可信封装"];

export const HiddenAIPage: React.FC = () => {
  const [scene, setScene] = useState<SceneKey>("duct");
  const [running, setRunning] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [finished, setFinished] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);

  const current = useMemo(() => sceneConfig[scene], [scene]);

  const runAnalysis = () => {
    setRunning(true);
    setFinished(false);
    setNotice("");
    setStepIndex(0);

    let currentStep = 0;
    const timer = window.setInterval(() => {
      currentStep += 1;
      setStepIndex(currentStep);
      if (currentStep >= steps.length - 1) {
        window.clearInterval(timer);
        window.setTimeout(() => {
          setRunning(false);
          setFinished(true);
          setNotice(
            "原型动作：仅播放前端步骤动画。分数、埋深、置信度与短指纹均为写死示意，不是后端推理、实验结果或正式指标。"
          );
        }, 500);
      }
    }, 520);
  };

  const result = current.result;

  return (
    <div className="space-y-5 page-enter">
      <Notice
        type="warning"
        message="原型页 · 本页全部分数/置信度/量测/短指纹为本地写死示意。不会调用后端、不产生证据包，不能当作 85%/90% 指标或模型能力。真实链路请用「真实闭环联调」。"
      />
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-br from-sky-600 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <ShieldIcon className="h-4 w-4" />
              静态交互原型 · 非真实推理 · 非评测
            </div>
            <h2 className="text-2xl font-semibold tracking-tight">隐蔽工程 AI 分析中心（布局演示）</h2>
            <p className="mt-2 text-sm leading-6 text-sky-100">
              仅演示分割/量测/存证界面结构。真实上传、任务、复核、报告与 SHA-256/Merkle 证据包只在「真实闭环联调」中产生。
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3 rounded-3xl border border-white/15 bg-slate-950/20 p-3 backdrop-blur-sm">
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-sky-200">赛题目标</p>
              <p className="mt-1 text-sm font-semibold">未验证</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-sky-200">实测耗时</p>
              <p className="mt-1 text-sm font-semibold">待验证</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-sky-200">哈希语义</p>
              <p className="mt-1 text-sm font-semibold">完整性</p>
            </div>
          </div>
        </div>
      </div>

      {notice ? <Notice type="info" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">1. 场景选择与样例采集</h3>
                <p className="mt-1 text-sm text-slate-500">可直接选择演示样例，模拟现场采集后的 AI 验真流程。</p>
              </div>
              <button
                onClick={() => setPreviewOpen(true)}
                className="inline-flex items-center gap-2 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-700 transition-all hover:bg-sky-100"
              >
                <EyeIcon className="h-4 w-4" /> 查看原图
              </button>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              {(Object.keys(sceneConfig) as SceneKey[]).map((key) => {
                const item = sceneConfig[key];
                const active = scene === key;
                return (
                  <button
                    key={key}
                    onClick={() => {
                      setScene(key);
                      setFinished(false);
                      setNotice("");
                    }}
                    className={cn(
                      "overflow-hidden rounded-[24px] border text-left transition-all duration-300",
                      active
                        ? "border-sky-300 bg-sky-50 shadow-lg shadow-sky-100"
                        : "border-slate-200 bg-white hover:border-sky-200 hover:shadow-md"
                    )}
                  >
                    <div className="h-28 bg-cover bg-center" style={{ backgroundImage: `url(${item.cover})` }} />
                    <div className="space-y-1 p-4">
                      <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                      <p className="text-xs text-slate-500">{item.location}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">2. AI 分析流程</h3>
                <p className="mt-1 text-sm text-slate-500">点击后将依次完成分割、量测、规则比对与可信归档。</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={runAnalysis}
                  disabled={running}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm font-medium text-white transition-all",
                    running ? "cursor-not-allowed bg-sky-300" : "bg-sky-600 hover:bg-sky-700"
                  )}
                >
                  <AnalyticsIcon className="h-4 w-4" />
                  {running ? "分析中..." : finished ? "重新分析" : "开始智能分析"}
                </button>
                <button
                  onClick={() => setReportOpen(true)}
                  className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
                >
                  <DatabaseIcon className="h-4 w-4" /> 数据预览
                </button>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-5">
              {steps.map((step, index) => {
                const isDone = finished || stepIndex > index;
                const isActive = running && stepIndex === index;
                return (
                  <div
                    key={step}
                    className={cn(
                      "rounded-[22px] border p-4 transition-all",
                      isDone
                        ? "border-emerald-200 bg-emerald-50"
                        : isActive
                          ? "border-sky-300 bg-sky-50 shadow-md shadow-sky-100"
                          : "border-slate-200 bg-slate-50"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-slate-500">Step {index + 1}</span>
                      {isDone ? (
                        <CheckIcon className="h-4 w-4 text-emerald-600" />
                      ) : (
                        <span className={cn("h-2.5 w-2.5 rounded-full", isActive ? "animate-pulse bg-sky-500" : "bg-slate-300")} />
                      )}
                    </div>
                    <p className="mt-3 text-sm font-medium text-slate-900">{step}</p>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">3. 结构化结果输出</h3>
                <p className="mt-1 text-sm text-slate-500">自动生成埋深、间距、规格、数量与交付结论。</p>
              </div>
              <span
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-semibold",
                  result.status === "pass"
                    ? "bg-emerald-100 text-emerald-700"
                    : result.status === "warn"
                      ? "bg-amber-100 text-amber-700"
                      : "bg-rose-100 text-rose-700"
                )}
              >
                示意
                {result.status === "pass" ? "通过" : result.status === "warn" ? "需复检" : "不通过"}
              </span>
            </div>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {[
                ["识别得分（示意）", `${result.score} / 100 · 非实测`],
                ["模型置信度（示意）", `${result.confidence}% · 非实测`],
                ["埋深测算", result.depth],
                ["间距测算", result.spacing],
                ["设施规格", result.specification],
                ["目标数量", result.quantity],
                ["采集场景", current.title],
                ["定位点位", current.location],
              ].map(([label, value]) => (
                <div key={label} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-2 text-base font-semibold text-slate-900">{value}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-[22px] border border-sky-100 bg-sky-50 p-4">
              <p className="text-sm font-semibold text-slate-900">AI 结论摘要</p>
              <p className="mt-2 text-sm leading-6 text-slate-600">{result.summary}</p>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
            <div className="relative h-72 bg-cover bg-center" style={{ backgroundImage: `url(${current.cover})` }}>
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950/70 via-slate-900/20 to-transparent" />
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_22%_18%,rgba(14,165,233,0.28),transparent_22%),radial-gradient(circle_at_60%_44%,rgba(34,211,238,0.18),transparent_18%),radial-gradient(circle_at_70%_72%,rgba(59,130,246,0.24),transparent_20%)]" />
              <div className="absolute left-5 top-5 rounded-full border border-white/25 bg-white/10 px-3 py-1 text-xs font-medium text-white backdrop-blur-sm">
                AI 分割热区叠加演示
              </div>
              <div className="absolute bottom-5 left-5 right-5 flex items-end justify-between gap-3">
                <div>
                  <p className="text-lg font-semibold text-white">{current.title}</p>
                  <p className="mt-1 text-sm text-sky-100">{current.location}</p>
                </div>
                <button
                  onClick={() => setPreviewOpen(true)}
                  className="rounded-2xl border border-white/25 bg-white/10 px-4 py-2 text-sm font-medium text-white backdrop-blur-sm transition-all hover:bg-white/15"
                >
                  查看对比
                </button>
              </div>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">风险点与整改建议</h3>
              <ShieldIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="space-y-3">
              {result.riskPoints.map((item) => (
                <div key={item} className="flex items-start gap-3 rounded-[22px] border border-rose-100 bg-rose-50 p-4">
                  <XIcon className="mt-0.5 h-4 w-4 text-rose-500" />
                  <p className="text-sm leading-6 text-slate-700">{item}</p>
                </div>
              ))}
              {result.suggestions.map((item) => (
                <div key={item} className="flex items-start gap-3 rounded-[22px] border border-sky-100 bg-sky-50 p-4">
                  <CheckIcon className="mt-0.5 h-4 w-4 text-sky-600" />
                  <p className="text-sm leading-6 text-slate-700">{item}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">可信指纹与交付动作</h3>
              <BlockchainIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">演示短指纹（非标准 SHA-256）</p>
              <p className="mt-2 break-all font-mono text-sm text-slate-800">{result.hash}</p>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <button
                onClick={() =>
                  setNotice("原型动作：未生成真实报告文件；请用真实闭环联调产生 JSON/HTML 报告。")
                }
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                <DownloadIcon className="h-4 w-4" /> 导出 AI 报告
              </button>
              <button
                onClick={() => window.print()}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                <PrintIcon className="h-4 w-4" /> 打印验真单
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未写入证据包或哈希链；请进入「真实闭环联调」。")
                }
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm font-medium text-sky-700 transition-all hover:bg-sky-100"
              >
                <BlockchainIcon className="h-4 w-4" /> 演示存证动作
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未推送整改建议，也未创建告警案件。")
                }
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                <CameraIcon className="h-4 w-4" /> 发起整改复检
              </button>
            </div>
          </div>
        </div>
      </div>

      <Modal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        title="样例影像对比预览"
        description="左侧为原始施工影像，右侧为 AI 分割热区与参数标注叠加演示。"
        size="xl"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setPreviewOpen(false)}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700"
            >
              关闭
            </button>
            <button
              onClick={() => {
                setPreviewOpen(false);
                setNotice("原型动作：未加入后端任务队列；请在「真实闭环联调」上传媒体。");
              }}
              className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
            >
              使用该样例分析
            </button>
          </div>
        }
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-2 text-sm font-medium text-slate-700">原始影像</p>
            <div className="h-80 rounded-[24px] bg-cover bg-center" style={{ backgroundImage: `url(${current.cover})` }} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-700">AI 叠加结果</p>
            <div className="relative h-80 rounded-[24px] bg-cover bg-center" style={{ backgroundImage: `url(${current.cover})` }}>
              <div className="absolute inset-0 rounded-[24px] bg-[radial-gradient(circle_at_30%_28%,rgba(14,165,233,0.35),transparent_18%),radial-gradient(circle_at_52%_56%,rgba(16,185,129,0.28),transparent_16%),radial-gradient(circle_at_72%_68%,rgba(59,130,246,0.38),transparent_18%)]" />
              <div className="absolute left-4 top-4 rounded-full bg-sky-600 px-3 py-1 text-xs font-medium text-white">管线轮廓</div>
              <div className="absolute right-4 top-16 rounded-full bg-emerald-600 px-3 py-1 text-xs font-medium text-white">埋深量测</div>
              <div className="absolute bottom-4 left-4 rounded-full bg-blue-700 px-3 py-1 text-xs font-medium text-white">间距识别</div>
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        title="结构化数据预览"
        description="用于导出、报表生成、可信交付的标准化字段预览。"
        size="lg"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setReportOpen(false)}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700"
            >
              关闭
            </button>
            <button
              onClick={() => {
                setReportOpen(false);
                setNotice("原型动作：未推送结构化数据到看板或报表中心。");
              }}
              className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
            >
              推送到业务系统
            </button>
          </div>
        }
      >
        <div className="overflow-hidden rounded-[24px] border border-slate-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-3 font-medium">字段</th>
                <th className="px-4 py-3 font-medium">结果值</th>
                <th className="px-4 py-3 font-medium">说明</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {[
                ["scene_name", current.title, "演示场景名称（非后端）"],
                ["location", current.location, "写死示意点位"],
                ["depth", result.depth, "示意埋深 · 非自动量测"],
                ["spacing", result.spacing, "示意间距 · 非自动量测"],
                ["specification", result.specification, "示意规格 · 非识别结果"],
                ["quantity", result.quantity, "示意数量 · 非识别结果"],
                ["confidence", `${result.confidence}%`, "示意置信度 · 非模型输出"],
                ["conclusion", result.summary, "示意结论 · 非算法结论"],
                ["archive_hash", result.hash, "演示短指纹 · 非 SHA-256"],
              ].map(([field, value, desc]) => (
                <tr key={String(field)}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">{field}</td>
                  <td className="px-4 py-3 text-sm font-medium text-slate-900">{value}</td>
                  <td className="px-4 py-3 text-sm text-slate-600">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Modal>
    </div>
  );
};
