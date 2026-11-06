import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { ArrowUpRight, Camera, Crosshair, FileUp, History, RefreshCw } from "lucide-react";
import { api, ApiRequestError } from "../../lib/api";
import type {
  AnalyzerName, CapabilityMeta, ComplianceEvaluation, EvidenceCapture, LocationSource, VerificationDetail, WorkOrder,
} from "../../lib/api";
import { friendlyApiError, labelLocationSource, labelSpatialStatus } from "../../lib/productCopy";
import {
  groupDifferenceRows,
  labelHumanReviewStatus,
  labelRuleVerdict,
  labelTaskProgress,
  pickDefaultCaptureId,
  spatialBusinessSummary,
  structureObservations,
} from "../../lib/verificationDisplay";
import { EvidencePreview } from "../ui/EvidencePreview";
import { Notice } from "../ui/Notice";
import { FieldScene } from "../ui/FieldScene";
import { OperationStatus } from "../ui/OperationStatus";
import type { CaptureMarker } from "./GeoMap";
import { formatWorkbenchTime } from "./workbenchTime";
import "./workbench.css";

const CAPTURE_ALLOWED = new Set(["assigned", "remediating", "evidence_uploaded", "analyzing"]);
const ACTIVE_JOBS = new Set(["queued", "running", "sealing"]);
const ANALYZERS: Record<AnalyzerName, string> = {
  remote_http: "辅助提取",
  stub: "人工核验",
  demo_fixture: "规则辅助",
};

type Props = {
  workOrder: WorkOrder;
  onWorkOrderUpdated?: (workOrder: WorkOrder) => void;
  onCaptureMarkerChange?: (marker: CaptureMarker | null) => void;
};

type CaptureResult = {
  detail: VerificationDetail;
  compliance: ComplianceEvaluation | null;
};

function localTime(milliseconds = Date.now()) {
  const date = new Date(milliseconds);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

const meters = (value: number | null) => value == null ? "—" : `${Number(value.toFixed(1))} 米`;
const locationLabel = (value: string) => value === "synthetic_demo" ? "标注位置" : labelLocationSource(value);
const explain = (error: unknown, fallback: string) => error instanceof ApiRequestError ? friendlyApiError(error.message) : fallback;

async function loadCaptureResult(jobId: string): Promise<CaptureResult> {
  const detail = await api.verification(jobId);
  const compliance = ACTIVE_JOBS.has(detail.job.status)
    ? null
    : await api.getCompliance(jobId).catch(() => null);
  return { detail, compliance: compliance?.job_id === jobId ? compliance : null };
}

export function WorkOrderCapturePanel({ workOrder, onWorkOrderUpdated, onCaptureMarkerChange }: Props) {
  const [captures, setCaptures] = useState<EvidenceCapture[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, CaptureResult>>({});
  const [historyBadges, setHistoryBadges] = useState<Record<string, { status: string; verdict?: string }>>({});
  const [historyLoading, setHistoryLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [resultRevision, setResultRevision] = useState(0);
  const [mutation, setMutation] = useState<"upload" | "review" | null>(null);
  const [notice, setNotice] = useState("");
  const [formError, setFormError] = useState("");
  const [reviewError, setReviewError] = useState<{ captureId: string; message: string } | null>(null);
  const [locating, setLocating] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [accuracy, setAccuracy] = useState("");
  const [locationSource, setLocationSource] = useState<LocationSource>("unknown");
  const [captureTime, setCaptureTime] = useState(localTime);
  const [deviceId, setDeviceId] = useState("");
  const [metadataText, setMetadataText] = useState("");
  const [adapters, setAdapters] = useState<CapabilityMeta["adapters"] | null>(null);
  const [analyzerOverride, setAnalyzerOverride] = useState<AnalyzerName | null>(null);
  const [markerSource, setMarkerSource] = useState<"capture" | "form">("capture");
  const fileRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(false);
  const listSequence = useRef(0);
  const mutationLock = useRef(false);
  const orderUpdate = useRef(onWorkOrderUpdated);
  orderUpdate.current = onWorkOrderUpdated;
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const analyzer = analyzerOverride ?? (adapters?.remote_http?.enabled ? "remote_http" : "stub");
  const selected = captures.find((capture) => capture.id === selectedId) ?? null;
  const jobId = selected?.verification_job_id ?? null;
  const result = selected && results[selected.id]?.detail.job.id === jobId ? results[selected.id] : null;
  const job = result?.detail.job ?? null;
  const preview = result?.detail.evidence.id === selected?.evidence_id ? result?.detail.evidence : null;
  const canUpload = Boolean(workOrder.baseline_id && CAPTURE_ALLOWED.has(workOrder.status));

  const refreshCaptures = useCallback(async (preferredId?: string) => {
    const request = ++listSequence.current;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const rows = await api.listWorkOrderCaptures(workOrder.id);
      if (request !== listSequence.current) return;
      setCaptures(rows);
      setSelectedId((current) => {
        const desired = preferredId ?? current;
        return rows.some((row) => row.id === desired) ? desired : pickDefaultCaptureId(rows);
      });
      setResultRevision((revision) => revision + 1);
    } catch (error) {
      if (request === listSequence.current) setHistoryError(explain(error, "无法读取采集记录"));
    } finally {
      if (request === listSequence.current) setHistoryLoading(false);
    }
  }, [workOrder.id]);

  useEffect(() => {
    mounted.current = true;
    void refreshCaptures();
    return () => {
      mounted.current = false;
      listSequence.current += 1;
    };
  }, [refreshCaptures]);

  useEffect(() => {
    let live = true;
    void api.meta().then((meta) => {
      if (!live) return;
      setAdapters(meta.adapters);
    }).catch(() => {});
    return () => { live = false; };
  }, []);

  useEffect(() => {
    setDetailError("");
    setDetailLoading(Boolean(selectedId && jobId));
    if (!selectedId || !jobId) return;

    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const loaded = await loadCaptureResult(jobId);
        if (disposed) return;
        setResults((previous) => ({ ...previous, [selectedId]: loaded }));
        setDetailLoading(false);
        setDetailError("");
        const state = loaded.detail.job.status;
        if (ACTIVE_JOBS.has(state)) {
          timer = window.setTimeout(() => void poll(), 1600);
        } else {
          try {
            const order = await api.getWorkOrder(workOrder.id);
            if (!disposed) orderUpdate.current?.(order);
          } catch {
            // Keep the latest order returned by its own endpoint.
          }
        }
      } catch (error) {
        if (disposed) return;
        setDetailLoading(false);
        setDetailError(explain(error, "无法读取核验结果"));
        timer = window.setTimeout(() => void poll(), 4800);
      }
    };
    void poll();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [selectedId, jobId, resultRevision, workOrder.id]);

  useEffect(() => {
    let disposed = false;
    const refreshBadges = async () => {
      for (const capture of captures) {
        if (disposed) return;
        if (!capture.verification_job_id || capture.id === selectedRef.current) continue;
        try {
          const { detail, compliance } = await loadCaptureResult(capture.verification_job_id);
          if (!disposed) setHistoryBadges((previous) => ({
            ...previous,
            [capture.id]: { status: detail.job.status, verdict: compliance?.verdict },
          }));
        } catch {
          // A failed background lookup does not block other records.
        }
      }
    };
    void refreshBadges();
    return () => { disposed = true; };
  }, [captures]);

  useEffect(() => {
    if (!onCaptureMarkerChange) return;
    const hasFormPosition = markerSource === "form" && latitude.trim() !== "" && longitude.trim() !== "";
    const lat = hasFormPosition ? Number(latitude) : selected?.latitude;
    const lon = hasFormPosition ? Number(longitude) : selected?.longitude;
    const valid = lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon)
      && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
    onCaptureMarkerChange(valid ? {
      longitude: lon,
      latitude: lat,
      synthetic: hasFormPosition ? locationSource === "synthetic_demo" : selected?.is_synthetic_location,
    } : null);
  }, [latitude, longitude, locationSource, markerSource, onCaptureMarkerChange, selected]);

  const requestPosition = () => {
    if (!navigator.geolocation) {
      setFormError("设备定位不可用，可手动填写经纬度。");
      return;
    }
    setLocating(true);
    setFormError("");
    navigator.geolocation.getCurrentPosition(
      ({ coords, timestamp }) => {
        if (!mounted.current) return;
        setLatitude(String(coords.latitude));
        setLongitude(String(coords.longitude));
        setAccuracy(Number.isFinite(coords.accuracy) ? String(Math.round(coords.accuracy * 10) / 10) : "");
        setCaptureTime(localTime(timestamp));
        setLocationSource("device_gps");
        setMarkerSource("form");
        setLocating(false);
      },
      () => {
        if (!mounted.current) return;
        setFormError("未获取设备定位，可手动填写经纬度。");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 },
    );
  };

  const editPosition = (setter: (value: string) => void, value: string) => {
    setter(value);
    setMarkerSource("form");
    if (locationSource !== "synthetic_demo") setLocationSource("manual");
  };

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !canUpload || mutationLock.current) return;
    const lat = latitude.trim() ? Number(latitude) : null;
    const lon = longitude.trim() ? Number(longitude) : null;
    const acc = accuracy.trim() ? Number(accuracy) : null;
    if ((lat != null && (!Number.isFinite(lat) || Math.abs(lat) > 90))
      || (lon != null && (!Number.isFinite(lon) || Math.abs(lon) > 180))
      || (acc != null && (!Number.isFinite(acc) || acc < 0))) {
      setFormError("请检查经纬度范围和定位精度。");
      return;
    }
    let metadata: Record<string, unknown>;
    try {
      metadata = JSON.parse(metadataText || "{}") as Record<string, unknown>;
      if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error();
    } catch {
      setFormError("附加信息须为有效的 JSON 对象。");
      return;
    }
    const date = captureTime ? new Date(captureTime) : new Date();
    if (!Number.isFinite(date.getTime())) {
      setFormError("请填写有效的采集时间。");
      return;
    }
    mutationLock.current = true;
    setMutation("upload");
    setFormError("");
    setNotice("");
    try {
      const updated = await api.uploadWorkOrderVerification({
        workOrderId: workOrder.id,
        file,
        analyzer,
        latitude: lat,
        longitude: lon,
        accuracy_m: acc,
        location_source: locationSource,
        is_synthetic_location: locationSource === "synthetic_demo",
        client_captured_at: date.toISOString(),
        device_id: deviceId.trim() || "WEB-WORK-ORDER",
        metadata: {
          source: "engineering-workbench",
          privacy: "user-provided",
          ...metadata,
          synthetic_demo: locationSource === "synthetic_demo",
        },
      });
      orderUpdate.current?.(updated.work_order);
      if (!mounted.current) return;
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      setNotice(`现场资料已提交 · ${labelSpatialStatus(updated.capture.spatial_check_status)}`);
      setCaptures((previous) => [updated.capture, ...previous.filter((row) => row.id !== updated.capture.id)]);
      setMarkerSource("capture");
      setSelectedId(updated.capture.id);
      void refreshCaptures(updated.capture.id);
    } catch (error) {
      if (mounted.current) setFormError(explain(error, "无法提交现场资料"));
    } finally {
      mutationLock.current = false;
      if (mounted.current) setMutation(null);
    }
  };

  const approve = async () => {
    if (!selected || !job || job.status !== "needs_review" || detailLoading || mutationLock.current) return;
    const captureId = selected.id;
    mutationLock.current = true;
    setMutation("review");
    setReviewError(null);
    try {
      const reviewed = await api.review(job.id, {
        decision: "approve",
        reviewer: "现场复核员",
        note: "通过工单现场资料复核",
      });
      if (mounted.current) {
        setResults((previous) => {
          const existing = previous[captureId];
          return existing ? {
            ...previous,
            [captureId]: { ...existing, detail: { ...existing.detail, ...reviewed } },
          } : previous;
        });
        setNotice("人工复核已通过");
        setResultRevision((revision) => revision + 1);
      }
      try {
        orderUpdate.current?.(await api.getWorkOrder(workOrder.id));
      } catch {
        // The returned job already confirms the review.
      }
    } catch (error) {
      if (mounted.current) setReviewError({ captureId, message: explain(error, "无法提交复核") });
    } finally {
      mutationLock.current = false;
      if (mounted.current) setMutation(null);
    }
  };

  return (
    <div className="gis-capture-workspace" data-view={canUpload || !selected ? "capture" : "review"}>
      {(canUpload || !selected) && <section className="fm-panel gis-capture-form">
        <div className="gis-section-head">
          <h3><FileUp size={15} aria-hidden="true" /> 现场采集</h3>
        </div>
        {canUpload && !file && <FieldScene scene="capture"
          guidance="取景时保留工点全貌，再补拍相关细节和量测记录。" />}
        {canUpload ? (
          <form className="gis-form gis-capture-form-body" onSubmit={(event) => void upload(event)}>
            <label className="gis-upload">
              <Camera size={19} aria-hidden="true" />
              <span>{file?.name || "选择现场照片或视频"}</span>
              <small>照片 · JPG、PNG / 视频 · MP4、WebM</small>
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,video/mp4,video/webm,.jpg,.jpeg,.png,.mp4,.webm"
                aria-label="选择现场照片或视频"
                disabled={Boolean(mutation)}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <button type="button" className="fm-button" onClick={requestPosition} disabled={locating || Boolean(mutation)}>
              <Crosshair size={14} aria-hidden="true" /> {locating ? "定位中…" : "获取设备定位"}
            </button>
            <div className="gis-form-pair">
              <label>纬度<input className="fm-input" inputMode="decimal" value={latitude} onChange={(event) => editPosition(setLatitude, event.target.value)} disabled={Boolean(mutation)} /></label>
              <label>经度<input className="fm-input" inputMode="decimal" value={longitude} onChange={(event) => editPosition(setLongitude, event.target.value)} disabled={Boolean(mutation)} /></label>
              <label>精度（米）<input className="fm-input" inputMode="decimal" value={accuracy} onChange={(event) => editPosition(setAccuracy, event.target.value)} disabled={Boolean(mutation)} /></label>
              <label>位置来源
                <select className="fm-input" value={locationSource} onChange={(event) => setLocationSource(event.target.value as LocationSource)} disabled={Boolean(mutation)}>
                  <option value="unknown">未提供</option>
                  <option value="manual">手动录入</option>
                  <option value="synthetic_demo">标注位置</option>
                  {locationSource === "device_gps" && <option value="device_gps">设备定位</option>}
                </select>
              </label>
            </div>
            <label>采集时间<input className="fm-input" type="datetime-local" value={captureTime} onChange={(event) => setCaptureTime(event.target.value)} disabled={Boolean(mutation)} /></label>
            <details className="gis-disclosure">
              <summary>采集设置</summary>
              <div className="gis-form">
                <label>核验方式
                  <select className="fm-input" value={analyzer} onChange={(event) => setAnalyzerOverride(event.target.value as AnalyzerName)} disabled={Boolean(mutation)}>
                    <option value="stub">{ANALYZERS.stub}</option>
                    {adapters?.remote_http?.enabled && <option value="remote_http">{ANALYZERS.remote_http}</option>}
                    {adapters?.demo_fixture?.enabled && <option value="demo_fixture">{ANALYZERS.demo_fixture}</option>}
                  </select>
                </label>
                <label>设备编号<input className="fm-input" value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={Boolean(mutation)} /></label>
                <label>附加信息<textarea className="fm-input" rows={2} value={metadataText} onChange={(event) => setMetadataText(event.target.value)} disabled={Boolean(mutation)} /></label>
              </div>
            </details>
            {mutation === "upload" && <OperationStatus detail="文件登记后会自动进入核验队列。">正在提交现场资料</OperationStatus>}
            {locating && <OperationStatus>正在获取设备位置</OperationStatus>}
            {formError && <Notice type="warning" message={formError} />}
            <button type="submit" className="fm-button fm-button-primary" disabled={!file || Boolean(mutation) || locating}>
              {mutation === "upload" ? "提交中…" : "提交核验"}
            </button>
          </form>
        ) : (
          <div className="gis-capture-form-body">
            <p className="gis-empty">
              {workOrder.status === "draft" ? "工单分派后可提交现场资料。" : !workOrder.baseline_id ? "工单缺少设计基线。" : "可查阅该工单已提交的现场资料。"}
            </p>
          </div>
        )}
      </section>}

      <div className="gis-capture-review">
        {notice && <Notice type="success" message={notice} />}
        <section className="fm-panel">
          <div className="gis-section-head">
            <h3><History size={15} aria-hidden="true" /> 核验记录 <span className="gis-count">{captures.length}</span></h3>
            <button type="button" className="gis-icon-button" aria-label="刷新核验记录" disabled={historyLoading} onClick={() => void refreshCaptures()}>
              <RefreshCw size={15} />
            </button>
          </div>
          {historyError && <p className="gis-record-error" role="status">{historyError}</p>}
          <ul className="gis-capture-history">
            {captures.map((capture) => {
              const data = results[capture.id];
              const currentBadge = data && capture.id === selectedId
                ? { status: data.detail.job.status, verdict: data.compliance?.verdict }
                : historyBadges[capture.id];
              const status = currentBadge?.status ?? data?.detail.job.status;
              const verdict = currentBadge ? currentBadge.verdict : data?.compliance?.verdict;
              return (
                <li key={capture.id}>
                  <button type="button" aria-pressed={capture.id === selectedId} onClick={() => {
                    setMarkerSource("capture");
                    if (selectedId !== capture.id) {
                      setDetailLoading(Boolean(capture.verification_job_id));
                      setSelectedId(capture.id);
                    }
                  }}>
                    <strong>{formatWorkbenchTime(capture.server_received_at)}</strong>
                    <span>{labelSpatialStatus(capture.spatial_check_status)} · {locationLabel(capture.location_source)}</span>
                    {status && <span>{labelTaskProgress(status)}{verdict ? ` · ${labelRuleVerdict(verdict)}` : ""}</span>}
                  </button>
                </li>
              );
            })}
            {!captures.length && <li className="gis-empty">{historyLoading ? "正在读取核验记录…" : "暂无核验记录"}</li>}
          </ul>
        </section>
        {selected && (
          <div className="gis-review-detail">
            {(preview || !jobId) && (
              <EvidencePreview
                evidenceId={selected.evidence_id}
                originalName={preview?.original_name}
                sha256={preview?.sha256}
                registeredSizeBytes={preview?.size_bytes}
              />
            )}
            <div className="gis-review-inspector">
              <CaptureResults capture={selected} result={result} />
              {detailLoading && <OperationStatus>正在读取核验结果</OperationStatus>}
              {mutation === "review" && <OperationStatus detail="正在写入报告和证据包。">正在保存复核并封存</OperationStatus>}
              {detailError && <Notice type="warning" message={detailError} />}
              {job && (
                <div className="gis-actions gis-capture-actions">
                  <Link className="fm-button" to={`/backend-workflow?job=${encodeURIComponent(job.id)}`}>
                    打开核验中心 <ArrowUpRight size={13} aria-hidden="true" />
                  </Link>
                  {job.status === "needs_review" && (
                    <button type="button" className="fm-button fm-button-primary" onClick={() => void approve()} disabled={Boolean(mutation) || detailLoading || Boolean(detailError)}>
                      {mutation === "review" ? "提交中…" : "批准核验"}
                    </button>
                  )}
                </div>
              )}
              {reviewError?.captureId === selected.id && <Notice type="warning" message={reviewError.message} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CaptureResults({ capture, result }: { capture: EvidenceCapture; result: CaptureResult | null }) {
  const { detail, compliance } = result ?? {};
  const job = detail?.job;
  const observations = job?.result?.observations;
  const raw = observations && typeof observations === "object" ? observations as Record<string, unknown> : null;
  const measurements = raw?.measurements;
  const structured = useMemo(
    () => structureObservations(measurements && typeof measurements === "object" ? measurements as Record<string, unknown> : raw),
    [measurements, raw],
  );
  const differences = useMemo(() => groupDifferenceRows(compliance?.differences), [compliance?.differences]);
  return (
    <section className="fm-panel gis-capture-results">
      <div className="gis-section-head"><h3>核验详情</h3></div>
      <div className="gis-capture-result-grid">
        <div>
          <h4>空间核验</h4>
          <span className="gis-status" data-status={capture.spatial_check_status === "passed" ? "approved" : capture.spatial_check_status === "failed" ? "deviation" : "needs_review"}>
            {labelSpatialStatus(capture.spatial_check_status)}
          </span>
          <p>{spatialBusinessSummary(capture)}</p>
          <dl className="gis-data">
            <div><dt>目标距离</dt><dd>{meters(capture.distance_to_target_m)}</dd></div>
            <div><dt>空间容差</dt><dd>{meters(capture.tolerance_m)}</dd></div>
            <div><dt>定位精度</dt><dd>{meters(capture.accuracy_m)}</dd></div>
            <div><dt>精度阈值</dt><dd>{meters(capture.gps_accuracy_threshold_m)}</dd></div>
            <div><dt>采集时间</dt><dd>{formatWorkbenchTime(capture.client_captured_at)}</dd></div>
          </dl>
        </div>
        <div>
          <h4>规则核验</h4>
          <dl className="gis-data">
            <div><dt>处理进度</dt><dd>{job ? labelTaskProgress(job.status) : capture.verification_job_id ? "等待结果" : "等待任务"}</dd></div>
            <div><dt>规则初判</dt><dd>{compliance ? labelRuleVerdict(compliance.verdict) : "—"}</dd></div>
            <div><dt>人工复核</dt><dd>{job ? labelHumanReviewStatus(job.status) : "—"}</dd></div>
          </dl>
          {differences.bad.length > 0 && <p>偏差项 · {differences.bad.join("、")}</p>}
          {differences.pending.length > 0 && <p>待确认 · {differences.pending.join("、")}</p>}
          {differences.ok.length > 0 && <p>符合项 · {differences.ok.join("、")}</p>}
          {job?.status === "failed" && <p className="gis-record-error">核验未完成，可在核验中心查看处理记录。</p>}
        </div>
      </div>
      {(job || compliance) && (
        <div className="gis-capture-observations">
          <h4>现场观察</h4>
          {job?.analyzer_name === "demo_fixture" && <span className="fm-label">来源 · 规则辅助</span>}
          {structured.known.length ? (
            <dl className="gis-data">
              {structured.known.map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.displayValue}</dd></div>)}
            </dl>
          ) : <p>暂无观察条目</p>}
          <details className="gis-disclosure">
            <summary>原始记录与判定依据</summary>
            <pre>{JSON.stringify({
              location: capture.spatial_check_reason,
              observations: raw,
              ruleVersion: compliance?.rule_version,
              expected: compliance?.expected,
              differences: compliance?.differences,
              processingError: job?.error,
            }, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
