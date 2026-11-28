"""Hình học phẳng cục bộ cho giám sát tuân thủ, không cần thư viện GIS khi chạy.

Hành lang dài 55 km quanh vĩ độ 11°B: chiếu phẳng cục bộ theo bán kính cong của ellipsoid WGS84 tại vĩ độ điểm xét
(bán kính kinh tuyến M, bán kính vòng thẳng đứng N). Khoảng cách ngang tới tim tuyến và chiều dài tim tuyến khớp đường
trắc địa WGS84 trong 0,1 m (bài kiểm thử so với pyproj.Geod). Lưới UTM 48N lệch thêm hệ số tỷ lệ 0,1–0,2%.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

R_EARTH = 6_371_008.8
_A, _E2 = 6_378_137.0, 6.69437999014e-3  # WGS84
Pt = tuple[float, float]  # (lon, lat), độ


def scale(lat0: float) -> tuple[float, float]:
    """Mét trên một độ kinh, một độ vĩ tại vĩ độ lat0."""
    s = math.sin(math.radians(lat0))
    w = 1.0 - _E2 * s * s
    n, m = _A / math.sqrt(w), _A * (1.0 - _E2) / w ** 1.5
    return math.radians(1.0) * n * math.cos(math.radians(lat0)), math.radians(1.0) * m


def _xy(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    kx, ky = scale(lat0)
    return lon * kx, lat * ky


def distance_m(a: Pt, b: Pt) -> float:
    """Khoảng cách trên ellipsoid WGS84, chiếu cục bộ tại vĩ độ giữa (sai < 0,01% với khoảng cách dưới 100 km)."""
    kx, ky = scale((a[1] + b[1]) / 2.0)
    return math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * ky)


class Polyline:
    """Tim tuyến: khoảng cách ngang tới tim tuyến và vị trí Km dọc tuyến."""

    def __init__(self, coords: Sequence[Sequence[float]], nominal_length_m: float | None = None):
        """`nominal_length_m`: chiều dài công bố (Km đặc tả đo trên UTM 48N); vị trí dọc tuyến co giãn theo đó để mốc Km
        của ràng buộc (ví dụ Km 34,00) khớp với đặc tả."""
        self.pts = [(float(c[0]), float(c[1])) for c in coords]
        cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            cum.append(cum[-1] + distance_m(a, b))
        k = nominal_length_m / cum[-1] if nominal_length_m and cum[-1] else 1.0
        self.cum = [c * k for c in cum]

    @property
    def length_m(self) -> float:
        return self.cum[-1]

    def project(self, p: Pt) -> tuple[float, float]:
        """Trả (khoảng cách ngang tới tim tuyến, Km dọc tuyến tại chân đường vuông góc), mét."""
        best = (math.inf, 0.0)
        for i, (a, b) in enumerate(zip(self.pts, self.pts[1:])):
            lat0 = p[1]
            ax, ay = _xy(*a, lat0)
            bx, by = _xy(*b, lat0)
            px, py = _xy(*p, lat0)
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            cx, cy = ax + t * dx, ay + t * dy
            d = math.hypot(px - cx, py - cy)
            if d < best[0]:
                best = (d, self.cum[i] + t * (self.cum[i + 1] - self.cum[i]))
        return best


def in_ring(p: Pt, ring: Sequence[Sequence[float]]) -> bool:
    """Điểm trong vòng đa giác (tia ngang), toạ độ (lon, lat)."""
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, list(ring[1:]) + [ring[0]]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_polygon(p: Pt, geometry: dict) -> bool:
    if geometry["type"] == "Polygon":
        polys = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polys = geometry["coordinates"]
    else:
        return False
    return any(in_ring(p, poly[0]) and not any(in_ring(p, h) for h in poly[1:]) for poly in polys)


def distance_to_ring_m(p: Pt, ring: Sequence[Sequence[float]]) -> float:
    """Khoảng cách tới cạnh gần nhất của vòng (dương), mét."""
    return Polyline(list(ring) + ([ring[0]] if tuple(ring[0]) != tuple(ring[-1]) else [])).project(p)[0]
