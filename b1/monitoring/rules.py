"""Quy tắc giám sát tuân thủ trên một mẫu telemetry (L5, F-06). Hàm thuần, không đọc CSDL: dễ kiểm và dễ thay.

Ngưỡng mặc định (D6 demo, chờ phương án an toàn được duyệt):
- lệch ngang khỏi tim tuyến > 500 m: ngoài vùng bình thường (cảnh báo); > 750 m: vào vùng dự phòng (nguy cấp, không tuân thủ);
- ra ngoài ranh khối đang kích hoạt của chuyến: vượt ranh (nguy cấp); khối chưa kích hoạt cũng tính là vượt ranh;
- độ cao trên trần của kế hoạch kích hoạt hoặc trần bao của hành lang: vượt trần (nguy cấp);
- đường truyền điều khiển suy giảm (cảnh báo), mất (nguy cấp); pin < 25% (cảnh báo), < 15% (nguy cấp);
- mỗi cờ an toàn đang bật là một cảnh báo riêng (CT-5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from b1 import geo

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# Loại cảnh báo do quy tắc trên mẫu telemetry sinh ra: hết điều kiện ở mẫu sau thì tự giải toả.
TELEMETRY_KINDS = ("LATERAL_DEVIATION", "BOUNDARY", "CEILING", "C2_LINK", "LOW_BATTERY", "SAFETY_FLAG", "TELEMETRY_STALE")
FLAG_SEVERITY = {"c2_lost": "critical", "geofence_breach": "critical", "emergency": "critical", "failsafe_land": "critical",
                 "failsafe_rtl": "warning", "low_battery": "warning", "gnss_degraded": "warning"}


@dataclass
class Thresholds:
    normal_half_width_m: float = 500.0
    contingency_half_width_m: float = 750.0
    battery_warn_pct: float = 25.0
    battery_crit_pct: float = 15.0


@dataclass
class Condition:
    kind: str
    severity: str
    message: str
    subject: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class FlightContext:
    """Những gì quy tắc cần biết về chuyến: tim tuyến, ranh khối đang kích hoạt, trần."""
    centerline: geo.Polyline
    keep_in: list[dict]            # [{"block_id", "geometry", "ceiling": {"value","reference"}}]
    envelope: list[dict]           # [{"id", "km_start", "km_end", "value", "reference", "inclusive"}]
    hard_geofences: list[dict] = field(default_factory=list)  # [{"id", "geometry"}]


def _over(alt: float | None, limit: float, inclusive: bool) -> bool:
    return alt is not None and (alt > limit if inclusive else alt >= limit)


def _spatial(row: dict, ctx: FlightContext, th: Thresholds, p, lateral: float, km: float, m: dict) -> list[Condition]:
    out: list[Condition] = []
    if lateral > th.contingency_half_width_m:
        out.append(Condition("LATERAL_DEVIATION", "critical",
                             f"Vào vùng dự phòng: lệch {lateral:.0f} m khỏi tim tuyến (ngưỡng {th.contingency_half_width_m:.0f} m)",
                             data={**m, "zone": "contingency", "conformance": "nonconforming"}))
    elif lateral > th.normal_half_width_m:
        out.append(Condition("LATERAL_DEVIATION", "warning",
                             f"Ngoài vùng bình thường: lệch {lateral:.0f} m khỏi tim tuyến (ngưỡng {th.normal_half_width_m:.0f} m)",
                             data={**m, "zone": "outside_normal"}))

    inside = [k for k in ctx.keep_in if geo.in_polygon(p, k["geometry"])]
    if not ctx.keep_in:
        out.append(Condition("BOUNDARY", "critical", "Khối của chuyến chưa kích hoạt hoặc đã đóng",
                             data={**m, "conformance": "nonconforming"}))
    elif not inside:
        near = [g["id"] for g in ctx.hard_geofences if g.get("geometry") and
                geo.Polyline(g["geometry"]["coordinates"]).project(p)[0] < 1000]
        out.append(Condition("BOUNDARY", "critical", "Ra ngoài ranh khối đang kích hoạt" +
                             (" (rào địa lý cứng: " + ", ".join(near) + ")" if near else ""),
                             data={**m, "hard_geofence": near, "conformance": "nonconforming"}))

    for k in inside or ctx.keep_in[:1]:
        c = k.get("ceiling") or {}
        alt = row.get("alt_agl") if c.get("reference") == "AGL" else row.get("alt_amsl")
        if c and _over(alt, c["value"], True):
            out.append(Condition("CEILING", "critical", f"Vượt trần khối {k['block_id']}: {alt:.0f} m > {c['value']:.0f} m {c['reference']}",
                                 data={**m, "limit": c, "source": k["block_id"]}))
            break
    else:
        for e in ctx.envelope:
            if e["km_start"] <= km < e["km_end"] or (km >= e["km_end"] == ctx.centerline.length_m / 1000.0):
                alt = row.get("alt_agl") if e["reference"] == "AGL" else row.get("alt_amsl")
                if _over(alt, e["value"], e.get("inclusive", True)):
                    out.append(Condition("CEILING", "critical", f"Vượt trần bao {e['id']}: {alt:.0f} m, trần {e['value']:.0f} m {e['reference']}",
                                         data={**m, "limit": e, "source": e["id"]}))
                    break
    return out


def evaluate(row: dict, ctx: FlightContext, th: Thresholds, spatial: bool = True) -> tuple[list[Condition], dict]:
    """Trả (điều kiện đang vi phạm, số đo) cho một mẫu đã chuẩn hoá. `spatial=False` (phiên kiểm tra CT tại khu thử):
    bỏ quy tắc lệch tuyến, vượt ranh, vượt trần, chỉ giữ đường truyền, năng lượng, cờ an toàn."""
    p = (row["lon"], row["lat"])
    lateral, along = ctx.centerline.project(p)
    km = along / 1000.0
    m = {"lateral_m": round(lateral, 1), "km": round(km, 3), "alt_agl": row.get("alt_agl"), "alt_amsl": row.get("alt_amsl")}
    out: list[Condition] = []

    if spatial:
        out += _spatial(row, ctx, th, p, lateral, km, m)

    c2 = row["c2_state"]
    if c2 == "lost":
        out.append(Condition("C2_LINK", "critical", "Mất đường truyền điều khiển (phía phương tiện báo)", data={"c2": c2}))
    elif c2 == "degraded":
        out.append(Condition("C2_LINK", "warning", "Đường truyền điều khiển suy giảm", data={"c2": c2, "rssi_dbm": row.get("c2_rssi_dbm")}))

    level = row.get("battery_pct") if row.get("battery_pct") is not None else row.get("fuel_pct")
    if level is not None and level < th.battery_crit_pct:
        out.append(Condition("LOW_BATTERY", "critical", f"Năng lượng còn {level:.0f}% (< {th.battery_crit_pct:.0f}%)", data={"level_pct": level}))
    elif level is not None and level < th.battery_warn_pct:
        out.append(Condition("LOW_BATTERY", "warning", f"Năng lượng còn {level:.0f}% (< {th.battery_warn_pct:.0f}%)", data={"level_pct": level}))

    for f in row.get("safety_flags") or []:
        out.append(Condition("SAFETY_FLAG", FLAG_SEVERITY.get(f, "warning"), f"Phương tiện bật cờ an toàn {f}", subject=f, data={"flag": f}))
    return out, m
