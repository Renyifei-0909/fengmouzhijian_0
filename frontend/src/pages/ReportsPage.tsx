import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowUpRight, Download, FileText, Printer, RefreshCw, Search } from "lucide-react";
import { api } from "../lib/api";
import type { Project, Report } from "../lib/api";
import { PRODUCT, projectDisplayName } from "../lib/productCopy";
import { reportTruthFromReport } from "../lib/truth";
import { labelRuleVerdict } from "../lib/verificationDisplay";
import { RecordEmpty, RecordError, RecordMeta, ReportStatus, REPORT_STATES, recordError, recordObject, recordProcedure, recordText, recordTime } from "./records-ui";

function reportParts(report: Report) {
  const content = recordObject(report.content);
  return {
    content,
    project: recordObject(content.project),
    baseline: recordObject(content.design_baseline),
    evidence: recordObject(content.evidence),
    analysis: recordObject(content.analysis),
    review: recordObject(content.human_review),
    cases: Array.isArray(content.finding_cases) ? content.finding_cases.map(recordObject) : [],
    sensors: Array.isArray(content.related_sensor_events) ? content.related_sensor_events : [],
  };
}

function ReadablePaper({ view, sealedAt }: { view: Record<string, unknown>; sealedAt: string }) {
  const sections = Array.isArray(view.sections) ? view.sections.map(recordObject) : [];
  const highlights = Array.isArray(view.highlights) ? view.highlights.map(recordObject) : [];
  return <article className="fm-paper fm-report-paper" aria-label="核验报告正文">
    <header>
      <p>{PRODUCT.name}　/　工程交付记录</p>
      <h2 className="mt-2">{recordText(view.title)}</h2>
      <small>封存于 {recordTime(sealedAt)}</small>
    </header>
    <div className="fm-report-summary">
      <p>{recordText(view.summary)}</p>
      <dl>{highlights.map(({ label, value }) => <div key={recordText(label)}>
        <dt>{recordText(label)}</dt><dd>{recordText(value)}</dd>
      </div>)}</dl>
    </div>
    {sections.map((section) => {
      const heading = recordText(section.heading);
      const facts = Array.isArray(section.facts) ? section.facts.map(recordObject) : [];
      const paragraphs = Array.isArray(section.paragraphs) ? section.paragraphs.map(recordText).filter(Boolean) : [];
      const checks = Array.isArray(section.checks) ? section.checks.map(recordObject) : [];
      return <section key={heading} className="fm-report-chapter">
        <h3>{heading}</h3>
        {facts.length ? <RecordMeta entries={facts
          .filter(({ label, value }) => !(label === "资料类别" && value === "场景影像"))
          .map(({ label, value }) => [recordText(label), value === "规则参考" ? "规则辅助" : recordText(value)])} /> : null}
        {paragraphs.map((paragraph, index) => <p key={index} className="fm-report-paragraph">{paragraph}</p>)}
        {checks.length ? <table className="fm-report-checks">
          <thead><tr>{["核验项", "设计要求", "观测记录", "判定"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{checks.map((check, index) => <tr key={index}>
            <th scope="row">{recordText(check.item)}</th><td>{recordText(check.expected)}</td>
            <td>{recordText(check.observed)}</td><td>{recordText(check.status)}</td>
          </tr>)}</tbody>
        </table> : null}
      </section>;
    })}
  </article>;
}

function ReportPaper({ report, project }: { report: Report; project?: Project }) {
  const { content, project: storedProject, baseline, evidence, analysis, review, cases, sensors } = reportParts(report);
  const readable = recordObject(content.readable);
  if (readable.version === 1) return <ReadablePaper view={readable} sealedAt={report.created_at} />;
  const title = projectDisplayName(recordText(storedProject.name) || project?.name || "工程项目");
  const reportState = REPORT_STATES[report.status]?.label || "报告已生成";
  const reviewDecision = { approve: "已批准", approved: "已批准", reject: "已驳回", rejected: "已驳回" }[recordText(review.decision)];
  const analysisSummary = recordText(analysis.summary) || recordText(analysis.conclusion);
  const verdict = recordText(recordObject(analysis.compliance_evaluation).verdict);
  const remediation = recordObject(recordObject(content.remediation_context).attempt);
  const isRoutine = report.status === "reviewed_demo" || report.status === "reviewed_placeholder";
  const observations = cases.filter((item) => recordText(item.scope) === "operational" && recordText(item.message));
  const reviewer = recordText(review.reviewer);
  const reviewNote = recordText(review.note).replace(/^submitted reviewer label: [^\n]*(?:\n|$)/, "")
    .split("\n").filter((line) => !/^(仅批准合成演示闭环|确认已阅读安全占位输出边界|仅批准远程单样本推理记录)/.test(line)).join("\n").trim();
  const reviewerLabel = ["api-key:reviewer", "合成演示复核员", "合成整改复验员"].includes(reviewer) ? "复核员" : reviewer;

  return <article className="fm-paper fm-report-paper" aria-label="核验报告正文">
    <header>
      <p>{PRODUCT.name}　/　工程交付记录</p>
      <h2 className="mt-2">工程核验报告</h2>
      <small>{reportState}　·　{recordTime(report.created_at)}</small>
    </header>
    <div className="fm-report-summary">
      <p>{remediation.resolution_decision === "resolved" ? "本轮复验通过。" : remediation.resolution_decision === "not_resolved"
        ? "本轮复验后继续整改。" : `规则初判为「${verdict ? labelRuleVerdict(verdict) : "未形成规则判定"}」，人工复核结论为「${reviewDecision ?? "待确认"}」。`}</p>
    </div>
    <section>
      <h3>工程信息</h3>
      <RecordMeta entries={[
        ["所属项目", title],
        ["项目编号", recordText(storedProject.code) || project?.code || "—"],
        ["施工地点", recordText(storedProject.location) || project?.location || "—"],
        ["工点", recordText(baseline.site_id) || "—"],
        ["工序", recordProcedure(recordText(baseline.procedure_code)) || "—"],
        ["设计版本", recordText(baseline.version) || "—"],
      ]} />
    </section>
    <section>
      <h3>人工复核</h3>
      <RecordMeta entries={[
        ["复核结论", reviewDecision ?? "—"],
        ["复核人", reviewerLabel || "—"],
        ["复核时间", recordTime(recordText(review.reviewed_at))],
      ]} />
      {reviewNote ? <p className="mt-2 whitespace-pre-line">{reviewNote}</p> : null}
    </section>
    <section>
      <h3>现场资料</h3>
      <RecordMeta entries={[
        ["影像文件", recordText(evidence.original_name) || "—"],
        ["采集时间", recordTime(recordText(evidence.captured_at))],
        ["关联采集记录", sensors.length],
      ]} />
    </section>
    {!isRoutine && (analysisSummary || observations.length > 0) ? <section>
      <h3>核验记录</h3>
      {analysisSummary ? <p>{analysisSummary}</p> : null}
      {observations.length ? <ul className="mt-2 list-disc space-y-1 pl-4">{observations.map((item, index) => (
        <li key={recordText(item.id) || index}>{recordText(item.message)}</li>
      ))}</ul> : null}
    </section> : null}
  </article>;
}

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("report") || "";
  const [reports, setReports] = useState<Report[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectFilter, setProjectFilter] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [view, setView] = useState<"reader" | "structured">("reader");
  const [download, setDownload] = useState<"json" | "html" | "">("");
  const [downloadError, setDownloadError] = useState<{ id: string; message: string } | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void Promise.allSettled([api.listReports(), api.listProjects()]).then(([reportValues, projectValues]) => {
      if (!active) return;
      if (reportValues.status === "fulfilled") setReports(reportValues.value);
      else setError(recordError(reportValues.reason, "报告列表读取失败。"));
      if (projectValues.status === "fulfilled") setProjects(projectValues.value);
      setLoading(false);
    });
    return () => { active = false; };
  }, [reload]);

  const projectById = new Map(projects.map((project) => [project.id, project]));
  const keyword = query.trim().toLocaleLowerCase();
  const matching = reports.filter((report) => {
    if (projectFilter && report.project_id !== projectFilter) return false;
    if (!keyword) return true;
    const { project, baseline, evidence } = reportParts(report);
    const currentName = projectById.get(report.project_id)?.name || recordText(project.name);
    return [report.id, report.job_id, currentName, projectDisplayName(currentName), recordText(project.name), recordText(project.code),
      recordText(baseline.site_id), recordText(evidence.original_name)].some((value) => value?.toLocaleLowerCase().includes(keyword));
  });
  const selected = matching.find(({ id }) => id === selectedId) ?? (!selectedId || projectFilter || keyword ? matching[0] : undefined);
  const selectedTruth = selected ? reportTruthFromReport(selected) : null;
  const chooseReport = (id: string) => {
    setView("reader");
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set("report", id);
      else next.delete("report");
      return next;
    }, { replace: true });
  };

  async function downloadSelected(format: "json" | "html") {
    if (!selected || download) return;
    const id = selected.id;
    setDownload(format);
    setDownloadError(null);
    try { await api.downloadReport(id, format); }
    catch (reason) { setDownloadError({ id, message: recordError(reason, "报告下载失败。") }); }
    finally { setDownload(""); }
  }

  return <div className="fm-records fm-report-page page-enter">
    {error ? <div data-print-hidden><RecordError message={error} /></div> : null}
    <div className="fm-record-split">
      <section className="fm-panel" aria-label="报告清单" aria-busy={loading} data-print-hidden>
        <div className="fm-record-section-head">
          <h2>报告清单 <span className="fm-record-muted">· {matching.length}</span></h2>
          <button type="button" className="fm-button" title="刷新报告" aria-label="刷新报告" disabled={loading} onClick={() => setReload((value) => value + 1)}>
            <RefreshCw size={15} aria-hidden="true" />
          </button>
        </div>
        <div className="fm-report-filters">
          <label className="fm-record-search">
            <Search size={15} aria-hidden="true" />
            <input className="fm-input" aria-label="搜索报告" placeholder="搜索项目、工点、文件" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <select className="fm-input min-w-0" aria-label="按项目筛选报告" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
            <option value="">全部项目</option>
            {projects.map((project) => <option key={project.id} value={project.id}>{projectDisplayName(project.name)}</option>)}
          </select>
        </div>
        {matching.length ? <ul className="fm-record-list">
          {matching.map((report) => {
            const { content, project, baseline } = reportParts(report);
            const name = projectDisplayName(projectById.get(report.project_id)?.name || recordText(project.name) || "工程核验报告");
            const attempt = recordObject(recordObject(content.remediation_context).attempt);
            const reportKind = typeof attempt.attempt_no === "number" ? `第 ${attempt.attempt_no} 轮复验` : "施工核验";
            return <li key={report.id}><button type="button" className="fm-record-row" aria-pressed={selected?.id === report.id} onClick={() => chooseReport(report.id)}>
              <FileText size={19} strokeWidth={1.55} aria-hidden="true" />
              <span>
                <span className="fm-record-name">{name}</span>
                <small>{[reportKind, recordText(baseline.site_id), recordTime(report.created_at, true)].filter(Boolean).join(" · ")}</small>
                <span className="mt-1 block"><ReportStatus value={report.status} /></span>
              </span>
              <ArrowUpRight size={14} aria-hidden="true" />
            </button></li>;
          })}
        </ul> : <RecordEmpty loading={loading}>{loading ? "正在读取报告…" : error ? "报告清单暂不可用" : "没有符合条件的报告"}</RecordEmpty>}
      </section>

      <section className="fm-panel fm-report-preview" aria-label="报告预览">
        <div className="fm-record-section-head" data-print-hidden>
          <h2>报告预览</h2>
          <div className="fm-record-actions">
            <button type="button" className="fm-button" disabled={!selected || !!download} onClick={() => void downloadSelected("json")}>
              <Download size={15} aria-hidden="true" /> {download === "json" ? "下载中…" : "下载 JSON"}
            </button>
            <button type="button" className="fm-button" disabled={!selected || !!download} onClick={() => void downloadSelected("html")}>
              <Download size={15} aria-hidden="true" /> {download === "html" ? "下载中…" : "下载封存版"}
            </button>
            <button type="button" className="fm-button fm-button-primary" disabled={!selected || view !== "reader"} onClick={() => window.print()}>
              <Printer size={15} aria-hidden="true" />打印报告
            </button>
          </div>
        </div>
        {selected && selectedTruth ? <div className="fm-report-surface">
          {downloadError?.id === selected.id ? <div className="mb-3" data-print-hidden><RecordError message={downloadError.message} /></div> : null}
          <div className="fm-record-segments fm-report-view" role="group" aria-label="报告显示方式" data-print-hidden>
            <button type="button" aria-pressed={view === "reader"} onClick={() => setView("reader")}>阅读报告</button>
            <button type="button" aria-pressed={view === "structured"} onClick={() => setView("structured")}>结构化记录</button>
          </div>
          {view === "reader"
            ? <ReportPaper report={selected} project={projectById.get(selected.project_id)} />
            : <pre className="fm-report-code" aria-label="结构化报告" tabIndex={0}>{JSON.stringify(selected.content, null, 2)}</pre>}
          <details className="fm-record-details mt-3" data-print-hidden key={selected.id}>
            <summary>报告详情</summary>
            <RecordMeta entries={[
              ["报告编号", selected.id],
              ["报告版本", selected.schema_version],
              ["报告摘要值", <code>{selected.sha256}</code>],
              ["证据等级", selectedTruth.evidenceGrade ? "评测证据" : "非评测资料"],
              ["准确率声明", selectedTruth.accuracyClaimPresent ? "已登记" : "未登记"],
              ["核验任务", <Link className="fm-link" to={`/backend-workflow?job=${encodeURIComponent(selected.job_id)}`}>查看原核验记录</Link>],
            ]} />
          </details>
        </div> : <div data-print-hidden><RecordEmpty loading={loading}>{loading ? "正在读取报告…" : selectedId && !projectFilter && !keyword ? "未找到指定报告，请在左侧重新选择。" : "选择报告查看正文"}</RecordEmpty></div>}
      </section>
    </div>
  </div>;
}
