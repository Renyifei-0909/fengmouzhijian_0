import React, { useEffect, useMemo, useState } from "react";
import { api, Project, Report } from "../lib/api";
import { EyeIcon, PrintIcon, DownloadIcon, DatabaseIcon, ShieldIcon } from "../components/Icons";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";
import { TruthBadge } from "../components/ui/TruthStatus";
import { reportTruthFromReport } from "../lib/truth";

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const reportSummary = (report: Report): string => {
  const review = isRecord(report.content.human_review) ? report.content.human_review : null;
  const analysis = isRecord(report.content.analysis) ? report.content.analysis : null;
  const evidence = isRecord(report.content.evidence) ? report.content.evidence : null;
  const candidates = [review?.note, analysis?.summary, analysis?.conclusion];
  const persistedSummary = candidates.find((value) => typeof value === "string" && value.trim().length > 0);
  if (typeof persistedSummary === "string") return persistedSummary.trim();
  if (typeof evidence?.original_name === "string" && evidence.original_name.trim()) {
    return `原始证据：${evidence.original_name.trim()}`;
  }
  return reportTruthFromReport(report).description;
};

export const ReportsPage: React.FC = () => {
  const [reports, setReports] = useState<Report[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selected = reports.find((item) => item.id === selectedId) || null;
  const selectedTruth = useMemo(() => selected ? reportTruthFromReport(selected) : null, [selected]);
  const projectNames = useMemo(() => Object.fromEntries(projects.map((item) => [item.id, item.name])), [projects]);

  useEffect(() => {
    Promise.all([api.listReports(), api.listProjects()])
      .then(([reportValues, projectValues]) => { setReports(reportValues); setProjects(projectValues); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "报告列表加载失败"))
      .finally(() => setLoading(false));
  }, []);

  return <div className="space-y-5 page-enter">
    {error ? <Notice type="info" message={error} /> : null}
    <section className="rounded-[30px] border border-sky-100 bg-gradient-to-br from-white to-sky-50 p-6 shadow-sm">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between"><div><div className="inline-flex items-center gap-2 rounded-full bg-sky-100 px-3 py-1 text-xs font-semibold text-sky-700"><DatabaseIcon className="h-4 w-4" /> 后端结构化报告库</div><h2 className="mt-4 text-2xl font-semibold text-slate-900">从复核结论到可下载交付物</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">这里只展示服务端生成并封存的报告制品；制品真实存在不等于算法已经评测。JSON 与 HTML 下载前都会重新校验 SHA-256。</p></div><div className="rounded-3xl border border-sky-100 bg-white px-6 py-4 text-center shadow-sm"><p className="text-[10px] tracking-[.2em] text-sky-600">SEALED REPORTS</p><p className="mt-1 text-3xl font-semibold text-slate-900">{reports.length}</p></div></div>
    </section>

    <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h3 className="text-base font-semibold text-slate-900">报告清单</h3><p className="mt-1 text-sm text-slate-500">预览内容来自后端 `content` 字段，不使用静态报表模板。</p></div><button type="button" onClick={() => window.print()} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 sm:w-auto"><PrintIcon className="h-4 w-4" /> 打印当前页</button></div>

    {loading ? <div className="rounded-[28px] border border-slate-200 bg-white p-12 text-center text-sm text-slate-500">正在读取报告…</div> : null}
    {!loading && reports.length === 0 ? <div className="rounded-[28px] border border-dashed border-slate-300 bg-white p-12 text-center"><ShieldIcon className="mx-auto h-8 w-8 text-slate-300" /><p className="mt-3 text-sm font-semibold text-slate-700">暂无已封存报告</p><p className="mt-1 text-xs text-slate-500">在真实闭环页面批准一个任务后，报告会出现在这里。</p></div> : null}

    {reports.length > 0 ? <>
      <section aria-label="移动端报告清单" className="grid gap-3 md:hidden">{reports.map((report) => {
        const truth = reportTruthFromReport(report);
        return <article key={report.id} className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-slate-900">服务端封存报告</p>
            <TruthBadge truth={truth} />
          </div>
          <div className="mt-3 rounded-2xl border border-slate-100 bg-slate-50 px-3 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-sky-600">报告摘要</p>
            <p className="mt-2 max-h-14 overflow-hidden text-xs leading-5 text-slate-600">{reportSummary(report)}</p>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="col-span-2 rounded-2xl bg-sky-50/70 px-3 py-2.5"><dt className="text-[10px] text-slate-400">所属项目</dt><dd className="mt-1 break-words font-medium text-slate-700">{projectNames[report.project_id] || report.project_id}</dd></div>
            <div className="rounded-2xl bg-slate-50 px-3 py-2.5"><dt className="text-[10px] text-slate-400">生成时间</dt><dd className="mt-1 leading-5 text-slate-700">{new Date(report.created_at).toLocaleString("zh-CN")}</dd></div>
            <div className="rounded-2xl bg-slate-50 px-3 py-2.5"><dt className="text-[10px] text-slate-400">Schema</dt><dd className="mt-1 font-medium text-slate-700">v{report.schema_version}</dd></div>
          </dl>
          <div className="mt-4 grid grid-cols-2 gap-2 border-t border-slate-100 pt-4">
            <button type="button" onClick={() => setSelectedId(report.id)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-2xl border border-sky-200 bg-white px-3 text-sm font-medium text-sky-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"><EyeIcon className="h-4 w-4" /> 预览报告</button>
            <button type="button" onClick={() => void api.downloadReport(report.id, "json").catch((reason) => setError(reason instanceof Error ? reason.message : "JSON 报告下载失败"))} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-2xl bg-sky-600 px-3 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"><DownloadIcon className="h-4 w-4" /> 下载 JSON</button>
          </div>
        </article>;
      })}</section>
      <div className="hidden overflow-x-auto rounded-[28px] border border-slate-200 bg-white shadow-sm md:block"><table className="min-w-[880px] w-full text-left text-sm"><thead><tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600"><th className="px-4 py-3 font-medium">报告 / 摘要</th><th className="px-4 py-3 font-medium">所属项目</th><th className="px-4 py-3 font-medium">Schema</th><th className="px-4 py-3 font-medium">生成时间</th><th className="px-4 py-3 font-medium">真实性状态</th><th className="px-4 py-3 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-slate-100">{reports.map((report) => {
      const truth = reportTruthFromReport(report);
      return <tr key={report.id} className="hover:bg-slate-50"><td className="px-4 py-4"><p className="text-sm font-semibold text-slate-900">服务端封存报告</p><p className="mt-1 max-w-72 truncate font-mono text-[11px] text-slate-500">{report.sha256}</p></td><td className="px-4 py-4 text-xs text-slate-600">{projectNames[report.project_id] || report.project_id}</td><td className="px-4 py-4 text-xs text-slate-600">v{report.schema_version}</td><td className="px-4 py-4 text-xs text-slate-600">{new Date(report.created_at).toLocaleString("zh-CN")}</td><td className="px-4 py-4"><TruthBadge truth={truth} /><p className="mt-1 text-[10px] text-slate-400">{report.status}</p></td><td className="px-4 py-4"><div className="flex justify-end gap-2"><button aria-label="预览报告" onClick={() => setSelectedId(report.id)} className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 hover:text-sky-700"><EyeIcon className="h-4 w-4" /></button><button aria-label="下载 JSON 报告" onClick={() => void api.downloadReport(report.id, "json").catch((reason) => setError(reason.message))} className="flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-600 text-white"><DownloadIcon className="h-4 w-4" /></button></div></td></tr>;
      })}</tbody></table></div>
    </> : null}

    <Modal open={!!selected} onClose={() => setSelectedId(null)} title="服务端封存报告预览" description={selectedTruth?.description || "以下 JSON 来自服务端封存内容。"} footer={<div className="flex flex-col gap-3 sm:flex-row sm:justify-end"><button type="button" onClick={() => setSelectedId(null)} className="w-full rounded-2xl border border-slate-200 px-4 py-2 text-sm sm:w-auto">关闭</button>{selected ? <><button type="button" onClick={() => void api.downloadReport(selected.id, "json").catch((reason) => setError(reason instanceof Error ? reason.message : "JSON 报告下载失败"))} className="w-full rounded-2xl border border-sky-200 px-4 py-2 text-sm text-sky-700 sm:w-auto">下载 JSON</button><button type="button" onClick={() => void api.downloadReport(selected.id, "html").catch((reason) => setError(reason instanceof Error ? reason.message : "HTML 报告下载失败"))} className="w-full rounded-2xl bg-sky-600 px-4 py-2 text-sm text-white sm:w-auto">下载 HTML</button></> : null}</div>}>
      {selected && selectedTruth ? <div className="space-y-3"><div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4"><TruthBadge truth={selectedTruth} /><p className="mt-3 text-xs leading-5 text-slate-600">{selectedTruth.description}</p><div className="mt-3 flex flex-wrap gap-2 text-[11px]"><span className="rounded-full bg-white px-2.5 py-1 text-slate-600">证据等级：{selectedTruth.evidenceGrade ? "评测证据" : "非指标证据"}</span><span className="rounded-full bg-white px-2.5 py-1 text-slate-600">准确率声明：{selectedTruth.accuracyClaimPresent ? "已提供，需核验" : "无"}</span></div></div><div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4"><p className="text-xs text-slate-500">报告 SHA-256</p><p className="mt-2 break-all font-mono text-[11px] text-slate-700">{selected.sha256}</p></div><pre className="max-h-[420px] overflow-auto rounded-[22px] bg-[#07172b] p-4 text-[11px] leading-5 text-cyan-100">{JSON.stringify(selected.content, null, 2)}</pre></div> : null}
    </Modal>
  </div>;
};
