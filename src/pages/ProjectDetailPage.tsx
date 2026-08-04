import React, { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { projects, devices, alarms, reports } from "../data/mock";
import { cn } from "../utils/cn";
import { ChevronRightIcon, CameraIcon, BellIcon, DeviceIcon, AnalyticsIcon, ArrowUpIcon, ShieldIcon, DownloadIcon, PrintIcon, EyeIcon } from "../components/Icons";
import { Notice } from "../components/ui/Notice";
import { Modal } from "../components/ui/Modal";

const statusStyles: Record<string, { bg: string; text: string; label: string }> = {
  active: { bg: "bg-emerald-100", text: "text-emerald-700", label: "进行中" },
  pending: { bg: "bg-amber-100", text: "text-amber-700", label: "待启动" },
  paused: { bg: "bg-slate-100", text: "text-slate-700", label: "已暂停" },
  completed: { bg: "bg-indigo-100", text: "text-indigo-700", label: "已完成" },
};

const StatCard: React.FC<{ title: string; value: string; icon: React.ReactNode }> = ({ title, value, icon }) => (
  <div className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
    <div className="flex items-start justify-between">
      <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-blue-700 text-white">{icon}</div>
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600"><ArrowUpIcon className="h-3.5 w-3.5" /> 稳定</span>
    </div>
    <p className="mt-4 text-2xl font-semibold text-slate-900">{value}</p>
    <p className="mt-1 text-sm text-slate-500">{title}</p>
  </div>
);

export const ProjectDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const project = projects.find((p) => p.id === id);
  const [tab, setTab] = useState<"overview" | "devices" | "reports" | "archive">("overview");
  const [notice, setNotice] = useState("");
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);

  if (!project) {
    return (
      <div className="rounded-[28px] border border-dashed border-slate-200 bg-white p-12 text-center">
        <p className="text-base font-semibold text-slate-900">未找到该项目</p>
        <p className="mt-2 text-sm text-slate-500">请确认项目 ID 是否有效</p>
      </div>
    );
  }

  const style = statusStyles[project.status];
  const projectAlarms = alarms.filter((a) => a.projectId === project.id);
  const projectDevices = devices.filter((d) => d.projectId === project.id);
  const projectReports = reports.filter((r) => r.projectId === project.id);
  const selectedDevice = projectDevices.find((item) => item.id === selectedDeviceId) || null;
  const selectedReport = projectReports.find((item) => item.id === selectedReportId) || null;

  return (
    <div className="space-y-5 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      <div className="rounded-[30px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", style.bg, style.text)}>{style.label}</span>
              <span className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">项目负责人：{project.manager}</span>
            </div>
            <h2 className="text-xl font-semibold text-slate-900">{project.name}</h2>
            <p className="mt-2 text-sm text-slate-500">{project.location} · 工期 {project.startDate} ~ {project.endDate}</p>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">{project.description}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => window.print()} className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700"><PrintIcon className="h-4 w-4" /> 打印</button>
            <button onClick={() => setNotice(`项目 ${project.name} 的资料导出任务已创建。`)} className="inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"><DownloadIcon className="h-4 w-4" /> 导出资料</button>
            <button onClick={() => setNotice(`已向 ${project.name} 现场端推送采集任务。`)} className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700"><CameraIcon className="h-4 w-4" /> 采集上报</button>
            <button onClick={() => navigate("/ai-verification")} className="inline-flex items-center gap-2 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-700"><EyeIcon className="h-4 w-4" /> AI 验真</button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
          <StatCard title="项目进度" value={`${project.progress}%`} icon={<AnalyticsIcon className="h-5 w-5" />} />
          <StatCard title="参与人数" value={`${project.participants} 人`} icon={<BellIcon className="h-5 w-5" />} />
          <StatCard title="监控点位" value={`${project.cameras} 个`} icon={<CameraIcon className="h-5 w-5" />} />
          <StatCard title="设备接入" value={`${projectDevices.length} 台`} icon={<DeviceIcon className="h-5 w-5" />} />
          <StatCard title="活跃告警" value={`${projectAlarms.length} 条`} icon={<BellIcon className="h-5 w-5" />} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {[
          { key: "overview", label: "总览" },
          { key: "devices", label: "设备" },
          { key: "reports", label: "报表" },
          { key: "archive", label: "可信档案" },
        ].map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key as typeof tab)}
            className={cn("rounded-2xl px-4 py-2 text-sm font-medium transition-all", tab === item.key ? "bg-sky-600 text-white shadow-sm" : "bg-white text-slate-700 border border-slate-200 hover:border-sky-200 hover:text-sky-700")}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">阶段进度</h3>
            <div className="mt-4 space-y-3">
              {[ ["设计", 100], ["施工", project.progress], ["隐蔽验真", 45], ["验收", 20], ["交付", 0] ].map(([label, value]) => (
                <div key={String(label)}>
                  <div className="mb-1 flex items-center justify-between text-sm text-slate-600"><span>{label}</span><span className="font-semibold text-slate-900">{value}%</span></div>
                  <div className="h-2.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-700" style={{ width: `${value}%` }} /></div>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">项目建议动作</h3>
            <div className="mt-4 space-y-3">
              {[
                "补充一次隐蔽工程 AI 验真，形成结构化参数证据。",
                "对高风险告警点位执行现场复检并归档。",
                "在交付前执行可信档案哈希校验。",
              ].map((item) => (
                <div key={item} className="rounded-[22px] border border-sky-100 bg-sky-50 p-4 text-sm text-slate-700">{item}</div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {tab === "devices" ? (
        <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
                <th className="px-4 py-3 font-medium">设备</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">位置</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {projectDevices.map((device) => (
                <tr key={device.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{device.name}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{device.type}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{device.location}</td>
                  <td className="px-4 py-3">
                    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-bold", device.status === "online" ? "bg-emerald-100 text-emerald-700" : device.status === "warning" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-700")}>{device.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => setSelectedDeviceId(device.id)} className="inline-flex items-center gap-1 text-sm font-medium text-sky-700 hover:text-sky-800">详情 <ChevronRightIcon className="h-4 w-4" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {tab === "reports" ? (
        <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
                <th className="px-4 py-3 font-medium">报表名称</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">周期</th>
                <th className="px-4 py-3 font-medium">生成时间</th>
                <th className="px-4 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {projectReports.map((report) => (
                <tr key={report.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{report.name}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{report.type}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{report.period}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{report.createdAt}</td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => setSelectedReportId(report.id)} className="inline-flex items-center gap-1 text-sm font-medium text-sky-700 hover:text-sky-800">下载 <ChevronRightIcon className="h-4 w-4" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {tab === "archive" ? (
        <div className="rounded-[28px] border border-slate-200 bg-white p-8 text-center shadow-sm">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-sky-50 text-sky-700"><ShieldIcon className="h-6 w-6" /></div>
          <p className="text-base font-semibold text-slate-900">可信数字化交付档案</p>
          <p className="mt-2 text-sm text-slate-500">施工实景影像、AI 核验参数、整改记录和进度数据均可生成可信档案并支持上链存证。</p>
          <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
            <button onClick={() => setNotice(`项目 ${project.name} 的数字档案已生成。`)} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white">生成数字档案</button>
            <button onClick={() => setNotice(`项目 ${project.name} 的可信档案真伪核验通过。`)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">核验真伪</button>
            <button onClick={() => setNotice(`项目 ${project.name} 的样例档案已完成上链存证。`)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">上链存证</button>
          </div>
        </div>
      ) : null}

      <Modal
        open={!!selectedDevice}
        onClose={() => setSelectedDeviceId(null)}
        title="项目设备详情"
        description="查看设备运行状态与点位信息。"
        footer={<div className="flex justify-end"><button onClick={() => setSelectedDeviceId(null)} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white">关闭</button></div>}
      >
        {selectedDevice ? (
          <div className="grid gap-3 md:grid-cols-2">
            {[["设备名称", selectedDevice.name], ["设备类型", selectedDevice.type], ["部署位置", selectedDevice.location], ["运行状态", selectedDevice.status], ["最近心跳", selectedDevice.lastHeartbeat], ["电量", selectedDevice.battery ? `${selectedDevice.battery}%` : "—"]].map(([label, value]) => (
              <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
              </div>
            ))}
          </div>
        ) : null}
      </Modal>

      <Modal
        open={!!selectedReport}
        onClose={() => setSelectedReportId(null)}
        title="报表下载确认"
        description="这里用 Demo 方式演示项目报表下载。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setSelectedReportId(null)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">取消</button>
            {selectedReport ? <button onClick={() => { setSelectedReportId(null); setNotice(`已下载《${selectedReport.name}》。`); }} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white">确认下载</button> : null}
          </div>
        }
      >
        {selectedReport ? <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">报表名称：{selectedReport.name}<br />统计周期：{selectedReport.period}<br />生成时间：{selectedReport.createdAt}</div> : null}
      </Modal>
    </div>
  );
};
