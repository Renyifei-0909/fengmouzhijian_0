import type { ReactNode } from "react";
import { AlertCircle, FolderOpen } from "lucide-react";
import { serviceTimestamp } from "../lib/serviceTime";
import { friendlyApiError } from "../lib/productCopy";
import "./records-workspace.css";

export type RecordTone = "neutral" | "blue" | "amber" | "green" | "red";

export function RecordStatus({ tone = "neutral", children }: { tone?: RecordTone; children: ReactNode }) {
  return <span className="fm-record-status" data-tone={tone}>{children}</span>;
}

export function RecordMetrics({ entries }: { entries: Array<{ label: string; value: ReactNode }> }) {
  return <dl className="fm-record-metrics">
    {entries.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
  </dl>;
}

export function RecordMeta({ entries }: { entries: Array<[string, ReactNode]> }) {
  return <dl className="fm-record-meta">
    {entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "—"}</dd></div>)}
  </dl>;
}

export function RecordEmpty({ children, loading = false }: { children: ReactNode; loading?: boolean }) {
  return <div className="fm-record-empty" role={loading ? "status" : undefined}>
    {loading ? <span className="fm-record-spinner" aria-hidden="true" /> : <FolderOpen size={25} strokeWidth={1.4} aria-hidden="true" />}
    <p>{children}</p>
  </div>;
}

export function RecordError({ message, action }: { message: string; action?: ReactNode }) {
  const friendly = friendlyApiError(message);
  return <div className="fm-record-error" role="alert">
    <AlertCircle size={17} aria-hidden="true" />
    <div>
      <p>{friendly}</p>
      {message !== friendly ? <details><summary>查看原因</summary><p>{message}</p></details> : null}
    </div>
    {action}
  </div>;
}

export function recordError(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function recordTime(value: string | null | undefined, short = false) {
  if (!value) return "—";
  const date = new Date(serviceTimestamp(value));
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    year: short ? undefined : "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function recordObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function recordText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

export function recordProcedure(code: string) {
  return code === "TRENCH-BEFORE-BACKFILL" ? "沟槽回填前" : code;
}

export const REPORT_STATES: Record<string, { label: string; tone: RecordTone }> = {
  final: { label: "已封存", tone: "green" },
  reviewed_demo: { label: "已封存 · 规则辅助", tone: "neutral" },
  reviewed_placeholder: { label: "已封存 · 人工复核", tone: "green" },
  reviewed_non_evaluated: { label: "已封存 · 辅助识别", tone: "blue" },
};

export function ReportStatus({ value }: { value: string }) {
  const status = REPORT_STATES[value] ?? { label: "报告已生成", tone: "neutral" as const };
  return <RecordStatus tone={status.tone}>{status.label}</RecordStatus>;
}
