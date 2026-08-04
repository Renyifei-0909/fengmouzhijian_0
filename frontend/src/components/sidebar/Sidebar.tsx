import React from "react";
import { NavLink } from "react-router";
import { cn } from "../../utils/cn";
import {
  DashboardIcon,
  ProjectIcon,
  AlarmIcon,
  ReportIcon,
  ShieldIcon,
  MapIcon,
  BlockchainIcon,
} from "../Icons";
import { COPY, PRIMARY_NAV, PRODUCT } from "../../lib/productCopy";

const iconByPath: Record<string, React.FC<{ className?: string }>> = {
  "/dashboard": DashboardIcon,
  "/projects": ProjectIcon,
  "/gis-map": MapIcon,
  "/backend-workflow": ShieldIcon,
  "/alarms": AlarmIcon,
  "/reports": ReportIcon,
  "/traceability": BlockchainIcon,
};

export const Sidebar: React.FC = () => {
  return (
    <aside className="fixed left-0 top-0 z-40 hidden h-screen w-72 border-r border-sky-100 bg-[linear-gradient(180deg,#07111f_0%,#0b1730_38%,#0d1b36_100%)] text-white shadow-[0_20px_60px_-24px_rgba(2,8,23,0.75)] lg:block">
      <div className="flex h-full flex-col">
        <div className="border-b border-white/10 px-5 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-400 to-blue-600 shadow-lg shadow-sky-700/30">
              <ShieldIcon className="h-5 w-5 text-white" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-wide text-white">{PRODUCT.name}</p>
              <p className="mt-1 text-xs leading-4 text-sky-200/80">{PRODUCT.subtitle}</p>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-5">
          <p className="mb-3 px-3 text-[11px] font-medium uppercase tracking-[0.26em] text-sky-200/60">
            主导航
          </p>
          <nav className="space-y-1.5" aria-label="主导航">
            {PRIMARY_NAV.map((item) => {
              const Icon = iconByPath[item.path] || ShieldIcon;
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={({ isActive }) =>
                    cn(
                      "group flex items-center gap-3 rounded-2xl px-3.5 py-3 text-sm transition-all duration-200",
                      isActive
                        ? "bg-gradient-to-r from-sky-500/20 to-blue-500/20 text-white shadow-[inset_0_0_0_1px_rgba(56,189,248,0.28)]"
                        : "text-slate-300 hover:bg-white/6 hover:text-white",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <div
                        className={cn(
                          "flex h-9 w-9 items-center justify-center rounded-xl transition-all",
                          isActive
                            ? "bg-sky-500/20 text-sky-300"
                            : "bg-white/5 text-slate-300 group-hover:bg-white/10 group-hover:text-sky-300",
                        )}
                      >
                        <Icon className="h-4.5 w-4.5" />
                      </div>
                      <span className="flex-1 font-medium">{item.label}</span>
                    </>
                  )}
                </NavLink>
              );
            })}
          </nav>
        </div>

        <div className="border-t border-white/10 px-5 py-4">
          <p className="text-xs font-medium text-white">{COPY.identityRole}</p>
          <p className="mt-1 text-[11px] text-sky-200/70">{COPY.identityOrg}</p>
        </div>
      </div>
    </aside>
  );
};
