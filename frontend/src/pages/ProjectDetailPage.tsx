import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, Baseline, Project, ProjectProgress, Proof, Report, VerificationDetail, VerificationJob } from "../lib/api";
import { AnalyticsIcon, CameraIcon, DownloadIcon, EyeIcon, ShieldIcon, DatabaseIcon, BlockchainIcon, InfoIcon } from "../components/Icons";
import { cn } from "../utils/cn";
import { Notice } from "../components/ui/Notice";
import { EvidencePreview } from "../components/ui/EvidencePreview";
import { AnalysisTruthPanel, TruthBadge } from "../components/ui/TruthStatus";
import { analysisTruthFromJob, reportTruthFromReport } from "../lib/truth";

const jobLabel: Record<string, string> = { queued: "排队中", running: "处理中", needs_review: "待复核", sealing: "封存恢复中", approved: "已批准", rejected: "已驳回", failed: "失败" };

export const ProjectDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { id = "" } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [progress, setProgress] = useState<ProjectProgress | null>(null);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [jobs, setJobs] = useState<VerificationJob[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [proofs, setProofs] = useState<Proof[]>([]);
  const [tab, setTab] = useState<"baselines" | "jobs" | "reports" | "proofs">("jobs");
  const [previewDetail, setPreviewDetail] = useState<VerificationDetail | null>(null);
  const [previewLoadingId, setPreviewLoadingId] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [retryingId, setRetryingId] = useState("");
  const previewRequestRef = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.getProject(id), api.projectProgress(id), api.listBaselines(id), api.listVerifications(id), api.listReports(id), api.listProofs()])
      .then(([projectValue, progressValue, baselineValues, jobValues, reportValues, proofValues]) => {
        if (!active) return;
        setProject(projectValue);
        setProgress(progressValue);
        setBaselines(baselineValues);
        setJobs(jobValues);
        setReports(reportValues);
        const reportIds = new Set(reportValues.map((item) => item.id));
        setProofs(proofValues.filter((item) => reportIds.has(item.report_id)));
      })
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "项目详情加载失败"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [id]);

  useEffect(() => () => {
    previewRequestRef.current += 1;
  }, []);

  useEffect(() => {
    const pendingIds = jobs.filter((item) => item.status === "queued" || item.status === "running" || item.status === "sealing").map((item) => item.id);
    if (pendingIds.length === 0) return;
    let active = true;
    const timer = window.setInterval(() => {
      void Promise.all(pendingIds.map((jobId) => api.verification(jobId)))
        .then((details) => {
          if (!active) return;
          const byId = new Map(details.map((detail) => [detail.job.id, detail.job]));
          setJobs((current) => current.map((job) => byId.get(job.id) || job));
        })
        .catch((reason) => active && setError(reason instanceof Error ? reason.message : "任务状态刷新失败"));
    }, 900);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [jobs]);

  const latestJob = jobs[0] || null;
  const latestTruth = useMemo(() => latestJob ? analysisTruthFromJob(latestJob) : null, [latestJob]);
  const approvedCount = useMemo(() => jobs.filter((item) => item.status === "approved").length, [jobs]);

  const openEvidencePreview = async (job: VerificationJob) => {
    const requestId = ++previewRequestRef.current;
    setPreviewLoadingId(job.id);
    setPreviewError("");
    try {
      const next = await api.verification(job.id);
      if (requestId !== previewRequestRef.current) return;
      setPreviewDetail(next);
    } catch (reason) {
      if (requestId !== previewRequestRef.current) return;
      setPreviewDetail(null);
      setPreviewError(reason instanceof Error ? reason.message : "证据详情加载失败");
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoadingId("");
    }
  };

  const retryJob = async (job: VerificationJob) => {
    if (job.status !== "failed") return;
    setRetryingId(job.id);
    setError("");
    try {
      const retried = await api.retryVerification(job.id);
      setJobs((current) => current.map((item) => item.id === retried.id ? retried : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "失败任务重新排队失败");
    } finally {
      setRetryingId("");
    }
  };

  if (loading) return <div className="rounded-[28px] border border-slate-200 bg-white p-12 text-center text-sm text-slate-500">正在加载真实项目详情…</div>;
  if (!project) return <div className="rounded-[28px] border border-dashed border-slate-300 bg-white p-12 text-center"><p className="font-semibold text-slate-900">未找到该后端项目</p><p className="mt-2 text-sm text-slate-500">{error || "请检查项目 ID。"}</p><button onClick={() => navigate("/projects")} className="mt-4 rounded-2xl bg-sky-600 px-4 py-2 text-sm text-white">返回项目列表</button></div>;

  const tabs = [{ key: "jobs", label: `验真任务 ${jobs.length}` }, { key: "baselines", label: `设计基线 ${baselines.length}` }, { key: "reports", label: `结构化报告 ${reports.length}` }, { key: "proofs", label: `可信档案 ${proofs.length}` }] as const;

  return <div className="space-y-5 page-enter">
    {error ? <Notice type="info" message={error} /> : null}
    <section className="relative overflow-hidden rounded-[30px] border border-sky-300/20 bg-[#07172b] p-6 text-white">
      <div className="absolute inset-0 opacity-50 [background-image:linear-gradient(rgba(56,189,248,.07)_1px,transparent_1px),linear-gradient(90deg,rgba(56,189,248,.07)_1px,transparent_1px)] [background-size:32px_32px]" />
      <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div><div className="flex flex-wrap gap-2"><span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs text-cyan-100">{project.code}</span><span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">{project.status}</span></div><h2 className="mt-4 text-2xl font-semibold">{project.name}</h2><p className="mt-2 text-sm text-slate-300">{project.location} · {project.manager || "未指定负责人"}</p><p className="mt-3 max-w-2xl text-xs leading-5 text-slate-400">项目 ID {project.id}</p></div>
        <button onClick={() => navigate("/backend-workflow")} className="inline-flex items-center justify-center gap-2 rounded-2xl bg-cyan-400 px-4 py-3 text-sm font-semibold text-slate-950 hover:bg-cyan-300"><EyeIcon className="h-4 w-4" /> 进入验真闭环</button>
      </div>
    </section>

    <section className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
        { label: "已批准基线覆盖率（代理指标）", value: `${progress?.completion_rate ?? 0}%`, Icon: AnalyticsIcon },
        { label: "设计基线", value: baselines.length, Icon: DatabaseIcon },
        { label: "验真任务", value: jobs.length, Icon: CameraIcon },
        { label: "证据档案", value: proofs.length, Icon: BlockchainIcon },
      ].map(({ label, value, Icon }) => <div key={label} className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm"><div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-sky-50 text-sky-700"><Icon className="h-4 w-4" /></div><p className="mt-4 text-2xl font-semibold text-slate-900">{value}</p><p className="mt-1 text-xs leading-5 text-slate-500">{label}</p></div>)}</div>
      {progress?.metric_note ? <div className="flex items-start gap-2 rounded-2xl border border-sky-100 bg-sky-50/70 px-4 py-3 text-xs leading-5 text-sky-800"><InfoIcon className="mt-0.5 h-4 w-4 shrink-0" /><span>后端指标口径：{progress.metric_note}</span></div> : null}
    </section>

    <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div role="tablist" aria-label="项目详情分类" className="flex flex-wrap gap-2">{tabs.map((item) => <button key={item.key} role="tab" aria-selected={tab === item.key} onClick={() => setTab(item.key)} className={cn("rounded-full px-4 py-2 text-sm font-medium", tab === item.key ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-600")}>{item.label}</button>)}</div>
      <div className="mt-5">
        {tab === "jobs" ? <div className="space-y-3">
          {jobs.map((job) => {
            const truth = analysisTruthFromJob(job);
            return <div key={job.id} className="grid gap-3 rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:grid-cols-[1fr_auto_auto] md:items-center"><div className="min-w-0"><p className="truncate font-mono text-xs text-slate-500">{job.id}</p><div className="mt-2 flex flex-wrap items-center gap-2"><TruthBadge truth={truth} /><span className="max-w-full break-all font-mono text-[10px] text-slate-400">{job.analyzer_version}</span></div><p className="mt-1 truncate font-mono text-[10px] text-slate-400">证据 {job.evidence_id}</p>{job.status === "failed" ? <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2"><p className="break-words text-xs leading-5 text-rose-700">{job.error || "服务端未提供失败原因。"}</p><p className="mt-1 text-[10px] leading-4 text-rose-500">后端会复用固定版本和幂等键；配置漂移时拒绝重试。</p></div> : null}</div><span className={cn("w-fit rounded-full px-3 py-1 text-xs font-semibold", job.status === "approved" ? "bg-emerald-100 text-emerald-700" : job.status === "failed" || job.status === "rejected" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700")}>{jobLabel[job.status] || job.status}</span><div className="flex flex-wrap justify-end gap-2">{job.status === "failed" ? <button type="button" disabled={retryingId === job.id} onClick={() => void retryJob(job)} className="inline-flex min-h-10 items-center justify-center rounded-xl bg-rose-700 px-3 text-sm font-semibold text-white hover:bg-rose-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 disabled:cursor-wait disabled:bg-slate-300">{retryingId === job.id ? "重试中…" : "显式重试"}</button> : null}<button type="button" disabled={previewLoadingId === job.id} onClick={() => void openEvidencePreview(job)} className="inline-flex min-h-10 items-center justify-center gap-1 rounded-xl border border-sky-200 bg-white px-3 text-sm font-medium text-sky-700 hover:border-sky-400 hover:bg-sky-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-wait disabled:text-slate-400"><EyeIcon className="h-4 w-4" /> {previewLoadingId === job.id ? "读取中…" : "预览证据"}</button></div></div>;
          })}
          {jobs.length === 0 ? <Empty text="该项目尚未提交验真任务" /> : null}
          {previewError ? <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{previewError}</div> : null}
          {previewDetail ? (
            <div className="pt-2">
              <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">选中任务的服务端原件</p><p className="mt-1 text-xs text-slate-500">完整载入仅适合本地小样例；生产视频应改用短期播放票据以保留 Range。</p></div>
                <button type="button" onClick={() => setPreviewDetail(null)} className="self-start rounded-xl px-3 py-2 text-xs font-semibold text-slate-500 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400">关闭预览</button>
              </div>
              {previewDetail.job.result ? <div className="mb-3"><AnalysisTruthPanel job={previewDetail.job} /></div> : null}
              <EvidencePreview
                key={previewDetail.evidence.id}
                evidenceId={previewDetail.evidence.id}
                originalName={previewDetail.evidence.original_name}
                sha256={previewDetail.evidence.sha256}
                registeredSizeBytes={previewDetail.evidence.size_bytes}
                autoLoad
              />
            </div>
          ) : null}
        </div> : null}
        {tab === "baselines" ? <div className="grid gap-3 md:grid-cols-2">{baselines.map((item) => <div key={item.id} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4"><p className="text-xs font-semibold text-sky-700">{item.site_id} · {item.procedure_code}</p><p className="mt-2 text-sm font-semibold text-slate-900">版本 {item.version}</p><p className="mt-3 break-all font-mono text-[11px] text-slate-500">{item.sha256}</p></div>)}{baselines.length === 0 ? <Empty text="尚未登记设计基线" /> : null}</div> : null}
        {tab === "reports" ? <div className="space-y-3">{reports.map((item) => {
          const truth = reportTruthFromReport(item);
          return <div key={item.id} className="flex flex-col gap-3 rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:flex-row md:items-center md:justify-between"><div><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold text-slate-900">结构化报告 · schema {item.schema_version}</p><TruthBadge truth={truth} /></div><p className="mt-1 font-mono text-[11px] text-slate-500">{item.sha256}</p></div><div className="flex gap-2"><button onClick={() => void api.downloadReport(item.id, "json").catch((reason) => setError(reason instanceof Error ? reason.message : "JSON 报告下载失败"))} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700">JSON</button><button onClick={() => void api.downloadReport(item.id, "html").catch((reason) => setError(reason instanceof Error ? reason.message : "HTML 报告下载失败"))} className="rounded-xl bg-sky-600 px-3 py-2 text-xs font-medium text-white">HTML</button></div></div>;
        })}{reports.length === 0 ? <Empty text="尚未生成结构化报告" /> : null}</div> : null}
        {tab === "proofs" ? <div className="space-y-3">{proofs.map((item) => <div key={item.id} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4"><div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"><div><p className="text-sm font-semibold text-slate-900">{item.archive_id}</p><p className="mt-1 text-xs text-slate-500">Ledger #{item.ledger_index} · {item.evidence_grade ? "正式证据" : "非正式证据"}</p></div><button onClick={() => void api.downloadArchive(item.id).catch((reason) => setError(reason instanceof Error ? reason.message : "证据包下载失败"))} className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-3 py-2 text-xs font-medium text-white"><DownloadIcon className="h-4 w-4" /> 下载证据包</button></div><p className="mt-3 break-all font-mono text-[11px] text-slate-500">{item.archive_sha256}</p></div>)}{proofs.length === 0 ? <Empty text="尚未生成可信档案" /> : null}</div> : null}
      </div>
    </section>

    <div className="flex items-start gap-3 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800"><InfoIcon className="mt-0.5 h-4 w-4" /><span>最新任务：{latestJob && latestTruth ? `${jobLabel[latestJob.status] || latestJob.status}，${latestTruth.label}` : "暂无"}。已批准基线覆盖率是证据交付代理指标，不代表施工形象进度或算法准确率。</span></div>
  </div>;
};

const Empty: React.FC<{ text: string }> = ({ text }) => <div className="rounded-[22px] border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">{text}</div>;
