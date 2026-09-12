import React, { useRef, useState } from "react";
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
  ChevronRightIcon,
  ChevronDownIcon,
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

export const SIDEBAR_MIN_WIDTH = 208;
export const SIDEBAR_MAX_WIDTH = 384;
export const SIDEBAR_DEFAULT_WIDTH = 288;
export const SIDEBAR_COLLAPSED_WIDTH = 80;

type SidebarProps = {
  collapsed: boolean;
  width: number;
  onToggle: () => void;
  onWidthChange: (width: number) => void;
};

export const Sidebar: React.FC<SidebarProps> = ({ collapsed, width, onToggle, onWidthChange }) => {
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const effectiveWidth = collapsed ? SIDEBAR_COLLAPSED_WIDTH : width;

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (collapsed) return;
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: width };
    setDragging(true);

    const handleMove = (moveEvent: PointerEvent) => {
      const state = dragRef.current;
      if (!state) return;
      const next = Math.min(
        SIDEBAR_MAX_WIDTH,
        Math.max(SIDEBAR_MIN_WIDTH, state.startWidth + (moveEvent.clientX - state.startX)),
      );
      onWidthChange(next);
    };
    const handleUp = () => {
      dragRef.current = null;
      setDragging(false);
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
  };

  return (
    <aside
      style={{ width: effectiveWidth }}
      className={cn(
        "sidebar-bg fixed left-0 top-0 z-40 hidden h-screen flex-col border-r border-sky-100/15 text-white shadow-[0_20px_60px_-24px_rgba(2,8,23,0.75)] [text-shadow:0_1px_10px_rgba(2,8,23,0.45)] lg:flex",
        !dragging && "transition-[width] duration-200 ease-out",
      )}
    >
      {/* 拖拽调宽手柄（收起时禁用） */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整导航宽度"
        onPointerDown={handlePointerDown}
        className={cn(
          "group absolute right-0 top-0 z-50 h-full w-3 cursor-col-resize touch-none",
          collapsed && "pointer-events-none opacity-0",
        )}
      >
        <div className="absolute right-1 top-1/2 h-20 w-1 -translate-y-1/2 rounded-full bg-white/0 transition-colors duration-200 group-hover:bg-sky-300/70" />
      </div>

      {/* 顶部：品牌区 + 收起/展开按钮 */}
      <div className={cn("relative border-b border-white/10", collapsed ? "px-3 py-4" : "px-4 py-4")}>
        <div className={cn("flex items-center gap-3", collapsed && "justify-center")}>
          <div className="h-11 w-11 shrink-0 overflow-hidden rounded-2xl shadow-lg shadow-primary-900/40 ring-1 ring-sky-300/30">
            <img
              src="/brand/app-logo-mark.png"
              alt="烽眸智鉴"
              className="h-full w-full object-cover"
              draggable={false}
            />
          </div>
          <div
            className={cn(
              "min-w-0 overflow-hidden transition-all duration-200 ease-out",
              collapsed ? "w-0 opacity-0" : "w-auto opacity-100",
            )}
            aria-hidden={collapsed}
          >
            <p className="truncate text-sm font-semibold tracking-wide text-white">{PRODUCT.name}</p>
            <p className="mt-1 truncate text-xs leading-4 text-sky-200/80">{PRODUCT.subtitle}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "展开导航" : "收起导航"}
          title={collapsed ? "展开导航" : "收起导航"}
          className="absolute -right-4 top-7 z-50 flex h-8 w-8 items-center justify-center rounded-full border border-sky-300/30 bg-[#0b1a33] text-sky-200 shadow-lg transition hover:border-sky-300/60 hover:bg-[#12294d] hover:text-white"
        >
          <ChevronRightIcon
            className={cn("h-4 w-4 transition-transform duration-200", !collapsed && "rotate-180")}
          />
        </button>
      </div>

      {/* 导航列表 */}
      <nav className={cn("flex-1 overflow-y-auto overflow-x-hidden py-4", collapsed ? "px-2" : "px-4")}>
        {!collapsed ? (
          <p className="mb-3 px-3 text-[11px] font-medium tracking-[0.2em] text-sky-200/60">主导航</p>
        ) : null}
        <div className="space-y-1.5" aria-label="主导航">
          {PRIMARY_NAV.map((item) => {
            const Icon = iconByPath[item.path] || ShieldIcon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  cn(
                    "group flex items-center rounded-2xl text-sm transition-all duration-200",
                    collapsed ? "justify-center px-0 py-2.5" : "gap-3 px-3.5 py-3",
                    isActive
                      ? "bg-gradient-to-r from-primary-500/25 to-primary-600/20 text-white shadow-[inset_0_0_0_1px_rgba(56,189,248,0.35)]"
                      : "text-slate-300 hover:bg-white/6 hover:text-white",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <div
                      className={cn(
                        "flex items-center justify-center rounded-xl transition-all",
                        collapsed ? "h-9 w-9" : "h-9 w-9",
                        isActive
                          ? "bg-primary-500/25 text-sky-300"
                          : "bg-white/5 text-slate-300 group-hover:bg-white/10 group-hover:text-sky-300",
                      )}
                    >
                      <Icon className="h-4.5 w-4.5" />
                    </div>
                    {!collapsed ? <span className="flex-1 truncate font-medium">{item.label}</span> : null}
                  </>
                )}
              </NavLink>
            );
          })}
        </div>
      </nav>

      {/* 底部：登录身份卡片 */}
      <div className={cn("border-t border-white/10", collapsed ? "px-2 py-3" : "px-3 py-3")}>
        <div
          className={cn(
            "flex items-center rounded-2xl border border-white/10 bg-white/[0.06] backdrop-blur-sm transition hover:border-sky-300/25 hover:bg-white/[0.1]",
            collapsed ? "justify-center px-2 py-2.5" : "gap-3 px-3 py-3",
          )}
          title={collapsed ? `${COPY.identityRole} · ${COPY.identityOrg}` : undefined}
        >
          <div className="relative shrink-0">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-sky-400 to-primary-600 text-sm font-semibold text-white shadow-lg shadow-primary-900/40 ring-2 ring-sky-300/20">
              管
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-emerald-400 ring-2 ring-[#0a1730]" />
          </div>
          <div
            className={cn(
              "flex min-w-0 flex-1 items-center gap-3 overflow-hidden transition-all duration-200 ease-out",
              collapsed ? "w-0 opacity-0" : "w-auto opacity-100",
            )}
            aria-hidden={collapsed}
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-white">{COPY.identityRole}</p>
              <p className="mt-0.5 truncate text-[11px] text-sky-200/70">{COPY.identityOrg}</p>
            </div>
            <ChevronDownIcon className="h-4 w-4 shrink-0 text-sky-200/50" />
          </div>
        </div>
      </div>
    </aside>
  );
};
