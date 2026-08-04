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
      <Notice
        type="warning"
        message="原型页 · 开关与策略仅影响本页 UI。不会修改后端配置、数据库保留策略、审计导出或“上链”。哈希链是本地完整性校验，不是区块链。"
      />
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <h2 className="text-2xl font-semibold">系统设置（原型）</h2>
        <p className="mt-2 text-sm text-sky-100">演示运维配置布局；真实配置请改环境变量与部署清单。</p>
      </div>

      {notice ? <Notice type="info" message={notice} /> : null}

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
                onClick={() =>
                  setNotice("原型动作：未保存系统配置到后端或磁盘。")
                }
                className="rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                保存系统配置
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未生成备份包文件。")
                }
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
                ["autoArchive", "开启自动归档（原型；非上链/区块链）"],
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
                onClick={() =>
                  setNotice("原型动作：未测试数据库连接，也未测量响应时间。")
                }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                测试数据库
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未检测边缘节点，无在线率数据。")
                }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                测试边缘节点
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未导出审计日志文件。")
                }
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                导出审计日志
              </button>
              <button
                onClick={() =>
                  setNotice("原型动作：未执行系统健康巡检。")
                }
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
