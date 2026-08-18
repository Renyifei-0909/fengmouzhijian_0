import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from "react-router";
import { Sidebar } from "./components/sidebar/Sidebar";
import { Header } from "./components/header/Header";
import { WorkerShell } from "./components/worker/WorkerShell";
import { cn } from "./utils/cn";
import {
  AlarmIcon,
  DashboardIcon,
  ProjectIcon,
  ReportIcon,
  BlockchainIcon,
  ShieldIcon,
  MapIcon,
} from "./components/Icons";
import {
  DashboardPage,
  ProjectsPage,
  ProjectDetailPage,
  AlarmsPage,
  DevicesPage,
  AnalyticsPage,
  ReportsPage,
  HiddenAIPage,
  GISMapPage,
  WorkOrderPage,
  DataCockpitPage,
  TraceabilityPage,
  ModelServicePage,
  AccountSettingsPage,
  SystemSettingsPage,
  BackendWorkflowPage,
  WorkerWorkOrdersPage,
  WorkerWorkOrderPage,
  WorkerRemediationPage,
  WorkerProfilePage,
} from "./pages";
import { PAGE_META, PRIMARY_NAV } from "./lib/productCopy";

const mobileIconByPath: Record<string, React.FC<{ className?: string }>> = {
  "/dashboard": DashboardIcon,
  "/projects": ProjectIcon,
  "/gis-map": MapIcon,
  "/backend-workflow": ShieldIcon,
  "/alarms": AlarmIcon,
  "/reports": ReportIcon,
  "/traceability": BlockchainIcon,
};

const mobileShortLabel: Record<string, string> = {
  "/dashboard": "总览",
  "/projects": "项目",
  "/gis-map": "作业",
  "/backend-workflow": "核验",
  "/alarms": "整改",
  "/reports": "报告",
  "/traceability": "追溯",
};

const mobileNavItems = PRIMARY_NAV.map((item) => ({
  label: mobileShortLabel[item.path] || item.label,
  path: item.path,
  icon: mobileIconByPath[item.path] || ShieldIcon,
}));

const MobileQuickNav: React.FC = () => (
  <nav aria-label="移动端核心功能" className="mb-4 flex gap-2 overflow-x-auto pb-1 lg:hidden">
    {mobileNavItems.map((item) => (
      <NavLink
        key={item.path}
        to={item.path}
        className={({ isActive }) =>
          cn(
            "inline-flex flex-shrink-0 items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition-all",
            isActive
              ? "border-sky-200 bg-sky-50 text-sky-700"
              : "border-slate-200 bg-white text-slate-600",
          )
        }
      >
        <item.icon className="h-4 w-4" />
        {item.label}
      </NavLink>
    ))}
  </nav>
);

/** Internal/prototype routes are not commercial primary nav; gate with explicit flag. */
const ENABLE_INTERNAL_ROUTES = import.meta.env.VITE_ENABLE_INTERNAL_ROUTES === "true";

const NotOpenPage: React.FC<{ title: string }> = ({ title }) => (
  <div className="rounded-[24px] border border-slate-200 bg-white p-8 text-center shadow-sm">
    <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
    <p className="mt-2 text-sm text-slate-600">该功能未在当前正式入口开放。</p>
  </div>
);

const AppShell: React.FC = () => {
  const location = useLocation();

  const getPageMeta = () => {
    const path = location.pathname;
    if (path.startsWith("/projects/")) {
      return { title: "项目详情", subtitle: "工程信息与核验记录" };
    }
    if (path.startsWith("/work-orders/")) {
      return { title: "施工工单", subtitle: "现场资料提交与核验" };
    }
    return PAGE_META[path] || { title: "锋眸智鉴", subtitle: "通信工程施工合规管理平台" };
  };

  const meta = getPageMeta();

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,#dbeafe_0%,#eff6ff_22%,#f8fafc_55%,#f8fafc_100%)]">
      <Sidebar />
      <div className="min-h-screen lg:pl-72">
        <Header title={meta.title} subtitle={meta.subtitle} />
        <main className="px-4 pb-8 pt-4 md:px-6 lg:px-8">
          <MobileQuickNav />
          <Routes>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/backend-workflow" element={<BackendWorkflowPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectDetailPage />} />
            <Route path="/alarms" element={<AlarmsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/gis-map" element={<GISMapPage />} />
            <Route path="/work-orders/:id" element={<WorkOrderPage />} />
            <Route path="/traceability" element={<TraceabilityPage />} />
            <Route path="/settings/account" element={<AccountSettingsPage />} />
            {ENABLE_INTERNAL_ROUTES ? (
              <>
                <Route path="/ai-verification" element={<HiddenAIPage />} />
                <Route path="/devices" element={<DevicesPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/data-cockpit" element={<DataCockpitPage />} />
                <Route path="/model-service" element={<ModelServicePage />} />
                <Route path="/settings/system" element={<SystemSettingsPage />} />
              </>
            ) : (
              <>
                <Route path="/ai-verification" element={<NotOpenPage title="AI 核验" />} />
                <Route path="/devices" element={<NotOpenPage title="设备监控" />} />
                <Route path="/analytics" element={<NotOpenPage title="分析" />} />
                <Route path="/data-cockpit" element={<NotOpenPage title="数据看板" />} />
                <Route path="/model-service" element={<NotOpenPage title="模型服务" />} />
                <Route path="/settings/system" element={<NotOpenPage title="系统设置" />} />
              </>
            )}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
};

const WorkerApp: React.FC = () => (
  <WorkerShell>
    <Routes>
      <Route path="/worker" element={<Navigate to="/worker/work-orders" replace />} />
      <Route path="/worker/work-orders" element={<WorkerWorkOrdersPage />} />
      <Route path="/worker/work-orders/:id" element={<WorkerWorkOrderPage />} />
      <Route path="/worker/remediation" element={<WorkerRemediationPage />} />
      <Route path="/worker/profile" element={<WorkerProfilePage />} />
      <Route path="/worker/*" element={<Navigate to="/worker/work-orders" replace />} />
    </Routes>
  </WorkerShell>
);

const AppRouter: React.FC = () => {
  const location = useLocation();
  return location.pathname.startsWith("/worker") ? <WorkerApp /> : <AppShell />;
};

const App: React.FC = () => (
  <BrowserRouter>
    <AppRouter />
  </BrowserRouter>
);

export default App;
