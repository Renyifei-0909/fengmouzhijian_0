import React, { useMemo, useState } from "react";
import { dashboardStats, weeklyAlarmTrend, deviceTypeDistribution } from "../data/mock";
import { Notice } from "../components/ui/Notice";
import { AnalyticsIcon, DeviceIcon, BellIcon, DownloadIcon, DatabaseIcon } from "../components/Icons";

const periods = ["日", "周", "月"] as const;

const metricSets = {
  日: [96, 88, 72, 91, 83, 77, 95],
  周: [82, 92, 76, 89, 94, 73, 85],
  月: [78, 84, 81, 88, 91, 86, 90],
};

export const DataCockpitPage: React.FC = () => {
  const [period, setPeriod] = useState<(typeof periods)[number]>("周");
  const [notice, setNotice] = useState("");

  const trend = useMemo(() => metricSets[period], [period]);
  const max = Math.max(...trend);

  return (
    <div className="space-y-5 page-enter">
      <Notice
        type="warning"
        message="原型页 · 驾驶舱数字来自本地 mock，不是后端汇总 API。禁止当作项目进度、告警量或 85%/90% 指标。"
      />
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <DatabaseIcon className="h-4 w-4" /> 数据看板 · 静态原型
            </div>
            <h2 className="text-2xl font-semibold">施工监管数据驾驶舱（演示布局）</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-sky-100">
              仅展示 UI 布局。真实项目/任务/告警统计请使用总览、项目、告警与真实闭环页的后端数据。
            </p>
          </div>
          <button
            onClick={() =>
              setNotice("原型动作：未生成驾驶舱快照文件，也未写入报表中心。")
            }
            className="inline-flex items-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-4 py-2 text-sm font-medium backdrop-blur-sm transition-all hover:bg-white/15"
          >
            <DownloadIcon className="h-4 w-4" /> 演示导出
          </button>
        </div>
      </div>

      {notice ? <Notice type="info" message={notice} /> : null}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <div className="rounded-[26px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">在建项目</span>
            <AnalyticsIcon className="h-5 w-5 text-sky-600" />
          </div>
          <p className="mt-4 text-3xl font-semibold text-slate-900">{dashboardStats.activeProjects}</p>
          <p className="mt-2 text-xs text-emerald-600">较昨日 +2 个</p>
        </div>
        <div className="rounded-[26px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">在线设备</span>
            <DeviceIcon className="h-5 w-5 text-sky-600" />
          </div>
          <p className="mt-4 text-3xl font-semibold text-slate-900">{dashboardStats.onlineDevices}</p>
          <p className="mt-2 text-xs text-emerald-600">在线率 91.0%</p>
        </div>
        <div className="rounded-[26px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">今日告警</span>
            <BellIcon className="h-5 w-5 text-sky-600" />
          </div>
          <p className="mt-4 text-3xl font-semibold text-slate-900">{dashboardStats.todayAlarms}</p>
          <p className="mt-2 text-xs text-rose-500">严重告警 {dashboardStats.criticalAlarms} 条</p>
        </div>
        <div className="rounded-[26px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">平均完成率</span>
            <DatabaseIcon className="h-5 w-5 text-sky-600" />
          </div>
          <p className="mt-4 text-3xl font-semibold text-slate-900">{dashboardStats.completionRate}%</p>
          <p className="mt-2 text-xs text-emerald-600">交付质量稳定提升</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">综合运行指数</h3>
              <p className="mt-1 text-sm text-slate-500">支持日 / 周 / 月切换查看综合指数走势</p>
            </div>
            <div className="flex items-center gap-2 rounded-full bg-slate-100 p-1">
              {periods.map((item) => (
                <button
                  key={item}
                  onClick={() => setPeriod(item)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium transition-all ${period === item ? "bg-sky-600 text-white" : "text-slate-600 hover:text-sky-700"}`}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-7 gap-3">
            {trend.map((value, index) => (
              <div key={`${period}-${index}`} className="flex flex-col items-center gap-2">
                <div className="flex h-52 items-end">
                  <div
                    className="w-10 rounded-t-2xl bg-gradient-to-t from-sky-500 via-blue-600 to-cyan-400 shadow-lg shadow-sky-100"
                    style={{ height: `${(value / max) * 100}%` }}
                  />
                </div>
                <div className="text-center">
                  <p className="text-xs font-semibold text-slate-900">{value}</p>
                  <p className="text-[11px] text-slate-400">{period}{index + 1}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">设备接入分布</h3>
            <div className="mt-4 space-y-3">
              {deviceTypeDistribution.labels.map((label, index) => (
                <div key={label}>
                  <div className="mb-1 flex items-center justify-between text-sm text-slate-600">
                    <span>{label}</span>
                    <span className="font-semibold text-slate-900">{deviceTypeDistribution.datasets[0].data[index]}</span>
                  </div>
                  <div className="h-2.5 rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-sky-500 to-cyan-500"
                      style={{ width: `${(deviceTypeDistribution.datasets[0].data[index] / 58) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">告警结构占比</h3>
            <div className="mt-4 space-y-3">
              {weeklyAlarmTrend.datasets.map((dataset) => {
                const total = dataset.data.reduce((sum, item) => sum + item, 0);
                return (
                  <div key={dataset.label} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-slate-900">{dataset.label}</span>
                      <span className="text-sm font-semibold text-slate-900">{total}</span>
                    </div>
                    <div className="mt-3 h-2.5 rounded-full bg-white">
                      <div className="h-full rounded-full" style={{ width: `${(total / 28) * 100}%`, backgroundColor: dataset.color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
