import React, { useEffect, useId, useState } from "react";
import { CameraIcon, CheckIcon, InfoIcon, ShieldIcon, XIcon } from "../Icons";
import { api, EvidenceObjectUrl } from "../../lib/api";

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

  return (
    <figure
      aria-busy={loading}
      className="overflow-hidden rounded-[24px] border border-slate-700 bg-[#06111f] text-white shadow-[0_22px_70px_-42px_rgba(2,132,199,.9)]"
    >
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-cyan-200">
            <ShieldIcon className="h-4 w-4" /> 服务端证据预览
          </div>
          <p className="mt-1 truncate text-sm font-medium text-white">
            {originalName || evidenceId}
          </p>
        </div>
        {content ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1 text-[11px] font-semibold text-emerald-200 sm:self-auto">
            <CheckIcon className="h-3.5 w-3.5" /> 服务端原件 / 摘要已校验
          </span>
        ) : null}
      </div>

      <div className="relative flex min-h-52 items-center justify-center bg-[radial-gradient(circle_at_center,rgba(14,165,233,.13),transparent_58%)] sm:min-h-72">
        {requestVersion === 0 && isLargeEvidence && !largeFileConfirmed ? (
          <div
            className="mx-4 w-full max-w-xl rounded-[22px] border border-amber-300/25 bg-amber-300/10 p-4 text-left sm:mx-6 sm:p-5"
            role="alert"
            aria-labelledby={warningTitleId}
            aria-describedby={warningDescriptionId}
          >
            <div className="flex items-start gap-3">
              <InfoIcon className="mt-0.5 h-5 w-5 shrink-0 text-amber-300" />
              <div className="min-w-0">
                <p id={warningTitleId} className="text-sm font-semibold text-amber-100">大文件自动预览已暂停</p>
                <p id={warningDescriptionId} className="mt-2 text-xs leading-5 text-amber-100/75">
                  服务端登记大小为 {formatBytes(registeredSizeBytes ?? 0)}，超过 64 MiB 保护阈值。当前鉴权方案会把完整媒体载入浏览器内存，可能造成页面卡顿或崩溃。
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setLargeFileConfirmed(true);
                    setRequestVersion((value) => value + 1);
                  }}
                  className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-amber-300 px-4 py-2.5 text-sm font-semibold text-slate-950 hover:bg-amber-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100 sm:w-auto"
                >
                  <CameraIcon className="h-4 w-4" /> 我已了解内存风险，仍要加载
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {requestVersion === 0 && !isLargeEvidence ? (
          <button
            type="button"
            onClick={() => setRequestVersion(1)}
            className="mx-5 inline-flex min-h-11 items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/10 px-5 py-3 text-sm font-semibold text-cyan-100 hover:bg-cyan-300/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
          >
            <CameraIcon className="h-4 w-4" /> 鉴权加载原始证据
          </button>
        ) : null}

        {loading ? (
          <div className="flex flex-col items-center px-5 text-center" role="status">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-300/25 border-t-sky-300" />
            <p className="mt-3 text-sm font-medium text-slate-200">正在从服务端安全读取原件…</p>
            <p className="mt-1 text-xs text-slate-400">API Key 仅通过请求头发送</p>
          </div>
        ) : null}

        {error ? (
          <div className="mx-5 max-w-lg rounded-2xl border border-rose-300/20 bg-rose-300/10 p-4 text-center" role="alert">
            <XIcon className="mx-auto h-5 w-5 text-rose-300" />
            <p className="mt-2 text-sm font-semibold text-rose-100">无法读取原始证据</p>
            <p className="mt-1 break-words text-xs leading-5 text-rose-200/80">{error}</p>
            <button
              type="button"
              onClick={() => setRequestVersion((value) => value + 1)}
              className="mt-3 rounded-xl bg-white/10 px-4 py-2 text-xs font-semibold text-white hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
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
            aria-label={`原始证据视频：${originalName || evidenceId}`}
          >
            <source src={content.url} type={content.contentType} />
            当前浏览器无法播放该视频。
          </video>
        ) : null}

        {content && mediaKind === "image" ? (
          <img
            src={content.url}
            alt={`原始施工证据：${originalName || evidenceId}`}
            className="max-h-[32rem] w-full object-contain"
          />
        ) : null}

        {content && mediaKind === "unsupported" ? (
          <div className="px-5 text-center">
            <InfoIcon className="mx-auto h-6 w-6 text-amber-300" />
            <p className="mt-2 text-sm font-semibold text-amber-100">浏览器不支持内嵌预览此格式</p>
            <p className="mt-1 text-xs text-slate-400">{content.contentType}</p>
          </div>
        ) : null}
      </div>

      <figcaption className="grid gap-3 px-4 py-3 text-[11px] leading-5 text-slate-400 sm:grid-cols-[1fr_auto] sm:items-end">
        <div className="min-w-0">
          <p className="break-all font-mono">证据 ID · {evidenceId}</p>
          {sha256 ? <p className="mt-1 break-all font-mono">SHA-256 · {sha256}</p> : null}
        </div>
        <div className="sm:text-right">
          {typeof registeredSizeBytes === "number" ? <p>登记大小 · {formatBytes(registeredSizeBytes)}</p> : null}
          {content ? <p>{content.contentType} · {formatBytes(content.sizeBytes)}</p> : null}
          <p>本地演示会完整载入 Blob，非生产级 Range 流播放</p>
        </div>
      </figcaption>
    </figure>
  );
};
