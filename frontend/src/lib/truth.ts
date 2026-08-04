import type { Report, VerificationJob } from "./api";

export type TruthTone = "slate" | "amber" | "violet" | "emerald" | "rose";

export type TruthDescriptor = {
  label: string;
  tone: TruthTone;
  description: string;
  reviewNote: string;
  evidenceGrade: boolean;
  accuracyClaimPresent: boolean;
};

export type RemoteProvenance = {
  modelName: string | null;
  modelVersion: string | null;
  artifactSha256: string | null;
  codeSha256: string | null;
  configSha256: string | null;
  requestSha256: string | null;
  responseSha256: string | null;
  limitations: string[];
};

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const stringValue = (value: unknown): string | null => (
  typeof value === "string" && value.length > 0 ? value : null
);

const resultMode = (job: VerificationJob): string => {
  const persistedMode = isRecord(job.result) ? stringValue(job.result.analysis_mode) : null;
  return persistedMode || job.analyzer_name;
};

const resultEvidenceGrade = (result: Record<string, unknown> | null): boolean => (
  isRecord(result) && result.evidence_grade === true
);

const resultHasAccuracyClaim = (result: Record<string, unknown> | null): boolean => (
  isRecord(result) && result.accuracy_claim !== null && result.accuracy_claim !== undefined
);

export const analysisTruthFromJob = (job: VerificationJob): TruthDescriptor => {
  const mode = resultMode(job);
  const evidenceGrade = resultEvidenceGrade(job.result);
  const accuracyClaimPresent = resultHasAccuracyClaim(job.result);

  if (mode === "remote_http") {
    return {
      label: "远程单样本推理（未评测）",
      tone: "amber",
      description: "结果来自任务锁定的远程服务，但未绑定冻结 EvaluationRun；人工批准只记录复核，不会升级为准确率或正式指标证据。",
      reviewNote: "仅批准远程单样本推理记录；未绑定冻结 EvaluationRun，人工批准不构成准确率或正式指标证据。",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (mode === "demo_fixture") {
    return {
      label: "合成演示夹具（非真实推理）",
      tone: "violet",
      description: "该结果由确定性合成夹具生成，只验证软件闭环；人工批准不会把演示输出升级为真实模型结果。",
      reviewNote: "仅批准合成演示闭环，不认可为真实模型输出、准确率或竞赛指标。",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (mode === "stub") {
    return {
      label: "安全占位输出（非算法结论）",
      tone: "slate",
      description: "该任务只验证工程链路，不输出物理量测或识别能力结论。",
      reviewNote: "确认已阅读安全占位输出边界；该报告不构成算法验真、准确率或竞赛指标结论。",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (evidenceGrade) {
    return {
      label: "评测证据",
      tone: "emerald",
      description: "该任务由服务端标记为评测证据；仍应核对绑定的 EvaluationRun 和冻结制品。",
      reviewNote: "已核对服务端评测证据状态及其绑定制品。",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  return {
    label: `${mode || "未知适配器"}（未评测）`,
    tone: "amber",
    description: "当前持久化结果未被服务端标记为评测证据，不能据此声明准确率或指标达标。",
    reviewNote: "仅批准当前单样本记录；未形成服务端评测证据，不构成准确率或竞赛指标。",
    evidenceGrade,
    accuracyClaimPresent,
  };
};

export const reportTruthFromReport = (report: Report): TruthDescriptor => {
  const content = isRecord(report.content) ? report.content : {};
  const analysis = isRecord(content.analysis) ? content.analysis : null;
  const evidenceGrade = content.evidence_grade === true;
  const accuracyClaimPresent = resultHasAccuracyClaim(analysis);

  if (report.status === "reviewed_non_evaluated") {
    return {
      label: "已复核 · 未评测",
      tone: "amber",
      description: "这是服务端封存的单样本复核报告，不是冻结 EvaluationRun；人工批准不升级准确率或正式指标。",
      reviewNote: "",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (report.status === "reviewed_demo") {
    return {
      label: "演示报告 · 非指标",
      tone: "violet",
      description: "报告来自合成演示夹具，只证明软件流程可运行，不代表真实模型能力。",
      reviewNote: "",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (report.status === "reviewed_placeholder") {
    return {
      label: "占位报告 · 非指标",
      tone: "slate",
      description: "报告来自安全占位任务，不包含算法验真或准确率结论。",
      reviewNote: "",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  if (report.status === "final" && evidenceGrade) {
    return {
      label: "正式评测结果",
      tone: "emerald",
      description: "服务端将该报告标记为评测证据；仍需核对其冻结 EvaluationRun 和制品绑定。",
      reviewNote: "",
      evidenceGrade,
      accuracyClaimPresent,
    };
  }
  return {
    label: report.status || "未知报告状态",
    tone: evidenceGrade ? "emerald" : "rose",
    description: evidenceGrade
      ? "报告被服务端标记为评测证据；请继续核对绑定制品。"
      : "该报告未被服务端标记为评测证据，不能作为准确率或正式指标证明。",
    reviewNote: "",
    evidenceGrade,
    accuracyClaimPresent,
  };
};

export const remoteProvenanceFromJob = (job: VerificationJob): RemoteProvenance | null => {
  if (resultMode(job) !== "remote_http" || !isRecord(job.result)) return null;
  const provenance = isRecord(job.result.provenance) ? job.result.provenance : {};
  const model = isRecord(provenance.model) ? provenance.model : {};
  return {
    modelName: stringValue(model.name),
    modelVersion: stringValue(model.version),
    artifactSha256: stringValue(model.artifact_sha256),
    codeSha256: stringValue(model.code_sha256),
    configSha256: stringValue(model.config_sha256),
    requestSha256: stringValue(provenance.request_sha256),
    responseSha256: stringValue(provenance.response_sha256),
    limitations: Array.isArray(provenance.limitations)
      ? provenance.limitations.filter((item): item is string => typeof item === "string")
      : [],
  };
};
