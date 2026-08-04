import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api, DashboardSummary, Project, Proof, VerificationJob } from "../lib/api";
import { AnalyticsIcon, BlockchainIcon, CameraIcon, CheckIcon, ChevronRightIcon, DatabaseIcon, EyeIcon, ProjectIcon, ShieldIcon } from "../components/Icons";
import { cn } from "../utils/cn";
import { Notice } from "../components/ui/Notice";
import { TruthBadge } from "../components/ui/TruthStatus";
import { analysisTruthFromJob } from "../lib/truth";

const jobLabel: Record<string, string> = { queued: "排队", running: "处理中", needs_review: "待人工复核", sealing: "封存恢复中", approved: "已批准封装", rejected: "已驳回", failed: "执行失败" };

const flow = [
  ["01", "证据采集", "服务端摘要"],
  ["02", "结构化处理", "适配器合同"],
  ["03", "人工复核", "强制审批门"],
  ["04", "报告生成", "JSON / HTML"],
  ["05", "完整性核验", "Merkle / 哈希链"],
];

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [jobs, setJobs] = useState<VerificationJob[]>([]);
  const [proofs, setProofs] = useState<Proof[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.dashboardSummary(), api.listProjects(), api.listVerifications(), api.listProofs()])
      .then(([summaryValue, projectValues, jobValues, proofValues]) => {
        setSummary(summaryValue); setProjects(projectValues); setJobs(jobValues); setProofs(proofValues);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "实时总览加载失败"))
      .finally(() => setLoading(false));
  }, []);

  const pendingReview = summary?.jobs_by_status.needs_review || 0;
  const latestProof = proofs[0] || null;
  const latestJobs = useMemo(() => jobs.slice(0, 5), [jobs]);

  return <div className="space-y-5 page-enter">
    {error ? <Notice type="info" message={error} /> : null}

    <section className="relative overflow-hidden rounded-[30px] border border-cyan-300/15 bg-[#061526] px-6 py-6 text-white shadow-[0_30px_100px_-50px_rgba(8,145,178,.95)]">
      <div className="absolute inset-0 opacity-50 [background-image:linear-gradient(rgba(56,189,248,.06)_1px,transparent_1px),linear-gradient(90deg,rgba(56,189,248,.06)_1px,transparent_1px)] [background-size:34px_34px]" />
      <div className="absolute -right-16 -top-24 h-72 w-72 rounded-full bg-cyan-400/15 blur-3xl" />
      <div className="relative flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-3xl"><div className="flex flex-wrap gap-2"><span className={cn("inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs", error ? "border-rose-300/20 bg-rose-300/10 text-rose-100" : summary ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100" : "border-amber-300/20 bg-amber-300/10 text-amber-100")}><span className={cn("h-2 w-2 rounded-full", error ? "bg-rose-400" : summary ? "bg-emerald-400" : "bg-amber-400")} /> {error ? "后端数据不可用" : summary ? "后端数据已同步" : "正在连接后端"}</span><span className="rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1 text-xs text-amber-100">本地篡改可检测 · 非公链</span></div><h2 className="mt-5 text-3xl font-semibold tracking-tight">证据运营中心</h2><p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">把项目、原始媒体、算法来源、人工判断与交付档案放在同一条可追溯链路中。页面数字来自当前后端数据库，不注入演示项目总数。</p><div className="mt-5 flex flex-wrap gap-3"><button onClick={() => navigate("/backend-workflow")} className="inline-flex items-center gap-2 rounded-2xl bg-cyan-400 px-4 py-2.5 text-sm font-semibold text-slate-950 hover:bg-cyan-300"><EyeIcon className="h-4 w-4" /> 发起验真任务</button><button onClick={() => navigate("/traceability")} className="inline-flex items-center gap-2 rounded-2xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-medium text-white hover:bg-white/10"><BlockchainIcon className="h-4 w-4" /> 核验证据档案</button></div></div>
        <div className="grid grid-cols-2 gap-2 rounded-[26px] border border-white/10 bg-white/5 p-3 backdrop-blur md:grid-cols-4 xl:min-w-[560px]">{[
          ["项目总账", summary?.projects ?? "—", "PROJECTS"],
          ["原始证据", summary?.evidence_assets ?? "—", "EVIDENCE"],
          ["待复核", summary ? pendingReview : "—", "REVIEW"],
          ["完整性档案", summary?.proof_archives ?? "—", "ARCHIVES"],
        ].map(([label, value, code]) => <div key={String(label)} className="rounded-2xl bg-slate-950/30 px-4 py-3"><p className="text-[9px] tracking-[.2em] text-cyan-300">{code}</p><p className="mt-2 text-2xl font-semibold">{value}</p><p className="mt-1 text-xs text-slate-400">{label}</p></div>)}</div>
      </div>
    </section>

    <section className="grid gap-3 md:grid-cols-5">{flow.map(([index, title, detail], position) => <div key={index} className={cn("relative rounded-[22px] border bg-white p-4 shadow-sm", position === 2 && pendingReview > 0 ? "border-amber-300" : "border-slate-200")}><div className="flex items-center justify-between"><span className="text-[10px] font-semibold tracking-[.18em] text-sky-600">{index}</span><span className={cn("h-2.5 w-2.5 rounded-full", position < 2 ? "bg-cyan-500" : position === 2 && pendingReview > 0 ? "bg-amber-500 animate-pulse" : "bg-slate-300")} /></div><p className="mt-3 text-sm font-semibold text-slate-900">{title}</p><p className="mt-1 text-[11px] text-slate-500">{detail}</p></div>)}</section>

    {loading ? <div className="rounded-[28px] border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">正在聚合后端运行数据…</div> : null}

    <div className="grid gap-5 xl:grid-cols-[1.08fr_.92fr]">
      <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between"><div><p className="text-xs font-semibold tracking-[.18em] text-sky-600">CURRENT OPERATIONS</p><h3 className="mt-2 font-semibold text-slate-900">最近验真任务</h3></div><button onClick={() => navigate("/backend-workflow")} className="text-sm font-medium text-sky-700">进入闭环</button></div>
        <div className="mt-4 space-y-3">{latestJobs.map((job) => {
          const truth = analysisTruthFromJob(job);
          return <div key={job.id} className="grid gap-3 rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:grid-cols-[1fr_auto] md:items-center"><div><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-slate-900">{jobLabel[job.status] || job.status}</span><TruthBadge truth={truth} /></div><p className="mt-2 truncate font-mono text-[10px] text-slate-500">{job.id}</p></div><div className="min-w-32"><div className="flex justify-between text-[10px] text-slate-500"><span>处理进度</span><span>{job.progress}%</span></div><div className="mt-2 h-1.5 rounded-full bg-slate-200"><div className={cn("h-full rounded-full", job.status === "failed" || job.status === "rejected" ? "bg-rose-500" : job.status === "needs_review" ? "bg-amber-500" : "bg-cyan-500")} style={{ width: `${job.progress}%` }} /></div></div></div>;
        })}{latestJobs.length === 0 ? <div className="rounded-[22px] border border-dashed border-slate-300 p-9 text-center"><CameraIcon className="mx-auto h-7 w-7 text-slate-300" /><p className="mt-3 text-sm text-slate-500">尚无验真任务，先在真实闭环中上传一段证据。</p></div> : null}</div>
      </section>

      <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between"><div><p className="text-xs font-semibold tracking-[.18em] text-emerald-600">INTEGRITY STATUS</p><h3 className="mt-2 font-semibold text-slate-900">最新交付指纹</h3></div><ShieldIcon className="h-5 w-5 text-emerald-600" /></div>
        {latestProof ? <div className="mt-4 space-y-3"><div className="rounded-[22px] border border-emerald-200 bg-emerald-50 p-4"><div className="flex items-center gap-2 text-sm font-semibold text-emerald-700"><CheckIcon className="h-4 w-4" /> 已生成可独立核验档案</div><p className="mt-2 text-xs text-emerald-700">{latestProof.archive_id} · Ledger #{latestProof.ledger_index}</p></div><HashRow label="Archive SHA-256" value={latestProof.archive_sha256} /><HashRow label="Merkle Root" value={latestProof.merkle_root} /><HashRow label="Record Hash" value={latestProof.record_hash} /><button onClick={() => navigate("/traceability")} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-700">执行逐项重算 <ChevronRightIcon className="h-4 w-4" /></button></div> : <div className="mt-4 rounded-[22px] border border-dashed border-slate-300 p-9 text-center"><BlockchainIcon className="mx-auto h-7 w-7 text-slate-300" /><p className="mt-3 text-sm text-slate-500">批准任务后会生成档案指纹。</p></div>}
      </section>
    </div>

    <div className="grid gap-5 xl:grid-cols-[1fr_.75fr]">
      <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between"><div><h3 className="font-semibold text-slate-900">项目账本</h3><p className="mt-1 text-xs text-slate-500">最近登记的真实项目</p></div><ProjectIcon className="h-5 w-5 text-sky-600" /></div><div className="mt-4 overflow-x-auto"><table className="min-w-[620px] w-full text-left text-sm"><thead><tr className="border-b border-slate-200 text-[11px] text-slate-500"><th className="pb-3 font-medium">项目</th><th className="pb-3 font-medium">地点</th><th className="pb-3 font-medium">负责人</th><th className="pb-3 font-medium">状态</th><th className="pb-3 text-right font-medium">入口</th></tr></thead><tbody className="divide-y divide-slate-100">{projects.slice(0, 5).map((project) => <tr key={project.id}><td className="py-3"><p className="font-semibold text-slate-900">{project.name}</p><p className="mt-1 text-[10px] text-sky-600">{project.code}</p></td><td className="py-3 text-xs text-slate-500">{project.location}</td><td className="py-3 text-xs text-slate-500">{project.manager || "—"}</td><td className="py-3"><span className="rounded-full bg-emerald-100 px-2 py-1 text-[10px] text-emerald-700">{project.status}</span></td><td className="py-3 text-right"><button onClick={() => navigate(`/projects/${project.id}`)} className="text-xs font-medium text-sky-700">详情</button></td></tr>)}</tbody></table>{projects.length === 0 ? <p className="py-8 text-center text-sm text-slate-500">暂无项目数据</p> : null}</div></section>
      <section className="rounded-[28px] border border-amber-200 bg-amber-50 p-5"><div className="flex items-center gap-3"><AnalyticsIcon className="h-5 w-5 text-amber-700" /><div><h3 className="font-semibold text-amber-900">运行边界</h3><p className="mt-1 text-xs text-amber-700">实时数据与原型模块分离</p></div></div><div className="mt-4 grid gap-3">{[["待复核任务", pendingReview], ["运营待整改案件", summary?.finding_cases.confirmed_open_operational ?? 0], ["合成演示案件", summary?.finding_cases.demo_cases ?? 0], ["正式证据档案", summary?.formal_evidence_archives ?? 0]].map(([label, value]) => <div key={String(label)} className="flex items-center justify-between rounded-2xl bg-white/70 px-4 py-3"><span className="text-sm text-amber-900">{label}</span><span className="text-lg font-semibold text-amber-900">{value}</span></div>)}</div><button onClick={() => navigate("/alarms")} className="mt-4 w-full rounded-2xl border border-amber-300 bg-white/70 px-4 py-2.5 text-sm font-semibold text-amber-900">查看真实告警与整改</button><p className="mt-4 text-xs leading-5 text-amber-800">stub、synthetic demo fixture 与 remote_http 单样本参考服务均不是冻结评测。告警与整改页已读取真实数据库并隔离 demo 统计；真实 PPE 模型与正式 85% 指标尚未完成，GIS、设备、数据分析仍属于原型展示。</p></section>
    </div>
  </div>;
};

const HashRow: React.FC<{ label: string; value: string }> = ({ label, value }) => <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4"><p className="text-[10px] tracking-[.12em] text-slate-400">{label}</p><p className="mt-2 truncate font-mono text-[11px] text-slate-700">{value}</p></div>;
