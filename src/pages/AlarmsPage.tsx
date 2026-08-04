import React, { useMemo, useState } from "react";
import { alarms as alarmSeed } from "../data/mock";
import { cn } from "../utils/cn";
import { BellIcon, EyeIcon, SearchIcon, FilterIcon } from "../components/Icons";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";

const levelTone = (level: string) =>
  level === "critical" ? "bg-red-100 text-red-700" : level === "warn" ? "bg-amber-100 text-amber-700" : "bg-sky-100 text-sky-700";

const levelDot = (level: string) =>
  level === "critical" ? "bg-red-500" : level === "warn" ? "bg-amber-500" : "bg-sky-500";

export const AlarmsPage: React.FC = () => {
  const [alarms, setAlarms] = useState(alarmSeed);
  const [filter, setFilter] = useState<"all" | "unack" | "ack">("all");
  const [keyword, setKeyword] = useState("");
  const [showFilter, setShowFilter] = useState(false);
  const [notice, setNotice] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const visible = useMemo(
    () =>
      alarms
        .filter((item) => (filter === "unack" ? !item.acknowledged : filter === "ack" ? item.acknowledged : true))
        .filter((item) => item.message.includes(keyword) || item.projectName.includes(keyword)),
    [alarms, filter, keyword]
  );

  const selected = alarms.find((item) => item.id === selectedId) || null;

  return (
    <div className="space-y-4 page-enter">
      {notice ? <Notice type="success" message={notice} /> : null}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">告警中心</h2>
          <p className="mt-1 text-sm text-slate-500">施工安全、设备异常、工艺偏差统一汇聚</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-2xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
            <SearchIcon className="mr-2 h-4 w-4 text-slate-400" />
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索告警..."
              className="h-5 w-36 bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 md:w-52"
            />
          </div>
          <button
            onClick={() => setShowFilter((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-2xl border px-4 py-2 text-sm font-medium transition-colors",
              showFilter ? "border-sky-200 bg-sky-50 text-sky-700" : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
            )}
          >
            <FilterIcon className="h-4 w-4" /> 筛选
          </button>
        </div>
      </div>

      {showFilter && (
        <div className="flex flex-wrap items-center gap-2 rounded-[24px] border border-slate-200 bg-white p-3 shadow-sm">
          {([["all", "全部"], ["unack", "未处理"], ["ack", "已处理"]] as const).map((tab) => (
            <button
              key={tab[0]}
              onClick={() => setFilter(tab[0])}
              className={cn(
                "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                filter === tab[0] ? "bg-sky-600 text-white shadow-sm" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              )}
            >
              {tab[1]}
            </button>
          ))}
          <button
            onClick={() => {
              setFilter("all");
              setKeyword("");
            }}
            className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-sky-600"
          >
            重置
          </button>
        </div>
      )}

      <div className="space-y-3">
        {visible.map((alarm) => (
          <div key={alarm.id} className="rounded-[26px] border border-slate-200 bg-white p-4 shadow-sm transition-all hover:border-sky-200 hover:shadow-md">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <div className={cn("mt-0.5 flex h-10 w-10 items-center justify-center rounded-2xl text-white", levelDot(alarm.level) === "bg-red-500" ? "bg-red-500" : levelDot(alarm.level) === "bg-amber-500" ? "bg-amber-500" : "bg-sky-500")}>
                  <BellIcon className="h-4 w-4" />
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-slate-900">{alarm.projectName}</p>
                    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-bold", levelTone(alarm.level))}>
                      {alarm.level === "critical" ? "严重" : alarm.level === "warn" ? "警告" : "信息"}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{alarm.message}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-slate-500">
                    <span>{alarm.deviceName}</span>
                    <span>{new Date(alarm.timestamp).toLocaleString("zh-CN")}</span>
                    <span className={alarm.acknowledged ? "font-semibold text-emerald-600" : "font-semibold text-rose-500"}>
                      {alarm.acknowledged ? "已确认" : "未处理"}
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setSelectedId(alarm.id)}
                  className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50 hover:text-sky-600"
                >
                  <EyeIcon className="h-4 w-4" />
                </button>
                {!alarm.acknowledged && (
                  <button
                    onClick={() => {
                      setAlarms((prev) => prev.map((item) => (item.id === alarm.id ? { ...item, acknowledged: true } : item)));
                      setNotice(`告警 ${alarm.id} 已确认，并进入整改跟踪队列。`);
                    }}
                    className="inline-flex items-center gap-1 rounded-2xl bg-sky-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-sky-700"
                  >
                    确认处理
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelectedId(null)}
        title="告警详情"
        description="展示该告警的项目、设备、时间与处置建议。"
        footer={
          <div className="flex items-center justify-end gap-3">
            <button onClick={() => setSelectedId(null)} className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">关闭</button>
            {selected && !selected.acknowledged ? (
              <button
                onClick={() => {
                  setAlarms((prev) => prev.map((item) => (item.id === selected.id ? { ...item, acknowledged: true } : item)));
                  setSelectedId(null);
                  setNotice(`告警 ${selected.id} 已在详情面板中确认处理。`);
                }}
                className="rounded-2xl bg-sky-600 px-4 py-2 text-sm font-medium text-white"
              >
                确认并关闭
              </button>
            ) : null}
          </div>
        }
      >
        {selected ? (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">项目</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{selected.projectName}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs text-slate-500">设备</p>
                <p className="mt-2 text-sm font-semibold text-slate-900">{selected.deviceName}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4 md:col-span-2">
                <p className="text-xs text-slate-500">告警内容</p>
                <p className="mt-2 text-sm leading-6 text-slate-700">{selected.message}</p>
              </div>
            </div>
            <div className="rounded-[22px] border border-sky-100 bg-sky-50 p-4 text-sm text-slate-700">
              建议动作：立即通知现场负责人复核，补充影像采集，并在整改完成后重新发起 AI 验真或人工复检。
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
};
