import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { BellIcon, CheckIcon, EyeIcon, FilterIcon, SearchIcon, ShieldIcon } from "../components/Icons";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";
import {
  api,
  FindingCase,
  FindingCaseDetail,
  FindingCaseStatus,
  FindingCaseSummary,
  Project,
} from "../lib/api";
import { cn } from "../utils/cn";

const statusLabel: Record<FindingCaseStatus, string> = {
  pending_triage: "待人工分诊",
  open: "已确认 · 待整改",
  remediation_in_progress: "整改中",
  verification_pending: "复验处理中",
  closed: "已闭环",
  dismissed: "已排除",
};

const severityLabel: Record<string, string> = {
  info: "信息",
  warning: "警告",
  error: "严重",
  critical: "紧急",
};

const severityTone = (severity: string) =>
  severity === "critical"
    ? "border-rose-200 bg-rose-100 text-rose-800"
    : severity === "error"
      ? "border-orange-200 bg-orange-100 text-orange-800"
      : severity === "warning"
        ? "border-amber-200 bg-amber-100 text-amber-800"
        : "border-sky-200 bg-sky-100 text-sky-800";

const statusTone = (status: FindingCaseStatus) =>
  status === "closed"
    ? "bg-emerald-100 text-emerald-800"
    : status === "dismissed"
      ? "bg-slate-100 text-slate-600"
      : status === "verification_pending"
        ? "bg-violet-100 text-violet-800"
        : status === "remediation_in_progress"
          ? "bg-blue-100 text-blue-800"
          : "bg-amber-100 text-amber-800";

const truthDescriptor = (item: FindingCase) => {
  if (item.scope === "demo") {
    return { label: "合成演示案件 · 非现场事实", tone: "border-violet-200 bg-violet-50 text-violet-800" };
  }
  if (item.status === "dismissed") {
    return { label: "人工排除候选 · 保留审计记录", tone: "border-slate-200 bg-slate-50 text-slate-700" };
  }
  if (item.analysis_mode === "remote_http") {
    return { label: "远程单样本候选 · 未评测", tone: "border-amber-200 bg-amber-50 text-amber-800" };
  }
  if (item.status === "pending_triage") {
    return { label: "算法候选观察 · 尚非运营告警", tone: "border-slate-200 bg-slate-50 text-slate-700" };
  }
  return { label: "人工确认运营案件 · 非模型指标", tone: "border-sky-200 bg-sky-50 text-sky-800" };
};

const formatTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—";

export const AlarmsPage: React.FC = () => {
  const [cases, setCases] = useState<FindingCase[]>([]);
  const [summary, setSummary] = useState<FindingCaseSummary | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<FindingCaseDetail | null>(null);
  const [keyword, setKeyword] = useState("");
  const [scope, setScope] = useState<"all" | "operational" | "demo">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | FindingCaseStatus>("all");
  const [showFilters, setShowFilters] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [reason, setReason] = useState("");
  const [confirmedSeverity, setConfirmedSeverity] = useState<"info" | "warning" | "error" | "critical">("warning");
  const [assignee, setAssignee] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [remediationPlan, setRemediationPlan] = useState("");
  const [attemptDescription, setAttemptDescription] = useState("");

  const projectNames = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  );

  const refresh = useCallback(async (selectedId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const [nextCases, nextSummary, nextProjects] = await Promise.all([
        api.listFindingCases(),
        api.findingCaseSummary(),
        api.listProjects(),
      ]);
      setCases(nextCases);
      setSummary(nextSummary);
      setProjects(nextProjects);
      if (selectedId) setSelected(await api.findingCase(selectedId));
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "案件数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const visible = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLocaleLowerCase();
    return cases.filter((item) => {
      if (scope !== "all" && item.scope !== scope) return false;
      if (statusFilter !== "all" && item.status !== statusFilter) return false;
      if (!normalizedKeyword) return true;
      const searchable = [
        item.finding_code,
        item.finding_message,
        item.assigned_to || "",
        projectNames.get(item.project_id) || item.project_id,
      ].join(" ").toLocaleLowerCase();
      return searchable.includes(normalizedKeyword);
    });
  }, [cases, keyword, projectNames, scope, statusFilter]);

  const openDetail = async (caseId: string) => {
    setBusy(`detail:${caseId}`);
    setError(null);
    try {
      const detail = await api.findingCase(caseId);
      setSelected(detail);
      setReason("");
      setAssignee(detail.case.assigned_to || "");
      setDueAt(detail.case.due_at ? detail.case.due_at.slice(0, 16) : "");
      setRemediationPlan("");
      setAttemptDescription("");
      setConfirmedSeverity(detail.case.confirmed_severity || detail.case.proposed_severity);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "案件详情加载失败");
    } finally {
      setBusy(null);
    }
  };

  const triage = async (decision: "confirm" | "dismiss") => {
    if (!selected || reason.trim().length < 2) return;
    setBusy("triage");
    setError(null);
    try {
      await api.triageFindingCase(selected.case.id, {
        request_id: crypto.randomUUID(),
        expected_version: selected.case.version,
        decision,
        confirmed_severity: decision === "confirm" ? confirmedSeverity : undefined,
        reason: reason.trim(),
      });
      setNotice(decision === "confirm" ? "候选 observation 已由复核角色确认进入案件流程。" : "候选 observation 已排除并保留审计记录。");
      await refresh(selected.case.id);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "分诊提交失败");
    } finally {
      setBusy(null);
    }
  };

  const start = async () => {
    if (!selected || assignee.trim().length < 2 || remediationPlan.trim().length < 2) return;
    setBusy("start");
    setError(null);
    try {
      await api.startRemediation(selected.case.id, {
        request_id: crypto.randomUUID(),
        expected_version: selected.case.version,
        assignee: assignee.trim(),
        action_description: remediationPlan.trim(),
        due_at: dueAt ? new Date(dueAt).toISOString() : undefined,
      });
      setNotice("整改已开始；状态和负责人已持久化。");
      setRemediationPlan("");
      await refresh(selected.case.id);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "整改启动失败");
    } finally {
      setBusy(null);
    }
  };

  const createAttempt = async () => {
    if (!selected || attemptDescription.trim().length < 2) return;
    setBusy("attempt");
    setError(null);
    try {
      const attempt = await api.createRemediationAttempt(selected.case.id, {
        client_request_id: crypto.randomUUID(),
        expected_version: selected.case.version,
        action_description: attemptDescription.trim(),
      });
      setNotice(`整改尝试 #${attempt.attempt_no} 已创建；请从该 Attempt 进入真实闭环页上传复验证据。`);
      setAttemptDescription("");
      await refresh(selected.case.id);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "整改尝试创建失败");
    } finally {
      setBusy(null);
    }
  };

  const summaryCards = summary ? [
    ["待人工分诊", summary.pending_triage, "候选 observation，不计运营告警"],
    ["已确认待整改", summary.confirmed_open_operational, "仅 operational + reviewer confirmed"],
    ["整改执行中", summary.remediation_in_progress_operational, "已持久化负责人和状态"],
    ["复验处理中", summary.verification_pending_operational, "等待新任务与证据包"],
    ["已证据闭环", summary.closed_operational, "关闭证明需保持核验有效"],
    ["合成演示案件", summary.demo_cases, "永不计入真实告警或指标"],
  ] as const : [];

  return (
    <div className="space-y-5 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      {error ? (
        <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}。页面不会回退显示旧 mock 告警。
        </div>
      ) : null}

      <section className="overflow-hidden rounded-[30px] border border-sky-300/20 bg-[#07172b] px-5 py-6 text-white shadow-[0_28px_80px_-48px_rgba(3,105,161,.9)] md:px-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs text-cyan-100">
              <ShieldIcon className="h-4 w-4" /> 真实数据库 · 人工分诊 · 复验证据关闭
            </div>
            <h2 className="mt-4 text-2xl font-semibold tracking-tight md:text-3xl">告警与整改，不把模型建议冒充现场事实</h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">
              Analyzer finding 首先是候选 observation。只有复核角色确认后才成为运营案件；整改关闭必须绑定新的复验任务、报告和可核验证据包。
            </p>
          </div>
          <button onClick={() => void refresh(selected?.case.id)} disabled={loading} className="rounded-2xl border border-white/15 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white hover:bg-white/15 disabled:opacity-50">
            {loading ? "正在刷新…" : "刷新真实状态"}
          </button>
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6" aria-label="真实案件统计">
        {summaryCards.map(([label, value, note]) => (
          <div key={label} className="rounded-[22px] border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
            <p className="mt-2 text-[11px] leading-4 text-slate-400">{note}</p>
          </div>
        ))}
      </section>

      {summary ? <Notice message={summary.truth_note} /> : null}

      <section className="rounded-[26px] border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <label className="flex flex-1 items-center rounded-2xl border border-slate-200 px-3 py-2.5">
            <SearchIcon className="mr-2 h-4 w-4 text-slate-400" />
            <span className="sr-only">搜索案件</span>
            <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索项目、finding code、内容或负责人" className="w-full bg-transparent text-sm outline-none" />
          </label>
          <button aria-pressed={showFilters} onClick={() => setShowFilters((value) => !value)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-2xl border border-slate-200 px-4 text-sm font-semibold text-slate-700">
            <FilterIcon className="h-4 w-4" /> 筛选
          </button>
        </div>
        {showFilters ? (
          <div className="mt-3 grid gap-3 border-t border-slate-100 pt-3 sm:grid-cols-2">
            <label className="text-xs font-semibold text-slate-600">真实性范围
              <select value={scope} onChange={(event) => setScope(event.target.value as typeof scope)} className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2.5 text-sm">
                <option value="all">全部范围</option><option value="operational">运营案件</option><option value="demo">合成演示</option>
              </select>
            </label>
            <label className="text-xs font-semibold text-slate-600">处置状态
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)} className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2.5 text-sm">
                <option value="all">全部状态</option>
                {Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          </div>
        ) : null}
      </section>

      {loading && !cases.length ? (
        <div className="rounded-[26px] border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">正在读取后端持久化案件…</div>
      ) : visible.length ? (
        <section className="grid gap-3 xl:grid-cols-2">
          {visible.map((item) => {
            const truth = truthDescriptor(item);
            const severity = item.confirmed_severity || item.proposed_severity;
            return (
              <article key={item.id} data-testid="finding-case-card" className="rounded-[26px] border border-slate-200 bg-white p-4 shadow-sm transition hover:border-sky-200 hover:shadow-md">
                <div className="flex items-start gap-3">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-white"><BellIcon className="h-5 w-5" /></div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap gap-2">
                      <span className={cn("rounded-full border px-2.5 py-1 text-[11px] font-semibold", severityTone(severity))}>{severityLabel[severity] || severity}</span>
                      <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", statusTone(item.status))}>{statusLabel[item.status]}</span>
                    </div>
                    <p className="mt-3 text-sm font-semibold text-slate-900">{projectNames.get(item.project_id) || item.project_id}</p>
                    <p className="mt-1 text-sm leading-6 text-slate-600">{item.finding_message}</p>
                    <span className={cn("mt-3 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold", truth.tone)}><ShieldIcon className="h-3.5 w-3.5" />{truth.label}</span>
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                      <span>{item.finding_code}</span><span>{item.analyzer_name} · {item.analyzer_version}</span><span>{formatTime(item.created_at)}</span>
                    </div>
                  </div>
                  <button aria-label={`查看案件 ${item.id}`} disabled={busy === `detail:${item.id}`} onClick={() => void openDetail(item.id)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 hover:text-sky-700"><EyeIcon className="h-4 w-4" /></button>
                </div>
              </article>
            );
          })}
        </section>
      ) : (
        <div className="rounded-[26px] border border-dashed border-slate-300 bg-white p-10 text-center">
          <p className="text-sm font-semibold text-slate-700">没有符合条件的持久化案件</p>
          <p className="mt-2 text-xs text-slate-500">这不是 mock 回退；可调整筛选，或从真实闭环页产生带 finding 的任务。</p>
        </div>
      )}

      <Modal open={!!selected} onClose={() => setSelected(null)} title="Finding 案件与整改证据" description="候选观察、人工决策、整改尝试和关闭证明分别记录。">
        {selected ? (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["项目", projectNames.get(selected.case.project_id) || selected.case.project_id],
                ["状态", statusLabel[selected.case.status]],
                ["Finding Code", selected.case.finding_code],
                ["来源模式", `${selected.case.analysis_mode} · ${selected.case.scope}`],
                ["来源任务", selected.case.source_job_id],
                ["Finding SHA-256", selected.case.finding_sha256],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0 rounded-[20px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">{label}</p><p className="mt-2 break-all text-sm font-semibold text-slate-800">{value}</p>
                </div>
              ))}
            </div>
            <div className="rounded-[20px] border border-slate-200 p-4"><p className="text-xs text-slate-500">候选内容</p><p className="mt-2 text-sm leading-6 text-slate-700">{selected.case.finding_message}</p></div>

            {selected.case.status === "pending_triage" ? (
              <section className="rounded-[22px] border border-amber-200 bg-amber-50 p-4">
                <p className="text-sm font-semibold text-amber-900">Reviewer 分诊</p>
                <p className="mt-2 text-xs leading-5 text-amber-700">确认只表示进入人工案件流程，不证明模型判断正确；demo 案件始终不进入运营统计。</p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <select value={confirmedSeverity} onChange={(event) => setConfirmedSeverity(event.target.value as typeof confirmedSeverity)} className="rounded-2xl border border-amber-200 bg-white px-3 py-2.5 text-sm"><option value="info">信息</option><option value="warning">警告</option><option value="error">严重</option><option value="critical">紧急</option></select>
                  <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="必填：人工确认或排除理由" className="rounded-2xl border border-amber-200 bg-white px-3 py-2.5 text-sm" />
                </div>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row"><button disabled={busy === "triage" || reason.trim().length < 2} onClick={() => void triage("confirm")} className="min-h-11 flex-1 rounded-2xl bg-sky-700 px-4 text-sm font-semibold text-white disabled:opacity-50">确认进入案件</button><button disabled={busy === "triage" || reason.trim().length < 2} onClick={() => void triage("dismiss")} className="min-h-11 rounded-2xl border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 disabled:opacity-50">排除候选</button></div>
              </section>
            ) : null}

            {selected.case.status === "open" ? (
              <section className="rounded-[22px] border border-sky-200 bg-sky-50 p-4">
                <p className="text-sm font-semibold text-sky-900">启动整改</p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2"><input value={assignee} onChange={(event) => setAssignee(event.target.value)} placeholder="负责人" className="rounded-2xl border border-sky-200 bg-white px-3 py-2.5 text-sm" /><input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} className="rounded-2xl border border-sky-200 bg-white px-3 py-2.5 text-sm" /></div>
                <textarea value={remediationPlan} onChange={(event) => setRemediationPlan(event.target.value)} placeholder="整改要求与计划" className="mt-3 min-h-24 w-full rounded-2xl border border-sky-200 bg-white px-3 py-2.5 text-sm" />
                <button disabled={busy === "start" || assignee.trim().length < 2 || remediationPlan.trim().length < 2} onClick={() => void start()} className="mt-3 min-h-11 w-full rounded-2xl bg-sky-700 px-4 text-sm font-semibold text-white disabled:opacity-50">持久化整改任务</button>
              </section>
            ) : null}

            {selected.case.status === "remediation_in_progress" ? (
              <section className="rounded-[22px] border border-violet-200 bg-violet-50 p-4">
                <p className="text-sm font-semibold text-violet-900">提交一次整改尝试</p>
                <textarea value={attemptDescription} onChange={(event) => setAttemptDescription(event.target.value)} placeholder="实际完成的整改动作" className="mt-3 min-h-24 w-full rounded-2xl border border-violet-200 bg-white px-3 py-2.5 text-sm" />
                <button disabled={busy === "attempt" || attemptDescription.trim().length < 2 || selected.attempts.some((item) => item.resolution_decision === "pending")} onClick={() => void createAttempt()} className="mt-3 min-h-11 w-full rounded-2xl bg-violet-700 px-4 text-sm font-semibold text-white disabled:opacity-50">创建 Attempt 并准备复验</button>
              </section>
            ) : null}

            {selected.attempts.length ? (
              <section>
                <p className="text-sm font-semibold text-slate-900">整改尝试与复验</p>
                <div className="mt-3 space-y-3">{selected.attempts.map((attempt) => <div key={attempt.id} className="rounded-[20px] border border-slate-200 bg-slate-50 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-semibold text-slate-800">Attempt #{attempt.attempt_no}</p><span className="rounded-full bg-white px-2.5 py-1 text-[11px] text-slate-600">{attempt.resolution_decision}</span></div><p className="mt-2 text-sm text-slate-600">{attempt.action_description}</p><p className="mt-2 break-all font-mono text-[11px] text-slate-500">Attempt ID: {attempt.id}</p>{attempt.verification_job_id ? <p className="mt-1 break-all font-mono text-[11px] text-slate-500">复验任务: {attempt.verification_job_id}</p> : attempt.resolution_decision === "pending" && selected.case.status === "remediation_in_progress" ? <div className="mt-3 flex flex-col gap-2 sm:flex-row"><Link to={`/backend-workflow?caseId=${encodeURIComponent(selected.case.id)}&attemptId=${encodeURIComponent(attempt.id)}`} className="inline-flex min-h-11 flex-1 items-center justify-center rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-white">使用原项目与基线上传复验证据</Link><button onClick={() => void navigator.clipboard.writeText(attempt.id)} className="min-h-11 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700">复制 Attempt ID</button></div> : null}</div>)}</div>
              </section>
            ) : null}

            {selected.case.status === "verification_pending" ? <Notice message="复验任务已绑定。请在真实闭环页完成分析、人工判定与封存；关闭将引用新的 proof，不改写原报告。" /> : null}
            {selected.case.status === "closed" ? <div className={cn("rounded-[22px] border p-4", selected.closure_evidence_status === "sealed" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800")}><div className="flex items-center gap-2 text-sm font-semibold"><CheckIcon className="h-4 w-4" />关闭证明：{selected.closure_evidence_status}</div><p className="mt-2 break-all font-mono text-[11px]">{selected.case.closure_proof_id}</p></div> : null}

            {selected.history.length ? <details className="rounded-[20px] border border-slate-200"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-slate-700">查看幂等命令历史（{selected.history.length}）</summary><div className="space-y-2 border-t border-slate-100 p-4">{selected.history.map((entry) => <div key={entry.id} className="text-xs leading-5 text-slate-600">{formatTime(entry.created_at)} · {entry.command} · {entry.from_status} → {entry.to_status} · v{entry.result_version}</div>)}</div></details> : null}
          </div>
        ) : null}
      </Modal>
    </div>
  );
};
