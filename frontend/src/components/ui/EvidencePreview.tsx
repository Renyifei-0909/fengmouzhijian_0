import React, { useEffect, useId, useState } from "react";
import { CameraIcon, CheckIcon, InfoIcon, XIcon } from "../Icons";
import { api, EvidenceObjectUrl } from "../../lib/api";
import { friendlyApiError } from "../../lib/productCopy";

type EvidencePreviewProps = {
  evidenceId: string;
  originalName?: string;
  sha256?: string;
  registeredSizeBytes?: number;
  autoLoad?: boolean;
};

export const DEFAULT_EVIDENCE_PREVIEW_LIMIT_BYTES = 64 * 1024 * 1024;

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
};

export const EvidencePreview: React.FC<EvidencePreviewProps> = (props) => {
  const sessionKey = JSON.stringify([
    props.evidenceId,
    props.registeredSizeBytes ?? null,
    props.autoLoad ?? false,
    DEFAULT_EVIDENCE_PREVIEW_LIMIT_BYTES,
  ]);
  return <EvidencePreviewSession key={sessionKey} {...props} />;
};

const EvidencePreviewSession: React.FC<EvidencePreviewProps> = ({
  evidenceId,
  originalName,
  sha256,
  registeredSizeBytes,
  autoLoad = false,
}) => {
  const isLargeEvidence = typeof registeredSizeBytes === "number"
    && registeredSizeBytes > DEFAULT_EVIDENCE_PREVIEW_LIMIT_BYTES;
  const hasEvidence = !!evidenceId && evidenceId !== "pending";
  const warningTitleId = useId();
  const warningDescriptionId = useId();
  const [requestVersion, setRequestVersion] = useState(autoLoad && !isLargeEvidence ? 1 : 0);
  const [largeFileConfirmed, setLargeFileConfirmed] = useState(false);
  const [content, setContent] = useState<EvidenceObjectUrl | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (
      requestVersion === 0
      || !evidenceId
      || evidenceId === "pending"
      || (isLargeEvidence && !largeFileConfirmed)
    ) {
      setContent(null);
      setError("");
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let disposed = false;
    let currentObjectUrl: EvidenceObjectUrl | null = null;
    setLoading(true);
    setError("");
    setContent(null);

    api.createEvidenceObjectUrl(evidenceId, { signal: controller.signal })
      .then((next) => {
        if (disposed) {
          next.revoke();
          return;
        }
        currentObjectUrl = next;
        setContent(next);
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "原始证据预览失败");
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });

    return () => {
      disposed = true;
      controller.abort();
      currentObjectUrl?.revoke();
    };
  }, [evidenceId, isLargeEvidence, largeFileConfirmed, requestVersion]);

  const mediaKind = content?.contentType === "video/mp4"
    || content?.contentType === "video/quicktime"
    || content?.contentType === "video/x-msvideo"
    || content?.contentType === "video/x-matroska"
    || content?.contentType === "video/webm"
    ? "video"
    : content?.contentType === "image/jpeg" || content?.contentType === "image/png"
      ? "image"
      : "unsupported";
  const title = originalName || "现场影像";
  const displayError = error ? friendlyApiError(error) : "";

  return (
    <figure
      aria-busy={loading}
      className="min-w-0 overflow-hidden rounded-md border border-[#25435c] bg-[#0b2036] text-[#e2edf6]"
    >
      <div className="flex items-center justify-between gap-3 border-b border-[#25435c] px-3.5 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={title}>{title}</p>
        </div>
        {content ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-emerald-200">
            <CheckIcon className="h-3.5 w-3.5" /> 原件已校验
          </span>
        ) : null}
      </div>

      <div className="relative flex min-h-44 items-center justify-center bg-[#061526]">
        {hasEvidence && requestVersion === 0 && isLargeEvidence && !largeFileConfirmed ? (
          <div
            className="m-4 w-full max-w-xl rounded-md border border-amber-300/25 bg-amber-300/10 p-4 text-left"
            role="alert"
            aria-labelledby={warningTitleId}
            aria-describedby={warningDescriptionId}
          >
            <div className="flex items-start gap-3">
              <InfoIcon className="mt-0.5 h-5 w-5 shrink-0 text-amber-300" />
              <div className="min-w-0">
                <p id={warningTitleId} className="text-sm font-semibold text-amber-100">大文件预览</p>
                <p id={warningDescriptionId} className="mt-2 text-xs leading-5 text-amber-100/75">
                  该文件大小为 {formatBytes(registeredSizeBytes ?? 0)}。打开预览需先加载完整文件。
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setLargeFileConfirmed(true);
                    setRequestVersion((value) => value + 1);
                  }}
                  className="mt-3 inline-flex min-h-9 items-center gap-2 rounded-md border border-amber-300/40 px-3 py-2 text-sm font-medium text-amber-100 hover:bg-amber-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                >
                  <CameraIcon className="h-4 w-4" /> 加载预览
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {hasEvidence && requestVersion === 0 && !isLargeEvidence ? (
          <button
            type="button"
            onClick={() => setRequestVersion(1)}
            className="mx-4 inline-flex min-h-9 items-center gap-2 rounded-md border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-sm font-medium text-cyan-100 hover:bg-cyan-300/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
          >
            <CameraIcon className="h-4 w-4" /> 查看原件
          </button>
        ) : null}
        {!hasEvidence ? <p className="text-sm text-[#a2b7cb]">暂无现场影像</p> : null}

        {loading ? (
          <div className="flex flex-col items-center px-5 text-center" role="status">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-300/25 border-t-sky-300" />
            <p className="mt-3 text-sm font-medium text-slate-200">正在读取原件…</p>
          </div>
        ) : null}

        {error ? (
          <div className="m-4 max-w-lg rounded-md border border-rose-300/20 bg-rose-300/10 p-4 text-center" role="alert">
            <XIcon className="mx-auto h-5 w-5 text-rose-300" />
            <p className="mt-2 text-sm font-semibold text-rose-100">无法读取原始证据</p>
            <p className="mt-1 break-words text-xs leading-5 text-rose-200/80">{displayError}</p>
            {displayError !== error ? <details className="mt-2 text-xs text-rose-200/80">
              <summary className="cursor-pointer">查看原因</summary>
              <p className="mt-1 break-words">{error}</p>
            </details> : null}
            <button
              type="button"
              onClick={() => setRequestVersion((value) => value + 1)}
              className="mt-3 rounded-md bg-white/10 px-4 py-2 text-xs font-semibold text-white hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
            >
              重新加载
            </button>
          </div>
        ) : null}

        {content && mediaKind === "video" ? (
          <video
            key={content.url}
            className="max-h-[32rem] w-full bg-black object-contain"
            controls
            playsInline
            preload="metadata"
            aria-label={title}
          >
            <source src={content.url} type={content.contentType} />
            当前浏览器无法播放该视频。
          </video>
        ) : null}

        {content && mediaKind === "image" ? (
          <img
            src={content.url}
            alt={title}
            className="max-h-[32rem] w-full object-contain"
          />
        ) : null}

        {content && mediaKind === "unsupported" ? (
          <div className="px-5 text-center">
            <InfoIcon className="mx-auto h-6 w-6 text-amber-300" />
            <p className="mt-2 text-sm font-semibold text-amber-100">浏览器不支持内嵌预览此格式</p>
          </div>
        ) : null}
      </div>

      {hasEvidence ? (
        <figcaption className="border-t border-[#25435c] px-3.5 py-2 text-xs leading-5 text-[#a2b7cb]">
          <details>
            <summary className="cursor-pointer">证据详情</summary>
            <dl className="mt-2 grid gap-x-4 gap-y-1 [grid-template-columns:auto_minmax(0,1fr)]">
              <dt>证据编号</dt><dd className="break-all">{evidenceId}</dd>
              {sha256 ? <><dt>证据摘要</dt><dd className="break-all font-mono">{sha256}</dd></> : null}
              {typeof registeredSizeBytes === "number" ? <><dt>登记大小</dt><dd>{formatBytes(registeredSizeBytes)}</dd></> : null}
              {content ? <><dt>媒体格式</dt><dd className="break-words">{content.contentType} · {formatBytes(content.sizeBytes)}</dd></> : null}
            </dl>
          </details>
        </figcaption>
      ) : null}
    </figure>
  );
};
