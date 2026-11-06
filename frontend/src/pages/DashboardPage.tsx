import { useEffect, useState } from "react";
import { Link } from "react-router";
import { RefreshCw } from "lucide-react";
import { api, type CapabilityMeta, type DashboardSummary, type Project, type Proof, type VerificationJob } from "../lib/api";
import { BlockchainIcon, ChevronRightIcon, MapIcon, ShieldIcon } from "../components/Icons";
import { labelJobStatus, labelProjectStatus, archiveTitle, projectDisplayName } from "../lib/productCopy";
import { isTrialJob, isTrialProject } from "../lib/truth";
import { serviceTimestamp } from "../lib/serviceTime";
import { Notice } from "../components/ui/Notice";
import "./dashboard.css";

type Overview = { summary: DashboardSummary; projects: Project[]; jobs: VerificationJob[]; proofs: Proof[]; meta: CapabilityMeta | null };

const time = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

function formatTime(value: string) {
  const date = new Date(serviceTimestamp(value));
  return Number.isNaN(date.getTime()) ? "—" : time.format(date);
}

function jobTone(status: VerificationJob["status"]) {
  if (status === "approved") return "success";
  if (status === "needs_review") return "amber";
  if (status === "rejected" || status === "failed") return "danger";
  return "neutral";
}

export function DashboardPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filter, setFilter] = useState<"all" | "pending">("all");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    Promise.all([api.dashboardSummary(), api.listProjects(), api.listVerifications(), api.listProofs(), api.meta().catch(() => null)])
      .then(([summary, projects, jobs, proofs, meta]) => {
        if (active) setData({ summary, projects, jobs, proofs, meta });
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  const summary = data?.summary;
  const projectsById = new Map(data?.projects.map((project) => [project.id, project]));
  const scopedJobs = (data?.jobs ?? []).filter((job) => !isTrialJob(job, data?.meta) && !isTrialProject(projectsById.get(job.project_id)));
  const pendingJobs = scopedJobs.filter((job) => job.status === "needs_review");
  const jobs = (filter === "pending" ? pendingJobs : scopedJobs).slice(0, 6);
  const archives = data?.proofs.slice(0, 5) ?? [];

  return (
    <div className="fm-overview page-enter">
      {error && <Notice type="error" message={data ? "刷新未完成，当前显示上次读取的数据。" : "总览暂时无法加载，请重试。"} />}

      <section className="fm-overview-hero" aria-labelledby="overview-heading">
        <div className="fm-overview-hero-content">
          <p className="fm-overview-kicker"><span aria-hidden="true" /> 施工监管工作台</p>
          <h2 id="overview-heading">从工程现场到交付档案</h2>
          <p className="fm-overview-subtitle">定位工程对象，处理核验与整改，查阅交付记录。</p>
          <div className="fm-overview-actions">
            <Link to="/gis-map" className="fm-button fm-button-primary"><MapIcon className="size-4" /> 进入工程作业</Link>
            <Link to="/backend-workflow" className="fm-button"><ShieldIcon className="size-4" /> 核验中心</Link>
          </div>
        </div>
        <dl className="fm-overview-metrics">
          <div><dt>工程项目</dt><dd>{summary?.projects ?? "—"}</dd></div>
          <div data-tone="amber"><dt>待复核任务</dt><dd>{data ? pendingJobs.length : "—"}</dd></div>
          <div data-tone="danger"><dt>待整改案件</dt><dd>{summary?.finding_cases.confirmed_open_operational ?? "—"}</dd></div>
          <div data-tone="success"><dt>归档记录</dt><dd>{summary?.proof_archives ?? "—"}</dd></div>
        </dl>
      </section>

      <div className="fm-overview-grid">
        <section className="fm-panel fm-overview-section" aria-labelledby="overview-jobs" aria-busy={loading}>
          <div className="fm-overview-section-heading">
            <div><h2 id="overview-jobs">核验任务</h2><p>最近提交的现场资料</p></div>
            <button type="button" className="fm-button" onClick={() => setRefresh((value) => value + 1)} disabled={loading}>
              <RefreshCw aria-hidden className="size-4" />{loading ? "读取中…" : "刷新"}
            </button>
          </div>
          <div className="fm-overview-tabs" aria-label="筛选核验任务">
            <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>最近任务</button>
            <button type="button" aria-pressed={filter === "pending"} onClick={() => setFilter("pending")}>待复核</button>
            <Link to={filter === "pending" ? "/backend-workflow?status=needs_review" : "/backend-workflow"} className="fm-link">查看全部 <ChevronRightIcon className="size-3.5" /></Link>
          </div>
          {jobs.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="fm-overview-table">
                <thead><tr><th scope="col">项目</th><th scope="col">任务状态</th><th scope="col">提交时间</th><th scope="col">操作</th></tr></thead>
                <tbody>{jobs.map((job) => (
                  <tr key={job.id}>
                    <td><span className="fm-overview-project-title">{projectDisplayName(projectsById.get(job.project_id)?.name ?? "工程项目")}</span></td>
                    <td><span className="fm-overview-status" data-tone={jobTone(job.status)}>{labelJobStatus(job.status)}</span></td>
                    <td><time dateTime={job.created_at}>{formatTime(job.created_at)}</time></td>
                    <td><Link to={`/backend-workflow?job=${encodeURIComponent(job.id)}`} className="fm-link">查看任务</Link></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="fm-overview-empty" role="status">{loading ? "正在读取核验任务…" : error && !data ? "核验任务暂不可用" : filter === "pending" ? "暂无待复核任务" : "暂无核验任务"}</p>}
        </section>

        <section className="fm-panel fm-overview-section" aria-labelledby="overview-archives">
          <div className="fm-overview-section-heading">
            <div><h2 id="overview-archives">交付档案</h2><p>最近封存的交付记录</p></div>
            <Link to="/traceability" className="fm-link">审计追溯</Link>
          </div>
          {archives.length ? <ul className="fm-overview-archives">
            {archives.map((archive) => (
              <li key={archive.id}>
                <Link to={`/traceability?archive=${encodeURIComponent(archive.archive_id)}`}>
                  <BlockchainIcon className="size-[18px] shrink-0" />
                  <span><strong>{archiveTitle(archive.ledger_index)}</strong><time dateTime={archive.created_at}>{formatTime(archive.created_at)}</time></span>
                  <ChevronRightIcon className="size-4 shrink-0" />
                </Link>
              </li>
            ))}
          </ul> : <p className="fm-overview-empty" role="status">{loading ? "正在读取档案…" : error && !data ? "档案暂不可用" : "暂无已封存档案"}</p>}
          <div className="fm-overview-section-footer"><Link to="/alarms" className="fm-link">进入整改中心 <ChevronRightIcon className="size-4" /></Link></div>
        </section>
      </div>

      <section className="fm-panel fm-overview-section" aria-labelledby="overview-projects">
        <div className="fm-overview-section-heading">
          <div><h2 id="overview-projects">近期项目</h2><p>项目地点、负责人和施工状态</p></div>
          <Link to="/projects" className="fm-link">项目管理 <ChevronRightIcon className="size-4" /></Link>
        </div>
        {data?.projects.length ? <div className="overflow-x-auto">
          <table className="fm-overview-table">
            <thead><tr><th scope="col">项目</th><th scope="col">地点</th><th scope="col">负责人</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
            <tbody>{data.projects.slice(0, 4).map((project) => (
              <tr key={project.id}>
                <td className="fm-overview-project-title">{projectDisplayName(project.name)}</td>
                <td>{project.location || "—"}</td>
                <td>{project.manager || "—"}</td>
                <td><span className="fm-overview-status" data-tone={project.status === "active" ? "success" : "neutral"}>{labelProjectStatus(project.status)}</span></td>
                <td><Link to={`/projects/${project.id}`} className="fm-link">项目详情</Link></td>
              </tr>
            ))}</tbody>
          </table>
        </div> : <p className="fm-overview-empty" role="status">{loading ? "正在读取项目…" : error ? "项目暂不可用" : "暂无项目记录"}</p>}
      </section>
    </div>
  );
}
