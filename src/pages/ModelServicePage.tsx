import React, { useMemo, useState } from "react";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";
import { CpuIcon, CheckIcon, XIcon, AnalyticsIcon, ShieldIcon, DownloadIcon } from "../components/Icons";
import { cn } from "../utils/cn";

type ModelStatus = "running" | "idle" | "updating";

interface ModelItem {
  id: string;
  name: string;
  version: string;
  accuracy: string;
  latency: string;
  status: ModelStatus;
  scene: string;
}

const initialModels: ModelItem[] = [
  { id: "m1", name: "边缘违章检测", version: "v3.4.2", accuracy: "86.7%", latency: "62ms", status: "running", scene: "安全帽 / 区域入侵 / 动火" },
  { id: "m2", name: "隐蔽工程结构化验真", version: "v2.8.1", accuracy: "91.3%", latency: "3.2s", status: "running", scene: "沟槽 / 管道 / 线缆预埋" },
  { id: "m3", name: "图物对齐进度核算", version: "v1.9.0", accuracy: "89.1%", latency: "1.4s", status: "idle", scene: "GIS / 设计图 / 实景比对" },
  { id: "m4", name: "弱网任务补偿调度", version: "v1.2.6", accuracy: "99.0%", latency: "180ms", status: "updating", scene: "边缘缓存 / 断点续传 / 调度" },
];

export const ModelServicePage: React.FC = () => {
  const [models, setModels] = useState(initialModels);
  const [notice, setNotice] = useState("");
  const [activeId, setActiveId] = useState("m2");
  const [detailOpen, setDetailOpen] = useState(false);

  const activeModel = useMemo(() => models.find((item) => item.id === activeId) || models[0], [activeId, models]);

  const toggleModel = (id: string) => {
    setModels((prev) =>
      prev.map((item) =>
        item.id === id
          ? { ...item, status: item.status === "running" ? "idle" : "running" }
          : item
      )
    );
    const target = models.find((item) => item.id === id);
    setNotice(`${target?.name || "模型"} 已${target?.status === "running" ? "停止" : "启动"}。`);
  };

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <CpuIcon className="h-4 w-4" /> 模型服务 Demo
            </div>
            <h2 className="text-2xl font-semibold">端边云模型编排与运行面板</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-sky-100">
              支持查看模型版本、运行状态、准确率和时延，并演示启停、基准测试、版本切换和结果预览。
            </p>
          </div>
          <button
            onClick={() => setNotice("模型运行报表已生成，可用于答辩演示和方案汇报。")}
            className="inline-flex items-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-4 py-2 text-sm font-medium backdrop-blur-sm transition-all hover:bg-white/15"
          >
            <DownloadIcon className="h-4 w-4" /> 导出服务报表
          </button>
        </div>
      </div>

      {notice ? <Notice type="success" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">模型列表</h3>
              <p className="mt-1 text-sm text-slate-500">点击可切换查看详情或执行启停操作。</p>
            </div>
            <AnalyticsIcon className="h-5 w-5 text-sky-600" />
          </div>
          <div className="space-y-3">
            {models.map((model) => (
              <div
                key={model.id}
                className={cn(
                  "rounded-[24px] border p-4 transition-all",
                  activeId === model.id ? "border-sky-300 bg-sky-50 shadow-md shadow-sky-100" : "border-slate-200 bg-white"
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <button className="min-w-0 flex-1 text-left" onClick={() => setActiveId(model.id)}>
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-semibold text-slate-900">{model.name}</p>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                          model.status === "running"
                            ? "bg-emerald-100 text-emerald-700"
                            : model.status === "idle"
                              ? "bg-slate-100 text-slate-700"
                              : "bg-amber-100 text-amber-700"
                        )}
                      >
                        {model.status === "running" ? "运行中" : model.status === "idle" ? "空闲" : "升级中"}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-slate-500">{model.scene}</p>
                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-600">
                      <span>版本 {model.version}</span>
                      <span>准确率 {model.accuracy}</span>
                      <span>时延 {model.latency}</span>
                    </div>
                  </button>
                  <button
                    onClick={() => toggleModel(model.id)}
                    className={cn(
                      "rounded-2xl px-3 py-2 text-xs font-medium transition-all",
                      model.status === "running"
                        ? "border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
                        : "border border-sky-200 bg-sky-50 text-sky-700 hover:bg-sky-100"
                    )}
                  >
                    {model.status === "running" ? "停止" : "启动"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">当前模型详情</h3>
                <p className="mt-1 text-sm text-slate-500">版本信息、性能指标和服务动作</p>
              </div>
              <ShieldIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["模型名称", activeModel.name],
                ["当前版本", activeModel.version],
                ["适配场景", activeModel.scene],
                ["平均时延", activeModel.latency],
                ["模型精度", activeModel.accuracy],
                ["运行状态", activeModel.status === "running" ? "在线推理" : activeModel.status === "idle" ? "待命" : "升级中"],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <button
                onClick={() => setDetailOpen(true)}
                className="rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                运行 Demo 推理
              </button>
              <button
                onClick={() => setNotice(`${activeModel.name} 已切换到演示版本 ${activeModel.version}。`) }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                版本切换
              </button>
              <button
                onClick={() => setNotice(`${activeModel.name} 基准测试完成：吞吐 148 FPS / 峰值显存 1.9GB。`) }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                性能压测
              </button>
              <button
                onClick={() => setNotice(`${activeModel.name} 已同步到边缘节点样例集群。`) }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                下发边缘节点
              </button>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">服务健康度</h3>
            <div className="mt-4 grid grid-cols-3 gap-3">
              {[
                ["在线实例", "12"],
                ["健康率", "99.2%"],
                ["待调度任务", "4"],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 text-center">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-2 text-xl font-semibold text-slate-900">{value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <Modal
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        title="模型运行结果预览"
        description="演示一次完整的模型推理结果输出。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setDetailOpen(false)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">
              关闭
            </button>
            <button
              onClick={() => {
                setDetailOpen(false);
                setNotice(`${activeModel.name} Demo 推理完成，结果已写入样例缓存。`);
              }}
              className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
            >
              确认写入结果
            </button>
          </div>
        }
      >
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs text-slate-500">模型</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{activeModel.name}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs text-slate-500">推理耗时</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{activeModel.latency}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs text-slate-500">输出精度</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{activeModel.accuracy}</p>
          </div>
        </div>
        <div className="mt-4 rounded-[24px] border border-sky-100 bg-sky-50 p-4">
          <p className="text-sm font-semibold text-slate-900">输出摘要</p>
          <ul className="mt-3 space-y-2 text-sm text-slate-600">
            <li className="flex items-center gap-2"><CheckIcon className="h-4 w-4 text-emerald-600" /> 已识别目标 12 个</li>
            <li className="flex items-center gap-2"><CheckIcon className="h-4 w-4 text-emerald-600" /> 告警置信度阈值满足上线配置</li>
            <li className="flex items-center gap-2"><XIcon className="h-4 w-4 text-amber-500" /> 检测到 1 个待人工复核区域</li>
          </ul>
        </div>
      </Modal>
    </div>
  );
};
