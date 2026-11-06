import maplibregl from "maplibre-gl";
import type { Map as LibreMap } from "maplibre-gl";

const SOURCE = "binjiang-context";
export const BINJIANG_BOUNDS: [number, number, number, number] = [120.123, 30.148, 120.269, 30.258];
export const BINJIANG_CENTER: [number, number] = [120.191, 30.207];

export function isBinjiangViewport(map: LibreMap) {
  const { lng, lat } = map.getCenter();
  const [west, south, east, north] = BINJIANG_BOUNDS;
  return map.getZoom() >= 11.8 && lng >= west && lng <= east && lat >= south && lat <= north;
}

const LAYERS: maplibregl.LayerSpecification[] = [
  { id: "fm-context-parks", type: "fill", source: SOURCE, filter: ["==", ["get", "kind"], "park"],
    paint: { "fill-color": "#1c3c34" } },
  { id: "fm-context-water", type: "fill", source: SOURCE, filter: ["==", ["get", "kind"], "water"],
    paint: { "fill-color": "#153c59", "fill-outline-color": "#245572" } },
  { id: "fm-context-streams", type: "line", source: SOURCE, filter: ["==", ["get", "kind"], "stream"],
    paint: { "line-color": "#30617f", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.7, 17, 2] } },
  { id: "fm-context-local-roads", type: "line", source: SOURCE, minzoom: 12,
    filter: ["all", ["==", ["get", "kind"], "road"], ["==", ["get", "rank"], "local"]],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#3a5060", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.5, 15, 1.15, 18, 2.6] } },
  { id: "fm-context-main-roads", type: "line", source: SOURCE, filter: ["all", ["==", ["get", "kind"], "road"], ["!=", ["get", "rank"], "local"]],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": ["match", ["get", "rank"], "express", "#a39575", "main", "#778a94", "#536b7a"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.7, 14, 2, 18, 5],
    } },
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
  const labels = data.features.flatMap(({ geometry, properties }) => {
    if (geometry.type !== "Point" || properties?.kind !== "label") return [];
    const [lon, lat] = geometry.coordinates;
    const element = document.createElement("span");
    element.className = "gis-region-label";
    element.dataset.rank = properties.rank;
    element.textContent = properties.name;
    element.setAttribute("aria-hidden", "true");
    return [{
      marker: new maplibregl.Marker({ element }).setLngLat([lon, lat]).addTo(map),
      minZoom: properties.rank === "district" || properties.rank === "water" ? 11.8 : properties.rank === "road" ? 13.1 : 15.1,
    }];
  });
  const update = () => {
    const zoom = map.getZoom();
    for (const { marker, minZoom } of labels) marker.getElement().hidden = zoom < minZoom;
  };
  update();
  map.on("zoomend", update);
  return () => {
    map.off("zoomend", update);
    labels.forEach(({ marker }) => marker.remove());
  };
}
