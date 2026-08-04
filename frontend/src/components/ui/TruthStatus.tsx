import React from "react";
import type { VerificationJob } from "../../lib/api";
import {
  analysisTruthFromJob,
  remoteProvenanceFromJob,
  TruthDescriptor,
  TruthTone,
} from "../../lib/truth";
import { InfoIcon, ShieldIcon } from "../Icons";

const toneClasses: Record<TruthTone, string> = {
  slate: "border-slate-200 bg-slate-100 text-slate-700",
  amber: "border-amber-200 bg-amber-100 text-amber-800",
  violet: "border-violet-200 bg-violet-100 text-violet-800",
  emerald: "border-emerald-200 bg-emerald-100 text-emerald-800",
  rose: "border-rose-200 bg-rose-100 text-rose-800",
};

const panelToneClasses: Record<TruthTone, { panel: string; text: string; divider: string; icon: string }> = {
  slate: { panel: "border-slate-200 bg-slate-50", text: "text-slate-700", divider: "border-slate-200", icon: "text-slate-600" },
  amber: { panel: "border-amber-200 bg-amber-50", text: "text-amber-800", divider: "border-amber-200", icon: "text-amber-700" },
  violet: { panel: "border-violet-200 bg-violet-50", text: "text-violet-800", divider: "border-violet-200", icon: "text-violet-700" },
  emerald: { panel: "border-emerald-200 bg-emerald-50", text: "text-emerald-800", divider: "border-emerald-200", icon: "text-emerald-700" },
  rose: { panel: "border-rose-200 bg-rose-50", text: "text-rose-800", divider: "border-rose-200", icon: "text-rose-700" },
};

export const TruthBadge: React.FC<{ truth: TruthDescriptor; className?: string }> = ({ truth, className = "" }) => (
  <span className={`inline-flex w-fit items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold ${toneClasses[truth.tone]} ${className}`}>
    <ShieldIcon className="h-3.5 w-3.5" /> {truth.label}
  </span>
);

const HashValue: React.FC<{ label: string; value: string | null }> = ({ label, value }) => value ? (
  <div className="min-w-0 rounded-xl border border-white/80 bg-white/75 px-3 py-2 shadow-sm shadow-slate-950/5">
    <p className="text-[10px] text-slate-500">{label}</p>
    <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-600">{value}</p>
  </div>
) : null;

export const AnalysisTruthPanel: React.FC<{ job: VerificationJob }> = ({ job }) => {
  const truth = analysisTruthFromJob(job);
  const remote = remoteProvenanceFromJob(job);
  const panelTone = panelToneClasses[truth.tone];
  return (
    <section className={`rounded-[22px] border p-4 ${panelTone.panel}`} aria-label="任务真实性状态">
      <TruthBadge truth={truth} />
      <p className={`mt-3 text-xs leading-5 ${panelTone.text}`}>{truth.description}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
        <span className={`rounded-full bg-white/70 px-2.5 py-1 ${panelTone.text}`}>
          证据等级：{truth.evidenceGrade ? "评测证据" : "非指标证据"}
        </span>
        <span className={`rounded-full bg-white/70 px-2.5 py-1 ${panelTone.text}`}>
          准确率声明：{truth.accuracyClaimPresent ? "已提供，需核验" : "无"}
        </span>
      </div>
      {remote ? (
        <div className={`mt-4 border-t pt-3 ${panelTone.divider}`}>
          <div className="flex items-start gap-2">
            <InfoIcon className={`mt-0.5 h-4 w-4 shrink-0 ${panelTone.icon}`} />
            <p className={`text-xs leading-5 ${panelTone.text}`}>
              固定模型：{remote.modelName || "未提供"} · {remote.modelVersion || "未提供"}
            </p>
          </div>
          <details className="mt-3 rounded-2xl border border-white/80 bg-white/45">
            <summary className={`flex cursor-pointer items-center justify-between gap-3 rounded-2xl px-3 py-2.5 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${panelTone.text}`}>
              <span>展开技术溯源</span>
              <span className="font-normal opacity-70">哈希摘要与限制</span>
            </summary>
            <div className={`space-y-3 border-t px-3 pb-3 pt-3 ${panelTone.divider}`}>
              <div className="grid gap-2 sm:grid-cols-2">
                <HashValue label="模型制品 SHA-256" value={remote.artifactSha256} />
                <HashValue label="代码 SHA-256" value={remote.codeSha256} />
                <HashValue label="配置 SHA-256" value={remote.configSha256} />
                <HashValue label="请求 SHA-256" value={remote.requestSha256} />
                <HashValue label="响应 SHA-256" value={remote.responseSha256} />
              </div>
              {remote.limitations.length ? (
                <div>
                  <p className={`text-[10px] font-semibold uppercase tracking-[0.14em] ${panelTone.text}`}>服务端限制说明</p>
                  <ul className={`mt-2 space-y-1 text-xs leading-5 ${panelTone.text}`}>
                    {remote.limitations.map((item, index) => <li key={`${index}-${item}`}>· {item}</li>)}
                  </ul>
                </div>
              ) : null}
            </div>
          </details>
        </div>
      ) : null}
    </section>
  );
};
