import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { Link, NavLink, useLocation } from "react-router";
import {
  AlarmIcon, BlockchainIcon, ChevronRightIcon, DashboardIcon, MapIcon,
  ProjectIcon, ReportIcon, ShieldIcon, UserIcon,
} from "../Icons";
import { PRIMARY_NAV, PRODUCT } from "../../lib/productCopy";
import { cn } from "../../utils/cn";

const icons = [DashboardIcon, ProjectIcon, MapIcon, ShieldIcon, AlarmIcon, ReportIcon, BlockchainIcon];

export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 340;
export const SIDEBAR_DEFAULT_WIDTH = 224;
export const SIDEBAR_COLLAPSED_WIDTH = 72;

type SidebarProps = {
  collapsed: boolean;
  width: number;
  onToggle: () => void;
  onWidthChange: (width: number) => void;
};

const limitWidth = (width: number) => Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, width));

export function Sidebar({ collapsed, width, onToggle, onWidthChange }: SidebarProps) {
  const { pathname } = useLocation();
  const drag = useRef<{ x: number; width: number } | null>(null);

  const resizeStart = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { x: event.clientX, width };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const resizeMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current) onWidthChange(limitWidth(drag.current.width + event.clientX - drag.current.x));
  };

  const resizeKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    if (event.key === "Home") onWidthChange(SIDEBAR_MIN_WIDTH);
    else if (event.key === "End") onWidthChange(SIDEBAR_MAX_WIDTH);
    else onWidthChange(limitWidth(width + (event.key === "ArrowLeft" ? -8 : 8)));
  };

  return (
    <aside className={cn("fm-sidebar", collapsed && "fm-sidebar-compact")}>
      <div className="fm-side-brand">
        <Link to="/dashboard" className="fm-brand-link" aria-label={PRODUCT.name + "项目总览"}>
          <img src="/brand/app-logo-integrated.webp" alt="" width={44} height={44} draggable={false} />
          {!collapsed && <span><strong>{PRODUCT.name}</strong><small>通信工程监管与交付</small></span>}
        </Link>
        <button
          type="button"
          className="fm-side-toggle"
          onClick={onToggle}
          aria-label={collapsed ? "展开导航" : "收起导航"}
          aria-expanded={!collapsed}
          aria-controls="fm-nav"
          title={collapsed ? "展开导航" : "收起导航"}
        >
          <ChevronRightIcon className={cn("size-4", !collapsed && "rotate-180")} />
        </button>
      </div>

      <nav id="fm-nav" aria-label="主导航" className="fm-side-nav">
        {!collapsed && <p className="fm-side-caption">工程管理</p>}
        {PRIMARY_NAV.map((item, index) => {
          const Icon = icons[index] ?? ShieldIcon;
          const workOrder = item.path === "/gis-map" && pathname.startsWith("/work-orders/");
          return (
            <NavLink
              key={item.path}
              to={item.path}
              aria-label={collapsed ? item.label : undefined}
              aria-current={workOrder ? "location" : undefined}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) => cn("fm-side-link", (isActive || workOrder) && "is-active")}
            >
              <Icon className="size-[19px]" />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          );
        })}
      </nav>

      <div className="fm-side-footer">
        <NavLink to="/settings/account" className={({ isActive }) => cn("fm-side-link", isActive && "is-active")} aria-label="工作台设置" title="工作台设置">
          <UserIcon className="size-[19px]" />
          {!collapsed && <span>工作台设置</span>}
        </NavLink>
      </div>

      {!collapsed && <div
        className="fm-side-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="调整导航宽度"
        aria-valuemin={SIDEBAR_MIN_WIDTH}
        aria-valuemax={SIDEBAR_MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={resizeStart}
        onPointerMove={resizeMove}
        onLostPointerCapture={() => { drag.current = null; }}
        onKeyDown={resizeKey}
      />}
    </aside>
  );
}
