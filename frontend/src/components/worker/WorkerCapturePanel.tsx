import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  ChevronDown,
  CloudUpload,
  Crosshair,
  FileImage,
  LoaderCircle,
  MapPin,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Upload,
  WifiOff,
} from "lucide-react";
import {
  AnalyzerName,
  ApiRequestError,
  ComplianceEvaluation,
  EvidenceCapture,
  LocationSource,
  VerificationDetail,
  WorkOrder,
  api,
} from "../../lib/api";
import { labelJobStatus, labelLocationSource, labelSpatialStatus } from "../../lib/productCopy";
import {
  classifyWorkerError,
  distanceToGeometryMeters,
  spatialEvidenceEligibility,
  workerCanSubmitEvidence,
} from "../../lib/workerDomain";
import { useWorkerIdentity } from "../../lib/workerIdentity";
import {
  createWorkerDraftId,
  deleteWorkerEvidenceDraft,
  listWorkerEvidenceDrafts,
  offlineDraftsSupported,
  saveWorkerEvidenceDraft,
  WorkerEvidenceDraft,
  WorkerEvidenceDraftState,
} from "../../lib/workerOffline";
import { cn } from "../../utils/cn";
import { EvidencePreview } from "../ui/EvidencePreview";

type Props = {
  workOrder: WorkOrder;
  onWorkOrderUpdated?: (workOrder: WorkOrder) => void;
};

type CaptureBundle = {
  capture: EvidenceCapture;
  detail: VerificationDetail | null;
  compliance: ComplianceEvaluation | null;
};

type PositionState = {
  latitude: number;
  longitude: number;
  accuracyM: number | null;
  source: LocationSource;
  capturedAt: string;
};

const MAX_EVIDENCE_BYTES = 500 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function captureResult(bundle: CaptureBundle): {
  label: string;
  tone: string;
  action: string;
} {
  const jobStatus = bundle.detail?.job.status;
  const verdict = bundle.compliance?.verdict;
  if (jobStatus === "approved") {
    return { label: "已通过", tone: "border-emerald-200 bg-emerald-50 text-emerald-900", action: "无需继续操作" };
  }
  if (verdict === "deviation_detected") {
    return { label: "存在偏差", tone: "border-rose-200 bg-rose-50 text-rose-900", action: "查看整改要求并提交新的整改记录" };
  }
  if (verdict === "insufficient_evidence" || jobStatus === "rejected") {
    return { label: "待补充资料", tone: "border-orange-200 bg-orange-50 text-orange-900", action: "按缺失项补拍，原记录将继续保留" };
  }
  if (jobStatus === "failed") {
    return { label: "处理失败", tone: "border-rose-200 bg-rose-50 text-rose-900", action: "查看失败原因，确认任务状态后再提交" };
  }
  if (jobStatus === "queued" || jobStatus === "running" || jobStatus === "sealing") {
    return { label: "核验中", tone: "border-sky-200 bg-sky-50 text-sky-900", action: "等待服务端完成处理" };
  }
  if (jobStatus === "needs_review") {
    return { label: "待复核", tone: "border-amber-200 bg-amber-50 text-amber-900", action: "等待管理人员复核" };
  }
  return { label: "已提交", tone: "border-slate-200 bg-slate-50 text-slate-800", action: "等待创建核验任务" };
}

function StepHeading({ step, title, description }: { step: number; title: string; description: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-950 text-sm font-bold text-white">{step}</span>
      <div>
        <h3 className="text-base font-semibold text-slate-950">{title}</h3>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      </div>
    </div>
  );
}

export const WorkerCapturePanel: React.FC<Props> = ({ workOrder, onWorkOrderUpdated }) => {
  const { profile } = useWorkerIdentity();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [position, setPosition] = useState<PositionState | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualLatitude, setManualLatitude] = useState("");
  const [manualLongitude, setManualLongitude] = useState("");
  const [manualAccuracy, setManualAccuracy] = useState("");
  const [locating, setLocating] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [abnormalRemarks, setAbnormalRemarks] = useState("");
  const [safetyState, setSafetyState] = useState<"safe" | "risk" | "paused">("safe");
  const [analyzer, setAnalyzer] = useState<AnalyzerName>("demo_fixture");
  const [uploading, setUploading] = useState(false);
  const [uploadStage, setUploadStage] = useState("");
  const [message, setMessage] = useState<{ tone: "info" | "success" | "error"; text: string } | null>(null);
  const [bundles, setBundles] = useState<CaptureBundle[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [selectedCaptureId, setSelectedCaptureId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<WorkerEvidenceDraft[]>([]);

  const reloadDrafts = useCallback(async () => {
    if (!profile.id || !offlineDraftsSupported()) return;
    try {
      setDrafts(await listWorkerEvidenceDrafts(workOrder.id, profile.id));
    } catch {
      setDrafts([]);
    }
  }, [profile.id, workOrder.id]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const captures = await api.listWorkOrderCaptures(workOrder.id);
      const next = await Promise.all(captures.map(async (capture): Promise<CaptureBundle> => {
        if (!capture.verification_job_id) return { capture, detail: null, compliance: null };
        const [detailResult, complianceResult] = await Promise.allSettled([
          api.verification(capture.verification_job_id),
          api.getCompliance(capture.verification_job_id),
        ]);
        return {
          capture,
          detail: detailResult.status === "fulfilled" ? detailResult.value : null,
          compliance: complianceResult.status === "fulfilled" ? complianceResult.value : null,
        };
      }));
      setBundles(next);
      setSelectedCaptureId((current) => current && next.some((row) => row.capture.id === current)
        ? current
        : next[0]?.capture.id ?? null);
    } catch (reason) {
      const error = classifyWorkerError(reason);
      setMessage({ tone: "error", text: `${error.title}：${error.message}` });
    } finally {
      setHistoryLoading(false);
    }
  }, [workOrder.id]);

  useEffect(() => {
    void loadHistory();
    void reloadDrafts();
  }, [loadHistory, reloadDrafts]);

  useEffect(() => {
    let active = true;
    api.meta().then((meta) => {
      if (!active) return;
      const candidates = Object.entries(meta.adapters)
        .filter(([, descriptor]) => descriptor.enabled)
        .map(([name, descriptor]) => ({ name: name as AnalyzerName, synthetic: descriptor.synthetic }));
      const selected = candidates.find((item) => !item.synthetic && item.name === "remote_http")
        || candidates.find((item) => item.name === "demo_fixture")
        || candidates.find((item) => item.name === "stub");
      if (selected) setAnalyzer(selected.name);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const targetDistanceM = useMemo(() => position
    ? distanceToGeometryMeters(
        position.latitude,
        position.longitude,
        workOrder.geometry_snapshot.geometry_wgs84,
      )
    : null, [position, workOrder.geometry_snapshot.geometry_wgs84]);

  const eligibility = useMemo(() => spatialEvidenceEligibility({
    locationSource: position?.source || "unknown",
    synthetic: false,
    distanceM: targetDistanceM,
    toleranceM: workOrder.spatial_tolerance_m,
    accuracyM: position?.accuracyM ?? null,
    accuracyThresholdM: workOrder.gps_accuracy_threshold_m,
  }), [position, targetDistanceM, workOrder.gps_accuracy_threshold_m, workOrder.spatial_tolerance_m]);

  const requestLocation = () => {
    if (!navigator.geolocation) {
      setManualOpen(true);
      setMessage({ tone: "error", text: "当前设备不支持自动定位，可改用手工位置；手工位置无法作为正式空间核验证据。" });
      return;
    }
    setLocating(true);
    setMessage({ tone: "info", text: "正在获取高精度定位…" });
    navigator.geolocation.getCurrentPosition(
      (result) => {
        setPosition({
          latitude: result.coords.latitude,
          longitude: result.coords.longitude,
          accuracyM: Number.isFinite(result.coords.accuracy) ? result.coords.accuracy : null,
          source: "device_gps",
          capturedAt: new Date(result.timestamp).toISOString(),
        });
        setManualOpen(false);
        setLocating(false);
        setMessage({ tone: "success", text: "已取得设备定位，请核对精度与目标距离。" });
      },
      () => {
        setLocating(false);
        setManualOpen(true);
        setMessage({ tone: "error", text: "定位失败，请检查系统权限或改用手工位置。手工位置无法作为正式空间核验证据。" });
      },
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 0 },
    );
  };

  const applyManualPosition = () => {
    const latitude = Number(manualLatitude);
    const longitude = Number(manualLongitude);
    const accuracy = manualAccuracy.trim() ? Number(manualAccuracy) : null;
    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90
      || !Number.isFinite(longitude) || longitude < -180 || longitude > 180
      || (accuracy != null && (!Number.isFinite(accuracy) || accuracy < 0))) {
      setMessage({ tone: "error", text: "手工位置格式不正确，请检查经纬度和精度。" });
      return;
    }
    setPosition({
      latitude,
      longitude,
      accuracyM: accuracy,
      source: "manual",
      capturedAt: new Date().toISOString(),
    });
    setMessage({ tone: "info", text: "已记录手工位置。无法作为正式空间核验证据。" });
  };

  const chooseFile = (next: File | null) => {
    if (!next) return;
    if (next.size > MAX_EVIDENCE_BYTES) {
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setMessage({ tone: "error", text: `文件大小为 ${formatBytes(next.size)}，超过 500 MB 上限，请压缩后重试。` });
      return;
    }
    if (!/^(image\/(jpeg|png)|video\/(mp4|webm))$/i.test(next.type)) {
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setMessage({ tone: "error", text: "仅支持 JPG、PNG、MP4 或 WebM 文件。" });
      return;
    }
    setFile(next);
    setMessage(null);
  };

  const makeDraft = (state: WorkerEvidenceDraftState): WorkerEvidenceDraft | null => {
    if (!file) return null;
    return {
      id: createWorkerDraftId(),
      workOrderId: workOrder.id,
      workerId: profile.id,
      createdAt: new Date().toISOString(),
      deviceId: navigator.userAgent.slice(0, 160),
      file,
      fileName: file.name,
      fileType: file.type,
      fileLastModified: file.lastModified,
      analyzer,
      latitude: position?.latitude ?? null,
      longitude: position?.longitude ?? null,
      accuracyM: position?.accuracyM ?? null,
      locationSource: position?.source ?? "unknown",
      synthetic: false,
      clientCapturedAt: position?.capturedAt ?? new Date().toISOString(),
      notes,
      abnormalRemarks,
      safetyState,
      state,
      lastError: state === "attention" ? "上次上传连接中断，服务端接收结果未知" : null,
    };
  };

  const saveCurrentDraft = async (state: WorkerEvidenceDraftState = "pending") => {
    const draft = makeDraft(state);
    if (!draft) {
      setMessage({ tone: "error", text: "请先选择现场照片或视频。" });
      return;
    }
    try {
      await saveWorkerEvidenceDraft(draft);
      await reloadDrafts();
      setMessage({ tone: "success", text: state === "attention" ? "已保存为待确认草稿，重试前请先刷新提交记录。" : "草稿已保存在当前设备，联网后会自动同步。" });
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (reason) {
      const text = reason instanceof Error && /quota/i.test(reason.name)
        ? "设备存储空间不足，无法保存草稿。"
        : "离线草稿保存失败，请保留原始文件并检查浏览器存储权限。";
      setMessage({ tone: "error", text });
    }
  };

  const uploadPayload = async (source: WorkerEvidenceDraft | null = null) => {
    const selectedFile = source
      ? new File([source.file], source.fileName, { type: source.fileType, lastModified: source.fileLastModified })
      : file;
    if (!selectedFile) throw new Error("请先选择现场资料");
    const currentPosition = source ? {
      latitude: source.latitude,
      longitude: source.longitude,
      accuracyM: source.accuracyM,
      source: source.locationSource,
      capturedAt: source.clientCapturedAt,
    } : position;
    return api.uploadWorkOrderVerification({
      workOrderId: workOrder.id,
      file: selectedFile,
      analyzer: source?.analyzer ?? analyzer,
      latitude: currentPosition?.latitude ?? null,
      longitude: currentPosition?.longitude ?? null,
      accuracy_m: currentPosition?.accuracyM ?? null,
      location_source: currentPosition?.source ?? "unknown",
      is_synthetic_location: false,
      client_captured_at: currentPosition?.capturedAt ?? new Date().toISOString(),
      device_id: source?.deviceId ?? navigator.userAgent.slice(0, 160),
      metadata: {
        source: "worker-field-capture",
        worker_id: profile.id,
        local_created_at: source?.createdAt ?? new Date().toISOString(),
        notes: source?.notes ?? notes,
        abnormal_remarks: source?.abnormalRemarks ?? abnormalRemarks,
        safety_state: source?.safetyState ?? safetyState,
        sync_state: source ? "offline_draft_sync" : "direct_upload",
      },
    });
  };

  const submit = async () => {
    if (!file) {
      setMessage({ tone: "error", text: "请先选择现场照片或视频。" });
      return;
    }
    if (!workerCanSubmitEvidence(workOrder.status)) {
      setMessage({ tone: "error", text: "当前工单状态不接受新资料，请刷新任务或联系项目负责人。" });
      return;
    }
    if (!navigator.onLine) {
      await saveCurrentDraft("pending");
      return;
    }
    setUploading(true);
    setUploadStage("正在上传原始资料，请保持页面开启…");
    setMessage(null);
    try {
      const result = await uploadPayload();
      setUploadStage("服务端已接收，正在刷新提交记录…");
      onWorkOrderUpdated?.(result.work_order);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setNotes("");
      setAbnormalRemarks("");
      setMessage({ tone: "success", text: `资料已提交，空间核验：${labelSpatialStatus(result.capture.spatial_check_status)}。` });
      await loadHistory();
    } catch (reason) {
      const error = classifyWorkerError(reason);
      const networkUnknown = reason instanceof ApiRequestError && reason.status == null;
      setMessage({
        tone: "error",
        text: networkUnknown
          ? "上传连接中断，服务端接收结果未知。请先刷新提交记录；确认未收到后再保存待确认草稿。"
          : `${error.title}：${error.message}`,
      });
    } finally {
      setUploading(false);
      setUploadStage("");
    }
  };

  const syncDraft = useCallback(async (draft: WorkerEvidenceDraft, confirmAttention: boolean) => {
    if (!navigator.onLine || uploading) return;
    if (draft.state === "attention" && !confirmAttention) return;
    const syncing = { ...draft, state: "syncing" as const, lastError: null };
    await saveWorkerEvidenceDraft(syncing);
    await reloadDrafts();
    setUploading(true);
    setUploadStage(`正在同步 ${draft.fileName}…`);
    try {
      const result = await uploadPayload(draft);
      await deleteWorkerEvidenceDraft(draft.id);
      onWorkOrderUpdated?.(result.work_order);
      setMessage({ tone: "success", text: `离线草稿已同步，空间核验：${labelSpatialStatus(result.capture.spatial_check_status)}。` });
      await Promise.all([reloadDrafts(), loadHistory()]);
    } catch (reason) {
      const error = classifyWorkerError(reason);
      await saveWorkerEvidenceDraft({
        ...draft,
        state: "attention",
        lastError: `${error.title}：${error.message}`,
      });
      await reloadDrafts();
      setMessage({ tone: "error", text: "草稿同步未确认成功，已停止自动重试，避免产生重复记录。" });
    } finally {
      setUploading(false);
      setUploadStage("");
    }
  }, [loadHistory, onWorkOrderUpdated, reloadDrafts, uploading]);

  useEffect(() => {
    const syncPending = () => {
      void listWorkerEvidenceDrafts(workOrder.id, profile.id).then((rows) => {
        const pending = rows.find((row) => row.state === "pending");
        if (pending) void syncDraft(pending, false);
      });
    };
    window.addEventListener("online", syncPending);
    if (navigator.onLine) syncPending();
    return () => window.removeEventListener("online", syncPending);
  }, [profile.id, syncDraft, workOrder.id]);

  const selectedBundle = bundles.find((row) => row.capture.id === selectedCaptureId) ?? null;
  const canSubmit = workerCanSubmitEvidence(workOrder.status);

  return (
    <div className="space-y-6">
      {!canSubmit ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950" role="alert">
          当前状态不接受新资料。历史证据仍可查看；如需补充，请联系项目负责人将工单转入可提交状态。
        </div>
      ) : null}

      {message ? (
        <div className={cn(
          "rounded-lg border px-4 py-3 text-sm",
          message.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-900",
          message.tone === "info" && "border-sky-200 bg-sky-50 text-sky-900",
          message.tone === "error" && "border-rose-200 bg-rose-50 text-rose-900",
        )} role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </div>
      ) : null}

      {uploading ? (
        <div className="flex items-center gap-3 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900" role="status">
          <LoaderCircle className="h-5 w-5 shrink-0 animate-spin" />
          <span>{uploadStage || "正在处理…"}</span>
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <StepHeading step={1} title="确认现场位置" description="优先使用设备高精度定位，距离和精度必须同时满足冻结要求。" />
        <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]">
          <div className={cn(
            "rounded-lg border p-4",
            position
              ? eligibility.formal ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"
              : "border-slate-200 bg-slate-50",
          )}>
            {position ? (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs text-slate-500">当前位置</p>
                    <p className="mt-1 font-mono text-sm font-semibold text-slate-900">
                      {position.latitude.toFixed(6)}, {position.longitude.toFixed(6)}
                    </p>
                  </div>
                  {eligibility.formal ? <ShieldCheck className="h-5 w-5 text-emerald-700" /> : <ShieldAlert className="h-5 w-5 text-amber-700" />}
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                  <div><dt className="text-slate-500">目标距离</dt><dd className="mt-1 font-semibold">{targetDistanceM == null ? "无法估算" : `${targetDistanceM.toFixed(1)} 米 / 容差 ${workOrder.spatial_tolerance_m} 米`}</dd></div>
                  <div><dt className="text-slate-500">定位精度</dt><dd className="mt-1 font-semibold">{position.accuracyM == null ? "未知" : `${position.accuracyM.toFixed(1)} 米 / 要求 ≤ ${workOrder.gps_accuracy_threshold_m} 米`}</dd></div>
                </dl>
                <p className={cn("mt-3 text-xs font-medium", eligibility.formal ? "text-emerald-800" : "text-amber-900")}>{eligibility.message}</p>
              </>
            ) : (
              <div className="flex items-center gap-3 text-sm text-slate-600"><MapPin className="h-5 w-5" /> 尚未获取现场位置</div>
            )}
          </div>
          <button
            type="button"
            onClick={requestLocation}
            disabled={locating || uploading}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
          >
            <Crosshair className={cn("h-4 w-4", locating && "animate-spin")} />
            {position ? "重新定位" : "获取定位"}
          </button>
        </div>
        <button
          type="button"
          onClick={() => setManualOpen((value) => !value)}
          className="mt-3 inline-flex min-h-9 items-center gap-1 text-xs font-medium text-slate-600 underline"
        >
          定位不可用时手工记录 <ChevronDown className={cn("h-3.5 w-3.5", manualOpen && "rotate-180")} />
        </button>
        {manualOpen ? (
          <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <p className="text-xs font-semibold text-amber-900">手工位置无法作为正式空间核验证据</p>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <input value={manualLatitude} onChange={(event) => setManualLatitude(event.target.value)} inputMode="decimal" placeholder="纬度" className="min-h-11 rounded-lg border border-amber-300 bg-white px-3 text-sm" />
              <input value={manualLongitude} onChange={(event) => setManualLongitude(event.target.value)} inputMode="decimal" placeholder="经度" className="min-h-11 rounded-lg border border-amber-300 bg-white px-3 text-sm" />
              <input value={manualAccuracy} onChange={(event) => setManualAccuracy(event.target.value)} inputMode="decimal" placeholder="估计精度（米）" className="min-h-11 rounded-lg border border-amber-300 bg-white px-3 text-sm" />
            </div>
            <button type="button" onClick={applyManualPosition} className="mt-3 min-h-10 rounded-lg border border-amber-400 bg-white px-3 text-sm font-semibold text-amber-950">记录手工位置</button>
          </div>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <StepHeading step={2} title="拍摄并填写说明" description="照片或视频保留原文件；异常和安全状态将随本次记录一并提交。" />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,video/mp4,video/webm"
          capture="environment"
          className="sr-only"
          onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
        />
        {file && previewUrl ? (
          <div className="mt-4 overflow-hidden rounded-lg border border-slate-200 bg-slate-950">
            {file.type.startsWith("image/") ? (
              <img src={previewUrl} alt="待提交现场照片预览" className="max-h-80 w-full object-contain" />
            ) : (
              <video src={previewUrl} aria-label="待提交现场视频预览" controls playsInline className="max-h-80 w-full" />
            )}
            <div className="flex items-center justify-between gap-3 bg-white px-3 py-2 text-xs">
              <span className="min-w-0 truncate font-medium text-slate-800">{file.name} · {formatBytes(file.size)}</span>
              <button type="button" onClick={() => { setFile(null); if (fileInputRef.current) fileInputRef.current.value = ""; }} title="移除文件" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-rose-700 hover:bg-rose-50"><Trash2 className="h-4 w-4" /></button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="mt-4 flex min-h-28 w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-400 bg-slate-50 px-4 text-sm font-semibold text-slate-800 hover:border-slate-700"
          >
            <Camera className="h-6 w-6" />
            拍照或选择现场文件
            <span className="text-xs font-normal text-slate-500">JPG、PNG、MP4、WebM，最大 500 MB</span>
          </button>
        )}

        <div className="mt-4">
          <p className="text-sm font-medium text-slate-800">现场安全状态</p>
          <div className="mt-2 grid grid-cols-3 gap-2" role="radiogroup" aria-label="现场安全状态">
            {([
              ["safe", "正常", ShieldCheck],
              ["risk", "发现风险", AlertTriangle],
              ["paused", "已停工", ShieldAlert],
            ] as const).map(([value, label, Icon]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={safetyState === value}
                onClick={() => setSafetyState(value)}
                className={cn(
                  "flex min-h-12 min-w-0 items-center justify-center gap-1.5 rounded-lg border px-2 text-xs font-semibold sm:text-sm",
                  safetyState === value ? "border-slate-950 bg-slate-950 text-white" : "border-slate-300 bg-white text-slate-700",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" /> <span className="truncate">{label}</span>
              </button>
            ))}
          </div>
        </div>
        <label className="mt-4 block">
          <span className="text-sm font-medium text-slate-800">施工说明</span>
          <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={500} placeholder="说明本次施工内容和完成情况" className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200" />
        </label>
        <label className="mt-4 block">
          <span className="text-sm font-medium text-slate-800">异常备注</span>
          <textarea value={abnormalRemarks} onChange={(event) => setAbnormalRemarks(event.target.value)} rows={2} maxLength={500} placeholder="无异常可留空；发现异常时说明位置和现象" className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200" />
        </label>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <StepHeading step={3} title="提交现场资料" description="每次提交都会新增记录，不会覆盖历史证据。" />
        <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!file || uploading || !canSubmit}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[#f2c94c] px-5 text-sm font-bold text-slate-950 hover:bg-[#e8bd35] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {navigator.onLine ? <Upload className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
            {navigator.onLine ? "提交现场资料" : "保存离线草稿"}
          </button>
          <button
            type="button"
            onClick={() => void saveCurrentDraft("pending")}
            disabled={!file || uploading || !offlineDraftsSupported()}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 hover:border-slate-600 disabled:opacity-50"
          >
            <Save className="h-4 w-4" /> 仅保存草稿
          </button>
        </div>
        {message?.tone === "error" && file ? (
          <button type="button" onClick={() => void saveCurrentDraft("attention")} className="mt-3 inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-rose-800 underline">
            <Save className="h-4 w-4" /> 保存为待确认草稿
          </button>
        ) : null}
      </section>

      {drafts.length ? (
        <section className="border-t border-slate-200 pt-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold">待同步草稿</h3>
              <p className="mt-1 text-xs text-slate-500">保存在当前设备，共 {drafts.length} 条</p>
            </div>
            <CloudUpload className="h-5 w-5 text-slate-500" />
          </div>
          <div className="mt-3 space-y-2">
            {drafts.map((draft) => (
              <div key={draft.id} className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{draft.fileName}</p>
                  <p className="mt-1 text-xs text-slate-500">本地创建 {new Date(draft.createdAt).toLocaleString("zh-CN")} · {draft.state === "attention" ? "需确认" : draft.state === "syncing" ? "同步中" : "待同步"}</p>
                  {draft.lastError ? <p className="mt-1 text-xs text-rose-700">{draft.lastError}</p> : null}
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void syncDraft(draft, true)}
                    disabled={!navigator.onLine || uploading}
                    className="inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg bg-slate-950 px-3 text-xs font-semibold text-white disabled:opacity-50 sm:flex-none"
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> {draft.state === "attention" ? "确认后重试" : "立即同步"}
                  </button>
                  <button type="button" onClick={() => void deleteWorkerEvidenceDraft(draft.id).then(reloadDrafts)} title="删除草稿" className="flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 text-rose-700"><Trash2 className="h-4 w-4" /></button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="border-t border-slate-200 pt-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">提交历史</h3>
            <p className="mt-1 text-xs text-slate-500">历史记录只读；补充资料将新增一条记录</p>
          </div>
          <button type="button" onClick={() => void loadHistory()} disabled={historyLoading} title="刷新提交历史" className="flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-700"><RefreshCw className={cn("h-4 w-4", historyLoading && "animate-spin")} /></button>
        </div>
        {historyLoading && !bundles.length ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-slate-500"><LoaderCircle className="h-4 w-4 animate-spin" /> 正在加载提交记录…</div>
        ) : bundles.length ? (
          <div className="mt-3 space-y-2">
            {bundles.map((bundle, index) => {
              const result = captureResult(bundle);
              const spatial = spatialEvidenceEligibility({
                locationSource: bundle.capture.location_source,
                synthetic: bundle.capture.is_synthetic_location,
                distanceM: bundle.capture.distance_to_target_m,
                toleranceM: bundle.capture.tolerance_m,
                accuracyM: bundle.capture.accuracy_m,
                accuracyThresholdM: bundle.capture.gps_accuracy_threshold_m,
              });
              return (
                <button
                  key={bundle.capture.id}
                  type="button"
                  onClick={() => setSelectedCaptureId(bundle.capture.id)}
                  className={cn(
                    "w-full rounded-lg border bg-white p-3 text-left",
                    selectedCaptureId === bundle.capture.id ? "border-slate-800 ring-2 ring-slate-200" : "border-slate-200",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">第 {bundles.length - index} 次提交</p>
                      <p className="mt-1 text-xs text-slate-500">{new Date(bundle.capture.server_received_at).toLocaleString("zh-CN")}</p>
                    </div>
                    <span className={cn("shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold", result.tone)}>{result.label}</span>
                  </div>
                  <div className="mt-3 grid gap-1 text-xs text-slate-600 sm:grid-cols-2">
                    <p>空间核验：{labelSpatialStatus(bundle.capture.spatial_check_status)}</p>
                    <p>任务进度：{bundle.detail ? labelJobStatus(bundle.detail.job.status) : "等待任务"}</p>
                    <p className="sm:col-span-2">下一步：{result.action}</p>
                    {!spatial.formal ? <p className="font-semibold text-amber-800 sm:col-span-2">{spatial.message}</p> : null}
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-500">暂无提交记录</div>
        )}
      </section>

      {selectedBundle ? (
        <section className="space-y-3 border-t border-slate-200 pt-5">
          <div className="flex items-center gap-2">
            <FileImage className="h-5 w-5 text-slate-600" />
            <h3 className="text-base font-semibold">所选提交详情</h3>
          </div>
          <EvidencePreview
            evidenceId={selectedBundle.capture.evidence_id}
            originalName={selectedBundle.detail?.evidence.original_name}
            sha256={selectedBundle.detail?.evidence.sha256}
            registeredSizeBytes={selectedBundle.detail?.evidence.size_bytes}
          />
          {selectedBundle.compliance ? (
            <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
              <p className="font-semibold">空间核验与缺失资料说明</p>
              <p className="mt-2 leading-6 text-slate-700">{selectedBundle.compliance.notes || selectedBundle.capture.spatial_check_reason || "服务端未返回补充说明。"}</p>
            </div>
          ) : null}
          {selectedBundle.detail?.job.error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
              处理失败：{selectedBundle.detail.job.error}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
};

