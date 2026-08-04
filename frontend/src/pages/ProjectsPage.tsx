import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api, Project, ProjectProgress } from "../lib/api";
import { ChevronRightIcon, CameraIcon, ShieldIcon, InfoIcon, XIcon, FilterIcon, DatabaseIcon } from "../components/Icons";
import { cn } from "../utils/cn";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";

type StatusFilter = "all" | "active" | "pending" | "paused" | "completed";

const statusStyles: Record<string, { bg: string; text: string; label: string }> = {
  active: { bg: "bg-emerald-100", text: "text-emerald-700", label: "进行中" },
  pending: { bg: "bg-amber-100", text: "text-amber-700", label: "待启动" },
  paused: { bg: "bg-slate-100", text: "text-slate-700", label: "已暂停" },
  completed: { bg: "bg-indigo-100", text: "text-indigo-700", label: "已完成" },
};

const statusTabs: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "active", label: "进行中" },
  { value: "pending", label: "待启动" },
  { value: "paused", label: "已暂停" },
  { value: "completed", label: "已完成" },
];

export const ProjectsPage: React.FC = () => {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [progress, setProgress] = useState<Record<string, ProjectProgress>>({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [showFilter, setShowFilter] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ code: "", name: "", location: "", manager: "" });

  const loadProjects = async () => {
    setLoading(true);
    setError("");
    try {
      const items = await api.listProjects();
      setProjects(items);
      const entries = await Promise.all(
        items.map(async (item) => [item.id, await api.projectProgress(item.id)] as const)
      );
      setProgress(Object.fromEntries(entries));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目数据加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadProjects(); }, []);

  const filtered = useMemo(
    () => (filter === "all" ? projects : projects.filter((item) => item.status === filter)),
    [filter, projects]
  );

  const submitCreate = async () => {
    if (!form.code.trim() || !form.name.trim() || !form.location.trim()) {
      setError("请填写项目代码、项目名称和施工地点。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const created = await api.createProject({
        code: form.code.trim(),
        name: form.name.trim(),
        location: form.location.trim(),
        manager: form.manager.trim() || undefined,
      });
      setCreateOpen(false);
      setNotice(`项目 ${created.code} 已写入后端数据库。`);
      setForm({ code: "", name: "", location: "", manager: "" });
      await loadProjects();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目创建失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      {error ? <Notice type="info" message={error} /> : null}

      <section className="overflow-hidden rounded-[30px] border border-sky-300/20 bg-[#07172b] px-6 py-6 text-white shadow-[0_26px_90px_-50px_rgba(3,105,161,.9)]">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs text-cyan-100"><DatabaseIcon className="h-4 w-4" /> 后端实时项目库</div>
            <h2 className="mt-4 text-2xl font-semibold">工程语义与交付进度总账</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">以下项目、基线数量和已批准基线覆盖率均来自当前 SQLite 持久化数据，不再使用静态项目卡片；覆盖率仅为代理指标。</p>
          </div>
          <div className="grid grid-cols-3 gap-2 rounded-3xl border border-white/10 bg-white/5 p-3">
            <div className="rounded-2xl bg-slate-950/30 px-4 py-3"><p className="text-[10px] tracking-[.18em] text-sky-300">PROJECTS</p><p className="mt-1 text-xl font-semibold">{projects.length}</p></div>
            <div className="rounded-2xl bg-slate-950/30 px-4 py-3"><p className="text-[10px] tracking-[.18em] text-sky-300">BASELINES</p><p className="mt-1 text-xl font-semibold">{Object.values(progress).reduce((sum, item) => sum + item.baseline_count, 0)}</p></div>
            <div className="rounded-2xl bg-slate-950/30 px-4 py-3"><p className="text-[10px] tracking-[.18em] text-sky-300">REVIEW</p><p className="mt-1 text-xl font-semibold">{Object.values(progress).reduce((sum, item) => sum + item.pending_review_count, 0)}</p></div>
          </div>
        </div>
      </section>

      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div><h3 className="text-base font-semibold text-slate-900">项目列表</h3><p className="mt-1 text-sm text-slate-500">已批准基线覆盖率是后端代理指标，不等同于施工总进度。</p></div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowFilter((value) => !value)} className={cn("inline-flex items-center gap-2 rounded-2xl border px-4 py-2 text-sm font-medium", showFilter ? "border-sky-200 bg-sky-50 text-sky-700" : "border-slate-200 bg-white text-slate-700")}><FilterIcon className="h-4 w-4" /> 筛选</button>
          <button onClick={() => setCreateOpen(true)} className="inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700"><CameraIcon className="h-4 w-4" /> 新建真实项目</button>
        </div>
      </div>

      {showFilter ? <div className="flex flex-wrap gap-2 rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">{statusTabs.map((tab) => <button key={tab.value} aria-pressed={filter === tab.value} onClick={() => setFilter(tab.value)} className={cn("rounded-full px-3 py-1.5 text-xs font-medium", filter === tab.value ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700")}>{tab.label}</button>)}<button aria-label="关闭筛选选项" onClick={() => setShowFilter(false)} className="ml-auto rounded-full p-1 text-slate-400"><XIcon className="h-4 w-4" /></button></div> : null}

      {loading ? <div className="rounded-[28px] border border-slate-200 bg-white p-12 text-center text-sm text-slate-500">正在读取后端项目数据…</div> : null}
      {!loading && filtered.length === 0 ? <div className="rounded-[28px] border border-dashed border-slate-300 bg-white p-12 text-center"><ShieldIcon className="mx-auto h-8 w-8 text-slate-300" /><p className="mt-3 text-sm font-semibold text-slate-700">当前筛选下没有项目</p></div> : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((project) => {
          const itemProgress = progress[project.id];
          const style = statusStyles[project.status] || statusStyles.pending;
          return <article key={project.id} className="group rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-sky-200 hover:shadow-md">
            <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="text-xs font-semibold tracking-[.16em] text-sky-600">{project.code}</p><h3 className="mt-2 truncate text-base font-semibold text-slate-900">{project.name}</h3><p className="mt-1 text-sm text-slate-500">{project.location}</p></div><span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", style.bg, style.text)}>{style.label}</span></div>
            <div className="mt-5 grid grid-cols-3 gap-2">{[["基线", itemProgress?.baseline_count ?? 0], ["待复核", itemProgress?.pending_review_count ?? 0], ["异常", itemProgress?.failed_or_rejected_count ?? 0]].map(([label, value]) => <div key={String(label)} className="rounded-2xl bg-slate-50 px-3 py-3"><p className="text-[11px] text-slate-400">{label}</p><p className="mt-1 text-lg font-semibold text-slate-800">{value}</p></div>)}</div>
            <div className="mt-4"><div className="mb-2 flex items-end justify-between gap-3 text-xs text-slate-500"><span className="leading-5">已批准基线覆盖率<br /><span className="text-[10px] text-slate-400">代理指标</span></span><span className="font-semibold text-sky-700">{itemProgress?.completion_rate ?? 0}%</span></div><div role="progressbar" aria-label={`${project.name}已批准基线覆盖率（代理指标）`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={itemProgress?.completion_rate ?? 0} className="h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-cyan-500" style={{ width: `${itemProgress?.completion_rate ?? 0}%` }} /></div>{itemProgress?.metric_note ? <p className="mt-2 text-[10px] leading-4 text-slate-400">后端口径：{itemProgress.metric_note}</p> : null}</div>
            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-4"><div className="flex items-center gap-2 text-xs text-slate-500"><InfoIcon className="h-3.5 w-3.5" /> {project.manager || "未指定负责人"}</div><button onClick={() => navigate(`/projects/${project.id}`)} className="inline-flex items-center gap-1 text-sm font-medium text-sky-700">真实详情 <ChevronRightIcon className="h-4 w-4" /></button></div>
          </article>;
        })}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="新建后端项目" description="保存后会立即写入当前后端数据库。" footer={<div className="flex justify-end gap-3"><button onClick={() => setCreateOpen(false)} className="rounded-2xl border border-slate-200 px-4 py-2 text-sm">取消</button><button disabled={saving} onClick={() => void submitCreate()} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300">{saving ? "保存中…" : "写入数据库"}</button></div>}>
        <div className="grid gap-4 md:grid-cols-2">{[["项目代码", "code"], ["项目名称", "name"], ["施工地点", "location"], ["负责人", "manager"]].map(([label, key]) => <label key={key} className="block"><span className="mb-2 block text-sm font-medium text-slate-700">{label}</span><input value={form[key as keyof typeof form]} onChange={(event) => setForm((value) => ({ ...value, [key]: event.target.value }))} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-sky-300 focus:bg-white" /></label>)}</div>
      </Modal>
    </div>
  );
};
