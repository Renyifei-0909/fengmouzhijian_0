import React, { useEffect, useState } from "react";
import { ClipboardList, CloudOff, HardHat, UserRound, Wifi, Wrench } from "lucide-react";
import { NavLink, useLocation } from "react-router";
import { WorkerIdentityProvider, useWorkerIdentity } from "../../lib/workerIdentity";
import { cn } from "../../utils/cn";

const NAV_ITEMS = [
  { path: "/worker/work-orders", label: "我的工单", icon: ClipboardList },
  { path: "/worker/remediation", label: "整改反馈", icon: Wrench },
  { path: "/worker/profile", label: "我的账号", icon: UserRound },
] as const;

const PAGE_TITLES: Record<string, string> = {
  "/worker/work-orders": "我的工单",
  "/worker/remediation": "整改反馈",
  "/worker/profile": "我的账号",
};

const WorkerShellInner: React.FC<React.PropsWithChildren> = ({ children }) => {
  const location = useLocation();
  const { profile } = useWorkerIdentity();
  const [online, setOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const title = location.pathname.startsWith("/worker/work-orders/")
    ? "工单详情"
    : PAGE_TITLES[location.pathname] || "工人端";

  return (
    <div className="min-h-screen bg-[#f3f5f7] text-slate-950">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 border-r border-slate-800 bg-[#101820] text-white lg:flex lg:flex-col">
        <div className="border-b border-white/10 px-5 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#f2c94c] text-slate-950">
              <HardHat className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-semibold">锋眸智鉴</p>
              <p className="mt-1 text-xs text-slate-400">现场作业端</p>
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-5" aria-label="工人端主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                "flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium",
                isActive ? "bg-white text-slate-950" : "text-slate-300 hover:bg-white/8 hover:text-white",
              )}
            >
              <item.icon className="h-4.5 w-4.5" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 px-5 py-4">
          <p className="truncate text-sm font-medium">{profile.name || "未设置账号"}</p>
          <p className="mt-1 truncate text-xs text-slate-400">{profile.id || "请先完成身份设置"}</p>
        </div>
      </aside>

      <div className="min-h-screen lg:pl-60">
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur lg:px-8">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-slate-500 lg:hidden">锋眸智鉴 · 现场作业端</p>
              <h1 className="truncate text-lg font-semibold text-slate-950">{title}</h1>
            </div>
            <div
              className={cn(
                "inline-flex min-h-9 shrink-0 items-center gap-2 rounded-full border px-3 text-xs font-medium",
                online
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-amber-200 bg-amber-50 text-amber-900",
              )}
              role="status"
            >
              {online ? <Wifi className="h-3.5 w-3.5" /> : <CloudOff className="h-3.5 w-3.5" />}
              {online ? "网络正常" : "离线模式"}
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 pb-24 pt-4 lg:px-8 lg:pb-10 lg:pt-6">
          {children}
        </main>
      </div>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 grid h-[4.5rem] grid-cols-3 border-t border-slate-200 bg-white px-2 pb-[env(safe-area-inset-bottom)] lg:hidden"
        aria-label="工人端底部导航"
      >
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) => cn(
              "flex min-w-0 flex-col items-center justify-center gap-1 text-[11px] font-medium",
              isActive ? "text-slate-950" : "text-slate-500",
            )}
          >
            {({ isActive }) => (
              <>
                <span className={cn("flex h-7 w-11 items-center justify-center rounded-full", isActive && "bg-[#f2c94c]") }>
                  <item.icon className="h-4.5 w-4.5" aria-hidden="true" />
                </span>
                <span className="truncate">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
};

export const WorkerShell: React.FC<React.PropsWithChildren> = ({ children }) => (
  <WorkerIdentityProvider>
    <WorkerShellInner>{children}</WorkerShellInner>
  </WorkerIdentityProvider>
);

