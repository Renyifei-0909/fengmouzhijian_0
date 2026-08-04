import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import {
  api,
  AnalyzerName,
  ApiRequestError,
  ComplianceEvaluation,
  EvidenceCapture,
  LocationSource,
  VerificationDetail,
  WorkOrder,
} from "../../lib/api";
import { Notice } from "../ui/Notice";
import { cn } from "../../utils/cn";
import { InfoIcon } from "../Icons";
import {
  COMPLIANCE_BUSINESS_NOTE,
  COPY,
  friendlyApiError,
  labelLocationSource,
  labelSpatialStatus,
  labelWorkOrderStatus,
} from "../../lib/productCopy";
import {
  groupDifferenceRows,
  labelHumanReviewStatus,
  labelRuleVerdict,
  labelTaskProgress,
  pickDefaultCaptureId,
  spatialBusinessSummary,
  structureObservations,
} from "../../lib/verificationDisplay";

const spatialTone: Record<string, string> = {
  passed: "border-emerald-300 bg-emerald-50 text-emerald-950",
  failed: "border-rose-300 bg-rose-50 text-rose-950",
  unavailable: "border-amber-300 bg-amber-50 text-amber-950",
  skipped: "border-slate-300 bg-slate-50 text-slate-800",
};

const verdictTone: Record<string, string> = {
  compliant: "border-emerald-300 bg-emerald-50 text-emerald-950",
  deviation_detected: "border-rose-300 bg-rose-50 text-rose-950",
  insufficient_evidence: "border-amber-300 bg-amber-50 text-amber-950",
  needs_review: "border-amber-300 bg-amber-50 text-amber-950",
};

type Props = {
  workOrder: WorkOrder;
  onWorkOrderUpdated?: (wo: WorkOrder) => void;
  onCaptureMarkerChange?: (
    marker: { longitude: number; latitude: number; synthetic: boolean } | null,
  ) => void;
};

type HistoryMeta = {
  jobStatus: string | null;
  verdict: string | null;
  loading: boolean;
};

export const WorkOrderCapturePanel: React.FC<Props> = ({
  workOrder,
  onWorkOrderUpdated,
  onCaptureMarkerChange,
}) => {
  const [captures, setCaptures] = useState<EvidenceCapture[]>([]);
  const [selectedCaptureId, setSelectedCaptureId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VerificationDetail | null>(null);
  const [compliance, setCompliance] = useState<ComplianceEvaluation | null>(null);
  const [historyMeta, setHistoryMeta] = useState<Record<string, HistoryMeta>>({});
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const [locating, setLocating] = useState(false);
  const [polling, setPolling] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [file, setFile] = useState<File | null>(null);
  const [clientCapturedAt, setClientCapturedAt] = useState(() =>
    new Date().toISOString().slice(0, 16),
  );
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [accuracyM, setAccuracyM] = useState("");
  const [locationSource, setLocationSource] = useState<LocationSource>("synthetic_demo");
  const [syntheticDemo, setSyntheticDemo] = useState(true);
  const [deviceId, setDeviceId] = useState("WEB-WORK-ORDER");
  const [metadataText, setMetadataText] = useState(
    '{"source":"engineering-workbench","privacy":"user-provided"}',
  );
  const [analyzer, setAnalyzer] = useState<AnalyzerName>("demo_fixture");

  const selectionSeq = useRef(0);
  const selectedCaptureIdRef = useRef<string | null>(null);
  selectedCaptureIdRef.current = selectedCaptureId;

  const setFriendlyError = (raw: string) => {
    setError(friendlyApiError(raw));
    setErrorDetail(raw);
  };

  const loadCaptureDetails = useCallback(
    async (capture: EvidenceCapture, seq: number) => {
      const jobId = capture.verification_job_id;
      if (!jobId) {
        if (seq === selectionSeq.current) {
          setDetail(null);
          setCompliance(null);
          setSelectionLoading(false);
          setHistoryMeta((prev) => ({
            ...prev,
            [capture.id]: { jobStatus: null, verdict: null, loading: false },
          }));
        }
        return;
      }

      setHistoryMeta((prev) => ({
        ...prev,
        [capture.id]: {
          jobStatus: prev[capture.id]?.jobStatus ?? null,
          verdict: prev[capture.id]?.verdict ?? null,
          loading: true,
        },
      }));

      let nextDetail: VerificationDetail | null = null;
      let nextCompliance: ComplianceEvaluation | null = null;
      let jobStatus: string | null = null;
      let verdict: string | null = null;

      try {
        nextDetail = await api.verification(jobId);
        jobStatus = nextDetail.job.status;
        try {
          nextCompliance = await api.getCompliance(jobId);
          verdict = nextCompliance.verdict;
        } catch {
          nextCompliance = null;
          verdict = null;
        }
      } catch {
        nextDetail = null;
        nextCompliance = null;
      }

      if (seq !== selectionSeq.current) {
        // Stale request: still update history meta for list badges, not main detail.
        setHistoryMeta((prev) => ({
          ...prev,
          [capture.id]: { jobStatus, verdict, loading: false },
        }));
        return;
      }

      setDetail(nextDetail);
      setCompliance(nextCompliance);
      setSelectionLoading(false);
      setHistoryMeta((prev) => ({
        ...prev,
        [capture.id]: { jobStatus, verdict, loading: false },
      }));
    },
    [],
  );

  const selectCapture = useCallback(
    (captureId: string) => {
      const capture = captures.find((c) => c.id === captureId);
      if (!capture) return;
      const seq = ++selectionSeq.current;
      setSelectedCaptureId(captureId);
      setSelectionLoading(true);
      setDetail(null);
      setCompliance(null);
      void loadCaptureDetails(capture, seq);
    },
    [captures, loadCaptureDetails],
  );

  const refreshCaptures = useCallback(
    async (preferCaptureId?: string | null) => {
      try {
        const rows = await api.listWorkOrderCaptures(workOrder.id);
        setCaptures(rows);
        const nextId =
          preferCaptureId && rows.some((r) => r.id === preferCaptureId)
            ? preferCaptureId
            : selectedCaptureIdRef.current && rows.some((r) => r.id === selectedCaptureIdRef.current)
              ? selectedCaptureIdRef.current
              : pickDefaultCaptureId(rows);
        if (nextId) {
          const capture = rows.find((r) => r.id === nextId);
          if (capture) {
            const seq = ++selectionSeq.current;
            setSelectedCaptureId(nextId);
            setSelectionLoading(true);
            setDetail(null);
            setCompliance(null);
            void loadCaptureDetails(capture, seq);
          }
        } else {
          setSelectedCaptureId(null);
          setDetail(null);
          setCompliance(null);
        }
        // Prefetch meta for list rows (status/verdict badges)
        for (const cap of rows) {
          if (!cap.verification_job_id) {
            setHistoryMeta((prev) => ({
              ...prev,
              [cap.id]: { jobStatus: null, verdict: null, loading: false },
            }));
            continue;
          }
          void (async () => {
            try {
              const d = await api.verification(cap.verification_job_id!);
              let verdict: string | null = null;
              try {
                const c = await api.getCompliance(cap.verification_job_id!);
                verdict = c.verdict;
              } catch {
                verdict = null;
              }
              setHistoryMeta((prev) => ({
                ...prev,
                [cap.id]: { jobStatus: d.job.status, verdict, loading: false },
              }));
            } catch {
              setHistoryMeta((prev) => ({
                ...prev,
                [cap.id]: { jobStatus: null, verdict: null, loading: false },
              }));
            }
          })();
        }
      } catch (err) {
        setFriendlyError(err instanceof ApiRequestError ? err.message : "无法加载核验记录");
      }
    },
    [workOrder.id, loadCaptureDetails],
  );

  useEffect(() => {
    void refreshCaptures();
  }, [refreshCaptures]);

  useEffect(() => {
    if (syntheticDemo) setLocationSource("synthetic_demo");
  }, [syntheticDemo]);

  useEffect(() => {
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (Number.isFinite(lat) && Number.isFinite(lon) && latitude !== "" && longitude !== "") {
      onCaptureMarkerChange?.({
        longitude: lon,
        latitude: lat,
        synthetic: syntheticDemo || locationSource === "synthetic_demo",
      });
    } else {
      onCaptureMarkerChange?.(null);
    }
  }, [latitude, longitude, syntheticDemo, locationSource, onCaptureMarkerChange]);

  // Poll only the currently selected capture's job.
  useEffect(() => {
    if (!detail || !selectedCaptureId) return;
    const selected = captures.find((c) => c.id === selectedCaptureId);
    if (!selected?.verification_job_id || selected.verification_job_id !== detail.job.id) {
      setPolling(false);
      return;
    }
    if (!["queued", "running"].includes(detail.job.status)) {
      setPolling(false);
      return;
    }
    setPolling(true);
    const captureIdAtStart = selectedCaptureId;
    const jobId = detail.job.id;
    const seqAtStart = selectionSeq.current;
    const timer = window.setTimeout(async () => {
      if (selectionSeq.current !== seqAtStart || selectedCaptureIdRef.current !== captureIdAtStart) {
        return;
      }
      try {
        const d = await api.verification(jobId);
        if (selectionSeq.current !== seqAtStart || selectedCaptureIdRef.current !== captureIdAtStart) {
          return;
        }
        setDetail(d);
        if (!["queued", "running"].includes(d.job.status)) {
          let nextComp: ComplianceEvaluation | null = null;
          try {
            nextComp = await api.getCompliance(jobId);
          } catch {
            nextComp = null;
          }
          if (selectionSeq.current !== seqAtStart || selectedCaptureIdRef.current !== captureIdAtStart) {
            return;
          }
          setCompliance(nextComp);
          setHistoryMeta((prev) => ({
            ...prev,
            [captureIdAtStart]: {
              jobStatus: d.job.status,
              verdict: nextComp?.verdict ?? null,
              loading: false,
            },
          }));
          const wo = await api.getWorkOrder(workOrder.id);
          onWorkOrderUpdated?.(wo);
        }
      } catch {
        /* keep last state */
      }
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [detail, selectedCaptureId, captures, workOrder.id, onWorkOrderUpdated]);

  const requestBrowserLocation = () => {
    if (!navigator.geolocation) {
      setFriendlyError("当前设备不支持自动定位，请手工填写位置。");
      setLocationSource("manual");
      setSyntheticDemo(false);
      return;
    }
    setLocating(true);
    setError(null);
    setErrorDetail(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLatitude(String(pos.coords.latitude));
        setLongitude(String(pos.coords.longitude));
        setAccuracyM(
          pos.coords.accuracy != null && Number.isFinite(pos.coords.accuracy)
            ? String(Math.round(pos.coords.accuracy * 10) / 10)
            : "",
        );
        setSyntheticDemo(false);
        setLocationSource("device_gps");
        setClientCapturedAt(new Date(pos.timestamp).toISOString().slice(0, 16));
        setNotice(
          pos.coords.accuracy != null
            ? `已获取设备定位，精度约 ${pos.coords.accuracy.toFixed(1)} ${COPY.unitMeters}`
            : "已获取设备定位",
        );
        setLocating(false);
      },
      (geoErr) => {
        setFriendlyError(`定位失败：${geoErr.message}。请手工填写位置。`);
        setLocationSource("manual");
        setSyntheticDemo(false);
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 },
    );
  };

  const onUpload = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) {
      setFriendlyError(`请选择${COPY.fieldMedia}`);
      return;
    }
    const lat = latitude.trim() === "" ? null : Number(latitude);
    const lon = longitude.trim() === "" ? null : Number(longitude);
    const acc = accuracyM.trim() === "" ? null : Number(accuracyM);
    if (lat != null && !Number.isFinite(lat)) {
      setFriendlyError("纬度须为有效数字");
      return;
    }
    if (lon != null && !Number.isFinite(lon)) {
      setFriendlyError("经度须为有效数字");
      return;
    }
    if (acc != null && !Number.isFinite(acc)) {
      setFriendlyError("定位精度须为有效数字");
      return;
    }
    let metadata: Record<string, unknown> = {};
    try {
      metadata = JSON.parse(metadataText || "{}") as Record<string, unknown>;
      if (typeof metadata !== "object" || metadata === null || Array.isArray(metadata)) {
        throw new Error("metadata 必须是 JSON 对象");
      }
    } catch (e) {
      setFriendlyError(e instanceof Error ? e.message : "元数据格式无效");
      return;
    }

    const source: LocationSource = syntheticDemo ? "synthetic_demo" : locationSource;
    const capturedIso = clientCapturedAt
      ? new Date(clientCapturedAt).toISOString()
      : new Date().toISOString();

    setUploading(true);
    setError(null);
    setErrorDetail(null);
    setNotice("正在提交核验…");
    try {
      const result = await api.uploadWorkOrderVerification({
        workOrderId: workOrder.id,
        file,
        analyzer,
        latitude: lat,
        longitude: lon,
        accuracy_m: acc,
        location_source: source,
        is_synthetic_location: syntheticDemo || source === "synthetic_demo",
        client_captured_at: capturedIso,
        device_id: deviceId || "WEB-WORK-ORDER",
        metadata: {
          ...metadata,
          synthetic_demo: syntheticDemo || source === "synthetic_demo",
        },
      });
      onWorkOrderUpdated?.(result.work_order);
      setNotice(
        `已提交 · ${labelSpatialStatus(result.capture.spatial_check_status)}` +
          (result.capture.is_synthetic_location ? ` · ${COPY.sampleDataBadge}` : ""),
      );
      setFile(null);
      await refreshCaptures(result.capture.id);
    } catch (err) {
      setFriendlyError(err instanceof ApiRequestError ? err.message : "提交失败");
      setNotice("");
    } finally {
      setUploading(false);
    }
  };

  const selectedCapture = captures.find((c) => c.id === selectedCaptureId) ?? null;

  const observations = useMemo(() => {
    const result = detail?.job.result;
    if (!result || typeof result !== "object") return null;
    const obs = (result as Record<string, unknown>).observations;
    if (!obs || typeof obs !== "object") return null;
    const measurements = (obs as Record<string, unknown>).measurements;
    if (measurements && typeof measurements === "object") {
      return measurements as Record<string, unknown>;
    }
    return obs as Record<string, unknown>;
  }, [detail]);

  const structuredObs = useMemo(() => structureObservations(observations), [observations]);

  const isDemoAnalyzer =
    detail?.job.analyzer_name === "demo_fixture" ||
    detail?.job.analyzer_name === "stub" ||
    analyzer === "demo_fixture" ||
    analyzer === "stub";

  const differenceRows = useMemo(
    () => groupDifferenceRows(compliance?.differences),
    [compliance],
  );

  const spatialSummary = selectedCapture
    ? spatialBusinessSummary({
        spatial_check_status: selectedCapture.spatial_check_status,
        latitude: selectedCapture.latitude,
        longitude: selectedCapture.longitude,
        accuracy_m: selectedCapture.accuracy_m,
        gps_accuracy_threshold_m: selectedCapture.gps_accuracy_threshold_m,
        distance_to_target_m: selectedCapture.distance_to_target_m,
        tolerance_m: selectedCapture.tolerance_m,
      })
    : "";

  // Association guard for display: detail/compliance must match selected capture job.
  const selectedJobId = selectedCapture?.verification_job_id ?? null;
  const detailMatches =
    !selectedJobId || (detail != null && detail.job.id === selectedJobId);
  const complianceMatches =
    !selectedJobId ||
    compliance == null ||
    compliance.job_id === selectedJobId;
  const safeDetail = detailMatches ? detail : null;
  const safeCompliance = complianceMatches && detailMatches ? compliance : null;

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs text-slate-500">当前工单</p>
            <p className="text-sm font-semibold text-slate-900">{workOrder.work_order_code}</p>
            <p className="mt-1 text-[11px] text-slate-500">
              {labelWorkOrderStatus(workOrder.status)} · {COPY.fieldSpatialTolerance}{" "}
              {workOrder.spatial_tolerance_m}
              {COPY.unitMeters} · {COPY.fieldGpsAccuracy} {workOrder.gps_accuracy_threshold_m}
              {COPY.unitMeters}
            </p>
          </div>
          <Link
            to={`/work-orders/${workOrder.id}`}
            className="text-xs font-medium text-sky-700 hover:underline"
          >
            {COPY.independentWorkOrderPage}
          </Link>
        </div>
        {(syntheticDemo || locationSource === "synthetic_demo") && (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-[11px] font-medium text-violet-900">
            {COPY.sampleDataBadge} · {COPY.captureMode}
          </div>
        )}
      </div>

      {error ? (
        <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          <p>{error}</p>
          {errorDetail && errorDetail !== error ? (
            <details className="mt-1">
              <summary className="cursor-pointer">{COPY.techDetails}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all text-[10px]">{errorDetail}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
      {notice ? <Notice type={uploading || polling ? "info" : "success"} message={notice} /> : null}
      {polling ? <Notice type="info" message="核验进行中，正在更新当前记录…" /> : null}

      <form className="space-y-3" onSubmit={(e) => void onUpload(e)}>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">{COPY.fieldMedia}</span>
          <input
            type="file"
            accept="image/jpeg,image/png,video/mp4,video/webm,.jpg,.jpeg,.png,.mp4,.webm"
            className="mt-1 block w-full text-xs"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>

        <div className="rounded-2xl border border-slate-200 bg-white px-3 py-2">
          <p className="text-xs font-medium text-slate-700">{COPY.captureMode}</p>
          <label className="mt-2 flex cursor-pointer items-start gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={syntheticDemo}
              onChange={(e) => setSyntheticDemo(e.target.checked)}
            />
            <span>
              <strong>{COPY.useSampleLocation}</strong>
              <br />
              <span className="text-slate-500">{COPY.useSampleLocationHelp}</span>
            </span>
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={requestBrowserLocation}
            disabled={locating || syntheticDemo}
            className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-800 disabled:opacity-50"
          >
            {locating ? "定位中…" : "使用设备定位"}
          </button>
          <span className="self-center text-[10px] text-slate-500">失败时可手工填写</span>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-[11px] text-slate-500">纬度</span>
            <input
              value={latitude}
              onChange={(e) => setLatitude(e.target.value)}
              inputMode="decimal"
              className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-slate-500">经度</span>
            <input
              value={longitude}
              onChange={(e) => setLongitude(e.target.value)}
              inputMode="decimal"
              className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-slate-500">
              {COPY.reportedAccuracy}（{COPY.unitMeters}）
            </span>
            <input
              value={accuracyM}
              onChange={(e) => setAccuracyM(e.target.value)}
              inputMode="decimal"
              className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-slate-500">{COPY.captureMode}</span>
            <select
              value={syntheticDemo ? "synthetic_demo" : locationSource}
              disabled={syntheticDemo}
              onChange={(e) => setLocationSource(e.target.value as LocationSource)}
              className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
            >
              <option value="synthetic_demo">{labelLocationSource("synthetic_demo")}</option>
              <option value="device_gps">{labelLocationSource("device_gps")}</option>
              <option value="manual">{labelLocationSource("manual")}</option>
              <option value="unknown">{labelLocationSource("unknown")}</option>
            </select>
          </label>
          <label className="col-span-2 block">
            <span className="text-[11px] text-slate-500">{COPY.captureTime}</span>
            <input
              type="datetime-local"
              value={clientCapturedAt}
              onChange={(e) => setClientCapturedAt(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
            />
          </label>
        </div>

        <div>
          <button
            type="button"
            className="text-[11px] font-medium text-sky-800 underline"
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            {advancedOpen
              ? `收起${COPY.advancedSettings}`
              : `${COPY.advancedSettings} / ${COPY.testConfig}`}
          </button>
          {advancedOpen ? (
            <div className="mt-2 space-y-2 rounded-xl border border-slate-100 bg-slate-50 p-2">
              <p className="text-[10px] text-slate-500">{COPY.analyzerHelp}</p>
              <label className="block text-[11px] text-slate-500">
                设备编号
                <input
                  value={deviceId}
                  onChange={(e) => setDeviceId(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
                />
              </label>
              <label className="block text-[11px] text-slate-500">
                附加说明（JSON）
                <textarea
                  value={metadataText}
                  onChange={(e) => setMetadataText(e.target.value)}
                  rows={2}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 font-mono text-[11px]"
                />
              </label>
              <label className="block text-[11px] text-slate-500">
                测试分析配置
                <select
                  value={analyzer}
                  onChange={(e) => setAnalyzer(e.target.value as AnalyzerName)}
                  className="mt-1 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
                >
                  <option value="demo_fixture">业务流程验证（样例观察）</option>
                  <option value="stub">链路占位（无观察）</option>
                </select>
              </label>
            </div>
          ) : null}
        </div>

        <button
          type="submit"
          disabled={uploading || !file}
          className="w-full rounded-2xl bg-sky-600 px-3 py-2.5 text-sm font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
        >
          {uploading ? "提交中…" : COPY.submitVerification}
        </button>
        <p className="text-[10px] leading-4 text-slate-500">{COPY.spatialDisclaimer}</p>
      </form>

      {captures.length > 0 ? (
        <section className="rounded-2xl border border-slate-200 bg-white p-3">
          <h4 className="text-sm font-semibold text-slate-900">{COPY.verificationHistory}</h4>
          <ul className="mt-2 space-y-2">
            {captures.map((c) => {
              const meta = historyMeta[c.id];
              const selected = c.id === selectedCaptureId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => selectCapture(c.id)}
                    aria-pressed={selected}
                    className={cn(
                      "w-full rounded-xl border px-3 py-2 text-left text-[11px] transition-all",
                      selected
                        ? "border-sky-400 bg-sky-50 ring-1 ring-sky-200"
                        : "border-slate-100 bg-slate-50 hover:border-sky-200",
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-slate-800">
                        {COPY.submittedAt}{" "}
                        {new Date(c.server_received_at).toLocaleString("zh-CN")}
                      </span>
                      {selected ? (
                        <span className="rounded-full bg-sky-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                          当前查看
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">
                      <span>
                        {COPY.locationStatus} {labelSpatialStatus(c.spatial_check_status)}
                      </span>
                      <span>
                        {COPY.ruleVerdictStatus}{" "}
                        {meta?.verdict
                          ? labelRuleVerdict(meta.verdict)
                          : c.verification_job_id
                            ? meta?.loading
                              ? "加载中"
                              : "等待判定"
                            : "—"}
                      </span>
                      <span>
                        {COPY.humanReviewStatus}{" "}
                        {meta?.jobStatus
                          ? labelHumanReviewStatus(meta.jobStatus)
                          : c.verification_job_id
                            ? meta?.loading
                              ? "加载中"
                              : "—"
                            : "等待创建核验任务"}
                      </span>
                      <span>
                        {COPY.captureMode} {labelLocationSource(c.location_source)}
                      </span>
                      {c.is_synthetic_location ? <span>{COPY.sampleDataBadge}</span> : null}
                      {workOrder.assigned_to ? (
                        <span>
                          {COPY.owner} {workOrder.assigned_to}
                        </span>
                      ) : null}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {selectedCapture && !selectedCapture.verification_job_id ? (
        <Notice type="info" message="等待创建核验任务" />
      ) : null}
      {selectionLoading ? <Notice type="info" message="正在加载所选核验记录…" /> : null}

      {selectedCapture ? (
        <section
          className={cn(
            "rounded-2xl border p-3",
            spatialTone[selectedCapture.spatial_check_status] || spatialTone.skipped,
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-semibold">{COPY.spatialCheck}</h4>
            <span className="rounded-full bg-white/80 px-2 py-0.5 text-[11px] font-bold">
              {labelSpatialStatus(selectedCapture.spatial_check_status)}
            </span>
          </div>
          <p className="mt-2 text-xs leading-5 font-medium">{spatialSummary}</p>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
            <div>
              <dt className="opacity-70">{COPY.distanceToObject}</dt>
              <dd className="font-semibold">
                {selectedCapture.distance_to_target_m == null
                  ? "—"
                  : `${selectedCapture.distance_to_target_m.toFixed(1)} ${COPY.unitMeters}`}
              </dd>
            </div>
            <div>
              <dt className="opacity-70">{COPY.allowedRange}</dt>
              <dd className="font-semibold">
                {selectedCapture.tolerance_m} {COPY.unitMeters}
              </dd>
            </div>
            <div>
              <dt className="opacity-70">{COPY.reportedAccuracy}</dt>
              <dd className="font-semibold">
                {selectedCapture.accuracy_m == null
                  ? "—"
                  : `${selectedCapture.accuracy_m} ${COPY.unitMeters}`}
              </dd>
            </div>
            <div>
              <dt className="opacity-70">{COPY.accuracyRequirement}</dt>
              <dd className="font-semibold">
                {selectedCapture.gps_accuracy_threshold_m} {COPY.unitMeters}
              </dd>
            </div>
            <div>
              <dt className="opacity-70">{COPY.captureMode}</dt>
              <dd className="font-semibold">
                {labelLocationSource(selectedCapture.location_source)}
              </dd>
            </div>
            <div>
              <dt className="opacity-70">{COPY.captureTime}</dt>
              <dd className="font-semibold text-[10px]">
                {selectedCapture.client_captured_at
                  ? new Date(selectedCapture.client_captured_at).toLocaleString("zh-CN")
                  : "—"}
              </dd>
            </div>
          </dl>
          <p className="mt-2 text-[10px] opacity-80">{COPY.spatialDisclaimer}</p>
          <details className="mt-2 text-[10px]">
            <summary className="cursor-pointer">{COPY.techDetails}</summary>
            <pre className="mt-1 max-h-28 overflow-auto rounded-lg bg-white/70 p-1.5">
              {JSON.stringify(
                {
                  spatial_check_status: selectedCapture.spatial_check_status,
                  spatial_check_reason: selectedCapture.spatial_check_reason,
                  location_source: selectedCapture.location_source,
                  is_synthetic_location: selectedCapture.is_synthetic_location,
                  server_received_at: selectedCapture.server_received_at,
                  capture_id: selectedCapture.id,
                  evidence_id: selectedCapture.evidence_id,
                  verification_job_id: selectedCapture.verification_job_id,
                  distance_to_target_m: selectedCapture.distance_to_target_m,
                  accuracy_m: selectedCapture.accuracy_m,
                  gps_accuracy_threshold_m: selectedCapture.gps_accuracy_threshold_m,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </section>
      ) : null}

      {safeDetail ? (
        <section className="space-y-2 rounded-2xl border border-slate-200 bg-white p-3">
          <h4 className="text-sm font-semibold text-slate-900">处理进度</h4>
          <dl className="grid grid-cols-1 gap-1.5 text-[11px] sm:grid-cols-3">
            <div className="rounded-lg border border-slate-100 bg-slate-50 px-2 py-1.5">
              <dt className="text-slate-500">{COPY.taskProgress}</dt>
              <dd className="font-semibold text-slate-900">
                {labelTaskProgress(safeDetail.job.status)}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-100 bg-slate-50 px-2 py-1.5">
              <dt className="text-slate-500">{COPY.humanReviewStatus}</dt>
              <dd className="font-semibold text-slate-900">
                {labelHumanReviewStatus(safeDetail.job.status)}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-100 bg-slate-50 px-2 py-1.5">
              <dt className="text-slate-500">{COPY.ruleVerdictStatus}</dt>
              <dd className="font-semibold text-slate-900">
                {safeCompliance
                  ? labelRuleVerdict(safeCompliance.verdict)
                  : "等待判定"}
              </dd>
            </div>
          </dl>
          <p className="text-[10px] leading-4 text-slate-500">
            规则初判由服务端规则引擎产生；人工复核在核验中心完成，二者含义不同。
          </p>
          <Link
            to="/backend-workflow"
            className="inline-flex text-xs font-medium text-sky-700 hover:underline"
          >
            {COPY.goVerificationCenter}
          </Link>
          <details className="text-[10px] text-slate-500">
            <summary className="cursor-pointer">{COPY.techDetails}</summary>
            <p className="mt-1 font-mono break-all">job_id={safeDetail.job.id}</p>
            <p>
              status={safeDetail.job.status} · analyzer={safeDetail.job.analyzer_name} ·
              version={safeDetail.job.analyzer_version}
            </p>
            {safeDetail.job.error ? <p className="text-rose-700">{safeDetail.job.error}</p> : null}
          </details>
        </section>
      ) : selectedCapture?.verification_job_id && !selectionLoading ? (
        <Notice type="info" message="等待判定" />
      ) : null}

      <div className="grid grid-cols-1 gap-3">
        <section className="rounded-2xl border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold text-slate-900">{COPY.observations}</h4>
            {isDemoAnalyzer ? (
              <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-900">
                {COPY.resultSource}：{COPY.testAnalyzer}
              </span>
            ) : null}
          </div>
          {isDemoAnalyzer ? (
            <p className="mt-1 text-[10px] text-slate-500">{COPY.testAnalyzerNote}</p>
          ) : (
            <p className="mt-1 text-[10px] text-slate-500">
              识别结果为观察字段，不直接作为最终合规裁决。
            </p>
          )}
          {structuredObs.known.length ? (
            <dl className="mt-2 space-y-1.5 text-[11px]">
              {structuredObs.known.map((item) => (
                <div
                  key={item.key}
                  className="flex items-start justify-between gap-2 rounded-lg border border-slate-100 bg-slate-50 px-2 py-1.5"
                >
                  <dt className="text-slate-500">{item.label}</dt>
                  <dd className="font-medium text-slate-900">{item.displayValue}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="mt-2 text-xs text-slate-500">（等待核验完成）</p>
          )}
          <details className="mt-2 text-[10px]">
            <summary className="cursor-pointer text-sky-800">
              {COPY.techDetails}（原始识别 JSON）
            </summary>
            <pre className="mt-1 max-h-40 overflow-auto rounded-xl border border-slate-100 bg-slate-50 p-2 text-slate-700">
              {observations ? JSON.stringify(observations, null, 2) : "null"}
            </pre>
            {structuredObs.unknown.length ? (
              <pre className="mt-1 max-h-24 overflow-auto rounded-xl bg-slate-50 p-2">
                {JSON.stringify(
                  Object.fromEntries(structuredObs.unknown.map((u) => [u.key, u.rawValue])),
                  null,
                  2,
                )}
              </pre>
            ) : null}
          </details>
        </section>

        <section
          className={cn(
            "rounded-2xl border p-3",
            safeCompliance
              ? verdictTone[safeCompliance.verdict] || verdictTone.needs_review
              : "border-slate-200 bg-white",
          )}
        >
          <h4 className="text-sm font-semibold">{COPY.complianceJudgement}</h4>
          {safeCompliance ? (
            <>
              <p className="mt-1 text-sm font-bold">
                {COPY.ruleVerdictStatus}：{labelRuleVerdict(safeCompliance.verdict)}
              </p>
              <p className="mt-1 text-[11px]">
                {COPY.ruleVersion} {safeCompliance.rule_version}
                {safeCompliance.spatial_check_status
                  ? ` · ${COPY.locationStatus} ${labelSpatialStatus(safeCompliance.spatial_check_status)}`
                  : ""}
              </p>
              <div className="mt-2 grid gap-1 text-[11px]">
                {differenceRows.ok.length ? (
                  <p>
                    {COPY.compliantItems}：{differenceRows.ok.join("、")}
                  </p>
                ) : null}
                {differenceRows.bad.length ? (
                  <p>
                    {COPY.deviationItems}：{differenceRows.bad.join("、")}
                  </p>
                ) : null}
                {differenceRows.pending.length ? (
                  <p>
                    {COPY.pendingItems}：{differenceRows.pending.join("、")}
                  </p>
                ) : null}
              </div>
              <p className="mt-2 text-[11px] leading-4 opacity-90">{COMPLIANCE_BUSINESS_NOTE}</p>
              <details className="mt-2 text-[10px]">
                <summary className="cursor-pointer">
                  {COPY.judgementBasis} / {COPY.techDetails}
                </summary>
                <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-white/70 p-1.5">
                  {JSON.stringify(
                    {
                      engine_version: safeCompliance.engine_version,
                      rule_version: safeCompliance.rule_version,
                      expected: safeCompliance.expected,
                      observed: safeCompliance.observed,
                      differences: safeCompliance.differences,
                      field_labels: differenceRows.rawFields,
                      notes: safeCompliance.notes,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          ) : selectedCapture?.verification_job_id ? (
            <p className="mt-2 text-xs opacity-70">等待规则初判</p>
          ) : (
            <p className="mt-2 text-xs opacity-70">提交资料并完成分析后显示规则初判。</p>
          )}
        </section>
      </div>

      {selectedCapture ? (
        <details className="rounded-2xl border border-slate-100 bg-slate-50 p-2 text-[10px] text-slate-600">
          <summary className="cursor-pointer font-medium text-sky-800">
            {COPY.techDetails}（当前记录标识）
          </summary>
          <pre className="mt-1 overflow-auto">
            {JSON.stringify(
              {
                capture_id: selectedCapture.id,
                evidence_id: selectedCapture.evidence_id,
                job_id: selectedCapture.verification_job_id,
                analyzer: safeDetail?.job.analyzer_name,
                rule_version: safeCompliance?.rule_version,
                engine_version: safeCompliance?.engine_version,
                distance: selectedCapture.distance_to_target_m,
                accuracy: selectedCapture.accuracy_m,
                gps_threshold: selectedCapture.gps_accuracy_threshold_m,
              },
              null,
              2,
            )}
          </pre>
        </details>
      ) : null}

      <div className="flex items-start gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] leading-4 text-slate-600">
        <InfoIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        人工复核、整改与报告归档请前往核验中心与整改中心处理。
      </div>
    </div>
  );
};
