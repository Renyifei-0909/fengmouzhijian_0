import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { cn } from "../../utils/cn";
import {
  DashboardIcon,
  ProjectIcon,
  AlarmIcon,
  DeviceIcon,
  AnalyticsIcon,
  ReportIcon,
  UserIcon,
  SettingsIcon,
  LogoutIcon,
  ShieldIcon,
  MapIcon,
  DatabaseIcon,
  BlockchainIcon,
  CpuIcon,
  EyeIcon,
} from "../Icons";

interface NavItem {
  label: string;
  icon: React.FC<{ className?: string }>;
  path: string;
  badge?: number | string;
}

const mainNavItems: NavItem[] = [
  { label: "监管总览", icon: DashboardIcon, path: "/dashboard" },
  { label: "隐蔽验真 AI", icon: EyeIcon, path: "/ai-verification" },
  { label: "项目管理", icon: ProjectIcon, path: "/projects" },
  { label: "告警中心", icon: AlarmIcon, path: "/alarms", badge: 37 },
  { label: "设备监控", icon: DeviceIcon, path: "/devices" },
  { label: "数据分析", icon: AnalyticsIcon, path: "/analytics" },
  { label: "报表中心", icon: ReportIcon, path: "/reports" },
];

const quickActions = [
  { label: "GIS地图", icon: MapIcon, path: "/gis-map" },
  { label: "数据看板", icon: DatabaseIcon, path: "/data-cockpit" },
  { label: "溯源查询", icon: BlockchainIcon, path: "/traceability" },
  { label: "模型服务", icon: CpuIcon, path: "/model-service" },
];

const settingsNavItems: NavItem[] = [
  { label: "账户设置", icon: UserIcon, path: "/settings/account" },
  { label: "系统设置", icon: SettingsIcon, path: "/settings/system" },
];

export const Sidebar: React.FC = () => {
  const navigate = useNavigate();

  return (
    <aside className="fixed left-0 top-0 z-40 hidden h-screen w-72 border-r border-sky-100 bg-[linear-gradient(180deg,#07111f_0%,#0b1730_38%,#0d1b36_100%)] text-white shadow-[0_20px_60px_-24px_rgba(2,8,23,0.75)] lg:block">
      <div className="flex h-full flex-col">
        <div className="border-b border-white/10 px-5 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-400 to-blue-600 shadow-lg shadow-sky-700/30">
              <ShieldIcon className="h-5 w-5 text-white" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-wide text-white">智能监管与可信交付</p>
              <p className="mt-1 text-xs text-sky-200/80">多源感知 · 端边云协同 · 科技蓝 Demo</p>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-5">
          <div>
            <p className="mb-3 px-3 text-[11px] font-medium uppercase tracking-[0.26em] text-sky-200/60">主导航</p>
            <nav className="space-y-1.5">
              {mainNavItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={({ isActive }) =>
                    cn(
                      "group flex items-center gap-3 rounded-2xl px-3.5 py-3 text-sm transition-all duration-200",
                      isActive
                        ? "bg-gradient-to-r from-sky-500/20 to-blue-500/20 text-white shadow-[inset_0_0_0_1px_rgba(56,189,248,0.28)]"
                        : "text-slate-300 hover:bg-white/6 hover:text-white"
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <div
                        className={cn(
                          "flex h-9 w-9 items-center justify-center rounded-xl transition-all",
                          isActive ? "bg-sky-500/20 text-sky-300" : "bg-white/5 text-slate-300 group-hover:bg-white/10 group-hover:text-sky-300"
                        )}
                      >
                        <item.icon className="h-4.5 w-4.5" />
                      </div>
                      <span className="flex-1 font-medium">{item.label}</span>
                      {item.badge ? <span className="rounded-full bg-rose-500/20 px-2 py-0.5 text-[11px] font-semibold text-rose-200">{item.badge}</span> : null}
                    </>
                  )}
                </NavLink>
              ))}
            </nav>
          </div>

          <div className="mt-6">
            <p className="mb-3 px-3 text-[11px] font-medium uppercase tracking-[0.26em] text-sky-200/60">快捷入口</p>
            <div className="grid grid-cols-2 gap-2.5">
              {quickActions.map((item) => (
                <button
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  className="group rounded-2xl border border-white/8 bg-white/5 p-3 text-left transition-all duration-200 hover:border-sky-400/30 hover:bg-sky-500/10"
                >
                  <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-sky-500/12 text-sky-300 transition-all group-hover:bg-sky-500/20">
                    <item.icon className="h-5 w-5" />
                  </div>
                  <p className="text-sm font-medium text-white">{item.label}</p>
                  <p className="mt-1 text-[11px] text-slate-400">点击进入 Demo</p>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="border-t border-white/10 p-4">
          <div className="space-y-1.5">
            {settingsNavItems.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-2xl px-3.5 py-3 text-sm transition-all duration-200",
                    isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/6 hover:text-white"
                  )
                }
              >
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/5">
                  <item.icon className="h-4.5 w-4.5" />
                </div>
                <span>{item.label}</span>
              </NavLink>
            ))}
            <button
              onClick={() => window.alert("演示环境已退出登录（Demo）。")}
              className="flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-sm text-slate-300 transition-all duration-200 hover:bg-white/6 hover:text-white"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/5">
                <LogoutIcon className="h-4.5 w-4.5" />
              </div>
              <span>退出登录</span>
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
};
