import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { ArrowRight, FolderOpen, MapPinned, Plus, RefreshCw, Search } from "lucide-react";
import { api, ApiRequestError } from "../lib/api";
import type { Project, ProjectProgress } from "../lib/api";
import { labelProjectStatus, projectDisplayName } from "../lib/productCopy";
import { Modal } from "../components/ui/Modal";
import { RecordEmpty, RecordError, RecordMeta, RecordMetrics, RecordStatus, recordError, recordTime } from "./records-ui";
import type { RecordTone } from "./records-ui";

const filters = ["all", "active", "pending", "paused", "completed"] as const;
type ProjectFilter = typeof filters[number];

const statusTones: Record<string, RecordTone> = { active: "green", pending: "amber", completed: "blue" };
const initialForm = { code: "", name: "", location: "", manager: "" };
const fields: Array<{ name: keyof typeof initialForm; label: string; required?: boolean }> = [
  { name: "code", label: "项目编号", required: true },
  { name: "name", label: "项目名称", required: true },
  { name: "location", label: "施工地点", required: true },
  { name: "manager", label: "负责人" },
];

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [progress, setProgress] = useState<Record<string, ProjectProgress>>({});
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [filter, setFilter] = useState<ProjectFilter>("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState("");
  const [progressError, setProgressError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setProgressError("");
    void api.listProjects().then(async (items) => {
      if (!active) return;
      setProjects(items);
      const entries = await Promise.allSettled(items.map(async ({ id }) => [id, await api.projectProgress(id)] as const));
      if (!active) return;
      setProgress(Object.fromEntries(entries.flatMap((entry) => entry.status === "fulfilled" ? [entry.value] : [])));
      const failure = entries.find((entry) => entry.status === "rejected");
      if (failure) setProgressError(recordError(failure.reason, "部分项目进度暂不可用。"));
    }).catch((reason) => {
      if (active) setError(recordError(reason, "项目列表读取失败。"));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [reload]);

  const keyword = query.trim().toLocaleLowerCase();
  const matching = projects.filter((project) =>
    (filter === "all" || project.status === filter) &&
    (!keyword || [project.name, projectDisplayName(project.name), project.code, project.location, project.manager].some((value) => value?.toLocaleLowerCase().includes(keyword))));
  const selected = matching.find(({ id }) => id === selectedId) ?? matching[0];
  const selectedProgress = selected && progress[selected.id];
  const projectsReady = !error && (!loading || projects.length > 0);
  const aggregateReady = !loading && !error && !progressError;
  const progressValues = Object.values(progress);

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const [code, name, location, manager] = [form.code, form.name, form.location, form.manager].map((value) => value.trim());
    if (!code || !name || !location) {
      setFormError("请填写项目编号、项目名称和施工地点。");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const created = await api.createProject({ code, name, location, manager: manager || undefined });
      setProjects((items) => [created, ...items.filter(({ id }) => id !== created.id)]);
      setSelectedId(created.id);
      setFilter("all");
      setQuery("");
      setCreateOpen(false);
      setForm(initialForm);
      setNotice(`已创建项目「${projectDisplayName(created.name)}」。`);
      setReload((value) => value + 1);
    } catch (reason) {
      setFormError(reason instanceof ApiRequestError && reason.status === 409
        ? "项目编号已存在，请更换编号。"
        : recordError(reason, "项目创建失败。"));
    } finally {
      setSaving(false);
    }
  }

  return <div className="fm-records fm-projects-page page-enter">
    <section className="fm-panel fm-record-banner" aria-label="项目汇总">
      <div className="fm-record-toolbar">
        <div className="flex items-center gap-3">
          <FolderOpen size={24} className="shrink-0 text-sky-300" aria-hidden="true" />
          <div><h2 className="!mt-0">工程项目台账</h2><p className="mt-1 fm-record-muted">按项目查看设计、核验与交付记录</p></div>
        </div>
        <button className="fm-button fm-button-primary" type="button" onClick={() => { setFormError(""); setCreateOpen(true); }}>
          <Plus size={16} aria-hidden="true" />新建项目
        </button>
      </div>
      <RecordMetrics entries={[
        { label: "全部项目", value: projectsReady ? projects.length : "—" },
        { label: "进行中", value: projectsReady ? projects.filter(({ status }) => status === "active").length : "—" },
        { label: "设计基线", value: aggregateReady ? progressValues.reduce((sum, entry) => sum + entry.baseline_count, 0) : "—" },
        { label: "待复核", value: aggregateReady ? progressValues.reduce((sum, entry) => sum + entry.pending_review_count, 0) : "—" },
      ]} />
    </section>

    {notice ? <p className="fm-record-muted text-emerald-300" role="status">{notice}</p> : null}
    {error ? <RecordError message={error} /> : null}

    <div className="fm-record-split">
      <section className="fm-panel" aria-label="项目清单" aria-busy={loading}>
        <div className="fm-record-section-head">
          <h3>项目清单 <span className="fm-record-muted">· {matching.length}</span></h3>
          <div className="fm-record-actions">
            <label className="fm-record-search">
              <Search size={15} aria-hidden="true" />
              <input className="fm-input" aria-label="搜索工程项目" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、编号、地点" />
            </label>
            <button type="button" className="fm-button" aria-label="刷新项目" title="刷新项目" disabled={loading} onClick={() => setReload((value) => value + 1)}>
              <RefreshCw size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
        <div className="fm-record-segments" role="group" aria-label="项目状态">
          {filters.map((status) => <button key={status} type="button" aria-pressed={status === filter} onClick={() => setFilter(status)}>
            {status === "all" ? "全部" : labelProjectStatus(status)}
          </button>)}
        </div>
        {progressError ? <p className="border-t border-slate-200 px-4 py-2 text-xs text-amber-300">部分项目的进度暂不可用。</p> : null}
        {matching.length ? <div className="fm-record-table-wrap">
          <table className="fm-record-table min-w-[520px]">
            <thead><tr><th scope="col">项目名称</th><th scope="col">负责人</th><th scope="col">状态</th><th scope="col">基线覆盖率</th><th scope="col">操作</th></tr></thead>
            <tbody>
              {matching.map((project) => {
                const item = progress[project.id];
                const name = projectDisplayName(project.name);
                return <tr key={project.id} data-active={selected?.id === project.id}>
                  <td><button type="button" className="fm-record-name" aria-pressed={selected?.id === project.id} onClick={() => setSelectedId(project.id)}>{name}</button>
                    <small>{project.code} · {project.location}</small></td>
                  <td className="whitespace-nowrap">{project.manager || "未指定"}</td>
                  <td><RecordStatus tone={statusTones[project.status]}>{labelProjectStatus(project.status)}</RecordStatus></td>
                  <td>{item ? <><span className="fm-record-numeric">{item.completion_rate}%</span>
                    <progress className="fm-record-meter mt-1.5" value={item.completion_rate} max={100} aria-label={`${name}的已批准基线覆盖率`} /></> : "—"}</td>
                  <td><Link className="fm-link inline-flex items-center gap-1 whitespace-nowrap" to={`/projects/${project.id}`} aria-label={`查看${name}详情`}>详情 <ArrowRight size={14} aria-hidden="true" /></Link></td>
                </tr>;
              })}
            </tbody>
          </table>
        </div> : <RecordEmpty loading={loading}>{loading ? "正在读取项目…" : error ? "项目清单暂不可用" : "没有符合条件的项目"}</RecordEmpty>}
      </section>

      <aside className="fm-panel" aria-label="所选项目">
        <div className="fm-record-section-head"><h3>项目概况</h3></div>
        {selected ? <div className="fm-record-body">
          <div>
            <p className="fm-record-muted">{selected.code}</p>
            <p className="fm-record-name mt-1">{projectDisplayName(selected.name)}</p>
          </div>
          <RecordMeta entries={[
            ["施工地点", selected.location],
            ["负责人", selected.manager || "未指定"],
            ["登记时间", recordTime(selected.created_at)],
            ["设计基线", selectedProgress?.baseline_count],
            ["待复核", selectedProgress?.pending_review_count],
            ["失败或驳回", selectedProgress?.failed_or_rejected_count],
          ]} />
          {selectedProgress?.metric_note ? <details className="fm-record-details"><summary>覆盖率口径</summary><p>已批准基线占已登记基线的比例。</p></details> : null}
          <div className="fm-record-actions">
            <Link className="fm-button fm-button-primary" to={`/projects/${selected.id}`}>查看项目</Link>
            <Link className="fm-button" to={`/gis-map?project=${encodeURIComponent(selected.id)}`}><MapPinned size={15} aria-hidden="true" />现场地图</Link>
          </div>
        </div> : <RecordEmpty>选择项目查看概况</RecordEmpty>}
      </aside>
    </div>

    <Modal open={createOpen} onClose={() => { if (!saving) setCreateOpen(false); }} title="新建项目" placement="right" size="md"
      footer={<div className="fm-record-actions justify-end">
        <button type="button" className="fm-button" disabled={saving} onClick={() => setCreateOpen(false)}>取消</button>
        <button form="fm-create-project" type="submit" className="fm-button fm-button-primary" disabled={saving}>{saving ? "保存中…" : "保存项目"}</button>
      </div>}>
      <form id="fm-create-project" onSubmit={createProject} className="grid gap-4">
        {formError ? <RecordError message={formError} /> : null}
        {fields.map(({ name, label, required }) => <label key={name} className="grid gap-1.5">
          <span className="fm-label">{label}{required ? "" : "（选填）"}</span>
          <input className="fm-input" required={required} disabled={saving} value={form[name]} onChange={(event) => setForm((current) => ({ ...current, [name]: event.target.value }))} />
        </label>)}
      </form>
    </Modal>
  </div>;
}
