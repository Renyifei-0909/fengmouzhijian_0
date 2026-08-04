import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiRequestError,
  StandardGpkgImportResult,
  StandardGpkgPreviewResult,
} from "../../lib/api";
import { COPY } from "../../lib/productCopy";
import { cn } from "../../utils/cn";

export type GpkgImportPhase =
  | "idle"
  | "selecting"
  | "uploading"
  | "preview_ready"
  | "confirming"
  | "success"
  | "preview_invalid"
  | "token_expired"
  | "conflict"
  | "network_error";

type Props = {
  projectId: string;
  disabled?: boolean;
  onImported?: (result: StandardGpkgImportResult) => void;
};

function shortFingerprint(sha: string): string {
  if (!sha || sha.length < 16) return sha || "—";
  return `${sha.slice(0, 8)}…${sha.slice(-6)}`;
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function formatExpiry(unix: number | null | undefined): string {
  if (!unix) return "—";
  try {
    return new Date(unix * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

function suggestPackageCode(projectId: string): string {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 12);
  const suffix = projectId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 6) || "PROJ";
  return `PKG-${suffix}-${stamp}`;
}

export const GpkgImportPanel: React.FC<Props> = ({ projectId, disabled, onImported }) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const confirmLock = useRef(false);
  const precheckLock = useRef(false);
  const requestGen = useRef(0);
  const [phase, setPhase] = useState<GpkgImportPhase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [packageCode, setPackageCode] = useState(() => suggestPackageCode(projectId));
  const [preview, setPreview] = useState<StandardGpkgPreviewResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [techOpen, setTechOpen] = useState(false);
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonImporting, setJsonImporting] = useState(false);

  // Project switch: clear all import state
  useEffect(() => {
    requestGen.current += 1;
    confirmLock.current = false;
    precheckLock.current = false;
    setPhase("idle");
    setFile(null);
    setPreview(null);
    setMessage(null);
    setPackageCode(suggestPackageCode(projectId));
    if (fileRef.current) fileRef.current.value = "";
  }, [projectId]);

  const canPrecheck = Boolean(
    projectId && file && packageCode.trim().length >= 2 && !disabled && !precheckLock.current,
  );
  const confirming = phase === "confirming";
  const canConfirm =
    phase === "preview_ready" &&
    preview?.valid === true &&
    Boolean(preview?.preview_token) &&
    !confirmLock.current &&
    !disabled;

  const statusLabel = useMemo(() => {
    switch (phase) {
      case "uploading":
        return COPY.gpkgUploading;
      case "preview_ready":
        return COPY.gpkgPreviewReady;
      case "confirming":
        return COPY.gpkgConfirming;
      case "preview_invalid":
        return COPY.gpkgBlocked;
      case "token_expired":
        return COPY.gpkgTokenExpired;
      case "success":
        return "导入成功";
      case "conflict":
        return "导入冲突，请检查后重试";
      case "network_error":
        return "网络异常，请稍后重试";
      default:
        return "";
    }
  }, [phase]);

  const invalidatePreview = (nextPhase: GpkgImportPhase = "selecting") => {
    requestGen.current += 1;
    setPreview(null);
    setPhase(nextPhase);
  };

  const resetToIdle = () => {
    requestGen.current += 1;
    confirmLock.current = false;
    precheckLock.current = false;
    setPhase("idle");
    setFile(null);
    setPreview(null);
    setMessage(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const onPickFile = (f: File | null) => {
    if (!f) return;
    invalidatePreview("selecting");
    setFile(f);
    setMessage(null);
  };

  const onPackageCodeChange = (value: string) => {
    setPackageCode(value);
    // Editing package code after preview invalidates token binding.
    if (preview) {
      invalidatePreview(file ? "selecting" : "idle");
      setMessage("设计包编号已变更，请重新预检。");
    }
  };

  const onPrecheck = async () => {
    if (!canPrecheck || !file || precheckLock.current) return;
    precheckLock.current = true;
    const gen = ++requestGen.current;
    setPhase("uploading");
    setMessage(null);
    try {
      const result = await api.previewStandardGpkg(projectId, packageCode.trim(), file);
      if (gen !== requestGen.current) return; // stale response
      setPreview(result);
      if (result.valid && result.preview_token) {
        setPhase("preview_ready");
        setMessage(result.truth_note || COPY.gpkgPreviewReady);
      } else {
        setPhase("preview_invalid");
        setMessage(result.errors?.join("；") || COPY.gpkgBlocked);
      }
    } catch (err) {
      if (gen !== requestGen.current) return;
      const apiErr = err instanceof ApiRequestError ? err : null;
      if (apiErr?.errorCode === "preview_token_expired") {
        setPhase("token_expired");
      } else if (apiErr?.status === 409) {
        setPhase("conflict");
      } else if (apiErr?.status === null) {
        setPhase("network_error");
      } else {
        setPhase("preview_invalid");
      }
      setPreview(null);
      setMessage(apiErr?.message || "预检失败");
    } finally {
      if (gen === requestGen.current) {
        precheckLock.current = false;
      }
    }
  };

  const onConfirm = async () => {
    if (!preview?.valid || !preview.preview_token || confirmLock.current) return;
    if (phase !== "preview_ready") return;
    confirmLock.current = true;
    const gen = requestGen.current;
    setPhase("confirming");
    setMessage(COPY.gpkgConfirming);
    try {
      const result = await api.confirmStandardGpkg(projectId, {
        package_code: preview.package_code,
        staging_id: preview.staging_id,
        preview_token: preview.preview_token,
        design_version: "design-v1",
      });
      if (gen !== requestGen.current) return;
      setPhase("success");
      setPreview(null);
      setMessage(
        `已导入 ${result.package.package_code}，工程对象 ${result.package.object_count} 个。${result.truth_note}`,
      );
      onImported?.(result);
    } catch (err) {
      if (gen !== requestGen.current) return;
      const apiErr = err instanceof ApiRequestError ? err : null;
      const code = apiErr?.errorCode || "";
      if (code === "preview_token_expired") {
        setPhase("token_expired");
        setMessage(COPY.gpkgTokenExpired);
      } else if (
        code.includes("conflict") ||
        code.includes("token") ||
        code === "confirm_in_progress" ||
        code === "confirm_already_completed" ||
        apiErr?.status === 409
      ) {
        setPhase("conflict");
        setMessage(apiErr?.message || "导入冲突");
      } else if (apiErr?.status === null) {
        setPhase("network_error");
        setMessage(apiErr.message);
      } else {
        setPhase("preview_invalid");
        setMessage(apiErr?.message || "确认导入失败");
      }
    } finally {
      if (gen === requestGen.current) {
        confirmLock.current = false;
      }
    }
  };

  const onJsonCompat = async (f: File | null) => {
    if (!f || !projectId) return;
    setJsonImporting(true);
    setMessage(null);
    try {
      const result = await api.importDesignPackageJson(projectId, f);
      setMessage(
        `兼容格式已导入 ${result.package.package_code}，工程对象 ${result.package.object_count} 个。`,
      );
      onImported?.({
        package: result.package,
        objects: result.objects,
        idempotent: false,
        truth_note: result.truth_note,
        source_classification: result.package.synthetic ? "sample_or_unverified" : "library",
      });
    } catch (err) {
      setMessage(err instanceof ApiRequestError ? err.message : "兼容格式导入失败");
    } finally {
      setJsonImporting(false);
    }
  };

  const showConfirmButton = phase === "preview_ready" && preview?.valid === true;
  const packageCodeLocked = phase === "preview_ready" || phase === "confirming";

  return (
    <section className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm" data-testid="gpkg-import-panel">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">{COPY.gpkgImportTitle}</h3>
        <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-900">
          {COPY.gpkgSourceClassSample}
        </span>
      </div>
      <p className="mb-3 text-[11px] leading-4 text-slate-500">{COPY.importHelp}</p>

      <label className="mb-2 block text-[11px] font-medium text-slate-600">
        {COPY.gpkgPackageCode}
        <input
          type="text"
          data-testid="gpkg-package-code"
          value={packageCode}
          onChange={(e) => onPackageCodeChange(e.target.value)}
          disabled={disabled || packageCodeLocked || phase === "uploading"}
          className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900 disabled:bg-slate-50"
          maxLength={100}
          autoComplete="off"
        />
      </label>

      <label
        className={cn(
          "mb-2 flex cursor-pointer items-center justify-center rounded-2xl border border-dashed border-sky-300 bg-sky-50/60 px-3 py-3 text-sm font-medium text-sky-800 hover:bg-sky-50",
          (disabled || phase === "uploading" || phase === "confirming") && "pointer-events-none opacity-60",
        )}
      >
        {file ? file.name : COPY.gpkgSelectFile}
        <input
          ref={fileRef}
          data-testid="gpkg-file-input"
          type="file"
          accept=".gpkg,application/geopackage+sqlite3,application/octet-stream"
          className="hidden"
          disabled={disabled || phase === "uploading" || phase === "confirming"}
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            e.target.value = "";
            onPickFile(f);
          }}
        />
      </label>

      <div className="mb-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="gpkg-precheck-btn"
          disabled={!canPrecheck || phase === "uploading" || phase === "confirming"}
          onClick={() => void onPrecheck()}
          className="rounded-xl bg-sky-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {phase === "uploading" ? COPY.gpkgUploading : COPY.gpkgRunPrecheck}
        </button>
        {showConfirmButton ? (
          <button
            type="button"
            data-testid="gpkg-confirm-btn"
            disabled={!canConfirm}
            onClick={() => void onConfirm()}
            className="rounded-xl bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {COPY.gpkgConfirmImport}
          </button>
        ) : null}
        {(phase === "preview_invalid" ||
          phase === "token_expired" ||
          phase === "conflict" ||
          phase === "success" ||
          phase === "network_error") && (
          <button
            type="button"
            data-testid="gpkg-reupload-btn"
            onClick={resetToIdle}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
          >
            {COPY.gpkgReupload}
          </button>
        )}
      </div>

      {statusLabel ? (
        <p className="mb-2 text-[11px] font-medium text-slate-700" role="status" data-testid="gpkg-status">
          {statusLabel}
        </p>
      ) : null}
      {message ? (
        <p className="mb-2 text-[11px] leading-4 text-slate-600" data-testid="gpkg-message">
          {message}
        </p>
      ) : null}

      {preview ? (
        <div
          className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50 p-3 text-[11px] text-slate-700"
          data-testid="gpkg-preview-card"
        >
          <div className="flex flex-wrap gap-x-3 gap-y-1 break-all">
            <span>
              文件：{file?.name || "—"}（{formatBytes(preview.size_bytes)}）
            </span>
            <span>编号：{preview.package_code}</span>
            <span>对象数：{preview.candidate_count}</span>
            <span>指纹：{shortFingerprint(preview.source_sha256)}</span>
            <span>凭证有效至：{formatExpiry(preview.expires_at_unix)}</span>
          </div>
          <div>
            <p className="mb-1 font-medium text-slate-800">图层</p>
            <ul className="max-h-40 space-y-1 overflow-y-auto">
              {(preview.layers_summary || []).map((layer) => (
                <li key={layer.name} className="rounded-lg bg-white px-2 py-1">
                  <span className="font-medium">{layer.name}</span>
                  {" · "}
                  {layer.accepted ? "可导入" : "不导入"}
                  {layer.resolved_epsg != null ? ` · EPSG:${layer.resolved_epsg}` : ""}
                  {layer.feature_count != null ? ` · ${layer.feature_count} 条` : ""}
                  {layer.rejection_reasons?.length
                    ? ` · ${layer.rejection_reasons.join(", ")}`
                    : ""}
                </li>
              ))}
            </ul>
          </div>
          {preview.warnings?.length ? (
            <p className="text-amber-800">警告：{preview.warnings.join("；")}</p>
          ) : null}
          {preview.errors?.length ? (
            <p className="text-rose-800">阻断：{preview.errors.join("；")}</p>
          ) : null}

          <details open={techOpen} onToggle={(e) => setTechOpen((e.target as HTMLDetailsElement).open)}>
            <summary className="cursor-pointer text-sky-800">
              {COPY.techDetails} / {COPY.gpkgDataSource}
            </summary>
            <div className="mt-1 space-y-1 break-all rounded-xl bg-white p-2 text-[10px] text-slate-600">
              <p>完整摘要：{preview.source_sha256 || "—"}</p>
              <p>契约版本：{preview.import_contract_version}</p>
              <p>来源分类：{preview.source_classification || COPY.gpkgSourceClassSample}</p>
              <p>错误码：{preview.error_code || "—"}</p>
              <p>{COPY.gpkgTruthBoundary}</p>
            </div>
          </details>
        </div>
      ) : null}

      <details
        className="mt-3"
        data-testid="gpkg-json-compat"
        open={jsonOpen}
        onToggle={(e) => setJsonOpen((e.target as HTMLDetailsElement).open)}
      >
        <summary className="cursor-pointer text-[11px] font-medium text-slate-600">
          {COPY.gpkgCompatibleFormats}
        </summary>
        <div className="mt-2 rounded-2xl border border-slate-100 bg-slate-50 p-3">
          <p className="mb-2 text-[11px] text-slate-500">{COPY.gpkgJsonCompatHelp}</p>
          <label className="flex cursor-pointer items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2 text-xs text-slate-700">
            {jsonImporting ? "导入中…" : "选择 JSON 兼容包"}
            <input
              type="file"
              accept="application/json,.json"
              className="hidden"
              data-testid="gpkg-json-input"
              disabled={!projectId || jsonImporting || disabled}
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                e.target.value = "";
                void onJsonCompat(f);
              }}
            />
          </label>
        </div>
      </details>
    </section>
  );
};

export default GpkgImportPanel;
