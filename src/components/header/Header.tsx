import React, { useEffect, useMemo, useState } from "react";
import { cn } from "../../utils/cn";
import { SearchIcon, BellIcon, ChevronDownIcon, CloseIcon, ShieldIcon } from "../Icons";

export const Header: React.FC<{ title?: string; subtitle?: string }> = ({ title, subtitle }) => {
  const [searchOpen, setSearchOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 10);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const nowText = useMemo(
    () =>
      new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date()),
    []
  );

  return (
    <header
      className={cn(
        "sticky top-0 z-30 border-b px-4 py-4 backdrop-blur-xl md:px-6 lg:px-8",
        scrolled ? "border-sky-100 bg-white/82 shadow-[0_10px_40px_-24px_rgba(2,132,199,0.35)]" : "border-transparent bg-transparent"
      )}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <div className="hidden h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-blue-700 text-white shadow-lg shadow-sky-100 lg:flex">
              <ShieldIcon className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold tracking-tight text-slate-900">{title || "智慧监管平台"}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-500">
                {subtitle ? <span>{subtitle}</span> : null}
                <span className="hidden h-1 w-1 rounded-full bg-slate-300 md:inline-flex" />
                <span>当前时间 {nowText}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className={cn("transition-all duration-300", searchOpen ? "w-full md:w-80" : "w-11") }>
            {searchOpen ? (
              <div className="flex items-center gap-2 rounded-2xl border border-sky-200 bg-white px-3 py-2 shadow-sm shadow-sky-100">
                <SearchIcon className="h-4 w-4 text-sky-500" />
                <input
                  autoFocus
                  placeholder="搜索项目、设备、告警、档案编号..."
                  className="w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400"
                />
                <button
                  onClick={() => setSearchOpen(false)}
                  className="flex h-7 w-7 items-center justify-center rounded-full text-slate-400 transition-all hover:bg-sky-50 hover:text-sky-700"
                >
                  <CloseIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setSearchOpen(true)}
                className="flex h-11 w-11 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 shadow-sm transition-all hover:border-sky-200 hover:text-sky-700"
              >
                <SearchIcon className="h-4.5 w-4.5" />
              </button>
            )}
          </div>

          <div className="relative">
            <button
              onClick={() => setNoticeOpen((prev) => !prev)}
              className="relative flex h-11 w-11 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 shadow-sm transition-all hover:border-sky-200 hover:text-sky-700"
            >
              <BellIcon className="h-4.5 w-4.5" />
              <span className="absolute right-3 top-3 h-2.5 w-2.5 rounded-full bg-rose-500 ring-2 ring-white" />
            </button>
            {noticeOpen ? (
              <div className="absolute right-0 top-14 w-80 rounded-3xl border border-slate-200 bg-white p-4 shadow-2xl shadow-sky-100">
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">通知中心</p>
                  <button onClick={() => setNoticeOpen(false)} className="text-xs text-sky-700 hover:text-sky-800">关闭</button>
                </div>
                <div className="space-y-2">
                  {[
                    "隐蔽工程 AI 验真任务已完成",
                    "监控 CAM-06 离线超过 24 小时",
                    "可信档案 ARC-2026-001 已生成",
                  ].map((item) => (
                    <div key={item} className="rounded-2xl border border-slate-100 bg-slate-50 px-3 py-3 text-sm text-slate-600">
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          <button
            onClick={() => window.alert("已进入账户设置页，可在左侧菜单继续操作。")}
            className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-3 py-2 shadow-sm transition-all hover:border-sky-200"
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-blue-700 text-sm font-semibold text-white">
              张
            </div>
            <div className="hidden text-left md:block">
              <p className="text-sm font-semibold text-slate-900">张工程师</p>
              <p className="text-xs text-slate-500">项目经理 / Demo 管理员</p>
            </div>
            <ChevronDownIcon className="hidden h-4 w-4 text-slate-400 md:block" />
          </button>
        </div>
      </div>
    </header>
  );
};
