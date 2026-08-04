import React, { useState } from "react";
import { reports } from "../data/mock";
import { cn } from "../utils/cn";
import { EyeIcon, PrintIcon, DownloadIcon } from "../components/Icons";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";

const typeTone = (type: string) =>
  type === "weekly"
    ? "bg-blue-100 text-blue-700"
    : type === "monthly"
      ? "bg-purple-100 text-purple-700"
      : type === "quality"
        ? "bg-amber-100 text-amber-700"
        : type === "safety"
          ? "bg-red-100 text-red-700"
          : type === "progress"
            ? "bg-emerald-100 text-emerald-700"
            : "bg-slate-100 text-slate-700";

const typeLabel = (type: string) => ({ weekly: "周报", monthly: "月报", quality: "质检", safety: "安全", progress: "进度" }[type] || type);

export const ReportsPage: React.FC = () => {
  const [notice, setNotice] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = reports.find((item) => item.id === selectedId) || null;

  return (
    <div className="space-y-4 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">报表中心</h2>
          <p className="mt-1 text-sm text-slate-500">质量、安全、进度、月度综合报表</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => window.print()} className="flex items-center gap-1.5 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50">
            <PrintIcon className="h-4 w-4" /> 打印
          </button>
          <button onClick={() => setNotice("全部报表导出完成（Demo）。")} className="flex items-center gap-1.5 rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700">
            <DownloadIcon className="h-4 w-4" /> 导出全部
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
              <th className="px-4 py-3 font-medium">报表名称</th>
              <th className="px-4 py-3 font-medium">类型</th>
              <th className="px-4 py-3 font-medium">项目</th>
              <th className="px-4 py-3 font-medium">周期</th>
              <th className="px-4 py-3 font-medium">生成时间</th>
              <th className="px-4 py-3 font-medium">大小</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {reports.map((report) => (
              <tr key={report.id} className="transition-colors hover:bg-slate-50">
                <td className="px-4 py-3 text-sm font-medium text-slate-900">{report.name}</td>
                <td className="px-4 py-3">
                  <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-bold", typeTone(report.type))}>{typeLabel(report.type)}</span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-600">{report.projectName}</td>
                <td className="px-4 py-3 text-xs text-slate-600">{report.period}</td>
                <td className="px-4 py-3 text-xs text-slate-600">{report.createdAt}</td>
                <td className="px-4 py-3 text-xs text-slate-600">{report.size}</td>
                <td className="px-4 py-3">
                  <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-bold", report.status === "generated" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700")}>{report.status === "generated" ? "已生成" : "生成中"}</span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <button onClick={() => setSelectedId(report.id)} className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition-colors hover:bg-slate-100 hover:text-sky-600">
                      <EyeIcon className="h-4 w-4" />
                    </button>
                    <button onClick={() => setNotice(`报表《${report.name}》已下载。`)} className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition-colors hover:bg-slate-100 hover:text-sky-600">
                      <DownloadIcon className="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelectedId(null)}
        title="报表预览"
        description="演示报表预览、打印和下载流程。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setSelectedId(null)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">关闭</button>
            {selected ? (
              <button
                onClick={() => {
                  setSelectedId(null);
                  setNotice(`已从预览面板下载《${selected.name}》。`);
                }}
                className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
              >
                下载报表
              </button>
            ) : null}
          </div>
        }
      >
        {selected ? (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              {[
                ["报表名称", selected.name],
                ["报表类型", typeLabel(selected.type)],
                ["所属项目", selected.projectName],
                ["统计周期", selected.period],
                ["生成时间", selected.createdAt],
                ["文件大小", selected.size],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
                </div>
              ))}
            </div>
            <div className="rounded-[24px] border border-sky-100 bg-sky-50 p-4 text-sm text-slate-700">
              预览内容：已汇总项目进度、告警统计、设备运行与可信交付摘要，可直接用于答辩演示和结果汇报。
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
};
