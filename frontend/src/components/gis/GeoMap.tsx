import { useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Info, Layers, LocateFixed } from "lucide-react";
import maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as LibreMap, MapGeoJSONFeature, MapMouseEvent, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { EngineeringObject, GeoJsonGeometry } from "../../lib/api";
import { boundsFromPoints, collectLonLats, geometryToSvgPath, projectToSvg } from "../../lib/geoDisplay";
import type { LonLat } from "../../lib/geoDisplay";
import { engineeringObjectDisplayName, labelObjectType } from "../../lib/productCopy";
import { addRegionalLabels, BINJIANG_CENTER, isBinjiangViewport, setRegionalBasemap } from "./regionalBasemap";

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
const STREETS = "fm-streets";
const ENGINEERING_LAYERS = [FILL, LINE, POINT, SELECTED_LINE, SELECTED_POINT];

const QGIS_LAYERS = [
  { id: "pipes", label: "管线", type: "line", color: "#76c2fb", size: 3.4 },
  { id: "survey", label: "测量", type: "line", color: "#eca360", size: 2.5 },
  { id: "house-connections", label: "入户线", type: "line", color: "#ea8886", size: 2.4 },
  { id: "addresses", label: "地址", type: "circle", color: "#a69eed", size: 3.5 },
  { id: "infrastructure", label: "设施", type: "circle", color: "#68c7a5", size: 5 },
  { id: "pit-work", label: "施工点", type: "circle", color: "#e8b463", size: 5 },
  { id: "photos", label: "照片点", type: "circle", color: "#d482ad", size: 4.5 },
] as const;

type ReferenceLayer = typeof QGIS_LAYERS[number];
type ReferenceData = { layer: ReferenceLayer; collection: GeoJSON.FeatureCollection };
type Basemap = "engineering" | "streets" | "configured";

export type CaptureMarker = {
  longitude: number;
  latitude: number;
  synthetic?: boolean;
};

export type QgisDesignObject = {
  id: string;
  objectCode: string;
  objectType: string;
  sourceLayer: string;
  featureIndex: number;
  geometryType: string;
  designVersion: string;
};

type Props = {
  objects: EngineeringObject[];
  selectedObjectId: string | null;
  selectedQgisObjectId?: string | null;
  onSelectObject: (objectId: string) => void;
  onSelectQgisObject?: (object: QgisDesignObject | null) => void;
  captureMarker?: CaptureMarker | null;
  className?: string;
  onlineStyleUrl?: string | null;
  emptyMessage?: string;
};

const pointFilter: maplibregl.ExpressionSpecification = ["==", ["geometry-type"], "Point"];
const lineFilter: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]];
const referenceId = (id: ReferenceLayer["id"]) => `fm-reference-${id}`;

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
        type: labelObjectType(object.object_type),
      },
    }] : []),
  };
}

function addEngineeringLayers(map: LibreMap, features: GeoJSON.FeatureCollection) {
  map.addSource(SOURCE, { type: "geojson", data: features, promoteId: "id" });
  map.addLayer({
    id: FILL, type: "fill", source: SOURCE, filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#2fb7dd", "fill-opacity": 0.19 },
  });
  map.addLayer({
    id: LINE_CASING, type: "line", source: SOURCE, filter: lineFilter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#07151f", "line-width": 7.5, "line-opacity": .76 },
  });
  map.addLayer({
    id: LINE, type: "line", source: SOURCE, filter: lineFilter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#52d4ed", "line-width": 3.5 },
  });
  map.addLayer({
    id: SELECTED_LINE, type: "line", source: SOURCE, filter: ["all", ["==", ["get", "id"], ""], lineFilter],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#f3b766", "line-width": 5 },
  });
  map.addLayer({
    id: POINT, type: "circle", source: SOURCE, filter: pointFilter,
    paint: { "circle-radius": 5.5, "circle-color": "#42c6e4", "circle-stroke-width": 2, "circle-stroke-color": "#0c1a2a" },
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
    }, QGIS_LAYERS.map((layer) => referenceId(layer.id)).find((id) => map.getLayer(id)) ?? FILL);
  }
  if (map.getLayer(STREETS)) map.setLayoutProperty(STREETS, "visibility", visible ? "visible" : "none");
}

function setReferenceLayers(map: LibreMap, data: ReferenceData[], visible: boolean, selectedId: string | null) {
  for (const { layer, collection } of data) {
    const id = referenceId(layer.id);
    if (!map.getSource(id)) {
      map.addSource(id, { type: "geojson", data: collection, generateId: true });
      if (layer.type === "line") {
        map.addLayer({
          id, type: "line", source: id,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": layer.color, "line-width": layer.size, "line-opacity": 0.85 },
        }, FILL);
      } else {
        map.addLayer({
          id, type: "circle", source: id,
          paint: { "circle-color": layer.color, "circle-radius": layer.size, "circle-stroke-width": 1, "circle-stroke-color": "#122437" },
        }, FILL);
      }
    }
    const selected: maplibregl.ExpressionSpecification = [
      "==", ["concat", id + "-", ["to-string", ["id"]]], selectedId ?? "",
    ];
    map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    if (layer.type === "line") {
      map.setPaintProperty(id, "line-color", ["case", selected, "#f3b766", layer.color]);
      map.setPaintProperty(id, "line-width", ["case", selected, layer.size + 2.5, layer.size]);
    } else {
      map.setPaintProperty(id, "circle-color", ["case", selected, "#f3b766", layer.color]);
      map.setPaintProperty(id, "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        12, ["case", selected, 4, 2],
        17, ["case", selected, layer.size + 2, layer.size],
      ]);
    }
  }
}

function getReferenceObject(feature: MapGeoJSONFeature): QgisDesignObject | null {
  const layer = QGIS_LAYERS.find((candidate) => referenceId(candidate.id) === feature.layer.id);
  if (!layer || feature.id == null) return null;
  const index = Number(feature.id) + 1;
  return {
    id: `${referenceId(layer.id)}-${feature.id}`,
    objectCode: `${layer.label} ${String(index).padStart(3, "0")}`,
    objectType: layer.label,
    sourceLayer: layer.label,
    featureIndex: index,
    geometryType: feature.geometry.type,
    designVersion: "Dreieich Mitte · QGIS",
  };
}

function fit(map: LibreMap, points: LonLat[], duration = 350) {
  const bounds = boundsFromPoints(points, 0.035);
  if (!bounds) return;
  const padding = Math.min(54, Math.floor(map.getContainer().clientWidth / 7));
  map.fitBounds([[bounds.minLon, bounds.minLat], [bounds.maxLon, bounds.maxLat]], { padding, maxZoom: 17, duration });
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
  selectedQgisObjectId = null,
  onSelectObject,
  onSelectQgisObject,
  captureMarker = null,
  className = "",
  onlineStyleUrl = null,
  emptyMessage = "暂无工程图层",
}: Props) {
  const configuredStyle = onlineStyleUrl && /^https?:\/\//i.test(onlineStyleUrl) ? onlineStyleUrl : null;
  const [basemap, setBasemap] = useState<Basemap>(configuredStyle ? "configured" : "streets");
  const [showReference, setShowReference] = useState(false);
  const [referenceData, setReferenceData] = useState<ReferenceData[] | null>(null);
  const [referenceError, setReferenceError] = useState(false);
  const [referenceRetry, setReferenceRetry] = useState(0);
  const [styleVersion, setStyleVersion] = useState(0);
  const [styleError, setStyleError] = useState(false);
  const [inRegion, setInRegion] = useState(true);
  const [regional, setRegional] = useState<GeoJSON.FeatureCollection | null>(null);
  const [regionalError, setRegionalError] = useState(false);
  const [rasterState, setRasterState] = useState<"loading" | "loaded" | "unavailable">("loading");
  const [basemapRevision, setBasemapRevision] = useState(0);
  const [collapseCredit, setCollapseCredit] = useState(false);
  const showRegion = basemap === "streets" && inRegion && !regionalError;
  const showRaster = basemap === "streets" && (!inRegion || regionalError);
  const osmVisible = Boolean((showRegion && regional) || (showRaster && rasterState === "loaded"));
  const [unavailable, setUnavailable] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const displayedStyle = useRef<string | null>(null);
  const lastFitKey = useRef<string | null>(null);
  const appliedBasemapRevision = useRef(0);
  const features = useMemo(() => toFeatureCollection(objects), [objects]);
  const allPoints = useMemo(() => objects.flatMap((object) => collectLonLats(object.geometry_wgs84)), [objects]);
  const focusPoints = useMemo(
    () => collectLonLats(objects.find((object) => object.id === selectedObjectId)?.geometry_wgs84),
    [objects, selectedObjectId],
  );
  const referencePoints = useMemo(() => (referenceData ?? []).flatMap(({ collection }) =>
    collection.features.flatMap((feature) => collectLonLats(feature.geometry as GeoJsonGeometry))),
  [referenceData]);
  const latest = useRef({ features, onSelectObject, onSelectQgisObject, basemap });
  latest.current = { features, onSelectObject, onSelectQgisObject, basemap };
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
      const layers = [...ENGINEERING_LAYERS, ...QGIS_LAYERS.map(({ id }) => referenceId(id))]
        .filter((layer) => map.getLayer(layer));
      if (!layers.length) return [];
      return map.queryRenderedFeatures(
        [[event.point.x - 4, event.point.y - 4], [event.point.x + 4, event.point.y + 4]],
        { layers },
      );
    };

    map.on("click", (event) => {
      const found = renderedFeatures(event);
      const engineering = found.find((feature) => feature.source === SOURCE);
      const id = engineering?.properties?.id;
      if (typeof id === "string") {
        latest.current.onSelectObject(id);
        popup.remove();
        return;
      }
      const reference = found[0] && getReferenceObject(found[0]);
      if (reference) {
        latest.current.onSelectQgisObject?.(reference);
        popup.remove();
      }
    });

    map.on("mousemove", (event) => {
      const found = renderedFeatures(event);
      const feature = found.find((item) => item.source === SOURCE) ?? found[0];
      map.getCanvas().style.cursor = feature ? "pointer" : "";
      if (!feature) {
        popup.remove();
        return;
      }
      const reference = feature.source === SOURCE ? null : getReferenceObject(feature);
      const name = reference?.objectCode || String(feature.properties?.name || feature.properties?.code || "工程对象");
      const caption = reference ? "外部设计图层" : String(feature.properties?.type || "设计对象");
      popup.setLngLat(event.lngLat).setDOMContent(tooltip(name, caption)).addTo(map);
    });
    map.on("mouseout", () => popup.remove());

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
    if (!showReference || referenceData || unavailable) return;
    const controller = new AbortController();
    setReferenceError(false);
    Promise.all(QGIS_LAYERS.map(async (layer) => {
      const response = await fetch(`${import.meta.env.BASE_URL}qgis-dreieich/${layer.id}.geojson`, { signal: controller.signal });
      if (!response.ok) throw new Error("外部设计图层读取失败");
      const collection = await response.json() as GeoJSON.FeatureCollection;
      return { layer, collection };
    })).then((rows) => {
      if (!controller.signal.aborted) setReferenceData(rows);
    }).catch(() => {
      if (!controller.signal.aborted) setReferenceError(true);
    });
    return () => controller.abort();
  }, [showReference, referenceData, referenceRetry, unavailable]);

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
    setRegionalBasemap(map, regional, showRegion, QGIS_LAYERS.map(({ id }) => referenceId(id)).find((id) => map.getLayer(id)) ?? FILL);
    setReferenceLayers(map, referenceData ?? [], showReference, selectedQgisObjectId);
  }, [showRaster, regional, showRegion, referenceData, selectedQgisObjectId, showReference, styleVersion, basemapRevision]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer(FILL)) return;
    if (selectedQgisObjectId) {
      lastFitKey.current = null;
      return;
    }
    const focusKey = selectedObjectId ?? (allPoints.length ? features.features.map((feature) => feature.id).join("|") : null);
    const usingReference = !focusKey && showReference && referencePoints.length > 0;
    const key = usingReference ? "reference" : focusKey;
    if (lastFitKey.current === key) return;
    lastFitKey.current = key;
    if (key) fit(map, focusPoints.length ? focusPoints : usingReference ? referencePoints : allPoints);
  }, [allPoints, features, focusPoints, referencePoints, selectedObjectId, selectedQgisObjectId, showReference, styleVersion]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !captureMarker) return;
    const { longitude, latitude } = captureMarker;
    if (!Number.isFinite(longitude) || Math.abs(longitude) > 180 || !Number.isFinite(latitude) || Math.abs(latitude) > 90) return;
    const element = document.createElement("div");
    element.className = captureMarker.synthetic ? "gis-capture-marker is-reference" : "gis-capture-marker";
    element.title = captureMarker.synthetic ? "标注点" : "采集定位";
    const marker = new maplibregl.Marker({ element }).setLngLat([longitude, latitude]).addTo(map);
    markerRef.current = marker;
    return () => {
      marker.remove();
      if (markerRef.current === marker) markerRef.current = null;
    };
  }, [captureMarker]);

  const locate = (points: LonLat[]) => { if (mapRef.current) fit(mapRef.current, points); };
  const chooseBasemap = (mode: Basemap) => { setBasemap(mode); setStyleError(false); };
  const retryBasemap = () => {
    setRegionalError(false);
    setRasterState("loading");
    setBasemapRevision((revision) => revision + 1);
  };

  return (
    <div className={`gis-map-surface ${className}`}>
      <div ref={containerRef} className="gis-map-canvas" />
      {unavailable ? (
        <EngineeringSketch objects={objects} selectedObjectId={selectedObjectId} captureMarker={captureMarker} onSelectObject={onSelectObject} />
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
                <span className="fm-label">叠加图层</span>
                <label>
                  <input type="checkbox" checked={showReference} onChange={(event) => {
                    setShowReference(event.target.checked);
                    if (!event.target.checked) onSelectQgisObject?.(null);
                  }} /> 外部 QGIS 设计图
                </label>
                {showReference && !referenceData && !referenceError && <small role="status">正在读取外部设计图层…</small>}
                {showReference && referenceError && <button type="button" onClick={() => setReferenceRetry((value) => value + 1)}>外部设计图层加载失败，重试</button>}
                {showReference && referenceData && <button type="button" onClick={() => locate(referencePoints)}>定位外部设计图层</button>}
              </div>
            </details>
            <button type="button" title="定位全部工程对象" onClick={() => locate(allPoints.length ? allPoints : referencePoints)} disabled={!allPoints.length && !(showReference && referencePoints.length)}>
              <Crosshair size={15} aria-hidden="true" /> 全图
            </button>
            {captureMarker && <button type="button" title="定位采集点" onClick={() => locate([{ lon: captureMarker.longitude, lat: captureMarker.latitude }])}>
              <LocateFixed size={15} aria-hidden="true" /> 采集点
            </button>}
          </div>
          {osmVisible && <div className="gis-attribution">
            {!collapseCredit && <a className="gis-attribution-credit" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a>}
            <details>
              <summary aria-label="地图来源" title="地图来源"><Info size={14} aria-hidden="true" /></summary>
              <div><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a><span> · ODbL</span></div>
            </details>
          </div>}
          {showRegion && !regional && <div className="gis-map-message fm-operation-inline" role="status">正在读取区域底图…</div>}
          {styleError && <div className="gis-map-message" role="status">项目底图暂不可用，已切换街道底图</div>}
          {!styleError && showRaster && rasterState === "unavailable" && <div className="gis-map-message" role="status">街道底图暂不可用，工程图层可继续查看</div>}
        </>
      )}
      {!features.features.length && !(showReference && referenceData) && (
        <div className="gis-map-empty" role="status">{emptyMessage}</div>
      )}
      <div className="gis-map-legend" aria-label="图例">
        <span><i /> 工程对象</span>
        {selectedObjectId && <span><i className="is-selected" /> 已选对象</span>}
        {captureMarker && (
          <span><i className={captureMarker.synthetic ? "is-reference" : "is-capture"} />
            {captureMarker.synthetic ? "标注点" : "采集点"}
          </span>
        )}
        {showReference && referenceData && <span><i className="is-reference" /> 外部设计要素</span>}
      </div>
    </div>
  );
}

function EngineeringSketch({
  objects, selectedObjectId, captureMarker, onSelectObject,
}: Pick<Props, "objects" | "selectedObjectId" | "captureMarker" | "onSelectObject">) {
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
            fill={object.geometry_type === "LineString" ? "none" : selectable ? "#f3b76655" : "#52d4ed44"}
            stroke={selectable ? "#f3b766" : "#52d4ed"}
            strokeWidth={selectable ? 4.5 : 3}
          ><title>{object.name || object.object_code}</title></path>;
        })}
        {position && <circle cx={position.x} cy={position.y} r={5} fill="#62d4a4" stroke="#e2fff5" strokeWidth={2} />}
      </svg>}
    </div>
  );
}
