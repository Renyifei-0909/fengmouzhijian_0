import maplibregl from "maplibre-gl";
import type { Map as LibreMap } from "maplibre-gl";

const SOURCE = "binjiang-context";
export const BINJIANG_BOUNDS: [number, number, number, number] = [120.123, 30.148, 120.269, 30.258];
export const BINJIANG_CENTER: [number, number] = [120.191, 30.207];

export function isBinjiangViewport(map: LibreMap) {
  const { lng, lat } = map.getCenter();
  const [west, south, east, north] = BINJIANG_BOUNDS;
  return map.getZoom() >= 10.8 && lng >= west && lng <= east && lat >= south && lat <= north;
}

const LAYERS: maplibregl.LayerSpecification[] = [
  { id: "fm-context-parks", type: "fill", source: SOURCE, filter: ["==", ["get", "kind"], "park"],
    paint: { "fill-color": "#1b3d38", "fill-outline-color": "#274942" } },
  { id: "fm-context-water", type: "fill", source: SOURCE, filter: ["==", ["get", "kind"], "water"],
    paint: { "fill-color": "#123b54", "fill-outline-color": "#31617b" } },
  { id: "fm-context-streams", type: "line", source: SOURCE, filter: ["==", ["get", "kind"], "stream"],
    paint: { "line-color": "#30617f", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.7, 17, 2] } },
  { id: "fm-context-buildings", type: "fill", source: SOURCE, minzoom: 15,
    filter: ["==", ["get", "kind"], "building"],
    paint: { "fill-color": "#324754", "fill-outline-color": "#425a67",
      "fill-opacity": ["interpolate", ["linear"], ["zoom"], 15, .35, 16.5, .88] } },
  { id: "fm-context-local-roads", type: "line", source: SOURCE, minzoom: 12,
    filter: ["all", ["==", ["get", "kind"], "road"], ["==", ["get", "rank"], "local"]],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#405967", "line-opacity": .8, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.45, 15, 1.05, 18, 2.35] } },
  { id: "fm-context-road-casing", type: "line", source: SOURCE, minzoom: 13,
    filter: ["all", ["==", ["get", "kind"], "road"], ["!=", ["get", "rank"], "local"]],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#11212e", "line-width": ["interpolate", ["linear"], ["zoom"], 13, 2.6, 16, 5.4, 18, 8] } },
  { id: "fm-context-main-roads", type: "line", source: SOURCE, filter: ["all", ["==", ["get", "kind"], "road"], ["!=", ["get", "rank"], "local"]],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": ["match", ["get", "rank"], "express", "#b3a27d", "main", "#829aa6", "#607887"],
      "line-opacity": .77,
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.7, 14, 2, 18, 5],
    } },
  { id: "fm-context-bridges", type: "line", source: SOURCE, minzoom: 14,
    filter: ["==", ["get", "kind"], "bridge"],
    paint: { "line-color": "#bac3c1", "line-opacity": .55, "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.9, 18, 2.6] } },
];

export function setRegionalBasemap(map: LibreMap, data: GeoJSON.FeatureCollection | null, visible: boolean, before: string) {
  if (data && visible && !map.getSource(SOURCE)) {
    map.addSource(SOURCE, { type: "geojson", data });
    LAYERS.forEach((layer) => map.addLayer(layer, before));
  }
  for (const { id } of LAYERS) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  }
}

export function addRegionalLabels(map: LibreMap, data: GeoJSON.FeatureCollection) {
  const ranks = ["water", "district", "road", "arterial", "neighborhood", "street"];
  const minZoom = [11.8, 11.8, 12.3, 13.8, 15.1, 16.15];
  const labels = data.features.flatMap(({ geometry, properties }) => {
    if (geometry.type !== "Point" || properties?.kind !== "label") return [];
    const rank = ranks.indexOf(properties.rank);
    if (rank < 0 || typeof properties.name !== "string") return [];
    const [lon, lat] = geometry.coordinates;
    const element = document.createElement("span");
    element.className = "gis-region-label";
    element.dataset.rank = properties.rank;
    element.textContent = properties.name;
    element.setAttribute("aria-hidden", "true");
    return [{
      marker: new maplibregl.Marker({ element }).setLngLat([lon, lat]),
      coordinate: [lon, lat] as [number, number],
      name: properties.name as string,
      rank,
      visible: false,
    }];
  }).sort((a, b) => a.rank - b.rank);
  const update = () => {
    const zoom = map.getZoom();
    const { clientWidth: width, clientHeight: height } = map.getContainer();
    const names = new Set<string>();
    const positions: { x: number; y: number; halfWidth: number }[] = [];
    for (const label of labels) {
      let visible = false;
      if (zoom >= minZoom[label.rank] && !names.has(label.name)) {
        const { x, y } = map.project(label.coordinate);
        const halfWidth = label.name.length * 6 + 8;
        visible = x > halfWidth && x < width - halfWidth && y > 60 && y < height - 46
          && !positions.some((p) => Math.abs(y - p.y) < 22 && Math.abs(x - p.x) < halfWidth + p.halfWidth);
        if (visible) {
          names.add(label.name);
          positions.push({ x, y, halfWidth });
        }
      }
      if (visible !== label.visible) {
        if (visible) label.marker.addTo(map);
        else label.marker.remove();
        label.visible = visible;
      }
    }
  };
  update();
  map.on("moveend", update);
  map.on("resize", update);
  return () => {
    map.off("moveend", update);
    map.off("resize", update);
    labels.forEach(({ marker }) => marker.remove());
  };
}
