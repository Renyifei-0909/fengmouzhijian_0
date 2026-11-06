import React, { useEffect, useMemo, useRef, useState } from "react";
import { OperationStatus } from "../ui/OperationStatus";
import {
  api,
  ApiRequestError,
  StandardGpkgImportResult,
  StandardGpkgPreviewResult,
} from "../../lib/api";
import { Check, FileUp } from "lucide-react";
import { COPY, friendlyApiError } from "../../lib/productCopy";
import "./workbench.css";

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
  onFileSelected?: (file: File) => void;
  onSavingChange?: (saving: boolean) => void;
};

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

function suggestPackageCode(): string {
  const stamp = new Date().toISOString();
  return `SJ-${stamp.slice(0, 10).replace(/-/g, "")}-${stamp.slice(11, 19).replace(/:/g, "")}`;
}

const explainImportError = (error: unknown, fallback: string) => {
  if (!(error instanceof ApiRequestError)) return fallback;
  const message = friendlyApiError(error.message);
  return /[一-鿿]/.test(message) ? message : fallback;
};

export const GpkgImportPanel: React.FC<Props> = ({
  projectId,
  disabled,
  onImported,
  onFileSelected,
  onSavingChange,
}) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const confirmLock = useRef(false);
  const precheckLock = useRef(false);
  const requestGen = useRef(0);
  const [phase, setPhase] = useState<GpkgImportPhase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [packageCode, setPackageCode] = useState(suggestPackageCode);
  const [preview, setPreview] = useState<StandardGpkgPreviewResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
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
    setJsonImporting(false);
    setPackageCode(suggestPackageCode());
    if (fileRef.current) fileRef.current.value = "";
  }, [projectId]);

  const confirming = phase === "confirming";
  const busy = disabled || phase === "uploading" || confirming || jsonImporting;
  const canPrecheck = Boolean(projectId && file && packageCode.trim().length >= 2 && !busy);

  const canConfirm =
    phase === "preview_ready" &&
    preview?.valid === true &&
    Boolean(preview?.preview_token) &&
    !confirmLock.current &&
    !busy;

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
    onFileSelected?.(f);
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
        setMessage(null);
      } else {
        setPhase("preview_invalid");
        setMessage("请检查文件格式、图层及坐标系统。");
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
      setMessage(explainImportError(err, "请检查文件和设计包编号后重试。"));
    } finally {
      if (gen === requestGen.current) {
        precheckLock.current = false;
      }
    }
  };

  const onConfirm = async () => {
    if (!canConfirm || !preview?.preview_token || confirmLock.current) return;
    confirmLock.current = true;
    const gen = requestGen.current;
    setPhase("confirming");
    setMessage(null);
    onSavingChange?.(true);
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
      setMessage(`已导入 ${result.package.object_count} 个工程对象。`);
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
        setMessage(explainImportError(err, "请核对设计包编号及导入记录。"));
      } else if (apiErr?.status === null) {
        setPhase("network_error");
        setMessage(explainImportError(err, "请检查网络连接后重试。"));
      } else {
        setPhase("preview_invalid");
        setMessage(explainImportError(err, "无法确认导入，请重新预检。"));
      }
    } finally {
      if (gen === requestGen.current) {
        confirmLock.current = false;
        onSavingChange?.(false);
      }
    }
  };

  const onJsonCompat = async (f: File | null) => {
    if (!f || !projectId || busy || confirmLock.current || precheckLock.current) return;
    confirmLock.current = true;
    const gen = ++requestGen.current;
    setPhase("idle");
    setPreview(null);
    setJsonImporting(true);
    onSavingChange?.(true);
    setMessage(null);
    try {
      const result = await api.importDesignPackageJson(projectId, f);
      if (gen !== requestGen.current) return;
      setPhase("success");
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
      if (gen === requestGen.current) {
        setPhase("preview_invalid");
        setMessage(explainImportError(err, "兼容设计包导入失败。"));
      }
    } finally {
      if (gen === requestGen.current) {
        confirmLock.current = false;
        setJsonImporting(false);
        onSavingChange?.(false);
      }
    }
  };

  const packageCodeLocked = phase === "preview_ready" || confirming;
  const hasError = ["preview_invalid", "token_expired", "conflict", "network_error"].includes(phase);
  const layers = preview?.layers_summary ?? [];

  return (
    <section className="gis-import-panel" data-testid="gpkg-import-panel">
      <div className="gis-import-steps" aria-label="导入步骤">
        <span data-current={!preview && phase !== "success"}>01 选择文件</span>
        <span data-current={Boolean(preview && phase !== "confirming")}>02 预检</span>
        <span data-current={confirming || phase === "success"}>03 写入工程</span>
      </div>

      <div className="gis-form">
        <label>{COPY.gpkgPackageCode}
          <input
            type="text"
            className="fm-input"
            data-testid="gpkg-package-code"
            value={packageCode}
            onChange={(event) => onPackageCodeChange(event.target.value)}
            disabled={busy || packageCodeLocked}
            maxLength={100}
            autoComplete="off"
          />
        </label>

        <label className="gis-upload">
          <FileUp size={20} aria-hidden="true" />
          <span>{file?.name || COPY.gpkgSelectFile}</span>
          <small>{file ? formatBytes(file.size) : "工程空间数据包（.gpkg）"}</small>
          <input
            ref={fileRef}
            data-testid="gpkg-file-input"
            type="file"
            accept=".gpkg,application/geopackage+sqlite3,application/octet-stream"
            aria-label={COPY.gpkgSelectFile}
            disabled={busy}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              event.target.value = "";
              onPickFile(selected);
            }}
          />
        </label>
      </div>

      {busy && <OperationStatus detail={confirming ? "正在写入已确认的图层和工程对象。" : "正在读取文件与图层信息。"}>
        {confirming ? "正在导入工程对象" : jsonImporting ? "正在读取设计包" : "正在预检设计图"}
      </OperationStatus>}
      {statusLabel && (
        <p className={hasError ? "gis-record-error" : "gis-import-feedback"} role="status" data-testid="gpkg-status">
          {statusLabel}
        </p>
      )}
      {message && <p className="gis-import-feedback" data-testid="gpkg-message">{message}</p>}

      {preview && (
        <div className="gis-import-preview" data-testid="gpkg-preview-card">
          <dl className="gis-data">
            <div><dt>设计包</dt><dd>{preview.package_code}</dd></div>
            <div><dt>工程对象</dt><dd>{preview.candidate_count} 个</dd></div>
          </dl>
          {layers.length > 0 && (
            <ul className="gis-import-layers" aria-label="预检图层">
              {layers.map((layer) => (
                <li key={layer.name}>
                  <span>{layer.name}</span>
                  <span>{layer.accepted ? "可导入" : "已排除"}{layer.feature_count == null ? "" : ` · ${layer.feature_count} 条`}</span>
                </li>
              ))}
            </ul>
          )}
          {preview.warnings.length > 0 && <p className="gis-import-feedback">预检提醒 {preview.warnings.length} 项</p>}
          <details className="gis-disclosure">
            <summary>预检详情</summary>
            <dl className="gis-data">
              <div><dt>预检有效至</dt><dd>{formatExpiry(preview.expires_at_unix)}</dd></div>
              <div><dt>来源核验</dt><dd>{preview.source_classification === "sample_or_unverified" ? "待核验" : "见导入资料"}</dd></div>
            </dl>
            <pre>{JSON.stringify({
              layers,
              errors: preview.errors,
              warnings: preview.warnings,
              fingerprint: preview.source_sha256,
            }, null, 2)}</pre>
          </details>
        </div>
      )}

      <div className="gis-actions gis-import-actions">
        {phase === "preview_ready" && preview?.valid ? (
          <>
            <button type="button" className="fm-button" onClick={resetToIdle}>{COPY.gpkgReupload}</button>
            <button type="button" className="fm-button fm-button-primary" data-testid="gpkg-confirm-btn" disabled={!canConfirm} onClick={() => void onConfirm()}>
              <Check size={15} aria-hidden="true" /> {COPY.gpkgConfirmImport}
            </button>
          </>
        ) : (
          <>
            {(hasError || phase === "success") && <button type="button" className="fm-button" data-testid="gpkg-reupload-btn" onClick={resetToIdle}>{COPY.gpkgReupload}</button>}
            <button type="button" className="fm-button fm-button-primary" data-testid="gpkg-precheck-btn" disabled={!canPrecheck} onClick={() => void onPrecheck()}>
              {phase === "uploading" ? COPY.gpkgUploading : confirming ? COPY.gpkgConfirming : COPY.gpkgRunPrecheck}
            </button>
          </>
        )}
      </div>

      <details className="gis-disclosure" data-testid="gpkg-json-compat">
        <summary>{COPY.gpkgCompatibleFormats}</summary>
        <label className="gis-upload">
          <FileUp size={17} aria-hidden="true" />
          <span>{jsonImporting ? "正在导入…" : "选择兼容设计包（.json）"}</span>
          <input
            type="file"
            accept="application/json,.json"
            aria-label="选择兼容设计包"
            data-testid="gpkg-json-input"
            disabled={!projectId || busy}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              event.target.value = "";
              void onJsonCompat(selected);
            }}
          />
        </label>
      </details>
    </section>
  );
};

export default GpkgImportPanel;
