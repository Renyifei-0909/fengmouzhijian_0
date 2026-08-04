import React, { useState } from "react";
import { Notice } from "../components/ui/Notice";
import { SettingsIcon, DatabaseIcon, ShieldIcon, CpuIcon } from "../components/Icons";

export const SystemSettingsPage: React.FC = () => {
  const [networkMode, setNetworkMode] = useState("端边云协同");
  const [retention, setRetention] = useState("180");
  const [logLevel, setLogLevel] = useState("INFO");
  const [options, setOptions] = useState({ weakNet: true, audit: true, cache: true, autoArchive: true });
  const [notice, setNotice] = useState("");

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <h2 className="text-2xl font-semibold">系统设置</h2>
        <p className="mt-2 text-sm text-sky-100">管理协同模式、存储策略、审计日志与系统级功能开关。</p>
      </div>

      {notice ? <Notice type="success" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold text-slate-900">协同与存储策略</h3>
            <DatabaseIcon className="h-5 w-5 text-sky-600" />
          </div>
          <div className="space-y-4">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700">协同模式</span>
              <select
                value={networkMode}
                onChange={(event) => setNetworkMode(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-sky-300 focus:bg-white"
              >
                <option>端边云协同</option>
                <option>边缘优先</option>
                <option>云端集中</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700">数据保留天数</span>
              <input
                value={retention}
                onChange={(event) => setRetention(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-sky-300 focus:bg-white"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700">日志等级</span>
              <select
                value={logLevel}
                onChange={(event) => setLogLevel(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none focus:border-sky-300 focus:bg-white"
              >
                <option>DEBUG</option>
                <option>INFO</option>
                <option>WARN</option>
                <option>ERROR</option>
              </select>
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                onClick={() => setNotice("系统配置已保存，协同策略已生效。")}
                className="rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                保存系统配置
              </button>
              <button
                onClick={() => setNotice("样例备份包已生成，含配置快照与日志摘要。")}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                立即备份
              </button>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">系统开关</h3>
              <SettingsIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="space-y-3">
              {[
                ["weakNet", "开启弱网缓存与断点续传"],
                ["audit", "开启全量审计日志"],
                ["cache", "开启边缘节点本地缓存"],
                ["autoArchive", "开启自动归档与上链"],
              ].map(([key, label]) => (
                <button
                  key={String(key)}
                  onClick={() => setOptions((prev) => ({ ...prev, [key]: !prev[key as keyof typeof prev] }))}
                  className="flex w-full items-center justify-between rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3 text-left"
                >
                  <span className="text-sm text-slate-700">{label}</span>
                  <span className={`inline-flex h-7 w-12 items-center rounded-full p-1 transition-all ${options[key as keyof typeof options] ? "bg-sky-600 justify-end" : "bg-slate-300 justify-start"}`}>
                    <span className="h-5 w-5 rounded-full bg-white" />
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">系统检测</h3>
              <ShieldIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                onClick={() => setNotice("数据库连接测试通过，响应时间 32ms。")}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                测试数据库
              </button>
              <button
                onClick={() => setNotice("边缘节点联通性检测完成，在线率 96.8%。")}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                测试边缘节点
              </button>
              <button
                onClick={() => setNotice("审计日志导出成功，已生成近 7 日操作记录。")}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                导出审计日志
              </button>
              <button
                onClick={() => setNotice("系统健康巡检完成，当前未发现阻断性风险。")}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                执行健康巡检
              </button>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">系统资源</h3>
              <CpuIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid grid-cols-3 gap-3 text-center">
              {[
                ["CPU", "42%"],
                ["内存", "61%"],
                ["存储", "73%"],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className="mt-2 text-xl font-semibold text-slate-900">{value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
