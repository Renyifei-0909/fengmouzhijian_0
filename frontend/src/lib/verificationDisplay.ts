/**
 * Pure display helpers for work-order verification UI (testable without DOM).
 */

import { COMPLIANCE_BUSINESS_NOTE as _COMPLIANCE_NOTE } from "./productCopy";

export const COMPLIANCE_BUSINESS_NOTE = _COMPLIANCE_NOTE;

/** Domain fields that may appear in compliance.differences[].field */
const DOMAIN_FIELD_LABELS: Record<string, string> = {
  spatial_check: "空间位置核验",
  object_visibility: "工程对象可见性",
  visible_pipe_count: "可见管线数量",
  trench_stage: "施工阶段",
  visible_material_or_specification: "可见材料或规格",
  separation: "间距",
  spacing_m: "间距",
  count: "数量",
  quantity: "数量",
  specification: "规格",
  depth_m: "深度",
  min_depth_m: "最小深度",
};

/** Primary UI label for a difference field. Never leaks unknown internal keys. */
export function labelDomainField(fieldKey: string | null | undefined): string {
  if (!fieldKey || !String(fieldKey).trim()) return "其他核验项";
  const key = String(fieldKey).trim();
  return DOMAIN_FIELD_LABELS[key] || "其他核验项";
}

/** Whether the key has an explicit commercial mapping. */
export function isKnownDomainField(fieldKey: string): boolean {
  return Object.prototype.hasOwnProperty.call(DOMAIN_FIELD_LABELS, fieldKey);
}

export type DifferenceBuckets = {
  /** Chinese labels for primary UI lists */
  ok: string[];
  bad: string[];
  pending: string[];
  /** Original field keys retained for technical detail / audit */
  rawFields: Array<{ field: string; status: string; label: string }>;
};

/**
 * Group compliance differences for primary UI (Chinese labels only)
 * and keep raw keys for the technical details panel.
 */
export function groupDifferenceRows(differences: unknown): DifferenceBuckets {
  const ok: string[] = [];
  const bad: string[] = [];
  const pending: string[] = [];
  const rawFields: DifferenceBuckets["rawFields"] = [];
  if (!Array.isArray(differences)) {
    return { ok, bad, pending, rawFields };
  }
  const pushUnique = (list: string[], label: string) => {
    if (!list.includes(label)) list.push(label);
  };
  for (const item of differences) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const field = String(row.field ?? "");
    const status = String(row.status ?? "");
    const label = labelDomainField(field || null);
    rawFields.push({ field: field || "(missing)", status, label });
    if (status === "compliant" || status === "not_applicable") pushUnique(ok, label);
    else if (status === "deviation_detected") pushUnique(bad, label);
    else pushUnique(pending, label);
  }
  return { ok, bad, pending, rawFields };
}

/** Rule-engine first judgement (not human review). */
export function labelRuleVerdict(verdict: string | null | undefined): string {
  if (!verdict) return "等待判定";
  const map: Record<string, string> = {
    compliant: "符合要求",
    deviation_detected: "发现偏差",
    insufficient_evidence: "资料不足",
    needs_review: "需要复核",
  };
  return map[verdict] || "待确认";
}

/**
 * Human review presentation from verification job status only.
 * Does not invent states; non-review statuses use clear processing labels.
 */
export function labelHumanReviewStatus(jobStatus: string | null | undefined): string {
  if (!jobStatus) return "—";
  const map: Record<string, string> = {
    queued: "尚未进入复核",
    running: "尚未进入复核",
    needs_review: "待复核",
    sealing: "复核后归档中",
    approved: "已通过",
    rejected: "已退回",
    failed: "处理失败",
  };
  return map[jobStatus] || "—";
}

/** Task pipeline progress (not the same as rule verdict or human review). */
export function labelTaskProgress(jobStatus: string | null | undefined): string {
  if (!jobStatus) return "—";
  const map: Record<string, string> = {
    queued: "排队中",
    running: "分析中",
    needs_review: "等待人工复核",
    sealing: "归档中",
    approved: "已完成",
    rejected: "已结束",
    failed: "处理失败",
  };
  return map[jobStatus] || "—";
}

export type SpatialFields = {
  spatial_check_status: string;
  latitude?: number | null;
  longitude?: number | null;
  accuracy_m?: number | null;
  gps_accuracy_threshold_m?: number | null;
  distance_to_target_m?: number | null;
  tolerance_m?: number | null;
};

export function spatialBusinessSummary(fields: SpatialFields): string {
  const status = fields.spatial_check_status;
  if (status === "passed") {
    return "采集位置位于工单允许范围内。";
  }
  if (status === "failed") {
    return "采集位置超出工单允许范围，需要人工复核。";
  }
  if (status === "skipped") {
    return "本次记录未执行位置核验。";
  }
  if (status === "unavailable") {
    const hasCoords =
      fields.latitude != null &&
      fields.longitude != null &&
      Number.isFinite(fields.latitude) &&
      Number.isFinite(fields.longitude);
    if (!hasCoords) {
      return "未获取有效位置信息，暂时无法完成位置核验。";
    }
    const accuracy = fields.accuracy_m;
    const threshold = fields.gps_accuracy_threshold_m;
    if (
      accuracy != null &&
      threshold != null &&
      Number.isFinite(accuracy) &&
      Number.isFinite(threshold) &&
      accuracy > threshold
    ) {
      return "定位精度未满足工单要求，暂时无法完成位置核验。";
    }
    return "暂时无法完成位置核验。";
  }
  return "位置核验状态未知。";
}

const OBSERVATION_FIELD_LABELS: Record<string, string> = {
  visible_pipe_count: "可见管线数量",
  trench_stage: "施工阶段",
  object_visibility: "工程对象可见性",
  visible_material_or_specification: "可见材料或规格",
  spacing_m: "间距",
  quantity: "数量",
  specification: "规格",
};

const OBSERVATION_VALUE_LABELS: Record<string, string> = {
  visible: "清晰可见",
  partially_visible: "部分可见",
  laying: "敷设阶段",
  excavation: "开挖阶段",
  backfill: "回填阶段",
  completed: "已完成",
};

export type StructuredObservation = {
  key: string;
  label: string;
  displayValue: string;
  rawValue: unknown;
};

export type StructuredObservationsResult = {
  known: StructuredObservation[];
  unknown: StructuredObservation[];
};

function formatObservationValue(value: unknown): string {
  if (value === null || value === undefined) return "未识别";
  if (typeof value === "string") {
    return OBSERVATION_VALUE_LABELS[value] || value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

export function structureObservations(
  measurements: Record<string, unknown> | null | undefined,
): StructuredObservationsResult {
  const known: StructuredObservation[] = [];
  const unknown: StructuredObservation[] = [];
  if (!measurements || typeof measurements !== "object") {
    return { known, unknown };
  }
  for (const [key, rawValue] of Object.entries(measurements)) {
    const item: StructuredObservation = {
      key,
      label: OBSERVATION_FIELD_LABELS[key] || key,
      displayValue: formatObservationValue(rawValue),
      rawValue,
    };
    if (key in OBSERVATION_FIELD_LABELS) known.push(item);
    else unknown.push(item);
  }
  return { known, unknown };
}

/** Prefer selection of latest capture by server_received_at then id. */
export function pickDefaultCaptureId(
  captures: Array<{ id: string; server_received_at: string }>,
): string | null {
  if (!captures.length) return null;
  const sorted = [...captures].sort((a, b) => {
    const ta = Date.parse(a.server_received_at) || 0;
    const tb = Date.parse(b.server_received_at) || 0;
    if (tb !== ta) return tb - ta;
    return b.id.localeCompare(a.id);
  });
  return sorted[0]?.id ?? null;
}

export type CaptureDetailBundle = {
  captureId: string;
  jobId: string | null;
  detail: unknown | null;
  compliance: unknown | null;
  jobMissing: boolean;
  complianceMissing: boolean;
};

/**
 * Pure association helper used by tests: given a capture and loaded maps,
 * return the exact bundle for that capture only (no cross-mixing).
 */
export function associateCaptureBundle(
  capture: { id: string; verification_job_id: string | null },
  jobsById: Record<string, unknown>,
  complianceByJobId: Record<string, unknown | null | undefined>,
): CaptureDetailBundle {
  const jobId = capture.verification_job_id;
  if (!jobId) {
    return {
      captureId: capture.id,
      jobId: null,
      detail: null,
      compliance: null,
      jobMissing: true,
      complianceMissing: true,
    };
  }
  const detail = Object.prototype.hasOwnProperty.call(jobsById, jobId)
    ? jobsById[jobId]
    : null;
  const hasComp = Object.prototype.hasOwnProperty.call(complianceByJobId, jobId);
  const compliance = hasComp ? (complianceByJobId[jobId] ?? null) : null;
  return {
    captureId: capture.id,
    jobId,
    detail,
    compliance,
    jobMissing: detail == null,
    complianceMissing: !hasComp || compliance == null,
  };
}
