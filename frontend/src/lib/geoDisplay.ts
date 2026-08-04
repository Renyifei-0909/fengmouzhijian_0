/** Lightweight WGS84 → SVG helpers for work-order geometry previews (not a map engine). */

import type { GeoJsonGeometry } from "./api";

export type LonLat = { lon: number; lat: number };

export type MapBounds = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

export function collectLonLats(geometry: GeoJsonGeometry | undefined | null): LonLat[] {
  if (!geometry || !geometry.coordinates) return [];
  const out: LonLat[] = [];
  const walk = (node: unknown): void => {
    if (!Array.isArray(node) || node.length === 0) return;
    if (typeof node[0] === "number" && typeof node[1] === "number") {
      out.push({ lon: Number(node[0]), lat: Number(node[1]) });
      return;
    }
    for (const child of node) walk(child);
  };
  walk(geometry.coordinates);
  return out;
}

export function boundsFromPoints(points: LonLat[], padRatio = 0.12): MapBounds | null {
  if (!points.length) return null;
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  for (const p of points) {
    minLon = Math.min(minLon, p.lon);
    maxLon = Math.max(maxLon, p.lon);
    minLat = Math.min(minLat, p.lat);
    maxLat = Math.max(maxLat, p.lat);
  }
  if (!Number.isFinite(minLon) || !Number.isFinite(minLat)) return null;
  const dLon = Math.max(maxLon - minLon, 1e-5);
  const dLat = Math.max(maxLat - minLat, 1e-5);
  const padLon = dLon * padRatio;
  const padLat = dLat * padRatio;
  return {
    minLon: minLon - padLon,
    maxLon: maxLon + padLon,
    minLat: minLat - padLat,
    maxLat: maxLat + padLat,
  };
}

export function projectToSvg(
  lon: number,
  lat: number,
  bounds: MapBounds,
  width: number,
  height: number,
  margin = 16,
): { x: number; y: number } {
  const x =
    margin +
    ((lon - bounds.minLon) / Math.max(bounds.maxLon - bounds.minLon, 1e-9)) * (width - margin * 2);
  // SVG y increases downward; invert latitude.
  const y =
    margin +
    (1 - (lat - bounds.minLat) / Math.max(bounds.maxLat - bounds.minLat, 1e-9)) *
      (height - margin * 2);
  return { x, y };
}

export function geometryToSvgPath(
  geometry: GeoJsonGeometry,
  bounds: MapBounds,
  width: number,
  height: number,
): string | null {
  const points = collectLonLats(geometry);
  if (!points.length) return null;
  if (geometry.type === "Point") {
    const { x, y } = projectToSvg(points[0].lon, points[0].lat, bounds, width, height);
    return `M ${x - 5} ${y} a 5 5 0 1 0 10 0 a 5 5 0 1 0 -10 0`;
  }
  const parts: string[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const { x, y } = projectToSvg(points[i].lon, points[i].lat, bounds, width, height);
    parts.push(`${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  if (geometry.type === "Polygon" && points.length > 2) {
    parts.push("Z");
  }
  return parts.join(" ");
}

export function statusTone(status: string): string {
  switch (status) {
    case "passed":
    case "approved":
    case "compliant":
    case "completed":
      return "emerald";
    case "failed":
    case "deviation":
    case "deviation_detected":
    case "rejected":
      return "rose";
    case "unavailable":
    case "needs_review":
    case "insufficient_evidence":
    case "analyzing":
    case "evidence_uploaded":
      return "amber";
    default:
      return "slate";
  }
}
