import { projectDisplayName } from "../lib/productCopy";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowLeft, ArrowUpRight, Check, ChevronRight, ClipboardList, Download, FileCheck2, FileUp, Plus, RefreshCw, Search, Settings2 } from "lucide-react";
import { Modal } from "../components/ui/Modal";
import { FieldScene } from "../components/ui/FieldScene";
import { OperationStatus } from "../components/ui/OperationStatus";
import { api, ApiRequestError } from "../lib/api";
import type {
  AnalyzerName, Baseline, CapabilityMeta, FindingCaseDetail, IntegrityCheck, Project, RemediationAttempt,
  VerificationDetail, VerificationJob, VerificationOperationsSnapshot,
} from "../lib/api";
import { isTrialJob, isTrialProject } from "../lib/truth";
import { groupDifferenceRows, labelHumanReviewStatus, labelRuleVerdict, structureObservations } from "../lib/verificationDisplay";
import {
  apiMessage, findingTitle, LinkedWorkOrder, localTime, objectValue, textValue, useRecordedEvidence, WorkbenchEmpty, WorkbenchEvidence, WorkbenchMeta, WorkbenchStatus,
} from "./workbench-ui";
import type { WorkbenchTone } from "./workbench-ui";

const jobStatus: Record<VerificationJob["status"], { label: string; tone: WorkbenchTone }> = {
  queued: { label: "排队中", tone: "neutral" },
  running: { label: "处理中", tone: "cyan" },
  needs_review: { label: "待复核", tone: "amber" },
  sealing: { label: "封存中", tone: "cyan" },
  approved: { label: "已封存", tone: "green" },
  rejected: { label: "已驳回", tone: "red" },
  failed: { label: "处理失败", tone: "red" },
};

const queueFilters = {
  all: { label: "全部", statuses: [] },
  review: { label: "待复核", statuses: ["needs_review"] },
  active: { label: "处理中", statuses: ["queued", "running", "sealing"] },
  finished: { label: "已完成", statuses: ["approved", "rejected"] },
  failed: { label: "处理失败", statuses: ["failed"] },
} satisfies Record<string, { label: string; statuses: VerificationJob["status"][] }>;

type QueueFilter = keyof typeof queueFilters;
type RemediationResolution = "resolved" | "not_resolved";
type RemediationContext = {
  detail: FindingCaseDetail;
  attempt: RemediationAttempt;
  project: Project;
  baseline: Baseline;
};

const analyzerLabel: Record<string, string> = {
  stub: "人工核验",
  remote_http: "辅助识别",
  demo_fixture: "规则辅助（演示）",
};

const alertLabels: Record<VerificationOperationsSnapshot["alerts"][number]["code"], string> = {
  INTEGRITY_INCIDENT: "调度记录存在完整性异常",
  DEAD_LETTER_PRESENT: "存在等待人工处理的失败任务",
  QUEUE_WAIT_EXCEEDED: "排队时间超过设定阈值",
  RECENT_LEASE_INSTABILITY: "近期执行节点连接不稳定",
};

const isAuthError = (reason: unknown) => reason instanceof ApiRequestError && [401, 403].includes(reason.status ?? 0);
const matchesStatus = (job: VerificationJob, filter: QueueFilter) => filter === "all"
  || (queueFilters[filter].statuses as VerificationJob["status"][]).includes(job.status);

const loadFailure = "核验任务读取失败";

export function BackendWorkflowPage() {
  const [params, setParams] = useSearchParams();
  const caseId = params.get("caseId")?.trim() || "";
  const attemptId = params.get("attemptId")?.trim() || "";
  const queryJob = params.get("job")?.trim() || "";
  const remediation = Boolean(caseId || attemptId);
  const caseReturnQuery = new URLSearchParams();
  if (caseId) caseReturnQuery.set("caseId", caseId);
  if (params.get("scope") === "trial") caseReturnQuery.set("scope", "trial");
  const requestedStatus = params.get("status");
  const filter: QueueFilter = requestedStatus === "needs_review" ? "review"
    : requestedStatus === "failed" ? "failed"
      : requestedStatus && ["queued", "running", "sealing"].includes(requestedStatus) ? "active"
        : requestedStatus && ["approved", "rejected"].includes(requestedStatus) ? "finished" : "all";
  const uploadOpen = params.get("action") === "upload";
  const [jobs, setJobs] = useState<VerificationJob[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [meta, setMeta] = useState<CapabilityMeta | null>(null);
  const [keyword, setKeyword] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [includeTrial, setIncludeTrial] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const [accessRevision, setAccessRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [queueError, setQueueError] = useState("");
  const queueRequest = useRef(0);
  const queueJobUpdates = useRef(new Map<string, VerificationJob>());

  useEffect(() => {
    let active = true;
    void api.meta().then((next) => { if (active) setMeta(next); })
      .catch(() => { if (active) setMeta(null); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const generation = ++queueRequest.current;
    const updates = new Map<string, VerificationJob>();
    queueJobUpdates.current = updates;
    setLoading(true);
    setQueueError("");
    void Promise.allSettled([api.listVerifications(), api.listProjects()]).then(([tasks, engineeringProjects]) => {
      if (generation !== queueRequest.current) return;
      if (tasks.status === "fulfilled") {
        setJobs([...tasks.value.map((item) => updates.get(item.id) ?? item),
          ...[...updates.values()].filter((item) => !tasks.value.some(({ id }) => id === item.id))]);
      }
      if (engineeringProjects.status === "fulfilled") setProjects(engineeringProjects.value);
      const failure = tasks.status === "rejected" ? tasks.reason : engineeringProjects.status === "rejected" ? engineeringProjects.reason : null;
      if (failure) setQueueError(apiMessage(failure, "任务列表读取失败"));
      setLoading(false);
    });
    return () => { queueRequest.current += 1; };
  }, [accessRevision, reload]);

  const projectById = useMemo(() => new Map(projects.map((project) => [project.id, project])), [projects]);
  const scopedJobs = useMemo(() => {
    const search = keyword.trim().toLocaleLowerCase();
    return jobs.filter((job) => {
      const project = projectById.get(job.project_id);
      return (includeTrial || (!isTrialJob(job, meta) && !isTrialProject(project)))
        && (!projectFilter || projectFilter === job.project_id)
        && (!search || [job.id, project?.name, project && projectDisplayName(project.name), project?.code, project?.location, jobStatus[job.status].label]
          .some((value) => value?.toLocaleLowerCase().includes(search)));
    }).sort((left, right) => right.created_at.localeCompare(left.created_at));
  }, [includeTrial, jobs, keyword, meta, projectById, projectFilter]);
  const visibleJobs = scopedJobs.filter((job) => matchesStatus(job, filter));
  const activeJob = queryJob;
  const activeRow = jobs.find((job) => job.id === activeJob);

  useEffect(() => {
    if (remediation || queryJob || !visibleJobs[0]) return;
    const id = visibleJobs[0].id;
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("job", id);
      return next;
    }, { replace: true });
  }, [queryJob, remediation, setParams, visibleJobs]);

  const syncJob = useCallback((job: VerificationJob) => {
    queueJobUpdates.current.set(job.id, job);
    setJobs((current) => current.some((item) => item.id === job.id)
      ? current.map((item) => item.id === job.id ? job : item)
      : [job, ...current]);
  }, []);

  const setParam = (key: string, value: string, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace });
  const afterUpload = (job: VerificationJob) => {
    syncJob(job);
    setKeyword("");
    setProjectFilter("");
    setParams({ job: job.id }, { replace: true });
    setReload((value) => value + 1);
  };
  const activeProjects = includeTrial ? projects : projects.filter((project) => !isTrialProject(project));

  return <div className="fm-wf page-enter">
    <header className="fm-wf-heading">
      <div>
        {remediation ? <Link className="fm-wf-back" to={caseReturnQuery.size ? `/alarms?${caseReturnQuery}` : "/alarms"}><ArrowLeft size={14} /> 返回整改中心</Link> : null}
        <h2>{remediation ? "整改复验" : "任务与复核"}</h2>
        <p>{remediation ? "核对整改前后资料，记录本次现场复验结论。" : "按项目核对现场资料、复核工程偏差并封存证据。"}</p>
      </div>
      <div className="fm-wf-actions">
        <button type="button" className="fm-button" onClick={() => setSettingsOpen(true)}><Settings2 size={16} />运行设置</button>
        {!remediation ? <button type="button" className="fm-button fm-button-primary" onClick={() => setParam("action", "upload", true)}><Plus size={17} />提交核验资料</button> : null}
      </div>
    </header>

    {remediation ? (
      <RemediationWorkbench key={JSON.stringify([caseId, attemptId, accessRevision])}
        caseId={caseId} attemptId={attemptId} meta={meta} onJob={syncJob} />
    ) : (
      <div className="fm-wf-layout">
        <section className="fm-panel fm-wf-queue" aria-label="核验任务列表" aria-busy={loading}>
          <div className="fm-wf-queue-head">
            <div className="fm-wf-section-head"><h2><ClipboardList size={17} />核验任务</h2>
              <button type="button" className="fm-wf-icon-button" title="刷新任务" aria-label="刷新任务" disabled={loading}
                onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} /></button>
            </div>
            <label className="fm-wf-search"><Search size={15} />
              <input aria-label="搜索核验任务" placeholder="搜索项目、工点或任务" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
            </label>
            <select aria-label="按项目筛选核验任务" className="fm-input" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
              <option value="">全部项目</option>
              {activeProjects.map((project) => <option key={project.id} value={project.id}>{projectDisplayName(project.name)}</option>)}
            </select>
          </div>
          <div className="fm-wf-tabs" aria-label="核验任务状态">
            {(Object.keys(queueFilters) as QueueFilter[]).map((key) => <button key={key} type="button" aria-pressed={filter === key}
              onClick={() => setParam("status", queueFilters[key].statuses[0] || "", true)}>{queueFilters[key].label}{key !== "all" ? <span>{scopedJobs.filter((job) => matchesStatus(job, key)).length}</span> : null}</button>)}
          </div>
          {queueError ? <p className="fm-wf-message" data-tone="red" role="alert">{queueError}</p> : null}
          {visibleJobs.length ? <ol className="fm-wf-list">
            {visibleJobs.map((job) => {
              const project = projectById.get(job.project_id);
              const status = jobStatus[job.status];
              return <li key={job.id}>
                <button type="button" aria-pressed={activeJob === job.id} onClick={() => setParam("job", job.id)}>
                  <FileCheck2 size={17} aria-hidden="true" />
                  <div className="fm-wf-row-text">
                    <strong title={project ? projectDisplayName(project.name) : undefined}>{project ? projectDisplayName(project.name) : "工程核验"}</strong>
                    <small>{localTime(job.created_at, true)}{project?.location ? ` · ${project.location}` : ""}</small>
                  </div>
                  <WorkbenchStatus tone={status.tone}>{status.label}</WorkbenchStatus>
                </button>
              </li>;
            })}
          </ol> : <WorkbenchEmpty title={loading ? "正在读取任务" : "暂无核验任务"}>
            {loading || queueError ? null : keyword || filter !== "all" || projectFilter ? "可调整搜索条件或状态筛选。" : "从工单采集或提交现场资料开始。"}
          </WorkbenchEmpty>}
          <div className="fm-wf-queue-foot"><span>{visibleJobs.length} 项</span>{includeTrial ? <span>含扩展记录</span> : null}</div>
        </section>
        {activeJob ? <JobWorkbench key={JSON.stringify([activeJob, accessRevision])}
          jobId={activeJob} project={activeRow ? projectById.get(activeRow.project_id) : undefined} meta={meta} onJob={syncJob} />
          : <section className="fm-panel fm-wf-unselected"><WorkbenchEmpty title="核验工作区" media>
            选择一项任务查看现场原件与复核记录。
          </WorkbenchEmpty></section>}
      </div>
    )}

    {uploadOpen && !remediation ? <VerificationUpload
      projects={activeProjects} initialProjectId={activeRow?.project_id || projectFilter}
      meta={meta} allowTrial={includeTrial} onClose={() => setParam("action", "", true)} onUploaded={afterUpload}
    /> : null}
    {settingsOpen ? <VerificationSettings meta={meta} includeTrial={includeTrial}
      onTrialChange={(value) => {
        setIncludeTrial(value);
        if (!value && isTrialProject(projectById.get(projectFilter))) setProjectFilter("");
      }}
      onAccessChange={() => {
        setJobs([]);
        setProjects([]);
        setAccessRevision((value) => value + 1);
      }} onClose={() => setSettingsOpen(false)} /> : null}
  </div>;
}

function RemediationWorkbench({ caseId, attemptId, meta, onJob }: {
  caseId: string; attemptId: string; meta: CapabilityMeta | null; onJob: (job: VerificationJob) => void;
}) {
  const [context, setContext] = useState<RemediationContext | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const source = useRecordedEvidence(context?.detail.case.source_job_id, context?.detail.case.source_evidence_id);

  useEffect(() => {
    if (!caseId || !attemptId) {
      setError("整改复验链接不完整，请从整改记录重新进入。");
      return;
    }
    let active = true;
    setError("");
    void api.findingCase(caseId).then(async (detail) => {
      const attempt = detail.attempts.find((item) => item.id === attemptId);
      if (!attempt) throw new Error("当前整改记录不存在，请返回整改中心。");
      const [project, baselines] = await Promise.all([api.getProject(detail.case.project_id), api.listBaselines(detail.case.project_id)]);
      const baseline = baselines.find((item) => item.id === detail.case.baseline_id);
      if (!baseline) throw new Error("当前案件的设计基线无法读取。");
      if (active) setContext({ detail, attempt, project, baseline });
    }).catch((reason: unknown) => { if (active) setError(apiMessage(reason, "整改复验记录读取失败")); });
    return () => { active = false; };
  }, [attemptId, caseId, revision]);

  if (error || !context) return <section className="fm-panel fm-wf-unselected">
    <WorkbenchEmpty title={error || "正在读取整改记录"}>
      {error ? <button type="button" className="fm-button" onClick={() => setRevision((value) => value + 1)}>重新读取</button> : null}
    </WorkbenchEmpty>
  </section>;

  const { detail, attempt, project, baseline } = context;
  const canUpload = detail.case.status === "remediation_in_progress" && attempt.resolution_decision === "pending" && !attempt.verification_job_id;
  return <>
    <section className="fm-panel fm-wf-remediation-context">
      <div><span className="fm-wf-label">第 {attempt.attempt_no} 次整改</span><h2>{findingTitle(detail.case.finding_code, detail.case.finding_message)}</h2>
        <p>{projectDisplayName(project.name)} · {baseline.site_id} · {baseline.version}</p></div>
      <div className="fm-wf-actions">
        <Link className="fm-button" to={`/backend-workflow?job=${encodeURIComponent(detail.case.source_job_id)}`}>原核验任务 <ArrowUpRight size={14} /></Link>
        {canUpload ? <button type="button" className="fm-button fm-button-primary" onClick={() => setUploadOpen(true)}><FileUp size={16} />提交复验资料</button> : null}
      </div>
    </section>
    {attempt.verification_job_id ? <JobWorkbench key={attempt.verification_job_id} jobId={attempt.verification_job_id}
      project={project} meta={meta} remediation={context} original={source} onJob={onJob} /> : <section className="fm-panel fm-wf-unselected">
      <div className="fm-wf-unbound-compare">
        {source.evidence ? <WorkbenchEvidence evidence={source.evidence} title="整改前" />
          : <WorkbenchEmpty title={source.loading ? "正在读取整改前资料" : "整改前资料暂不可用"} media />}
        {canUpload ? <FieldScene scene="recheck" guidance="提交时拍清整改部位、周边范围和工点标记。" />
          : <WorkbenchEmpty title="暂无整改后资料" media />}
      </div>
    </section>}
    {uploadOpen && canUpload ? <VerificationUpload projects={[project]} meta={meta} locked={context} allowTrial={detail.case.scope === "demo"}
      onClose={() => setUploadOpen(false)} onUploaded={(job) => {
        onJob(job);
        setUploadOpen(false);
        setContext({ ...context, detail: { ...detail, case: { ...detail.case, status: "verification_pending" } },
          attempt: { ...attempt, verification_job_id: job.id } });
      }} /> : null}
  </>;
}

function JobWorkbench({ jobId, project, meta, remediation, original, onJob }: {
  jobId: string;
  project?: Project;
  meta: CapabilityMeta | null;
  remediation?: RemediationContext;
  original?: ReturnType<typeof useRecordedEvidence>;
  onJob: (job: VerificationJob) => void;
}) {
  const [detail, setDetail] = useState<VerificationDetail | null>(null);
  const [baseline, setBaseline] = useState<Baseline | null>(remediation?.baseline ?? null);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState("");
  const [pollFailures, setPollFailures] = useState(0);
  const [pollPaused, setPollPaused] = useState(false);
  const [pendingCases, setPendingCases] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [resolution, setResolution] = useState<RemediationResolution | "">("");
  const [integrity, setIntegrity] = useState<{ proofId: string; result: IntegrityCheck } | null>(null);
  const requestVersion = useRef(0);
  const operation = useRef<string | null>(null);
  const mounted = useRef(false);
  const lockedAttemptId = remediation?.attempt.id;
  const lockedProjectId = remediation?.project.id;
  const lockedBaselineId = remediation?.baseline.id;
  const originalJobId = remediation?.detail.case.source_job_id;
  const readJob = useCallback(async () => {
    const request = ++requestVersion.current;
    try {
      const next = await api.verification(jobId);
      if (request !== requestVersion.current || !mounted.current) return;
      if (lockedAttemptId && (next.remediation_attempt?.id !== lockedAttemptId
        || next.job.project_id !== lockedProjectId || next.job.baseline_id !== lockedBaselineId)) {
        setDetail(null);
        setPollPaused(true);
        setRefreshError("当前任务与整改记录不匹配。");
        return;
      }
      setDetail(next);
      onJob(next.job);
      setPollFailures(0);
      setPollPaused(false);
      setRefreshError("");
    } catch (reason) {
      if (request !== requestVersion.current || !mounted.current) return;
      setPollFailures((value) => value + 1);
      setPollPaused(isAuthError(reason));
      setRefreshError(apiMessage(reason, loadFailure));
    } finally {
      if (request === requestVersion.current && mounted.current) setLoading(false);
    }
  }, [jobId, lockedAttemptId, lockedBaselineId, lockedProjectId, onJob]);

  useEffect(() => {
    mounted.current = true;
    void readJob();
    return () => { mounted.current = false; requestVersion.current += 1; };
  }, [readJob]);

  const job = detail?.job;
  const polling = job?.status === "queued" || job?.status === "running"
    || (job?.status === "sealing" && !detail?.recovery.last_error);
  useEffect(() => {
    if (!polling || pollPaused || busy) return;
    const timer = window.setTimeout(() => void readJob(), Math.min(1200 * 2 ** pollFailures, 15_000));
    return () => window.clearTimeout(timer);
  }, [busy, detail, pollFailures, pollPaused, polling, readJob]);

  const projectId = job?.project_id;
  const baselineId = job?.baseline_id;
  useEffect(() => {
    if (!projectId || !baselineId || lockedBaselineId) return;
    let active = true;
    void api.listBaselines(projectId).then((rows) => {
      if (active) setBaseline(rows.find((row) => row.id === baselineId) ?? null);
    }).catch(() => {});
    return () => { active = false; };
  }, [baselineId, lockedBaselineId, projectId]);

  const needsReview = job?.status === "needs_review";
  const trial = Boolean(job && (isTrialJob(job, meta) || isTrialProject(project)));
  useEffect(() => {
    if (!needsReview || !projectId || trial) return;
    let active = true;
    void api.listFindingCases({ projectId, status: "pending_triage", scope: "operational" }).then((items) => {
      if (active) setPendingCases(items.filter((item) => item.source_job_id === jobId).length);
    }).catch(() => { if (active) setPendingCases(null); });
    return () => { active = false; };
  }, [jobId, needsReview, projectId, revision, trial]);

  const run = async (name: string, action: () => Promise<void>, refreshOnError = false) => {
    if (operation.current) return;
    operation.current = name;
    setBusy(name);
    setError("");
    try {
      await action();
    } catch (reason) {
      if (!mounted.current) return;
      const message = apiMessage(reason, "操作未能完成");
      setError(message.includes("candidate findings") ? "请先在整改中心处理待确认事项。" : message);
      if (refreshOnError) {
        await readJob();
        if (mounted.current) setRevision((value) => value + 1);
      }
    } finally {
      if (mounted.current) setBusy("");
      operation.current = null;
    }
  };

  const review = (decision: "approve" | "reject", resume = false) => {
    if (!detail || (decision === "approve" && pendingCases && !resume)) return;
    const attempt = detail.remediation_attempt;
    const result = resume ? attempt?.resolution_decision : resolution;
    if (decision === "approve" && attempt && result !== "resolved" && result !== "not_resolved") return;
    const note = resume ? attempt?.resolution_note || "继续封存已复核记录。"
      : reviewNote.trim() || (decision === "approve" ? "已完成现场资料复核。" : "本次复核未通过。");
    void run(resume ? "seal" : decision, async () => {
      requestVersion.current += 1;
      const outcome = await api.review(jobId, {
        decision, reviewer: "网页复核员", note,
        remediation_resolution: decision === "approve" && attempt && (result === "resolved" || result === "not_resolved") ? result : undefined,
      });
      if (!mounted.current) return;
      setDetail({ ...detail, job: outcome.job, report: outcome.report, proof: outcome.proof });
      onJob(outcome.job);
      await readJob();
    }, true);
  };

  const retry = () => {
    if (!detail || detail.job.status !== "failed" || !detail.recovery.retryable) return;
    void run("retry", async () => {
      requestVersion.current += 1;
      const next = await api.retryVerification(jobId);
      if (!mounted.current) return;
      setDetail({ ...detail, job: next });
      onJob(next);
      await readJob();
    }, true);
  };

  if (!detail || !job) return <section className="fm-panel fm-wf-unselected" aria-busy={loading}>
    <WorkbenchEmpty title={loading ? "正在读取核验记录" : "核验记录暂不可用"} media>
      {refreshError ? <p role="alert">{refreshError}</p> : null}
      {!loading ? <button type="button" className="fm-button" onClick={() => { setLoading(true); void readJob(); }}>重新读取</button> : null}
    </WorkbenchEmpty>
  </section>;

  const analysis = objectValue(job.result);
  const compliance = objectValue(analysis.compliance_evaluation);
  const ruleVerdict = textValue(compliance.verdict);
  const differences = groupDifferenceRows(ruleVerdict ? objectValue(analysis.alignment).differences : null);
  const observations = structureObservations(objectValue(objectValue(analysis.observations).measurements)).known;
  const orderMetadata = remediation ? original?.evidence?.metadata : detail.evidence.metadata;
  const reportOrder = objectValue(detail.report?.content.work_order);
  const workOrderId = textValue(reportOrder.id) || textValue(orderMetadata?.work_order_id);
  const workOrderCode = textValue(reportOrder.code) || textValue(orderMetadata?.work_order_code);
  const analysisMode = textValue(analysis.analysis_mode) || job.analyzer_name;
  const provenance = trial && analysisMode !== "demo_fixture" ? "规则辅助" : analyzerLabel[analysisMode] || "其他来源";
  const analysisStatus = jobStatus[job.status];
  const { report, proof, recovery, remediation_attempt: attempt } = detail;
  const packageCheck = integrity?.proofId === proof?.id ? integrity?.result : null;
  const currentStep = job.status === "needs_review" || job.status === "rejected" ? 2
    : job.status === "sealing" || job.status === "approved" ? 3 : 1;
  const canResume = recovery.action === "resume_sealing" && recovery.retryable
    && (!attempt || attempt.resolution_decision === "resolved" || attempt.resolution_decision === "not_resolved");
  const finished = job.status === "approved" && Boolean(proof);
  const awaitingAnalysis = (job.status === "queued" || job.status === "running") && !refreshError;
  const automated = job.analyzer_name !== "stub";

  return <article className="fm-panel fm-wf-detail">
    <header className="fm-wf-detail-head">
      <div className="fm-wf-detail-title">
        <span className="fm-wf-label">{remediation ? "复验记录" : "任务详情"}</span>
        <h2>{project ? projectDisplayName(project.name) : "工程核验记录"}</h2>
        <p>{[baseline?.site_id || project?.location, baseline?.version && `基线 ${baseline.version}`, localTime(job.created_at)].filter(Boolean).join(" · ")}</p>
      </div>
      <div className="fm-wf-actions">
        <WorkbenchStatus tone={analysisStatus.tone}>{analysisStatus.label}</WorkbenchStatus>
        <button type="button" className="fm-wf-icon-button" title="刷新当前任务" aria-label="刷新当前任务" disabled={Boolean(busy)}
          onClick={() => { setRevision((value) => value + 1); void readJob(); }}><RefreshCw size={15} /></button>
      </div>
    </header>
    <ol className="fm-wf-steps" aria-label="核验进度">
      {["资料登记", "工程核验", "人工复核", "证据封存"].map((step, index) => <li key={step}
        data-state={index < currentStep || (index === currentStep && finished) ? "done" : index === currentStep ? "current" : "pending"}
        aria-current={index === currentStep && !finished ? "step" : undefined}>
        <span>{index + 1}</span>{step}{index < 3 ? <ChevronRight size={13} aria-hidden="true" /> : null}
      </li>)}
    </ol>
    {(awaitingAnalysis || needsReview) && <div className="fm-wf-task-status" data-ready={needsReview}>
      <div className="fm-wf-task-status-copy" role="status">
        {needsReview ? <Check size={17} aria-hidden="true" /> : <span className="fm-operation-orbit" aria-hidden="true" />}
        <span><strong>{needsReview ? automated ? "核验结果已就绪" : "复核资料已就绪"
          : job.status === "queued" ? "影像已登记，正在排队"
          : automated ? "正在后台分析影像" : "正在整理复核资料"}</strong>
        <small>{needsReview ? "可对照现场原件和核验摘要，填写复核意见。"
          : job.status === "queued" ? "后台领取任务后开始处理。"
          : "处理完成后，本页会自动显示结果。"}</small></span>
      </div>
      {awaitingAnalysis && job.status === "running" && <div className="fm-wf-progress">
        <progress aria-label="后台处理进度" value={job.progress} max={100} /><strong>{job.progress}%</strong>
      </div>}
    </div>}
    {(busy === "approve" || busy === "seal" || (job.status === "sealing" && !canResume)) &&
      <OperationStatus detail="正在生成报告、校验文件并写入交付档案。">正在封存复核记录</OperationStatus>}
    {refreshError ? <p className="fm-wf-message" data-tone="amber" role="status">{refreshError}</p> : null}
    {error ? <p className="fm-wf-message" data-tone="red" role="alert">{error}</p> : null}
    <div className="fm-wf-inspection" data-remediation={Boolean(remediation)}>
      <div className="fm-wf-media-stack" data-compare={Boolean(remediation)}>
        {remediation ? original?.evidence ? <WorkbenchEvidence evidence={original.evidence} title="整改前" /> :
          <WorkbenchEmpty title={original?.loading ? "正在读取整改前资料" : "整改前资料暂不可用"} media>
            <Link className="fm-wf-link" to={`/backend-workflow?job=${encodeURIComponent(originalJobId || "")}`}>查看原核验记录</Link>
          </WorkbenchEmpty> : null}
        <WorkbenchEvidence evidence={detail.evidence} title={remediation ? "整改后" : "现场原件"} />
      </div>
      <div className="fm-wf-inspection-side">
        <section className="fm-wf-subsection">
          <div className="fm-wf-section-head"><h3>核验摘要</h3>{trial ? <WorkbenchStatus>扩展记录</WorkbenchStatus> : null}</div>
          <WorkbenchMeta entries={[
            ["规则初判", ruleVerdict ? labelRuleVerdict(ruleVerdict) : "暂无规则判定"],
            ["人工复核", labelHumanReviewStatus(job.status)],
            ["关联工单", workOrderId ? <LinkedWorkOrder id={workOrderId} code={workOrderCode} revision={`${job.status}:${revision}`} /> : "未关联工单"],
            ["资料来源", detail.evidence.metadata.media_origin === "field_photo_redacted" ? "外部现场影像（脱敏）"
              : remediation ? "整改复验" : "资料提交"],
            ["处理方式", provenance],
          ]} />
          {differences.bad.length ? <p className="fm-wf-observation" data-tone="red">偏差 · {differences.bad.join("、")}</p> : null}
          {differences.pending.length ? <p className="fm-wf-observation" data-tone="amber">待核对 · {differences.pending.join("、")}</p> : null}
          {differences.ok.length ? <p className="fm-wf-observation" data-tone="green">已核对 · {differences.ok.join("、")}</p> : null}
          {observations.length ? <WorkbenchMeta entries={observations.map((item) => [item.label, item.displayValue])} /> : null}
        </section>

        {needsReview ? <section className="fm-wf-subsection">
          <div className="fm-wf-section-head"><h3>{attempt ? "复验结论" : "人工复核"}</h3></div>
          {pendingCases ? <Link className="fm-wf-action-link" to={`/alarms?job=${encodeURIComponent(jobId)}`}>{pendingCases} 项待确认事项 <ArrowUpRight size={14} /></Link> : null}
          {attempt ? <fieldset className="fm-wf-choices">
            <legend>整改结果</legend>
            <label><input type="radio" name="remediation-resolution" checked={resolution === "resolved"} onChange={() => setResolution("resolved")} />已解决</label>
            <label><input type="radio" name="remediation-resolution" checked={resolution === "not_resolved"} onChange={() => setResolution("not_resolved")} />继续整改</label>
          </fieldset> : null}
          <label className="fm-wf-field">复核意见<textarea rows={3} maxLength={1600} className="fm-input" value={reviewNote}
            onChange={(event) => setReviewNote(event.target.value)} placeholder="填写现场判定或需补充的资料" /></label>
          <div className="fm-wf-actions">
            <button type="button" className="fm-button fm-button-primary"
              disabled={Boolean(busy || pendingCases || (attempt && !resolution))} onClick={() => review("approve")}>
              {busy === "approve" ? "正在提交…" : attempt && resolution === "resolved" ? "通过并关闭案件" : "通过并封存"}
            </button>
            <button type="button" className="fm-button" disabled={Boolean(busy)} onClick={() => review("reject")}>{busy === "reject" ? "正在驳回…" : "驳回"}</button>
          </div>
        </section> : null}
        {attempt && attempt.resolution_decision !== "pending" && !needsReview ? <section className="fm-wf-subsection">
          <div className="fm-wf-section-head"><h3>已记录的复验结论</h3></div>
          <WorkbenchMeta entries={[
            ["整改结果", attempt.resolution_decision === "resolved" ? "已解决" : "仍需整改"],
            ["复验时间", localTime(attempt.resolved_at)],
          ]} />
        </section> : null}

        {job.status === "failed" || canResume || recovery.action === "integrity_review" ? <section className="fm-wf-subsection">
          <div className="fm-wf-section-head"><h3>任务处置</h3></div>
          <p className="fm-wf-observation" data-tone={recovery.action === "integrity_review" ? "red" : "amber"}>
            {recovery.action === "integrity_review" ? "检测到完整性异常，请联系管理员。"
              : canResume ? "复核记录已保存，证据封存待继续。"
              : recovery.retryable ? "处理未完成，可重新排队。" : "处理未完成，请检查运行记录。"}
          </p>
          {job.status === "failed" && recovery.retryable ? <button type="button" className="fm-button fm-button-primary" disabled={Boolean(busy)}
            onClick={retry}>{busy === "retry" ? "正在提交…" : "重新排队"}</button> : null}
          {canResume ? <button type="button" className="fm-button fm-button-primary" disabled={Boolean(busy)}
            onClick={() => review("approve", true)}>{busy === "seal" ? "正在封存…" : "继续封存"}</button> : null}
        </section> : null}
      </div>
    </div>

    {proof || report ? <section className="fm-wf-archive">
      <div className="fm-wf-section-head"><h3>报告与证据包</h3>
        {packageCheck ? <WorkbenchStatus tone={packageCheck.valid ? "green" : "red"}>{packageCheck.valid ? "完整性通过" : "完整性异常"}</WorkbenchStatus>
          : proof ? <WorkbenchStatus>待完整性校验</WorkbenchStatus> : null}
      </div>
      <div className="fm-wf-actions">
        {report ? <>
          <Link className="fm-button" to={`/reports?report=${encodeURIComponent(report.id)}`}>查看报告 <ArrowUpRight size={13} /></Link>
          <button className="fm-button" type="button" disabled={Boolean(busy)} onClick={() => void run("report", () => api.downloadReport(report.id, "html"))}><Download size={14} />下载报告</button>
          <button className="fm-button" type="button" disabled={Boolean(busy)} onClick={() => void run("report-data", () => api.downloadReport(report.id, "json"))}><Download size={14} />下载报告数据</button>
        </> : null}
        {proof ? <>
          <Link className="fm-button" to={`/traceability?archive=${encodeURIComponent(proof.id)}`}>打开交付档案 <ArrowUpRight size={13} /></Link>
          <button className="fm-button" type="button" disabled={Boolean(busy)} onClick={() => void run("archive", () => api.downloadArchive(proof.id))}><Download size={14} />下载证据包</button>
          <button className="fm-button fm-button-primary" type="button" disabled={Boolean(busy)}
            onClick={() => void run("verify", async () => {
              const result = await api.verifyProof(proof.id);
              if (mounted.current) setIntegrity({ proofId: proof.id, result });
            })}>{busy === "verify" ? "正在校验…" : "校验证据包"}</button>
        </> : null}
      </div>
      {packageCheck ? <p className="fm-wf-muted" aria-live="polite">通过 {Object.values(packageCheck.checks).filter(Boolean).length} 项 · 共 {Object.keys(packageCheck.checks).length} 项</p> : null}
    </section> : null}
    <details className="fm-wf-disclosure">
      <summary>技术详情与运行记录</summary>
      <WorkbenchMeta entries={[
        ["任务编号", job.id],
        ["证据摘要", detail.evidence.sha256],
        ["处理来源", `${provenance} · ${job.analyzer_version}`],
        ["执行次数", `${detail.dispatch.attempt_count} / ${detail.dispatch.max_attempts}`],
        ["恢复记录", recovery.last_error || job.error || "—"],
        ...(proof ? [["档案编号", proof.archive_id], ["归档摘要", proof.archive_sha256]] as [string, string][] : []),
      ]} />
      {detail.attempts.length ? <ul className="fm-wf-log">{detail.attempts.map((item) => <li key={item.id}>
        <span>执行第 {item.attempt_no} 次</span><time>{localTime(item.claimed_at)}</time>
        <span>{item.outcome ? item.outcome.error_message || (item.outcome.disposition === "committed_success" ? "处理完成" : "已结束") : "处理中"}</span>
      </li>)}</ul> : null}
      {job.result ? <details><summary>结构化记录</summary><pre>{JSON.stringify(job.result, null, 2)}</pre></details> : null}
      {packageCheck ? <details><summary>逐项校验记录</summary><pre>{JSON.stringify({ checks: packageCheck.checks, errors: packageCheck.errors }, null, 2)}</pre></details> : null}
    </details>
  </article>;
}

function VerificationUpload({ projects, initialProjectId, meta, locked, allowTrial, onClose, onUploaded }: {
  projects: Project[]; initialProjectId?: string; meta: CapabilityMeta | null; locked?: RemediationContext; allowTrial: boolean;
  onClose: () => void; onUploaded: (job: VerificationJob) => void;
}) {
  const [projectId, setProjectId] = useState(locked?.project.id
    || (projects.some((item) => item.id === initialProjectId) ? initialProjectId! : projects.length === 1 ? projects[0].id : ""));
  const [baselines, setBaselines] = useState<Baseline[]>(locked ? [locked.baseline] : []);
  const [baselineId, setBaselineId] = useState(locked?.baseline.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<{ file: File; url: string } | null>(null);
  const [analyzer, setAnalyzer] = useState<AnalyzerName | "">("");
  const [trialProject, setTrialProject] = useState<Project | null>(null);
  const [baselineLoading, setBaselineLoading] = useState(false);
  const [baselineError, setBaselineError] = useState("");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const trialCode = useRef(`WEB-${Date.now().toString(36).toUpperCase()}`);
  const active = useRef(false);

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  useEffect(() => {
    if (!file?.type.startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    setImagePreview({ file, url });
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!projectId || locked) return;
    let current = true;
    setBaselineLoading(true);
    setBaselineError("");
    setBaselines([]);
    void api.listBaselines(projectId).then((next) => {
      if (!current) return;
      setBaselines(next);
      setBaselineId((prior) => next.some((item) => item.id === prior) ? prior : next.length === 1 ? next[0].id : "");
    }).catch((reason: unknown) => {
      if (current) setBaselineError(apiMessage(reason, "设计基线读取失败"));
    }).finally(() => {
      if (current) setBaselineLoading(false);
    });
    return () => { current = false; };
  }, [locked, projectId, revision]);

  const remote = meta?.adapters.remote_http;
  const useRemote = remote?.enabled && (allowTrial || !remote.synthetic);
  const defaultAnalyzer = useRemote && !remote.synthetic ? "remote_http" : "stub";
  const availableAnalyzers = [
    ...(useRemote ? ["remote_http" as const] : []),
    ...(meta?.adapters.stub?.enabled !== false ? ["stub" as const] : []),
    ...(allowTrial && meta?.adapters.demo_fixture?.enabled ? ["demo_fixture" as const] : []),
  ];
  const selectedAnalyzer = analyzer && availableAnalyzers.includes(analyzer) ? analyzer
    : availableAnalyzers.includes(defaultAnalyzer) ? defaultAnalyzer : availableAnalyzers[0];
  const allProjects = trialProject && !projects.some((project) => project.id === trialProject.id)
    ? [...projects, trialProject] : projects;
  const selectedBaseline = baselines.find((item) => item.id === baselineId && item.project_id === projectId);

  const transact = async (name: string, action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(name);
    setError("");
    try {
      await action();
    } catch (reason) {
      if (active.current) setError(apiMessage(reason, "操作未能完成"));
    } finally {
      busyRef.current = false;
      if (active.current) setBusy("");
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!projectId || !selectedBaseline || !file || !selectedAnalyzer) return;
    if (locked && (locked.detail.case.status !== "remediation_in_progress" || locked.attempt.resolution_decision !== "pending"
      || locked.attempt.verification_job_id || locked.baseline.id !== selectedBaseline.id || locked.project.id !== projectId)) return;
    void transact("upload", async () => {
      let job: VerificationJob;
      try {
        job = await api.uploadVerification({ projectId, baselineId, file, analyzer: selectedAnalyzer, remediationAttemptId: locked?.attempt.id });
      } catch (reason) {
        if (!locked || isAuthError(reason)) throw reason;
        const fresh = await api.findingCase(locked.detail.case.id).catch(() => null);
        const bound = fresh?.attempts.find((item) => item.id === locked.attempt.id)?.verification_job_id;
        if (!bound) throw reason;
        job = (await api.verification(bound)).job;
      }
      if (active.current) onUploaded(job);
    });
  };

  const createTrialBaseline = () => void transact("trial", async () => {
    let project = trialProject || (await api.listProjects()).find((item) => item.code === trialCode.current);
    if (!project) {
      project = await api.createProject({
        code: trialCode.current, name: "现场核验工作区", location: "现场工点", manager: "网页复核员",
      });
    }
    if (!active.current) return;
    setTrialProject(project);
    const existing = await api.listBaselines(project.id);
    const baseline = existing.find((item) => item.site_id === "SITE-A12" && item.version === "design-v1")
      ?? await api.createBaseline(project.id, {
        site_id: "SITE-A12", procedure_code: "TRENCH-BEFORE-BACKFILL", version: "design-v1", source_type: "manual",
        expected: { scene_type: "trench", measurements: {
          min_depth_m: 0.8, min_spacing_m: 0.2, expected_quantity: 4, expected_specification: "PE110 x 4",
        } },
      });
    if (!active.current) return;
    setProjectId(project.id);
    setBaselineId(baseline.id);
    setRevision((value) => value + 1);
    if (availableAnalyzers.includes("demo_fixture")) setAnalyzer("demo_fixture");
  });

  return <Modal open onClose={() => { if (!busyRef.current) onClose(); }} size="md" placement="right"
    title={locked ? "提交复验资料" : "提交核验资料"}>
    <form className="fm-wf-form" onSubmit={submit}>
      {locked ? <WorkbenchMeta entries={[["所属项目", projectDisplayName(locked.project.name)], ["设计基线", `${locked.baseline.site_id} · ${locked.baseline.version}`],
        ["整改记录", `第 ${locked.attempt.attempt_no} 次`]]} /> : <>
        <label className="fm-wf-field">所属项目
          <select required className="fm-input" value={projectId} disabled={Boolean(busy)}
            onChange={(event) => { setProjectId(event.target.value); setBaselineId(""); }}>
            <option value="">选择项目</option>
            {allProjects.map((project) => <option key={project.id} value={project.id}>{projectDisplayName(project.name)}</option>)}
          </select>
        </label>
        <label className="fm-wf-field">设计基线
          <select required className="fm-input" value={baselineId} disabled={!projectId || baselineLoading || Boolean(busy)}
            onChange={(event) => setBaselineId(event.target.value)}>
            <option value="" disabled>{baselineLoading ? "正在读取基线" : projectId && !baselines.length ? "暂无可选基线" : "选择工点与版本"}</option>
            {baselines.map((item) => <option key={item.id} value={item.id}>{item.site_id} · {item.version}</option>)}
          </select>
        </label>
        {baselineError ? <p className="fm-wf-message" data-tone="amber" role="status">{baselineError} <button type="button" className="fm-wf-link"
          onClick={() => setRevision((value) => value + 1)}>重试</button></p> : null}
      </>}
      <label className="fm-wf-upload" data-selected={Boolean(file)}>
        {imagePreview && imagePreview.file === file
          ? <img key={imagePreview.url} className="fm-wf-upload-preview" src={imagePreview.url} alt="" />
          : <span className="fm-wf-upload-icon"><FileUp size={23} strokeWidth={1.6} aria-hidden="true" /></span>}
        <span className="fm-wf-upload-copy">
          {file && <span className="fm-wf-upload-selected"><Check size={12} aria-hidden="true" />已选文件</span>}
          <strong title={file?.name}>{file?.name ?? "选择现场照片或视频"}</strong>
          <small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击更换` : "选中后可预览照片并核对文件名"}</small>
        </span>
        <input required type="file" accept="image/jpeg,image/png,video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm,.mov,.avi,.mkv"
          aria-label={file ? "更换现场照片或视频" : "选择现场照片或视频"}
          disabled={Boolean(busy)} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </label>
      <label className="fm-wf-field">处理方式
        <select required className="fm-input" value={selectedAnalyzer ?? ""} disabled={Boolean(busy)}
          onChange={(event) => setAnalyzer(event.target.value as AnalyzerName)}>
          {availableAnalyzers.length ? null : <option value="">暂无可用处理方式</option>}
          {availableAnalyzers.map((item) => <option key={item} value={item}>{item === "remote_http" && remote?.synthetic ? "远程辅助处理" : analyzerLabel[item]}</option>)}
        </select>
      </label>
      {allowTrial && !locked ? <details className="fm-wf-form-disclosure">
        <summary>独立设计基线</summary>
        <button type="button" className="fm-button" disabled={Boolean(busy)} onClick={createTrialBaseline}>{busy === "trial" ? "正在准备…" : "准备独立设计基线"}</button>
      </details> : null}
      {error ? <p className="fm-wf-message" data-tone="red" role="alert">{error}</p> : null}
      {busy === "upload" && <OperationStatus detail="服务端确认收到资料后，会打开本次核验任务。">正在上传影像</OperationStatus>}
      <div className="fm-wf-actions fm-wf-form-actions">
        <button type="button" className="fm-button" disabled={Boolean(busy)} onClick={onClose}>取消</button>
        <button type="submit" className="fm-button fm-button-primary" disabled={Boolean(busy || !file || !selectedBaseline || !selectedAnalyzer)}>
          {busy === "upload" ? "正在提交…" : locked ? "提交复验任务" : "提交核验任务"}
        </button>
      </div>
    </form>
  </Modal>;
}

function VerificationSettings({ meta, includeTrial, onTrialChange, onAccessChange, onClose }: {
  meta: CapabilityMeta | null; includeTrial: boolean; onTrialChange: (value: boolean) => void;
  onAccessChange: () => void; onClose: () => void;
}) {
  const [operator, setOperator] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [health, setHealth] = useState<"loading" | "ready" | "offline">("loading");
  const [snapshot, setSnapshot] = useState<VerificationOperationsSnapshot | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setHealth("loading");
    setError("");
    void Promise.allSettled([api.health(), api.verificationDispatchOperations()]).then(([ready, operations]) => {
      if (!active) return;
      setHealth(ready.status === "fulfilled" && ready.value.status === "ready" ? "ready" : "offline");
      setSnapshot(operations.status === "fulfilled" ? operations.value : null);
      if (operations.status === "rejected") setError(apiMessage(operations.reason, "运行状态读取失败"));
    });
    return () => { active = false; };
  }, [revision]);

  const configure = (reset: boolean) => {
    const next = reset ? {
      operator: import.meta.env.VITE_OPERATOR_API_KEY || "", reviewer: import.meta.env.VITE_REVIEWER_API_KEY || "",
    } : {
      operator: operator.trim() || sessionAccess.operator, reviewer: reviewer.trim() || sessionAccess.reviewer,
    };
    sessionAccess = next;
    api.configureTokens(next);
    setOperator("");
    setReviewer("");
    setNotice(reset ? "已恢复默认访问设置。" : "本次会话访问设置已更新。");
    setRevision((value) => value + 1);
    onAccessChange();
  };

  return <Modal open onClose={onClose} title="核验运行设置" size="md" placement="right">
    <div className="fm-wf-form">
      <div className="fm-wf-section-head"><h3>服务状态</h3>
        <WorkbenchStatus tone={health === "ready" ? "green" : health === "offline" ? "red" : "neutral"}>
          {health === "ready" ? "服务就绪" : health === "offline" ? "服务暂不可用" : "读取中"}
        </WorkbenchStatus>
      </div>
      <WorkbenchMeta entries={[
        ["运行模式", meta?.verification_execution.mode === "external" ? "独立执行节点" : meta ? "服务内执行" : "—"],
        ["数据结构", meta ? (meta.database_schema.at_head && meta.database_schema.drift_free ? "已同步" : "待检查") : "—"],
        ["待调度任务", snapshot?.dispatch.unclaimed_queued_jobs],
        ["正在执行", snapshot?.dispatch.active_leases],
        ["待处置失败", snapshot?.dispatch.dead_letter_jobs],
      ]} />
      {snapshot ? <details className="fm-wf-form-disclosure">
        <summary>调度与数据状态</summary>
        <WorkbenchMeta entries={[
          ["读取时间", localTime(snapshot.generated_at)],
          ["调度状态", snapshot.status === "healthy" ? "正常" : snapshot.status === "attention" ? "待关注" : "异常"],
          ["最长排队", snapshot.dispatch.oldest_queued_seconds === null ? "—" : `${Math.round(snapshot.dispatch.oldest_queued_seconds)} 秒`],
          ["执行次数", snapshot.attempts.total],
          ["执行中记录", snapshot.attempts.open],
          ["完整性问题", snapshot.integrity.issue_count],
        ]} />
      </details> : null}
      {snapshot?.alerts.map((alert) => <p key={alert.code} className="fm-wf-message" data-tone={alert.severity === "incident" ? "red" : "amber"}>
        {alertLabels[alert.code]} · {alert.count}
      </p>)}
      {error ? <p className="fm-wf-message" data-tone="amber" role="status">{error}</p> : null}
      <button className="fm-button" type="button" disabled={health === "loading"} onClick={() => setRevision((value) => value + 1)}><RefreshCw size={14} />刷新状态</button>
      <details className="fm-wf-form-disclosure">
        <summary>高级设置</summary>
        <label className="fm-wf-check"><input type="checkbox" checked={includeTrial} onChange={(event) => onTrialChange(event.target.checked)} />显示扩展任务和入口</label>
        <form className="fm-wf-form" onSubmit={(event) => { event.preventDefault(); configure(false); }}>
          <label className="fm-wf-field">操作员访问密钥<input type="password" autoComplete="off" className="fm-input" value={operator}
            onChange={(event) => setOperator(event.target.value)} placeholder="留空时沿用当前设置" /></label>
          <label className="fm-wf-field">复核员访问密钥<input type="password" autoComplete="off" className="fm-input" value={reviewer}
            onChange={(event) => setReviewer(event.target.value)} placeholder="留空时沿用当前设置" /></label>
          <div className="fm-wf-actions">
            <button className="fm-button" type="button" onClick={() => configure(true)}>恢复默认</button>
            <button className="fm-button fm-button-primary" type="submit" disabled={!operator.trim() && !reviewer.trim()}>应用访问设置</button>
          </div>
          {notice ? <p className="fm-wf-muted" role="status">{notice}</p> : null}
        </form>
      </details>
    </div>
  </Modal>;
}

let sessionAccess = {
  operator: import.meta.env.VITE_OPERATOR_API_KEY || "",
  reviewer: import.meta.env.VITE_REVIEWER_API_KEY || "",
};
