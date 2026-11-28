"""Thu dữ liệu trạm RIS (I-06), gộp vết ADS-B, vết Broadcast Remote ID, đối chiếu Remote ID với telemetry (L1–L3, F-05).

- Gộp vết ADS-B (Đặc tả Mục 4.5): khoá theo ICAO 24 bit, cửa sổ 200 ms; nhiều trạm thu cùng một bản tin thì giữ bản có tín
  hiệu tốt nhất và lưu danh sách trạm đã thu. Quá 15 giây không có bản tin: vết "suy giảm", vòng bất định = tốc độ × tuổi;
  quá 30 giây: bỏ khỏi bản đồ (bản ghi gốc vẫn trong CSDL).
- Đối chiếu Remote ID: bản tin Broadcast Remote ID của UAS ID đang bay so với mẫu telemetry gần nhất cùng thời điểm; lệch
  quá 100 m là "không khớp" và cảnh báo (phát hiện Remote ID giả). Tỷ lệ khớp từng chuyến ghi vào báo cáo (AT-05).
- UAS ID lạ trong hành lang: Remote ID không thuộc chuyến nào đang bay mà vị trí nằm trong hành lang thì cảnh báo.
- Giao thông có người lái: vết ADS-B gần một chuyến đang bay (ngang < 2 km, đứng < 300 m) thì cảnh báo.
- Trạm: mất bản tin trạng thái quá 5 giây là mất trạm (L2); chạy 4G/5G là suy giảm L1; chạy bộ lưu điện là L4.
"""
from __future__ import annotations

import datetime as dt
import functools
import pathlib
import threading
import time
import uuid
from dataclasses import dataclass, field

import jsonschema
import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from b1 import geo
from b1.monitoring.rules import Condition
from b1.surveillance.models import RidReconciliation, StationRecord, StationState

SPEC = pathlib.Path(__file__).resolve().parents[2] / "api" / "i06-ris-station.openapi.yaml"
STATIONS = ("RIS-1", "RIS-2", "RIS-3", "RIS-4", "RIS-5")


@functools.cache
def _validators() -> dict[str, jsonschema.Draft202012Validator]:
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    out = {}
    for stream, name in (("adsb", "AdsbRecord"), ("rid", "BroadcastRidRecord"), ("weather", "WeatherRecord"),
                         ("spectrum", "SpectrumRecord"), ("status", "StationStatusRecord")):
        out[stream] = jsonschema.Draft202012Validator(dict(schemas[name], components={"schemas": schemas}))
    return out


def record_errors(rec: object) -> list[str]:
    if not isinstance(rec, dict) or rec.get("stream") not in _validators():
        return ["stream: phải là adsb | rid | weather | spectrum | status"]
    return [f"{'/'.join(map(str, e.absolute_path)) or '(gốc)'}: {e.message}"[:300]
            for e in _validators()[rec["stream"]].iter_errors(rec)]


def _ts(s: str | None) -> dt.datetime | None:
    return None if s is None else dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(t: dt.datetime | None) -> str | None:
    return None if t is None else t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Track:
    key: str
    kind: str                      # adsb | rid
    lat: float
    lon: float
    alt: float | None
    alt_ref: str | None
    ground_speed: float | None
    track: float | None
    t_source: float               # giây epoch
    signal_dbm: float
    stations: set[str] = field(default_factory=set)
    callsign: str | None = None
    updated_mono: float = 0.0
    reports: int = 0
    flight_id: str | None = None

    def out(self, now_mono: float, stale_s: float) -> dict:
        age = now_mono - self.updated_mono
        st = "degraded" if age > stale_s else "ok"
        return {"key": self.key, "kind": self.kind, "callsign": self.callsign, "lat": self.lat, "lon": self.lon,
                "alt": self.alt, "alt_ref": self.alt_ref, "ground_speed": self.ground_speed, "track": self.track,
                "t_source": _iso(dt.datetime.fromtimestamp(self.t_source, dt.timezone.utc)), "signal_dbm": self.signal_dbm,
                "stations": sorted(self.stations), "age_s": round(age, 1), "status": st, "reports": self.reports,
                "uncertainty_m": round((self.ground_speed or 0.0) * age) if st == "degraded" else 0, "flight_id": self.flight_id}


class SurveillanceService:
    def __init__(self, sm: sessionmaker[Session], settings, monitor, airspace):
        self.sm, self.s, self.monitor, self.airspace = sm, settings, monitor, airspace
        self.lock = threading.RLock()
        self.adsb: dict[str, Track] = {}
        self.rid: dict[str, Track] = {}
        self.station_seen: dict[str, float] = {}      # trạm → thời điểm (monotonic) nhận bản tin trạng thái gần nhất
        self.station_status: dict[str, dict] = {}
        self._rid_match_streak: dict[uuid.UUID, int] = {}
        self._corridor: list[dict] | None = None
        self._flights_cache: tuple[float, dict] | None = None
        self._pending: list[tuple] = []  # bản tin Remote ID chờ telemetry cùng thời điểm tới

    # ---------- nhận ----------

    def ingest(self, station_id: str, records: list, backfill: bool = False, t_receive: dt.datetime | None = None) -> dict:
        t_receive = t_receive or dt.datetime.now(dt.timezone.utc)
        ok, rejected = [], []
        for i, r in enumerate(records):
            errs = record_errors(r)
            if errs:
                rejected.append({"index": i, "reason": "; ".join(errs)[:500]})
            else:
                ok.append(r)
        rows = [{"station_id": station_id, "stream": r["stream"], "key": r.get("icao24") or r.get("uasId"),
                 "t_source": _ts(r["time"].get("tSource")), "t_station": _ts(r["time"]["tStation"]), "t_receive": t_receive,
                 "backfill": backfill, "data": r} for r in ok]
        with self.sm() as s:
            if rows:
                s.execute(insert(StationRecord).values(rows))
            st = s.get(StationState, station_id) or StationState(station_id=station_id)
            for r in ok:
                if r["stream"] == "status" and (st.status_t_station is None or _ts(r["time"]["tStation"]) >= st.status_t_station):
                    st.status, st.status_t_station = r, _ts(r["time"]["tStation"])
                if r["stream"] == "weather" and (st.weather_t_station is None or _ts(r["time"]["tStation"]) >= st.weather_t_station):
                    st.weather, st.weather_t_station = r, _ts(r["time"]["tStation"])
            st.last_receive = t_receive
            s.add(st)
            s.commit()
        alerts: list[dict] = []
        if not backfill:
            for r in ok:
                alerts += self._live(station_id, r, t_receive)
        return {"accepted": len(ok), "rejected": rejected, "serverReceivedAt": _iso(t_receive), "alerts": alerts}

    def _live(self, sid: str, r: dict, t_receive: dt.datetime) -> list[dict]:
        stream = r["stream"]
        if stream == "status":
            with self.lock:
                self.station_seen[sid] = time.monotonic()
                self.station_status[sid] = r
            return []
        if stream == "adsb":
            return self._adsb(sid, r, t_receive)
        if stream == "rid" and "position" in r:
            return self._rid(sid, r, t_receive)
        return []

    def _fuse(self, table: dict[str, Track], kind: str, key: str, sid: str, r: dict) -> Track | None:
        """Gộp một bản tin vào vết. Trả vết nếu vị trí của vết đổi (bản tin mới hoặc tín hiệu tốt hơn trong cửa sổ)."""
        t = (_ts(r["time"].get("tSource")) or _ts(r["time"]["tStation"])).timestamp()
        win = self.s.adsb_fusion_window_ms / 1000.0
        with self.lock:
            tr = table.get(key)
            now = time.monotonic()
            pos = r["position"]
            alt = r.get("altitude") or {}
            if tr is not None and abs(t - tr.t_source) <= win:
                tr.stations.add(sid)
                tr.updated_mono = now
                if r["signalDbm"] <= tr.signal_dbm:
                    return None
            elif tr is not None and t < tr.t_source - win:
                return None  # bản tin cũ đến muộn: đã có bản mới hơn
            if tr is None:
                tr = table[key] = Track(key, kind, pos["lat"], pos["lon"], alt.get("value"), alt.get("reference"),
                                        r.get("groundSpeed"), r.get("track"), t, r["signalDbm"], {sid})
            elif abs(t - tr.t_source) > win:
                tr.stations = {sid}
                tr.reports += 1
            tr.lat, tr.lon, tr.alt, tr.alt_ref = pos["lat"], pos["lon"], alt.get("value"), alt.get("reference")
            tr.ground_speed, tr.track, tr.t_source, tr.signal_dbm = r.get("groundSpeed"), r.get("track"), t, r["signalDbm"]
            tr.callsign = r.get("callsign") or tr.callsign
            tr.updated_mono = now
            return tr

    def _adsb(self, sid: str, r: dict, t_receive: dt.datetime) -> list[dict]:
        tr = self._fuse(self.adsb, "adsb", r["icao24"], sid, r)
        if tr is None:
            return []
        out = []
        for st in self.monitor.live_flights():
            if st.get("state") != "airborne":
                continue
            fid = uuid.UUID(st["flight_id"])
            h = geo.distance_m((tr.lon, tr.lat), (st["lon"], st["lat"]))
            v = abs((tr.alt or 0.0) - (st.get("alt_amsl") or 0.0))
            close = h < self.s.traffic_horizontal_m and v < self.s.traffic_vertical_m
            if close:
                out += self.monitor.raise_condition(Condition(
                    "TRAFFIC", "critical", f"Tàu bay có người lái {tr.callsign or tr.key} cách {h:.0f} m ngang, {v:.0f} m đứng",
                    subject=tr.key, data={"icao24": tr.key, "callsign": tr.callsign, "horizontal_m": round(h), "vertical_m": round(v)}),
                    fid, _ts(r["time"].get("tSource")), t_receive)
            elif self.monitor.is_open(fid, "TRAFFIC", tr.key) and (h > 1.2 * self.s.traffic_horizontal_m or v > 1.2 * self.s.traffic_vertical_m):
                out += self.monitor.resolve_condition(fid, "TRAFFIC", tr.key)
        return out

    def corridor(self) -> list[dict]:
        if self._corridor is None:
            with self.airspace.sm() as s:
                self._corridor = [b.geometry for b in self.airspace.blocks(s)]
        return self._corridor

    def _known_flight(self, uas: str) -> uuid.UUID | None:
        """Chuyến đã được cho phép cất cánh hoặc đang bay của UAS ID, kể cả khi chưa có mẫu telemetry nào."""
        fid = self.monitor.flight_of_uas(uas)
        if fid is not None:
            return fid
        now = time.monotonic()
        c = self._flights_cache
        if c is None or c[0] < now or (uas not in c[1] and c[0] - 2.0 + 0.5 < now):  # UAS ID mới: đọc lại, tối đa 2 lần/giây
            rows = self.monitor.operations.in_states(("cleared", "airborne"))
            self._flights_cache = c = (now + 2.0, {f["uas_id"]: f["id"] for f in rows if f["uas_id"]})
        return c[1].get(uas)

    def _rid(self, sid: str, r: dict, t_receive: dt.datetime) -> list[dict]:
        uas = r["uasId"]
        tr = self._fuse(self.rid, "rid", uas, sid, r)
        fid = self._known_flight(uas)
        t_src = _ts(r["time"].get("tSource")) or _ts(r["time"]["tStation"])
        out: list[dict] = self._drain_pending()
        if fid is None:
            p = (r["position"]["lon"], r["position"]["lat"])
            if any(geo.in_polygon(p, g) for g in self.corridor()):
                out += self.monitor.raise_condition(Condition(
                    "RID_UNKNOWN", "warning", f"Remote ID {uas} trong hành lang không thuộc chuyến nào đang bay",
                    subject=uas, data={"uas_id": uas, "station": sid, "lat": p[1], "lon": p[0]}), None, t_src, t_receive)
            return out
        if tr is not None:
            tr.flight_id = str(fid)
        return out + self._reconcile(fid, uas, sid, r, t_src, t_receive, time.monotonic() + 2.0)

    def _drain_pending(self, force: bool = False) -> list[dict]:
        out: list[dict] = []
        with self.lock:
            pending, self._pending = self._pending, []
        for item in pending:
            out += self._reconcile(*item[:6], deadline=item[6], expired=force or time.monotonic() > item[6])
        return out

    def _reconcile(self, fid, uas, sid, r, t_src, t_receive, deadline, expired: bool = False) -> list[dict]:
        tel = self.monitor.telemetry_at(fid, t_src.timestamp(), self.s.rid_match_max_dt_s)
        if tel == "pending":
            if not expired:
                with self.lock:
                    self._pending.append((fid, uas, sid, r, t_src, t_receive, deadline))
                return []
            tel = None
        out: list[dict] = []
        dist = None if tel is None else geo.distance_m((tel[1], tel[2]), (r["position"]["lon"], r["position"]["lat"]))
        matched = dist is not None and dist <= self.s.rid_mismatch_m
        self._count(fid, uas, sid, dist, matched)
        if dist is not None and not matched:
            self._rid_match_streak[fid] = 0
            out += self.monitor.raise_condition(Condition(
                "RID_MISMATCH", "warning", f"Remote ID tại {sid} lệch {dist:.0f} m so với telemetry (ngưỡng {self.s.rid_mismatch_m:.0f} m)",
                subject=uas, data={"uas_id": uas, "station": sid, "distance_m": round(dist, 1)}), fid, t_src, t_receive)
        elif matched:
            self._rid_match_streak[fid] = self._rid_match_streak.get(fid, 0) + 1
            if self._rid_match_streak[fid] >= 3 and self.monitor.is_open(fid, "RID_MISMATCH", uas):
                out += self.monitor.resolve_condition(fid, "RID_MISMATCH", uas)
        return out

    def _count(self, fid: uuid.UUID, uas: str, sid: str, dist: float | None, matched: bool) -> None:
        with self.sm() as s:
            x = s.get(RidReconciliation, fid, with_for_update=True) or RidReconciliation(
                flight_id=fid, uas_id=uas, total=0, matched=0, mismatched=0, no_telemetry=0, max_distance_m=0.0, by_station={})
            x.total += 1
            if dist is None:
                x.no_telemetry += 1
            elif matched:
                x.matched += 1
            else:
                x.mismatched += 1
            if dist is not None:
                x.max_distance_m = max(x.max_distance_m, round(dist, 1))
            bs = dict(x.by_station)
            bs[sid] = bs.get(sid, 0) + 1
            x.by_station = bs
            x.updated_at = dt.datetime.now(dt.timezone.utc)
            s.add(x)
            s.commit()

    # ---------- kiểm định kỳ (gọi từ MonitorService.tick) ----------

    def health_tick(self, monitor) -> list[dict]:
        out = self._drain_pending()
        now = time.monotonic()
        with self.lock:
            for table in (self.adsb, self.rid):
                for k, tr in list(table.items()):
                    if now - tr.updated_mono > self.s.track_drop_s:
                        del table[k]
                        if tr.kind == "adsb":
                            for st in monitor.live_flights():
                                fid = uuid.UUID(st["flight_id"])
                                if monitor.is_open(fid, "TRAFFIC", k):
                                    out += monitor.resolve_condition(fid, "TRAFFIC", k)
                        elif monitor.is_open(None, "RID_UNKNOWN", k):
                            out += monitor.resolve_condition(None, "RID_UNKNOWN", k)
            for sid, seen in self.station_seen.items():
                gap = now - seen
                st = self.station_status.get(sid) or {}
                if gap > self.s.station_timeout_s:
                    out += monitor.raise_condition(Condition("STATION", "critical", f"Trạm {sid} mất tín hiệu {gap:.0f} giây (L2)",
                                                             subject=sid, data={"station": sid, "level": "L2", "gap_s": round(gap)}), None)
                elif st.get("backhaul") == "cellular":
                    out += monitor.raise_condition(Condition("STATION", "warning", f"Trạm {sid} chạy 4G/5G (L1)", subject=sid,
                                                             data={"station": sid, "level": "L1"}), None)
                elif st.get("power") == "ups":
                    out += monitor.raise_condition(Condition("STATION", "warning", f"Trạm {sid} chạy bộ lưu điện (L4)", subject=sid,
                                                             data={"station": sid, "level": "L4",
                                                                   "ups_minutes_left": st.get("upsMinutesLeft")}), None)
                elif monitor.is_open(None, "STATION", sid):
                    out += monitor.resolve_condition(None, "STATION", sid)
        return out

    # ---------- đọc ----------

    def tracks(self) -> dict:
        now = time.monotonic()
        with self.lock:
            return {"adsb": [t.out(now, self.s.track_stale_s) for t in self.adsb.values()],
                    "rid": [t.out(now, self.s.track_stale_s) for t in self.rid.values()]}

    def stations(self) -> list[dict]:
        now = time.monotonic()
        with self.sm() as s:
            rows = {x.station_id: x for x in s.scalars(select(StationState))}
        out = []
        for sid in STATIONS:
            x = rows.get(sid)
            st = (x.status if x else None) or {}
            seen = self.station_seen.get(sid)
            if seen is None:
                level = "unknown"
            elif now - seen > self.s.station_timeout_s:
                level = "L2"
            elif st.get("power") == "ups":
                level = "L4"
            elif st.get("backhaul") == "cellular":
                level = "L1"
            else:
                level = "L0"
            lon, lat = self.monitor.stations.get(sid, (None, None))
            out.append({"station_id": sid, "lon": lon, "lat": lat, "level": level, "status": st or None,
                        "status_t_station": _iso(x.status_t_station) if x else None,
                        "last_seen_s": None if seen is None else round(now - seen, 1),
                        "weather": x.weather if x else None, "weather_t_station": _iso(x.weather_t_station) if x else None})
        return out

    def reconciliation(self, flight_id: uuid.UUID) -> dict | None:
        with self.sm() as s:
            x = s.get(RidReconciliation, flight_id)
            if x is None:
                return None
            compared = x.matched + x.mismatched
            return {"flight_id": str(x.flight_id), "uas_id": x.uas_id, "total": x.total, "matched": x.matched,
                    "mismatched": x.mismatched, "no_telemetry": x.no_telemetry,
                    "match_ratio": round(x.matched / compared, 4) if compared else None,
                    "max_distance_m": x.max_distance_m, "by_station": x.by_station, "threshold_m": self.s.rid_mismatch_m}
