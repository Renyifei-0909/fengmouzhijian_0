import React, { useEffect, useRef } from "react";
import maplibregl, { GeoJSONSource, Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { EngineeringObject, GeoJsonGeometry } from "../../lib/api";
import { boundsFromPoints, collectLonLats } from "../../lib/geoDisplay";

/** Offline-capable blank style — no network tiles required. */
export const OFFLINE_BLANK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  name: "fengmou-offline-blank",
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#e8f1f8",
      },
    },
  ],
};

const SOURCE_ID = "engineering-objects";
const FILL_LAYER = "engineering-fill";
const LINE_LAYER = "engineering-line";
const POINT_LAYER = "engineering-point";
const SELECTED_LINE = "engineering-selected-line";
const SELECTED_POINT = "engineering-selected-point";

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id: string;
    properties: {
      id: string;
      object_code: string;
      object_type: string;
      design_version: string;
    };
    geometry: GeoJsonGeometry;
  }>;
};

function toFeatureCollection(objects: EngineeringObject[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: objects
      .filter((o) => o.geometry_wgs84?.type && o.geometry_wgs84.coordinates)
      .map((o) => ({
        type: "Feature" as const,
        id: o.id,
        properties: {
          id: o.id,
          object_code: o.object_code,
          object_type: o.object_type,
          design_version: o.design_version,
        },
        geometry: o.geometry_wgs84,
      })),
  };
}

export type CaptureMarker = {
  longitude: number;
  latitude: number;
  synthetic?: boolean;
};

type GeoMapProps = {
  objects: EngineeringObject[];
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
  captureMarker?: CaptureMarker | null;
  className?: string;
  /** Optional online basemap style URL; offline blank style is always the default. */
  onlineStyleUrl?: string | null;
};

export const GeoMap: React.FC<GeoMapProps> = ({
  objects,
  selectedObjectId,
  onSelectObject,
  captureMarker = null,
  className = "",
  onlineStyleUrl = null,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const onSelectRef = useRef(onSelectObject);
  onSelectRef.current = onSelectObject;

  // Init map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const useOnline =
      Boolean(onlineStyleUrl) &&
      typeof onlineStyleUrl === "string" &&
      onlineStyleUrl.startsWith("http");

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: useOnline ? onlineStyleUrl! : OFFLINE_BLANK_STYLE,
      center: [7.56, 51.44],
      zoom: 12,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        promoteId: "id",
      });
      map.addLayer({
        id: FILL_LAYER,
        type: "fill",
        source: SOURCE_ID,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "fill-color": "#0ea5e9",
          "fill-opacity": 0.18,
        },
      });
      map.addLayer({
        id: LINE_LAYER,
        type: "line",
        source: SOURCE_ID,
        filter: ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]],
        paint: {
          "line-color": "#0284c7",
          "line-width": 3,
        },
      });
      map.addLayer({
        id: POINT_LAYER,
        type: "circle",
        source: SOURCE_ID,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 7,
          "circle-color": "#0369a1",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addLayer({
        id: SELECTED_LINE,
        type: "line",
        source: SOURCE_ID,
        filter: [
          "all",
          ["==", ["get", "id"], ""],
          ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]],
        ],
        paint: {
          "line-color": "#ea580c",
          "line-width": 5,
        },
      });
      map.addLayer({
        id: SELECTED_POINT,
        type: "circle",
        source: SOURCE_ID,
        filter: ["all", ["==", ["get", "id"], ""], ["==", ["geometry-type"], "Point"]],
        paint: {
          "circle-radius": 9,
          "circle-color": "#ea580c",
          "circle-stroke-width": 3,
          "circle-stroke-color": "#fff7ed",
        },
      });

      const handleClick = (e: maplibregl.MapMouseEvent) => {
        const features = map.queryRenderedFeatures(e.point, {
          layers: [FILL_LAYER, LINE_LAYER, POINT_LAYER],
        });
        const id = features[0]?.properties?.id;
        if (typeof id === "string" && id) onSelectRef.current(id);
      };
      map.on("click", FILL_LAYER, handleClick);
      map.on("click", LINE_LAYER, handleClick);
      map.on("click", POINT_LAYER, handleClick);
      map.on("mouseenter", LINE_LAYER, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", LINE_LAYER, () => {
        map.getCanvas().style.cursor = "";
      });
      map.on("mouseenter", POINT_LAYER, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", POINT_LAYER, () => {
        map.getCanvas().style.cursor = "";
      });
    });

    // If online style fails, fall back to offline blank so the page never whites out.
    map.on("error", (ev) => {
      const msg = String(ev.error?.message || ev.error || "");
      if (useOnline && /style|tile|fetch|network|CORS/i.test(msg)) {
        try {
          map.setStyle(OFFLINE_BLANK_STYLE);
        } catch {
          /* ignore */
        }
      }
    });

    mapRef.current = map;
    return () => {
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [onlineStyleUrl]);

  // Sync features + fit bounds.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const apply = () => {
      const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
      if (!source) return;
      source.setData(toFeatureCollection(objects) as unknown as GeoJSON.FeatureCollection);

      const points = objects.flatMap((o) => collectLonLats(o.geometry_wgs84 as GeoJsonGeometry));
      const bounds = boundsFromPoints(points, 0.18);
      if (bounds) {
        map.fitBounds(
          [
            [bounds.minLon, bounds.minLat],
            [bounds.maxLon, bounds.maxLat],
          ],
          { padding: 48, maxZoom: 16, duration: 400 },
        );
      }
    };

    if (map.isStyleLoaded() && map.getSource(SOURCE_ID)) {
      apply();
    } else {
      map.once("load", apply);
      // Style swap may re-fire idle
      map.once("idle", () => {
        if (map.getSource(SOURCE_ID)) apply();
      });
    }
  }, [objects]);

  // Highlight selection.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const id = selectedObjectId || "";
    const setFilter = () => {
      if (!map.getLayer(SELECTED_LINE)) return;
      map.setFilter(SELECTED_LINE, [
        "all",
        ["==", ["get", "id"], id],
        ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]],
      ]);
      map.setFilter(SELECTED_POINT, [
        "all",
        ["==", ["get", "id"], id],
        ["==", ["geometry-type"], "Point"],
      ]);
    };
    if (map.isStyleLoaded()) setFilter();
    else map.once("idle", setFilter);
  }, [selectedObjectId]);

  // Capture GPS marker.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markerRef.current?.remove();
    markerRef.current = null;
    if (
      captureMarker &&
      Number.isFinite(captureMarker.longitude) &&
      Number.isFinite(captureMarker.latitude)
    ) {
      const el = document.createElement("div");
      el.className = captureMarker.synthetic
        ? "h-3.5 w-3.5 rounded-full border-2 border-white bg-violet-500 shadow-md"
        : "h-3.5 w-3.5 rounded-full border-2 border-white bg-emerald-500 shadow-md";
      el.title = captureMarker.synthetic ? "合成演示定位" : "采集定位";
      markerRef.current = new maplibregl.Marker({ element: el })
        .setLngLat([captureMarker.longitude, captureMarker.latitude])
        .addTo(map);
    }
  }, [captureMarker]);

  return (
    <div className={`relative overflow-hidden ${className}`}>
      <div ref={containerRef} className="h-full w-full min-h-[280px]" />
      <div className="pointer-events-none absolute bottom-3 left-3 max-w-[min(100%,22rem)] rounded-xl border border-slate-200/90 bg-white/90 px-2.5 py-1.5 text-[10px] leading-4 text-slate-600 shadow-sm backdrop-blur">
        位置与几何来源于当前设计数据
      </div>
    </div>
  );
};
