const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
let operatorApiKey = import.meta.env.VITE_OPERATOR_API_KEY || "";
let reviewerApiKey = import.meta.env.VITE_REVIEWER_API_KEY || "";

type AuthRole = "public" | "operator" | "reviewer";
export type AnalyzerName = "stub" | "demo_fixture" | "remote_http";

export class ApiRequestError extends Error {
  readonly status: number | null;
  readonly errorCode: string | null;

  constructor(message: string, status: number | null, errorCode: string | null = null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

export type Project = {
  id: string;
  code: string;
  name: string;
  location: string;
  manager: string | null;
  status: string;
  created_at: string;
};

export type Baseline = {
  id: string;
  project_id: string;
  site_id: string;
  procedure_code: string;
  version: string;
  sha256: string;
  created_at: string;
};

export type VerificationJob = {
  id: string;
  project_id: string;
  baseline_id: string;
  evidence_id: string;
  analyzer_name: string;
  analyzer_version: string;
  status: "queued" | "running" | "needs_review" | "sealing" | "approved" | "rejected" | "failed";
  progress: number;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type VerificationOperationsJobStatus = VerificationJob["status"] | "other";

export type VerificationAttemptDisposition =
  | "committed_success"
  | "committed_failure"
  | "lease_expired"
  | "lease_lost"
  | "write_fenced";

export type VerificationAttemptOutcome = {
  id: string;
  attempt_id: string;
  disposition: VerificationAttemptDisposition;
  stage: string | null;
  result_sha256: string | null;
  error_code: string | null;
  error_retryable: boolean | null;
  error_message: string | null;
  upstream_status: number | null;
  dead_lettered: boolean;
  finished_at: string;
};

export type VerificationExecutionAttempt = {
  id: string;
  job_id: string;
  generation: number;
  attempt_no: number;
  worker_ref: string;
  execution_mode: "inline" | "external";
  analyzer_name: string;
  analyzer_version: string;
  evidence_sha256: string;
  baseline_sha256: string;
  max_attempts: number;
  claimed_at: string;
  outcome: VerificationAttemptOutcome | null;
};

export type VerificationDetail = {
  job: VerificationJob;
  dispatch: {
    execution_mode: "inline" | "external";
    state: "unclaimed" | "leased" | "released" | "dead_letter";
    generation: number;
    attempt_count: number;
    max_attempts: number;
    heartbeat_at: string | null;
    lease_expires_at: string | null;
  };
  attempts: VerificationExecutionAttempt[];
  evidence: {
    id: string;
    original_name: string;
    content_type: string;
    size_bytes: number;
    sha256: string;
    metadata: Record<string, unknown>;
  };
  report: Report | null;
  proof: Proof | null;
  remediation_attempt: RemediationAttempt | null;
  recovery: {
    action: "none" | "retry_analysis" | "resume_sealing" | "integrity_review";
    retryable: boolean;
    reason: string;
    operation_state: string | null;
    attempt_count: number;
    last_error: string | null;
    updated_at: string | null;
  };
};

export type VerificationOperationsSnapshot = {
  status: "healthy" | "attention" | "incident";
  generated_at: string;
  execution_mode: "inline" | "external";
  thresholds: {
    queue_wait_warning_seconds: number;
    recent_window_seconds: number;
    lease_seconds: number;
    heartbeat_seconds: number;
  };
  jobs: {
    total: number;
    by_status: Partial<Record<VerificationOperationsJobStatus, number>>;
  };
  dispatch: {
    lease_rows: number;
    active_leases: number;
    expired_running_leases: number;
    unclaimed_queued_jobs: number;
    queued_over_warning_threshold: number;
    dead_letter_jobs: number;
    oldest_queued_seconds: number | null;
    oldest_active_heartbeat_seconds: number | null;
  };
  attempts: {
    total: number;
    open: number;
    outcomes_total_by_disposition: Record<VerificationAttemptDisposition, number>;
    outcomes_window_by_disposition: Record<VerificationAttemptDisposition, number>;
    recent_instability: number;
  };
  integrity: {
    status: "ok" | "incident";
    dispatch_issue_count: number;
    attempt_issue_count: number;
    issue_count: number;
  };
  alerts: Array<{
    severity: "warning" | "incident";
    code:
      | "INTEGRITY_INCIDENT"
      | "DEAD_LETTER_PRESENT"
      | "QUEUE_WAIT_EXCEEDED"
      | "RECENT_LEASE_INSTABILITY";
    count: number;
    message: string;
  }>;
  truth_note: string;
};

export type FindingCaseStatus =
  | "pending_triage"
  | "open"
  | "remediation_in_progress"
  | "verification_pending"
  | "closed"
  | "dismissed";

export type FindingCase = {
  id: string;
  project_id: string;
  source_job_id: string;
  source_evidence_id: string;
  baseline_id: string;
  finding_key: string;
  finding_index: number;
  finding_sha256: string;
  source_result_sha256: string;
  analyzer_name: string;
  analyzer_version: string;
  analysis_mode: string;
  source_synthetic: boolean;
  source_evidence_grade: boolean;
  finding_code: string;
  proposed_severity: "info" | "warning" | "error" | "critical";
  finding_message: string;
  scope: "operational" | "demo";
  status: FindingCaseStatus;
  confirmed_severity: "info" | "warning" | "error" | "critical" | null;
  decision_reason: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  assigned_to: string | null;
  due_at: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  closed_by: string | null;
  closed_at: string | null;
  closure_proof_id: string | null;
  active_attempt_no: number | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type FindingCaseSummary = {
  pending_triage: number;
  confirmed_open_operational: number;
  remediation_in_progress_operational: number;
  verification_pending_operational: number;
  closed_operational: number;
  dismissed_operational: number;
  demo_cases: number;
  truth_note: string;
};

export type RemediationAttempt = {
  id: string;
  case_id: string;
  attempt_no: number;
  client_request_id: string;
  action_description: string;
  submitted_by: string;
  submitted_at: string;
  verification_job_id: string | null;
  resolution_decision: "pending" | "resolved" | "not_resolved";
  resolution_note: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  report_id: string | null;
  proof_id: string | null;
  created_at: string;
};

export type FindingCaseCommand = {
  id: string;
  case_id: string;
  command: string;
  from_status: FindingCaseStatus;
  to_status: FindingCaseStatus;
  actor: string;
  actor_role: string;
  payload_sha256: string;
  result_version: number;
  created_at: string;
};

export type FindingCaseDetail = {
  case: FindingCase;
  attempts: RemediationAttempt[];
  history: FindingCaseCommand[];
  closure_evidence_status: "unsealed" | "sealed" | "invalid";
};

export type Report = {
  id: string;
  job_id: string;
  project_id: string;
  status: string;
  schema_version: string;
  sha256: string;
  html_sha256: string;
  created_at: string;
  content: Record<string, unknown>;
};

export type Proof = {
  id: string;
  archive_id: string;
  report_id: string;
  purpose: string;
  evidence_grade: boolean;
  merkle_root: string;
  manifest_sha256: string;
  archive_sha256: string;
  record_hash: string;
  previous_record_hash: string;
  ledger_index: number;
  created_at: string;
};

export type DashboardSummary = {
  projects: number;
  design_baselines: number;
  evidence_assets: number;
  jobs_by_status: Record<string, number>;
  reports: number;
  proof_archives: number;
  formal_evidence_archives: number;
  finding_cases: FindingCaseSummary;
  note: string;
};

export type ProjectProgress = {
  project_id: string;
  baseline_count: number;
  approved_baseline_count: number;
  pending_review_count: number;
  failed_or_rejected_count: number;
  completion_rate: number;
  metric_note: string;
};

export type IntegrityCheck = {
  valid: boolean;
  archive_id: string;
  checks: Record<string, boolean>;
  errors: string[];
};

export type CapabilityMeta = {
  service_version: string;
  implemented: string[];
  adapters: Record<string, { version: string; purpose: string; synthetic: boolean; enabled: boolean }>;
  database_schema: {
    mode: "create_all" | "upgrade" | "verify";
    expected_heads: string[];
    current_heads: string[];
    managed_by_alembic: boolean;
    at_head: boolean;
    drift_free: boolean;
    legacy_adopted: boolean;
  };
  verification_execution: {
    mode: "inline" | "external";
    queue: string;
    lease_seconds: number;
    heartbeat_seconds: number;
    max_attempts: number;
    queue_warning_seconds: number;
    observability_window_seconds: number;
    sqlite_external_scope: string;
  };
  truth_boundary: string[];
};

export type EvidenceContent = {
  blob: Blob;
  contentType: string;
  sizeBytes: number;
  contentRange: string | null;
};

export type EvidenceObjectUrl = Omit<EvidenceContent, "blob"> & {
  url: string;
  revoke: () => void;
};

// ---------------------------------------------------------------------------
// Alpha18: QGIS work-order compliance types
// ---------------------------------------------------------------------------

export type GeoJsonGeometry = {
  type: "Point" | "LineString" | "Polygon";
  coordinates: number[] | number[][] | number[][][];
};

export type DesignPackage = {
  id: string;
  project_id: string;
  package_code: string;
  source_filename: string;
  source_sha256: string;
  source_type: string;
  purpose: string;
  synthetic: boolean;
  source_crs_epsg: number;
  layers: Record<string, unknown>;
  field_mapping: Record<string, unknown>;
  redaction_policy: Record<string, unknown>;
  import_status: string;
  import_warnings: unknown[];
  object_count: number;
  imported_at: string | null;
  created_at: string;
};

export type EngineeringObject = {
  id: string;
  project_id: string;
  design_package_id: string;
  object_code: string;
  object_type: string;
  name: string;
  source_layer: string;
  source_feature_id: string;
  geometry_type: string;
  geometry_wgs84: GeoJsonGeometry;
  geometry_source_crs_epsg: number;
  attributes_snapshot: Record<string, unknown>;
  expected_rules: Record<string, unknown>;
  design_version: string;
  created_at: string;
};

export type WorkOrderStatus =
  | "draft"
  | "assigned"
  | "evidence_uploaded"
  | "analyzing"
  | "needs_review"
  | "approved"
  | "deviation"
  | "remediating"
  | "closed";

export type WorkOrder = {
  id: string;
  project_id: string;
  engineering_object_id: string;
  baseline_id: string | null;
  work_order_code: string;
  procedure_code: string;
  status: WorkOrderStatus | string;
  design_version: string;
  design_snapshot: Record<string, unknown>;
  geometry_snapshot: {
    geometry_type?: string;
    geometry_wgs84?: GeoJsonGeometry;
    geometry_source_crs_epsg?: number;
  };
  rules_snapshot: Record<string, unknown>;
  spatial_tolerance_m: number;
  gps_accuracy_threshold_m: number;
  assigned_to: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type SpatialCheckStatus = "passed" | "failed" | "skipped" | "unavailable";

export type EvidenceCapture = {
  id: string;
  project_id: string;
  work_order_id: string;
  evidence_id: string;
  verification_job_id: string | null;
  client_captured_at: string | null;
  server_received_at: string;
  latitude: number | null;
  longitude: number | null;
  accuracy_m: number | null;
  location_source: string;
  is_synthetic_location: boolean;
  distance_to_target_m: number | null;
  tolerance_m: number;
  gps_accuracy_threshold_m: number;
  spatial_check_status: SpatialCheckStatus | string;
  spatial_check_reason: string;
  created_at: string;
};

export type ComplianceEvaluation = {
  id: string;
  project_id: string;
  work_order_id: string;
  job_id: string;
  rule_version: string;
  engine_version: string;
  expected: Record<string, unknown>;
  observed: Record<string, unknown>;
  differences: unknown[];
  verdict: "compliant" | "deviation_detected" | "insufficient_evidence" | "needs_review" | string;
  spatial_check_status: string | null;
  notes: string | null;
  created_at: string;
};

export type DesignPackageImportResult = {
  package: DesignPackage;
  objects: EngineeringObject[];
  truth_note: string;
};

export type StandardGpkgLayerSummary = {
  name: string;
  accepted?: boolean;
  whitelisted?: boolean;
  feature_count?: number | null;
  resolved_epsg?: number | null;
  rejection_reasons?: string[];
};

export type StandardGpkgPreviewResult = {
  valid: boolean;
  preview_token: string | null;
  expires_at_unix: number | null;
  source_sha256: string;
  size_bytes: number;
  import_contract_version: string;
  package_code: string;
  staging_id: string;
  candidate_count: number;
  object_codes: string[];
  layers_summary: StandardGpkgLayerSummary[];
  errors: string[];
  warnings: string[];
  preflight_valid: boolean;
  normalize_valid: boolean;
  error_code?: string | null;
  source_classification?: string;
  truth_note: string;
};

export type StandardGpkgConfirmRequest = {
  package_code: string;
  staging_id: string;
  preview_token: string;
  design_version?: string;
};

export type StandardGpkgImportResult = {
  package: DesignPackage;
  objects: EngineeringObject[];
  idempotent: boolean;
  truth_note: string;
  source_classification?: string;
};

export type WorkOrderVerificationResult = {
  job: VerificationJob;
  capture: EvidenceCapture;
  work_order: WorkOrder;
  compliance: ComplianceEvaluation | null;
  truth_note: string;
};

export type ProjectGisSummary = {
  project_id: string;
  design_package_count: number;
  engineering_object_count: number;
  work_order_count: number;
  objects: EngineeringObject[];
  work_orders: WorkOrder[];
  truth_note: string;
};

export type LocationSource = "device_gps" | "synthetic_demo" | "manual" | "unknown";

const PREVIEWABLE_EVIDENCE_TYPES = new Set([
  "video/mp4",
  "video/quicktime",
  "video/x-msvideo",
  "video/x-matroska",
  "video/webm",
  "image/jpeg",
  "image/png",
]);

async function request<T>(path: string, init?: RequestInit, role: AuthRole = "operator"): Promise<T> {
  const headers = new Headers(init?.headers);
  const apiKey = role === "reviewer" ? reviewerApiKey : role === "operator" ? operatorApiKey : "";
  if (apiKey) headers.set("X-API-Key", apiKey);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : "网络连接失败";
    throw new ApiRequestError(message, null);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    let message = `请求失败 (${response.status})`;
    let errorCode: string | null = null;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { error_code?: unknown; message?: unknown; detail?: unknown };
      if (typeof d.error_code === "string") errorCode = d.error_code;
      if (typeof d.message === "string") message = d.message;
      else if (typeof d.detail === "string") message = d.detail;
      else message = JSON.stringify(detail);
    }
    // Never surface raw filesystem paths from servers
    message = message.replace(/[A-Za-z]:\\[^\s"']+/g, "[path]").replace(/\/(?:var|home|Users|tmp)\/[^\s"']+/g, "[path]");
    throw new ApiRequestError(message, response.status, errorCode);
  }
  return response.json() as Promise<T>;
}

async function download(path: string, filename: string): Promise<void> {
  const headers = new Headers();
  if (operatorApiKey) headers.set("X-API-Key", operatorApiKey);
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `下载失败 (${response.status})`);
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Keep the object URL alive until the browser has consumed the synthetic click.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function fetchEvidenceContent(
  evidenceId: string,
  options: { signal?: AbortSignal; range?: string } = {},
): Promise<EvidenceContent> {
  const headers = new Headers();
  if (operatorApiKey) headers.set("X-API-Key", operatorApiKey);
  if (options.range) headers.set("Range", options.range);
  const response = await fetch(
    `${API_BASE}/evidence-assets/${encodeURIComponent(evidenceId)}/content`,
    { headers, signal: options.signal, cache: "no-store" },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `原始证据读取失败 (${response.status})`,
    );
  }
  const blob = await response.blob();
  return {
    blob,
    contentType: response.headers.get("Content-Type") || blob.type || "application/octet-stream",
    sizeBytes: blob.size,
    contentRange: response.headers.get("Content-Range"),
  };
}

async function createEvidenceObjectUrl(
  evidenceId: string,
  options: { signal?: AbortSignal } = {},
): Promise<EvidenceObjectUrl> {
  const { blob, ...metadata } = await fetchEvidenceContent(evidenceId, options);
  if (!PREVIEWABLE_EVIDENCE_TYPES.has(metadata.contentType)) {
    throw new Error(`不允许预览的证据类型 (${metadata.contentType})`);
  }
  const url = URL.createObjectURL(blob);
  let revoked = false;
  return {
    ...metadata,
    url,
    revoke: () => {
      if (revoked) return;
      URL.revokeObjectURL(url);
      revoked = true;
    },
  };
}

export const api = {
  baseUrl: API_BASE,
  configureTokens: (tokens: { operator: string; reviewer: string }) => {
    operatorApiKey = tokens.operator.trim();
    reviewerApiKey = tokens.reviewer.trim();
  },
  health: () => request<{ status: string }>("/readyz", undefined, "public"),
  meta: () => request<CapabilityMeta>("/meta", undefined, "public"),
  verificationDispatchOperations: () =>
    request<VerificationOperationsSnapshot>("/operations/verification-dispatch"),
  dashboardSummary: () => request<DashboardSummary>("/dashboard/summary"),
  listProjects: () => request<Project[]>("/projects"),
  getProject: (projectId: string) => request<Project>(`/projects/${projectId}`),
  projectProgress: (projectId: string) => request<ProjectProgress>(`/projects/${projectId}/progress`),
  listBaselines: (projectId: string) => request<Baseline[]>(`/projects/${projectId}/baselines`),
  listVerifications: (projectId?: string) =>
    request<VerificationJob[]>(`/verifications${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  listReports: (projectId?: string) =>
    request<Report[]>(`/reports${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  listProofs: (fingerprint?: string) =>
    request<Proof[]>(`/proofs${fingerprint ? `?fingerprint=${encodeURIComponent(fingerprint)}` : ""}`),
  createProject: (payload: { code: string; name: string; location: string; manager?: string }) =>
    request<Project>("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  createBaseline: (projectId: string, payload: Record<string, unknown>) =>
    request<Baseline>(`/projects/${projectId}/baselines`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  uploadVerification: (payload: {
    projectId: string;
    baselineId: string;
    file: File;
    analyzer: AnalyzerName;
    remediationAttemptId?: string;
    deviceId?: string;
    metadata?: Record<string, unknown>;
  }) => {
    const data = new FormData();
    data.set("project_id", payload.projectId);
    data.set("baseline_id", payload.baselineId);
    data.set("analyzer", payload.analyzer);
    data.set("device_id", payload.deviceId || "WEB-UPLOAD-01");
    data.set(
      "metadata",
      JSON.stringify(payload.metadata || { source: "backend-workflow-page", privacy: "user-provided" }),
    );
    if (payload.remediationAttemptId?.trim()) {
      data.set("remediation_attempt_id", payload.remediationAttemptId.trim());
    }
    data.set("file", payload.file);
    return request<VerificationJob>("/verifications", { method: "POST", body: data });
  },
  verification: (jobId: string) => request<VerificationDetail>(`/verifications/${jobId}`),
  retryVerification: (jobId: string) =>
    request<VerificationJob>(`/verifications/${jobId}/retry`, { method: "POST" }),
  review: (jobId: string, payload: {
    decision: "approve" | "reject";
    reviewer: string;
    note: string;
    remediation_resolution?: "resolved" | "not_resolved";
  }) =>
    request<{ job: VerificationJob; report: Report | null; proof: Proof | null }>(`/verifications/${jobId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }, "reviewer"),
  listFindingCases: (filters: { projectId?: string; status?: FindingCaseStatus; scope?: "operational" | "demo" } = {}) => {
    const params = new URLSearchParams();
    if (filters.projectId) params.set("project_id", filters.projectId);
    if (filters.status) params.set("status", filters.status);
    if (filters.scope) params.set("scope", filters.scope);
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<FindingCase[]>(`/finding-cases${suffix}`);
  },
  findingCaseSummary: (projectId?: string) =>
    request<FindingCaseSummary>(
      `/finding-cases/summary${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),
  findingCase: (caseId: string) => request<FindingCaseDetail>(`/finding-cases/${caseId}`),
  triageFindingCase: (caseId: string, payload: {
    request_id: string;
    expected_version: number;
    decision: "confirm" | "dismiss";
    confirmed_severity?: "info" | "warning" | "error" | "critical";
    reason: string;
  }) => request<FindingCase>(`/finding-cases/${caseId}/triage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, "reviewer"),
  startRemediation: (caseId: string, payload: {
    request_id: string;
    expected_version: number;
    assignee: string;
    action_description: string;
    due_at?: string;
  }) => request<FindingCase>(`/finding-cases/${caseId}/start-remediation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  createRemediationAttempt: (caseId: string, payload: {
    client_request_id: string;
    expected_version: number;
    action_description: string;
  }) => request<RemediationAttempt>(`/finding-cases/${caseId}/remediation-attempts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  verifyProof: (proofId: string) => request<IntegrityCheck>(`/proofs/${proofId}/verify`),
  fetchEvidenceContent,
  createEvidenceObjectUrl,
  downloadReport: (reportId: string, format: "json" | "html") =>
    download(`/reports/${reportId}/download?format=${format}`, `report-${reportId}.${format}`),
  downloadArchive: (proofId: string) => download(`/proofs/${proofId}/archive`, `evidence-${proofId}.zip`),

  // Alpha18 work-order / GIS
  projectGisSummary: (projectId: string) =>
    request<ProjectGisSummary>(`/projects/${encodeURIComponent(projectId)}/gis-summary`),
  listDesignPackages: (projectId: string) =>
    request<DesignPackage[]>(`/projects/${encodeURIComponent(projectId)}/design-packages`),
  listEngineeringObjects: (projectId: string, objectType?: string) => {
    const params = new URLSearchParams();
    if (objectType) params.set("object_type", objectType);
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<EngineeringObject[]>(
      `/projects/${encodeURIComponent(projectId)}/engineering-objects${suffix}`,
    );
  },
  getEngineeringObject: (objectId: string) =>
    request<EngineeringObject>(`/engineering-objects/${encodeURIComponent(objectId)}`),
  listWorkOrders: (projectId: string, status?: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<WorkOrder[]>(`/projects/${encodeURIComponent(projectId)}/work-orders${suffix}`);
  },
  getWorkOrder: (workOrderId: string) =>
    request<WorkOrder>(`/work-orders/${encodeURIComponent(workOrderId)}`),
  listWorkOrderCaptures: (workOrderId: string) =>
    request<EvidenceCapture[]>(`/work-orders/${encodeURIComponent(workOrderId)}/captures`),
  getCompliance: (jobId: string) =>
    request<ComplianceEvaluation>(`/verifications/${encodeURIComponent(jobId)}/compliance`),
  importDesignPackageJson: (projectId: string, file: File) => {
    const data = new FormData();
    data.set("file", file);
    return request<DesignPackageImportResult>(
      `/projects/${encodeURIComponent(projectId)}/design-packages/import-json`,
      { method: "POST", body: data },
    );
  },
  previewStandardGpkg: (projectId: string, packageCode: string, file: File) => {
    const data = new FormData();
    data.set("file", file);
    data.set("package_code", packageCode);
    // Do not set Content-Type manually — browser must add multipart boundary.
    return request<StandardGpkgPreviewResult>(
      `/projects/${encodeURIComponent(projectId)}/design-packages/standard-gpkg/preview`,
      { method: "POST", body: data },
    );
  },
  confirmStandardGpkg: (projectId: string, payload: StandardGpkgConfirmRequest) =>
    request<StandardGpkgImportResult>(
      `/projects/${encodeURIComponent(projectId)}/design-packages/standard-gpkg/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
  createWorkOrder: (
    projectId: string,
    payload: {
      engineering_object_id: string;
      work_order_code: string;
      procedure_code?: string;
      spatial_tolerance_m?: number;
      gps_accuracy_threshold_m?: number;
      assigned_to?: string;
      notes?: string;
    },
  ) =>
    request<WorkOrder>(`/projects/${encodeURIComponent(projectId)}/work-orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  // Backend keeps a new work order in `draft`; evidence upload requires `assigned`.
  assignWorkOrder: (workOrderId: string, assignee: string) =>
    request<WorkOrder>(`/work-orders/${encodeURIComponent(workOrderId)}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assigned_to: assignee }),
    }),
  uploadWorkOrderVerification: (payload: {
    workOrderId: string;
    file: File;
    analyzer?: AnalyzerName;
    latitude?: number | null;
    longitude?: number | null;
    accuracy_m?: number | null;
    location_source?: LocationSource;
    is_synthetic_location?: boolean;
    client_captured_at?: string | null;
    device_id?: string;
    metadata?: Record<string, unknown>;
  }) => {
    const data = new FormData();
    data.set("analyzer", payload.analyzer || "demo_fixture");
    data.set("location_source", payload.location_source || "unknown");
    data.set("is_synthetic_location", payload.is_synthetic_location ? "true" : "false");
    if (payload.latitude != null && Number.isFinite(payload.latitude)) {
      data.set("latitude", String(payload.latitude));
    }
    if (payload.longitude != null && Number.isFinite(payload.longitude)) {
      data.set("longitude", String(payload.longitude));
    }
    if (payload.accuracy_m != null && Number.isFinite(payload.accuracy_m)) {
      data.set("accuracy_m", String(payload.accuracy_m));
    }
    if (payload.client_captured_at) data.set("client_captured_at", payload.client_captured_at);
    if (payload.device_id) data.set("device_id", payload.device_id);
    data.set(
      "metadata",
      JSON.stringify(payload.metadata || { source: "work-order-page", privacy: "user-provided" }),
    );
    data.set("file", payload.file);
    return request<WorkOrderVerificationResult>(
      `/work-orders/${encodeURIComponent(payload.workOrderId)}/verifications`,
      { method: "POST", body: data },
    );
  },
};
