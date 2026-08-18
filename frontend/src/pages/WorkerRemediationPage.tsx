import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CalendarClock,
  Camera,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FilePlus2,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  Upload,
  Wrench,
} from "lucide-react";
import {
  AnalyzerName,
  ApiRequestError,
  FindingCase,
  FindingCaseDetail,
  api,
} from "../lib/api";
import { useWorkerIdentity } from "../lib/workerIdentity";
import { classifyWorkerError, matchesWorkerAssignment } from "../lib/workerDomain";
import { cn } from "../utils/cn";
import { EvidencePreview } from "../components/ui/EvidencePreview";

const MAX_EVIDENCE_BYTES = 500 * 1024 * 1024;

const STATUS_LABEL: Record<string, string> = {
  pending_triage: "待分诊",
  open: "待安排整改",
  remediation_in_progress: "整改中",
  verification_pending: "复验中",
  closed: "已关闭",
  dismissed: "已撤销",
};

const STATUS_TONE: Record<string, string> = {
  pending_triage: "border-slate-200 bg-slate-50 text-slate-800",
  open: "border-amber-200 bg-amber-50 text-amber-900",
  remediation_in_progress: "border-rose-200 bg-rose-50 text-rose-900",
  verification_pending: "border-sky-200 bg-sky-50 text-sky-900",
  closed: "border-emerald-200 bg-emerald-50 text-emerald-900",
  dismissed: "border-slate-200 bg-slate-100 text-slate-700",
};

type PendingAttempt = {
  caseId: string;
  clientRequestId: string;
  attemptId: string | null;
  expectedVersion: number;
  description: string;
};

function pendingKey(caseId: string): string {
  return `fengmou.worker-remediation-attempt.v1.${caseId}`;
}

function readPending(caseId: string): PendingAttempt | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(pendingKey(caseId)) || "null") as PendingAttempt | null;
    return value?.caseId === caseId && typeof value.clientRequestId === "string" ? value : null;
  } catch {
    return null;
  }
}

function newRequestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `attempt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function nextAction(item: FindingCase): string {
  if (item.status === "remediation_in_progress") return "提交新的整改说明与现场媒体";
  if (item.status === "verification_pending") return "等待复验结果";
  if (item.status === "open") return "等待管理人员下达整改要求";
  if (item.status === "closed") return "无需继续操作";
  return "等待管理人员处理";
}

export const WorkerRemediationPage: React.FC = () => {
  const { profile } = useWorkerIdentity();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [cases, setCases] = useState<FindingCase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FindingCaseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [message, setMessage] = useState<{ tone: "info" | "success" | "error"; text: string } | null>(null);
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [analyzer, setAnalyzer] = useState<AnalyzerName>("demo_fixture");
  const [pendingAttempt, setPendingAttempt] = useState<PendingAttempt | null>(null);

  const loadCases = useCallback(async () => {
    if (!profile.id) return;
    setLoading(true);
    setMessage(null);
    try {
      const rows = await api.listFindingCases({ scope: "operational" });
      const assigned = rows.filter((item) => matchesWorkerAssignment(item.assigned_to, profile.id, profile.teams));
      setCases(assigned);
      setSelectedId((current) => current && assigned.some((item) => item.id === current)
        ? current
        : assigned.find((item) => item.status === "remediation_in_progress")?.id || assigned[0]?.id || null);
      window.localStorage.setItem(`fengmou.worker-remediation-list.v1.${profile.id}`, JSON.stringify({ rows: assigned, syncedAt: new Date().toISOString() }));
    } catch (reason) {
      try {
        const cached = JSON.parse(window.localStorage.getItem(`fengmou.worker-remediation-list.v1.${profile.id}`) || "null") as { rows?: FindingCase[] } | null;
        if (cached?.rows) {
          setCases(cached.rows);
          setSelectedId((current) => current || cached.rows?.[0]?.id || null);
          setMessage({ tone: "info", text: "网络不可用，当前显示上次同步的整改任务。" });
        } else {
          throw reason;
        }
      } catch {
        const error = classifyWorkerError(reason);
        setMessage({ tone: "error", text: `${error.title}：${error.message}` });
      }
    } finally {
      setLoading(false);
    }
  }, [profile.id, profile.teams]);

  const loadDetail = useCallback(async (caseId: string) => {
    setDetailLoading(true);
    try {
      const next = await api.findingCase(caseId);
      if (!matchesWorkerAssignment(next.case.assigned_to, profile.id, profile.teams)) {
        setDetail(null);
        setMessage({ tone: "error", text: "权限受限：该整改任务未分配给当前人员或所属班组。" });
        return;
      }
      setDetail(next);
      const pending = readPending(caseId);
      setPendingAttempt(pending);
      setDescription(pending?.description || "");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (reason) {
      const error = classifyWorkerError(reason);
      setMessage({ tone: "error", text: `${error.title}：${error.message}` });
    } finally {
      setDetailLoading(false);
    }
  }, [profile.id, profile.teams]);

  useEffect(() => { void loadCases(); }, [loadCases]);
  useEffect(() => { if (selectedId) void loadDetail(selectedId); else setDetail(null); }, [loadDetail, selectedId]);

  useEffect(() => {
    let active = true;
    api.meta().then((meta) => {
      if (!active) return;
      const enabled = Object.entries(meta.adapters).filter(([, value]) => value.enabled);
      const selected = enabled.find(([name, value]) => name === "remote_http" && !value.synthetic)
        || enabled.find(([name]) => name === "demo_fixture")
        || enabled.find(([name]) => name === "stub");
      if (selected) setAnalyzer(selected[0] as AnalyzerName);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  const selected = cases.find((item) => item.id === selectedId) || null;
  const overdue = selected?.due_at ? Date.parse(selected.due_at) < Date.now() && !["closed", "dismissed"].includes(selected.status) : false;
  const canSubmit = selected?.status === "remediation_in_progress";

  const chooseFile = (next: File | null) => {
    if (!next) return;
    if (next.size > MAX_EVIDENCE_BYTES) {
      setFile(null);
      setMessage({ tone: "error", text: "文件超过 500 MB 上限，请压缩后重试。" });
      return;
    }
    if (!/^(image\/(jpeg|png)|video\/(mp4|webm))$/i.test(next.type)) {
      setFile(null);
      setMessage({ tone: "error", text: "仅支持 JPG、PNG、MP4 或 WebM 文件。" });
      return;
    }
    setFile(next);
    setMessage(null);
  };

  const submitAttempt = async () => {
    if (!detail || !selected || !canSubmit) return;
    if (!description.trim() || !file) {
      setMessage({ tone: "error", text: "请填写整改说明并选择新的现场照片或视频。" });
      return;
    }
    if (!navigator.onLine) {
      setMessage({ tone: "error", text: "当前处于离线状态。请保留原文件，联网后再提交整改记录。" });
      return;
    }
    setSubmitting(true);
    setMessage({ tone: "info", text: "正在创建追加整改记录…" });
    let current = pendingAttempt;
    try {
      if (!current) {
        current = {
          caseId: selected.id,
          clientRequestId: newRequestId(),
          attemptId: null,
          expectedVersion: selected.version,
          description: description.trim(),
        };
        window.localStorage.setItem(pendingKey(selected.id), JSON.stringify(current));
        setPendingAttempt(current);
      }
      if (!current.attemptId) {
        const attempt = await api.createRemediationAttempt(selected.id, {
          client_request_id: current.clientRequestId,
          expected_version: current.expectedVersion,
          action_description: current.description,
        });
        current = { ...current, attemptId: attempt.id };
        window.localStorage.setItem(pendingKey(selected.id), JSON.stringify(current));
        setPendingAttempt(current);
      }
      setMessage({ tone: "info", text: "追加记录已创建，正在上传新的整改证据…" });
      await api.uploadVerification({
        projectId: selected.project_id,
        baselineId: selected.baseline_id,
        file,
        analyzer,
        remediationAttemptId: current.attemptId || undefined,
        deviceId: navigator.userAgent.slice(0, 160),
        metadata: {
          source: "worker-remediation",
          worker_id: profile.id,
          local_created_at: new Date().toISOString(),
          action_description: current.description,
        },
      });
      window.localStorage.removeItem(pendingKey(selected.id));
      setPendingAttempt(null);
      setDescription("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setMessage({ tone: "success", text: "整改资料已追加提交，正在等待复验。原证据和历史尝试均保持不变。" });
      await Promise.all([loadCases(), loadDetail(selected.id)]);
    } catch (reason) {
      const error = classifyWorkerError(reason);
      const conflict = reason instanceof ApiRequestError && reason.status === 409;
      setMessage({
        tone: "error",
        text: conflict
          ? "任务版本已变化或存在重复提交，请刷新后核对最新整改记录。已创建的尝试编号会保留。"
          : `${error.title}：${error.message}${current?.attemptId ? " 已创建的追加记录会保留，可重新选择文件继续上传。" : ""}`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (!profile.id) {
    return <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">请先在“我的账号”中设置人员编号。</div>;
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-3 border-b border-slate-200 pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold">分配给我的整改任务</h2>
          <p className="mt-1 text-sm text-slate-600">每次反馈都会新增一条整改尝试，不覆盖原始证据。</p>
        </div>
        <button type="button" onClick={() => void loadCases()} disabled={loading} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /> 刷新任务</button>
      </section>

      {message ? (
        <div className={cn(
          "rounded-lg border px-4 py-3 text-sm",
          message.tone === "info" && "border-sky-200 bg-sky-50 text-sky-900",
          message.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-900",
          message.tone === "error" && "border-rose-200 bg-rose-50 text-rose-900",
        )} role={message.tone === "error" ? "alert" : "status"}>{message.text}</div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <section aria-label="整改任务列表">
          {cases.length ? (
            <div className="space-y-2">
              {cases.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className={cn(
                    "w-full rounded-lg border bg-white p-3 text-left shadow-sm",
                    selectedId === item.id ? "border-slate-800 ring-2 ring-slate-200" : "border-slate-200",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className={cn("rounded-full border px-2 py-1 text-xs font-semibold", STATUS_TONE[item.status] || STATUS_TONE.open)}>{STATUS_LABEL[item.status] || item.status}</span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                  </div>
                  <p className="mt-3 line-clamp-2 text-sm font-semibold leading-5">{item.finding_message}</p>
                  <p className="mt-2 text-xs text-slate-500">下一步：{nextAction(item)}</p>
                  {item.due_at ? <p className={cn("mt-2 flex items-center gap-1 text-xs", Date.parse(item.due_at) < Date.now() ? "font-semibold text-rose-700" : "text-slate-500")}><CalendarClock className="h-3.5 w-3.5" /> {new Date(item.due_at).toLocaleString("zh-CN")}</p> : null}
                </button>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-10 text-center text-sm text-slate-500">暂无分配给你的整改任务</div>
          )}
        </section>

        <section>
          {detailLoading ? (
            <div className="flex items-center gap-2 py-10 text-sm text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin" /> 正在加载整改详情…</div>
          ) : detail && selected ? (
            <div className="space-y-6">
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <span className={cn("inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold", STATUS_TONE[selected.status] || STATUS_TONE.open)}>{STATUS_LABEL[selected.status] || selected.status}</span>
                    <h3 className="mt-3 text-lg font-semibold">整改要求</h3>
                  </div>
                  {overdue ? <span className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-800"><Clock3 className="h-3.5 w-3.5" /> 已逾期</span> : null}
                </div>
                <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
                  <div className="sm:col-span-2"><dt className="text-xs text-slate-500">问题描述</dt><dd className="mt-1 leading-6 text-slate-900">{selected.finding_message}</dd></div>
                  <div className="sm:col-span-2"><dt className="text-xs text-slate-500">处理要求</dt><dd className="mt-1 leading-6 text-slate-900">{selected.decision_reason || "请按问题描述完成整改，并提交新的现场媒体和整改说明。"}</dd></div>
                  <div><dt className="text-xs text-slate-500">负责人</dt><dd className="mt-1 font-medium">{selected.assigned_to || "未分配"}</dd></div>
                  <div><dt className="text-xs text-slate-500">截止时间</dt><dd className={cn("mt-1 font-medium", overdue && "text-rose-700")}>{selected.due_at ? new Date(selected.due_at).toLocaleString("zh-CN") : "未设置"}</dd></div>
                </dl>
              </div>

              <div>
                <h3 className="mb-3 text-base font-semibold">原始问题证据</h3>
                <EvidencePreview evidenceId={selected.source_evidence_id} />
              </div>

              <div className="border-t border-slate-200 pt-5">
                <div className="flex items-center justify-between gap-3">
                  <div><h3 className="text-base font-semibold">整改历史</h3><p className="mt-1 text-xs text-slate-500">共 {detail.attempts.length} 次追加尝试</p></div>
                  <Wrench className="h-5 w-5 text-slate-500" />
                </div>
                {detail.attempts.length ? (
                  <ol className="mt-3 space-y-2">
                    {detail.attempts.map((attempt) => (
                      <li key={attempt.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-semibold">第 {attempt.attempt_no} 次整改</p>
                          <span className={cn(
                            "rounded-full border px-2 py-1 text-xs font-semibold",
                            attempt.resolution_decision === "resolved" && "border-emerald-200 bg-emerald-50 text-emerald-900",
                            attempt.resolution_decision === "not_resolved" && "border-rose-200 bg-rose-50 text-rose-900",
                            attempt.resolution_decision === "pending" && "border-sky-200 bg-sky-50 text-sky-900",
                          )}>{attempt.resolution_decision === "resolved" ? "复验通过" : attempt.resolution_decision === "not_resolved" ? "复验未通过" : "等待复验"}</span>
                        </div>
                        <p className="mt-2 leading-6 text-slate-700">{attempt.action_description}</p>
                        {attempt.resolution_note ? <p className="mt-2 text-xs text-rose-700">复验说明：{attempt.resolution_note}</p> : null}
                        <p className="mt-2 text-xs text-slate-500">提交时间 {new Date(attempt.submitted_at).toLocaleString("zh-CN")}</p>
                      </li>
                    ))}
                  </ol>
                ) : <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-500">尚未提交整改反馈</p>}
              </div>

              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <div className="flex items-start gap-3"><FilePlus2 className="mt-0.5 h-5 w-5 shrink-0 text-slate-600" /><div><h3 className="text-base font-semibold">追加整改反馈</h3><p className="mt-1 text-xs leading-5 text-slate-500">新说明和媒体会形成下一次整改尝试，历史内容不可修改。</p></div></div>
                {!canSubmit ? (
                  <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {nextAction(selected)}</div>
                ) : (
                  <>
                    {pendingAttempt?.attemptId ? <div className="mt-4 flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> 追加记录已创建，重新选择文件即可继续上传，不会创建新的尝试。</div> : null}
                    <label className="mt-4 block"><span className="text-sm font-medium">整改说明</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} disabled={Boolean(pendingAttempt?.attemptId)} maxLength={1000} rows={4} placeholder="说明已采取的整改措施、现场变化和复验要点" className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100" /></label>
                    <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,video/mp4,video/webm" capture="environment" className="sr-only" onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
                    <button type="button" onClick={() => fileInputRef.current?.click()} className="mt-4 flex min-h-20 w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-400 bg-slate-50 px-4 text-sm font-semibold text-slate-800"><Camera className="h-5 w-5" /> {file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB` : "拍照或选择新的整改媒体"}</button>
                    <button type="button" onClick={() => void submitAttempt()} disabled={submitting || !description.trim() || !file} className="mt-4 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-lg bg-[#f2c94c] px-5 text-sm font-bold text-slate-950 hover:bg-[#e8bd35] disabled:opacity-50 sm:w-auto">{submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} {pendingAttempt?.attemptId ? "继续上传整改证据" : "提交新的整改记录"}</button>
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 bg-white px-5 py-12 text-center text-sm text-slate-500"><ShieldAlert className="mx-auto h-6 w-6" /><p className="mt-2">请选择一条整改任务</p></div>
          )}
        </section>
      </div>
    </div>
  );
};

