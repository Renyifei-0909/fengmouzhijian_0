import { describe, expect, it } from "vitest";
import { ApiRequestError, WorkOrder } from "./api";
import {
  classifyWorkerError,
  distanceToGeometryMeters,
  matchesWorkerAssignment,
  spatialEvidenceEligibility,
  workOrderBucket,
  workOrderDeadline,
} from "./workerDomain";

describe("worker work-order rules", () => {
  it("maps backend states to worker task groups", () => {
    expect(workOrderBucket("assigned")).toBe("pending");
    expect(workOrderBucket("analyzing")).toBe("processing");
    expect(workOrderBucket("needs_review")).toBe("supplement");
    expect(workOrderBucket("remediating")).toBe("remediation");
    expect(workOrderBucket("closed")).toBe("completed");
  });

  it("matches exact personal or team assignments", () => {
    expect(matchesWorkerAssignment("worker-01", "worker-01", ["team-a"])).toBe(true);
    expect(matchesWorkerAssignment("team-a", "worker-01", ["team-a"])).toBe(true);
    expect(matchesWorkerAssignment("team-ab", "worker-01", ["team-a"])).toBe(false);
    expect(matchesWorkerAssignment(null, "worker-01", ["team-a"])).toBe(false);
  });

  it("requires both frozen distance and accuracy thresholds", () => {
    expect(spatialEvidenceEligibility({
      locationSource: "device_gps",
      synthetic: false,
      distanceM: 9.9,
      toleranceM: 10,
      accuracyM: 4.9,
      accuracyThresholdM: 5,
    }).formal).toBe(true);

    const inaccurate = spatialEvidenceEligibility({
      locationSource: "device_gps",
      synthetic: false,
      distanceM: 9.9,
      toleranceM: 10,
      accuracyM: 25,
      accuracyThresholdM: 5,
    });
    expect(inaccurate.formal).toBe(false);
    expect(inaccurate.distancePass).toBe(true);
    expect(inaccurate.accuracyPass).toBe(false);

    const tooFar = spatialEvidenceEligibility({
      locationSource: "device_gps",
      synthetic: false,
      distanceM: 12,
      toleranceM: 10,
      accuracyM: 2,
      accuracyThresholdM: 5,
    });
    expect(tooFar.formal).toBe(false);
    expect(tooFar.distancePass).toBe(false);
    expect(tooFar.accuracyPass).toBe(true);
  });

  it("never treats manual location as formal evidence", () => {
    const result = spatialEvidenceEligibility({
      locationSource: "manual",
      synthetic: false,
      distanceM: 0,
      toleranceM: 10,
      accuracyM: 0,
      accuracyThresholdM: 5,
    });
    expect(result.formal).toBe(false);
    expect(result.message).toBe("无法作为正式空间核验证据");
  });

  it("computes distance to points, line segments and polygon interiors", () => {
    expect(distanceToGeometryMeters(30, 114, { type: "Point", coordinates: [114, 30] })).toBe(0);
    expect(distanceToGeometryMeters(30, 114.005, {
      type: "LineString",
      coordinates: [[114, 30], [114.01, 30]],
    })).toBeLessThan(0.01);
    expect(distanceToGeometryMeters(30.005, 114.005, {
      type: "Polygon",
      coordinates: [[[114, 30], [114.01, 30], [114.01, 30.01], [114, 30.01], [114, 30]]],
    })).toBe(0);
  });

  it("reads only valid deadline fields", () => {
    const workOrder = {
      rules_snapshot: { due_at: "2026-08-20T12:00:00+08:00" },
      design_snapshot: {},
    } as unknown as WorkOrder;
    expect(workOrderDeadline(workOrder)).toBe("2026-08-20T04:00:00.000Z");
  });

  it("distinguishes permission, format, task and network errors", () => {
    expect(classifyWorkerError(new ApiRequestError("forbidden", 403)).kind).toBe("permission");
    expect(classifyWorkerError(new ApiRequestError("invalid", 422)).kind).toBe("format");
    expect(classifyWorkerError(new ApiRequestError("conflict", 409)).kind).toBe("task");
    expect(classifyWorkerError(new ApiRequestError("Failed to fetch", null)).kind).toBe("network");
  });
});
