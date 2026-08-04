import React, { useState } from "react";
import { weeklyAlarmTrend, projectProgressData, deviceTypeDistribution } from "../data/mock";
import { ChevronRightIcon } from "../components/Icons";
import { Notice } from "../components/ui/Notice";

const miniBar = (labels: string[], data: number[]) => {
  const max = Math.max(...data);
  return (
    <div className="flex h-28 items-end gap-2">
      {labels.map((label, i) => (
        <div key={label} className="flex flex-1 flex-col items-center gap-1.5">
          <div className="w-full rounded-t-2xl bg-gradient-to-t from-sky-500 via-blue-600 to-cyan-400" style={{ height: `${data[i] ? (data[i] / max) * 100 : 0}%`, minHeight: "4px" }} />
          <span className="text-[10px] text-slate-400">{label}</span>
        </div>
      ))}
    </div>
  );
};

const bar = (labels: string[], data: number[]) => {
  const max = Math.max(...data);
  return (
    <div className="space-y-3">
      {labels.map((label, i) => (
        <div key={label} className="flex items-center gap-3">
          <span className="w-24 truncate text-xs text-slate-600">{label}</span>
          <div className="h-2.5 flex-1 rounded-full bg-slate-100">
            <div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-700" style={{ width: `${(data[i] / max) * 100}%` }} />
          </div>
          <span className="w-8 text-right text-xs font-bold text-slate-700">{data[i]}%</span>
        </div>
      ))}
    </div>
  );
};

export const AnalyticsPage: React.FC = () => {
  const [notice, setNotice] = useState("");

  return (
    <div className="space-y-4 page-enter">
      <Notice
        type="warning"
        message="原型页 · 图表与数字来自本地静态 mock，不是后端数据库或冻结评测结果。不会写入报告、证据包或竞赛指标。"
      />
      {notice ? <Notice type="info" message={notice} /> : null}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">数据分析（原型）</h2>
          <p className="mt-1 text-sm text-slate-500">布局演示；运营统计请用总览/告警/真实闭环页的 API 数据</p>
        </div>
        <button
          onClick={() =>
            setNotice("原型动作：未生成真实报表文件，也未调用后端导出接口。")
          }
          className="flex items-center gap-1 text-sm font-semibold text-sky-700"
        >
          演示导出 <ChevronRightIcon className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm transition-all hover:shadow-md">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-base font-semibold text-slate-900">告警趋势</p>
            <span className="text-sm text-slate-500">近7日</span>
          </div>
          {miniBar(weeklyAlarmTrend.labels, weeklyAlarmTrend.datasets[0].data)}
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {weeklyAlarmTrend.datasets.map((ds) => (
              <span key={ds.label} className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: ds.color }} />
                {ds.label}
              </span>
            ))}
          </div>
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm transition-all hover:shadow-md">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-base font-semibold text-slate-900">项目进度</p>
            <span className="text-sm text-slate-500">当前周期</span>
          </div>
          {bar(projectProgressData.labels, projectProgressData.datasets[0].data)}
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm transition-all hover:shadow-md xl:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-base font-semibold text-slate-900">设备分布</p>
            <span className="text-sm text-slate-500">按类型</span>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {deviceTypeDistribution.labels.map((label, i) => (
              <div key={label} className="rounded-[24px] bg-slate-50 p-4 transition-all hover:bg-sky-50">
                <p className="text-sm text-slate-500">{label}</p>
                <p className="mt-2 text-2xl font-semibold text-sky-700">{deviceTypeDistribution.datasets[0].data[i]}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
