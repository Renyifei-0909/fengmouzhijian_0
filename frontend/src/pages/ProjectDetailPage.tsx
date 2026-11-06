import { useEffect, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ArrowUpRight, ClipboardList, Download, MapPinned, RefreshCw } from "lucide-react";
import { api, ApiRequestError } from "../lib/api";
import type { Baseline, Project, ProjectProgress, Proof, Report, VerificationDetail, VerificationJob } from "../lib/api";
import { labelJobStatus, labelProjectStatus, archiveTitle, projectDisplayName } from "../lib/productCopy";
import { analysisTruthFromJob } from "../lib/truth";
import { EvidencePreview } from "../components/ui/EvidencePreview";
import { RecordEmpty, RecordError, RecordMeta, RecordMetrics, RecordStatus, ReportStatus, recordError, recordObject, recordProcedure, recordText, recordTime } from "./records-ui";
import type { RecordTone } from "./records-ui";

const tabLabels = { jobs: "核验任务", baselines: "设计基线", reports: "交付报告", proofs: "归档记录" } as const;
type DetailTab = keyof typeof tabLabels;
const tabs: DetailTab[] = ["jobs", "baselines", "reports", "proofs"];
const jobTones: Record<VerificationJob["status"], RecordTone> = {
  queued: "neutral", running: "blue", needs_review: "amber", sealing: "blue", approved: "green", rejected: "red", failed: "red",
};

type ProjectWorkspaceData = {
  project: Project | null;
  progress: ProjectProgress | null;
  baselines: Baseline[] | null;
  jobs: VerificationJob[] | null;
  reports: Report[] | null;
  proofs: Proof[] | null;
};
const initialData: ProjectWorkspaceData = { project: null, progress: null, baselines: null, jobs: null, reports: null, proofs: null };
const isProcessing = ({ status }: VerificationJob) => status === "queued" || status === "running" || status === "sealing";
const payload = <T,>(result: PromiseSettledResult<T>) => result.status === "fulfilled" ? result.value : null;

export function ProjectDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  return <ProjectWorkspace key={id} id={id} />;
}

function ProjectWorkspace({ id }: { id: string }) {
  const [workspace, setWorkspace] = useState(initialData);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [loadError, setLoadError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [operationError, setOperationError] = useState("");
  const [pollError, setPollError] = useState("");
  const [tab, setTab] = useState<DetailTab>("jobs");
  const [selectedJobId, setSelectedJobId] = useState("");
  const [retryingId, setRetryingId] = useState("");
  const [downloadId, setDownloadId] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    setOperationError("");
    setPollError("");
    setNotFound(false);
    void Promise.allSettled([
      api.getProject(id), api.projectProgress(id), api.listBaselines(id), api.listVerifications(id), api.listReports(id), api.listProofs(),
    ]).then(([project, progress, baselines, jobs, reports, proofs]) => {
      if (!active) return;
      const reportIds = new Set(payload(reports)?.map((report) => report.id));
      setWorkspace({
        project: payload(project),
        progress: payload(progress),
        baselines: payload(baselines),
        jobs: payload(jobs),
        reports: payload(reports),
        proofs: reports.status === "fulfilled" ? payload(proofs)?.filter((proof) => reportIds.has(proof.report_id)) ?? null : null,
      });
      const failure = [project, progress, baselines, jobs, reports, proofs].find((result) => result.status === "rejected");
      if (failure) setLoadError(recordError(failure.reason, "项目记录读取失败。"));
      setNotFound(project.status === "rejected" && project.reason instanceof ApiRequestError && project.reason.status === 404);
      setLoading(false);
    });
    return () => { active = false; };
  }, [id, reload]);

  const pendingKey = workspace.jobs?.filter(isProcessing).map((item) => item.id).join(",") || "";
  useEffect(() => {
    if (!pendingKey || loading) return;
    const ids = pendingKey.split(",");
    let active = true;
    let timer = 0;
    const schedule = () => {
      timer = window.setTimeout(() => { void poll(); }, 900);
    };
    async function poll() {
      try {
        const details = await Promise.allSettled(ids.map((jobId) => api.verification(jobId)));
        if (!active) return;
        const updates = new Map<string, VerificationDetail>();
        for (const detail of details) if (detail.status === "fulfilled") updates.set(detail.value.job.id, detail.value);
        const progress = [...updates.values()].some(({ job }) => !isProcessing(job))
          ? await api.projectProgress(id).catch(() => null)
          : null;
        if (!active) return;
        setWorkspace((current) => {
          const reports = new Map(current.reports?.map((report) => [report.id, report]));
          const proofs = new Map(current.proofs?.map((proof) => [proof.id, proof]));
          for (const { report, proof } of updates.values()) {
            if (report) reports.set(report.id, report);
            if (proof) proofs.set(proof.id, proof);
          }
          return {
            ...current,
            progress: progress ?? current.progress,
            jobs: current.jobs?.map((job) => updates.get(job.id)?.job ?? job) ?? null,
            reports: current.reports ? [...reports.values()] : null,
            proofs: current.proofs ? [...proofs.values()] : null,
          };
        });
        const failure = details.find((detail) => detail.status === "rejected");
        setPollError(failure ? recordError(failure.reason, "核验状态刷新失败。") : "");
      } finally {
        if (active) schedule();
      }
    }
    schedule();
    return () => { active = false; window.clearTimeout(timer); };
  }, [id, pendingKey, loading]);

  const { project, progress, jobs, baselines, reports, proofs } = workspace;
  const selectedJob = jobs?.find((job) => job.id === selectedJobId) ?? jobs?.[0];
  const baselineById = new Map(baselines?.map((baseline) => [baseline.id, baseline]));
  const refresh = () => setReload((value) => value + 1);

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const offsets: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, Home: -index, End: tabs.length - 1 - index };
    const offset = offsets[event.key];
    if (offset === undefined) return;
    event.preventDefault();
    const next = tabs[(index + offset + tabs.length) % tabs.length];
    setTab(next);
    document.getElementById(`fm-project-tab-${next}`)?.focus();
  }

  async function retry(job: VerificationJob) {
    if (job.status !== "failed" || retryingId || loading) return;
    setRetryingId(job.id);
    setOperationError("");
    try {
      const updated = await api.retryVerification(job.id);
      setWorkspace((current) => ({ ...current, jobs: current.jobs?.map((item) => item.id === updated.id ? updated : item) ?? null }));
    } catch (reason) {
      setOperationError(recordError(reason, "任务重新排队失败。"));
    } finally {
      setRetryingId("");
    }
  }

  async function download(key: string, file: () => Promise<unknown>) {
    if (downloadId) return;
    setDownloadId(key);
    setOperationError("");
    try { await file(); }
    catch (reason) { setOperationError(recordError(reason, "文件下载失败。")); }
    finally { setDownloadId(""); }
  }

  if (!project) return <div className="fm-records page-enter">
    <Link className="fm-link inline-flex w-fit items-center gap-2" to="/projects"><ArrowLeft size={15} aria-hidden="true" />项目清单</Link>
    <section className="fm-panel">
      <RecordEmpty loading={loading}>{loading ? "正在读取项目…" : notFound ? "未找到该项目" : "项目详情暂不可用"}</RecordEmpty>
      {!loading && !notFound && loadError ? <div className="fm-record-body"><RecordError message={loadError} action={<button type="button" className="fm-button" onClick={refresh}>重新读取</button>} /></div> : null}
    </section>
  </div>;

  return <div className="fm-records page-enter">
    <div className="fm-record-toolbar">
      <Link className="fm-link inline-flex items-center gap-2" to="/projects"><ArrowLeft size={15} aria-hidden="true" />项目清单</Link>
      <div className="fm-record-actions">
        <button type="button" className="fm-button" disabled={loading || !!retryingId} onClick={refresh}><RefreshCw size={15} aria-hidden="true" />刷新</button>
        <Link className="fm-button" to={`/gis-map?project=${encodeURIComponent(project.id)}`}><MapPinned size={15} aria-hidden="true" />现场地图</Link>
        <Link className="fm-button fm-button-primary" to={selectedJob ? `/backend-workflow?job=${encodeURIComponent(selectedJob.id)}` : "/backend-workflow"}>核验中心 <ArrowUpRight size={15} aria-hidden="true" /></Link>
      </div>
    </div>
    <section className="fm-panel fm-record-banner">
      <div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span className="fm-record-muted">{project.code}</span>
          <RecordStatus tone={project.status === "active" ? "green" : project.status === "pending" ? "amber" : "neutral"}>{labelProjectStatus(project.status)}</RecordStatus>
        </div>
        <h2>{projectDisplayName(project.name)}</h2>
        <p className="fm-record-muted mt-2">{project.location}　·　{project.manager || "未指定负责人"}</p>
      </div>
      <RecordMetrics entries={[
        { label: "已批准基线覆盖率", value: progress ? `${progress.completion_rate}%` : "—" },
        { label: "设计基线", value: baselines?.length ?? "—" },
        { label: "核验任务", value: jobs?.length ?? "—" },
        { label: "待复核", value: progress?.pending_review_count ?? "—" },
        { label: "归档记录", value: proofs?.length ?? "—" },
      ]} />
      {progress?.metric_note ? <details className="fm-record-details px-4 py-2"><summary>覆盖率口径</summary><p>已批准基线占已登记基线的比例。</p></details> : null}
    </section>

    {loadError ? <RecordError message={loadError} /> : null}
    {operationError ? <RecordError message={operationError} /> : null}
    {pollError && tab === "jobs" ? <RecordError message={pollError} /> : null}

    <section className="fm-panel" aria-label="项目记录" aria-busy={loading}>
      <div className="fm-record-segments border-b border-slate-200" role="tablist" aria-label="项目记录分类">
        {tabs.map((key, index) => <button key={key} type="button" role="tab" id={`fm-project-tab-${key}`} aria-selected={tab === key} aria-controls="fm-project-tabpanel"
          tabIndex={tab === key ? 0 : -1} onKeyDown={(event) => moveTab(event, index)} onClick={() => { setTab(key); setOperationError(""); }}>
          {tabLabels[key]} <span className="fm-record-muted">{workspace[key]?.length ?? "—"}</span>
        </button>)}
      </div>
      <div id="fm-project-tabpanel" role="tabpanel" aria-labelledby={`fm-project-tab-${tab}`}>
        {tab === "jobs" ? selectedJob && jobs ? <div className="fm-record-workflow-body">
          <ul className="fm-record-list" aria-label="核验任务">
            {jobs.map((job) => {
              const baseline = baselineById.get(job.baseline_id);
              return <li key={job.id}><button type="button" className="fm-record-row" aria-pressed={job.id === selectedJob.id} onClick={() => { setSelectedJobId(job.id); setOperationError(""); }}>
                <ClipboardList size={18} strokeWidth={1.55} aria-hidden="true" />
                <span>
                  <span className="fm-record-name">{baseline?.site_id || "现场核验"}</span>
                  <small>{recordTime(job.created_at, true)}</small>
                  <span className="mt-1 block"><RecordStatus tone={jobTones[job.status]}>{labelJobStatus(job.status)}</RecordStatus></span>
                </span>
                <ArrowUpRight size={13} aria-hidden="true" />
              </button></li>;
            })}
          </ul>
          <ProjectJobInspector key={selectedJob.id} job={selectedJob} baseline={baselineById.get(selectedJob.baseline_id)}
            retrying={Boolean(retryingId)} onRetry={() => void retry(selectedJob)} />
        </div> : <RecordEmpty>{jobs ? "该项目暂无核验任务" : "核验记录暂不可用"}</RecordEmpty> : null}

        {tab === "baselines" ? baselines?.length ? <div className="fm-record-table-wrap"><table className="fm-record-table min-w-[520px]">
          <thead><tr><th scope="col">工点</th><th scope="col">工序</th><th scope="col">设计版本</th><th scope="col">登记时间</th><th scope="col">资料</th></tr></thead>
          <tbody>{baselines.map((item) => <tr key={item.id}><td className="font-semibold">{item.site_id}</td><td>{recordProcedure(item.procedure_code)}</td><td>{item.version}</td>
            <td className="fm-record-numeric">{recordTime(item.created_at)}</td><td><details className="text-xs"><summary className="fm-link cursor-pointer whitespace-nowrap">基线摘要</summary>
              <code className="mt-1 block max-w-48 break-all text-slate-400">{item.sha256}</code></details></td></tr>)}</tbody>
        </table></div> : <RecordEmpty>{baselines ? "尚未登记设计基线" : "设计基线暂不可用"}</RecordEmpty> : null}

        {tab === "reports" ? reports?.length ? <div className="fm-record-table-wrap"><table className="fm-record-table min-w-[550px]">
          <thead><tr><th scope="col">报告</th><th scope="col">状态</th><th scope="col">生成时间</th><th scope="col">操作</th></tr></thead>
          <tbody>{reports.map((item) => {
            const worksite = recordText(recordObject(recordObject(item.content).design_baseline).site_id);
            return <tr key={item.id}><td><Link className="fm-record-name" to={`/reports?report=${encodeURIComponent(item.id)}`}>工程核验报告</Link>{worksite ? <small>{worksite}</small> : null}</td>
              <td><ReportStatus value={item.status} /></td><td className="fm-record-numeric">{recordTime(item.created_at)}</td>
              <td><div className="fm-record-actions">
                <button type="button" className="fm-button" disabled={!!downloadId} onClick={() => void download(`${item.id}:json`, () => api.downloadReport(item.id, "json"))}>{downloadId === `${item.id}:json` ? "下载中…" : "结构化数据"}</button>
                <button type="button" className="fm-button" disabled={!!downloadId} onClick={() => void download(`${item.id}:html`, () => api.downloadReport(item.id, "html"))}>{downloadId === `${item.id}:html` ? "下载中…" : "网页版"}</button>
              </div></td></tr>;
          })}</tbody>
        </table></div> : <RecordEmpty>{reports ? "尚未生成交付报告" : "交付报告暂不可用"}</RecordEmpty> : null}

        {tab === "proofs" ? proofs?.length ? <div className="fm-record-table-wrap"><table className="fm-record-table min-w-[550px]">
          <thead><tr><th scope="col">档案编号</th><th scope="col">证据等级</th><th scope="col">归档时间</th><th scope="col">操作</th></tr></thead>
          <tbody>{proofs.map((item) => <tr key={item.id}>
            <td><Link className="fm-record-name" to={`/traceability?archive=${encodeURIComponent(item.archive_id)}`}>{archiveTitle(item.ledger_index)}</Link></td>
            <td>{item.evidence_grade ? "评测证据" : "工程过程资料"}</td><td className="fm-record-numeric">{recordTime(item.created_at)}</td>
            <td><button type="button" className="fm-button" disabled={!!downloadId} onClick={() => void download(item.id, () => api.downloadArchive(item.id))}><Download size={15} aria-hidden="true" />{downloadId === item.id ? "下载中…" : "下载证据包"}</button></td>
          </tr>)}</tbody>
        </table></div> : <RecordEmpty>{proofs ? "尚未生成归档记录" : "归档记录暂不可用"}</RecordEmpty> : null}
      </div>
    </section>
  </div>;
}

function ProjectJobInspector({ job, baseline, retrying, onRetry }: {
  job: VerificationJob;
  baseline?: Baseline;
  retrying: boolean;
  onRetry: () => void;
}) {
  const [detail, setDetail] = useState<VerificationDetail | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loadAttempt) return;
    let active = true;
    setLoading(true);
    setError("");
    void api.verification(job.id).then((next) => {
      if (active) setDetail(next);
    }).catch((reason) => {
      if (active) setError(recordError(reason, "证据详情读取失败。"));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [job.id, loadAttempt]);

  const truth = analysisTruthFromJob(job);
  const evidenceId = detail?.evidence?.id || "";
  function togglePreview() {
    if (!evidenceId) {
      setLoadAttempt((value) => value + 1);
      return;
    }
    setLoadAttempt(0);
    setDetail(null);
  }

  return <div className="fm-record-inspector">
    <div className="fm-record-toolbar">
      <h3>任务详情</h3>
      <RecordStatus tone={jobTones[job.status]}>{labelJobStatus(job.status)}</RecordStatus>
    </div>
    <RecordMeta entries={[
      ["工点", baseline?.site_id],
      ["工序", baseline ? recordProcedure(baseline.procedure_code) : null],
      ["提交时间", recordTime(job.created_at)],
      ["处理进度", isProcessing(job) ? `${job.progress}%` : null],
    ]} />
    {isProcessing(job) ? <progress className="fm-record-meter" value={job.progress} max={100} aria-label="核验进度" /> : null}
    {job.status === "failed" && job.error ? <RecordError message={job.error} /> : null}
    <div className="fm-record-actions">
      {job.status === "failed" ? <button type="button" className="fm-button" disabled={retrying} onClick={onRetry}>{retrying ? "正在重新排队…" : "重试任务"}</button> : null}
      <Link className="fm-button fm-button-primary" to={`/backend-workflow?job=${encodeURIComponent(job.id)}`}>打开核验记录 <ArrowUpRight size={15} aria-hidden="true" /></Link>
      <button type="button" className="fm-button" disabled={loading} aria-expanded={!!evidenceId} onClick={togglePreview}>
        {loading ? "读取中…" : evidenceId ? "关闭预览" : "预览证据"}
      </button>
    </div>
    {error ? <RecordError message={error} /> : null}
    {detail && evidenceId ? <EvidencePreview
      evidenceId={evidenceId}
      originalName={detail.evidence.original_name}
      sha256={detail.evidence.sha256}
      registeredSizeBytes={detail.evidence.size_bytes}
      autoLoad
    /> : detail ? <RecordEmpty>该任务暂无可预览的证据</RecordEmpty> : null}
    <details className="fm-record-details">
      <summary>任务记录</summary>
      <RecordMeta entries={[
        ["任务编号", job.id],
        ["分析版本", job.analyzer_version],
        ["证据等级", truth.evidenceGrade ? "评测证据" : "工程过程资料"],
      ]} />
    </details>
  </div>;
}
