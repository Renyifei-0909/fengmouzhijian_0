import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { FileUp, Layers, ListChecks, MapPin, Plus, RefreshCw, Search, X } from "lucide-react";
import { api, ApiRequestError } from "../lib/api";
import type { DesignPackage, EngineeringObject, ProjectGisSummary, StandardGpkgImportResult, WorkOrder } from "../lib/api";
import { GeoMap } from "../components/gis/GeoMap";
import type { CaptureMarker, QgisDesignObject } from "../components/gis/GeoMap";
import { GpkgImportPanel } from "../components/gis/GpkgImportPanel";
import { WorkOrderCapturePanel } from "../components/gis/WorkOrderCapturePanel";
import { Modal } from "../components/ui/Modal";
import { Notice } from "../components/ui/Notice";
import { FieldScene } from "../components/ui/FieldScene";
import {
  COPY,
  friendlyApiError,
  labelImportStatus,
  labelObjectType,
  labelWorkOrderStatus,
  WORK_ORDER_STATUS_LABEL,
  projectDisplayName,
  engineeringObjectDisplayName,
} from "../lib/productCopy";
import { formatWorkbenchTime } from "../components/gis/workbenchTime";
import { BINJIANG_DEMO_PACKAGE_ID, createBinjiangDemoObjects } from "../components/gis/binjiangDemoObjects";
import { serviceTimestamp } from "../lib/serviceTime";
import "../components/gis/workbench.css";

const EMPTY_OBJECTS: EngineeringObject[] = [];
const EMPTY_ORDERS: WorkOrder[] = [];
const ONLINE_STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL?.trim() || null;

type ProjectData = {
  summary: ProjectGisSummary;
  packages: DesignPackage[];
};

type ProjectResource = {
  id: string;
  data?: ProjectData;
  loading: boolean;
  error?: string;
};

type Dialog = { kind: "import" | "create" | "capture"; locationKey: string };

const describeError = (error: unknown, fallback: string) =>
  error instanceof ApiRequestError ? friendlyApiError(error.message) : fallback;

export function GISMapPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { key: locationKey } = useLocation();
  const projectId = searchParams.get("project") ?? "";
  const objectId = searchParams.get("object");
  const orderId = searchParams.get("workOrder");

  const [projects, setProjects] = useState<Awaited<ReturnType<typeof api.listProjects>> | null>(null);
  const [projectsError, setProjectsError] = useState("");
  const [resource, setResource] = useState<ProjectResource | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [allOrders, setAllOrders] = useState(false);
  const [planningProjectId, setPlanningProjectId] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [reference, setReference] = useState<{ object: QgisDesignObject; locationKey: string } | null>(null);
  const [marker, setMarker] = useState<{ orderId: string; position: CaptureMarker } | null>(null);
  const [feedback, setFeedback] = useState<{ projectId: string; message: string; warning?: boolean } | null>(null);
  const mounted = useRef(false);
  const projectListRequest = useRef(0);
  const projectRequest = useRef(0);
  const importSaving = useRef<string | null>(null);
  const current = useRef({ projectId, locationKey });
  useLayoutEffect(() => { current.current = { projectId, locationKey }; }, [projectId, locationKey]);

  const currentResource = resource?.id === projectId ? resource : null;
  const data = currentResource?.data;
  const selectedProject = projects?.find((project) => project.id === projectId);
  const hasPlanningLayer = selectedProject?.code === "FM-BJ-2607";
  const showPlanningLayer = hasPlanningLayer && (planningProjectId === projectId || Boolean(objectId?.startsWith("binjiang-demo-")));
  const importedObjects = data?.summary.objects ?? EMPTY_OBJECTS;
  const demoObjects = useMemo(() => showPlanningLayer ? createBinjiangDemoObjects(projectId) : EMPTY_OBJECTS, [projectId, showPlanningLayer]);
  const objects = useMemo(() => [...importedObjects, ...demoObjects], [importedObjects, demoObjects]);
  const workOrders = data?.summary.work_orders ?? EMPTY_ORDERS;
  const selectedWorkOrder = workOrders.find((order) => order.id === orderId);
  const selectedObject = objects.find((object) => object.id === (selectedWorkOrder?.engineering_object_id ?? objectId));
  const selectedReference = reference?.locationKey === locationKey ? reference.object : null;
  const activeDialog = dialog?.locationKey === locationKey ? dialog.kind : null;
  const query = search.trim().toLocaleLowerCase();
  const filteredObjects = query
    ? objects.filter((object) => [object.name, engineeringObjectDisplayName(object.name), object.object_code, labelObjectType(object.object_type)]
        .some((value) => value.toLocaleLowerCase().includes(query)))
    : objects;
  const filteredOrders = workOrders.filter((order) =>
    (allOrders || !selectedObject || order.engineering_object_id === selectedObject.id)
      && (!statusFilter || order.status === statusFilter));
  const loading = !projects || Boolean(!projectId && projects.length)
    || Boolean(selectedProject && (!currentResource || currentResource.loading));

  const loadProjects = useCallback(async () => {
    const request = ++projectListRequest.current;
    setProjectsError("");
    try {
      const rows = await api.listProjects();
      if (request === projectListRequest.current) setProjects(rows);
    } catch (error) {
      if (request === projectListRequest.current) setProjectsError(describeError(error, "无法读取项目列表"));
    }
  }, []);

  const loadProjectData = useCallback(async (id: string) => {
    if (!mounted.current || current.current.projectId !== id) return;
    const request = ++projectRequest.current;
    setResource((previous) => ({
      id,
      data: previous?.id === id ? previous.data : undefined,
      loading: true,
    }));
    try {
      const [summary, packages] = await Promise.all([api.projectGisSummary(id), api.listDesignPackages(id)]);
      if (request !== projectRequest.current || current.current.projectId !== id) return;
      setResource((previous) => {
        const previousOrders = previous?.id === id ? previous.data?.summary.work_orders : undefined;
        const latestOrders = new Map(previousOrders?.map((order) => [order.id, order]));
        const orders = summary.work_orders.map((order) => {
          const latest = latestOrders.get(order.id);
          return latest && serviceTimestamp(latest.updated_at) > serviceTimestamp(order.updated_at) ? latest : order;
        });
        return { id, data: { summary: { ...summary, work_orders: orders }, packages }, loading: false };
      });
    } catch (error) {
      if (request !== projectRequest.current || current.current.projectId !== id) return;
      setResource((previous) => ({
        id,
        data: previous?.id === id ? previous.data : undefined,
        loading: false,
        error: describeError(error, "无法读取工程资料"),
      }));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void loadProjects();
    return () => {
      mounted.current = false;
      projectListRequest.current += 1;
    };
  }, [loadProjects]);

  useEffect(() => {
    if (projectId || !projects?.length) return;
    let active = true;
    const resolve = async () => {
      let linkedProjectId: string | undefined;
      try {
        if (orderId) linkedProjectId = (await api.getWorkOrder(orderId)).project_id;
        else if (objectId) linkedProjectId = (await api.getEngineeringObject(objectId)).project_id;
      } catch {
        // An unavailable link opens the default project.
      }
      let target = projects.find((project) => project.id === linkedProjectId);
      // An imagery-only project may be the newest project in the ledger.
      for (let i = 0; !target && active && i < projects.length; i += 4) {
        const candidates = projects.slice(i, i + 4);
        const packages = await Promise.allSettled(candidates.map(({ id }) => api.listDesignPackages(id)));
        target = candidates.find((_, index) => {
          const result = packages[index];
          return result.status === "fulfilled" && result.value.some((item) => item.object_count > 0);
        });
      }
      if (!active || current.current.projectId) return;
      setSearchParams((previous) => {
        const next = new URLSearchParams(previous);
        next.set("project", (target ?? projects[0]).id);
        return next;
      }, { replace: true });
    };
    void resolve();
    return () => { active = false; };
  }, [projectId, objectId, orderId, projects, setSearchParams]);

  useEffect(() => {
    if (!selectedProject) return;
    void loadProjectData(selectedProject.id);
    return () => { projectRequest.current += 1; };
  }, [selectedProject, loadProjectData]);

  useEffect(() => {
    if (!data || currentResource?.loading || currentResource?.error) return;
    const nextObjectId = selectedObject?.id ?? null;
    const nextOrderId = selectedWorkOrder?.id ?? null;
    if (objectId === nextObjectId && orderId === nextOrderId) return;
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (nextObjectId) next.set("object", nextObjectId);
      else next.delete("object");
      if (nextOrderId) next.set("workOrder", nextOrderId);
      else next.delete("workOrder");
      return next;
    }, { replace: true });
  }, [data, currentResource, objectId, orderId, selectedObject, selectedWorkOrder, setSearchParams]);

  useEffect(() => {
    setDialog(null);
    setReference(null);
    importSaving.current = null;
  }, [locationKey]);

  const changeSelection = (nextObjectId: string | null, nextOrderId: string | null) => {
    setReference(null);
    setMarker(null);
    if (nextObjectId === objectId && nextOrderId === orderId) return;
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (nextObjectId) next.set("object", nextObjectId);
      else next.delete("object");
      if (nextOrderId) next.set("workOrder", nextOrderId);
      else next.delete("workOrder");
      return next;
    });
  };

  const changeProject = (id: string) => {
    setSearch("");
    setStatusFilter("");
    setAllOrders(false);
    setFeedback(null);
    setMarker(null);
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("project", id);
      next.delete("object");
      next.delete("workOrder");
      return next;
    });
  };

  const selectObject = (id: string) => {
    setAllOrders(false);
    const linkedOrder = selectedWorkOrder?.engineering_object_id === id
      ? selectedWorkOrder
      : workOrders.find((order) => order.engineering_object_id === id);
    changeSelection(id, linkedOrder?.id ?? null);
  };

  const setCaptureMarker = useCallback((position: CaptureMarker | null) => {
    setMarker(orderId && position ? { orderId, position } : null);
  }, [orderId]);

  const updateOrder = useCallback((updated: WorkOrder) => {
    if (!mounted.current || current.current.projectId !== updated.project_id) return;
    setResource((previous) => {
      if (previous?.id !== updated.project_id || !previous.data) return previous;
      const { summary } = previous.data;
      const orders = summary.work_orders;
      const exists = orders.some((order) => order.id === updated.id);
      const nextOrders = exists
        ? orders.map((order) => order.id === updated.id && serviceTimestamp(order.updated_at) <= serviceTimestamp(updated.updated_at) ? updated : order)
        : [updated, ...orders];
      return {
        ...previous,
        data: {
          ...previous.data,
          summary: {
            ...summary,
            work_orders: nextOrders,
            work_order_count: summary.work_order_count + (exists ? 0 : 1),
          },
        },
      };
    });
  }, []);

  const onImported = (result: StandardGpkgImportResult) => {
    if (!mounted.current || current.current.projectId !== result.package.project_id) return;
    setResource((previous) => {
      if (!previous?.data || previous.id !== projectId) return previous;
      const { summary, packages } = previous.data;
      const imported = new Map(summary.objects.map((object) => [object.id, object]));
      for (const object of result.objects) imported.set(object.id, object);
      const hasPackage = packages.some((pkg) => pkg.id === result.package.id);
      return {
        ...previous,
        data: {
          packages: [result.package, ...packages.filter((pkg) => pkg.id !== result.package.id)],
          summary: {
            ...summary,
            design_package_count: summary.design_package_count + (hasPackage ? 0 : 1),
            engineering_object_count: imported.size,
            objects: [...imported.values()],
          },
        },
      };
    });
    if (current.current.locationKey === locationKey) {
      setDialog(null);
      const importedObject = result.objects[0];
      if (importedObject) changeSelection(importedObject.id, null);
    }
    setFeedback({
      projectId,
      message: `已导入 ${result.package.package_code} · ${result.package.object_count} 个工程对象`,
    });
    void loadProjectData(projectId);
  };

  const onOrderSaved = (order: WorkOrder, warning?: string) => {
    if (!mounted.current || current.current.projectId !== order.project_id) return;
    updateOrder(order);
    if (current.current.locationKey === locationKey) {
      setDialog(null);
      setStatusFilter("");
      changeSelection(order.engineering_object_id, order.id);
    }
    setFeedback({
      projectId: order.project_id,
      message: warning || `已创建工单 ${order.work_order_code}`,
      warning: Boolean(warning),
    });
    void loadProjectData(order.project_id);
  };

  const openDialog = (kind: Dialog["kind"]) => setDialog({ kind, locationKey });
  const closeDialog = () => { if (importSaving.current !== locationKey) setDialog(null); };
  const visibleError = projectsError || (projects && projectId && !selectedProject ? "未找到该项目，请重新选择。" : currentResource?.error);

  return (
    <div className="gis-workbench page-enter">
      <div className="gis-page-head gis-project-toolbar">
        <div className="gis-actions">
          <label className="gis-project-field">
            <span className="fm-label">当前项目</span>
            <select className="fm-input" value={selectedProject?.id ?? ""} onChange={(event) => changeProject(event.target.value)}>
              <option value="" disabled>{projects?.length ? "选择项目" : loading && !projectsError ? "正在读取项目" : "暂无项目"}</option>
              {projects?.map((project) => <option key={project.id} value={project.id}>{projectDisplayName(project.name)}</option>)}
            </select>
          </label>
        </div>
        <div className="gis-actions">
          {hasPlanningLayer && <label className="gis-inline-control">
            <input type="checkbox" checked={showPlanningLayer} onChange={(event) => {
              setPlanningProjectId(event.target.checked ? projectId : "");
              if (!event.target.checked && selectedObject?.design_package_id === BINJIANG_DEMO_PACKAGE_ID) changeSelection(null, null);
            }} /> 布设示意
          </label>}
          <button
            type="button"
            className="fm-button"
            disabled={Boolean(loading && !visibleError)}
            onClick={() => selectedProject ? void loadProjectData(selectedProject.id) : void loadProjects()}
          >
            <RefreshCw size={14} aria-hidden="true" /> 刷新
          </button>
          <button type="button" className="fm-button fm-button-primary" onClick={() => openDialog("import")} disabled={!selectedProject}>
            <FileUp size={15} aria-hidden="true" /> 导入设计
          </button>
        </div>
      </div>

      {visibleError && <Notice type="warning" message={visibleError} />}
      {feedback?.projectId === projectId && (
        <Notice type={feedback.warning ? "warning" : "success"} message={feedback.message} />
      )}

      <div className="fm-panel gis-workspace">
        <aside className="gis-object-rail" aria-label="工程对象">
          <div className="gis-section-head">
            <h3><Layers size={15} aria-hidden="true" /> 工程对象</h3>
            <span>{data ? objects.length : "—"}</span>
          </div>
          <label className="gis-search">
            <Search size={14} aria-hidden="true" />
            <input
              aria-label="查找工程对象"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="名称或对象编号"
            />
          </label>
          <ul className="gis-object-list">
            {filteredObjects.map((object) => (
              <li key={object.id}>
                <button
                  type="button"
                  aria-pressed={selectedObject?.id === object.id && !selectedReference}
                  onClick={() => selectObject(object.id)}
                >
                  <span className="gis-object-type">{labelObjectType(object.object_type)}</span>
                  <strong>{engineeringObjectDisplayName(object.name || object.object_code)}</strong>
                  {object.name && <small>{object.object_code}</small>}
                </button>
              </li>
            ))}
            {!filteredObjects.length && (
              <li className="gis-empty">
                {loading && !data && !projectsError ? "正在读取工程对象…" : query ? (
                  <>未找到匹配对象 <button type="button" className="gis-empty-action" onClick={() => setSearch("")}>清除搜索</button></>
                ) : selectedProject ? (
                  <>该项目尚无工程图层 <button type="button" className="gis-empty-action" onClick={() => openDialog("import")}>导入工程设计</button></>
                ) : "暂无工程对象"}
              </li>
            )}
          </ul>
          <button type="button" className="gis-file-history" disabled={!selectedProject} onClick={() => openDialog("import")}>
            导入记录 <span>{data?.summary.design_package_count ?? "—"}</span>
          </button>
        </aside>

        <section className="gis-map-pane" aria-label="工程地图">
          <div className="gis-section-head">
            <h3><MapPin size={15} aria-hidden="true" /> 工程地图</h3>
            <span>{selectedObject && !selectedReference ? selectedObject.object_code : "项目视图"}</span>
          </div>
          <GeoMap
            key={projectId}
            className="gis-map"
            objects={objects}
            selectedObjectId={selectedReference ? null : selectedObject?.id ?? null}
            onSelectObject={selectObject}
            onSelectQgisObject={(object) => setReference(object ? { object, locationKey } : null)}
            selectedQgisObjectId={selectedReference?.id ?? null}
            captureMarker={marker && marker.orderId === orderId ? marker.position : null}
            onlineStyleUrl={ONLINE_STYLE_URL}
            emptyMessage={loading && !data && !projectsError ? "正在读取空间数据…" : "暂无工程图层"}
          />

        </section>

        <aside className="gis-inspector" aria-label="对象详情">
          <div className="gis-section-head">
            <h3>{selectedReference ? "外部设计要素" : "对象详情"}</h3>
            {(selectedReference || selectedObject) && (
              <button
                type="button"
                className="gis-icon-button"
                aria-label={selectedReference ? "清除外部设计要素" : "清除选中对象"}
                onClick={() => selectedReference ? setReference(null) : changeSelection(null, null)}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <div className="gis-inspector-body" key={selectedReference?.id ?? selectedObject?.id ?? "project"}>
            {selectedReference ? (
              <>
                <span className="fm-label">外部设计图层</span>
                <h4>{selectedReference.objectCode}</h4>
                <dl className="gis-data">
                  <div><dt>对象标识</dt><dd>{selectedReference.id}</dd></div>
                  <div><dt>类型</dt><dd>{selectedReference.objectType}</dd></div>
                  <div><dt>设计版本</dt><dd>{selectedReference.designVersion}</dd></div>
                  <div><dt>来源图层</dt><dd>{selectedReference.sourceLayer}</dd></div>
                  <div><dt>图形序号</dt><dd>第 {selectedReference.featureIndex} 个要素</dd></div>
                  <div><dt>几何类型</dt><dd>{selectedReference.geometryType}</dd></div>
                  <div><dt>显示坐标系</dt><dd>WGS 84 · EPSG:4326</dd></div>
                  <div><dt>数据来源</dt><dd>Dreieich Mitte QGIS 工程</dd></div>
                  <div><dt>来源工程</dt><dd>德赖艾希中心区</dd></div>
                  <div><dt>关联状态</dt><dd>已关联地图要素</dd></div>
                </dl>
              </>
            ) : selectedObject ? (
              <>
                <span className="fm-label">{labelObjectType(selectedObject.object_type)}</span>
                <h4>{engineeringObjectDisplayName(selectedObject.name || selectedObject.object_code)}</h4>
                <dl className="gis-data">
                  <div><dt>对象编号</dt><dd>{selectedObject.object_code}</dd></div>
                  <div><dt>设计版本</dt><dd>{selectedObject.design_version}</dd></div>
                  {selectedObject.design_package_id === BINJIANG_DEMO_PACKAGE_ID && <div><dt>数据性质</dt><dd>布设示意</dd></div>}
                </dl>
                <details className="gis-disclosure">
                  <summary>对象属性与设计要求</summary>
                  <dl className="gis-data">
                    <div><dt>来源图层</dt><dd>{selectedObject.source_layer}</dd></div>
                    <div><dt>来源要素</dt><dd>{selectedObject.source_feature_id}</dd></div>
                    <div><dt>坐标系统</dt><dd>EPSG:{selectedObject.geometry_source_crs_epsg}</dd></div>
                  </dl>
                  <p>{COPY.engineeringAttrs}</p>
                  <pre>{JSON.stringify(selectedObject.attributes_snapshot, null, 2)}</pre>
                  <p>{COPY.designRequirements}</p>
                  <pre>{JSON.stringify(selectedObject.expected_rules, null, 2)}</pre>
                </details>
                {selectedObject.design_package_id === BINJIANG_DEMO_PACKAGE_ID ? (
                  <p className="gis-import-feedback">此图层用于查看布设关系。施工工单可从已导入的工程对象创建。</p>
                ) : (
                  <button type="button" className="fm-button fm-button-primary gis-inspector-cta" onClick={() => openDialog("create")}>
                    <Plus size={15} aria-hidden="true" /> 新建工单
                  </button>
                )}
              </>
            ) : (
              <FieldScene scene="survey" guidance="在地图或左侧清单选中工程对象，查看工单和采集记录。" />
            )}

            {selectedWorkOrder && !selectedReference && (
              <section className="gis-related-order">
                <span className="fm-label">当前工单</span>
                <h4>{selectedWorkOrder.work_order_code}</h4>
                <span className="gis-status" data-status={selectedWorkOrder.status}>
                  {labelWorkOrderStatus(selectedWorkOrder.status)}
                </span>
                <dl className="gis-data">
                  <div><dt>执行人</dt><dd>{selectedWorkOrder.assigned_to || "待分派"}</dd></div>
                </dl>
                <div className="gis-inspector-links">
                  <Link className="fm-button" to={`/work-orders/${encodeURIComponent(selectedWorkOrder.id)}`}>
                    {selectedWorkOrder.status === "draft" ? "分派工单" : "工单详情"}
                  </Link>
                  <button type="button" className="fm-button" onClick={() => openDialog("capture")}>采集与核验</button>
                </div>
                {marker?.orderId === selectedWorkOrder.id && (
                  <div className="gis-position-hint">
                    {marker.position.comparison ? <dl className="gis-data">
                      <div><dt>{marker.position.synthetic ? "标注点距对象" : "采集点距对象"}</dt><dd>{marker.position.comparison.distance.toFixed(1)} 米</dd></div>
                      <div><dt>工单容差</dt><dd>{marker.position.comparison.tolerance} 米</dd></div>
                    </dl> : <p>点击「定位对照」，同时查看工程对象和采集点。</p>}
                  </div>
                )}
              </section>
            )}
          </div>
        </aside>
      </div>

      <section className="fm-panel gis-orders">
        <div className="gis-section-head">
          <h3><ListChecks size={16} aria-hidden="true" /> 施工工单 <span className="gis-count">{data?.summary.work_order_count ?? "—"}</span></h3>
          <div className="gis-actions">
            {selectedObject && (
              <label className="gis-inline-control">
                <input type="checkbox" checked={allOrders} onChange={(event) => setAllOrders(event.target.checked)} />
                全部对象
              </label>
            )}
            <select className="fm-input" aria-label="工单状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">全部状态</option>
              {Object.entries(WORK_ORDER_STATUS_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        </div>
        <div className="gis-table-wrap">
          <table className="gis-table">
            <thead><tr><th>工单</th><th>工程对象</th><th>工单状态</th><th>执行人</th><th>定位阈值</th><th><span className="sr-only">操作</span></th></tr></thead>
            <tbody>
              {filteredOrders.map((order) => {
                const object = objects.find((item) => item.id === order.engineering_object_id);
                return (
                  <tr key={order.id} data-selected={order.id === selectedWorkOrder?.id}>
                    <td>
                      <button type="button" className="gis-table-link" onClick={() => changeSelection(order.engineering_object_id, order.id)}>
                        {order.work_order_code}
                      </button>
                    </td>
                    <td>{engineeringObjectDisplayName(object?.name || object?.object_code || "—")}</td>
                    <td><span className="gis-status" data-status={order.status}>{labelWorkOrderStatus(order.status)}</span></td>
                    <td>{order.assigned_to || "待分派"}</td>
                    <td>{order.spatial_tolerance_m} 米 / {order.gps_accuracy_threshold_m} 米</td>
                    <td><Link className="gis-table-link" to={`/work-orders/${encodeURIComponent(order.id)}`}>打开</Link></td>
                  </tr>
                );
              })}
              {!filteredOrders.length && <tr><td colSpan={6} className="gis-empty">{loading && !data && !projectsError ? "正在读取工单…" : "暂无匹配工单"}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      {activeDialog === "import" && selectedProject && (
        <Modal open title="导入工程设计" description={projectDisplayName(selectedProject.name)} onClose={closeDialog} size="md" placement="right">
          <GpkgImportPanel
            key={projectId}
            projectId={projectId}
            onImported={onImported}
            onSavingChange={(saving) => {
              if (current.current.locationKey === locationKey) importSaving.current = saving ? locationKey : null;
            }}
          />
          <ImportRecords packages={data?.packages ?? []} />
        </Modal>
      )}
      {activeDialog === "create" && selectedObject && (
        <CreateOrderDialog
          key={selectedObject.id}
          object={selectedObject}
          workOrders={workOrders}
          onClose={closeDialog}
          onSaved={onOrderSaved}
        />
      )}
      {activeDialog === "capture" && selectedWorkOrder && (
        <Modal open title="采集与核验" description={selectedWorkOrder.work_order_code} onClose={closeDialog} size="xl" placement="right">
          <WorkOrderCapturePanel
            key={selectedWorkOrder.id}
            workOrder={selectedWorkOrder}
            onWorkOrderUpdated={updateOrder}
            onCaptureMarkerChange={setCaptureMarker}
          />
        </Modal>
      )}
    </div>
  );
}

function ImportRecords({ packages }: { packages: DesignPackage[] }) {
  return (
    <section className="gis-import-history">
      <h4>{COPY.gpkgImportRecords}</h4>
      {!packages.length && <p className="gis-empty">{COPY.noPackages}</p>}
      {packages.map((pkg) => (
        <details key={pkg.id} className="gis-disclosure">
          <summary>
            <span>{pkg.package_code}</span>
            <span className="gis-history-status">{labelImportStatus(pkg.import_status)} · {pkg.object_count} 个对象</span>
          </summary>
          <dl className="gis-data">
            {/^[a-f0-9]{32}\.gpkg$/i.test(pkg.source_filename)
              ? <div><dt>文件格式</dt><dd>GeoPackage (.gpkg)</dd></div>
              : <div><dt>来源文件</dt><dd>{pkg.source_filename}</dd></div>}
            <div><dt>坐标系统</dt><dd>EPSG:{pkg.source_crs_epsg}</dd></div>
            <div><dt>导入时间</dt><dd>{formatWorkbenchTime(pkg.imported_at)}</dd></div>
          </dl>
          {pkg.import_warnings?.length > 0 && (
            <details className="gis-disclosure">
              <summary>{COPY.viewImportNotes}</summary>
              <pre>{JSON.stringify(pkg.import_warnings, null, 2)}</pre>
            </details>
          )}
        </details>
      ))}
    </section>
  );
}

function CreateOrderDialog({
  object,
  workOrders,
  onClose,
  onSaved,
}: {
  object: EngineeringObject;
  workOrders: WorkOrder[];
  onClose: () => void;
  onSaved: (order: WorkOrder, warning?: string) => void;
}) {
  const codes = new Set(workOrders.map((order) => order.work_order_code));
  let sequence = 1;
  while (codes.has(`${object.object_code}-WO-${sequence}`)) sequence += 1;
  const defaultCode = `${object.object_code}-WO-${sequence}`;
  const [code, setCode] = useState("");
  const [procedure, setProcedure] = useState("TRENCH-BEFORE-BACKFILL");
  const [tolerance, setTolerance] = useState("50");
  const [accuracy, setAccuracy] = useState("30");
  const [assignee, setAssignee] = useState("现场采集");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const locked = useRef(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (locked.current) return;
    if (!Number.isFinite(Number(tolerance)) || Number(tolerance) <= 0
      || !Number.isFinite(Number(accuracy)) || Number(accuracy) <= 0) {
      setError("距离与定位精度阈值须大于 0 米。");
      return;
    }
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const draft = await api.createWorkOrder(object.project_id, {
        engineering_object_id: object.id,
        work_order_code: code.trim() || defaultCode,
        procedure_code: procedure.trim() || undefined,
        spatial_tolerance_m: Number(tolerance),
        gps_accuracy_threshold_m: Number(accuracy),
        notes: "工程作业台创建",
      });
      if (!assignee.trim()) {
        onSaved(draft);
        return;
      }
      let assigned: WorkOrder;
      try {
        assigned = await api.assignWorkOrder(draft.id, assignee.trim());
      } catch {
        onSaved(draft, `工单 ${draft.work_order_code} 已保存。分派未确认，可在工单详情查看状态并重试。`);
        return;
      }
      onSaved(assigned);
    } catch (reason) {
      setError(reason instanceof ApiRequestError && reason.status === 409
        ? "该工单编号已使用，请修改编号。"
        : describeError(reason, "无法创建工单，请重试"));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  };

  return (
    <Modal open title="新建施工工单" description={engineeringObjectDisplayName(object.name || object.object_code)} onClose={() => { if (!locked.current) onClose(); }} size="md" placement="right">
      <form className="gis-form" onSubmit={(event) => void submit(event)}>
        <label>工单编号<input className="fm-input" value={code} placeholder={defaultCode} onChange={(event) => setCode(event.target.value)} disabled={busy} /></label>
        <label>执行人<input className="fm-input" value={assignee} placeholder="留空保存为待分派" onChange={(event) => setAssignee(event.target.value)} disabled={busy} /></label>
        <div className="gis-form-pair">
          <label>空间容差（米）<input className="fm-input" type="number" min="0.01" step="any" required value={tolerance} onChange={(event) => setTolerance(event.target.value)} disabled={busy} /></label>
          <label>定位精度阈值（米）<input className="fm-input" type="number" min="0.01" step="any" required value={accuracy} onChange={(event) => setAccuracy(event.target.value)} disabled={busy} /></label>
        </div>
        <details className="gis-disclosure">
          <summary>施工工序{procedure === "TRENCH-BEFORE-BACKFILL" ? " · 沟槽回填前" : ""}</summary>
          <label>工序编号<input className="fm-input" value={procedure} onChange={(event) => setProcedure(event.target.value)} disabled={busy} /></label>
        </details>
        {error && <Notice type="warning" message={error} />}
        <div className="gis-actions">
          <button type="button" className="fm-button" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="fm-button fm-button-primary" disabled={busy}>
            {busy ? "正在保存…" : assignee.trim() ? "创建并分派" : "创建工单"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
