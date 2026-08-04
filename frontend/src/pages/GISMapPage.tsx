import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  api,
  ApiRequestError,
  DesignPackage,
  EngineeringObject,
  ProjectGisSummary,
  WorkOrder,
} from "../lib/api";
import { GeoMap } from "../components/gis/GeoMap";
import { GpkgImportPanel } from "../components/gis/GpkgImportPanel";
import { WorkOrderCapturePanel } from "../components/gis/WorkOrderCapturePanel";
import { Notice } from "../components/ui/Notice";
import { cn } from "../utils/cn";
import {
  MapIcon,
  ProjectIcon,
  ShieldIcon,
  InfoIcon,
  ChevronRightIcon,
} from "../components/Icons";
import {
  COMMERCIAL_SAMPLE_PROJECT_CODE,
  COPY,
  friendlyApiError,
  labelImportStatus,
  labelObjectType,
  labelWorkOrderStatus,
  WORK_ORDER_STATUS_LABEL,
} from "../lib/productCopy";

export const GISMapPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<Awaited<ReturnType<typeof api.listProjects>>>([]);
  const [projectId, setProjectId] = useState<string>(searchParams.get("project") || "");
  const [summary, setSummary] = useState<ProjectGisSummary | null>(null);
  const [packages, setPackages] = useState<DesignPackage[]>([]);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(
    searchParams.get("object") || null,
  );
  const [selectedWorkOrderId, setSelectedWorkOrderId] = useState<string | null>(
    searchParams.get("workOrder") || null,
  );
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [creatingWo, setCreatingWo] = useState(false);
  const [mobileTab, setMobileTab] = useState<"map" | "objects" | "orders">("map");
  const [dataNoteOpen, setDataNoteOpen] = useState(false);
  const [importNoteOpen, setImportNoteOpen] = useState<string | null>(null);
  const [objectTechOpen, setObjectTechOpen] = useState(false);

  const [woCode, setWoCode] = useState("");
  const [procedureCode, setProcedureCode] = useState("TRENCH-BEFORE-BACKFILL");
  const [toleranceM, setToleranceM] = useState("50");
  const [gpsThresholdM, setGpsThresholdM] = useState("30");
  const [assignedTo, setAssignedTo] = useState("现场采集");

  const [captureMarker, setCaptureMarker] = useState<{
    longitude: number;
    latitude: number;
    synthetic: boolean;
  } | null>(null);

  const onlineStyleUrl =
    (import.meta.env.VITE_MAP_STYLE_URL as string | undefined)?.trim() || null;

  const setFriendlyError = (raw: string) => {
    setError(friendlyApiError(raw));
    setErrorDetail(raw);
  };

  const loadProjects = useCallback(async () => {
    try {
      const rows = await api.listProjects();
      setProjects(rows);
      if (!projectId && rows.length > 0) {
        const preferred =
          rows.find((p) => p.code === COMMERCIAL_SAMPLE_PROJECT_CODE) || rows[0];
        setProjectId(preferred.id);
      }
      if (rows.length === 0) {
        setError(null);
        setErrorDetail(null);
        setNotice(COPY.emptyProjects);
      }
    } catch (err) {
      setProjects([]);
      const raw = err instanceof ApiRequestError ? err.message : COPY.loadFailed;
      setFriendlyError(raw);
    }
  }, [projectId]);

  const loadProjectData = useCallback(async (id: string) => {
    if (!id) {
      setSummary(null);
      setPackages([]);
      return;
    }
    setLoading(true);
    setError(null);
    setErrorDetail(null);
    try {
      const [gis, pkgs] = await Promise.all([
        api.projectGisSummary(id),
        api.listDesignPackages(id),
      ]);
      setSummary(gis);
      setPackages(pkgs);
      setSelectedObjectId((prev) => {
        if (prev && gis.objects.some((o) => o.id === prev)) return prev;
        return gis.objects[0]?.id ?? null;
      });
      setSelectedWorkOrderId((prev) => {
        if (prev && gis.work_orders.some((w) => w.id === prev)) return prev;
        return null;
      });
    } catch (err) {
      setSummary(null);
      setPackages([]);
      setFriendlyError(err instanceof ApiRequestError ? err.message : COPY.loadFailed);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (!projectId) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("project", projectId);
        if (selectedObjectId) next.set("object", selectedObjectId);
        else next.delete("object");
        if (selectedWorkOrderId) next.set("workOrder", selectedWorkOrderId);
        else next.delete("workOrder");
        return next;
      },
      { replace: true },
    );
    void loadProjectData(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, loadProjectData]);

  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (selectedObjectId) next.set("object", selectedObjectId);
        else next.delete("object");
        if (selectedWorkOrderId) next.set("workOrder", selectedWorkOrderId);
        else next.delete("workOrder");
        return next;
      },
      { replace: true },
    );
  }, [selectedObjectId, selectedWorkOrderId, setSearchParams]);

  const selectedObject: EngineeringObject | null = useMemo(() => {
    if (!summary || !selectedObjectId) return null;
    return summary.objects.find((o) => o.id === selectedObjectId) ?? null;
  }, [summary, selectedObjectId]);

  const selectedWorkOrder: WorkOrder | null = useMemo(() => {
    if (!summary || !selectedWorkOrderId) return null;
    return summary.work_orders.find((w) => w.id === selectedWorkOrderId) ?? null;
  }, [summary, selectedWorkOrderId]);

  const filteredWorkOrders = useMemo(() => {
    let list = summary?.work_orders ?? [];
    if (selectedObjectId) {
      list = list.filter((w) => w.engineering_object_id === selectedObjectId);
    }
    if (statusFilter) list = list.filter((w) => w.status === statusFilter);
    return list;
  }, [summary, selectedObjectId, statusFilter]);

  const hasSampleData = packages.some((p) => p.synthetic);

  const onGpkgImported = async (result: {
    package: DesignPackage;
    objects: EngineeringObject[];
  }) => {
    setError(null);
    setErrorDetail(null);
    setNotice(
      `已导入 ${result.package.package_code}，工程对象 ${result.package.object_count} 个。`,
    );
    await loadProjectData(projectId);
    const focusId = result.objects[0]?.id;
    if (focusId) {
      setSelectedObjectId(focusId);
      setMobileTab("map");
    }
  };

  const onCreateWorkOrder = async () => {
    if (!projectId || !selectedObject) {
      setFriendlyError(COPY.selectObjectFirst);
      return;
    }
    const code = woCode.trim() || `${selectedObject.object_code}-WO-1`;
    const tol = Number(toleranceM);
    const thr = Number(gpsThresholdM);
    if (!Number.isFinite(tol) || tol <= 0) {
      setFriendlyError(`${COPY.fieldSpatialTolerance}须为正数（${COPY.unitMeters}）`);
      return;
    }
    if (!Number.isFinite(thr) || thr <= 0) {
      setFriendlyError(`${COPY.fieldGpsAccuracy}须为正数（${COPY.unitMeters}）`);
      return;
    }
    setCreatingWo(true);
    setError(null);
    setErrorDetail(null);
    try {
      const wo = await api.createWorkOrder(projectId, {
        engineering_object_id: selectedObject.id,
        work_order_code: code,
        procedure_code: procedureCode.trim() || undefined,
        spatial_tolerance_m: tol,
        gps_accuracy_threshold_m: thr,
        assigned_to: assignedTo.trim() || undefined,
        notes: "工程作业台创建",
      });
      setNotice(`已创建施工工单 ${wo.work_order_code}`);
      setWoCode("");
      await loadProjectData(projectId);
      setSelectedWorkOrderId(wo.id);
      setMobileTab("orders");
    } catch (err) {
      setFriendlyError(err instanceof ApiRequestError ? err.message : "创建工单失败");
    } finally {
      setCreatingWo(false);
    }
  };

  const onSelectObject = (objectId: string) => {
    setSelectedObjectId(objectId);
    const linked = summary?.work_orders.find((w) => w.engineering_object_id === objectId);
    if (linked) setSelectedWorkOrderId(linked.id);
    setMobileTab("objects");
  };

  const selectedProject = projects.find((p) => p.id === projectId) ?? null;
  const latestPackage = packages[0] ?? null;

  return (
    <div className="space-y-4 page-enter">
      <div className="flex flex-col gap-3 rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-900">工程作业</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600">{COPY.engineeringIntro}</p>
          {hasSampleData || latestPackage?.synthetic ? (
            <span className="mt-2 inline-flex rounded-full border border-violet-200 bg-violet-50 px-2.5 py-0.5 text-[11px] font-medium text-violet-900">
              {COPY.sampleDataBadge}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setDataNoteOpen((v) => !v)}
          className="shrink-0 rounded-2xl border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          {COPY.dataNoteTitle}
        </button>
      </div>

      {dataNoteOpen ? (
        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-700">
          <p>
            · 设计数据来源：
            {latestPackage
              ? `${latestPackage.source_filename}（${latestPackage.synthetic ? "样例" : "受控"}）`
              : "尚未导入"}
          </p>
          <p>· 是否样例数据：{latestPackage?.synthetic ? "是" : packages.length ? "否" : "—"}</p>
          <p>· 导入时对敏感字段执行白名单脱敏，不直接暴露原始个人信息。</p>
          <p>· 当前提供工程对象浏览与工单核验，不包含完整地理信息系统编辑能力。</p>
          <p>· 位置核验仅判断采集位置合理性，不构成绝对防作弊证明。</p>
          <details className="mt-2">
            <summary className="cursor-pointer font-medium text-sky-800">{COPY.techDetails}</summary>
            <pre className="mt-2 max-h-40 overflow-auto rounded-xl bg-white p-2 text-[10px]">
              {JSON.stringify(
                {
                  packages: packages.map((p) => ({
                    package_code: p.package_code,
                    source_type: p.source_type,
                    source_crs_epsg: p.source_crs_epsg,
                    source_sha256: p.source_sha256,
                    synthetic: p.synthetic,
                    import_status: p.import_status,
                    import_warnings: p.import_warnings,
                  })),
                  truth_note: summary?.truth_note,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </div>
      ) : null}

      <div className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <label className="block min-w-0 flex-1">
            <span className="text-xs font-medium text-slate-500">{COPY.projectSelect}</span>
            <select
              className="mt-1 w-full rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm"
              value={projectId}
              onChange={(e) => {
                setProjectId(e.target.value);
                setSelectedObjectId(null);
                setSelectedWorkOrderId(null);
                setSummary(null);
              }}
            >
              {projects.length === 0 ? <option value="">{COPY.emptyProjects}</option> : null}
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <div className="text-sm text-slate-600">
            {selectedProject ? (
              <>
                <span className="font-semibold text-slate-900">{selectedProject.name}</span>
                <span className="mx-2 text-slate-300">·</span>
                <span className="text-xs text-slate-500">
                  {COPY.projectCode} {selectedProject.code}
                </span>
              </>
            ) : (
              "未选择项目"
            )}
          </div>
          <button
            type="button"
            onClick={() => projectId && void loadProjectData(projectId)}
            disabled={!projectId || loading}
            className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm font-medium text-sky-700 disabled:opacity-50"
          >
            {loading ? "加载中…" : COPY.refresh}
          </button>
        </div>
        {summary ? (
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-xl bg-slate-50 py-2">
              {COPY.statsDesign} <strong>{summary.design_package_count}</strong>
            </div>
            <div className="rounded-xl bg-slate-50 py-2">
              {COPY.statsObjects} <strong>{summary.engineering_object_count}</strong>
            </div>
            <div className="rounded-xl bg-slate-50 py-2">
              {COPY.statsWorkOrders} <strong>{summary.work_order_count}</strong>
            </div>
          </div>
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <p>{error}</p>
          {errorDetail && errorDetail !== error ? (
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer">{COPY.techDetails}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all">{errorDetail}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
      {notice ? <Notice type="info" message={notice} /> : null}

      <div className="flex gap-2 lg:hidden">
        {(
          [
            ["map", "地图"],
            ["objects", "对象/设计"],
            ["orders", "工单/资料"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setMobileTab(key)}
            className={cn(
              "flex-1 rounded-2xl border px-3 py-2 text-sm font-medium",
              mobileTab === key
                ? "border-sky-300 bg-sky-50 text-sky-800"
                : "border-slate-200 bg-white text-slate-600",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(260px,0.85fr)_minmax(0,1.4fr)_minmax(300px,1fr)]">
        <aside className={cn("space-y-4", mobileTab !== "objects" && "hidden lg:block")}>
          {projectId ? (
            <GpkgImportPanel
              projectId={projectId}
              disabled={loading}
              onImported={(result) => void onGpkgImported(result)}
            />
          ) : null}
          <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <ProjectIcon className="h-4 w-4 text-sky-600" />
              <h3 className="text-sm font-semibold text-slate-900">{COPY.gpkgImportRecords}</h3>
            </div>
            <div className="max-h-52 space-y-2 overflow-y-auto">
              {packages.map((pkg) => (
                <div
                  key={pkg.id}
                  className="rounded-2xl border border-slate-100 bg-slate-50 p-3 text-[11px] leading-4 text-slate-700"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-900">{pkg.package_code}</span>
                    {pkg.synthetic ? (
                      <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 font-medium text-violet-900">
                        {COPY.sampleDesignData}
                      </span>
                    ) : null}
                    <span className="rounded-full bg-white px-2 py-0.5">
                      {labelImportStatus(pkg.import_status)}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-slate-500">{pkg.source_filename}</p>
                  {pkg.import_status === "partial" ? (
                    <div className="mt-2">
                      <button
                        type="button"
                        className="text-sky-700 underline"
                        onClick={() =>
                          setImportNoteOpen((id) => (id === pkg.id ? null : pkg.id))
                        }
                      >
                        {COPY.viewImportNotes}
                      </button>
                      {importNoteOpen === pkg.id ? (
                        <div className="mt-1 rounded-xl bg-white p-2 text-[11px] text-slate-600">
                          <p>{COPY.partialImportExplain}</p>
                          <details className="mt-1">
                            <summary className="cursor-pointer text-sky-800">{COPY.techDetails}</summary>
                            <pre className="mt-1 max-h-24 overflow-auto text-[10px]">
                              {JSON.stringify(pkg.import_warnings, null, 2)}
                            </pre>
                          </details>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  <details className="mt-1">
                    <summary className="cursor-pointer text-sky-800">{COPY.techDetails}</summary>
                    <p className="mt-1 font-mono text-[10px] break-all">SHA-256 {pkg.source_sha256}</p>
                    <p className="mt-0.5">
                      CRS EPSG:{pkg.source_crs_epsg} · {pkg.source_type} · 对象 {pkg.object_count}
                    </p>
                  </details>
                </div>
              ))}
              {!packages.length ? <p className="text-xs text-slate-500">{COPY.noPackages}</p> : null}
            </div>
          </section>

          <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <MapIcon className="h-4 w-4 text-sky-600" />
              <h3 className="text-sm font-semibold text-slate-900">{COPY.statsObjects}</h3>
            </div>
            <div className="max-h-64 space-y-2 overflow-y-auto">
              {(summary?.objects || []).map((obj) => (
                <button
                  key={obj.id}
                  type="button"
                  onClick={() => onSelectObject(obj.id)}
                  className={cn(
                    "w-full rounded-2xl border px-3 py-2.5 text-left text-sm transition-all",
                    selectedObjectId === obj.id
                      ? "border-orange-300 bg-orange-50"
                      : "border-slate-200 hover:border-sky-200",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-900">{obj.object_code}</span>
                    <span className="text-[10px] text-slate-500">{labelObjectType(obj.object_type)}</span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-slate-500">{obj.name}</p>
                </button>
              ))}
              {!summary?.objects.length ? (
                <p className="text-xs text-slate-500">{COPY.noObjects}</p>
              ) : null}
            </div>

            {selectedObject ? (
              <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 text-[11px]">
                <p>
                  <span className="text-slate-500">{COPY.objectCode}</span>{" "}
                  <strong>{selectedObject.object_code}</strong>
                </p>
                <p>
                  <span className="text-slate-500">{COPY.objectType}</span>{" "}
                  <strong>{labelObjectType(selectedObject.object_type)}</strong>
                </p>
                <p>
                  <span className="text-slate-500">{COPY.designVersion}</span>{" "}
                  <strong>{selectedObject.design_version}</strong>
                </p>
                <p>
                  <span className="text-slate-500">{COPY.sourceLayer}</span>{" "}
                  <strong>{selectedObject.source_layer}</strong>
                </p>
                <button
                  type="button"
                  className="text-sky-700 underline"
                  onClick={() => setObjectTechOpen((v) => !v)}
                >
                  {objectTechOpen ? "收起" : COPY.techDetails}
                </button>
                {objectTechOpen ? (
                  <>
                    <p className="text-slate-500">{COPY.engineeringAttrs}</p>
                    <pre className="max-h-24 overflow-auto rounded-xl bg-slate-50 p-2 text-[10px]">
                      {JSON.stringify(selectedObject.attributes_snapshot, null, 2)}
                    </pre>
                    <p className="text-slate-500">{COPY.designRequirements}</p>
                    <pre className="max-h-28 overflow-auto rounded-xl bg-slate-50 p-2 text-[10px]">
                      {JSON.stringify(selectedObject.expected_rules, null, 2)}
                    </pre>
                  </>
                ) : null}
              </div>
            ) : null}
          </section>
        </aside>

        <section
          className={cn(
            "overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-sm",
            mobileTab !== "map" && "hidden lg:block",
          )}
        >
          <div className="border-b border-slate-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-900">{COPY.mapTitle}</h3>
          </div>
          <div className="h-[min(52vh,480px)] min-h-[280px] w-full lg:h-[min(62vh,560px)]">
            <GeoMap
              objects={summary?.objects ?? []}
              selectedObjectId={selectedObjectId}
              onSelectObject={onSelectObject}
              captureMarker={captureMarker}
              onlineStyleUrl={onlineStyleUrl}
              className="h-full w-full"
            />
          </div>
          <p className="border-t border-slate-100 px-4 py-2 text-[11px] leading-4 text-slate-500">
            {COPY.mapFooter}
          </p>
        </section>

        <aside className={cn("space-y-4", mobileTab !== "orders" && "hidden lg:block")}>
          <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <ShieldIcon className="h-4 w-4 text-sky-600" />
                <h3 className="text-sm font-semibold text-slate-900">{COPY.workOrders}</h3>
              </div>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="rounded-xl border border-slate-200 px-2 py-1 text-[11px]"
              >
                <option value="">{COPY.allStatuses}</option>
                {Object.entries(WORK_ORDER_STATUS_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="max-h-40 space-y-2 overflow-y-auto">
              {filteredWorkOrders.map((wo) => {
                const obj = summary?.objects.find((o) => o.id === wo.engineering_object_id);
                return (
                  <button
                    key={wo.id}
                    type="button"
                    onClick={() => {
                      setSelectedWorkOrderId(wo.id);
                      setSelectedObjectId(wo.engineering_object_id);
                    }}
                    className={cn(
                      "w-full rounded-2xl border px-3 py-2 text-left text-xs transition-all",
                      selectedWorkOrderId === wo.id
                        ? "border-sky-300 bg-sky-50"
                        : "border-slate-200 hover:border-sky-200",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-900">{wo.work_order_code}</span>
                      <span className="text-[10px] text-slate-500">
                        {labelWorkOrderStatus(wo.status)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[10px] text-slate-500">
                      {obj?.object_code ?? "—"} · {COPY.designVersion} {wo.design_version}
                    </p>
                    <p className="mt-0.5 text-[10px] text-slate-500">
                      {COPY.fieldSpatialTolerance} {wo.spatial_tolerance_m}
                      {COPY.unitMeters} · {COPY.fieldGpsAccuracy} {wo.gps_accuracy_threshold_m}
                      {COPY.unitMeters}
                      {wo.assigned_to ? ` · ${wo.assigned_to}` : ""}
                    </p>
                  </button>
                );
              })}
              {!filteredWorkOrders.length ? (
                <p className="text-xs text-slate-500">{COPY.noWorkOrders}</p>
              ) : null}
            </div>
          </section>

          <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900">{COPY.createWorkOrder}</h3>
            {!selectedObject ? (
              <p className="mt-2 text-xs text-amber-700">{COPY.selectObjectFirst}</p>
            ) : (
              <div className="mt-3 space-y-2">
                <label className="block text-[11px] text-slate-500">
                  {COPY.fieldWorkOrderCode}
                  <input
                    value={woCode}
                    onChange={(e) => setWoCode(e.target.value)}
                    placeholder={`${selectedObject.object_code}-WO-1`}
                    className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs text-slate-900"
                  />
                </label>
                <label className="block text-[11px] text-slate-500">
                  {COPY.fieldProcedure}
                  <input
                    value={procedureCode}
                    onChange={(e) => setProcedureCode(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs text-slate-900"
                  />
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[11px] text-slate-500">
                    {COPY.fieldSpatialTolerance}（{COPY.unitMeters}）
                    <input
                      value={toleranceM}
                      onChange={(e) => setToleranceM(e.target.value)}
                      className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs text-slate-900"
                    />
                  </label>
                  <label className="block text-[11px] text-slate-500">
                    {COPY.fieldGpsAccuracy}（{COPY.unitMeters}）
                    <input
                      value={gpsThresholdM}
                      onChange={(e) => setGpsThresholdM(e.target.value)}
                      className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs text-slate-900"
                    />
                  </label>
                </div>
                <label className="block text-[11px] text-slate-500">
                  {COPY.fieldAssignee}
                  <input
                    value={assignedTo}
                    onChange={(e) => setAssignedTo(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs text-slate-900"
                  />
                </label>
                <button
                  type="button"
                  disabled={creatingWo}
                  onClick={() => void onCreateWorkOrder()}
                  className="w-full rounded-2xl bg-sky-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {creatingWo ? "创建中…" : COPY.createWorkOrder}
                </button>
              </div>
            )}
          </section>

          {selectedWorkOrder ? (
            <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">{COPY.fieldCapture}</h3>
                <Link
                  to={`/work-orders/${selectedWorkOrder.id}`}
                  className="inline-flex items-center gap-0.5 text-[11px] font-medium text-sky-700"
                >
                  {COPY.independentWorkOrderPage} <ChevronRightIcon className="h-3.5 w-3.5" />
                </Link>
              </div>
              <details className="mb-3 rounded-xl border border-slate-100 bg-slate-50 p-2 text-[10px]">
                <summary className="cursor-pointer font-semibold text-slate-600">
                  {COPY.frozenSnapshots}
                </summary>
                <p className="mt-1">
                  {COPY.designVersion} {selectedWorkOrder.design_version}
                </p>
                <pre className="mt-1 max-h-28 overflow-auto">
                  {JSON.stringify(
                    {
                      design_snapshot: selectedWorkOrder.design_snapshot,
                      geometry_snapshot: selectedWorkOrder.geometry_snapshot,
                      rules_snapshot: selectedWorkOrder.rules_snapshot,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <WorkOrderCapturePanel
                workOrder={selectedWorkOrder}
                onWorkOrderUpdated={(wo) => {
                  setSummary((prev) =>
                    prev
                      ? {
                          ...prev,
                          work_orders: prev.work_orders.map((w) => (w.id === wo.id ? wo : w)),
                        }
                      : prev,
                  );
                }}
                onCaptureMarkerChange={setCaptureMarker}
              />
            </section>
          ) : (
            <div className="rounded-[24px] border border-dashed border-slate-200 bg-white p-4 text-xs text-slate-500">
              <InfoIcon className="mb-1 inline h-4 w-4 text-slate-400" />{" "}
              选择或创建施工工单后，可提交现场资料并查看核验结果。
            </div>
          )}
        </aside>
      </div>
    </div>
  );
};
