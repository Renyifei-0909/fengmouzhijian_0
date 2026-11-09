"""Build illustrative project layouts from a cached Overpass road extract."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path

from pyproj import Transformer
from shapely import LineString, box
from shapely.ops import linemerge, substring, transform

AREAS = {
    "FM-BJ-2607": (
        (120.149, 30.179, 120.224, 30.228),
        "江南大道 滨盛路 滨安路 闻涛路 江晖路 江虹路 江陵路 滨兴路 滨和路 建业路 长河路 江汉路",
        "月明路 丹枫路 阡陌路 信诚路 网商路 聚才路 秋溢路 新联路 科技馆街",
    ),
    "FM-PY-2608": (
        (120.126, 30.158, 120.153, 30.184),
        "浦沿路 闻涛路 东冠路 滨文路 东信大道 明德路 生地街 杨家墩街 六和路 西浦路",
        "新浦路 坚塔街 新和路 至善路 连江街 杨月巷 创智街 冠山路",
    ),
    "FM-XX-2611": (
        (120.214, 30.171, 120.235, 30.198),
        "西兴路 滨康路 滨安路 滨兴路 固陵路 江汉东路 风情大道 古塘路 西陵路 官河路",
        "湖园一路 湖园三路 越城巷 东流路 北塘路 老塘路 青年路 知困弄",
    ),
}

TO_METERS = Transformer.from_crs(4326, 32651, always_xy=True).transform
TO_WGS84 = Transformer.from_crs(32651, 4326, always_xy=True).transform


@dataclass
class Route:
    key: str
    road: str
    kind: str
    line: LineString
    ties: tuple[str, ...] = ()


def line_parts(geometry):
    if geometry.geom_type == "LineString":
        if not geometry.is_empty:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from line_parts(part)


def road_lines(elements):
    roads = defaultdict(list)
    for row in elements:
        tags = row.get("tags", {})
        name = tags.get("name:zh-Hans") or tags.get("name:zh") or tags.get("name")
        highway = tags.get("highway", "")
        if not name or not highway or highway.endswith("_link"):
            continue
        if tags.get("tunnel") not in (None, "no") or tags.get("bridge") not in (None, "no") or tags.get("layer") not in (None, "0"):
            continue
        coordinates = [(point["lon"], point["lat"]) for point in row.get("geometry", []) if point]
        if len(coordinates) >= 2:
            roads[name].append(LineString(coordinates))
    return roads


def coords(geometry):
    return [[round(x, 6), round(y, 6)] for x, y in transform(TO_WGS84, geometry).coords]


def candidates_on(roads, name, region):
    clipped = [part for line in roads[name] for part in line_parts(line.intersection(region))]
    if not clipped:
        return []
    candidates = [transform(TO_METERS, part).simplify(1.4) for part in line_parts(linemerge(clipped))]
    return sorted(candidates, key=lambda item: (-item.length, item.bounds))


def main_routes(roads, region, corridors):
    routes = []
    for corridor_index, name in enumerate(corridors.split()):
        kept = []
        for line in candidates_on(roads, name, region):
            if line.length < 170 or len(kept) == 3:
                break
            # Dual carriageways represent one corridor at this scale.
            if any(line.intersection(prior.buffer(25)).length / line.length > .83 for prior in kept):
                continue
            kept.append(line)
            key = f"{corridor_index + 1:02d}-{len(kept)}"
            routes.append(Route(key, name, "trunk" if corridor_index < 4 else "branch", line))
    return routes


def intersections(a, b):
    cross = a.intersection(b)
    if cross.geom_type == "Point" and not cross.is_empty:
        return [cross]
    return list(cross.geoms) if cross.geom_type == "MultiPoint" else []


def access_routes(roads, region, streets, mains):
    for index, name in enumerate(streets.split(), 1):
        best = None
        for line in candidates_on(roads, name, region):
            if line.length < 135:
                break
            for route in mains:
                for point in intersections(line, route.line):
                    start = line.project(point)
                    end = line.length if line.length - start >= start else 0
                    length = min(abs(end - start), 380)
                    if length < 115 or (best is not None and length <= best.line.length + 0.01):
                        continue
                    end = start + length if end > start else start - length
                    best = Route(f"a{index:02d}", name, "access", substring(line, start, end), (route.key,))
        if best is not None:
            yield best


def node_row(key, road, kind, point, *routes):
    return {"key": key, "road": road, "kind": kind, "coordinates": coords(point)[0], "ties": routes}


def project_layout(roads, extent, corridors, access_streets):
    region = box(*extent)
    mains = main_routes(roads, region, corridors)
    access = list(access_routes(roads, region, access_streets, mains))
    routes = mains + access
    result = [
        {"key": route.key, "road": route.road, "kind": route.kind, "coordinates": coords(route.line),
         "lengthM": round(route.line.length), "ties": route.ties}
        for route in routes
    ]
    nodes = []
    for index, left in enumerate(routes):
        for right in routes[index + 1:]:
            if left.road == right.road or (left.kind == right.kind == "access"):
                continue
            crossing = intersections(left.line, right.line)
            if left.key in right.ties and not crossing:
                # Clipping at the tie may introduce a submillimetre gap.
                crossing = [right.line.interpolate(0)]
            for number, point in enumerate(sorted(crossing, key=left.line.project), 1):
                if any(point.distance(previous) < 28 for previous in nodes):
                    continue
                nodes.append(point)
                result.append(node_row(f"j{left.key}-{right.key}-{number}", f"{left.road} / {right.road}",
                                       "junction", point, left.key, right.key))

    for route in routes:
        if route.kind == "access":
            point = route.line.interpolate(route.line.length)
            if all(point.distance(previous) >= 28 for previous in nodes):
                nodes.append(point)
                result.append(node_row(f"{route.key}-e", route.road, "terminal", point, route.key))
            continue
        step = 175 if route.kind == "trunk" else 145
        for index, nominal in enumerate(range(step // 2, int(route.line.length), step), 1):
            distance = min(nominal + (index * 29 % 59) - 29, route.line.length - 12)
            point = route.line.interpolate(distance)
            if any(point.distance(previous) < 45 for previous in nodes):
                continue
            nodes.append(point)
            kind = "cabinet" if route.kind == "trunk" and index % 6 == 3 else "manhole" if index % 3 == 1 else "handhole"
            result.append(node_row(f"{route.key}-{index:02d}", route.road, kind, point, route.key))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overpass", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    roads = road_lines(json.loads(args.overpass.read_text())["elements"])
    projects = {code: project_layout(roads, *area) for code, area in AREAS.items()}
    groups = []
    for code, records in projects.items():
        rows = ",\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in records)
        groups.append(f'"{code}": [\n{rows}\n]')
        print(code, dict(sorted((kind, sum(item["kind"] == kind for item in records)) for kind in {item["kind"] for item in records})))
    args.output.write_text("{\n" + ",\n".join(groups) + "\n}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
