import React, { useMemo, useState } from "react";
import { Notice } from "../components/ui/Notice";
import { Modal } from "../components/ui/Modal";
import { BlockchainIcon, SearchIcon, ShieldIcon, CheckIcon, DownloadIcon, ClipboardIcon } from "../components/Icons";

const archives = [
  {
    id: "ARC-2026-001",
    project: "长三角5G骨干光缆敷设工程",
    hash: "0x4b8e-b88d-c721-77fa-1f72-0a99-d5cd-0f21",
    timestamp: "2026-01-15 14:32:18",
    status: "可信有效",
    location: "浙江省杭州市 · 点位 A-12",
    summary: "隐蔽工程验真报告、施工实景照片、整改闭环记录已归档。",
    timeline: ["现场采集完成", "AI 验真通过", "整改复核完成", "哈希封装存证", "交付档案生成"],
  },
  {
    id: "ARC-2026-002",
    project: "算力网络城域网扩容项目",
    hash: "0x18ff-771c-3bd2-51a9-8f2d-7340-66ab-9931",
    timestamp: "2026-01-12 09:21:44",
    status: "可信有效",
    location: "江苏省南京市 · 点位 C-03",
    summary: "管线量测数据、图物对齐快照、设备心跳记录已归档。",
    timeline: ["GIS 对齐完成", "结构化数据入库", "边缘告警闭环", "可信封装存证", "阶段交付生成"],
  },
];

export const TraceabilityPage: React.FC = () => {
  const [keyword, setKeyword] = useState(archives[0].id);
  const [notice, setNotice] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);

  const matched = useMemo(
    () => archives.find((item) => item.id.includes(keyword) || item.hash.includes(keyword)) || archives[0],
    [keyword]
  );

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
              <BlockchainIcon className="h-4 w-4" /> 可信溯源查询 Demo
            </div>
            <h2 className="text-2xl font-semibold">数字交付档案真伪核验</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-sky-100">
              支持通过档案编号或哈希指纹查询施工过程数据，核验是否篡改，并输出完整的时间线和交付依据。
            </p>
          </div>
          <div className="rounded-3xl border border-white/15 bg-slate-950/20 px-5 py-4 backdrop-blur-sm">
            <p className="text-[11px] uppercase tracking-[0.24em] text-sky-200">链上校验状态</p>
            <p className="mt-2 text-xl font-semibold">全量有效</p>
          </div>
        </div>
      </div>

      {notice ? <Notice type="success" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="text-base font-semibold text-slate-900">档案检索</h3>
          <div className="mt-4 flex items-center gap-3 rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3">
            <SearchIcon className="h-4 w-4 text-slate-400" />
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              className="w-full bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400"
              placeholder="输入档案编号或哈希值"
            />
          </div>
          <div className="mt-4 grid gap-3">
            <button
              onClick={() => setNotice(`档案 ${matched.id} 校验通过，哈希一致。`) }
              className="rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
            >
              立即核验真伪
            </button>
            <button
              onClick={() => setDetailOpen(true)}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
            >
              查看完整时间线
            </button>
          </div>

          <div className="mt-5 rounded-[24px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-sm font-semibold text-slate-900">命中档案</p>
            <div className="mt-3 space-y-3">
              {archives.map((item) => (
                <button
                  key={item.id}
                  onClick={() => setKeyword(item.id)}
                  className={`w-full rounded-[20px] border p-4 text-left transition-all ${item.id === matched.id ? "border-sky-300 bg-sky-50" : "border-slate-200 bg-white hover:border-sky-200"}`}
                >
                  <p className="text-sm font-medium text-slate-900">{item.id}</p>
                  <p className="mt-1 text-xs text-slate-500">{item.project}</p>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-900">核验结果</h3>
                <p className="mt-1 text-sm text-slate-500">档案摘要、链上指纹和状态说明</p>
              </div>
              <span className="inline-flex items-center gap-2 rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
                <CheckIcon className="h-4 w-4" /> {matched.status}
              </span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">档案编号</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{matched.id}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">归档时间</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{matched.timestamp}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:col-span-2">
                <p className="text-xs text-slate-500">哈希指纹</p>
                <p className="mt-2 break-all font-mono text-sm text-slate-900">{matched.hash}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:col-span-2">
                <p className="text-xs text-slate-500">项目说明</p>
                <p className="mt-2 text-sm leading-6 text-slate-700">{matched.summary}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:col-span-2">
                <p className="text-xs text-slate-500">归属位置</p>
                <p className="mt-2 text-sm font-medium text-slate-900">{matched.location}</p>
              </div>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">交付动作</h3>
              <ShieldIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                onClick={() => setNotice(`已复制哈希指纹：${matched.hash}`)}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                <ClipboardIcon className="h-4 w-4" /> 复制哈希
              </button>
              <button
                onClick={() => setNotice(`档案 ${matched.id} 的可信校验单已生成。`) }
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                <DownloadIcon className="h-4 w-4" /> 导出核验单
              </button>
            </div>
          </div>
        </div>
      </div>

      <Modal
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        title="交付档案全流程时间线"
        description="演示从现场采集到可信交付的完整过程。"
        footer={
          <div className="flex items-center justify-end">
            <button onClick={() => setDetailOpen(false)} className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white">
              关闭
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          {matched.timeline.map((item, index) => (
            <div key={item} className="flex gap-4">
              <div className="flex flex-col items-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-600 text-white">{index + 1}</div>
                {index < matched.timeline.length - 1 ? <div className="mt-2 h-10 w-px bg-sky-200" /> : null}
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3">
                <p className="text-sm font-medium text-slate-900">{item}</p>
                <p className="mt-1 text-sm text-slate-500">节点已留痕，可用于责任追溯与交付核验。</p>
              </div>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
};
