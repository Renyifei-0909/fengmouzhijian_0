import { useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Focus, Info, Layers, LocateFixed, X } from "lucide-react";
import maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as LibreMap, MapMouseEvent, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { EngineeringObject, GeoJsonGeometry } from "../../lib/api";
import { boundsFromPoints, collectLonLats, geometryToSvgPath, projectToSvg } from "../../lib/geoDisplay";
import type { LonLat } from "../../lib/geoDisplay";
import { engineeringObjectDisplayName, labelObjectType } from "../../lib/productCopy";
import { addRegionalLabels, BINJIANG_CENTER, isBinjiangViewport, setRegionalBasemap } from "./regionalBasemap";
import { addMapAnnotations, annotationAnchor } from "./mapAnnotations";
import type { MapAnnotation } from "./mapAnnotations";
import { BINJIANG_DEMO_PACKAGE_ID } from "./binjiangDemoObjects";
export const OFFLINE_BLANK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  name: "fengmou-engineering",
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#152633" } }],
};

const SOURCE = "fm-engineering-objects";
const FILL = "fm-engineering-fill";
const LINE = "fm-engineering-line";
const LINE_CASING = "fm-engineering-line-casing";
const POINT = "fm-engineering-point";
const SELECTED_LINE = "fm-engineering-selected-line";
const SELECTED_POINT = "fm-engineering-selected-point";
const LAYOUT_CASING = "fm-layout-casing";
const LAYOUT_LINE = "fm-layout-line";
const LAYOUT_ACCESS = "fm-layout-access";
const LAYOUT_NODE = "fm-layout-node";
const LAYOUT_SYMBOL = "fm-layout-symbol";
const STREETS = "fm-streets";
const ENGINEERING_LAYERS = [LAYOUT_LINE, LAYOUT_ACCESS, LAYOUT_NODE, LAYOUT_SYMBOL, FILL, LINE, POINT, SELECTED_LINE, SELECTED_POINT];
const NETWORK_COLORS = { trunk: "#62c5fa", branch: "#4cbaa8", access: "#e2b672", junction: "#ffd08b", cabinet: "#9bdfbb", terminal: "#ecd2a2" };
const OBJECT_LAYERS = {
  routes: { label: "设计线路", color: "#52d4ed" },
  sites: { label: "设施点", color: "#7ed2a5" },
  areas: { label: "作业范围", color: "#a4b9f0" },
  layout: { label: "管网布设", color: "#67b5c8" },
} as const;
type ObjectLayer = keyof typeof OBJECT_LAYERS;
const OBJECT_LAYER_IDS = Object.keys(OBJECT_LAYERS) as ObjectLayer[];

function objectLayer({ design_package_id, geometry_type }: EngineeringObject): ObjectLayer {
  if (design_package_id === BINJIANG_DEMO_PACKAGE_ID) return "layout";
  if (geometry_type.endsWith("Point")) return "sites";
  return geometry_type.endsWith("Polygon") ? "areas" : "routes";
}

type Basemap = "engineering" | "streets" | "configured";

export type CaptureMarker = {
  longitude: number;
  latitude: number;
  synthetic?: boolean;
  comparison?: { distance: number; tolerance: number };
};

type Props = {
  objects: EngineeringObject[];
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
  captureMarker?: CaptureMarker | null;
  className?: string;
  onlineStyleUrl?: string | null;
  emptyMessage?: string;
};

const pointFilter: maplibregl.ExpressionSpecification = ["==", ["geometry-type"], "Point"];
const lineFilter: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]];
const layoutFilter: maplibregl.ExpressionSpecification = ["==", ["get", "layer"], "layout"];
const trunkFilter: maplibregl.ExpressionSpecification = ["==", ["get", "kind"], "trunk"];
const accessFilter: maplibregl.ExpressionSpecification = ["==", ["get", "kind"], "access"];
const symbolFilter: maplibregl.ExpressionSpecification = ["in", ["get", "kind"], ["literal", ["junction", "cabinet", "terminal"]]];

function toFeatureCollection(objects: EngineeringObject[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: objects.flatMap((object) => collectLonLats(object.geometry_wgs84).length ? [{
      type: "Feature" as const,
      id: object.id,
      geometry: object.geometry_wgs84 as GeoJSON.Geometry,
      properties: {
        id: object.id,
        name: engineeringObjectDisplayName(object.name),
        code: object.object_code,
        type: object.attributes_snapshot.布设类型 ?? labelObjectType(object.object_type),
        layer: objectLayer(object),
        kind: object.attributes_snapshot.layout_kind ?? "",
      },
    }] : []),
  };
}

function addNetworkSymbols(map: LibreMap) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return;
  for (const kind of ["junction", "cabinet", "terminal"] as const) {
    if (map.hasImage(`fm-${kind}`)) continue;
    ctx.clearRect(0, 0, 32, 32);
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = NETWORK_COLORS[kind];
    ctx.fillStyle = "#102639";
    ctx.beginPath();
    if (kind === "junction") {
      ctx.moveTo(16, 4);
      ctx.lineTo(28, 16);
      ctx.lineTo(16, 28);
      ctx.lineTo(4, 16);
      ctx.closePath();
    } else if (kind === "cabinet") {
      ctx.rect(6, 6, 20, 20);
    } else {
      ctx.arc(16, 16, 9, 0, 2 * Math.PI);
    }
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = NETWORK_COLORS[kind];
    ctx.fillRect(14, 14, 4, 4);
    map.addImage(`fm-${kind}`, ctx.getImageData(0, 0, 32, 32), { pixelRatio: 2 });
  }
}

function addEngineeringLayers(map: LibreMap, features: GeoJSON.FeatureCollection) {
  map.addSource(SOURCE, { type: "geojson", data: features, promoteId: "id" });
  addNetworkSymbols(map);
  map.addLayer({
    id: LAYOUT_CASING, type: "line", source: SOURCE, filter: ["all", lineFilter, layoutFilter],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#092132",
      "line-width": ["interpolate", ["linear"], ["zoom"], 12, 4, 16, 6, 18, 8],
      "line-opacity": .82,
    },
  });
  map.addLayer({
    id: LAYOUT_LINE, type: "line", source: SOURCE, filter: ["all", lineFilter, layoutFilter, ["!", accessFilter]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["case", trunkFilter, NETWORK_COLORS.trunk, NETWORK_COLORS.branch],
      "line-width": ["interpolate", ["linear"], ["zoom"], 12, ["case", trunkFilter, 2.6, 1.7], 15.5, ["case", trunkFilter, 3.9, 2.45], 18, 4.5],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 12, .86, 16.5, 1],
    },
  });
  map.addLayer({
    id: LAYOUT_ACCESS, type: "line", source: SOURCE, filter: ["all", lineFilter, layoutFilter, accessFilter],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": NETWORK_COLORS.access,
      "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1, 15.5, 2.15, 18, 3],
      "line-dasharray": [2.2, 1.6],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 12, .45, 15.5, .96],
    },
  });
  map.addLayer({
    id: LAYOUT_NODE, type: "circle", source: SOURCE, minzoom: 12.2, filter: ["all", pointFilter, layoutFilter, ["!", symbolFilter]],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.2, 1.05, 14.5, 2.25, 17, 3.45],
      "circle-color": ["match", ["get", "kind"], "manhole", "#173245", "#99c5d7"],
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 12.2, .28, 14.5, .82, 16.8, .97],
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 12.2, .15, 15.4, .95],
      "circle-stroke-color": ["match", ["get", "kind"], "manhole", "#b7d8e2", "#102739"],
    },
  });
  map.addLayer({
    id: LAYOUT_SYMBOL, type: "symbol", source: SOURCE, filter: ["all", pointFilter, layoutFilter, symbolFilter],
    layout: {
      "icon-image": ["concat", "fm-", ["get", "kind"]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 11, .27, 13, .47, 16, .9, 18, 1.1],
      "icon-allow-overlap": true,
    },
    paint: { "icon-opacity": ["interpolate", ["linear"], ["zoom"], 11, .48, 14, .92, 16, 1] },
  });
  map.addLayer({
    id: FILL, type: "fill", source: SOURCE, filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": OBJECT_LAYERS.areas.color, "fill-opacity": 0.26 },
  });
  map.addLayer({
    id: LINE_CASING, type: "line", source: SOURCE, filter: ["all", lineFilter, ["!", layoutFilter]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#07151f", "line-width": 7.5, "line-opacity": .76 },
  });
  map.addLayer({
    id: LINE, type: "line", source: SOURCE, filter: ["all", lineFilter, ["!", layoutFilter]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["case", ["==", ["geometry-type"], "Polygon"], OBJECT_LAYERS.areas.color, OBJECT_LAYERS.routes.color], "line-width": 3.5 },
  });
  map.addLayer({
    id: SELECTED_LINE, type: "line", source: SOURCE, filter: ["all", ["==", ["get", "id"], ""], lineFilter],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#f3b766", "line-width": 5 },
  });
  map.addLayer({
    id: POINT, type: "circle", source: SOURCE, filter: ["all", pointFilter, ["!", layoutFilter]],
    paint: { "circle-radius": 5.5, "circle-color": OBJECT_LAYERS.sites.color, "circle-stroke-width": 2, "circle-stroke-color": "#0c1a2a" },
  });
  map.addLayer({
    id: SELECTED_POINT, type: "circle", source: SOURCE, filter: ["all", ["==", ["get", "id"], ""], pointFilter],
    paint: { "circle-radius": 7.5, "circle-color": "#f3b766", "circle-stroke-width": 2, "circle-stroke-color": "#fff0d3" },
  });
}

function setStreets(map: LibreMap, visible: boolean) {
  if (visible && !map.getSource(STREETS)) {
    map.addSource(STREETS, {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
    });
    map.addLayer({
      id: STREETS,
      type: "raster",
      source: STREETS,
      paint: { "raster-brightness-max": 0.47, "raster-contrast": 0.18, "raster-saturation": -0.73, "raster-opacity": 0.68 },
    }, LAYOUT_CASING);
  }
  if (map.getLayer(STREETS)) map.setLayoutProperty(STREETS, "visibility", visible ? "visible" : "none");
}

function fit(map: LibreMap, points: LonLat[], maxZoom = 17) {
  const bounds = boundsFromPoints(points, 0.035);
  if (!bounds) return;
  const padding = Math.min(54, Math.floor(map.getContainer().clientWidth / 7));
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  map.fitBounds([[bounds.minLon, bounds.minLat], [bounds.maxLon, bounds.maxLat]], {
    padding, maxZoom, duration: reducedMotion ? 0 : 680, curve: 1.25,
    easing: (progress) => progress * progress * (3 - 2 * progress),
  });
}

function tooltip(title: string, caption: string) {
  const element = document.createElement("div");
  element.className = "gis-map-tooltip";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const text = document.createElement("small");
  text.textContent = caption;
  element.append(heading, text);
  return element;
}

export function GeoMap({
  objects,
  selectedObjectId,
  onSelectObject,
  captureMarker = null,
  className = "",
  onlineStyleUrl = null,
  emptyMessage = "暂无工程图层",
}: Props) {
  const configuredStyle = onlineStyleUrl && /^https?:\/\//i.test(onlineStyleUrl) ? onlineStyleUrl : null;
  const [basemap, setBasemap] = useState<Basemap>(configuredStyle ? "configured" : "streets");
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [visibleLayers, setVisibleLayers] = useState<ObjectLayer[]>(OBJECT_LAYER_IDS);
  const [showCapture, setShowCapture] = useState(true);
  const [overlap, setOverlap] = useState<{ id: string; label: string }[]>([]);
  const [styleVersion, setStyleVersion] = useState(0);
  const [styleError, setStyleError] = useState(false);
  const [inRegion, setInRegion] = useState(true);
  const [regional, setRegional] = useState<GeoJSON.FeatureCollection | null>(null);
  const [regionalReady, setRegionalReady] = useState(false);
  const [regionalError, setRegionalError] = useState(false);
  const [rasterState, setRasterState] = useState<"loading" | "loaded" | "unavailable">("loading");
  const [basemapRevision, setBasemapRevision] = useState(0);
  const [collapseCredit, setCollapseCredit] = useState(false);
  const showRegion = basemap === "streets" && inRegion && !regionalError;
  const showRaster = basemap === "streets" && (!inRegion || regionalError);
  const [unavailable, setUnavailable] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const displayedStyle = useRef<string | null>(null);
  const lastFitKey = useRef<string | null>(null);
  const appliedBasemapRevision = useRef(0);
  const availableLayers = useMemo(() => new Set(objects.map(objectLayer)), [objects]);
  const detailExtent = useMemo(() => {
    const junctions = objects.flatMap((object) => object.attributes_snapshot.layout_kind === "junction"
      ? collectLonLats(object.geometry_wgs84) : []);
    let center: LonLat | undefined;
    let maxNeighbors = 0;
    for (const point of junctions) {
      const neighbors = junctions.filter((other) => Math.abs(other.lon - point.lon) < .006 && Math.abs(other.lat - point.lat) < .004).length;
      if (neighbors > maxNeighbors) {
        maxNeighbors = neighbors;
        center = point;
      }
    }
    return center ? [
      { lon: center.lon - .0065, lat: center.lat - .0039 },
      { lon: center.lon + .0065, lat: center.lat + .0039 },
    ] : [];
  }, [objects]);
  const visibleObjects = useMemo(() => objects.filter((object) => visibleLayers.includes(objectLayer(object))), [objects, visibleLayers]);
  const osmVisible = Boolean((showRegion && regional) || (showRaster && rasterState === "loaded")
    || (availableLayers.has("layout") && visibleLayers.includes("layout")));
  const features = useMemo(() => toFeatureCollection(visibleObjects), [visibleObjects]);
  const allPoints = useMemo(() => objects.flatMap((object) => collectLonLats(object.geometry_wgs84)), [objects]);
  const visiblePoints = useMemo(() => visibleObjects.flatMap((object) => collectLonLats(object.geometry_wgs84)), [visibleObjects]);
  const visibleIds = useMemo(() => new Set(visibleObjects.map(({ id }) => id)), [visibleObjects]);
  const visibleHits = overlap.filter(({ id }) => visibleIds.has(id));
  const focusObject = objects.find((object) => object.id === selectedObjectId);
  const selectedLayer = focusObject ? objectLayer(focusObject) : undefined;
  const focusPoints = useMemo(() => collectLonLats(focusObject?.geometry_wgs84), [focusObject]);
  const mappedCapture = showCapture ? captureMarker : null;
  const annotations = useMemo<MapAnnotation[]>(() => visibleObjects.flatMap((object) => {
    const point = annotationAnchor(collectLonLats(object.geometry_wgs84));
    const name = engineeringObjectDisplayName(object.name);
    const layer = objectLayer(object);
    const kind = object.attributes_snapshot.layout_kind;
    const detailZoom = kind === "junction" || kind === "cabinet" ? 15.1 : object.geometry_type === "Point" ? 17.1 : 16;
    return point ? [{
      id: object.id, point,
      text: name && name !== object.object_code ? `${object.object_code} · ${name}` : object.object_code,
      minZoom: layer === "layout" ? detailZoom : 14.5,
      color: OBJECT_LAYERS[layer].color,
    }] : [];
  }), [visibleObjects]);
  const latest = useRef({ features, onSelectObject });
  latest.current = { features, onSelectObject };
  const styleUrl = basemap === "configured" ? configuredStyle : null;

  useEffect(() => {
    if (!containerRef.current) return;
    let map: LibreMap;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: OFFLINE_BLANK_STYLE,
        center: BINJIANG_CENTER,
        zoom: 12.4,
        renderWorldCopies: false,
        attributionControl: { compact: false },
        locale: {
          "Map.Title": "工程地图",
          "NavigationControl.ZoomIn": "放大地图",
          "NavigationControl.ZoomOut": "缩小地图",
          "NavigationControl.ResetBearing": "恢复正北",
          "AttributionControl.ToggleAttribution": "查看地图来源",
          "ScaleControl.Meters": "米",
          "ScaleControl.Kilometers": "公里",
        },
      });
    } catch {
      setUnavailable(true);
      return;
    }
    mapRef.current = map;
    map.scrollZoom.setWheelZoomRate(1 / 650);
    map.scrollZoom.setZoomRate(1 / 125);
    displayedStyle.current = null;
    lastFitKey.current = null;
    map.addControl(new maplibregl.NavigationControl({ showCompass: true, showZoom: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "gis-popup" });

    map.on("style.load", () => {
      if (!map.getSource(SOURCE)) addEngineeringLayers(map, latest.current.features);
      setStyleVersion((version) => version + 1);
    });

    const renderedFeatures = (event: MapMouseEvent) => {
      const layers = ENGINEERING_LAYERS.filter((layer) => map.getLayer(layer));
      if (!layers.length) return [];
      return map.queryRenderedFeatures(
        [[event.point.x - 4, event.point.y - 4], [event.point.x + 4, event.point.y + 4]],
        { layers },
      );
    };

    map.on("click", (event) => {
      const hits = new Map<string, string>();
      for (const { properties } of renderedFeatures(event)) {
        if (typeof properties?.id === "string") hits.set(properties.id, String(properties.code || properties.name));
      }
      const choices = Array.from(hits, ([id, label]) => ({ id, label })).slice(0, 8);
      setOverlap(choices.length > 1 ? choices : []);
      if (choices[0]) latest.current.onSelectObject(choices[0].id);
      popup.remove();
    });


    map.on("mousemove", (event) => {
      const [feature] = renderedFeatures(event);
      map.getCanvas().style.cursor = feature ? "pointer" : "";
      if (!feature) {
        popup.remove();
        return;
      }
      const name = String(feature.properties?.name || feature.properties?.code || "工程对象");
      const caption = String(feature.properties?.type || "设计对象");
      popup.setLngLat(event.lngLat).setDOMContent(tooltip(name, caption)).addTo(map);
    });
    map.on("mouseout", () => popup.remove());
    map.on("movestart", () => popup.remove());

    map.on("moveend", () => setInRegion(isBinjiangViewport(map)));
    map.on("sourcedata", (event) => {
      if (event.sourceId === STREETS && event.sourceDataType === "content") {
        setRasterState("loaded");
      }
    });
    map.on("error", (event) => {
      if ("sourceId" in event && event.sourceId === STREETS) {
        setRasterState((state) => state === "loaded" ? state : "unavailable");
        return;
      }
      if (displayedStyle.current && !map.getSource(SOURCE)) {
        displayedStyle.current = null;
        setStyleError(true);
        setBasemap("streets");
        map.setStyle(OFFLINE_BLANK_STYLE, { diff: false });
      }
    });
    const resize = typeof ResizeObserver === "function" ? new ResizeObserver(() => map.resize()) : null;
    resize?.observe(containerRef.current);
    return () => {
      resize?.disconnect();
      markerRef.current?.remove();
      markerRef.current = null;
      popup.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || displayedStyle.current === styleUrl) return;
    displayedStyle.current = styleUrl;
    map.setStyle(styleUrl ?? OFFLINE_BLANK_STYLE, { diff: false });
  }, [styleUrl]);

  useEffect(() => {
    if (!showRegion || regional || unavailable) return;
    const controller = new AbortController();
    fetch(`${import.meta.env.BASE_URL}maps/binjiang.geojson`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("区域底图加载失败");
        const data = await response.json() as GeoJSON.FeatureCollection;
        if (!controller.signal.aborted) setRegional(data);
      })
      .catch(() => { if (!controller.signal.aborted) setRegionalError(true); });
    return () => controller.abort();
  }, [showRegion, regional, unavailable, basemapRevision]);

  useEffect(() => {
    if (!showRegion || !regional || !mapRef.current) return;
    return addRegionalLabels(mapRef.current, regional);
  }, [regional, showRegion]);

  useEffect(() => {
    if (!osmVisible || collapseCredit) return;
    const timeout = window.setTimeout(() => setCollapseCredit(true), 5200);
    return () => window.clearTimeout(timeout);
  }, [osmVisible, collapseCredit]);

  useEffect(() => {
    if (!showRaster) return;
    const timeout = window.setTimeout(() => {
      setRasterState((state) => state === "loading" ? "unavailable" : state);
    }, 9000);
    return () => window.clearTimeout(timeout);
  }, [showRaster, basemapRevision]);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource<GeoJSONSource>(SOURCE);
    if (!map || !source) return;
    source.setData(features);
    const match: maplibregl.ExpressionSpecification = ["==", ["get", "id"], selectedObjectId ?? ""];
    map.setFilter(SELECTED_LINE, ["all", match, lineFilter]);
    map.setFilter(SELECTED_POINT, ["all", match, pointFilter]);
  }, [features, selectedObjectId, styleVersion]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer(FILL)) return;
    if (showRaster && appliedBasemapRevision.current !== basemapRevision) {
      appliedBasemapRevision.current = basemapRevision;
      if (map.getLayer(STREETS)) {
        map.removeLayer(STREETS);
        map.removeSource(STREETS);
      }
    }
    setStreets(map, showRaster);
    setRegionalBasemap(map, regional, showRegion, LAYOUT_CASING);
  }, [showRaster, regional, showRegion, styleVersion, basemapRevision]);

  useEffect(() => {
    const map = mapRef.current;
    if (!showRegion || !regional || !map || !styleVersion) return;
    const ready = () => setRegionalReady(true);
    map.once("idle", ready);
    return () => { map.off("idle", ready); };
  }, [regional, showRegion, styleVersion]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer(FILL)) return;
    const key = focusObject?.id ?? (allPoints.length ? objects.map((object) => object.id).join("|") : null);
    if (lastFitKey.current === key) return;
    lastFitKey.current = key;
    if (key) fit(map, focusPoints.length ? focusPoints : visiblePoints.length ? visiblePoints : allPoints);
  }, [allPoints, focusObject, focusPoints, objects, styleVersion, visiblePoints]);

  useEffect(() => {
    if (selectedLayer) setVisibleLayers((previous) => previous.includes(selectedLayer) ? previous : [...previous, selectedLayer]);
  }, [selectedLayer, selectedObjectId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !showAnnotations) return;
    return addMapAnnotations(map, annotations, selectedObjectId);
  }, [annotations, selectedObjectId, showAnnotations]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mappedCapture) return;
    const { longitude, latitude } = mappedCapture;
    if (!Number.isFinite(longitude) || Math.abs(longitude) > 180 || !Number.isFinite(latitude) || Math.abs(latitude) > 90) return;
    const element = document.createElement("div");
    element.className = mappedCapture.synthetic ? "gis-capture-marker is-reference" : "gis-capture-marker";
    element.title = mappedCapture.synthetic ? "标注位置" : "采集位置";
    const marker = new maplibregl.Marker({ element }).setLngLat([longitude, latitude]).addTo(map);
    markerRef.current = marker;
    return () => {
      marker.remove();
      if (markerRef.current === marker) markerRef.current = null;
    };
  }, [mappedCapture]);

  const locate = (points: LonLat[], maxZoom?: number) => { if (mapRef.current) fit(mapRef.current, points, maxZoom); };
  const locateCapture = () => {
    if (!captureMarker) return;
    if (focusObject) {
      const layer = objectLayer(focusObject);
      setVisibleLayers((previous) => previous.includes(layer) ? previous : [...previous, layer]);
    }
    setShowCapture(true);
    locate([...focusPoints, { lon: captureMarker.longitude, lat: captureMarker.latitude }], 16.6);
  };
  const chooseBasemap = (mode: Basemap) => { setBasemap(mode); setStyleError(false); };
  const retryBasemap = () => {
    setRegionalError(false);
    setRasterState("loading");
    setBasemapRevision((revision) => revision + 1);
  };

  return (
    <div className={`gis-map-surface ${className}`} data-regional-ready={regionalReady}>
      <div ref={containerRef} className="gis-map-canvas" />
      {unavailable ? (
        <EngineeringSketch
          objects={visibleObjects}
          selectedObjectId={selectedObjectId}
          captureMarker={mappedCapture}
          onSelectObject={onSelectObject}
        />
      ) : (
        <>
          {(basemap === "engineering" || (showRaster && rasterState === "unavailable")) && <div className="gis-map-grid" aria-hidden="true" />}
          <div className="gis-map-tools">
            <details className="gis-layer-menu">
              <summary><Layers size={15} aria-hidden="true" /> 图层</summary>
              <div className="gis-layer-options">
                <span className="fm-label">底图</span>
                <label><input type="radio" name="gis-basemap" checked={basemap === "engineering"} onChange={() => chooseBasemap("engineering")} /> 工程简图</label>
                <label><input type="radio" name="gis-basemap" checked={basemap === "streets"} onChange={() => chooseBasemap("streets")} /> 街道底图</label>
                {configuredStyle && <label><input type="radio" name="gis-basemap" checked={basemap === "configured"} onChange={() => chooseBasemap("configured")} /> 项目底图</label>}
                {showRegion && regional && <small>杭州滨江及周边 · 离线可用</small>}
                {showRaster && rasterState === "unavailable" && <button type="button" onClick={retryBasemap}>重试街道底图</button>}
                <span className="fm-label gis-layer-divider">工程图层</span>
                {OBJECT_LAYER_IDS.filter((layer) => availableLayers.has(layer)).map((layer) => (
                  <label key={layer}>
                    <input type="checkbox" checked={visibleLayers.includes(layer)} onChange={(event) => {
                      const checked = event.target.checked;
                      setVisibleLayers((previous) => checked ? [...previous, layer] : previous.filter((id) => id !== layer));
                    }} /> {OBJECT_LAYERS[layer].label}
                  </label>
                ))}
                {captureMarker && <label><input type="checkbox" checked={showCapture} onChange={(event) => setShowCapture(event.target.checked)} /> 采集位置</label>}
                <label>
                  <input type="checkbox" checked={showAnnotations} onChange={(event) => setShowAnnotations(event.target.checked)} /> 对象标注
                </label>
              </div>
            </details>
            <button type="button" title="定位当前地图全图" onClick={() => locate(visiblePoints)} disabled={!visiblePoints.length}>
              <Crosshair size={15} aria-hidden="true" /> 全图
            </button>
            {detailExtent.length > 0 && <button type="button" className="gis-map-detail-tool" onClick={() => {
              setVisibleLayers((previous) => previous.includes("layout") ? previous : [...previous, "layout"]);
              locate(detailExtent, 16);
            }}><Focus size={15} aria-hidden="true" /> 街区详图</button>}
            {captureMarker && <button type="button" title="同时查看工单设计与采集位置" onClick={locateCapture}>
              <LocateFixed size={15} aria-hidden="true" /> 定位对照
            </button>}
          </div>
          {visibleHits.some(({ id }) => id === selectedObjectId) && visibleHits.length > 1 && (
            <div className="gis-map-overlap" role="group" aria-label="重叠要素">
              <div><strong>重叠要素</strong><button type="button" className="gis-icon-button" aria-label="收起重叠要素" onClick={() => setOverlap([])}><X size={13} /></button></div>
              {visibleHits.map((object) => <button type="button" key={object.id} aria-pressed={object.id === selectedObjectId}
                onClick={() => onSelectObject(object.id)}>{object.label}</button>)}
            </div>
          )}
          {osmVisible && <div className="gis-attribution">
            {!collapseCredit && <a className="gis-attribution-credit" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a>}
            <details>
              <summary aria-label="地图来源" title="地图来源"><Info size={14} aria-hidden="true" /></summary>
              <div><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a><span> · ODbL</span>
                {showRegion && regional && <span> · <a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a>
                  {" / "}<a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">© OpenMapTiles</a></span>}
              </div>
            </details>
          </div>}
          {showRegion && !regionalReady && <div className="gis-map-message fm-operation-inline" role="status">正在绘制区域底图…</div>}
          {styleError && <div className="gis-map-message" role="status">项目底图暂不可用，已切换街道底图</div>}
          {!styleError && showRaster && rasterState === "unavailable" && <div className="gis-map-message" role="status">街道底图暂不可用，工程图层可继续查看</div>}
        </>
      )}
      {!features.features.length && <div className="gis-map-empty" role="status">{allPoints.length ? "工程图层已隐藏" : emptyMessage}</div>}
      {(visibleObjects.length > 0 || mappedCapture) && <div className="gis-map-legend" aria-label="图例">
        {OBJECT_LAYER_IDS.filter((layer) => layer !== "layout" && availableLayers.has(layer) && visibleLayers.includes(layer)).map((layer) => (
          <span key={layer}><i data-shape={layer} style={{ color: OBJECT_LAYERS[layer].color }} />{OBJECT_LAYERS[layer].label}</span>
        ))}
        {availableLayers.has("layout") && visibleLayers.includes("layout") && <div className="gis-network-key">
          <span className="gis-network-key-title">布设</span>
          <span><i data-shape="trunk" style={{ color: NETWORK_COLORS.trunk }} />主干</span>
          <span><i data-shape="trunk" style={{ color: NETWORK_COLORS.branch }} />支线</span>
          <span><i data-shape="access" style={{ color: NETWORK_COLORS.access }} />接入</span>
          <span><i data-shape="junction" style={{ color: NETWORK_COLORS.junction }} />分歧井</span>
          <span><i data-shape="cabinet" style={{ color: NETWORK_COLORS.cabinet }} />交接箱</span>
        </div>}
        {selectedObjectId && visibleIds.has(selectedObjectId) && <span><i className="is-selected" /> 已选对象</span>}
        {mappedCapture && <span><i className={mappedCapture.synthetic ? "is-reference" : "is-capture"} />
          {mappedCapture.synthetic ? "标注位置" : "采集位置"}
        </span>}
      </div>}
    </div>
  );
}

function EngineeringSketch({
  objects, selectedObjectId, captureMarker, onSelectObject,
}: Pick<Props, "selectedObjectId" | "captureMarker" | "onSelectObject"> & {
  objects: EngineeringObject[];
}) {
  const width = 760;
  const height = 420;
  const markerPoint = captureMarker ? { lon: captureMarker.longitude, lat: captureMarker.latitude } : null;
  const points = objects.flatMap((object) => collectLonLats(object.geometry_wgs84));
  const bounds = boundsFromPoints(markerPoint ? [...points, markerPoint] : points, 0.09);
  const position = markerPoint && bounds ? projectToSvg(markerPoint.lon, markerPoint.lat, bounds, width, height) : null;
  return (
    <div className="gis-sketch">
      <div className="gis-map-grid" aria-hidden="true" />
      <div className="gis-map-message" role="status">工程简图</div>
      {bounds && <svg viewBox={`0 0 ${width} ${height}`} aria-label="工程对象空间简图">
        {objects.map((object) => {
          const path = geometryToSvgPath(object.geometry_wgs84, bounds, width, height);
          if (!path) return null;
          const selectable = object.id === selectedObjectId;
          const layer = objectLayer(object);
          const color = OBJECT_LAYERS[layer].color;
          return <path
            key={object.id}
            d={path}
            role="button"
            tabIndex={0}
            aria-label={object.name || object.object_code}
            aria-pressed={selectable}
            onClick={() => onSelectObject(object.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectObject(object.id);
              }
            }}
            fill={object.geometry_type.endsWith("LineString") ? "none" : selectable ? "#f3b76655" : `${color}44`}
            stroke={selectable ? "#f3b766" : color}
            strokeWidth={selectable ? 4.5 : 3}
          ><title>{object.name || object.object_code}</title></path>;
        })}
        {position && <circle cx={position.x} cy={position.y} r={5} fill={captureMarker?.synthetic ? "#b29ae5" : "#62d4a4"} stroke="#e2fff5" strokeWidth={2} />}
      </svg>}
    </div>
  );
}
