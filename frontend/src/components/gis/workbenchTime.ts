import { serviceTimestamp } from "../../lib/serviceTime";

export function formatWorkbenchTime(value: string | null): string {
  if (!value) return "—";
  const timestamp = serviceTimestamp(value);
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
