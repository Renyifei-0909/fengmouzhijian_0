import React, { useMemo, useState } from "react";
import { devices } from "../data/mock";
import { cn } from "../utils/cn";
import { DeviceIcon, CameraIcon, HelmetIcon, SensorIcon, EdgeIcon, EyeIcon } from "../components/Icons";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";

const tone = (status: string) =>
  status === "online" ? "bg-emerald-100 text-emerald-700" : status === "offline" ? "bg-slate-100 text-slate-700" : status === "warning" ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700";

const icons: Record<string, React.FC<{ className?: string }>> = {
  camera: CameraIcon,
  helmet: HelmetIcon,
  sensor: SensorIcon,
  edge: EdgeIcon,
};

export const DevicesPage: React.FC = () => {
  const [filter, setFilter] = useState<string>("all");
  const [notice, setNotice] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const visible = useMemo(() => (filter === "all" ? devices : devices.filter((d) => d.type === filter)), [filter]);
  const selected = devices.find((device) => device.id === selectedId) || null;
  const typeTabs = [
    { value: "all", label: "全部" },
    { value: "camera", label: "监控摄像头" },
    { value: "sensor", label: "传感器" },
    { value: "edge", label: "边缘盒子" },
    { value: "helmet", label: "安全帽" },
  ];

  return (
    <div className="space-y-4 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">设备监控</h2>
          <p className="mt-1 text-sm text-slate-500">感知端、边缘节点集中管控</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setNotice("批量诊断已完成，异常设备清单已生成。")}
            className="flex items-center gap-1.5 rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700"
          >
            批量诊断
          </button>
          <button
            onClick={() => setNotice("设备清单导出完成（Demo）。")}
            className="flex items-center gap-1.5 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            导出设备清单
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {typeTabs.map((tab) => (
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

      <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
              <th className="px-4 py-3 font-medium">设备名称</th>
              <th className="px-4 py-3 font-medium">类型</th>
              <th className="px-4 py-3 font-medium">位置</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">电量/最后心跳</th>
              <th className="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visible.map((device) => {
              const Icon = icons[device.type] || DeviceIcon;
              return (
                <tr key={device.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-sky-50 text-sky-700">
                        <Icon className="h-4 w-4" />
                      </div>
                      <span className="text-sm font-medium text-slate-900">{device.name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-600">{device.type.toUpperCase()}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{device.location}</td>
                  <td className="px-4 py-3">
                    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-bold", tone(device.status))}>{device.status}</span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-600">{device.battery ? `${device.battery}%` : new Date(device.lastHeartbeat).toLocaleString("zh-CN")}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setSelectedId(device.id)}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition-colors hover:bg-slate-100 hover:text-sky-600"
                    >
                      <EyeIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelectedId(null)}
        title="设备详情"
        description="查看设备状态、位置和最近心跳信息。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setSelectedId(null)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">关闭</button>
            {selected ? (
              <button
                onClick={() => {
                  setSelectedId(null);
                  setNotice(`已对 ${selected.name} 发起远程巡检任务。`);
                }}
                className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
              >
                发起巡检
              </button>
            ) : null}
          </div>
        }
      >
        {selected ? (
          <div className="grid gap-3 md:grid-cols-2">
            {[
              ["设备名称", selected.name],
              ["设备类型", selected.type],
              ["部署位置", selected.location],
              ["运行状态", selected.status],
              ["所属项目", selected.projectId],
              ["最近心跳", selected.lastHeartbeat],
              ["电量", selected.battery ? `${selected.battery}%` : "—"],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
              </div>
            ))}
          </div>
        ) : null}
      </Modal>
    </div>
  );
};
