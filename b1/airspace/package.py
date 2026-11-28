"""Gói dữ liệu kích hoạt khối (Đặc tả B1 v0.1, Mục 4.4; F-03; I-05).

Nội dung: mã khối, tầng độ cao, khung giờ, toạ độ đỉnh WGS84, thông tin chuyến bay.
Ba dạng cùng một nội dung: văn bản đưa thẳng vào công văn hiệp đồng, GeoJSON (RFC 7946) và ED-269 (dạng JSON vùng địa lý UAS).

Mã băm SHA-256 tính trên nội dung chuẩn hoá (JSON sắp khoá), nên lần hiệp đồng gắn đúng với bản đã gửi.
Bản ED-269 viết theo cấu trúc công khai của mô hình UASZone (EUROCAE ED-269, Quy định EU 2019/947);
chưa đối chiếu với văn bản chuẩn đầy đủ, cần rà lại khi có bản chuẩn và khi có quy chế phối hợp I-05.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
COUNTRY = "VNM"
AUTHORITY = "Bộ phận quản lý hành lang bay B1 Tây Ninh"


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256(content: dict) -> str:
    return hashlib.sha256(canonical(content)).hexdigest()


def _ring_ccw(ring: list[list[float]]) -> list[list[float]]:
    """RFC 7946 §3.1.6: vòng ngoài ngược chiều kim đồng hồ."""
    area2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))
    return ring if area2 > 0 else list(reversed(ring))


def dms(value: float, pos: str, neg: str) -> str:
    h = pos if value >= 0 else neg
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    if round(s, 1) >= 60.0:
        m, s = m + 1, 0.0
    if m >= 60:
        d, m = d + 1, 0
    return f"{d}°{m:02d}'{s:04.1f}\"{h}"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build(activation, block, envelope: list[dict]) -> dict:
    """Nội dung chuẩn của gói. `envelope`: các trần bao từ ràng buộc vùng trời chạm khối."""
    ring = _ring_ccw([list(map(float, p)) for p in block.geometry["coordinates"][0]])
    return {
        "package_id": str(activation.id),
        "block": {"id": block.id, "name": block.name, "km_start": block.km_start, "km_end": block.km_end,
                  "data_status": block.data_status, "source": block.source},
        "vertical": {"floor_m": activation.floor_m, "floor_ref": activation.floor_ref,
                     "ceiling_m": activation.ceiling_m, "ceiling_ref": activation.ceiling_ref, "envelope": envelope},
        "window": {"start": _iso(activation.starts_at), "end": _iso(activation.ends_at), "tz_display": "Asia/Ho_Chi_Minh"},
        "polygon": ring,
        "flights": activation.flights,
        "authority": AUTHORITY,
    }


def _vn(x: float, d: int = 2) -> str:
    """Số kiểu Việt Nam trong văn bản: dấu phẩy thập phân."""
    return f"{x:.{d}f}".replace(".", ",")


def _layer(v: dict) -> str:
    if v["floor_ref"] == v["ceiling_ref"]:
        return f"từ {v['floor_m']:g} m đến {v['ceiling_m']:g} m {_ref(v['floor_ref'])}"
    return f"từ {v['floor_m']:g} m {_ref(v['floor_ref'])} đến {v['ceiling_m']:g} m {_ref(v['ceiling_ref'])}"


def as_text(c: dict) -> str:
    st = dt.datetime.fromisoformat(c["window"]["start"]).astimezone(TZ)
    en = dt.datetime.fromisoformat(c["window"]["end"]).astimezone(TZ)
    v = c["vertical"]
    lines = [
        f"GÓI DỮ LIỆU KÍCH HOẠT KHỐI VÙNG TRỜI – {c['block']['name'].upper()} ({c['block']['id']})",
        f"Mã gói: {c['package_id']}",
        f"Mã băm SHA-256: {sha256(c)}",
        "",
        f"1. Khối: {c['block']['name']}, từ Km {_vn(c['block']['km_start'])} đến Km {_vn(c['block']['km_end'])} trên tim tuyến hành lang.",
        f"2. Tầng độ cao khai thác: {_layer(v)}.",
    ]
    if v["envelope"]:
        env = "; ".join(f"{e['name']}: {'dưới' if not e.get('inclusive', True) else 'đến'} {e['value']:g} m {_ref(e['reference'])}"
                        for e in v["envelope"])
        lines.append(f"   Trần bao của hành lang trong khối: {env}.")
    lines += [
        f"3. Khung giờ: từ {st:%H:%M} ngày {st:%d/%m/%Y} đến {en:%H:%M} ngày {en:%d/%m/%Y} (giờ Việt Nam, UTC+7);"
        f" UTC: {c['window']['start']} – {c['window']['end']}.",
        "4. Toạ độ đỉnh (WGS84):",
    ]
    for i, (lon, lat) in enumerate(c["polygon"][:-1], 1):
        lines.append(f"   Đỉnh {i}: {dms(lat, 'N', 'S')} {dms(lon, 'E', 'W')} ({lat:.7f}, {lon:.7f})")
    lines.append(f"5. Chuyến bay ({len(c['flights'])}):")
    if not c["flights"]:
        lines.append("   (chưa có chuyến)")
    for i, f in enumerate(c["flights"], 1):
        fs = dt.datetime.fromisoformat(f["starts_at"]).astimezone(TZ)
        fe = dt.datetime.fromisoformat(f["ends_at"]).astimezone(TZ)
        lines.append(f"   {i}. Số {f.get('queue_no') or '—'}; nhà khai thác: {f.get('operator') or '—'}; phương tiện: "
                     f"{f.get('model') or '—'}, số đăng ký {f.get('vn_registration_no') or '—'}, mã nhận dạng {f.get('uas_id') or '—'}, "
                     f"khối lượng cất cánh tối đa {_vn(f['mtow_kg'], 1) if f.get('mtow_kg') is not None else '—'} kg; {'ngoài tầm nhìn' if f.get('bvlos') else 'trong tầm nhìn'}; "
                     f"{fs:%H:%M}–{fe:%H:%M}.")
    lines += [
        "",
        f"Đơn vị lập: {c['authority']}.",
        "Ghi chú: \"Phê duyệt\" trên B1 là phê duyệt lịch bay nội bộ, không phải phép bay. "
        + ("Hình học khối là dự thảo theo KML hành lang, chưa phải vùng trời đã phê duyệt." if c["block"]["data_status"] != "approved" else ""),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _ref(r: str) -> str:
    return {"AGL": "so với mặt đất", "AMSL": "so với mực nước biển", "WGS84": "so với elipxoit WGS84"}.get(r, r)


def as_geojson(c: dict) -> dict:
    v = c["vertical"]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": c["package_id"],
            "geometry": {"type": "Polygon", "coordinates": [c["polygon"]]},
            "properties": {
                "kind": "block_activation", "block_id": c["block"]["id"], "block_name": c["block"]["name"],
                "floor": {"value": v["floor_m"], "unit": "m", "reference": v["floor_ref"]},
                "ceiling": {"value": v["ceiling_m"], "unit": "m", "reference": v["ceiling_ref"]},
                "envelope": v["envelope"], "start": c["window"]["start"], "end": c["window"]["end"],
                "flights": c["flights"], "authority": c["authority"], "sha256": sha256(c),
                "data_status": c["block"]["data_status"],
            },
        }],
    }


def as_ed269(c: dict) -> dict:
    v = c["vertical"]
    st = dt.datetime.fromisoformat(c["window"]["start"])
    en = dt.datetime.fromisoformat(c["window"]["end"])
    n = len(c["flights"])
    return {
        "title": f"Kích hoạt {c['block']['name']} hành lang B1 Tây Ninh",
        "description": f"Gói {c['package_id']}, SHA-256 {sha256(c)}",
        "features": [{
            # Mã vùng ngắn (≤7 ký tự theo mô hình UASZone): "B1" + số khối + 4 ký tự đầu của mã băm; mã đầy đủ ở extendedProperties.
            "identifier": f"B1{c['block']['id'].split('-')[-1]}{sha256(c)[:4].upper()}",
            "country": COUNTRY,
            "name": f"Hành lang B1 Tây Ninh – {c['block']['name']}",
            "type": "COMMON",
            "restriction": "REQ_AUTHORISATION",
            "restrictionConditions": [f"Chỉ các chuyến đã được B1 xếp lịch ({n} chuyến)"],
            "region": 0,
            "reason": ["OTHER"],
            "otherReasonInfo": "Hành lang bay thử nghiệm UAS",
            "regulationExemption": "NO",
            "uSpaceClass": "EUROCONTROL",
            "message": "Vùng được kích hoạt cho các chuyến bay thử đã xếp lịch; liên hệ đơn vị quản lý hành lang trước khi vào vùng.",
            "zoneAuthority": [{"name": c["authority"], "service": "Quản lý hành lang bay", "purpose": "AUTHORIZATION",
                               "intervalBefore": "P3D"}],
            "applicability": [{"permanent": "NO", "startDateTime": c["window"]["start"], "endDateTime": c["window"]["end"]}],
            "geometry": [{
                "uomDimensions": "M",
                "lowerLimit": int(round(v["floor_m"])), "lowerVerticalReference": v["floor_ref"],
                "upperLimit": int(round(v["ceiling_m"])), "upperVerticalReference": v["ceiling_ref"],
                "horizontalProjection": {"type": "Polygon", "coordinates": [c["polygon"]]},
            }],
            "extendedProperties": {"b1": {"package_id": c["package_id"], "block_id": c["block"]["id"],
                                          "duration_min": int((en - st).total_seconds() // 60), "flights": c["flights"]}},
        }],
    }


FORMATS = {"text": as_text, "geojson": as_geojson, "ed269": as_ed269}
