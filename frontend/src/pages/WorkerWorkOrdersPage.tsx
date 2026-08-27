import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDownUp,
  CalendarClock,
  ChevronRight,
  Crosshair,
  MapPin,
  RefreshCw,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { Link } from "react-router";
import { api, Project, WorkOrder } from "../lib/api";
import { labelWorkOrderStatus } from "../lib/productCopy";
import { useWorkerIdentity, workerProfileScope } from "../lib/workerIdentity";
import {
  classifyWorkerError,
  distanceToGeometryMeters,
  formatWorkerDistance,
  matchesWorkerAssignment,
  WORKER_BUCKET_LABEL,
  WorkerWorkOrderBucket,
  workOrderBucket,
  workOrderDeadline,
} from "../lib/workerDomain";
import { cn } from "../utils/cn";

type WorkerWorkOrderRow = {
  workOrder: WorkOrder;
  project: Project;
};

type CachedRows = {
  syncedAt: string;
  rows: WorkerWorkOrderRow[];
};

type SortMode = "deadline" | "distance" | "updated";

const bucketTone: Record<Exclude<WorkerWorkOrderBucket, "all">, string> = {
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  processing: "border-sky-300 bg-sky-50 text-sky-900",
  supplement: "border-orange-300 bg-orange-50 text-orange-900",
  remediation: "border-rose-300 bg-rose-50 text-rose-900",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-900",
};

function cacheKey(scope: string): string {
  return `fengmou.worker-work-orders.v1.${scope}`;
}

function readCache(scope: string): CachedRows | null {
  if (!scope) return null;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(cacheKey(scope)) || "null") as CachedRows | null;
    return parsed && Array.isArray(parsed.rows) && typeof parsed.syncedAt === "string" ? parsed : null;
  } catch {
    return null;
  }
}

export const WorkerWorkOrdersPage: React.FC = () => {
  const { profile } = useWorkerIdentity();
  const scope = workerProfileScope(profile);
  const [rows, setRows] = useState<WorkerWorkOrderRow[]>([]);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [bucket, setBucket] = useState<WorkerWorkOrderBucket>("all");
  const [sortMode, setSortMode] = useState<SortMode>("deadline");
  const [position, setPosition] = useState<{ latitude: number; longitude: number } | null>(null);
  const [locating, setLocating] = useState(false);

  const load = useCallback(async () => {
    if (!profile.id.trim()) return;
    setLoading(true);
    setMessage("");
    try {
      const projects = await api.listProjects();
      const results = await Promise.allSettled(projects.map(async (project) => ({
        project,
        workOrders: await api.listWorkOrders(project.id),
      })));
      const nextRows: WorkerWorkOrderRow[] = [];
      let failedProjects = 0;
      for (const result of results) {
        if (result.status === "rejected") {
          failedProjects += 1;
          continue;
        }
        for (const workOrder of result.value.workOrders) {
          if (matchesWorkerAssignment(workOrder.assigned_to, profile.id, profile.teams)) {
            nextRows.push({ workOrder, project: result.value.project });
          }
        }
      }
      if (projects.length > 0 && failedProjects === projects.length) {
        throw results.find((item) => item.status === "rejected")?.reason || new Error("工单加载失败");
      }
      const now = new Date().toISOString();
      const cached = { syncedAt: now, rows: nextRows };
      window.localStorage.setItem(cacheKey(scope), JSON.stringify(cached));
      setRows(nextRows);
      setSyncedAt(now);
      if (failedProjects > 0) setMessage(`${failedProjects} 个项目暂未同步，当前显示其余已加载工单。`);
    } catch (reason) {
      const cached = readCache(scope);
      if (cached) {
        setRows(cached.rows);
        setSyncedAt(cached.syncedAt);
        setMessage("网络不可用，当前显示上次同步的工单。 ");
      } else {
        const error = classifyWorkerError(reason);
        setMessage(`${error.title}：${error.message}`);
      }
    } finally {
      setLoading(false);
    }
  }, [profile.id, profile.teams, scope]);

  useEffect(() => {
    const cached = readCache(scope);
    if (cached) {
      setRows(cached.rows);
      setSyncedAt(cached.syncedAt);
    } else {
      setRows([]);
      setSyncedAt(null);
    }
    if (profile.id.trim() && navigator.onLine) void load();
  }, [load, profile.id, scope]);

  const locate = () => {
    if (!navigator.geolocation) {
      setMessage("当前设备不支持定位，无法按距离排序。");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (result) => {
        setPosition({ latitude: result.coords.latitude, longitude: result.coords.longitude });
        setSortMode("distance");
        setLocating(false);
      },
      () => {
        setMessage("未能取得定位授权，请检查系统定位设置。");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 },
    );
  };

  const counts = useMemo(() => {
    const next: Record<WorkerWorkOrderBucket, number> = {
      all: rows.length,
      pending: 0,
      processing: 0,
      supplement: 0,
      remediation: 0,
      completed: 0,
    };
    for (const row of rows) next[workOrderBucket(row.workOrder.status)] += 1;
    return next;
  }, [rows]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    const withDistance = rows.map((row) => ({
      ...row,
      distanceM: position
        ? distanceToGeometryMeters(
            position.latitude,
            position.longitude,
            row.workOrder.geometry_snapshot.geometry_wgs84,
          )
        : null,
      deadline: workOrderDeadline(row.workOrder),
    }));
    const selected = withDistance.filter((row) => {
      if (bucket !== "all" && workOrderBucket(row.workOrder.status) !== bucket) return false;
      if (!needle) return true;
      return [
        row.workOrder.work_order_code,
        row.workOrder.procedure_code,
        row.project.name,
        row.project.location,
      ].some((value) => value.toLocaleLowerCase("zh-CN").includes(needle));
    });
    return selected.sort((a, b) => {
      if (sortMode === "distance") {
        return (a.distanceM ?? Number.POSITIVE_INFINITY) - (b.distanceM ?? Number.POSITIVE_INFINITY);
      }
      if (sortMode === "updated") {
        return Date.parse(b.workOrder.updated_at) - Date.parse(a.workOrder.updated_at);
      }
      return (a.deadline ? Date.parse(a.deadline) : Number.POSITIVE_INFINITY)
        - (b.deadline ? Date.parse(b.deadline) : Number.POSITIVE_INFINITY);
    });
  }, [bucket, position, query, rows, sortMode]);

  if (!profile.id.trim()) {
    return (
      <div className="mx-auto max-w-xl rounded-lg border border-amber-200 bg-amber-50 p-5 text-amber-950">
        <h2 className="font-semibold">请先设置作业身份</h2>
        <p className="mt-2 text-sm leading-6">设置人员编号后，系统才会加载分配给本人或所属班组的工单。</p>
        <Link to="/worker/profile" className="mt-4 inline-flex min-h-11 items-center rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white">
          前往账号设置
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-3 border-b border-slate-200 pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-slate-600">{profile.name || profile.id} · {profile.teams.length ? profile.teams.join(" / ") : "个人任务"}</p>
          <p className="mt-1 text-xs text-slate-500">
            {syncedAt ? `上次同步 ${new Date(syncedAt).toLocaleString("zh-CN")}` : "尚未同步"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || !navigator.onLine}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 hover:border-slate-500 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {loading ? "同步中" : "同步工单"}
        </button>
      </section>

      {message ? (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {message}
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
        <label className="relative block">
          <Search className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索工单编号、项目或地点"
            className="min-h-11 w-full rounded-lg border border-slate-300 bg-white pl-10 pr-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200"
          />
        </label>
        <div className="flex gap-2 overflow-x-auto">
          <label className="relative min-w-32">
            <ArrowDownUp className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-slate-500" />
            <select
              value={sortMode}
              onChange={(event) => {
                const next = event.target.value as SortMode;
                if (next === "distance" && !position) locate();
                else setSortMode(next);
              }}
              className="min-h-11 w-full appearance-none rounded-lg border border-slate-300 bg-white pl-10 pr-8 text-sm font-medium"
            >
              <option value="deadline">按截止时间</option>
              <option value="distance">按距离</option>
              <option value="updated">按更新时间</option>
            </select>
          </label>
          <button
            type="button"
            onClick={locate}
            disabled={locating}
            title="刷新当前位置"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-700 hover:border-slate-500 disabled:opacity-50"
          >
            <Crosshair className={cn("h-4 w-4", locating && "animate-spin")} />
          </button>
        </div>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="工单状态筛选">
        {(Object.keys(WORKER_BUCKET_LABEL) as WorkerWorkOrderBucket[]).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={bucket === item}
            onClick={() => setBucket(item)}
            className={cn(
              "inline-flex min-h-10 shrink-0 items-center gap-2 rounded-full border px-3 text-sm font-medium",
              bucket === item
                ? "border-slate-950 bg-slate-950 text-white"
                : "border-slate-300 bg-white text-slate-700",
            )}
          >
            {WORKER_BUCKET_LABEL[item]}
            <span className={cn("text-xs", bucket === item ? "text-slate-300" : "text-slate-500")}>{counts[item]}</span>
          </button>
        ))}
      </div>

      <section aria-labelledby="work-order-list-title">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id="work-order-list-title" className="text-base font-semibold">工单列表</h2>
          <span className="inline-flex items-center gap-1 text-xs text-slate-500"><SlidersHorizontal className="h-3.5 w-3.5" /> {filtered.length} 条</span>
        </div>
        {filtered.length ? (
          <div className="grid gap-3 md:grid-cols-2">
            {filtered.map(({ workOrder, project, deadline, distanceM }) => {
              const group = workOrderBucket(workOrder.status);
              return (
                <Link
                  key={workOrder.id}
                  to={`/worker/work-orders/${encodeURIComponent(workOrder.id)}`}
                  className="group rounded-lg border border-slate-200 bg-white p-4 shadow-sm hover:border-slate-400 hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <span className={cn("inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold", bucketTone[group])}>
                        {labelWorkOrderStatus(workOrder.status)}
                      </span>
                      <h3 className="mt-3 break-words text-base font-semibold text-slate-950">{workOrder.work_order_code}</h3>
                      <p className="mt-1 truncate text-sm text-slate-600">{project.name}</p>
                    </div>
                    <ChevronRight className="mt-1 h-5 w-5 shrink-0 text-slate-400 group-hover:text-slate-800" />
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-slate-100 pt-3 text-xs">
                    <div>
                      <dt className="text-slate-500">施工工序</dt>
                      <dd className="mt-1 truncate font-medium text-slate-800">{workOrder.procedure_code || "未填写"}</dd>
                    </div>
                    <div>
                      <dt className="flex items-center gap-1 text-slate-500"><MapPin className="h-3.5 w-3.5" /> 距离</dt>
                      <dd className="mt-1 font-medium text-slate-800">{formatWorkerDistance(distanceM)}</dd>
                    </div>
                    <div>
                      <dt className="flex items-center gap-1 text-slate-500"><CalendarClock className="h-3.5 w-3.5" /> 截止时间</dt>
                      <dd className="mt-1 font-medium text-slate-800">
                        {deadline ? new Date(deadline).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "未设置"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">作业地点</dt>
                      <dd className="mt-1 truncate font-medium text-slate-800">{project.location || "未填写"}</dd>
                    </div>
                  </dl>
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white px-5 py-10 text-center">
            <p className="text-sm font-medium text-slate-800">当前条件下没有工单</p>
            <p className="mt-1 text-xs text-slate-500">可调整筛选条件，或在网络恢复后重新同步。</p>
          </div>
        )}
      </section>
    </div>
  );
};

