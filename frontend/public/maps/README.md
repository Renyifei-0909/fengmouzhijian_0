# 滨江区域底图

`binjiang.geojson` 采用 OpenStreetMap 贡献者提供的数据，按 [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) 提供。[OpenStreetMap 版权与署名](https://www.openstreetmap.org/copyright)

数据于 2026-11-06 提取，使用 [Overpass API](https://overpass-api.de/)。覆盖范围为东经 120.123° 至 120.269°、北纬 30.148° 至 30.258°。提取区域内道路、河流、水面、绿地和社区地名，裁剪并简化几何，以 WGS84 GeoJSON 单独存放。项目工程对象保存在系统业务数据中。

补充的道路标签与桥梁线均取自同一份 Overpass 原始结果。道路标签放在裁剪后连续路段的中点，主干道、支路在不同缩放级别显示，桥梁以 OSM 的 `bridge` 属性识别。

近景建筑轮廓和少量道路标签来自 [OpenFreeMap](https://openfreemap.org/) 的 [OpenMapTiles](https://www.openmaptiles.org/) 数据，使用 2026-11-01 快照在滨江沿江区域的四张 14 级瓦片。提取后与底图一同离线加载，建筑细节在高缩放级别显示。

地图首次显示署名，随后折叠到右下角的来源入口。[交互地图署名指引](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)
