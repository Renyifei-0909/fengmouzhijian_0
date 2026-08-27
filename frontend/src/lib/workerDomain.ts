import { ApiRequestError, GeoJsonGeometry, WorkOrder } from "./api";

export type WorkerWorkOrderBucket =
  | "all"
  | "pending"
  | "processing"
  | "supplement"
  | "remediation"
  | "completed";

export type WorkerErrorKind = "network" | "permission" | "format" | "task" | "system";

export const WORKER_BUCKET_LABEL: Record<WorkerWorkOrderBucket, string> = {
  all: "全部",
  pending: "待处理",
  processing: "处理中",
  supplement: "待补充",
  remediation: "整改中",
  completed: "已完成",
};

export function workOrderBucket(status: string): Exclude<WorkerWorkOrderBucket, "all"> {
  if (status === "assigned" || status === "draft") return "pending";
  if (status === "evidence_uploaded" || status === "analyzing") return "processing";
  if (status === "needs_review") return "supplement";
  if (status === "deviation" || status === "remediating") return "remediation";
  return "completed";
}

export function workerCanSubmitEvidence(status: string): boolean {
  return ["assigned", "remediating", "evidence_uploaded", "analyzing"].includes(status);
}

function nestedValue(source: Record<string, unknown>, path: string[]): unknown {
  let current: unknown = source;
  for (const part of path) {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

export function workOrderDeadline(workOrder: WorkOrder): string | null {
  const sources: Array<[Record<string, unknown>, string[]]> = [
    [workOrder.rules_snapshot, ["due_at"]],
    [workOrder.rules_snapshot, ["deadline_at"]],
    [workOrder.rules_snapshot, ["deadline"]],
    [workOrder.design_snapshot, ["due_at"]],
    [workOrder.design_snapshot, ["deadline_at"]],
    [workOrder.design_snapshot, ["collection", "due_at"]],
  ];
  for (const [source, path] of sources) {
    const value = nestedValue(source, path);
    if (typeof value !== "string" && typeof value !== "number") continue;
    const time = Date.parse(String(value));
    if (Number.isFinite(time)) return new Date(time).toISOString();
  }
  return null;
}

export function matchesWorkerAssignment(
  assignedTo: string | null,
  workerId: string,
  teams: string[],
): boolean {
  if (!assignedTo || !workerId.trim()) return false;
  const target = assignedTo.trim().toLocaleLowerCase("zh-CN");
  const identities = [workerId, ...teams]
    .map((value) => value.trim().toLocaleLowerCase("zh-CN"))
    .filter(Boolean);
  return identities.includes(target);
}

const EARTH_RADIUS_M = 6_371_008.8;

function toLocalMeters(point: [number, number], origin: [number, number]): [number, number] {
  const latitudeRadians = (origin[1] * Math.PI) / 180;
  const x = ((point[0] - origin[0]) * Math.PI / 180) * EARTH_RADIUS_M * Math.cos(latitudeRadians);
  const y = ((point[1] - origin[1]) * Math.PI / 180) * EARTH_RADIUS_M;
  return [x, y];
}

function pointDistanceMeters(a: [number, number], b: [number, number]): number {
  const [x, y] = toLocalMeters(a, b);
  return Math.hypot(x, y);
}

function pointToSegmentMeters(
  point: [number, number],
  start: [number, number],
  end: [number, number],
): number {
  const [px, py] = toLocalMeters(point, start);
  const [ex, ey] = toLocalMeters(end, start);
  const lengthSquared = ex * ex + ey * ey;
  if (lengthSquared === 0) return Math.hypot(px, py);
  const t = Math.max(0, Math.min(1, (px * ex + py * ey) / lengthSquared));
  return Math.hypot(px - t * ex, py - t * ey);
}

function isPosition(value: unknown): value is [number, number] {
  return Array.isArray(value)
    && value.length >= 2
    && Number.isFinite(value[0])
    && Number.isFinite(value[1]);
}

function lineDistance(point: [number, number], coordinates: unknown): number {
  if (!Array.isArray(coordinates)) return Number.POSITIVE_INFINITY;
  const positions = coordinates.filter(isPosition);
  if (positions.length === 1) return pointDistanceMeters(point, positions[0]);
  let minimum = Number.POSITIVE_INFINITY;
  for (let index = 1; index < positions.length; index += 1) {
    minimum = Math.min(
      minimum,
      pointToSegmentMeters(point, positions[index - 1], positions[index]),
    );
  }
  return minimum;
}

function pointInsideRing(point: [number, number], ring: unknown): boolean {
  if (!Array.isArray(ring)) return false;
  const positions = ring.filter(isPosition);
  let inside = false;
  for (let i = 0, j = positions.length - 1; i < positions.length; j = i, i += 1) {
    const [xi, yi] = positions[i];
    const [xj, yj] = positions[j];
    const intersects = (yi > point[1]) !== (yj > point[1])
      && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi || Number.EPSILON) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function distanceToGeometryMeters(
  latitude: number,
  longitude: number,
  geometry: GeoJsonGeometry | undefined,
): number | null {
  if (!geometry || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  const point: [number, number] = [longitude, latitude];
  const coordinates = geometry.coordinates;
  const geometryType = String(geometry.type);
  let distance = Number.POSITIVE_INFINITY;

  switch (geometryType) {
    case "Point":
      distance = isPosition(coordinates) ? pointDistanceMeters(point, coordinates) : distance;
      break;
    case "MultiPoint":
      if (Array.isArray(coordinates)) {
        for (const position of coordinates) {
          if (isPosition(position)) distance = Math.min(distance, pointDistanceMeters(point, position));
        }
      }
      break;
    case "LineString":
      distance = lineDistance(point, coordinates);
      break;
    case "MultiLineString":
      if (Array.isArray(coordinates)) {
        for (const line of coordinates) distance = Math.min(distance, lineDistance(point, line));
      }
      break;
    case "Polygon":
      if (Array.isArray(coordinates)) {
        if (pointInsideRing(point, coordinates[0])) return 0;
        for (const ring of coordinates) distance = Math.min(distance, lineDistance(point, ring));
      }
      break;
    case "MultiPolygon":
      if (Array.isArray(coordinates)) {
        for (const polygon of coordinates) {
          if (!Array.isArray(polygon)) continue;
          if (pointInsideRing(point, polygon[0])) return 0;
          for (const ring of polygon) distance = Math.min(distance, lineDistance(point, ring));
        }
      }
      break;
    default:
      return null;
  }
  return Number.isFinite(distance) ? distance : null;
}

export type SpatialEligibility = {
  formal: boolean;
  distancePass: boolean;
  accuracyPass: boolean;
  message: string;
};

export function spatialEvidenceEligibility(input: {
  locationSource: string;
  synthetic: boolean;
  distanceM: number | null;
  toleranceM: number;
  accuracyM: number | null;
  accuracyThresholdM: number;
}): SpatialEligibility {
  const distancePass = input.distanceM != null && input.distanceM <= input.toleranceM;
  const accuracyPass = input.accuracyM != null && input.accuracyM <= input.accuracyThresholdM;
  const formalSource = input.locationSource === "device_gps" && !input.synthetic;
  const formal = formalSource && distancePass && accuracyPass;

  if (!formalSource) {
    return {
      formal,
      distancePass,
      accuracyPass,
      message: "无法作为正式空间核验证据",
    };
  }
  if (!accuracyPass) {
    return {
      formal,
      distancePass,
      accuracyPass,
      message: "定位精度未达到工单冻结要求，无法作为正式空间核验证据",
    };
  }
  if (!distancePass) {
    return {
      formal,
      distancePass,
      accuracyPass,
      message: "当前位置超出工单冻结容差，无法作为正式空间核验证据",
    };
  }
  return {
    formal,
    distancePass,
    accuracyPass,
    message: "现场预检通过，提交后以服务端空间核验结果为准",
  };
}

export function classifyWorkerError(reason: unknown): {
  kind: WorkerErrorKind;
  title: string;
  message: string;
} {
  const status = reason instanceof ApiRequestError ? reason.status : null;
  const raw = reason instanceof Error ? reason.message : String(reason || "");
  if (status == null && /network|fetch|连接|offline/i.test(raw)) {
    return { kind: "network", title: "网络异常", message: "网络连接中断，可先保存本地草稿，恢复后再同步。" };
  }
  if (status === 401 || status === 403) {
    return { kind: "permission", title: "权限受限", message: "当前账号无权访问或操作该任务。" };
  }
  if (status === 409) {
    return { kind: "task", title: "任务状态冲突", message: "任务状态已变化，请刷新后按最新要求处理。" };
  }
  if (status === 413 || status === 415 || status === 422) {
    return { kind: "format", title: "资料不符合要求", message: "请检查文件大小、格式和填写内容后重试。" };
  }
  if (status != null && status >= 500) {
    return { kind: "system", title: "系统异常", message: "服务暂时不可用，请稍后重试；本地草稿不会丢失。" };
  }
  return { kind: "system", title: "操作未完成", message: raw || "请稍后重试。" };
}

export function formatWorkerDistance(distanceM: number | null): string {
  if (distanceM == null || !Number.isFinite(distanceM)) return "未定位";
  if (distanceM < 1_000) return `${Math.round(distanceM)} 米`;
  return `${(distanceM / 1_000).toFixed(1)} 公里`;
}
