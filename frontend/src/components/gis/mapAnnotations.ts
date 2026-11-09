import maplibregl from "maplibre-gl";
import type { Map as LibreMap } from "maplibre-gl";
import type { LonLat } from "../../lib/geoDisplay";

export type MapAnnotation = {
  id: string;
  point: LonLat;
  text: string;
  minZoom: number;
  color: string;
};

export function annotationAnchor(points: LonLat[]) {
  if (points.length < 2) return points[0];
  // The middle of the line by length stays on the line, even with unevenly spaced vertices.
  const longitudeScale = Math.cos(points[0].lat * Math.PI / 180);
  const lengths = points.slice(1).map((point, index) =>
    Math.hypot((point.lon - points[index].lon) * longitudeScale, point.lat - points[index].lat),
  );
  let remaining = lengths.reduce((total, length) => total + length, 0) / 2;
  for (const [index, length] of lengths.entries()) {
    if (remaining <= length && length > 0) {
      const a = points[index];
      const b = points[index + 1];
      const t = remaining / length;
      return { lon: a.lon + (b.lon - a.lon) * t, lat: a.lat + (b.lat - a.lat) * t };
    }
    remaining -= length;
  }
  return points[0];
}

export function addMapAnnotations(map: LibreMap, annotations: MapAnnotation[], selectedId: string | null) {
  const markers = new Map<string, maplibregl.Marker>();
  const candidates = [...annotations].sort((a, b) =>
    Number(b.id === selectedId) - Number(a.id === selectedId) || a.minZoom - b.minZoom,
  );
  const update = () => {
    const visible = new Set<string>();
    const boxes: { left: number; right: number; y: number }[] = [];
    const { clientWidth: width, clientHeight: height } = map.getContainer();
    const zoom = map.getZoom();
    for (const entry of candidates) {
      if (visible.size >= 24) break;
      const selected = entry.id === selectedId;
      if (!selected && zoom < entry.minZoom) continue;
      const { x, y } = map.project([entry.point.lon, entry.point.lat]);
      const left = x + 8;
      const right = left + [...entry.text].reduce((total, c) => total + (c.codePointAt(0)! > 255 ? 12 : 7), 15);
      if (x < 0 || x > width || y < 0 || y > height) continue;
      if (!selected && (left < 8 || right > width - 42 || y < 56 || y > height - 92
        || boxes.some((box) => Math.abs(box.y - y) < 24 && left < box.right && right > box.left))) continue;
      boxes.push({ left, right, y });
      visible.add(entry.id);
      if (markers.has(entry.id)) continue;
      const tag = document.createElement("span");
      tag.className = selected ? "gis-map-annotation is-selected" : "gis-map-annotation";
      tag.style.setProperty("--label-color", entry.color);
      tag.textContent = entry.text;
      tag.setAttribute("aria-hidden", "true");
      markers.set(entry.id, new maplibregl.Marker({ element: tag, anchor: "bottom-left", offset: [8, -5] })
        .setLngLat([entry.point.lon, entry.point.lat]).addTo(map));
    }
    for (const [id, marker] of markers) {
      if (visible.has(id)) continue;
      marker.remove();
      markers.delete(id);
    }
  };
  update();
  map.on("moveend", update);
  map.on("resize", update);
  return () => {
    map.off("moveend", update);
    map.off("resize", update);
    markers.forEach((marker) => marker.remove());
  };
}
