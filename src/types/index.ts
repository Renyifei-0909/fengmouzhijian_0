export interface Project {
  id: string;
  name: string;
  location: string;
  manager: string;
  status: "pending" | "active" | "paused" | "completed";
  progress: number;
  startDate: string;
  endDate: string;
  participants: number;
  cameras: number;
  alerts: number;
  description: string;
}

export interface Device {
  id: string;
  name: string;
  type: "camera" | "sensor" | "edge" | "helmet";
  status: "online" | "offline" | "warning" | "fault";
  location: string;
  lastHeartbeat: string;
  battery?: number;
  projectId: string;
}

export interface Alarm {
  id: string;
  type: "safety" | "quality" | "progress" | "device";
  deviceId: string;
  deviceName: string;
  projectId: string;
  projectName: string;
  level: "info" | "warn" | "critical";
  message: string;
  timestamp: string;
  image: string;
  acknowledged: boolean;
}

export interface Report {
  id: string;
  name: string;
  type: "weekly" | "monthly" | "quality" | "safety" | "progress";
  projectId: string;
  projectName: string;
  period: string;
  createdBy: string;
  createdAt: string;
  size: string;
  status: "generated" | "pending";
}

export interface DashboardStats {
  totalProjects: number;
  activeProjects: number;
  totalDevices: number;
  onlineDevices: number;
  todayAlarms: number;
  criticalAlarms: number;
  avgProgress: number;
  completionRate: number;
}

export interface ChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color?: string;
  }[];
}
