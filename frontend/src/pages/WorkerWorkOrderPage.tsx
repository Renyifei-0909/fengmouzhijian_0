import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ClipboardCheck,
  Contact,
  FileText,
  Map,
  MapPinned,
  Ruler,
  ShieldAlert,
} from "lucide-react";
import { Link, useParams } from "react-router";
import { WorkerCapturePanel } from "../components/worker/WorkerCapturePanel";
import { api, EngineeringObject, Project, WorkOrder } from "../lib/api";
import { labelObjectType, labelWorkOrderStatus } from "../lib/productCopy";
import { useWorkerIdentity } from "../lib/workerIdentity";
import { classifyWorkerError, matchesWorkerAssignment, workerCanSubmitEvidence, workOrderDeadline } from "../lib/workerDomain";
import { cn } from "../utils/cn";

function firstText(source: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export const WorkerWorkOrderPage: React.FC = () => {
  const { id = "" } = useParams();
  const { profile } = useWorkerIdentity();
  const captureRef = useRef<HTMLDivElement | null>(null);
  const [workOrder, setWorkOrder] = useState<WorkOrder | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [engineeringObject, setEngineeringObject] = useState<EngineeringObject | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id || !profile.id) return;
    setLoading(true);
    setError("");
    try {
      const nextWorkOrder = await api.getWorkOrder(id);
      if (!matchesWorkerAssignment(nextWorkOrder.assigned_to, profile.id, profile.teams)) {
        setWorkOrder(null);
        setProject(null);
        setEngineeringObject(null);
        setError("权限受限：该工单未分配给当前人员或所属班组。");
        return;
      }
      const [nextProject, nextObject] = await Promise.all([
        api.getProject(nextWorkOrder.project_id),
        api.getEngineeringObject(nextWorkOrder.engineering_object_id),
      ]);
      setWorkOrder(nextWorkOrder);
      setProject(nextProject);
      setEngineeringObject(nextObject);
    } catch (reason) {
      const next = classifyWorkerError(reason);
      setError(`${next.title}：${next.message}`);
    } finally {
      setLoading(false);
    }
  }, [id, profile.id, profile.teams]);

  useEffect(() => { void load(); }, [load]);

  const instructions = useMemo(() => workOrder
    ? firstText(workOrder.design_snapshot, ["collection_instructions", "capture_instructions", "instructions"])
      || workOrder.notes
      || "请按施工工序拍摄清晰的现场照片或视频，确保工程对象和关键工艺可辨识。"
    : "", [workOrder]);

  if (loading) {
    return <div className="flex items-center gap-2 py-12 text-sm text-slate-600"><span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900" /> 正在加载工单…</div>;
  }

  if (!workOrder || !project || !engineeringObject) {
    return (
      <div className="mx-auto max-w-xl space-y-4">
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900" role="alert">{error || "工单不存在或不可访问。"}</div>
        <Link to="/worker/work-orders" className="inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-slate-800 underline"><ArrowLeft className="h-4 w-4" /> 返回我的工单</Link>
      </div>
    );
  }

  const deadline = workOrderDeadline(workOrder);
  const contactPhone = project.manager?.match(/1\d{10}|(?:\d{3,4}-)?\d{7,8}/)?.[0] || null;
  const canSubmit = workerCanSubmitEvidence(workOrder.status);

  return (
    <div className="space-y-6">
      <Link to="/worker/work-orders" className="inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-slate-700 hover:text-slate-950"><ArrowLeft className="h-4 w-4" /> 返回工单列表</Link>

      <section className="border-b border-slate-200 pb-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <span className={cn(
              "inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold",
              canSubmit ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-300 bg-slate-100 text-slate-700",
            )}>{labelWorkOrderStatus(workOrder.status)}</span>
            <h2 className="mt-3 break-words text-2xl font-semibold text-slate-950">{workOrder.work_order_code}</h2>
            <p className="mt-2 text-sm text-slate-600">{project.name} · {project.location || "地点未填写"}</p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <button type="button" onClick={() => captureRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })} disabled={!canSubmit} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-[#f2c94c] px-4 text-sm font-bold text-slate-950 disabled:opacity-50"><ClipboardCheck className="h-4 w-4" /> 开始采集</button>
            <Link to={`/gis-map?project=${encodeURIComponent(project.id)}&workOrder=${encodeURIComponent(workOrder.id)}`} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800"><Map className="h-4 w-4" /> 查看图纸</Link>
          </div>
        </div>
      </section>

      {!canSubmit ? (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" />
          <div><p className="font-semibold">当前工单不可采集</p><p className="mt-1">工单可能已关闭、已通过或等待管理人员处理。历史资料仍可查看。</p></div>
        </div>
      ) : null}

      <section className="grid gap-x-8 gap-y-5 border-b border-slate-200 pb-6 sm:grid-cols-2 lg:grid-cols-3">
        <div className="flex items-start gap-3"><MapPinned className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" /><div><p className="text-xs text-slate-500">工程对象</p><p className="mt-1 text-sm font-semibold">{engineeringObject.name}</p><p className="mt-1 text-xs text-slate-500">{engineeringObject.object_code} · {labelObjectType(engineeringObject.object_type)}</p></div></div>
        <div className="flex items-start gap-3"><FileText className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" /><div><p className="text-xs text-slate-500">施工工序</p><p className="mt-1 text-sm font-semibold">{workOrder.procedure_code || "未填写"}</p><p className="mt-1 text-xs text-slate-500">设计版本 {workOrder.design_version}</p></div></div>
        <div className="flex items-start gap-3"><Ruler className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" /><div><p className="text-xs text-slate-500">冻结空间要求</p><p className="mt-1 text-sm font-semibold">容差 {workOrder.spatial_tolerance_m} 米</p><p className="mt-1 text-xs text-slate-500">定位精度 ≤ {workOrder.gps_accuracy_threshold_m} 米</p></div></div>
        <div className="sm:col-span-2"><p className="text-xs text-slate-500">采集说明</p><p className="mt-1 text-sm leading-6 text-slate-800">{instructions}</p></div>
        <div className="flex items-start gap-3"><Contact className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" /><div><p className="text-xs text-slate-500">项目负责人</p><p className="mt-1 text-sm font-semibold">{project.manager || "未配置"}</p>{contactPhone ? <a href={`tel:${contactPhone}`} className="mt-2 inline-flex text-xs font-semibold text-sky-800 underline">拨打电话</a> : <p className="mt-1 text-xs text-slate-500">未配置可拨打号码</p>}</div></div>
        {deadline ? <div><p className="text-xs text-slate-500">截止时间</p><p className="mt-1 text-sm font-semibold">{new Date(deadline).toLocaleString("zh-CN")}</p></div> : null}
      </section>

      <div ref={captureRef} className="scroll-mt-24">
        <WorkerCapturePanel workOrder={workOrder} onWorkOrderUpdated={setWorkOrder} />
      </div>
    </div>
  );
};

