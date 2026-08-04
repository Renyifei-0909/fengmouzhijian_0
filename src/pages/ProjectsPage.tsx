import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ChevronRightIcon,
  CameraIcon,
  BellIcon,
  ShieldIcon,
  InfoIcon,
  XIcon,
  FilterIcon,
} from "../components/Icons";
import { projects } from "../data/mock";
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
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [showFilter, setShowFilter] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({ name: "", location: "", manager: "", description: "" });

  const filtered = useMemo(
    () => (filter === "all" ? projects : projects.filter((p) => p.status === filter)),
    [filter]
  );

  const submitCreate = () => {
    if (!form.name || !form.location || !form.manager) {
      setNotice("请先填写项目名称、地点和负责人后再保存。");
      return;
    }
    setCreateOpen(false);
    setNotice(`已创建演示项目：${form.name}，可继续扩展接入真实接口。`);
    setForm({ name: "", location: "", manager: "", description: "" });
  };

  return (
    <div className="space-y-5 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}

      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">全部项目</h2>
          <p className="mt-1 text-sm text-slate-500">管理、筛选并追踪所有通信基建工程进度</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilter((prev) => !prev)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-2xl border px-4 py-2 text-sm font-medium transition-colors",
              showFilter ? "border-sky-200 bg-sky-50 text-sky-700" : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
            )}
          >
            <FilterIcon className="h-4 w-4" /> 筛选
          </button>
          <button
            onClick={() => setCreateOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700"
          >
            <CameraIcon className="h-4 w-4" /> 新建项目
          </button>
        </div>
      </div>

      {showFilter ? (
        <div className="rounded-[26px] border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-900">项目状态筛选</p>
            <button onClick={() => setShowFilter(false)} className="rounded-full p-1 text-slate-400 transition-all hover:bg-slate-100 hover:text-slate-600">
              <XIcon className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {statusTabs.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setFilter(tab.value)}
                className={cn(
                  "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                  filter === tab.value ? "bg-sky-600 text-white shadow-sm" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((project) => {
          const style = statusStyles[project.status] || statusStyles.pending;
          return (
            <div
              key={project.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/projects/${project.id}`)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  navigate(`/projects/${project.id}`);
                }
              }}
              className="group rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm transition-all duration-300 hover:-translate-y-0.5 hover:border-sky-200 hover:shadow-md"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-base font-semibold text-slate-900 transition-colors group-hover:text-sky-700">{project.name}</h3>
                  <p className="mt-1 text-sm text-slate-500">{project.location}</p>
                </div>
                <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", style.bg, style.text)}>{style.label}</span>
              </div>

              <p className="mt-4 line-clamp-2 text-sm leading-6 text-slate-600">{project.description}</p>

              <div className="mt-4">
                <div className="mb-2 flex items-center justify-between text-sm text-slate-600">
                  <span>施工进度</span>
                  <span className="font-semibold text-sky-700">{project.progress}%</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-700" style={{ width: `${project.progress}%` }} />
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-4">
                <div className="flex items-center gap-3 text-[12px] text-slate-500">
                  <span className="flex items-center gap-1">
                    <CameraIcon className="h-3.5 w-3.5 text-slate-400" /> {project.cameras}
                  </span>
                  <span className="flex items-center gap-1">
                    <BellIcon className="h-3.5 w-3.5 text-slate-400" /> {project.alerts}
                  </span>
                </div>
                <div className="flex items-center gap-1 text-sm font-medium text-sky-700">
                  查看详情 <ChevronRightIcon className="h-4 w-4" />
                </div>
              </div>

              <div className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
                <InfoIcon className="h-3.5 w-3.5 text-slate-400" />
                <span>{project.startDate} ~ {project.endDate}</span>
              </div>
            </div>
          );
        })}
      </div>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建演示项目"
        description="这里先使用前端 Demo 方式模拟项目创建流程，后续可无缝接入后端接口。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setCreateOpen(false)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">取消</button>
            <button onClick={submitCreate} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white">保存项目</button>
          </div>
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">项目名称</span>
            <input value={form.name} onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-sky-300 focus:bg-white" />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">施工地点</span>
            <input value={form.location} onChange={(e) => setForm((prev) => ({ ...prev, location: e.target.value }))} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-sky-300 focus:bg-white" />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">负责人</span>
            <input value={form.manager} onChange={(e) => setForm((prev) => ({ ...prev, manager: e.target.value }))} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-sky-300 focus:bg-white" />
          </label>
          <label className="block md:col-span-2">
            <span className="mb-2 block text-sm font-medium text-slate-700">项目说明</span>
            <textarea value={form.description} onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))} rows={4} className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-sky-300 focus:bg-white" />
          </label>
        </div>
      </Modal>
    </div>
  );
};
