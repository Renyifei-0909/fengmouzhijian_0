import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "./components/sidebar/Sidebar";
import { Header } from "./components/header/Header";
import { cn } from "./utils/cn";
import { DashboardIcon, EyeIcon, MapIcon, DatabaseIcon, BlockchainIcon, CpuIcon, SettingsIcon } from "./components/Icons";
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
  DataCockpitPage,
  TraceabilityPage,
  ModelServicePage,
  AccountSettingsPage,
  SystemSettingsPage,
} from "./pages";

const mobileNavItems = [
  { label: "总览", path: "/dashboard", icon: DashboardIcon },
  { label: "AI验真", path: "/ai-verification", icon: EyeIcon },
  { label: "GIS", path: "/gis-map", icon: MapIcon },
  { label: "看板", path: "/data-cockpit", icon: DatabaseIcon },
  { label: "溯源", path: "/traceability", icon: BlockchainIcon },
  { label: "模型", path: "/model-service", icon: CpuIcon },
  { label: "设置", path: "/settings/account", icon: SettingsIcon },
];

const MobileQuickNav: React.FC = () => (
  <div className="mb-4 flex gap-2 overflow-x-auto pb-1 lg:hidden">
    {mobileNavItems.map((item) => (
      <NavLink
        key={item.path}
        to={item.path}
        className={({ isActive }) =>
          cn(
            "inline-flex flex-shrink-0 items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition-all",
            isActive
              ? "border-sky-200 bg-sky-50 text-sky-700"
              : "border-slate-200 bg-white text-slate-600"
          )
        }
      >
        <item.icon className="h-4 w-4" />
        {item.label}
      </NavLink>
    ))}
  </div>
);

const AppShell: React.FC = () => {
  const location = useLocation();

  const getPageMeta = () => {
    const path = location.pathname;
    if (path.startsWith("/projects/")) return { title: "项目详情", subtitle: "工程全量监管与可信交付视图" };

    const metaMap: Record<string, { title: string; subtitle: string }> = {
      "/dashboard": { title: "监管总览", subtitle: "科技蓝监管驾驶舱" },
      "/ai-verification": { title: "隐蔽验真 AI", subtitle: "影像结构化自动验真中心" },
      "/projects": { title: "项目管理", subtitle: "在建工程全景视图" },
      "/alarms": { title: "告警中心", subtitle: "安全、质量、设备异常一体管控" },
      "/devices": { title: "设备监控", subtitle: "多源感知端与边缘节点管理" },
      "/analytics": { title: "数据分析", subtitle: "多维指标与趋势洞察" },
      "/reports": { title: "报表中心", subtitle: "交付、质量、安全报表输出" },
      "/gis-map": { title: "GIS 地图", subtitle: "图物动态对齐与点位联动" },
      "/data-cockpit": { title: "数据看板", subtitle: "监管数据驾驶舱 Demo" },
      "/traceability": { title: "溯源查询", subtitle: "可信档案核验与链路追踪" },
      "/model-service": { title: "模型服务", subtitle: "端边云模型编排与运行面板" },
      "/settings/account": { title: "账户设置", subtitle: "个人资料与通知偏好" },
      "/settings/system": { title: "系统设置", subtitle: "系统级策略与资源配置" },
    };

    return metaMap[path] || { title: "智慧监管平台", subtitle: "多源感知赋能端边云协同系统" };
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
            <Route path="/ai-verification" element={<HiddenAIPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectDetailPage />} />
            <Route path="/alarms" element={<AlarmsPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/gis-map" element={<GISMapPage />} />
            <Route path="/data-cockpit" element={<DataCockpitPage />} />
            <Route path="/traceability" element={<TraceabilityPage />} />
            <Route path="/model-service" element={<ModelServicePage />} />
            <Route path="/settings/account" element={<AccountSettingsPage />} />
            <Route path="/settings/system" element={<SystemSettingsPage />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
};

const App: React.FC = () => (
  <BrowserRouter>
    <AppShell />
  </BrowserRouter>
);

export default App;
