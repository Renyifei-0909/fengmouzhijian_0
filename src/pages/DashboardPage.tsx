import React from "react";
import { useNavigate } from "react-router-dom";
import {
  ChevronRightIcon,
  BellIcon,
  DeviceIcon,
  AnalyticsIcon,
  ProjectIcon,
  ArrowUpIcon,
  MapIcon,
  DatabaseIcon,
  BlockchainIcon,
  CpuIcon,
  EyeIcon,
  ShieldIcon,
} from "../components/Icons";
import { dashboardStats, alarms, weeklyAlarmTrend, projectProgressData } from "../data/mock";
import { cn } from "../utils/cn";

const StatCard: React.FC<{
  title: string;
  value: string;
  change: string;
  icon: React.ReactNode;
}> = ({ title, value, change, icon }) => (
  <div className="rounded-[26px] border border-slate-200 bg-white p-5 shadow-sm transition-all duration-300 hover:-translate-y-0.5 hover:shadow-md">
    <div className="flex items-start justify-between">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-blue-700 text-white shadow-lg shadow-sky-100">
        {icon}
      </div>
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
        <ArrowUpIcon className="h-3.5 w-3.5" /> {change}
      </span>
    </div>
    <p className="mt-4 text-3xl font-semibold text-slate-900">{value}</p>
    <p className="mt-1 text-sm text-slate-500">{title}</p>
  </div>
);

const quickLinks = [
  { title: "GIS地图", desc: "图物动态对齐", path: "/gis-map", icon: MapIcon },
  { title: "数据看板", desc: "驾驶舱指标联动", path: "/data-cockpit", icon: DatabaseIcon },
  { title: "溯源查询", desc: "可信档案核验", path: "/traceability", icon: BlockchainIcon },
  { title: "模型服务", desc: "端边云模型编排", path: "/model-service", icon: CpuIcon },
];

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const maxTrend = Math.max(...projectProgressData.datasets[0].data);

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[32px] border border-sky-100 bg-gradient-to-br from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_24px_90px_-36px_rgba(14,116,255,0.85)]">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-4xl">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <ShieldIcon className="h-4 w-4" /> 多源感知赋能端边云协同的智能监管与可信交付系统
            </div>
            <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">通信基建施工全链路智能监管总览</h2>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-sky-100 md:text-base">
              覆盖智能采集、边缘预警、隐蔽工程 AI 验真、GIS 图物对齐、可信档案交付的完整演示系统，整体采用科技蓝风格与可用的业务级交互流程。
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <button
                onClick={() => navigate("/ai-verification")}
                className="inline-flex items-center gap-2 rounded-2xl bg-white px-4 py-2.5 text-sm font-medium text-sky-700 transition-all hover:bg-sky-50"
              >
                <EyeIcon className="h-4 w-4" /> 进入隐蔽验真 AI
              </button>
              <button
                onClick={() => navigate("/traceability")}
                className="inline-flex items-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-white/15"
              >
                <BlockchainIcon className="h-4 w-4" /> 查看可信交付链路
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 rounded-3xl border border-white/15 bg-slate-950/20 p-4 backdrop-blur-sm md:grid-cols-4 xl:min-w-[520px]">
            {[
              ["在线设备", `${dashboardStats.onlineDevices}/${dashboardStats.totalDevices}`],
              ["在建项目", `${dashboardStats.activeProjects}`],
              ["今日告警", `${dashboardStats.todayAlarms}`],
              ["平均进度", `${dashboardStats.avgProgress}%`],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <p className="text-[11px] uppercase tracking-[0.2em] text-sky-200">{label}</p>
                <p className="mt-2 text-xl font-semibold text-white">{value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard title="在建项目总数" value={`${dashboardStats.activeProjects}`} change="+2 本月" icon={<ProjectIcon className="h-5 w-5" />} />
        <StatCard title="在线感知设备" value={`${dashboardStats.onlineDevices}`} change="98.1% 可用" icon={<DeviceIcon className="h-5 w-5" />} />
        <StatCard title="今日告警事件" value={`${dashboardStats.todayAlarms}`} change="响应更快" icon={<BellIcon className="h-5 w-5" />} />
        <StatCard title="平均施工进度" value={`${dashboardStats.avgProgress}%`} change="稳步提升" icon={<AnalyticsIcon className="h-5 w-5" />} />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_0.9fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">隐蔽工程 AI 智能分析</h3>
              <p className="mt-1 text-sm text-slate-500">重点补齐的核心能力：影像结构化验真、规则校验与可信存证</p>
            </div>
            <EyeIcon className="h-5 w-5 text-sky-600" />
          </div>
          <div className="grid gap-4 lg:grid-cols-[1fr_0.95fr]">
            <div className="rounded-[24px] bg-[linear-gradient(135deg,#eff6ff_0%,#dbeafe_45%,#cffafe_100%)] p-5">
              <div className="mb-4 inline-flex rounded-full bg-white/80 px-3 py-1 text-xs font-semibold text-sky-700">演示流程</div>
              <div className="space-y-3">
                {[
                  "施工影像接入与弱网缓存",
                  "语义分割 + 深度量测",
                  "埋深 / 间距 / 数量结构化输出",
                  "规则比对与通过/复检结论生成",
                  "SHA-256 指纹封装与可信归档",
                ].map((item, index) => (
                  <div key={item} className="flex items-start gap-3 rounded-[20px] bg-white/80 px-4 py-3 shadow-sm">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-sky-600 text-xs font-semibold text-white">{index + 1}</span>
                    <p className="text-sm text-slate-700">{item}</p>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
              <div className="grid grid-cols-2 gap-3">
                {[
                  ["识别精度", ">90%"],
                  ["平均耗时", "3.2s"],
                  ["结构化字段", "20+"],
                  ["可信存证", "已接通"],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-[20px] border border-slate-200 bg-white p-4">
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className="mt-2 text-lg font-semibold text-slate-900">{value}</p>
                  </div>
                ))}
              </div>
              <button
                onClick={() => navigate("/ai-verification")}
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                立即开始 AI 验真 <ChevronRightIcon className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">快捷业务入口</h3>
              <p className="mt-1 text-sm text-slate-500">全部入口已接入可点击的 Demo 页面</p>
            </div>
            <DatabaseIcon className="h-5 w-5 text-sky-600" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {quickLinks.map((item) => (
              <button
                key={item.path}
                onClick={() => navigate(item.path)}
                className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-2xl bg-sky-100 text-sky-700">
                  <item.icon className="h-5 w-5" />
                </div>
                <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                <p className="mt-1 text-xs text-slate-500">{item.desc}</p>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold text-slate-900">项目进度排名</h3>
            <button onClick={() => navigate("/projects")} className="text-sm font-medium text-sky-700 hover:text-sky-800">查看全部</button>
          </div>
          <div className="space-y-3">
            {projectProgressData.labels.map((label, index) => (
              <div key={label}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="text-slate-600">{label}</span>
                  <span className="font-semibold text-slate-900">{projectProgressData.datasets[0].data[index]}%</span>
                </div>
                <div className="h-2.5 rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-700"
                    style={{ width: `${(projectProgressData.datasets[0].data[index] / maxTrend) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold text-slate-900">最新告警动态</h3>
            <button onClick={() => navigate("/alarms")} className="text-sm font-medium text-sky-700 hover:text-sky-800">进入告警中心</button>
          </div>
          <div className="space-y-3">
            {alarms.slice(0, 5).map((alarm) => (
              <button
                key={alarm.id}
                onClick={() => navigate("/alarms")}
                className="w-full rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-900">{alarm.projectName}</p>
                    <p className="mt-1 text-xs text-slate-500">{alarm.message}</p>
                  </div>
                  <span
                    className={cn(
                      "rounded-full px-2 py-1 text-[11px] font-semibold",
                      alarm.level === "critical"
                        ? "bg-rose-100 text-rose-700"
                        : alarm.level === "warn"
                          ? "bg-amber-100 text-amber-700"
                          : "bg-sky-100 text-sky-700"
                    )}
                  >
                    {alarm.level === "critical" ? "严重" : alarm.level === "warn" ? "警告" : "信息"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
