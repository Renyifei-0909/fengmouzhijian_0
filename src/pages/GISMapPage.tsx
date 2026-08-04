import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { projects } from "../data/mock";
import { Notice } from "../components/ui/Notice";
import { cn } from "../utils/cn";
import { MapIcon, ProjectIcon, ChevronRightIcon, AnalyticsIcon, EyeIcon } from "../components/Icons";

const markers = [
  { id: "p1", top: "24%", left: "34%", status: "active" },
  { id: "p2", top: "44%", left: "58%", status: "active" },
  { id: "p3", top: "58%", left: "62%", status: "active" },
  { id: "p4", top: "35%", left: "47%", status: "paused" },
  { id: "p5", top: "53%", left: "53%", status: "active" },
  { id: "p6", top: "29%", left: "38%", status: "completed" },
];

export const GISMapPage: React.FC = () => {
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState("p1");
  const [notice, setNotice] = useState("");
  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || projects[0], [selectedId]);

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <MapIcon className="h-4 w-4" /> GIS 图物动态对齐
            </div>
            <h2 className="text-2xl font-semibold">施工 GIS 地图联动 Demo</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-sky-100">
              通过地图点位、现场项目和设计空间位置联动展示工程状态，可直接跳转到项目详情或 AI 隐蔽验真页面。
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3 rounded-3xl border border-white/15 bg-slate-950/20 p-3 text-center backdrop-blur-sm">
            <div>
              <p className="text-[11px] text-sky-200">活跃项目</p>
              <p className="mt-1 text-xl font-semibold">18</p>
            </div>
            <div>
              <p className="text-[11px] text-sky-200">已校准点位</p>
              <p className="mt-1 text-xl font-semibold">126</p>
            </div>
            <div>
              <p className="text-[11px] text-sky-200">偏差告警</p>
              <p className="mt-1 text-xl font-semibold">7</p>
            </div>
          </div>
        </div>
      </div>

      {notice ? <Notice type="info" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.3fr_0.7fr]">
        <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-5 py-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">项目地图分布</h3>
                <p className="mt-1 text-sm text-slate-500">点击任意点位可查看项目详情、进度和风险概况。</p>
              </div>
              <button
                onClick={() => setNotice("已完成 GIS 图层刷新，演示点位与项目状态已同步。")}
                className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-700 transition-all hover:bg-sky-100"
              >
                刷新图层
              </button>
            </div>
          </div>
          <div className="relative h-[560px] overflow-hidden bg-[linear-gradient(135deg,#e0f2fe_0%,#f8fbff_38%,#dbeafe_100%)]">
            <div className="absolute inset-0 bg-[linear-gradient(rgba(14,165,233,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(14,165,233,0.08)_1px,transparent_1px)] bg-[size:38px_38px]" />
            <div className="absolute inset-0 opacity-70">
              <div className="absolute left-[18%] top-[24%] h-40 w-48 rounded-full bg-sky-200/50 blur-3xl" />
              <div className="absolute left-[45%] top-[18%] h-48 w-56 rounded-full bg-blue-200/40 blur-3xl" />
              <div className="absolute left-[58%] top-[48%] h-48 w-56 rounded-full bg-cyan-200/40 blur-3xl" />
            </div>
            <div className="absolute left-[15%] top-[20%] h-[52%] w-[55%] rounded-[40%] border border-sky-300/60" />
            <div className="absolute left-[38%] top-[22%] h-[34%] w-[30%] rounded-[42%] border border-blue-300/60" />
            <div className="absolute left-[42%] top-[46%] h-[26%] w-[22%] rounded-[42%] border border-cyan-300/60" />
            <div className="absolute left-[32%] top-[32%] h-1.5 w-[34%] rotate-[22deg] rounded-full bg-sky-500/50" />
            <div className="absolute left-[44%] top-[46%] h-1.5 w-[18%] rotate-[10deg] rounded-full bg-blue-500/50" />
            <div className="absolute left-[40%] top-[58%] h-1.5 w-[12%] rotate-[-18deg] rounded-full bg-cyan-500/50" />

            {markers.map((marker) => {
              const project = projects.find((item) => item.id === marker.id);
              const active = selectedId === marker.id;
              return (
                <button
                  key={marker.id}
                  onClick={() => setSelectedId(marker.id)}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                  style={{ top: marker.top, left: marker.left }}
                >
                  <span className={cn("absolute left-1/2 top-1/2 h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full blur-xl", active ? "bg-sky-500/60" : "bg-sky-300/50")} />
                  <span
                    className={cn(
                      "relative flex h-12 w-12 items-center justify-center rounded-full border-4 text-white shadow-lg transition-all",
                      active
                        ? "scale-110 border-white bg-sky-600 shadow-sky-300"
                        : project?.status === "completed"
                          ? "border-white bg-emerald-500"
                          : project?.status === "paused"
                            ? "border-white bg-amber-500"
                            : "border-white bg-blue-600"
                    )}
                  >
                    <MapIcon className="h-5 w-5" />
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">点位详情</h3>
                <p className="mt-1 text-sm text-slate-500">当前已选中项目的 GIS 对齐信息</p>
              </div>
              <ProjectIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="space-y-4">
              <div className="rounded-[22px] border border-sky-100 bg-sky-50 p-4">
                <p className="text-xs text-slate-500">项目名称</p>
                <p className="mt-2 text-base font-semibold text-slate-900">{selectedProject.name}</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">施工位置</p>
                  <p className="mt-2 text-sm font-medium text-slate-900">{selectedProject.location}</p>
                </div>
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">项目进度</p>
                  <p className="mt-2 text-sm font-medium text-sky-700">{selectedProject.progress}%</p>
                </div>
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">监控点位</p>
                  <p className="mt-2 text-sm font-medium text-slate-900">{selectedProject.cameras} 个</p>
                </div>
                <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">现场告警</p>
                  <p className="mt-2 text-sm font-medium text-slate-900">{selectedProject.alerts} 条</p>
                </div>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-full bg-gradient-to-r from-sky-500 via-blue-600 to-cyan-500" style={{ width: `${selectedProject.progress}%` }} />
              </div>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">快捷操作</h3>
              <AnalyticsIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid gap-3">
              <button
                onClick={() => navigate(`/projects/${selectedProject.id}`)}
                className="flex items-center justify-between rounded-[22px] border border-slate-200 bg-white px-4 py-3 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">查看项目详情</p>
                  <p className="mt-1 text-xs text-slate-500">进入项目全量监管与交付页面</p>
                </div>
                <ChevronRightIcon className="h-4 w-4 text-sky-600" />
              </button>
              <button
                onClick={() => navigate("/ai-verification")}
                className="flex items-center justify-between rounded-[22px] border border-slate-200 bg-white px-4 py-3 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">发起隐蔽验真</p>
                  <p className="mt-1 text-xs text-slate-500">跳转到 AI 分析中心执行结构化验真</p>
                </div>
                <ChevronRightIcon className="h-4 w-4 text-sky-600" />
              </button>
              <button
                onClick={() => setNotice(`已生成 ${selectedProject.name} 的 GIS 对齐快照。`) }
                className="flex items-center justify-between rounded-[22px] border border-slate-200 bg-white px-4 py-3 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">生成对齐快照</p>
                  <p className="mt-1 text-xs text-slate-500">输出图物叠加截图与位置证据</p>
                </div>
                <EyeIcon className="h-4 w-4 text-sky-600" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
