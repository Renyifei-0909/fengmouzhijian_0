import type { EngineeringObject, GeoJsonGeometry } from "../../lib/api";
import networkLayouts from "./binjiangNetwork.json";

export const BINJIANG_DEMO_PACKAGE_ID = "internal-binjiang-demo";

const LAYOUT_TYPES = {
  trunk: { name: "主干管道", type: "telecom_duct" },
  branch: { name: "支线管道", type: "telecom_duct" },
  access: { name: "街区接入段", type: "telecom_duct" },
  junction: { name: "分歧井", type: "telecom_manhole" },
  manhole: { name: "通信人孔", type: "telecom_manhole" },
  handhole: { name: "通信手孔", type: "telecom_handhole" },
  cabinet: { name: "光缆交接箱", type: "fiber_cross_connect_cabinet" },
  terminal: { name: "接入末端", type: "fiber_distribution_box" },
} as const;

type LayoutRow = {
  key: string;
  road: string;
  kind: keyof typeof LAYOUT_TYPES;
  coordinates: number[] | number[][];
  lengthM?: number;
  ties: string[];
};

const layouts = networkLayouts as Record<string, LayoutRow[] | undefined>;

export function createNetworkLayoutObjects(projectId: string, projectCode: string): EngineeringObject[] {
  const district = projectCode.split("-")[1];
  return (layouts[projectCode] ?? []).map(({ key, road, kind, coordinates, lengthM, ties }) => {
    const isLine = lengthM !== undefined;
    const geometryType = isLine ? "LineString" : "Point";
    const { name, type } = LAYOUT_TYPES[kind];
    const code = `${district}-${isLine ? "L" : "N"}${key}`;
    return {
      id: `layout-${projectCode}-${key}`,
      project_id: projectId,
      design_package_id: BINJIANG_DEMO_PACKAGE_ID,
      object_code: code,
      object_type: type,
      name: `${road} · ${name}`,
      source_layer: "管网布设",
      source_feature_id: code,
      geometry_type: geometryType,
      geometry_wgs84: { type: geometryType, coordinates },
      geometry_source_crs_epsg: 4326,
      attributes_snapshot: {
        道路: road,
        布设类型: name,
        layout_kind: kind,
        length_m: lengthM,
        linked_routes: ties.map((route) => `layout-${projectCode}-${route}`),
      },
      expected_rules: {},
      design_version: "布设方案",
      created_at: "",
    };
  });
}

type DemoObject = {
  code: string;
  name: string;
  type: string;
  layer: string;
  geometry: GeoJsonGeometry;
  attributes: Record<string, unknown>;
};

const DEMO_OBJECTS: DemoObject[] = [
  { code: "GD-BJ-001", name: "江南大道通信管道段（西兴路—江晖路）", type: "telecom_duct", layer: "通信管道", geometry: { type: "LineString", coordinates: [[120.2112,30.2087],[120.2025,30.2059],[120.1941,30.2032],[120.1858,30.2004]] }, attributes: { duct_spec: "6孔梅花管", route_level: "主干" } },
  { code: "GD-BJ-002", name: "滨盛路通信管道段（江汉路—江虹路）", type: "telecom_duct", layer: "通信管道", geometry: { type: "LineString", coordinates: [[120.1948,30.1902],[120.2034,30.1928],[120.2126,30.1954],[120.2211,30.1981]] }, attributes: { duct_spec: "4孔波纹管", route_level: "次干" } },
  { code: "GD-BJ-003", name: "滨安路通信管道段（时代大道—风情大道）", type: "telecom_duct", layer: "通信管道", geometry: { type: "LineString", coordinates: [[120.1738,30.1804],[120.1841,30.1826],[120.1955,30.1847],[120.2074,30.1871]] }, attributes: { duct_spec: "6孔栅格管", route_level: "主干" } },
  { code: "GD-BJ-004", name: "闻涛路通信管道段（东信大道—江陵路）", type: "telecom_duct", layer: "通信管道", geometry: { type: "LineString", coordinates: [[120.1537,30.1991],[120.1662,30.2035],[120.1793,30.2074],[120.1921,30.2116]] }, attributes: { duct_spec: "4孔梅花管", route_level: "次干" } },
  { code: "GL-BJ-001", name: "滨江核心区96芯主干光缆段", type: "backbone_fiber", layer: "主干光缆", geometry: { type: "LineString", coordinates: [[120.1811,30.1978],[120.1918,30.2009],[120.2025,30.2042],[120.2142,30.2075]] }, attributes: { cable_type: "GYTA", fiber_count: 96, network_level: "主干层" } },
  { code: "GL-BJ-002", name: "西兴片区48芯主干光缆段", type: "backbone_fiber", layer: "主干光缆", geometry: { type: "LineString", coordinates: [[120.2154,30.1868],[120.2202,30.1935],[120.2253,30.2008],[120.2316,30.2071]] }, attributes: { cable_type: "GYTS", fiber_count: 48, network_level: "汇聚层" } },
  { code: "JR-BJ-001", name: "物联网产业园24芯接入光缆段", type: "access_fiber", layer: "接入光缆", geometry: { type: "LineString", coordinates: [[120.1981,30.1885],[120.2015,30.1847],[120.2056,30.1812]] }, attributes: { cable_type: "GYTA", fiber_count: 24, network_level: "接入层" } },
  { code: "JR-BJ-002", name: "滨江高新区12芯用户引入光缆段", type: "access_fiber", layer: "接入光缆", geometry: { type: "LineString", coordinates: [[120.1864,30.2051],[120.1829,30.2084],[120.1796,30.2119]] }, attributes: { cable_type: "蝶形引入光缆", fiber_count: 12, network_level: "用户引入" } },
  { code: "GJ-BJ-001", name: "江南大道1号光缆交接箱", type: "fiber_cross_connect_cabinet", layer: "光缆交接设施", geometry: { type: "Point", coordinates: [120.2025,30.2042] }, attributes: { capacity_core: 576, installation: "落地式" } },
  { code: "GJ-BJ-002", name: "滨盛路2号光缆交接箱", type: "fiber_cross_connect_cabinet", layer: "光缆交接设施", geometry: { type: "Point", coordinates: [120.2126,30.1954] }, attributes: { capacity_core: 288, installation: "落地式" } },
  { code: "FX-BJ-001", name: "江二社区1号光缆分纤箱", type: "fiber_distribution_box", layer: "光缆分纤设施", geometry: { type: "Point", coordinates: [120.1905,30.1973] }, attributes: { capacity_core: 72, installation: "壁挂式" } },
  { code: "FX-BJ-002", name: "中兴社区2号光缆分纤箱", type: "fiber_distribution_box", layer: "光缆分纤设施", geometry: { type: "Point", coordinates: [120.1995,30.2079] }, attributes: { capacity_core: 48, installation: "壁挂式" } },
  { code: "RK-BJ-001", name: "江南大道—江晖路通信人孔", type: "telecom_manhole", layer: "通信人孔", geometry: { type: "Point", coordinates: [120.1941,30.2032] }, attributes: { chamber_type: "标准三通人孔", cover_level: "重型" } },
  { code: "RK-BJ-002", name: "滨盛路—江虹路通信人孔", type: "telecom_manhole", layer: "通信人孔", geometry: { type: "Point", coordinates: [120.2126,30.1954] }, attributes: { chamber_type: "标准直通人孔", cover_level: "重型" } },
  { code: "RK-BJ-003", name: "滨安路—江陵路通信手孔", type: "telecom_handhole", layer: "通信手孔", geometry: { type: "Point", coordinates: [120.2074,30.1871] }, attributes: { chamber_type: "小号手孔", cover_level: "轻型" } },
  { code: "JF-BJ-001", name: "滨江核心汇聚接入机房", type: "access_room", layer: "通信机房", geometry: { type: "Point", coordinates: [120.2011,30.2004] }, attributes: { room_level: "汇聚机房", power_system: "-48V直流" } },
  { code: "JF-BJ-002", name: "西兴片区综合接入机房", type: "access_room", layer: "通信机房", geometry: { type: "Point", coordinates: [120.2211,30.1878] }, attributes: { room_level: "接入机房", power_system: "-48V直流" } },
  { code: "JZ-BJ-001", name: "滨江高新区宏基站", type: "macro_base_station", layer: "无线基站", geometry: { type: "Point", coordinates: [120.2131,30.1930] }, attributes: { station_type: "宏基站", support_system: "通信铁塔" } },
];

export function createBinjiangDemoObjects(projectId: string): EngineeringObject[] {
  return DEMO_OBJECTS.map((object, index) => ({
    id: `binjiang-demo-${index + 1}`,
    project_id: projectId,
    design_package_id: BINJIANG_DEMO_PACKAGE_ID,
    object_code: object.code,
    object_type: object.type,
    name: object.name,
    source_layer: object.layer,
    source_feature_id: `BJ-DEMO-${String(index + 1).padStart(3, "0")}`,
    geometry_type: object.geometry.type,
    geometry_wgs84: object.geometry,
    geometry_source_crs_epsg: 4326,
    attributes_snapshot: { ...object.attributes, data_classification: "内置示范数据" },
    expected_rules: { geometry_required: true, field_capture_required: true },
    design_version: "BJ-TELCOM-DEMO-2026.1",
    created_at: "2026-11-06T00:00:00Z",
  }));
}
