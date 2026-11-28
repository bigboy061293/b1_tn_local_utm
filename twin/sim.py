"""Bản sao số tối thiểu của hành lang B1 (bước 0).

Mô phỏng một hoặc nhiều phương tiện bay dọc tim tuyến, sinh:
- telemetry theo lược đồ I-07 (`TelemetrySample`), mặc định 10 Hz;
- bản tin Broadcast Remote ID tại các trạm RIS ảo trong bán kính thu (I-06 `BroadcastRidRecord`), 1 Hz;
- trạng thái trạm RIS (I-06 `StationStatusRecord`), 1 Hz.

Kịch bản tiêm lỗi để thử cảnh báo: lệch ngang khỏi tim tuyến, mất đường truyền điều khiển, pin yếu,
và trạm RIS chuyển sang 4G/5G hoặc mất điện (mức suy giảm L1, L2, L4).

Ví dụ:
    python -m twin.sim --duration 120 --start-km 0 --speed 15 \
        --inject offset:30:60:900 --inject c2_lost:80:90 > out.jsonl

Mỗi dòng đầu ra là {"interface": "I-07" | "I-06", "stationId"?: ..., "payload": {...}}.
Thời gian mô phỏng chạy nhanh hơn thực (không ngủ); --realtime để chạy theo đồng hồ thật.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import pathlib
import sys
import time
import uuid

from pyproj import Transformer
from shapely.geometry import LineString, Point, shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORRIDOR = ROOT / "config" / "corridor"
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True).transform
TO_WGS = Transformer.from_crs("EPSG:32648", "EPSG:4326", always_xy=True).transform

# D4: bán kính thu Broadcast Remote ID dùng trong thiết kế và mô phỏng (m).
RID_RANGE_M = 7650.0


def _load(name: str) -> dict:
    return json.loads((CORRIDOR / name).read_text(encoding="utf-8"))


def centerline_utm() -> LineString:
    geom = shape(_load("centerline.geojson")["features"][0]["geometry"])
    return LineString([TO_UTM(x, y) for x, y in geom.coords])


def stations_utm() -> dict[str, Point]:
    return {f["id"]: Point(TO_UTM(*f["geometry"]["coordinates"])) for f in _load("ris_stations.geojson")["features"]}


@dataclasses.dataclass
class Fault:
    kind: str          # offset | climb | c2_lost | low_battery | backhaul_cellular | station_down | ups
    start_s: float
    end_s: float
    value: float = 0.0  # offset: mét (dương = bên phải hướng bay); trạm: số thứ tự 1..5

    @classmethod
    def parse(cls, text: str) -> "Fault":
        parts = text.split(":")
        kind, start, end = parts[0], float(parts[1]), float(parts[2])
        value = float(parts[3]) if len(parts) > 3 else 0.0
        return cls(kind, start, end, value)

    def active(self, t: float) -> bool:
        return self.start_s <= t < self.end_s


def iso_ms(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Aircraft:
    def __init__(self, line: LineString, start_km: float, speed_ms: float, alt_agl_m: float, uas_id: str):
        self.line, self.speed, self.alt = line, speed_ms, alt_agl_m
        self.d0 = start_km * 1000.0
        self.flight_id = str(uuid.uuid4())
        self.uas_id = uas_id
        self.battery0 = 100.0

    def state(self, t: float, faults: list[Fault]) -> dict:
        d = min(self.d0 + self.speed * t, self.line.length)
        p = self.line.interpolate(d)
        q = self.line.interpolate(min(d + 1.0, self.line.length))
        if q.equals(p):
            q, p = p, self.line.interpolate(max(d - 1.0, 0.0))
        ux, uy = q.x - p.x, q.y - p.y
        n = math.hypot(ux, uy) or 1.0
        ux, uy = ux / n, uy / n
        offset = sum(f.value for f in faults if f.kind == "offset" and f.active(t))
        x, y = p.x + uy * offset, p.y - ux * offset  # pháp tuyến bên phải
        if d >= self.line.length:
            x, y = self.line.interpolate(self.line.length).x, self.line.interpolate(self.line.length).y
        track = (math.degrees(math.atan2(ux, uy)) + 360.0) % 360.0
        battery = max(self.battery0 - t * 0.02, 0.0)
        if any(f.kind == "low_battery" and f.active(t) for f in faults):
            battery = min(battery, 15.0)
        c2_lost = any(f.kind == "c2_lost" and f.active(t) for f in faults)
        alt = self.alt + sum(f.value for f in faults if f.kind == "climb" and f.active(t))
        return {"x": x, "y": y, "km": d / 1000.0, "offset": offset, "track": track, "alt": alt,
                "battery": battery, "c2_lost": c2_lost, "speed": 0.0 if d >= self.line.length else self.speed}


class ManLine:
    """Tàu bay có người lái bay thẳng cắt ngang hành lang ở 1.500 m so với mực nước biển, 70 m/s."""

    def __init__(self, line: LineString):
        mid = line.interpolate(line.length * 0.15)
        self.x0, self.y0 = mid.x - 30_000, mid.y - 10_000
        self.vx, self.vy = 70.0 * 0.949, 70.0 * 0.316
        self.track = round((math.degrees(math.atan2(self.vx, self.vy)) + 360) % 360, 1)

    def at(self, t: float) -> tuple[float, float, float]:
        return self.x0 + self.vx * t, self.y0 + self.vy * t, 1500.0


def telemetry(ac: Aircraft, s: dict, now: dt.datetime) -> dict:
    lon, lat = TO_WGS(s["x"], s["y"])
    flags = []
    if s["c2_lost"]:
        flags.append("c2_lost")
    if s["battery"] <= 20.0:
        flags.append("low_battery")
    return {
        "flightId": ac.flight_id,
        "uasId": ac.uas_id,
        "timeSource": iso_ms(now),
        "position": {"lat": round(lat, 7), "lon": round(lon, 7)},
        "altitude": {"value": s.get("alt", ac.alt), "reference": "AGL"},
        "velocity": {"groundSpeed": s["speed"], "verticalSpeed": 0.0, "track": round(s["track"], 1)},
        "gnss": {"fixType": "3d", "satellites": 16, "hdop": 0.8},
        "energy": {"batteryPercent": round(s["battery"], 1), "voltage": round(42.0 + s["battery"] * 0.084, 2)},
        "flightMode": {"mode": "AUTO", "autonomy": "automatic"},
        "armed": True,
        "safetyFlags": flags,
        "c2Link": {"state": "lost" if s["c2_lost"] else "ok"},
    }


def station_mode(idx: int, t: float, faults: list[Fault]) -> tuple[str, str]:
    """Trả (backhaul, power) của trạm thứ idx (1..5) tại thời điểm t."""
    backhaul, power = "fiber", "grid"
    for f in faults:
        if not f.active(t) or int(f.value) != idx:
            continue
        if f.kind == "backhaul_cellular":
            backhaul = "cellular"
        elif f.kind == "station_down":
            backhaul, power = "down", "down"
        elif f.kind == "ups":
            power = "ups"
    return backhaul, power


def run(args: argparse.Namespace, out=sys.stdout) -> int:
    line = centerline_utm()
    stations = stations_utm()
    faults = [Fault.parse(x) for x in args.inject]
    fleet = [Aircraft(line, args.start_km + i * args.spacing_km, args.speed, args.alt, f"SIM{i + 1:04d}B1TN") for i in range(args.count)]
    for ac, fid, uas in zip(fleet, args.flight_id, args.uas_id):  # gán mã chuyến, UAS ID do B1 cấp
        ac.flight_id, ac.uas_id = fid, uas
    if args.uas_id:
        for ac, uas in zip(fleet, args.uas_id):
            ac.uas_id = uas
    manned = ManLine(line) if args.adsb else None
    t0 = dt.datetime.fromisoformat(args.start).astimezone(dt.timezone.utc) if args.start else dt.datetime.now(dt.timezone.utc)
    dt_s = 1.0 / args.rate
    steps = int(args.duration * args.rate)
    wall0 = time.monotonic()
    emitted = 0
    for k in range(steps + 1):
        t = k * dt_s
        now = t0 + dt.timedelta(seconds=t)
        whole_second = k % int(args.rate) == 0
        for ac in fleet:
            s = ac.state(t, faults)
            out.write(json.dumps({"interface": "I-07", "payload": telemetry(ac, s, now)}, ensure_ascii=False) + "\n")
            emitted += 1
            if whole_second:
                pos = Point(s["x"], s["y"])
                lon, lat = TO_WGS(s["x"], s["y"])
                for idx, (sid, sp) in enumerate(stations.items(), start=1):
                    backhaul, _ = station_mode(idx, t, faults)
                    dist = pos.distance(sp)
                    if backhaul == "down" or dist > RID_RANGE_M:
                        continue
                    # Trễ trạm: cáp quang ≤100 ms, 4G/5G ≤300 ms (ngân sách D5); mô phỏng một nửa ngân sách.
                    lag = 0.05 if backhaul == "fiber" else 0.15
                    rec = {"stream": "rid",
                           "time": {"tSource": iso_ms(now), "tStation": iso_ms(now + dt.timedelta(seconds=lag))},
                           "transport": "bt5_long_range", "uasId": ac.uas_id, "uasIdType": "serial",
                           "position": {"lat": round(lat, 7), "lon": round(lon, 7)},
                           "altitude": {"value": ac.alt, "reference": "AGL"},
                           "signalDbm": round(-40.0 - 20.0 * math.log10(max(dist, 1.0) / 10.0), 1)}
                    out.write(json.dumps({"interface": "I-06", "stationId": sid, "payload": rec}, ensure_ascii=False) + "\n")
                    emitted += 1
        if whole_second and manned:
            mx, my, malt = manned.at(t)
            mlon, mlat = TO_WGS(mx, my)
            for idx, (sid, sp) in enumerate(stations.items(), start=1):
                backhaul, _ = station_mode(idx, t, faults)
                dist = Point(mx, my).distance(sp)
                if backhaul == "down" or dist > 120_000:
                    continue
                lag = 0.05 if backhaul == "fiber" else 0.15
                rec = {"stream": "adsb", "time": {"tSource": iso_ms(now), "tStation": iso_ms(now + dt.timedelta(seconds=lag))},
                       "icao24": "888123", "callsign": "VNA123", "position": {"lat": round(mlat, 7), "lon": round(mlon, 7)},
                       "altitude": {"value": malt, "reference": "AMSL"}, "groundSpeed": 70.0, "track": manned.track,
                       "signalDbm": round(-60.0 - 20.0 * math.log10(max(dist, 100.0) / 1000.0), 1)}
                out.write(json.dumps({"interface": "I-06", "stationId": sid, "payload": rec}, ensure_ascii=False) + "\n")
                emitted += 1
        if whole_second and k % int(args.rate * 10) == 0:
            for idx, sid in enumerate(stations, start=1):
                if station_mode(idx, t, faults)[0] == "down":
                    continue
                rec = {"stream": "weather", "time": {"tStation": iso_ms(now)}, "kind": "measured", "heightAglM": 10,
                       "windSpeedMs": round(4.0 + idx * 0.3, 1), "windGustMs": round(6.5 + idx * 0.4, 1), "windDirDeg": 200,
                       "rainMmH": 0.0, "temperatureC": 31.0, "visibilityM": 10000, "cloudBaseM": 900}
                out.write(json.dumps({"interface": "I-06", "stationId": sid, "payload": rec}, ensure_ascii=False) + "\n")
                emitted += 1
        if whole_second:
            for idx, sid in enumerate(stations, start=1):
                backhaul, power = station_mode(idx, t, faults)
                if backhaul == "down":
                    continue  # trạm mất điện và đường truyền: không có bản tin
                rec = {"stream": "status", "time": {"tStation": iso_ms(now)}, "backhaul": backhaul, "power": power,
                       "ptpLocked": True, "ptpOffsetNs": 120, "storageFreeGb": 820,
                       "sensors": {"adsb": "ok", "rid": "ok", "weather": "ok",
                                   "camera": "down" if power == "ups" else "ok",
                                   "spectrum": "down" if power == "ups" else "ok"}}
                out.write(json.dumps({"interface": "I-06", "stationId": sid, "payload": rec}, ensure_ascii=False) + "\n")
                emitted += 1
        if args.realtime:
            lag = (wall0 + t) - time.monotonic()
            if lag > 0:
                time.sleep(lag)
    out.flush()
    print(f"twin: {len(fleet)} phương tiện, {args.duration:.0f} s, {emitted} bản ghi", file=sys.stderr)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration", type=float, default=60.0, help="giây mô phỏng")
    p.add_argument("--rate", type=float, default=10.0, help="Hz telemetry (≥5)")
    p.add_argument("--count", type=int, default=1, help="số phương tiện (tối đa 2 đồng thời theo Mục 2.3)")
    p.add_argument("--start-km", type=float, default=0.0)
    p.add_argument("--spacing-km", type=float, default=15.0)
    p.add_argument("--speed", type=float, default=15.0, help="m/s")
    p.add_argument("--alt", type=float, default=120.0, help="m AGL")
    p.add_argument("--start", default=None, help="thời điểm bắt đầu RFC 3339; mặc định là bây giờ")
    p.add_argument("--inject", action="append", default=[],
                   help="kind:start_s:end_s[:value], ví dụ offset:30:60:900, c2_lost:80:90, backhaul_cellular:0:60:2")
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--flight-id", action="append", default=[], help="mã chuyến do B1 cấp, theo thứ tự phương tiện")
    p.add_argument("--uas-id", action="append", default=[], help="UAS ID, theo thứ tự phương tiện")
    p.add_argument("--adsb", action="store_true", help="thêm một tàu bay có người lái phát ADS-B cắt ngang hành lang")
    return p


if __name__ == "__main__":
    a = parser().parse_args()
    if a.rate < 5:
        sys.exit("tần suất telemetry phải ≥5 Hz (Mục 5.2)")
    raise SystemExit(run(a))
