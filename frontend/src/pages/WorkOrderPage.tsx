import React, { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, ApiRequestError, WorkOrder } from "../lib/api";
import { WorkOrderCapturePanel } from "../components/gis/WorkOrderCapturePanel";
import { Notice } from "../components/ui/Notice";
import { MapIcon, ShieldIcon } from "../components/Icons";
import { COPY, friendlyApiError, labelWorkOrderStatus } from "../lib/productCopy";

export const WorkOrderPage: React.FC = () => {
  const { id = "" } = useParams();
  const [workOrder, setWorkOrder] = useState<WorkOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setErrorDetail(null);
    try {
      setWorkOrder(await api.getWorkOrder(id));
    } catch (err) {
      setWorkOrder(null);
      const raw = err instanceof ApiRequestError ? err.message : "无法加载工单";
      setError(friendlyApiError(raw));
      setErrorDetail(raw);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !workOrder) {
    return (
      <div className="rounded-[28px] border border-slate-200 bg-white p-8 text-sm text-slate-600">
        正在加载工单…
      </div>
    );
  }

  if (!workOrder) {
    return (
      <div className="space-y-4">
        <Notice type="warning" message={error || "工单不存在"} />
        {errorDetail && errorDetail !== error ? (
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">{COPY.techDetails}</summary>
            <pre className="mt-1 whitespace-pre-wrap break-all">{errorDetail}</pre>
          </details>
        ) : null}
        <Link to="/gis-map" className="text-sm font-medium text-sky-700">
          返回工程作业
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-slate-900 via-sky-900 to-cyan-800 p-5 text-white">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-[11px]">
              <ShieldIcon className="h-3.5 w-3.5" /> 施工工单
            </div>
            <h2 className="text-xl font-semibold">{workOrder.work_order_code}</h2>
            <p className="mt-2 text-sm text-sky-100">
              {labelWorkOrderStatus(workOrder.status)} · {workOrder.procedure_code} ·{" "}
              {COPY.designVersion} {workOrder.design_version}
            </p>
          </div>
          <Link
            to={`/gis-map?project=${encodeURIComponent(workOrder.project_id)}&workOrder=${encodeURIComponent(workOrder.id)}`}
            className="inline-flex items-center gap-1 rounded-2xl border border-white/20 bg-white/10 px-3 py-2 text-sm"
          >
            <MapIcon className="h-4 w-4" /> 工程作业
          </Link>
        </div>
      </div>
      {error ? (
        <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      ) : null}
      <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <WorkOrderCapturePanel workOrder={workOrder} onWorkOrderUpdated={setWorkOrder} />
      </div>
      <Link to="/backend-workflow" className="inline-flex text-sm font-medium text-sky-700 hover:underline">
        {COPY.goVerificationCenter}
      </Link>
    </div>
  );
};
