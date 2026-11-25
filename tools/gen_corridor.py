"""Sinh dữ liệu cấu hình hành lang (GeoJSON) từ toạ độ trạm RIS theo KML v2.6 dự thảo.

Quy tắc (Đặc tả tóm tắt B1 v0.1, Mục 2.1):
- Tim tuyến gấp khúc RIS-1 -> RIS-2 -> RIS-3 -> RIS-5; rộng 6 km (3 km mỗi bên); hai đầu bằng.
- Ranh khối 1/2, 2/3: đường phân giác tại RIS-2, RIS-3.
- Ranh khối 3/4: vuông góc tim tuyến, qua hình chiếu của RIS-4.
- Ranh trần: Km 34,00 (trong vùng tiếp cận: <600 m AMSL; ngoài: 1.000 m AGL).

Chạy: python3 tools/gen_corridor.py  -> ghi config/corridor/*.geojson và in bảng kiểm tra.
"""
import json, math, pathlib
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, mapping
import numpy as np
import shapely
from shapely.ops import split, substring

OUT = pathlib.Path(__file__).resolve().parent.parent / "config" / "corridor"
HALF_WIDTH_M = 3000.0
CEILING_BREAK_KM = 34.00

STATIONS = {  # id: (lat, lon, ghi chú)
    "RIS-1": (11.1347056, 106.4437417, "Base Nam, khu bay Hưng Thuận"),
    "RIS-2": (11.2254222, 106.3445194, ""),
    "RIS-3": (11.3491472, 106.2861333, ""),
    "RIS-4": (11.4437557, 106.2245598, "Lệch tim tuyến về phía tây"),
    "RIS-5": (11.5560361, 106.1974222, "Base Bắc, khu bay Đồng Rùm"),
}
EXPECTED_KM = {"block-1": 14.77, "block-2": 15.10, "block-3": 12.26, "block-4": 12.59}

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True).transform
to_wgs = Transformer.from_crs("EPSG:32648", "EPSG:4326", always_xy=True).transform
P = {k: Point(to_utm(lon, lat)) for k, (lat, lon, _) in STATIONS.items()}


def unit(dx, dy):
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def cut_line(origin, direction, length=20000.0):
    ox, oy = origin.x, origin.y
    dx, dy = direction
    return LineString([(ox - dx * length, oy - dy * length), (ox + dx * length, oy + dy * length)])


def bisector_dir(a, b, c):
    """Hướng đường phân giác tại b (vuông góc với tổng hai hướng đoạn)."""
    u1 = unit(b.x - a.x, b.y - a.y)
    u2 = unit(c.x - b.x, c.y - b.y)
    sx, sy = unit(u1[0] + u2[0], u1[1] + u2[1])
    return (-sy, sx)


def perp_dir(a, b):
    ux, uy = unit(b.x - a.x, b.y - a.y)
    return (-uy, ux)


def r7(geom):
    """Về WGS84, làm tròn 7 chữ số thập phân."""
    geom = shapely.remove_repeated_points(geom, 0.01)
    g = shapely.transform(geom, lambda c: np.column_stack(to_wgs(c[:, 0], c[:, 1])))
    return json.loads(json.dumps(mapping(g)), parse_float=lambda s: round(float(s), 7))


def main():
    center = LineString([P["RIS-1"].coords[0], P["RIS-2"].coords[0], P["RIS-3"].coords[0], P["RIS-5"].coords[0]])
    corridor = center.buffer(HALF_WIDTH_M, cap_style="flat", join_style="mitre", mitre_limit=5.0)

    km = lambda m: m / 1000.0
    d_ris2 = center.project(P["RIS-2"])
    d_ris3 = center.project(P["RIS-3"])
    d_ris4 = center.project(P["RIS-4"])
    foot4 = center.interpolate(d_ris4)
    offset4 = P["RIS-4"].distance(foot4)

    cuts = [
        cut_line(P["RIS-2"], bisector_dir(P["RIS-1"], P["RIS-2"], P["RIS-3"])),
        cut_line(P["RIS-3"], bisector_dir(P["RIS-2"], P["RIS-3"], P["RIS-5"])),
        cut_line(foot4, perp_dir(P["RIS-3"], P["RIS-5"])),
    ]
    pieces = [corridor]
    for c in cuts:
        nxt = []
        for p in pieces:
            nxt.extend(split(p, c).geoms)
        pieces = nxt
    # sắp theo vị trí trên tim tuyến
    pieces.sort(key=lambda g: center.project(g.representative_point()))
    assert len(pieces) == 4, f"expected 4 blocks, got {len(pieces)}"

    bounds_m = [0.0, d_ris2, d_ris3, d_ris4, center.length]
    blocks = []
    for i, geom in enumerate(pieces, start=1):
        start, end = bounds_m[i - 1], bounds_m[i]
        blocks.append({
            "type": "Feature",
            "id": f"block-{i}",
            "properties": {
                "kind": "operating_block",
                "name": f"Khối {i}",
                "km_start": round(km(start), 2),
                "km_end": round(km(end), 2),
                "length_km": round(km(end - start), 2),
                "lateral_limit_m": HALF_WIDTH_M,
                "status": "draft",
                "source": "KML hành lang v2.6 dự thảo 30/10/2026",
            },
            "geometry": r7(geom),
        })

    # ranh trần tại Km 34,00
    seg_a = substring(center, 0, CEILING_BREAK_KM * 1000)
    seg_b = substring(center, CEILING_BREAK_KM * 1000, center.length)
    ceilings = [
        {"type": "Feature", "id": "ceiling-approach", "properties": {
            "kind": "airspace_constraint", "name": "Trần trong vùng tiếp cận Tân Sơn Nhất",
            "km_start": 0.0, "km_end": CEILING_BREAK_KM,
            "ceiling": {"value": 600, "unit": "m", "reference": "AMSL", "inclusive": False},
            "source": "KML v2.6; Đặc tả B1 v0.1 Mục 2.2"},
         "geometry": r7(seg_a.buffer(HALF_WIDTH_M, cap_style="flat", join_style="mitre", mitre_limit=5.0).intersection(corridor))},
        {"type": "Feature", "id": "ceiling-outside", "properties": {
            "kind": "airspace_constraint", "name": "Trần ngoài vùng tiếp cận",
            "km_start": CEILING_BREAK_KM, "km_end": round(km(center.length), 2),
            "ceiling": {"value": 1000, "unit": "m", "reference": "AGL", "inclusive": True},
            "source": "KML v2.6; Đặc tả B1 v0.1 Mục 2.2"},
         "geometry": r7(seg_b.buffer(HALF_WIDTH_M, cap_style="flat", join_style="mitre", mitre_limit=5.0).intersection(corridor))},
    ]

    lake_km = [(31.17, 40.48), (41.78, 45.11), (48.95, 48.98), (49.64, 49.78)]
    lake = [{"type": "Feature", "id": f"lake-dau-tieng-{j}", "properties": {
        "kind": "reference_layer", "name": "Mặt nước hồ Dầu Tiếng trên tim tuyến",
        "km_start": a, "km_end": b, "note": "Chỉ là lớp tham chiếu; khối 3D chưa trừ phạm vi bảo vệ hồ"},
        "geometry": r7(substring(center, a * 1000, b * 1000))} for j, (a, b) in enumerate(lake_km, 1)]

    stations = []
    for k, (lat, lon, note) in STATIONS.items():
        props = {"kind": "ris_station", "name": k, "note": note,
                 "km_on_centerline": round(km(center.project(P[k])), 2),
                 "offset_from_centerline_m": round(P[k].distance(center.interpolate(center.project(P[k])))),}
        stations.append({"type": "Feature", "id": k, "properties": props,
                         "geometry": {"type": "Point", "coordinates": [lon, lat]}})

    def fc(name, feats):
        return {"type": "FeatureCollection", "name": name,
                "crs_note": "WGS84 (EPSG:4326); toạ độ lon, lat; hình học dự thảo, chưa phải vùng trời đã phê duyệt",
                "features": feats}

    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "centerline.geojson": fc("centerline", [{"type": "Feature", "id": "centerline", "properties": {
            "kind": "centerline", "length_km": round(km(center.length), 2), "vertices": ["RIS-1", "RIS-2", "RIS-3", "RIS-5"]},
            "geometry": r7(center)}]),
        "blocks.geojson": fc("blocks", blocks),
        "constraints.geojson": fc("constraints", ceilings),
        "reference.geojson": fc("reference", lake),
        "ris_stations.geojson": fc("ris_stations", stations),
    }
    for name, data in files.items():
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"Tim tuyến: {km(center.length):.2f} km (đặc tả: 54,72)")
    print(f"RIS-4: lệch {offset4:.0f} m (đặc tả: 2.113), hình chiếu Km {km(d_ris4):.2f} (đặc tả: 42,12)")
    ok = True
    for b in blocks:
        exp = EXPECTED_KM[b["id"]]
        got = b["properties"]["length_km"]
        flag = "OK" if abs(got - exp) <= 0.02 else "LỆCH"
        ok &= flag == "OK"
        print(f"{b['id']}: Km {b['properties']['km_start']}–{b['properties']['km_end']}, dài {got} km (đặc tả {exp}) {flag}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
