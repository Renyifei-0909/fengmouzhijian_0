import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ArrowUpRight, Image, ListChecks } from "lucide-react";
import { EvidencePreview } from "../components/ui/EvidencePreview";
import { serviceTimestamp } from "../lib/serviceTime";
import { Link } from "react-router";
import { labelWorkOrderStatus } from "../lib/productCopy";
import { api } from "../lib/api";
import type { VerificationDetail, WorkOrder } from "../lib/api";
import "./verification-workbench.css";

export type WorkbenchTone = "neutral" | "cyan" | "amber" | "green" | "red";

export const apiMessage = (reason: unknown, fallback: string) =>
  reason instanceof Error && reason.message ? reason.message : fallback;

export const localTime = (time: string | null | undefined, short = false) => {
  if (!time) return "—";
  const date = new Date(serviceTimestamp(time));
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", short
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
    : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};

export function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export const textValue = (value: unknown) => typeof value === "string" ? value : "";

export function WorkbenchStatus({ tone = "neutral", children }: { tone?: WorkbenchTone; children: ReactNode }) {
  return <span className="fm-wf-status" data-tone={tone}>{children}</span>;
}

export function WorkbenchEmpty({ title, children, media = false }: { title: string; children?: ReactNode; media?: boolean }) {
  const Icon = media ? Image : ListChecks;
  return <div className="fm-wf-empty" data-media={media}>
    <Icon size={25} strokeWidth={1.4} aria-hidden="true" />
    <strong>{title}</strong>
    {children ? <div>{children}</div> : null}
  </div>;
}

export function WorkbenchMeta({ entries }: { entries: [string, ReactNode][] }) {
  return <dl className="fm-wf-meta">
    {entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "—"}</dd></div>)}
  </dl>;
}

export function LinkedWorkOrder({ id, code, revision }: { id: string; code?: string; revision: string | number }) {
  const [order, setOrder] = useState<WorkOrder | null>(null);
  useEffect(() => {
    let active = true;
    void api.getWorkOrder(id).then((current) => { if (active) setOrder(current); }).catch(() => {
      if (active) setOrder(null);
    });
    return () => { active = false; };
  }, [id, revision]);
  const current = order?.id === id ? order : null;
  return <Link className="fm-wf-link fm-work-order-link" to={`/work-orders/${encodeURIComponent(id)}`}>
    {current?.work_order_code || code || "查看工单"}
    {current && <span className="gis-status" data-status={current.status}>{labelWorkOrderStatus(current.status)}</span>}
    <ArrowUpRight size={12} aria-hidden="true" />
  </Link>;
}

export function WorkbenchEvidence({ evidence, title = "现场原件" }: {
  evidence: VerificationDetail["evidence"];
  title?: string;
}) {
  const reference = evidence.metadata.scene_reference === true;
  const heading = title === "现场原件"
    ? evidence.metadata.media_origin === "field_photo_redacted" ? "现场影像" : reference ? "原始影像" : title
    : title;
  return <section className="fm-wf-evidence" aria-label={heading}>
    <h3 className="fm-wf-media-heading"><Image size={15} aria-hidden="true" />{heading}</h3>
    <EvidencePreview
      evidenceId={evidence.id}
      originalName={evidence.original_name}
      sha256={evidence.sha256}
      registeredSizeBytes={evidence.size_bytes}
      autoLoad
    />
  </section>;
}

const findingTitles: Record<string, string> = {
  COMPLIANCE_DEVIATION_DETECTED: "现场资料与设计要求存在偏差",
  COMPLIANCE_INSUFFICIENT_EVIDENCE: "核验资料不足",
  COMPLIANCE_NEEDS_REVIEW: "现场资料待复核",
  COMPLIANCE_COMPLIANT: "符合设计要求",
};

export function findingTitle(code: string, message: string) {
  return findingTitles[code] || (/[\u3400-\u9fff]/.test(message) && message.length <= 100 ? message : "现场问题待核对");
}

export function useRecordedEvidence(jobId?: string, evidenceId?: string) {
  const [record, setRecord] = useState<{
    jobId: string;
    evidenceId: string;
    evidence: VerificationDetail["evidence"] | null;
  } | null>(null);
  useEffect(() => {
    if (!jobId || !evidenceId) return;
    let active = true;
    const finish = (evidence: VerificationDetail["evidence"] | null) => {
      if (active) setRecord({ jobId, evidenceId, evidence });
    };
    void api.verification(jobId).then((detail) => finish(detail.evidence.id === evidenceId ? detail.evidence : null))
      .catch(() => finish(null));
    return () => { active = false; };
  }, [evidenceId, jobId]);
  if (record && record.jobId === jobId && record.evidenceId === evidenceId) return { evidence: record.evidence, loading: false };
  return { evidence: null, loading: Boolean(jobId && evidenceId) };
}
