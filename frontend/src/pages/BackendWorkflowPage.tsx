import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { AnalyzerName, api, ApiRequestError, Baseline, CapabilityMeta, FindingCaseDetail, IntegrityCheck, Project, Proof, Report, VerificationDetail, VerificationOperationsSnapshot } from "../lib/api";
import { AnalyticsIcon, BlockchainIcon, CameraIcon, CheckIcon, DatabaseIcon, DownloadIcon, InfoIcon, ShieldIcon, XIcon } from "../components/Icons";
import { EvidencePreview } from "../components/ui/EvidencePreview";
import { AnalysisTruthPanel, TruthBadge } from "../components/ui/TruthStatus";
import { cn } from "../utils/cn";
import { analysisTruthFromJob, reportTruthFromReport } from "../lib/truth";

const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  needs_review: "待人工复核",
  sealing: "报告与证据封存中",
  approved: "已复核并封装",
  rejected: "已驳回",
  failed: "处理失败",
};

const dispatchStateLabel: Record<VerificationDetail["dispatch"]["state"], string> = {
  unclaimed: "等待领取",
  leased: "租约有效",
  released: "租约已释放",
  dead_letter: "已进入死信",
};

const attemptDispositionLabel = {
  committed_success: "结果已提交",
  committed_failure: "失败已提交",
  lease_expired: "租约已过期",
  lease_lost: "Worker 丢失租约",
  write_fenced: "陈旧写入已拦截",
} satisfies Record<
  NonNullable<VerificationDetail["attempts"][number]["outcome"]>["disposition"],
  string
>;

const attemptDispositionTone = {
  committed_success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  committed_failure: "border-rose-200 bg-rose-50 text-rose-700",
  lease_expired: "border-amber-200 bg-amber-50 text-amber-700",
  lease_lost: "border-orange-200 bg-orange-50 text-orange-700",
  write_fenced: "border-slate-300 bg-slate-100 text-slate-700",
} satisfies Record<
  NonNullable<VerificationDetail["attempts"][number]["outcome"]>["disposition"],
  string
>;

const shortWorkerRef = (value: string) => (
  value.length > 40 ? `${value.slice(0, 24)}…${value.slice(-12)}` : value
);

const operationsStatusLabel = {
  healthy: "运行平稳",
  attention: "需要关注",
  incident: "完整性事故",
} satisfies Record<VerificationOperationsSnapshot["status"], string>;

const operationsStatusTone = {
  healthy: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  attention: "border-amber-300/30 bg-amber-300/10 text-amber-100",
  incident: "border-rose-300/30 bg-rose-400/10 text-rose-100",
} satisfies Record<VerificationOperationsSnapshot["status"], string>;

const operationsAlertCopy = {
  INTEGRITY_INCIDENT: {
    label: "完整性门禁已触发",
    detail: "调度或 attempt 历史存在矛盾，readyz 会保持 fail closed。",
  },
  DEAD_LETTER_PRESENT: {
    label: "存在死信任务",
    detail: "重试预算已经耗尽，需要人工检查失败原因和处置路径。",
  },
  QUEUE_WAIT_EXCEEDED: {
    label: "排队等待超阈值",
    detail: "最早的排队任务已超过当前等待告警阈值。",
  },
  RECENT_LEASE_INSTABILITY: {
    label: "近期租约不稳定",
    detail: "观察窗口内出现过期、租约丢失或 fenced write。",
  },
} satisfies Record<
  VerificationOperationsSnapshot["alerts"][number]["code"],
  { label: string; detail: string }
>;

const formatOperationalAge = (seconds: number | null) => {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
};

const workflowSteps = ["工程基线", "原始证据", "结构化处理", "人工复核", "报告生成", "哈希校验"];

export const BackendWorkflowPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const queryCaseId = searchParams.get("caseId")?.trim() || "";
  const queryAttemptId = searchParams.get("attemptId")?.trim() || "";
  const isRemediationMode = Boolean(queryCaseId || queryAttemptId);
  const [health, setHealth] = useState<"checking" | "ready" | "offline">("checking");
  const [operatorToken, setOperatorToken] = useState(import.meta.env.VITE_OPERATOR_API_KEY || "");
  const [reviewerToken, setReviewerToken] = useState(import.meta.env.VITE_REVIEWER_API_KEY || "");
  const [meta, setMeta] = useState<CapabilityMeta | null>(null);
  const [operations, setOperations] = useState<VerificationOperationsSnapshot | null>(null);
  const [operationsLoading, setOperationsLoading] = useState(false);
  const [operationsError, setOperationsError] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [analyzer, setAnalyzer] = useState<AnalyzerName>("stub");
  const [remediationAttemptId, setRemediationAttemptId] = useState("");
  const [remediationCaseDetail, setRemediationCaseDetail] = useState<FindingCaseDetail | null>(null);
  const [remediationContextLoading, setRemediationContextLoading] = useState(false);
  const [remediationContextError, setRemediationContextError] = useState<string | null>(null);
  const [remediationResolution, setRemediationResolution] = useState<"" | "resolved" | "not_resolved">("");
  const [detail, setDetail] = useState<VerificationDetail | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [proof, setProof] = useState<Proof | null>(null);
  const [integrity, setIntegrity] = useState<IntegrityCheck | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [pollFailures, setPollFailures] = useState(0);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
  const contextGenerationRef = useRef(0);
  const activeJobIdRef = useRef<string | null>(null);
  const actionSequenceRef = useRef(0);
  const activeActionRef = useRef<number | null>(null);
  const operationsRequestRef = useRef(0);

  const refreshOperations = useCallback(async () => {
    const requestId = operationsRequestRef.current + 1;
    operationsRequestRef.current = requestId;
    if (!operatorToken.trim()) {
      setOperations(null);
      setOperationsError(null);
      setOperationsLoading(false);
      return;
    }
    setOperationsLoading(true);
    setOperationsError(null);
    try {
      const snapshot = await api.verificationDispatchOperations();
      if (operationsRequestRef.current !== requestId) return;
      setOperations(snapshot);
    } catch (reason) {
      if (operationsRequestRef.current !== requestId) return;
      setOperations(null);
      if (reason instanceof ApiRequestError && (reason.status === 401 || reason.status === 403)) {
        setOperationsError("调度快照鉴权失败；已停止刷新，请检查操作员 Key。");
      } else {
        setOperationsError(reason instanceof Error ? reason.message : "调度快照读取失败");
      }
    } finally {
      if (operationsRequestRef.current === requestId) setOperationsLoading(false);
    }
  }, [operatorToken]);

  const beginAction = (name: string) => {
    if (activeActionRef.current !== null) return null;
    const token = {
      id: actionSequenceRef.current + 1,
      generation: contextGenerationRef.current,
    };
    actionSequenceRef.current = token.id;
    activeActionRef.current = token.id;
    setBusy(name);
    return token;
  };

  const actionIsCurrent = (token: { id: number; generation: number }) => (
    token.generation === contextGenerationRef.current
    && activeActionRef.current === token.id
  );

  const finishAction = (token: { id: number; generation: number }) => {
    if (!actionIsCurrent(token)) return;
    activeActionRef.current = null;
    setBusy(null);
  };

  useEffect(() => {
    Promise.all([api.health(), api.meta()])
      .then(([, capability]) => {
        setHealth("ready");
        setMeta(capability);
      })
      .catch(() => setHealth("offline"));
  }, []);

  useEffect(() => {
    api.configureTokens({ operator: operatorToken, reviewer: reviewerToken });
  }, [operatorToken, reviewerToken]);

  useEffect(() => {
    operationsRequestRef.current += 1;
    setOperations(null);
    setOperationsError(null);
    setOperationsLoading(false);
    if (!operatorToken.trim()) return;
    const timer = window.setTimeout(() => {
      void refreshOperations();
    }, 500);
    return () => window.clearTimeout(timer);
  }, [operatorToken, refreshOperations]);

  useEffect(() => {
    setPollingPaused(false);
    setPollFailures(0);
    setPollError(null);
  }, [operatorToken]);

  useLayoutEffect(() => {
    contextGenerationRef.current += 1;
    activeJobIdRef.current = null;
    activeActionRef.current = null;
    setRemediationAttemptId(queryAttemptId);
    setRemediationResolution("");
    setRemediationCaseDetail(null);
    setRemediationContextError(null);
    setRemediationContextLoading(false);
    setProject(null);
    setBaseline(null);
    setDetail(null);
    setReport(null);
    setProof(null);
    setIntegrity(null);
    setFile(null);
    setBusy(null);
    setLastRefreshAt(null);
    setPollError(null);
    setPollFailures(0);
    setPollingPaused(false);
    setError(null);
  }, [isRemediationMode, queryAttemptId, queryCaseId]);

  useEffect(() => {
    if (!isRemediationMode) return;
    if (!queryCaseId || !queryAttemptId) {
      setRemediationContextError("整改复验链接不完整，必须同时包含 caseId 与 attemptId");
      return;
    }
    if (!operatorToken.trim()) return;

    let active = true;
    const generation = contextGenerationRef.current;
    setRemediationContextLoading(true);
    setRemediationContextError(null);
    const loadRemediationContext = async () => {
      try {
        const caseDetail = await api.findingCase(queryCaseId);
        const attempt = caseDetail.attempts.find((item) => item.id === queryAttemptId);
        if (!attempt) throw new Error("Attempt 不属于当前案件，已阻止复验证据绑定");
        const [originalProject, baselines] = await Promise.all([
          api.getProject(caseDetail.case.project_id),
          api.listBaselines(caseDetail.case.project_id),
        ]);
        const originalBaseline = baselines.find((item) => item.id === caseDetail.case.baseline_id);
        if (!originalBaseline) throw new Error("原案件设计基线不存在，已阻止复验证据上传");
        let existingDetail: VerificationDetail | null = null;
        if (attempt.verification_job_id) {
          existingDetail = await api.verification(attempt.verification_job_id);
        }
        if (!active || generation !== contextGenerationRef.current) return;
        activeJobIdRef.current = existingDetail?.job.id || null;
        setProject(originalProject);
        setBaseline(originalBaseline);
        setRemediationCaseDetail(caseDetail);
        setDetail(existingDetail);
        setReport(existingDetail?.report || null);
        setProof(existingDetail?.proof || null);
        setIntegrity(null);
      } catch (reason) {
        if (!active || generation !== contextGenerationRef.current) return;
        activeJobIdRef.current = null;
        setProject(null);
        setBaseline(null);
        setRemediationCaseDetail(null);
        setDetail(null);
        setReport(null);
        setProof(null);
        setIntegrity(null);
        setRemediationContextError(reason instanceof Error ? reason.message : "原案件与 Attempt 加载失败");
      } finally {
        if (active) setRemediationContextLoading(false);
      }
    };
    void loadRemediationContext();
    return () => {
      active = false;
    };
  }, [isRemediationMode, operatorToken, queryAttemptId, queryCaseId]);

  const remediationAttempt = useMemo(
    () => remediationCaseDetail?.attempts.find((item) => item.id === queryAttemptId) || null,
    [queryAttemptId, remediationCaseDetail],
  );

  const currentContextOwnsJob = useCallback((jobId: string) => {
    if (activeJobIdRef.current !== jobId) return false;
    if (!isRemediationMode) return true;
    return (
      remediationCaseDetail?.case.id === queryCaseId
      && remediationAttempt?.id === queryAttemptId
      && remediationAttempt.verification_job_id === jobId
    );
  }, [isRemediationMode, queryAttemptId, queryCaseId, remediationAttempt, remediationCaseDetail]);

  const refreshJob = useCallback(async (jobId: string) => {
    const generation = contextGenerationRef.current;
    if (activeJobIdRef.current !== jobId) return false;
    try {
      const next = await api.verification(jobId);
      if (generation !== contextGenerationRef.current || activeJobIdRef.current !== jobId) return false;
      setDetail(next);
      setReport(next.report);
      setProof(next.proof);
      setPollFailures(0);
      setPollingPaused(false);
      setLastRefreshAt(new Date());
      setPollError(null);
      return true;
    } catch (reason) {
      if (generation !== contextGenerationRef.current || activeJobIdRef.current !== jobId) return false;
      const authFailure = reason instanceof ApiRequestError && [401, 403].includes(reason.status || 0);
      setPollFailures((count) => count + 1);
      if (authFailure) setPollingPaused(true);
      const message = reason instanceof Error ? reason.message : "任务状态查询失败";
      setPollError(authFailure ? `任务轮询已暂停：API Key 无效或权限不足。${message}` : message);
      return false;
    }
  }, []);

  useEffect(() => {
    if (!detail || pollingPaused) return;
    const analysisIsActive = ["queued", "running"].includes(detail.job.status);
    const sealingMayBeActive = detail.job.status === "sealing" && !detail.recovery.last_error;
    if (!analysisIsActive && !sealingMayBeActive) return;
    const delayMs = Math.min(900 * 2 ** pollFailures, 15_000);
    const timer = window.setTimeout(() => void refreshJob(detail.job.id), delayMs);
    return () => window.clearTimeout(timer);
  }, [detail, pollFailures, pollingPaused, refreshJob]);

  const currentStep = useMemo(() => {
    if (integrity) return 5;
    if (proof || report) return 4;
    if (detail?.job.status === "sealing") return 4;
    if (detail?.job.status === "needs_review") return 3;
    if (detail) return 2;
    if (file) return 1;
    if (baseline) return 0;
    return -1;
  }, [baseline, detail, file, integrity, proof, report]);
  const persistedTaskTruth = useMemo(() => detail ? analysisTruthFromJob(detail.job) : null, [detail]);
  const persistedReportTruth = useMemo(() => report ? reportTruthFromReport(report) : null, [report]);
  const persistedReportBoundary = useMemo(() => {
    const boundary = report?.content.truth_boundary;
    return Array.isArray(boundary)
      ? boundary.filter((item): item is string => typeof item === "string" && item.length > 0)
      : [];
  }, [report]);

  const bootstrap = async () => {
    if (activeActionRef.current !== null) return;
    if (isRemediationMode) {
      setError("整改复验模式禁止创建匿名项目，请使用链接锁定的原案件项目与设计基线");
      return;
    }
    const action = beginAction("bootstrap");
    if (!action) return;
    setError(null);
    try {
      const suffix = Date.now().toString().slice(-8);
      const createdProject = await api.createProject({
        code: `WEB-${suffix}`,
        name: "匿名化隐蔽工程联调项目",
        location: "匿名化测试工点",
        manager: "网页联调审核员",
      });
      const createdBaseline = await api.createBaseline(createdProject.id, {
        site_id: "SITE-A12",
        procedure_code: "TRENCH-BEFORE-BACKFILL",
        version: "design-v1",
        source_type: "manual",
        expected: {
          scene_type: "trench",
          measurements: {
            min_depth_m: 0.8,
            min_spacing_m: 0.2,
            expected_quantity: 4,
            expected_specification: "PE110 x 4",
          },
        },
      });
      if (!actionIsCurrent(action)) return;
      setProject(createdProject);
      setBaseline(createdBaseline);
      setDetail(null);
      setReport(null);
      setProof(null);
      setIntegrity(null);
    } catch (reason) {
      if (actionIsCurrent(action)) {
        setError(reason instanceof Error ? reason.message : "工程基线初始化失败");
      }
    } finally {
      finishAction(action);
    }
  };

  const submit = async () => {
    if (activeActionRef.current !== null) return;
    if (!project || !baseline || !file) return;
    if (remediationAttemptId && !isRemediationMode) {
      setError("整改复验必须从告警与整改页的具体 Attempt 进入，不能手工绑定到匿名项目");
      return;
    }
    if (isRemediationMode) {
      if (!remediationCaseDetail || !remediationAttempt) {
        setError("原案件或 Attempt 尚未通过校验，不能上传复验证据");
        return;
      }
      if (remediationAttempt.verification_job_id) {
        setError("该 Attempt 已绑定复验任务，不能重复上传");
        return;
      }
      if (remediationAttempt.resolution_decision !== "pending" || remediationCaseDetail.case.status !== "remediation_in_progress") {
        setError("该 Attempt 当前不处于可上传复验证据的状态");
        return;
      }
      if (project.id !== remediationCaseDetail.case.project_id || baseline.id !== remediationCaseDetail.case.baseline_id) {
        setError("当前项目或基线与原案件不一致，已阻止复验证据上传");
        return;
      }
    }
    const action = beginAction("upload");
    if (!action) return;
    setError(null);
    setPollError(null);
    setIntegrity(null);
    setRemediationResolution("");
    try {
      const job = await api.uploadVerification({
        projectId: project.id,
        baselineId: baseline.id,
        file,
        analyzer,
        remediationAttemptId: remediationAttemptId || undefined,
      });
      if (!actionIsCurrent(action)) return;
      activeJobIdRef.current = job.id;
      if (isRemediationMode) {
        setRemediationCaseDetail((current) => current ? {
          ...current,
          case: { ...current.case, status: "verification_pending" },
          attempts: current.attempts.map((item) => item.id === remediationAttemptId
            ? { ...item, verification_job_id: job.id }
            : item),
        } : current);
      }
      setDetail({
        job,
        dispatch: {
          execution_mode: meta?.verification_execution.mode || "inline",
          state: "unclaimed",
          generation: 0,
          attempt_count: 0,
          max_attempts: meta?.verification_execution.max_attempts || 3,
          heartbeat_at: null,
          lease_expires_at: null,
        },
        attempts: [],
        evidence: { id: "pending", original_name: file.name, content_type: file.type, size_bytes: file.size, sha256: "计算中", metadata: {} },
        report: null,
        proof: null,
        remediation_attempt: null,
        recovery: {
          action: "none",
          retryable: false,
          reason: "任务刚创建，等待后端返回持久化恢复状态。",
          operation_state: null,
          attempt_count: 0,
          last_error: null,
          updated_at: null,
        },
      });
      await refreshJob(job.id);
    } catch (reason) {
      if (actionIsCurrent(action)) {
        setError(reason instanceof Error ? reason.message : "证据提交失败");
      }
    } finally {
      finishAction(action);
    }
  };

  const review = async (decision: "approve" | "reject") => {
    if (activeActionRef.current !== null) return;
    const currentDetail = detail;
    if (!currentDetail) return;
    if (!currentContextOwnsJob(currentDetail.job.id)) {
      setError("任务上下文已经变化，已阻止对旧任务执行复核");
      return;
    }
    if (currentDetail.remediation_attempt && decision === "approve" && !remediationResolution) {
      setError("请先明确选择整改复验结论，再批准记录");
      return;
    }
    const action = beginAction("review");
    if (!action) return;
    setError(null);
    try {
      const outcome = await api.review(currentDetail.job.id, {
        decision,
        reviewer: "网页联调审核员",
        note: currentDetail.remediation_attempt
          ? `${analysisTruthFromJob(currentDetail.job).reviewNote}\n${decision === "approve" ? `整改复验人工判定：${remediationResolution === "resolved" ? "已解决" : "未解决"}。` : "整改复验任务被驳回，本次不执行案件关闭判定。"}`
          : analysisTruthFromJob(currentDetail.job).reviewNote,
        remediation_resolution: currentDetail.remediation_attempt && decision === "approve"
          ? remediationResolution || undefined
          : undefined,
      });
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setReport(outcome.report);
      setProof(outcome.proof);
      await refreshJob(currentDetail.job.id);
    } catch (reason) {
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setError(reason instanceof Error ? reason.message : "复核提交失败");
      await refreshJob(currentDetail.job.id);
    } finally {
      finishAction(action);
    }
  };

  const retry = async () => {
    if (activeActionRef.current !== null) return;
    const currentDetail = detail;
    if (!currentDetail || currentDetail.job.status !== "failed" || !currentDetail.recovery.retryable) return;
    if (!currentContextOwnsJob(currentDetail.job.id)) {
      setError("任务上下文已经变化，已阻止对旧任务重新排队");
      return;
    }
    const action = beginAction("retry");
    if (!action) return;
    setError(null);
    try {
      const retried = await api.retryVerification(currentDetail.job.id);
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setDetail((current) => current ? { ...current, job: retried } : current);
      await refreshJob(retried.id);
    } catch (reason) {
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setError(reason instanceof Error ? reason.message : "失败任务重新排队失败");
    } finally {
      finishAction(action);
    }
  };

  const resumeSealing = async () => {
    if (activeActionRef.current !== null) return;
    const currentDetail = detail;
    if (!currentDetail || currentDetail.recovery.action !== "resume_sealing" || !currentDetail.recovery.retryable) return;
    if (!currentContextOwnsJob(currentDetail.job.id)) {
      setError("任务上下文已经变化，已阻止继续旧任务的封存操作");
      return;
    }
    const persistedAttempt = currentDetail.remediation_attempt;
    const persistedResolution = persistedAttempt?.resolution_decision;
    if (persistedAttempt && persistedResolution !== "resolved" && persistedResolution !== "not_resolved") {
      setError("整改复验尚未持久化明确结论，不能绕过人工判断继续封存");
      return;
    }
    const action = beginAction("resume-sealing");
    if (!action) return;
    setError(null);
    try {
      const outcome = await api.review(currentDetail.job.id, {
        decision: "approve",
        reviewer: "网页封存恢复复核员",
        note: persistedAttempt?.resolution_note || "继续既有人工复核对应的可恢复封存操作。",
        remediation_resolution: persistedResolution === "resolved" || persistedResolution === "not_resolved"
          ? persistedResolution
          : undefined,
      });
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setReport(outcome.report);
      setProof(outcome.proof);
      await refreshJob(currentDetail.job.id);
    } catch (reason) {
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setError(reason instanceof Error ? reason.message : "继续封存失败");
      await refreshJob(currentDetail.job.id);
    } finally {
      finishAction(action);
    }
  };

  const verify = async () => {
    if (activeActionRef.current !== null) return;
    const currentProof = proof;
    const currentDetail = detail;
    if (!currentProof || !currentDetail || !currentContextOwnsJob(currentDetail.job.id)) return;
    const action = beginAction("verify");
    if (!action) return;
    setError(null);
    try {
      const result = await api.verifyProof(currentProof.id);
      if (
        !actionIsCurrent(action)
        || activeJobIdRef.current !== currentDetail.job.id
        || currentProof.id !== proof?.id
      ) return;
      setIntegrity(result);
    } catch (reason) {
      if (!actionIsCurrent(action) || activeJobIdRef.current !== currentDetail.job.id) return;
      setError(reason instanceof Error ? reason.message : "证据校验失败");
    } finally {
      finishAction(action);
    }
  };

  return (
    <div className="space-y-5 page-enter">
      <section className="relative overflow-hidden rounded-[30px] border border-sky-300/30 bg-[#07172b] px-6 py-7 text-white shadow-[0_26px_90px_-45px_rgba(3,105,161,0.9)]">
        <div className="absolute inset-0 opacity-60 [background-image:linear-gradient(rgba(56,189,248,.07)_1px,transparent_1px),linear-gradient(90deg,rgba(56,189,248,.07)_1px,transparent_1px)] [background-size:32px_32px]" />
        <div className="absolute -right-20 -top-28 h-72 w-72 rounded-full bg-sky-400/20 blur-3xl" />
        <div className="relative grid gap-6 xl:grid-cols-[1fr_auto] xl:items-end">
          <div className="max-w-3xl">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs font-medium text-cyan-100">
              <ShieldIcon className="h-4 w-4" /> 后端真实闭环 · Stage 2 Alpha
            </div>
            <h2 className="text-3xl font-semibold tracking-tight">每一条结论，都能回到原始证据</h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">
              本页直接调用后端：文件由服务端接收并计算 SHA-256，任务状态真实持久化，人工复核后生成结构化报告与可下载证据包，再逐项重算校验。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 rounded-3xl border border-white/10 bg-white/5 p-3 backdrop-blur sm:grid-cols-3 xl:grid-cols-6">
            {[
              ["API", health === "ready" ? "在线" : health === "offline" ? "离线" : "检查中"],
              [
                "结构",
                !meta
                  ? "检查中"
                  : meta.database_schema.mode === "create_all"
                    ? "开发直建"
                    : meta.database_schema.at_head && meta.database_schema.drift_free
                      ? "迁移已校验"
                      : "结构异常",
              ],
              ["执行", meta?.verification_execution.mode === "external" ? "独立 Worker" : "单进程演示"],
              ["调度", operations ? operationsStatusLabel[operations.status] : operationsError ? "读取失败" : "待刷新"],
              ["算法", persistedTaskTruth?.label || "尚未提交任务"],
              ["存证", "本地哈希链"],
            ].map(([label, value]) => (
              <div key={label} className="min-w-0 rounded-2xl bg-slate-950/35 px-3 py-3">
                <p className="text-[10px] uppercase tracking-[0.2em] text-sky-300">{label}</p>
                <p className="mt-1 text-sm font-semibold text-white">{value}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="grid gap-3 md:grid-cols-6">
          {workflowSteps.map((step, index) => (
            <div
              key={step}
              className={cn(
                "relative rounded-[20px] border px-3 py-4 transition-all",
                index <= currentStep ? "border-sky-200 bg-sky-50" : "border-slate-200 bg-slate-50"
              )}
            >
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-slate-400">0{index + 1}</span>
                <span className={cn("h-2.5 w-2.5 rounded-full", index <= currentStep ? "bg-sky-500" : "bg-slate-300")} />
              </div>
              <p className="mt-3 text-sm font-semibold text-slate-800">{step}</p>
            </div>
          ))}
        </div>
      </section>

      <form className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm" onSubmit={(event) => event.preventDefault()}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          <div className="flex-1">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">本地演示鉴权</p>
            <p className="mt-2 text-sm leading-6 text-slate-500">操作员只能创建和上传；复核员才能批准并封存。生产环境应改为真实登录/JWT，这里使用角色分离的 API Key。</p>
          </div>
          <label className="w-full min-w-0 flex-1 text-xs font-semibold text-slate-600 lg:min-w-64">操作员 Key
            <input type="password" autoComplete="new-password" value={operatorToken} onChange={(event) => setOperatorToken(event.target.value)} placeholder="FENGMOU_OPERATOR_API_KEY" className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-3 font-mono text-sm outline-none focus:border-sky-400" />
          </label>
          <label className="w-full min-w-0 flex-1 text-xs font-semibold text-slate-600 lg:min-w-64">复核员 Key
            <input type="password" autoComplete="new-password" value={reviewerToken} onChange={(event) => setReviewerToken(event.target.value)} placeholder="FENGMOU_REVIEWER_API_KEY" className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-3 font-mono text-sm outline-none focus:border-sky-400" />
          </label>
        </div>
      </form>

      <section
        aria-labelledby="verification-operations-title"
        className="relative overflow-hidden rounded-[28px] border border-slate-700 bg-[#0a1421] text-white shadow-[0_22px_65px_-42px_rgba(15,23,42,0.95)]"
      >
        <div className="absolute inset-0 opacity-50 [background-image:linear-gradient(rgba(148,163,184,.06)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,.06)_1px,transparent_1px)] [background-size:24px_24px]" />
        <div className="relative border-b border-white/10 px-5 py-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-2.5 text-cyan-200">
                <AnalyticsIcon className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-300">Worker operations</p>
                <h3 id="verification-operations-title" className="mt-1 text-lg font-semibold tracking-tight">调度健康 · 数据库时点快照</h3>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
                  聚合排队、租约、死信与近期 fencing 信号；业务积压只标记“需要关注”，完整性矛盾才是事故。
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {operations ? (
                <span className={cn("rounded-full border px-3 py-1 text-xs font-semibold", operationsStatusTone[operations.status])}>
                  {operationsStatusLabel[operations.status]}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => void refreshOperations()}
                disabled={!operatorToken.trim() || operationsLoading}
                className="rounded-xl border border-white/15 bg-white/5 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:border-cyan-300/40 hover:bg-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {operationsLoading ? "读取中…" : "刷新快照"}
              </button>
            </div>
          </div>
        </div>

        <div className="relative p-5">
          {!operatorToken.trim() ? (
            <div className="rounded-2xl border border-dashed border-slate-600 bg-slate-900/55 px-4 py-5 text-sm text-slate-400">
              输入操作员 Key 后才能读取聚合调度状态；该端点不会公开任务 ID 或 Worker 标识。
            </div>
          ) : null}

          {operationsError ? (
            <div role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
              {operationsError}
            </div>
          ) : null}

          {operations ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                {[
                  {
                    label: "排队任务",
                    value: operations.jobs.by_status.queued || 0,
                    note: `最久 ${formatOperationalAge(operations.dispatch.oldest_queued_seconds)} · 超阈值 ${operations.dispatch.queued_over_warning_threshold}`,
                  },
                  {
                    label: "活跃租约",
                    value: operations.dispatch.active_leases,
                    note: `最久心跳龄 ${formatOperationalAge(operations.dispatch.oldest_active_heartbeat_seconds)}`,
                  },
                  {
                    label: "死信任务",
                    value: operations.dispatch.dead_letter_jobs,
                    note: "不会被启动恢复自动复活",
                  },
                  {
                    label: "近期租约波动",
                    value: operations.attempts.recent_instability,
                    note: `${Math.round(operations.thresholds.recent_window_seconds / 60)} 分钟观察窗`,
                  },
                ].map((metric) => (
                  <div key={metric.label} className="min-w-0 border-l-2 border-cyan-300/40 bg-white/[0.045] px-4 py-3">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">{metric.label}</p>
                    <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-white">{metric.value}</p>
                    <p className="mt-1 truncate text-[11px] text-slate-400" title={metric.note}>{metric.note}</p>
                  </div>
                ))}
              </div>

              {operations.alerts.length ? (
                <div className="grid gap-2 lg:grid-cols-2">
                  {operations.alerts.map((alert) => {
                    const copy = operationsAlertCopy[alert.code];
                    return (
                      <div
                        key={alert.code}
                        className={cn(
                          "flex items-start gap-3 rounded-2xl border px-4 py-3",
                          alert.severity === "incident"
                            ? "border-rose-300/25 bg-rose-400/10"
                            : "border-amber-300/20 bg-amber-300/[0.08]",
                        )}
                      >
                        <span className={cn(
                          "mt-0.5 rounded-lg px-2 py-1 font-mono text-xs font-semibold tabular-nums",
                          alert.severity === "incident" ? "bg-rose-300/15 text-rose-100" : "bg-amber-300/10 text-amber-100",
                        )}>
                          ×{alert.count}
                        </span>
                        <div>
                          <p className={cn("text-sm font-semibold", alert.severity === "incident" ? "text-rose-100" : "text-amber-100")}>{copy.label}</p>
                          <p className="mt-1 text-xs leading-5 text-slate-400">{copy.detail}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="flex items-center gap-2 rounded-2xl border border-emerald-300/15 bg-emerald-300/[0.06] px-4 py-3 text-sm text-emerald-100">
                  <CheckIcon className="h-4 w-4" /> 当前快照没有调度告警。
                </div>
              )}

              <div className="flex flex-col gap-1 border-t border-white/10 pt-3 text-[11px] leading-5 text-slate-500 sm:flex-row sm:items-center sm:justify-between">
                <span>数据库时点快照；不是 uptime SLA、外部监控或生产就绪声明。</span>
                <span className="font-mono">
                  {new Date(operations.generated_at).toLocaleString("zh-CN", { hour12: false })} · 完整性问题 {operations.integrity.issue_count}
                </span>
              </div>
            </div>
          ) : operationsLoading ? (
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="正在读取调度快照">
              {[0, 1, 2, 3].map((item) => <div key={item} className="h-24 animate-pulse bg-white/[0.045]" />)}
            </div>
          ) : null}
        </div>
      </section>

      {error ? (
        <div role="alert" className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <XIcon className="mt-0.5 h-4 w-4" /><span>{error}</span>
        </div>
      ) : null}
      {pollError ? (
        <div role="alert" className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <InfoIcon className="mt-0.5 h-4 w-4" /><span>{pollError}</span>
        </div>
      ) : null}
      {remediationContextError ? (
        <div role="alert" className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <XIcon className="mt-0.5 h-4 w-4" /><span>{remediationContextError}。本页不会回退创建匿名项目。</span>
        </div>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="min-w-0 space-y-5">
          <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">01 / 工程语义锚点</p>
                <h3 className="mt-2 text-lg font-semibold text-slate-900">{isRemediationMode ? "锁定原案件项目与设计基线" : "创建匿名项目与设计基线"}</h3>
                <p className="mt-1 text-sm leading-6 text-slate-500">{isRemediationMode ? "整改复验必须复用原案件的项目和基线，当前页面不允许替换。" : "每个证据必须绑定项目、工点、工序和基线版本。"}</p>
              </div>
              <DatabaseIcon className="h-6 w-6 text-sky-600" />
            </div>
            {baseline && project ? (
              <div className="mt-4 space-y-3 rounded-[22px] border border-emerald-200 bg-emerald-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-emerald-700"><CheckIcon className="h-4 w-4" /> {isRemediationMode ? "原案件项目与基线已锁定" : "基线已持久化"}</div>
                <p className="text-sm text-slate-700">{project.name} · {baseline.site_id}</p>
                <p className="break-all font-mono text-[11px] text-slate-500">Project ID: {project.id}</p>
                <p className="break-all font-mono text-[11px] text-slate-500">Baseline ID: {baseline.id}</p>
                <p className="break-all font-mono text-[11px] text-slate-500">{baseline.sha256}</p>
                {remediationCaseDetail && remediationAttempt ? (
                  <div className="rounded-[18px] border border-violet-200 bg-white/80 p-3 text-violet-900">
                    <p className="text-xs font-semibold">{remediationCaseDetail.case.finding_code} · Attempt #{remediationAttempt.attempt_no}</p>
                    <p className="mt-2 text-xs leading-5 text-violet-700">{remediationAttempt.action_description}</p>
                    <p className="mt-2 break-all font-mono text-[11px] text-violet-600">Case ID: {remediationCaseDetail.case.id}</p>
                    <p className="mt-1 break-all font-mono text-[11px] text-violet-600">Attempt ID: {remediationAttempt.id}</p>
                    <p className="mt-1 text-[11px] text-violet-600">案件状态：{remediationCaseDetail.case.status} · Attempt 结论：{remediationAttempt.resolution_decision}</p>
                  </div>
                ) : null}
              </div>
            ) : isRemediationMode ? (
              <div className="mt-5 rounded-[22px] border border-violet-200 bg-violet-50 p-4 text-sm text-violet-800">
                {remediationContextLoading
                  ? "正在读取并校验原案件、项目、设计基线与 Attempt…"
                  : !operatorToken.trim()
                    ? "请输入操作员 Key，以读取并锁定原案件项目与设计基线。"
                    : "原案件上下文尚未通过校验，禁止初始化匿名项目。"}
              </div>
            ) : (
              <button disabled={busy !== null || health !== "ready" || !operatorToken.trim()} onClick={bootstrap} className="mt-5 w-full rounded-2xl bg-sky-600 px-4 py-3 text-sm font-semibold text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-slate-300">
                {busy === "bootstrap" ? "正在创建..." : "初始化匿名演示工点"}
              </button>
            )}
          </section>

          <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">02 / 原始输入</p>
                <h3 className="mt-2 text-lg font-semibold text-slate-900">选择视频或现场图片</h3>
              </div>
              <CameraIcon className="h-6 w-6 text-sky-600" />
            </div>
            <label className="mt-5 flex cursor-pointer flex-col items-center justify-center rounded-[24px] border border-dashed border-sky-300 bg-sky-50/70 px-5 py-8 text-center hover:bg-sky-50">
              <CameraIcon className="h-8 w-8 text-sky-500" />
              <span className="mt-3 text-sm font-semibold text-slate-800">{file ? file.name : "点击选择本地证据文件"}</span>
              <span className="mt-1 text-xs text-slate-500">MP4 / MOV / AVI / MKV / WebM / JPG / PNG</span>
              <input className="sr-only" type="file" accept="video/*,image/jpeg,image/png" onChange={(event) => setFile(event.target.files?.[0] || null)} />
            </label>
            {file ? <p className="mt-3 text-xs text-slate-500">{(file.size / 1024 / 1024).toFixed(2)} MB · 哈希由服务端重新计算</p> : null}

            <div className="mt-5">
              <label className="text-xs font-semibold text-slate-600" htmlFor="analyzer">处理适配器</label>
              <select id="analyzer" value={analyzer} onChange={(event) => setAnalyzer(event.target.value as AnalyzerName)} className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800 outline-none focus:border-sky-400">
                <option value="stub">安全占位器：不输出物理测量</option>
                <option value="demo_fixture" disabled={!meta?.adapters.demo_fixture?.enabled}>演示夹具：仅测试流程，非真实推理</option>
                <option value="remote_http" disabled={!meta?.adapters.remote_http?.enabled}>远程单样本推理（未评测）</option>
              </select>
              <p className="mt-2 text-xs leading-5 text-amber-700">
                {analyzer === "stub"
                  ? "当前不会生成虚假准确率或测量值。"
                  : analyzer === "demo_fixture"
                    ? "演示夹具会在报告和证据包中标记 evidence_grade=false。"
                    : "媒体只发送到部署时固定的算法端点；单样本结果仍为 evidence_grade=false、accuracy_claim=null。"}
              </p>
            </div>
            <label className="mt-4 block text-xs font-semibold text-slate-600" htmlFor="remediation-attempt-id">
              {isRemediationMode ? "整改复验 Attempt ID（已由案件页锁定）" : "整改复验 Attempt ID"}
              <input
                id="remediation-attempt-id"
                value={remediationAttemptId}
                readOnly
                placeholder="普通任务留空；整改复验请从案件页进入"
                className="mt-2 w-full cursor-not-allowed rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs text-slate-800 outline-none"
              />
            </label>
            <p className="mt-2 text-xs leading-5 text-violet-700">{isRemediationMode ? "上传前已校验 Attempt 归属，并锁定原案件项目与设计基线。" : "普通任务保持为空；整改复验必须从告警与整改页的具体 Attempt 进入。"}</p>
            <button disabled={!baseline || !file || busy !== null || !operatorToken.trim() || remediationContextLoading || (isRemediationMode && (!remediationCaseDetail || !remediationAttempt || Boolean(remediationAttempt.verification_job_id)))} onClick={submit} className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-sky-600 px-4 py-3 text-sm font-semibold text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-slate-300">
              <AnalyticsIcon className="h-4 w-4" /> {busy === "upload" ? "上传并创建任务..." : "提交真实后端任务"}
            </button>
          </section>
        </div>

        <div className="min-w-0 space-y-5">
          <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">03 / 任务与结果</p>
                <h3 className="mt-2 text-lg font-semibold text-slate-900">持久化处理状态</h3>
              </div>
              {detail ? <span className={cn("rounded-full px-3 py-1 text-xs font-semibold", detail.job.status === "failed" ? "bg-rose-100 text-rose-700" : detail.job.status === "approved" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700")}>{statusLabel[detail.job.status] || detail.job.status}</span> : null}
            </div>
            {!detail ? (
              <div className="mt-5 flex min-h-52 flex-col items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50 text-center">
                <AnalyticsIcon className="h-8 w-8 text-slate-300" />
                <p className="mt-3 text-sm font-medium text-slate-500">提交文件后显示真实任务状态与输出</p>
              </div>
            ) : (
              <div className="mt-5 space-y-4">
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <div className="flex items-center justify-between text-xs text-slate-500"><span>任务进度</span><span>{detail.job.progress}%</span></div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${detail.job.progress}%` }} /></div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <div><p className="text-xs text-slate-400">原始文件摘要</p><p className="mt-1 break-all font-mono text-[11px] text-slate-700">{detail.evidence.sha256}</p></div>
                    <div><p className="text-xs text-slate-400">持久化任务适配器</p><p className="mt-1 text-sm font-semibold text-slate-700">{detail.job.analyzer_name}</p></div>
                    <div><p className="text-xs text-slate-400">算法版本</p><p className="mt-1 text-sm font-semibold text-slate-700">{detail.job.analyzer_version}</p></div>
                    <div>
                      <p className="text-xs text-slate-400">Worker 租约 / 尝试预算</p>
                      <p className="mt-1 text-sm font-semibold text-slate-700">
                        {detail.dispatch.execution_mode === "external" ? "独立" : "内联"} · {dispatchStateLabel[detail.dispatch.state]} · {detail.dispatch.attempt_count}/{detail.dispatch.max_attempts}
                      </p>
                      {detail.dispatch.lease_expires_at ? <p className="mt-1 text-[11px] text-slate-500">租约至 {new Date(detail.dispatch.lease_expires_at).toLocaleTimeString("zh-CN", { hour12: false })}</p> : null}
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3 text-[11px] text-slate-500">
                    <span>
                      {pollingPaused
                        ? "自动轮询已暂停，请检查操作员 API Key 后手动刷新。"
                        : pollFailures > 0
                          ? `状态读取连续失败 ${pollFailures} 次，已指数退避，最长 15 秒。`
                          : lastRefreshAt
                            ? `最近成功读取：${lastRefreshAt.toLocaleTimeString("zh-CN", { hour12: false })}`
                            : "等待首次从后端读取任务状态。"}
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        setPollingPaused(false);
                        setPollFailures(0);
                        void refreshJob(detail.job.id);
                      }}
                      className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 font-semibold text-slate-700 hover:border-sky-400 hover:text-sky-700"
                    >
                      手动刷新
                    </button>
                  </div>
                </div>
                <section className="overflow-hidden rounded-[22px] border border-slate-200 bg-white" aria-labelledby="verification-attempt-history-title">
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-[#0b1728] px-4 py-3 text-white">
                    <div>
                      <p id="verification-attempt-history-title" className="text-sm font-semibold">Worker 尝试账本</p>
                      <p className="mt-1 text-[11px] leading-5 text-slate-300">每次领取与终态只追加一次；原始 Worker 标识不返回前端。</p>
                    </div>
                    <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1 font-mono text-[11px] text-cyan-100">
                      {detail.attempts.length} records
                    </span>
                  </div>
                  {detail.attempts.length ? (
                    <ol className="divide-y divide-slate-200">
                      {detail.attempts.map((attempt) => {
                        const outcome = attempt.outcome;
                        return (
                          <li key={attempt.id} className="relative px-4 py-4 sm:pl-12">
                            <span className="absolute left-4 top-5 hidden h-5 w-5 items-center justify-center rounded-full border-2 border-sky-500 bg-white font-mono text-[9px] font-bold text-sky-700 sm:flex">
                              {attempt.attempt_no}
                            </span>
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-slate-900">
                                  Attempt #{attempt.attempt_no}
                                  <span className="ml-2 font-mono text-[11px] font-normal text-slate-400">generation {attempt.generation}</span>
                                </p>
                                <p className="mt-1 text-[11px] text-slate-500">
                                  {new Date(attempt.claimed_at).toLocaleString("zh-CN", { hour12: false })} · {attempt.execution_mode === "external" ? "独立 Worker" : "内联 Worker"} · {attempt.analyzer_name}@{attempt.analyzer_version}
                                </p>
                              </div>
                              {outcome ? (
                                <span className={cn("rounded-full border px-2.5 py-1 text-[11px] font-semibold", attemptDispositionTone[outcome.disposition])}>
                                  {attemptDispositionLabel[outcome.disposition]}
                                </span>
                              ) : (
                                <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">执行中</span>
                              )}
                            </div>
                            <div className="mt-3 grid gap-2 text-[11px] text-slate-600 sm:grid-cols-2">
                              <p>
                                <span className="text-slate-400">Worker ref</span>
                                <span className="ml-2 font-mono" title={attempt.worker_ref}>{shortWorkerRef(attempt.worker_ref)}</span>
                              </p>
                              <p>
                                <span className="text-slate-400">当次预算</span>
                                <span className="ml-2 font-mono">{attempt.attempt_no}/{attempt.max_attempts}</span>
                              </p>
                              {outcome?.stage ? <p><span className="text-slate-400">终态阶段</span><span className="ml-2 font-mono">{outcome.stage}</span></p> : null}
                              {outcome ? <p><span className="text-slate-400">结束时间</span><span className="ml-2">{new Date(outcome.finished_at).toLocaleString("zh-CN", { hour12: false })}</span></p> : null}
                            </div>
                            {outcome?.result_sha256 ? (
                              <div className="mt-3 rounded-xl border border-emerald-100 bg-emerald-50/60 px-3 py-2">
                                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-700">Result SHA-256</p>
                                <p className="mt-1 break-all font-mono text-[11px] text-emerald-900">{outcome.result_sha256}</p>
                              </div>
                            ) : null}
                            {outcome?.error_code ? (
                              <div className="mt-3 rounded-xl border border-rose-100 bg-rose-50/70 px-3 py-2 text-[11px] text-rose-800">
                                <p className="font-mono font-semibold">{outcome.error_code} · {outcome.error_retryable ? "可重试" : "不可重试"}{outcome.dead_lettered ? " · 已进入死信" : ""}</p>
                                {outcome.error_message ? <p className="mt-1 break-words leading-5">{outcome.error_message}</p> : null}
                              </div>
                            ) : null}
                            <details className="mt-3 text-[11px] text-slate-500">
                              <summary className="cursor-pointer font-semibold text-slate-600 hover:text-sky-700">查看不可变输入摘要</summary>
                              <div className="mt-2 space-y-1 rounded-xl bg-slate-50 px-3 py-2 font-mono">
                                <p className="break-all">evidence {attempt.evidence_sha256}</p>
                                <p className="break-all">baseline {attempt.baseline_sha256}</p>
                                <p className="break-all">attempt {attempt.id}</p>
                              </div>
                            </details>
                          </li>
                        );
                      })}
                    </ol>
                  ) : (
                    <div className="px-4 py-5 text-sm text-slate-500">
                      任务尚未被 Worker 领取，因此还没有尝试记录。
                    </div>
                  )}
                </section>
                {detail.job.result ? <AnalysisTruthPanel job={detail.job} /> : null}
                {detail.job.status === "failed" ? (
                  <div className="rounded-[22px] border border-rose-200 bg-rose-50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-rose-800"><XIcon className="h-4 w-4" /> 分析任务失败</div>
                    <p className="mt-2 break-words text-xs leading-5 text-rose-700">{detail.job.error || "服务端未提供失败原因。"}</p>
                    <p className="mt-2 text-xs leading-5 text-rose-600">{detail.recovery.reason} {detail.dispatch.state === "dead_letter" ? "该任务不会被启动恢复再次执行。" : "重试会复用原始证据、设计基线、算法版本和稳定幂等键。"}</p>
                    <button type="button" disabled={busy !== null || !operatorToken.trim() || !detail.recovery.retryable} onClick={() => void retry()} className="mt-3 rounded-2xl bg-rose-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-rose-800 disabled:cursor-not-allowed disabled:bg-slate-300">{busy === "retry" ? "正在重新排队…" : detail.recovery.retryable ? "检查原因后显式重试" : "当前配置不允许重试"}</button>
                  </div>
                ) : null}
                {detail.recovery.action === "resume_sealing" ? (
                  <div className="rounded-[22px] border border-amber-300 bg-amber-50 p-4" role="status">
                    <div className="flex items-center gap-2 text-sm font-semibold text-amber-900"><InfoIcon className="h-4 w-4" /> 封存尚未完成，当前没有可交付的新报告或证据包</div>
                    <p className="mt-2 text-xs leading-5 text-amber-800">后端持久态：{detail.recovery.operation_state || "unknown"} · 已尝试 {detail.recovery.attempt_count} 次。{detail.recovery.reason}</p>
                    {detail.recovery.last_error ? <p className="mt-2 break-words rounded-xl bg-white/70 px-3 py-2 font-mono text-[11px] leading-5 text-amber-900">{detail.recovery.last_error}</p> : null}
                    <p className="mt-2 text-xs leading-5 text-amber-700">继续操作会复用已冻结的复核、报告 ID、档案 ID 与账本记录；不会重新生成一套业务结论。</p>
                    <button type="button" disabled={busy !== null || !reviewerToken.trim()} onClick={() => void resumeSealing()} className="mt-3 rounded-2xl bg-amber-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:bg-slate-300">{busy === "resume-sealing" ? "正在继续封存…" : "复核员继续封存"}</button>
                  </div>
                ) : null}
                {detail.recovery.action === "integrity_review" ? (
                  <div className="rounded-[22px] border border-rose-300 bg-rose-50 p-4" role="alert">
                    <div className="flex items-center gap-2 text-sm font-semibold text-rose-900"><XIcon className="h-4 w-4" /> 完整性异常，禁止自动继续</div>
                    <p className="mt-2 text-xs leading-5 text-rose-800">{detail.recovery.reason}</p>
                    {detail.recovery.last_error ? <p className="mt-2 break-words rounded-xl bg-white/70 px-3 py-2 font-mono text-[11px] leading-5 text-rose-900">{detail.recovery.last_error}</p> : null}
                    <p className="mt-2 text-xs leading-5 text-rose-700">请先检查 readyz、SealOperation、报告/ZIP 摘要和 ledger；本页不会把完整性故障当成普通网络失败重试。</p>
                  </div>
                ) : null}
                {detail.evidence.id !== "pending" ? (
                  <EvidencePreview
                    key={detail.evidence.id}
                    evidenceId={detail.evidence.id}
                    originalName={detail.evidence.original_name}
                    sha256={detail.evidence.sha256}
                    registeredSizeBytes={detail.evidence.size_bytes}
                    autoLoad
                  />
                ) : null}
                {detail.job.result ? (
                  <details className="rounded-[22px] bg-[#081525] text-cyan-100">
                    <summary className="cursor-pointer px-4 py-3 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300">查看完整结构化输出</summary>
                    <pre className="max-h-80 overflow-auto border-t border-white/10 p-4 text-[11px] leading-5">{JSON.stringify(detail.job.result, null, 2)}</pre>
                  </details>
                ) : null}
                {detail.job.status === "needs_review" ? (
                  <div className="rounded-[22px] border border-amber-200 bg-amber-50 p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold text-amber-800"><InfoIcon className="h-4 w-4" /> 人工复核是封装前的强制门</div>
                    <p className="mt-2 text-xs leading-5 text-amber-700">{persistedTaskTruth?.description}</p>
                    {detail.remediation_attempt ? (
                      <label className="mt-4 block text-xs font-semibold text-amber-900">
                        整改复验结论
                        <select
                          value={remediationResolution}
                          onChange={(event) => setRemediationResolution(event.target.value as "" | "resolved" | "not_resolved")}
                          className="mt-2 w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-800 outline-none focus:border-amber-400"
                        >
                          <option value="" disabled>请选择复验结论</option>
                          <option value="resolved">复验证据支持“已解决”</option>
                          <option value="not_resolved">复验证据不支持关闭，继续整改</option>
                        </select>
                        <span className="mt-2 block font-normal leading-5 text-amber-700">关闭动作会绑定本次新报告和新证据包，不修改原始案件报告。</span>
                      </label>
                    ) : null}
                    <div className="mt-4 flex flex-col gap-3 sm:flex-row">
                      <button disabled={busy !== null || !reviewerToken.trim() || Boolean(detail.remediation_attempt && !remediationResolution)} onClick={() => review("approve")} className="w-full rounded-2xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300 sm:flex-1">{detail.remediation_attempt ? remediationResolution === "resolved" ? "批准、生成证据包并关闭案件" : remediationResolution === "not_resolved" ? "批准证据并继续整改" : "请先选择复验结论" : "批准记录并生成证据包"}</button>
                      <button disabled={busy !== null || !reviewerToken.trim()} onClick={() => review("reject")} className="w-full rounded-2xl border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:text-slate-400 sm:w-auto">驳回</button>
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </section>

          {proof && report ? (
            <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-600">04 / 可信交付</p><h3 className="mt-2 text-lg font-semibold text-slate-900">报告、证据包与严格核验</h3></div>
                <BlockchainIcon className="h-6 w-6 text-sky-600" />
              </div>
              {persistedReportTruth ? <div className="mt-4"><TruthBadge truth={persistedReportTruth} /><p className="mt-2 text-xs leading-5 text-slate-500">{persistedReportTruth.description}</p></div> : null}
              {persistedReportBoundary.length ? (
                <div className="mt-4 rounded-[20px] border border-amber-200 bg-amber-50 p-4" aria-label="报告内持久化真实性边界">
                  <p className="text-xs font-semibold text-amber-900">报告内持久化真实性边界</p>
                  <ul className="mt-2 space-y-1.5 text-xs leading-5 text-amber-800">
                    {persistedReportBoundary.map((item, index) => <li key={`${index}-${item}`}>· {item}</li>)}
                  </ul>
                </div>
              ) : null}
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4"><p className="text-xs text-slate-400">档案编号</p><p className="mt-2 break-all text-sm font-semibold text-slate-800">{proof.archive_id}</p></div>
                <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4"><p className="text-xs text-slate-400">证据等级 / 用途</p><p className="mt-2 text-sm font-semibold text-slate-800">{proof.evidence_grade ? "正式证据" : "非正式证据"} · {proof.purpose}</p></div>
                <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4 sm:col-span-2"><p className="text-xs text-slate-400">证据包 SHA-256</p><p className="mt-2 break-all font-mono text-[11px] text-slate-700">{proof.archive_sha256}</p></div>
                <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4 sm:col-span-2"><p className="text-xs text-slate-400">Merkle Root</p><p className="mt-2 break-all font-mono text-[11px] text-slate-700">{proof.merkle_root}</p></div>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                <button onClick={() => void api.downloadReport(report.id, "json").catch((reason) => setError(reason instanceof Error ? reason.message : "报告下载失败"))} className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 px-3 py-3 text-sm font-semibold text-slate-700 hover:border-sky-300 hover:text-sky-700"><DownloadIcon className="h-4 w-4" /> JSON 报告</button>
                <button onClick={() => void api.downloadArchive(proof.id).catch((reason) => setError(reason instanceof Error ? reason.message : "证据包下载失败"))} className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 px-3 py-3 text-sm font-semibold text-slate-700 hover:border-sky-300 hover:text-sky-700"><DownloadIcon className="h-4 w-4" /> 证据包</button>
                <button disabled={busy !== null} onClick={verify} className="inline-flex items-center justify-center gap-2 rounded-2xl bg-sky-600 px-3 py-3 text-sm font-semibold text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-slate-300"><ShieldIcon className="h-4 w-4" /> 重新校验</button>
              </div>
              {integrity ? (
                <div className={cn("mt-4 rounded-[22px] border p-4", integrity.valid ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50")}>
                  <div className={cn("flex items-center gap-2 text-sm font-semibold", integrity.valid ? "text-emerald-700" : "text-rose-700")}>
                    {integrity.valid ? <CheckIcon className="h-4 w-4" /> : <XIcon className="h-4 w-4" />}
                    {integrity.valid ? "逐项摘要一致，当前证据包未检测到篡改" : "校验失败，证据包已被修改或链路不完整"}
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{Object.entries(integrity.checks).map(([key, valid]) => <div key={key} className="rounded-xl bg-white/70 px-3 py-2 text-[11px] text-slate-600">{valid ? "✓" : "×"} {key}</div>)}</div>
                </div>
              ) : null}
            </section>
          ) : null}
        </div>
      </div>

      <section className="rounded-[28px] border border-amber-200 bg-amber-50 p-5">
        <div className="flex items-start gap-3">
          <InfoIcon className="mt-0.5 h-5 w-5 text-amber-700" />
          <div><h3 className="text-sm font-semibold text-amber-900">当前真实能力边界</h3><p className="mt-2 text-sm leading-6 text-amber-800">这是批处理 MVP，不是直播流实时监管；设计基线由人工绑定，不是自动空间配准；本地哈希链可检出篡改，但不是区块链、司法存证或可信时间戳。竞赛 85%/90% 指标尚未验证。</p></div>
        </div>
      </section>
    </div>
  );
};
