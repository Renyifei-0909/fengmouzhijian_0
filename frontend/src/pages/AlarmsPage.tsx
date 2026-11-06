import type { ReactNode } from "react";
import { projectDisplayName } from "../lib/productCopy";
import { OperationStatus } from "../components/ui/OperationStatus";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowUpRight, Bell, Check, ChevronRight, ListChecks, RefreshCw, Search, SlidersHorizontal, Wrench } from "lucide-react";
import { api, ApiRequestError } from "../lib/api";
import type { FindingCase, FindingCaseDetail, FindingCaseStatus, Project, RemediationAttempt } from "../lib/api";
import { apiMessage, findingTitle, LinkedWorkOrder, localTime, useRecordedEvidence, WorkbenchEmpty, WorkbenchEvidence, WorkbenchMeta, WorkbenchStatus } from "./workbench-ui";
import type { WorkbenchTone } from "./workbench-ui";

type Severity = FindingCase["proposed_severity"];
type Scope = FindingCase["scope"];
const caseStatus: Record<FindingCaseStatus, { label: string; tone: WorkbenchTone }> = {
  pending_triage: { label: "待确认", tone: "amber" },
  open: { label: "待派发", tone: "amber" },
  remediation_in_progress: { label: "整改中", tone: "cyan" },
  verification_pending: { label: "待复验", tone: "cyan" },
  closed: { label: "已关闭", tone: "green" },
  dismissed: { label: "已排除", tone: "neutral" },
};
const severities: Record<Severity, { label: string; tone: WorkbenchTone; order: number }> = {
  critical: { label: "紧急", tone: "red", order: 0 },
  error: { label: "严重", tone: "red", order: 1 },
  warning: { label: "警告", tone: "amber", order: 2 },
  info: { label: "提示", tone: "neutral", order: 3 },
};
const caseFilters = {
  all: { label: "全部", statuses: [] },
  triage: { label: "待确认", statuses: ["pending_triage"] },
  remediation: { label: "整改", statuses: ["open", "remediation_in_progress"] },
  verify: { label: "待复验", statuses: ["verification_pending"] },
  closed: { label: "已关闭", statuses: ["closed"] },
  dismissed: { label: "已排除", statuses: ["dismissed"] },
} satisfies Record<string, { label: string; statuses: FindingCaseStatus[] }>;

type CaseFilter = keyof typeof caseFilters;
const matchesStatus = (item: FindingCase, filter: CaseFilter) => filter === "all"
  || (caseFilters[filter].statuses as FindingCaseStatus[]).includes(item.status);
const itemSeverity = (item: FindingCase) => severities[item.confirmed_severity || item.proposed_severity];
function recheckLink(item: FindingCase, attemptId: string) {
  const query = new URLSearchParams({ caseId: item.id, attemptId });
  if (item.scope === "demo") query.set("scope", "trial");
  return `/backend-workflow?${query}`;
}

const commandLabels: Record<string, string> = {
  finding_confirmed: "确认问题",
  finding_dismissed: "排除问题",
  remediation_started: "派发整改",
  remediation_attempt_created: "记录整改结果",
};

const closedProofState = {
  sealed: { label: "关闭证据已封存", tone: "green" },
  invalid: { label: "关闭证据异常", tone: "red" },
  unsealed: { label: "关闭证据待封存", tone: "amber" },
} satisfies Record<FindingCaseDetail["closure_evidence_status"], { label: string; tone: WorkbenchTone }>;

export function AlarmsPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("caseId")?.trim() || "";
  const jobFilter = params.get("job")?.trim() || "";
  const [cases, setCases] = useState<FindingCase[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [filter, setFilter] = useState<CaseFilter>("all");
  const [keyword, setKeyword] = useState("");
  const scope: Scope = params.get("scope") === "trial" ? "demo" : "operational";
  const [severityFilter, setSeverityFilter] = useState<Severity | "">("");
  const [projectFilter, setProjectFilter] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const readGeneration = useRef(0);
  const changedCases = useRef(new Map<string, FindingCase>());

  useEffect(() => {
    const request = ++readGeneration.current;
    const updates = new Map<string, FindingCase>();
    changedCases.current = updates;
    setLoading(true);
    setError("");
    void Promise.allSettled([api.listFindingCases({ scope }), api.listProjects()]).then(([rows, projectRows]) => {
      if (request !== readGeneration.current) return;
      if (rows.status === "fulfilled") {
        const received = new Set(rows.value.map((item) => item.id));
        setCases([...rows.value.map((item) => {
          const current = updates.get(item.id);
          return current && current.version >= item.version ? current : item;
        }), ...[...updates.values()].filter((item) => item.scope === scope && !received.has(item.id))]);
      }
      if (projectRows.status === "fulfilled") setProjects(projectRows.value);
      const failure = rows.status === "rejected" ? rows.reason : projectRows.status === "rejected" ? projectRows.reason : null;
      if (failure) setError(apiMessage(failure, "整改队列读取失败"));
      setLoading(false);
    });
    return () => { readGeneration.current += 1; };
  }, [revision, scope]);

  const selectedCaseScope = cases.find((item) => item.id === selectedId)?.scope;
  useEffect(() => {
    if (!selectedCaseScope || selectedCaseScope === scope) return;
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (selectedCaseScope === "demo") next.set("scope", "trial");
      else next.delete("scope");
      return next;
    }, { replace: true });
  }, [scope, selectedCaseScope, setParams]);

  const projectNames = useMemo(() => new Map(projects.map((item) => [item.id, projectDisplayName(item.name)])), [projects]);
  const scopeCases = cases.filter((item) => item.scope === scope && (!jobFilter || item.source_job_id === jobFilter));
  const query = keyword.trim().toLocaleLowerCase();
  const searched = scopeCases.filter((item) => (!projectFilter || item.project_id === projectFilter)
    && (!severityFilter || (item.confirmed_severity || item.proposed_severity) === severityFilter)
    && (!query || [projectNames.get(item.project_id), findingTitle(item.finding_code, item.finding_message), item.finding_message, item.assigned_to, item.finding_code, item.id]
      .some((text) => text?.toLocaleLowerCase().includes(query))));
  const visible = searched.filter((item) => matchesStatus(item, filter)).sort((left, right) =>
    itemSeverity(left).order - itemSeverity(right).order || right.created_at.localeCompare(left.created_at));

  const chooseCase = (id: string) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("caseId", id);
      return next;
    });
  };

  useEffect(() => {
    if (selectedId || !visible[0]) return;
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("caseId", visible[0].id);
      return next;
    }, { replace: true });
  }, [selectedId, setParams, visible]);

  const syncCase = useCallback((item: FindingCase) => {
    const previous = changedCases.current.get(item.id);
    if (!previous || previous.version <= item.version) changedCases.current.set(item.id, item);
    setCases((current) => current.some((row) => row.id === item.id)
      ? current.map((row) => row.id === item.id && item.version >= row.version ? item : row)
      : [...current, item]);
  }, []);

  return <div className="fm-wf page-enter">
    <header className="fm-wf-heading">
      <div><h2>问题与处置</h2><p>确认现场问题，跟踪整改进度并复核关闭证据。</p></div>
      <button type="button" className="fm-button" disabled={loading} onClick={() => {
        setRevision((value) => value + 1);
        setDetailRevision((value) => value + 1);
      }}><RefreshCw size={16} />刷新状态</button>
    </header>
    <section className="fm-panel fm-wf-counterbar" aria-label="整改状态统计" aria-busy={loading}>
      {([
        ["triage", "待确认", "amber"],
        ["remediation", "待整改 / 整改中", "cyan"],
        ["verify", "待复验", "cyan"],
        ["closed", "已关闭", "green"],
      ] as const).map(([key, label, tone]) => <button type="button" key={key} aria-pressed={filter === key}
        onClick={() => setFilter((current) => current === key ? "all" : key)} data-tone={tone}>
        <span className="fm-wf-counter-label">{label}</span>
        <strong>{loading && !scopeCases.length ? "—" : scopeCases.filter((item) => matchesStatus(item, key)).length}</strong>
        <ChevronRight size={15} aria-hidden="true" />
      </button>)}
    </section>
    <div className="fm-wf-layout">
      <section className="fm-panel fm-wf-queue" aria-label="整改案件列表" aria-busy={loading}>
        <div className="fm-wf-queue-head">
          <div className="fm-wf-section-head"><h2><ListChecks size={17} />处置队列</h2>
            <button type="button" className="fm-wf-icon-button" aria-label="更多筛选" aria-expanded={advanced}
              onClick={() => setAdvanced((value) => !value)}><SlidersHorizontal size={16} /></button>
          </div>
          <label className="fm-wf-search"><Search size={15} />
            <input aria-label="搜索整改案件" placeholder="搜索问题、项目或负责人" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
          </label>
          {jobFilter ? <button type="button" className="fm-wf-link" onClick={() => setParams((current) => {
            const next = new URLSearchParams(current);
            next.delete("job");
            return next;
          })}>清除来源任务筛选</button> : null}
          {advanced ? <div className="fm-wf-filter-stack">
            <select aria-label="风险等级" className="fm-input" value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value as Severity | "")}>
              <option value="">所有风险等级</option>
              {(Object.keys(severities) as Severity[]).map((key) => <option key={key} value={key}>{severities[key].label}</option>)}
            </select>
            <select aria-label="整改项目" className="fm-input" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
              <option value="">所有项目</option>{projects.map((item) => <option key={item.id} value={item.id}>{projectDisplayName(item.name)}</option>)}
            </select>
            <details className="fm-wf-form-disclosure">
              <summary>扩展范围</summary>
              <label className="fm-wf-check"><input type="checkbox" checked={scope === "demo"} onChange={(event) => {
                setProjectFilter("");
                setParams(event.target.checked ? { scope: "trial" } : {});
              }} />查看扩展记录</label>
            </details>
          </div> : null}
        </div>
        <div className="fm-wf-tabs" aria-label="案件状态">
          {(Object.keys(caseFilters) as CaseFilter[]).map((key) => <button type="button" key={key} aria-pressed={filter === key}
            onClick={() => setFilter(key)}>{caseFilters[key].label}</button>)}
        </div>
        {error ? <p className="fm-wf-message" role="alert" data-tone="red">{error}</p> : null}
        {visible.length ? <ol className="fm-wf-list">
          {visible.map((item) => {
            const severity = itemSeverity(item);
            return <li key={item.id}><button type="button" aria-pressed={selectedId === item.id} onClick={() => chooseCase(item.id)}>
              <Bell size={16} data-tone={severity.tone} aria-hidden="true" />
              <div className="fm-wf-row-text"><strong>{findingTitle(item.finding_code, item.finding_message)}</strong>
                <small>{projectNames.get(item.project_id) || "工程项目"} · {localTime(item.created_at, true)}</small></div>
              <WorkbenchStatus tone={caseStatus[item.status].tone}>{caseStatus[item.status].label}</WorkbenchStatus>
            </button></li>;
          })}
        </ol> : <WorkbenchEmpty title={loading ? "正在读取案件" : "暂无整改案件"}>
          {!loading && !error ? keyword || filter !== "all" || jobFilter || projectFilter || severityFilter
            ? "可调整搜索条件或状态筛选。" : "核验产生的待确认问题会出现在这里。" : null}
        </WorkbenchEmpty>}
        <div className="fm-wf-queue-foot"><span>{visible.length} 项</span>{scope === "demo" ? <span>扩展范围</span> : null}</div>
      </section>
      {selectedId ? <CaseWorkbench key={selectedId} id={selectedId} projectNames={projectNames} revision={detailRevision} onCase={syncCase} />
        : <section className="fm-panel fm-wf-unselected"><WorkbenchEmpty title="案件工作区" media>选择案件查看现场资料和当前处置事项。</WorkbenchEmpty></section>}
    </div>
  </div>;
}

function CaseWorkbench({ id, projectNames, revision, onCase }: {
  id: string; projectNames: Map<string, string>; revision: number; onCase: (item: FindingCase) => void;
}) {
  const [detail, setDetail] = useState<FindingCaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const mounted = useRef(false);
  const readVersion = useRef(0);
  const operation = useRef<string | null>(null);
  const commandRequests = useRef(new Map<string, string>());

  const readCase = useCallback(async () => {
    const request = ++readVersion.current;
    setReadError("");
    try {
      const next = await api.findingCase(id);
      if (!mounted.current || readVersion.current !== request) return;
      setDetail(next);
      onCase(next.case);
    } catch (reason) {
      if (mounted.current && readVersion.current === request) setReadError(apiMessage(reason, "案件详情读取失败"));
    } finally {
      if (mounted.current && readVersion.current === request) setLoading(false);
    }
  }, [id, onCase]);

  useEffect(() => {
    mounted.current = true;
    void readCase();
    return () => { mounted.current = false; readVersion.current += 1; };
  }, [readCase, revision]);

  const source = useRecordedEvidence(detail?.case.source_job_id, detail?.case.source_evidence_id);

  const sendCommand = async <T extends { expected_version: number }>(name: string, payload: T, success: string,
    send: (requestId: string, input: T) => Promise<unknown>) => {
    if (operation.current) return;
    operation.current = name;
    readVersion.current += 1;
    setBusy(name);
    setActionError("");
    setNotice("");
    const identity = JSON.stringify([name, payload]);
    const requestId = commandRequests.current.get(identity) ?? newRequestId();
    commandRequests.current.set(identity, requestId);
    try {
      const result = await send(requestId, payload);
      if (!mounted.current) return;
      commandRequests.current.delete(identity);
      if (result && typeof result === "object" && "version" in result && "status" in result) {
        const next = result as FindingCase;
        onCase(next);
        setDetail((current) => current && next.version >= current.case.version ? { ...current, case: next } : current);
      } else if (result && typeof result === "object" && "attempt_no" in result && "case_id" in result) {
        const attempt = result as RemediationAttempt;
        setDetail((current) => current && !current.attempts.some((item) => item.id === attempt.id)
          ? { ...current, attempts: [...current.attempts, attempt] } : current);
      }
      setNotice(success);
      await readCase();
    } catch (reason) {
      if (!mounted.current) return;
      const conflict = reason instanceof ApiRequestError && reason.status === 409;
      setActionError(conflict ? "案件状态已变化，正在重新读取。请确认当前状态后再操作。" : apiMessage(reason, "案件更新失败"));
      if (conflict) await readCase();
    } finally {
      operation.current = null;
      if (mounted.current) setBusy("");
    }
  };

  if (!detail) return <section className="fm-panel fm-wf-unselected" aria-busy={loading}>
    <WorkbenchEmpty title={loading ? "正在读取案件" : "案件详情暂不可用"} media>
      {readError ? <p role="alert">{readError}</p> : null}
      {!loading ? <button type="button" className="fm-button" onClick={() => { setLoading(true); void readCase(); }}>重新读取</button> : null}
    </WorkbenchEmpty>
  </section>;

  const item = detail.case;
  const severity = itemSeverity(item);
  const latestFirst = [...detail.attempts].sort((left, right) => right.attempt_no - left.attempt_no);
  const closure = closedProofState[detail.closure_evidence_status];
  const currentStep = item.status === "pending_triage" || item.status === "dismissed" ? 0
    : item.status === "open" || item.status === "remediation_in_progress" ? 1 : item.status === "verification_pending" ? 2 : 3;
  return <article className="fm-panel fm-wf-detail">
    <header className="fm-wf-detail-head">
      <div className="fm-wf-detail-title">
        <span className="fm-wf-label">{item.scope === "demo" ? "扩展记录" : "案件详情"}</span>
        <h2>{findingTitle(item.finding_code, item.finding_message)}</h2>
        <p>{projectNames.get(item.project_id) || "工程项目"} · {localTime(item.created_at)}</p>
      </div>
      <div className="fm-wf-actions">
        <WorkbenchStatus tone={severity.tone}>{item.status === "pending_triage" ? "建议 · " : ""}{severity.label}</WorkbenchStatus>
        <WorkbenchStatus tone={caseStatus[item.status].tone}>{caseStatus[item.status].label}</WorkbenchStatus>
      </div>
    </header>
    <ol className="fm-wf-steps" aria-label="整改处置进度">
      {["问题确认", "现场整改", "资料复验", "证据关闭"].map((label, index) => <li key={label}
        data-state={index < currentStep || item.status === "closed" ? "done" : index === currentStep ? "current" : "pending"}
        aria-current={index === currentStep && item.status !== "closed" ? "step" : undefined}>
        <span>{index + 1}</span>{label}{index < 3 ? <ChevronRight size={13} aria-hidden="true" /> : null}
      </li>)}
    </ol>
    {busy && <OperationStatus detail="保存完成后会刷新案件和关联工单。">
      {busy === "start" ? "正在派发整改" : busy === "record" ? "正在登记整改措施" : "正在保存确认结果"}
    </OperationStatus>}
    {readError ? <p className="fm-wf-message" data-tone="amber" role="status">{readError}</p> : null}
    {actionError ? <p className="fm-wf-message" data-tone="red" role="alert">{actionError}</p> : null}
    {notice ? <p className="fm-wf-message" data-tone="green" role="status">{notice}</p> : null}
    <div className="fm-wf-inspection">
      <div className="fm-wf-media-stack">
        {source.evidence ? <WorkbenchEvidence evidence={source.evidence} title="问题原始资料" />
          : source.loading ? <OperationStatus>正在读取原始资料</OperationStatus>
            : <WorkbenchEmpty title="原始资料暂不可用" media />}
        <Link className="fm-wf-link" to={`/backend-workflow?job=${encodeURIComponent(item.source_job_id)}`}>查看原核验任务 <ArrowUpRight size={13} /></Link>
      </div>
      <div className="fm-wf-inspection-side">
        <section className="fm-wf-subsection">
          <div className="fm-wf-section-head"><h3>处置摘要</h3></div>
          <WorkbenchMeta entries={[
            ...(typeof source.evidence?.metadata.work_order_id === "string" ? [["关联工单", <LinkedWorkOrder
              id={source.evidence.metadata.work_order_id} code={typeof source.evidence.metadata.work_order_code === "string" ? source.evidence.metadata.work_order_code : undefined}
              revision={item.version} />] as [string, ReactNode]] : []),
            ["负责人", item.assigned_to || "待派发"],
            ["截止时间", localTime(item.due_at)],
            ["人工确认", localTime(item.confirmed_at)],
            ...(item.decision_reason ? [["确认意见", item.decision_reason]] as [string, string][] : []),
          ]} />
        </section>
        <CaseActionForm key={item.status} detail={detail} busy={busy} sendCommand={sendCommand} />
      </div>
    </div>
    {latestFirst.length ? <section className="fm-wf-archive">
      <div className="fm-wf-section-head"><h3><Wrench size={16} />整改记录</h3><span>{latestFirst.length} 次</span></div>
      <ol className="fm-wf-attempts">
        {latestFirst.map((attempt) => <li key={attempt.id}>
          <div>
            <strong>第 {attempt.attempt_no} 次整改</strong>
            <p>{attempt.action_description}</p>
            <small>{localTime(attempt.submitted_at)}{attempt.resolved_at ? ` · 复验 ${localTime(attempt.resolved_at)}` : ""}</small>
          </div>
          <div className="fm-wf-actions">
            <WorkbenchStatus tone={attempt.resolution_decision === "resolved" ? "green" : attempt.resolution_decision === "not_resolved" ? "amber" : "neutral"}>
              {attempt.resolution_decision === "resolved" ? "已解决" : attempt.resolution_decision === "not_resolved" ? "仍需整改" : attempt.verification_job_id ? "待复验" : "待上传资料"}
            </WorkbenchStatus>
            {attempt.verification_job_id || (item.status === "remediation_in_progress" && attempt.resolution_decision === "pending")
              ? <Link className={attempt.verification_job_id ? "fm-button" : "fm-button fm-button-primary"}
                to={recheckLink(item, attempt.id)}>{attempt.verification_job_id ? "查看复验" : "提交复验资料"} <ArrowUpRight size={13} /></Link> : null}
          </div>
        </li>)}
      </ol>
    </section> : null}
    {item.status === "closed" ? <section className="fm-wf-archive">
      <div className="fm-wf-section-head"><h3><Check size={16} />关闭证据</h3><WorkbenchStatus tone={closure.tone}>{closure.label}</WorkbenchStatus></div>
      <p className="fm-wf-muted">关闭时间 {localTime(item.closed_at)}</p>
      {item.closure_proof_id ? <Link className="fm-wf-link" to={`/traceability?archive=${encodeURIComponent(item.closure_proof_id)}`}>
        打开关闭档案 <ArrowUpRight size={13} />
      </Link> : null}
    </section> : null}
    <details className="fm-wf-disclosure">
      <summary>操作轨迹{detail.history.length ? `（${detail.history.length}）` : ""}</summary>
      {detail.history.length ? <ol className="fm-wf-log">
        {[...detail.history].sort((left, right) => right.created_at.localeCompare(left.created_at)).map((entry) => <li key={entry.id}>
          <strong>{commandLabels[entry.command] || caseStatus[entry.to_status].label}</strong>
          <time>{localTime(entry.created_at)}</time>
          <span>{caseStatus[entry.from_status].label} → {caseStatus[entry.to_status].label}</span>
        </li>)}
      </ol> : <p className="fm-wf-muted">暂无人工处置记录。</p>}
    </details>
    <details className="fm-wf-disclosure">
      <summary>技术详情</summary>
      <WorkbenchMeta entries={[
        ["案件编号", item.id],
        ["问题编号", item.finding_code],
        ["原始记录", item.finding_message],
        ["分析版本", item.analyzer_version],
        ["案件版本", item.version],
        ["问题摘要", item.finding_sha256],
        ["关闭证据编号", item.closure_proof_id],
      ]} />
    </details>
  </article>;
}

function newRequestId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

type SendCommand = <T extends { expected_version: number }>(name: string, payload: T, success: string,
  send: (requestId: string, input: T) => Promise<unknown>) => Promise<void>;

function CaseActionForm({ detail, busy, sendCommand }: { detail: FindingCaseDetail; busy: string; sendCommand: SendCommand }) {
  const item = detail.case;
  const [reason, setReason] = useState("");
  const [severity, setSeverity] = useState<Severity>(item.confirmed_severity || item.proposed_severity);
  const [assignee, setAssignee] = useState(item.assigned_to || "");
  const [deadline, setDeadline] = useState("");
  const [plan, setPlan] = useState("");
  const [action, setAction] = useState("");
  const pendingAttempt = detail.attempts.some((attempt) => attempt.resolution_decision === "pending");
  const triage = (decision: "confirm" | "dismiss") => {
    if (reason.trim().length < 2) return;
    void sendCommand(decision, { expected_version: item.version, decision, reason: reason.trim(),
      confirmed_severity: decision === "confirm" ? severity : undefined },
    decision === "confirm" ? "问题已确认，可派发整改。" : "该问题已排除。",
    (request_id, payload) => api.triageFindingCase(item.id, { request_id, ...payload }));
  };
  const start = (event: FormEvent) => {
    event.preventDefault();
    if (assignee.trim().length < 2 || plan.trim().length < 2) return;
    void sendCommand("start", { expected_version: item.version, assignee: assignee.trim(), action_description: plan.trim(),
      due_at: deadline ? new Date(deadline).toISOString() : undefined },
    "整改已派发。", (request_id, payload) => api.startRemediation(item.id, { request_id, ...payload }));
  };
  const record = (event: FormEvent) => {
    event.preventDefault();
    if (action.trim().length < 2 || pendingAttempt) return;
    void sendCommand("record", { expected_version: item.version, action_description: action.trim() },
      "整改记录已提交，可上传复验资料。", (client_request_id, payload) => api.createRemediationAttempt(item.id, { client_request_id, ...payload }));
  };

  if (item.status === "pending_triage") return <section className="fm-wf-subsection fm-wf-form">
    <div className="fm-wf-section-head"><h3>确认现场问题</h3></div>
    <label className="fm-wf-field">风险等级<select className="fm-input" value={severity} onChange={(event) => setSeverity(event.target.value as Severity)}>
      {(Object.keys(severities) as Severity[]).map((key) => <option key={key} value={key}>{severities[key].label}</option>)}
    </select></label>
    <label className="fm-wf-field">处理意见<textarea className="fm-input" rows={3} maxLength={2000} value={reason}
      onChange={(event) => setReason(event.target.value)} placeholder="填写确认或排除的现场依据" /></label>
    <div className="fm-wf-actions">
      <button type="button" className="fm-button fm-button-primary" disabled={Boolean(busy || reason.trim().length < 2)}
        onClick={() => triage("confirm")}>{busy === "confirm" ? "正在确认…" : "确认问题"}</button>
      <button type="button" className="fm-button" disabled={Boolean(busy || reason.trim().length < 2)}
        onClick={() => triage("dismiss")}>{busy === "dismiss" ? "正在提交…" : "排除"}</button>
    </div>
  </section>;

  if (item.status === "open") return <form className="fm-wf-subsection fm-wf-form" onSubmit={start}>
    <div className="fm-wf-section-head"><h3>派发整改</h3></div>
    <label className="fm-wf-field">负责人<input className="fm-input" required minLength={2} maxLength={100} value={assignee}
      onChange={(event) => setAssignee(event.target.value)} placeholder="输入负责人姓名" /></label>
    <label className="fm-wf-field">截止时间<input type="datetime-local" className="fm-input" value={deadline}
      onChange={(event) => setDeadline(event.target.value)} /></label>
    <label className="fm-wf-field">整改要求<textarea className="fm-input" required rows={3} minLength={2} maxLength={4000} value={plan}
      onChange={(event) => setPlan(event.target.value)} placeholder="填写整改范围与要求" /></label>
    <button type="submit" className="fm-button fm-button-primary" disabled={Boolean(busy || assignee.trim().length < 2 || plan.trim().length < 2)}>
      {busy === "start" ? "正在派发…" : "派发整改"}</button>
  </form>;

  if (item.status === "remediation_in_progress" && !pendingAttempt) return <form className="fm-wf-subsection fm-wf-form" onSubmit={record}>
    <div className="fm-wf-section-head"><h3>记录整改结果</h3></div>
    <label className="fm-wf-field">现场处理<textarea className="fm-input" required rows={3} minLength={2} maxLength={4000} value={action}
      onChange={(event) => setAction(event.target.value)} placeholder="填写已完成的整改措施" /></label>
    <button type="submit" className="fm-button fm-button-primary" disabled={Boolean(busy || action.trim().length < 2)}>
      {busy === "record" ? "正在提交…" : "保存整改记录"}</button>
  </form>;
  return null;
}
