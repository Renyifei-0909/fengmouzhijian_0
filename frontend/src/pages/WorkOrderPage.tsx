import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ClipboardList, RefreshCw, UserRound } from "lucide-react";
import { api, ApiRequestError } from "../lib/api";
import type { EngineeringObject, WorkOrder } from "../lib/api";
import { WorkOrderCapturePanel } from "../components/gis/WorkOrderCapturePanel";
import { Notice } from "../components/ui/Notice";
import { engineeringObjectDisplayName, friendlyApiError, labelObjectType, labelWorkOrderStatus } from "../lib/productCopy";
import { serviceTimestamp } from "../lib/serviceTime";
import "../components/gis/workbench.css";

const ASSIGNABLE = new Set(["draft", "assigned", "remediating"]);

export function WorkOrderPage() {
  const { id } = useParams();
  return id ? <WorkOrderDetail key={id} id={id} /> : <Link className="fm-button" to="/gis-map">返回工程作业</Link>;
}

function WorkOrderDetail({ id }: { id: string }) {
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [object, setObject] = useState<EngineeringObject | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const request = useRef(0);
  const mounted = useRef(false);

  const updateOrder = useCallback((next: WorkOrder) => {
    if (next.id !== id || !mounted.current) return;
    setOrder((previous) => previous && serviceTimestamp(previous.updated_at) > serviceTimestamp(next.updated_at) ? previous : next);
  }, [id]);

  const load = useCallback(async () => {
    const seq = ++request.current;
    setLoading(true);
    setError("");
    try {
      const next = await api.getWorkOrder(id);
      if (seq === request.current) updateOrder(next);
    } catch (reason) {
      if (seq === request.current) setError(reason instanceof ApiRequestError ? friendlyApiError(reason.message) : "无法读取工单");
    } finally {
      if (seq === request.current) setLoading(false);
    }
  }, [id, updateOrder]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      request.current += 1;
    };
  }, [load]);

  const objectId = order?.engineering_object_id;
  useEffect(() => {
    if (!objectId) return;
    let live = true;
    void api.getEngineeringObject(objectId).then((next) => {
      if (live) setObject(next);
    }).catch(() => {});
    return () => { live = false; };
  }, [objectId]);

  const gisUrl = order ? `/gis-map?${new URLSearchParams({
    project: order.project_id, object: order.engineering_object_id, workOrder: order.id,
  }).toString()}` : "/gis-map";
  const procedure = order?.procedure_code === "TRENCH-BEFORE-BACKFILL" ? "沟槽回填前" : order?.procedure_code;
  const snapshotCode = typeof order?.design_snapshot.object_code === "string" ? order.design_snapshot.object_code : "";
  const snapshotName = typeof order?.design_snapshot.name === "string" ? order.design_snapshot.name : "";
  const linkedObject = object?.id === objectId ? object : null;

  return (
    <div className="gis-workbench page-enter">
      <header className="gis-page-head">
        <div className="gis-page-heading">
          <span className="fm-label">施工工单</span>
          <h2>{order?.work_order_code ?? "工单详情"}</h2>
        </div>
        <div className="gis-actions">
          <Link className="fm-button" to={gisUrl}><ArrowLeft size={14} aria-hidden="true" /> 工程作业</Link>
          <button type="button" className="fm-button" disabled={loading} onClick={() => void load()}><RefreshCw size={14} aria-hidden="true" /> 刷新</button>
        </div>
      </header>
      {error && <Notice type="warning" message={error} />}
      {!order && loading && <p className="fm-panel gis-empty" role="status">正在读取工单…</p>}
      {order && (
        <>
          <div className="gis-order-overview">
            <section className="fm-panel gis-order-facts">
              <div className="gis-section-head">
                <h3><ClipboardList size={15} aria-hidden="true" /> 作业信息</h3>
                <span className="gis-status" data-status={order.status}>
                  工单状态 · {labelWorkOrderStatus(order.status)}
                </span>
              </div>
              <dl className="gis-order-fact-row">
                <div><dt>工程对象</dt><dd>{engineeringObjectDisplayName(linkedObject?.name || snapshotName || linkedObject?.object_code || snapshotCode || "—")}</dd></div>
                <div><dt>工序</dt><dd>{procedure || "—"}</dd></div>
                <div><dt>设计版本</dt><dd>{order.design_version}</dd></div>
                <div><dt>空间容差</dt><dd>{order.spatial_tolerance_m} 米</dd></div>
                <div><dt>定位精度阈值</dt><dd>{order.gps_accuracy_threshold_m} 米</dd></div>
              </dl>
              <details className="gis-disclosure">
                <summary>设计基线与核验要求</summary>
                <dl className="gis-data">
                  <div><dt>对象编号</dt><dd>{linkedObject?.object_code || snapshotCode || "—"}</dd></div>
                  {linkedObject && <div><dt>对象类型</dt><dd>{labelObjectType(linkedObject.object_type)}</dd></div>}
                  <div><dt>工单备注</dt><dd>{order.notes || "—"}</dd></div>
                </dl>
                <pre>{JSON.stringify({
                  design: order.design_snapshot,
                  spatial: order.geometry_snapshot,
                  rules: order.rules_snapshot,
                }, null, 2)}</pre>
              </details>
            </section>
            <Assignment order={order} onUpdated={updateOrder} />
          </div>
          <WorkOrderCapturePanel workOrder={order} onWorkOrderUpdated={updateOrder} />
        </>
      )}
    </div>
  );
}

function Assignment({ order, onUpdated }: { order: WorkOrder; onUpdated: (order: WorkOrder) => void }) {
  const [name, setName] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const lock = useRef(false);
  const canAssign = ASSIGNABLE.has(order.status);
  const assigning = canAssign && (editing || order.status === "draft");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const assignee = name.trim();
    if (!assignee || !canAssign || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await api.assignWorkOrder(order.id, assignee);
      onUpdated(updated);
      setEditing(false);
      setNotice("已分派");
    } catch (reason) {
      setError(reason instanceof ApiRequestError ? friendlyApiError(reason.message) : "分派未确认，请刷新工单状态后重试。");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <section className="fm-panel gis-order-facts">
      <div className="gis-section-head">
        <h3><UserRound size={15} aria-hidden="true" /> 执行分派</h3>
        {notice && <span role="status">{notice}</span>}
      </div>
      <div className="gis-assignment">
        {assigning ? (
          <form className="gis-form" onSubmit={(event) => void submit(event)}>
            <label>执行人<input className="fm-input" required value={name} onChange={(event) => setName(event.target.value)} disabled={busy} placeholder="填写执行人姓名" /></label>
            <div className="gis-actions">
              {editing && <button type="button" className="fm-button" disabled={busy} onClick={() => { setEditing(false); setError(""); }}>取消</button>}
              <button type="submit" className="fm-button fm-button-primary" disabled={busy || !name.trim()}>{busy ? "正在分派…" : "分派工单"}</button>
            </div>
          </form>
        ) : (
          <div className="gis-assignment-display">
            <div><span>执行人</span><strong>{order.assigned_to || "待分派"}</strong></div>
            {canAssign && <button type="button" className="fm-button" onClick={() => {
              setName(order.assigned_to || "");
              setNotice("");
              setEditing(true);
            }}>{order.status === "assigned" ? "调整执行人" : "重新分派"}</button>}
          </div>
        )}
        {error && <p className="gis-record-error" role="alert">{error}</p>}
      </div>
    </section>
  );
}
