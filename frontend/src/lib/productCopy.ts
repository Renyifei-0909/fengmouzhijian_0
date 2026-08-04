/**
 * Product-facing copy and status labels for 锋眸智鉴.
 * API enums remain English; only display strings are mapped here.
 */

export const PRODUCT = {
  name: "锋眸智鉴",
  subtitle: "通信工程施工合规管理平台",
  defaultPageTitle: "锋眸智鉴",
} as const;

/** Primary commercial navigation (routes still point at existing pages). */
export const PRIMARY_NAV = [
  { label: "项目总览", path: "/dashboard" },
  { label: "项目管理", path: "/projects" },
  { label: "工程作业", path: "/gis-map" },
  { label: "核验中心", path: "/backend-workflow" },
  { label: "整改中心", path: "/alarms" },
  { label: "报告中心", path: "/reports" },
  { label: "审计追溯", path: "/traceability" },
] as const;

export const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/dashboard": { title: "项目总览", subtitle: "工程进度与核验概况" },
  "/projects": { title: "项目管理", subtitle: "在建工程与工点管理" },
  "/gis-map": { title: "工程作业", subtitle: "工程对象、施工工单与现场核验" },
  "/backend-workflow": { title: "核验中心", subtitle: "资料提交、人工复核与交付归档" },
  "/alarms": { title: "整改中心", subtitle: "偏差分诊、整改与复验" },
  "/reports": { title: "报告中心", subtitle: "结构化报告与交付材料" },
  "/traceability": { title: "审计追溯", subtitle: "档案核验与链路查询" },
  "/ai-verification": { title: "隐蔽验真", subtitle: "影像结构化能力（内部入口）" },
  "/devices": { title: "设备监控", subtitle: "感知设备（内部入口）" },
  "/analytics": { title: "数据分析", subtitle: "指标分析（内部入口）" },
  "/data-cockpit": { title: "数据看板", subtitle: "运营看板（内部入口）" },
  "/model-service": { title: "模型服务", subtitle: "模型编排（内部入口）" },
  "/settings/account": { title: "账户设置", subtitle: "账户信息（内部入口）" },
  "/settings/system": { title: "系统设置", subtitle: "系统配置（内部入口）" },
};

export const WORK_ORDER_STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  assigned: "已派发",
  evidence_uploaded: "已提交资料",
  analyzing: "核验中",
  needs_review: "待复核",
  approved: "已通过",
  deviation: "存在偏差",
  remediating: "整改中",
  closed: "已关闭",
};

export const OBJECT_TYPE_LABEL: Record<string, string> = {
  pipe_route: "管线路由",
  trench: "沟槽",
  infrastructure_point: "设施点",
};

export const IMPORT_STATUS_LABEL: Record<string, string> = {
  completed: "导入完成",
  partial: "部分导入",
  failed: "导入失败",
  pending: "正在处理",
};

export const SPATIAL_STATUS_LABEL: Record<string, string> = {
  passed: "位置符合",
  failed: "位置异常",
  unavailable: "无法核验",
  skipped: "未执行",
};

export const COMPLIANCE_VERDICT_LABEL: Record<string, string> = {
  compliant: "符合要求",
  deviation_detected: "发现偏差",
  insufficient_evidence: "资料不足",
  needs_review: "需要复核",
};

export const LOCATION_SOURCE_LABEL: Record<string, string> = {
  device_gps: "设备定位",
  synthetic_demo: "样例位置",
  manual: "手工录入",
  unknown: "未知来源",
};

export const JOB_STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "核验中",
  needs_review: "待复核",
  sealing: "归档中",
  approved: "已通过",
  rejected: "已驳回",
  failed: "处理失败",
};

export function labelWorkOrderStatus(status: string): string {
  return WORK_ORDER_STATUS_LABEL[status] || status;
}

export function labelObjectType(type: string): string {
  return OBJECT_TYPE_LABEL[type] || type;
}

export function labelImportStatus(status: string): string {
  return IMPORT_STATUS_LABEL[status] || status;
}

export function labelSpatialStatus(status: string): string {
  return SPATIAL_STATUS_LABEL[status] || status;
}

export function labelComplianceVerdict(verdict: string): string {
  return COMPLIANCE_VERDICT_LABEL[verdict] || verdict;
}

export function labelLocationSource(source: string): string {
  return LOCATION_SOURCE_LABEL[source] || source;
}

export function labelJobStatus(status: string): string {
  return JOB_STATUS_LABEL[status] || status;
}

/** Friendly error for operators; raw backend text can go in technical details. */
export const COMPLIANCE_BUSINESS_NOTE =
  "合规结论依据工单冻结的设计要求和服务端规则生成，识别结果不直接决定最终结论。";

/** Preferred commercial sample project code for default selection. */
export const COMMERCIAL_SAMPLE_PROJECT_CODE = "ALPHA18-COMMERCIAL";

export function friendlyApiError(message: string): string {
  const m = message.trim();
  if (!m) return "操作未能完成，请稍后重试。";
  if (/network|fetch|failed to fetch|连接/i.test(m)) {
    return "无法连接服务，请检查网络后重试。";
  }
  if (/401|403|api key|未授权|鉴权/i.test(m)) {
    return "当前无操作权限或凭证无效，请联系管理员。";
  }
  if (/413|too large|exceeds max/i.test(m)) {
    return "上传文件过大，请压缩后重试。";
  }
  if (/422|invalid|json|synthetic/i.test(m)) {
    return "提交内容不符合要求，请核对后重试。";
  }
  // Keep concise backend business messages when already Chinese.
  if (/[\u4e00-\u9fff]/.test(m) && m.length <= 160) return m;
  return "操作未能完成，请在技术详情中查看具体原因。";
}

export const COPY = {
  identityRole: "项目管理员",
  identityOrg: "系统管理",

  engineeringIntro: "统一管理工程设计数据、施工工单与现场核验记录。",
  dataNoteTitle: "数据说明",
  techDetails: "技术详情",
  viewImportNotes: "查看导入说明",
  partialImportExplain: "检测到非白名单图层，相关数据已跳过，未进入工程对象快照。",

  designData: "工程数据",
  importDesignData: "导入工程数据",
  importHelp:
    "默认通过标准 GeoPackage 导入：先数据预检，再确认写入。预检通过不等于导入完成；格式校验不等于数据来源已获授权。",
  sampleDesignData: "样例数据",
  refresh: "刷新",
  gpkgImportTitle: "导入工程数据",
  gpkgSelectFile: "选择 GeoPackage 文件",
  gpkgPackageCode: "设计包编号",
  gpkgRunPrecheck: "上传并预检",
  gpkgConfirmImport: "确认导入",
  gpkgReupload: "重新上传",
  gpkgPreviewReady: "预检完成，尚未写入",
  gpkgImportRecords: "导入记录",
  gpkgDataSource: "数据来源",
  gpkgCompatibleFormats: "兼容格式",
  gpkgJsonCompatHelp: "JSON 设计包为兼容入口，不作为默认主操作。",
  gpkgTokenExpired: "预检凭证已过期，请重新上传并预检。",
  gpkgBlocked: "预检未通过，无法确认导入。",
  gpkgConfirming: "正在确认导入…",
  gpkgUploading: "正在上传并预检…",
  gpkgSourceClassSample: "样例 / 未核验来源",
  gpkgTruthBoundary:
    "预检通过不等于导入完成；格式校验不等于来源已授权；位置核验不构成绝对防作弊；单张照片不能提供工程级精确测深；当前无正式 85%/90% 指标。",

  statsDesign: "设计数据",
  statsObjects: "工程对象",
  statsWorkOrders: "施工工单",

  objectCode: "对象编号",
  objectType: "对象类型",
  designVersion: "设计版本",
  sourceLayer: "来源图层",
  designRequirements: "设计要求",
  engineeringAttrs: "工程属性",

  mapTitle: "工程空间分布",
  mapFooter: "位置与几何来源于当前设计数据。",

  workOrders: "施工工单",
  createWorkOrder: "创建工单",
  fieldWorkOrderCode: "工单编号",
  fieldProcedure: "施工工序",
  fieldSpatialTolerance: "位置容差",
  fieldGpsAccuracy: "定位精度要求",
  fieldAssignee: "负责人",
  fieldNotes: "备注",
  unitMeters: "米",

  fieldCapture: "现场资料",
  captureMode: "采集方式",
  useSampleLocation: "使用样例位置",
  useSampleLocationHelp: "仅用于系统试用，不代表现场定位。",
  fieldMedia: "现场照片或视频",
  submitVerification: "提交核验",
  advancedSettings: "高级设置",
  testConfig: "测试配置",
  analyzerHelp: "普通现场流程由系统配置分析方式，无需手动选择。",

  spatialCheck: "位置核验",
  distanceToObject: "距工程对象",
  allowedRange: "允许范围",
  reportedAccuracy: "定位精度",
  accuracyRequirement: "精度要求",
  captureTime: "采集时间",
  serverReceived: "服务接收时间",
  spatialReason: "核验说明",
  spatialDisclaimer: "位置核验用于判断采集位置合理性，不构成绝对防作弊证明。",

  observations: "识别结果",
  resultSource: "结果来源",
  testAnalyzer: "测试分析器",
  testAnalyzerNote: "当前结果用于验证业务流程，不计入正式算法指标。",
  complianceJudgement: "合规判定",
  judgementConclusion: "规则初判",
  ruleVersion: "规则版本",
  engineVersion: "判定引擎",
  compliantItems: "符合项",
  deviationItems: "偏差项",
  pendingItems: "待补充资料",
  judgementBasis: "判定依据",
  goVerificationCenter: "前往核验中心",

  verificationHistory: "核验记录",
  submittedAt: "提交时间",
  locationStatus: "空间核验",
  /** @deprecated prefer ruleVerdictStatus / humanReviewStatus / taskProgress */
  verificationStatus: "任务进度",
  judgementStatus: "规则初判",
  ruleVerdictStatus: "规则初判",
  humanReviewStatus: "人工复核",
  taskProgress: "任务进度",
  dataSource: "资料来源",
  owner: "负责人",

  sampleDataBadge: "样例数据",
  frozenSnapshots: "设计与规则快照",
  selectObjectFirst: "请先选择工程对象",
  noPackages: "暂无设计数据，请先导入。",
  noObjects: "暂无工程对象",
  noWorkOrders: "暂无施工工单",
  emptyProjects: "暂无项目，请先在项目管理中创建。",
  loadFailed: "无法加载数据",
  projectSelect: "项目选择",
  projectCode: "项目编号",
  allStatuses: "全部状态",
  independentWorkOrderPage: "工单详情",
} as const;
