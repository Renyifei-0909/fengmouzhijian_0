import { describe, expect, it } from "vitest";
import {
  COMPLIANCE_BUSINESS_NOTE,
  WORK_ORDER_STATUS_LABEL,
  labelComplianceVerdict,
  labelSpatialStatus,
  labelWorkOrderStatus,
} from "./productCopy";
import {
  associateCaptureBundle,
  groupDifferenceRows,
  isKnownDomainField,
  labelDomainField,
  labelHumanReviewStatus,
  labelRuleVerdict,
  labelTaskProgress,
  pickDefaultCaptureId,
  spatialBusinessSummary,
  structureObservations,
} from "./verificationDisplay";

describe("status Chinese labels", () => {
  it("maps work order statuses", () => {
    expect(labelWorkOrderStatus("assigned")).toBe("已派发");
    expect(labelWorkOrderStatus("needs_review")).toBe("待复核");
    expect(WORK_ORDER_STATUS_LABEL.evidence_uploaded).toBe("已提交资料");
  });

  it("maps spatial and compliance statuses", () => {
    expect(labelSpatialStatus("passed")).toBe("位置符合");
    expect(labelSpatialStatus("failed")).toBe("位置异常");
    expect(labelSpatialStatus("unavailable")).toBe("无法核验");
    expect(labelComplianceVerdict("compliant")).toBe("符合要求");
    expect(labelComplianceVerdict("insufficient_evidence")).toBe("资料不足");
  });
});

describe("spatialBusinessSummary", () => {
  it("summarizes passed without English reason", () => {
    const text = spatialBusinessSummary({
      spatial_check_status: "passed",
      latitude: 51.4,
      longitude: 7.5,
      accuracy_m: 8,
      gps_accuracy_threshold_m: 30,
      distance_to_target_m: 0,
      tolerance_m: 80,
    });
    expect(text).toContain("允许范围内");
    expect(text.toLowerCase()).not.toContain("distance");
    expect(text.toLowerCase()).not.toContain("tolerance");
  });

  it("summarizes failed", () => {
    expect(
      spatialBusinessSummary({
        spatial_check_status: "failed",
        latitude: 1,
        longitude: 1,
        accuracy_m: 5,
        gps_accuracy_threshold_m: 30,
        distance_to_target_m: 1000,
        tolerance_m: 10,
      }),
    ).toContain("超出工单允许范围");
  });

  it("summarizes accuracy threshold failure", () => {
    expect(
      spatialBusinessSummary({
        spatial_check_status: "unavailable",
        latitude: 51.4,
        longitude: 7.5,
        accuracy_m: 100,
        gps_accuracy_threshold_m: 30,
      }),
    ).toContain("定位精度未满足");
  });

  it("summarizes missing coordinates", () => {
    expect(
      spatialBusinessSummary({
        spatial_check_status: "unavailable",
        latitude: null,
        longitude: null,
      }),
    ).toContain("未获取有效位置信息");
  });

  it("summarizes skipped", () => {
    expect(spatialBusinessSummary({ spatial_check_status: "skipped" })).toContain("未执行");
  });
});

describe("structureObservations", () => {
  it("maps known fields and values", () => {
    const result = structureObservations({
      visible_pipe_count: 4,
      trench_stage: "laying",
      object_visibility: "visible",
      visible_material_or_specification: "PE110",
      spacing_m: null,
      mystery_field: 1,
    });
    expect(result.known.map((k) => k.label)).toContain("可见管线数量");
    expect(result.known.find((k) => k.key === "trench_stage")?.displayValue).toBe("敷设阶段");
    expect(result.known.find((k) => k.key === "object_visibility")?.displayValue).toBe("清晰可见");
    expect(result.known.find((k) => k.key === "spacing_m")?.displayValue).toBe("未识别");
    expect(result.unknown.some((u) => u.key === "mystery_field")).toBe(true);
  });

  it("handles empty measurements", () => {
    expect(structureObservations(null).known).toEqual([]);
  });
});

describe("capture selection association", () => {
  const captures = [
    {
      id: "cap-pass",
      verification_job_id: "job-pass",
      server_received_at: "2026-08-01T10:00:00Z",
    },
    {
      id: "cap-fail",
      verification_job_id: "job-fail",
      server_received_at: "2026-08-01T11:00:00Z",
    },
    {
      id: "cap-unavail",
      verification_job_id: "job-unavail",
      server_received_at: "2026-08-01T12:00:00Z",
    },
  ];

  it("defaults to latest capture", () => {
    expect(pickDefaultCaptureId(captures)).toBe("cap-unavail");
  });

  it("associates each capture only with its own job/evaluation", () => {
    const jobs = {
      "job-pass": { job: { id: "job-pass", status: "needs_review" } },
      "job-fail": { job: { id: "job-fail", status: "needs_review" } },
      "job-unavail": { job: { id: "job-unavail", status: "needs_review" } },
    };
    const comps = {
      "job-pass": { job_id: "job-pass", verdict: "compliant" },
      "job-fail": { job_id: "job-fail", verdict: "needs_review" },
      "job-unavail": { job_id: "job-unavail", verdict: "insufficient_evidence" },
    };

    const a = associateCaptureBundle(captures[0], jobs, comps);
    const b = associateCaptureBundle(captures[1], jobs, comps);
    const c = associateCaptureBundle(captures[2], jobs, comps);

    expect(a.jobId).toBe("job-pass");
    expect((a.compliance as { verdict: string }).verdict).toBe("compliant");
    expect(b.jobId).toBe("job-fail");
    expect((b.compliance as { verdict: string }).verdict).toBe("needs_review");
    expect(c.jobId).toBe("job-unavail");
    expect((c.compliance as { verdict: string }).verdict).toBe("insufficient_evidence");

    // No cross-mix: capture A never gets evaluation C
    expect((a.compliance as { job_id: string }).job_id).toBe("job-pass");
  });

  it("handles missing evaluation without borrowing another capture", () => {
    const jobs = { "job-pass": { job: { id: "job-pass" } } };
    const comps = { "job-pass": null };
    const bundle = associateCaptureBundle(captures[0], jobs, comps);
    expect(bundle.detail).not.toBeNull();
    expect(bundle.compliance).toBeNull();
    expect(bundle.complianceMissing).toBe(true);
  });

  it("handles capture without job", () => {
    const bundle = associateCaptureBundle(
      { id: "cap-x", verification_job_id: null },
      {},
      {},
    );
    expect(bundle.jobMissing).toBe(true);
    expect(bundle.detail).toBeNull();
    expect(bundle.compliance).toBeNull();
  });

  it("documents race-safe selection semantics with sequence numbers", () => {
    // Simulate: request 1 starts for cap-pass, request 2 for cap-fail finishes first,
    // then request 1 finishes — only latest sequence should apply.
    let applied: string | null = null;
    let seq = 0;
    const apply = (captureId: string, requestSeq: number) => {
      if (requestSeq !== seq) return;
      applied = captureId;
    };
    const s1 = ++seq;
    const s2 = ++seq;
    apply("cap-fail", s2); // later selection completes first
    apply("cap-pass", s1); // stale older request must not override
    expect(applied).toBe("cap-fail");
  });
});

describe("compliance business note", () => {
  it("uses Chinese product note without backend English phrase", () => {
    expect(COMPLIANCE_BUSINESS_NOTE).toContain("工单冻结");
    expect(COMPLIANCE_BUSINESS_NOTE.toLowerCase()).not.toContain("backend");
    expect(COMPLIANCE_BUSINESS_NOTE.toLowerCase()).not.toContain("adapter");
  });
});

describe("labelDomainField / groupDifferenceRows", () => {
  it("maps all known difference fields to Chinese", () => {
    const keys = [
      "spatial_check",
      "object_visibility",
      "visible_pipe_count",
      "trench_stage",
      "visible_material_or_specification",
      "separation",
      "count",
      "specification",
    ];
    for (const key of keys) {
      expect(isKnownDomainField(key)).toBe(true);
      const label = labelDomainField(key);
      expect(label).not.toBe(key);
      expect(/[a-z_]/i.test(label)).toBe(false);
    }
  });

  it("does not leak unknown internal keys on primary UI", () => {
    expect(labelDomainField("foo_bar_internal")).toBe("其他核验项");
    expect(labelDomainField("")).toBe("其他核验项");
    expect(labelDomainField(null)).toBe("其他核验项");
  });

  it("keeps raw keys in technical rawFields while UI lists stay Chinese", () => {
    const buckets = groupDifferenceRows([
      { field: "object_visibility", status: "compliant" },
      { field: "visible_pipe_count", status: "compliant" },
      { field: "spatial_check", status: "needs_review" },
      { field: "weird_internal_key", status: "deviation_detected" },
    ]);
    expect(buckets.ok).toEqual(["工程对象可见性", "可见管线数量"]);
    expect(buckets.pending).toContain("空间位置核验");
    expect(buckets.bad).toEqual(["其他核验项"]);
    expect(buckets.rawFields.some((r) => r.field === "weird_internal_key")).toBe(true);
    expect(buckets.rawFields.find((r) => r.field === "object_visibility")?.label).toBe(
      "工程对象可见性",
    );
    for (const label of [...buckets.ok, ...buckets.bad, ...buckets.pending]) {
      expect(label).not.toMatch(/object_visibility|visible_pipe_count|spatial_check|weird_/);
    }
  });
});

describe("three status semantics", () => {
  it("separates rule verdict from human review wording", () => {
    expect(labelRuleVerdict("compliant")).toBe("符合要求");
    expect(labelRuleVerdict("needs_review")).toBe("需要复核");
    expect(labelRuleVerdict("insufficient_evidence")).toBe("资料不足");
    expect(labelHumanReviewStatus("needs_review")).toBe("待复核");
    expect(labelHumanReviewStatus("approved")).toBe("已通过");
    expect(labelHumanReviewStatus("rejected")).toBe("已退回");
    // Same backend token, different presentation channels:
    expect(labelRuleVerdict("needs_review")).not.toBe(labelHumanReviewStatus("needs_review"));
  });

  it("task progress is distinct from rule verdict", () => {
    expect(labelTaskProgress("needs_review")).toBe("等待人工复核");
    expect(labelTaskProgress("running")).toBe("分析中");
    expect(labelTaskProgress("queued")).toBe("排队中");
  });
});
