"""Giám sát tuân thủ và cảnh báo (L5, F-06).

Mỗi mẫu telemetry thời gian thực đi qua bộ quy tắc ngay trong lượt nhận (không qua hàng đợi), nên độ trễ cảnh báo chỉ gồm
thời gian truyền và một lần ghi CSDL. Cảnh báo khử trùng theo (chuyến, loại, chủ thể): điều kiện còn thì không sinh cảnh báo
mới; nặng lên thì nâng cấp; hết thì tự giải toả. Mỗi thay đổi ghi một sự kiện vào `monitoring.event` trong cùng giao dịch;
luồng SSE và bản đồ đọc bảng đó theo số thứ tự (cùng cách với vùng trời, chạy được nhiều tiến trình).

Trạng thái trực tiếp (vị trí mới nhất, trạm đang phủ) giữ trong bộ nhớ của tiến trình nhận telemetry. Khi chạy nhiều tiến
trình nhận, gán mỗi chuyến vào một tiến trình (theo mã chuyến) hoặc chuyển phần này sang kho dùng chung ở bước 7.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import threading
import time
import uuid
from collections import deque

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from b1 import geo
from b1.audit.service import Actor, AuditService
from b1.monitoring import rules
from b1.monitoring.models import Alert, MonitorEvent


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime | None) -> str | None:
    return None if t is None else t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def alert_out(a: Alert) -> dict:
    return {"id": str(a.id), "flight_id": a.flight_id and str(a.flight_id), "subject": a.subject, "kind": a.kind,
            "severity": a.severity, "state": a.state, "message": a.message, "data": a.data, "t_source": _iso(a.t_source),
            "t_receive": _iso(a.t_receive), "raised_at": _iso(a.raised_at), "updated_at": _iso(a.updated_at), "count": a.count,
            "acked_by": a.acked_by and str(a.acked_by), "acked_at": _iso(a.acked_at), "resolved_at": _iso(a.resolved_at),
            "resolved_by": a.resolved_by}


def corridor_path(corridor_dir: str) -> pathlib.Path:
    d = pathlib.Path(corridor_dir)
    if not d.is_absolute() and not d.exists():
        d = pathlib.Path(__file__).resolve().parents[2] / d
    return d


class MonitorService:
    def __init__(self, sm: sessionmaker[Session], settings, audit: AuditService, airspace, operations):
        self.sm, self.s, self.audit, self.airspace, self.operations = sm, settings, audit, airspace, operations
        self.th = rules.Thresholds(settings.normal_half_width_m, settings.contingency_half_width_m,
                                   settings.battery_warn_pct, settings.battery_crit_pct)
        d = corridor_path(settings.corridor_dir)
        cl = json.loads((d / "centerline.geojson").read_text(encoding="utf-8"))["features"][0]
        self.centerline = geo.Polyline(cl["geometry"]["coordinates"], cl["properties"].get("length_km", 0) * 1000 or None)
        st = json.loads((d / "ris_stations.geojson").read_text(encoding="utf-8"))["features"]
        self.stations = {f["properties"]["name"]: tuple(f["geometry"]["coordinates"][:2]) for f in st}
        self.lock = threading.RLock()
        self.live: dict[uuid.UUID, dict] = {}
        self._open: dict[str, tuple[uuid.UUID, str]] | None = None  # dedup_key -> (alert_id, severity)
        self._geo_cache: tuple[float, dict, list] | None = None
        self.surveillance = None  # gắn sau khi tạo dịch vụ thu dữ liệu trạm
        self.recent_latency: deque[dict] = deque(maxlen=5000)
        self.history: dict[uuid.UUID, deque] = {}  # 20 giây mẫu gần nhất mỗi chuyến, để đối chiếu Remote ID

    # ---------- ngữ cảnh ----------

    def _geofence(self) -> tuple[dict, list]:
        now = time.monotonic()
        if self._geo_cache is None or self._geo_cache[0] < now:
            self._geo_cache = (now + self.s.monitor_context_ttl_s, self.airspace.geofence(), self.airspace.envelope_all())
        return self._geo_cache[1], self._geo_cache[2]

    def invalidate_context(self) -> None:
        self._geo_cache = None

    def context(self, block_ids: list[str]) -> rules.FlightContext:
        g, env = self._geofence()
        keep, hard = [], []
        for f in g["features"]:
            p = f["properties"]
            if p["kind"] == "keep_in" and p["block_id"] in block_ids:
                keep.append({"block_id": p["block_id"], "geometry": f["geometry"], "ceiling": p["ceiling"]})
            elif p["kind"] == "hard_geofence":
                hard.append({"id": f["id"], "geometry": f["geometry"]})
        return rules.FlightContext(self.centerline, keep, env, hard)

    def nearest_station(self, lon: float, lat: float) -> tuple[str, float]:
        sid, sp = min(self.stations.items(), key=lambda kv: geo.distance_m((lon, lat), kv[1]))
        return sid, geo.distance_m((lon, lat), sp)

    # ---------- mẫu telemetry ----------

    def on_sample(self, flight: dict, row: dict) -> list[dict]:
        """Đánh giá một mẫu thời gian thực; trả các sự kiện cảnh báo vừa phát (để đo độ trễ)."""
        fid = flight["id"]
        ctx = self.context(flight.get("block_ids") or [])
        conds, m = rules.evaluate(row, ctx, self.th, spatial=not flight.get("ct_session"))
        station, dist = self.nearest_station(row["lon"], row["lat"])
        nonconf = any(c.data.get("conformance") == "nonconforming" for c in conds)
        emitted: list[dict] = []
        with self.lock:
            prev = self.live.get(fid)
            if prev and prev.get("station") and prev["station"] != station:
                emitted += self._event_only(rules.Condition("HANDOVER", "info", f"Chuyển vùng phủ {prev['station']} → {station}",
                                                            subject=station, data={"from": prev["station"], "to": station,
                                                                                   "km": m["km"]}),
                                            fid, row["t_source"], row["t_receive"])
            self.live[fid] = {
                "flight_id": str(fid), "request_ref": flight.get("request_ref"), "uas_id": row["uas_id"],
                "state": flight.get("state"), "lat": row["lat"], "lon": row["lon"], "alt_agl": row.get("alt_agl"),
                "alt_amsl": row.get("alt_amsl"), "ground_speed": row["ground_speed"], "track": row["track"],
                "battery_pct": row.get("battery_pct"), "c2": row["c2_state"], "flags": row.get("safety_flags") or [],
                "armed": row["armed"], "mode": row["mode"], "t_source": _iso(row["t_source"]), "t_receive": _iso(row["t_receive"]),
                "received_mono": time.monotonic(), "lateral_m": m["lateral_m"], "km": m["km"], "station": station,
                "station_dist_m": round(dist), "conformance": "nonconforming" if nonconf else "conforming",
                "block_ids": flight.get("block_ids") or [],
            }
            self.history.setdefault(fid, deque(maxlen=400)).append(
                (row["t_source"].timestamp(), row["lon"], row["lat"], row.get("alt_amsl")))
            emitted += self._sync(fid, row["uas_id"], conds, row["t_source"], row["t_receive"])
        return emitted

    def flight_of_uas(self, uas_id: str) -> uuid.UUID | None:
        with self.lock:
            for fid, st in self.live.items():
                if st["uas_id"] == uas_id and st.get("state") not in ("landed", "closed", "cancelled"):
                    return fid
        return None

    def telemetry_at(self, flight_id: uuid.UUID, t: float, max_dt: float = 1.0) -> tuple | None:
        """Mẫu telemetry gần thời điểm t nhất (±max_dt giây): (t, lon, lat, alt_amsl)."""
        with self.lock:
            h = self.history.get(flight_id)
            if not h:
                return None
            best = min(h, key=lambda x: abs(x[0] - t))
        return best if abs(best[0] - t) <= max_dt else None

    def set_flight_state(self, flight_id: uuid.UUID, state: str) -> None:
        with self.lock:
            if flight_id in self.live:
                self.live[flight_id]["state"] = state

    @staticmethod
    def key(flight_id: uuid.UUID | None, kind: str, subject: str | None) -> str:
        return f"{flight_id or '-'}|{kind}|{subject or ''}"

    def _load_open(self) -> dict[str, tuple[uuid.UUID, str]]:
        if self._open is None:
            with self.sm() as s:
                self._open = {a.dedup_key: (a.id, a.severity) for a in s.scalars(select(Alert).where(Alert.resolved_at.is_(None)))}
        return self._open

    def _sync(self, fid: uuid.UUID, uas_id: str, conds: list[rules.Condition], t_source, t_receive) -> list[dict]:
        want = {self.key(fid, c.kind, c.subject): c for c in conds}
        opened = self._load_open()
        out = []
        for k, c in want.items():
            out += self._raise(k, c, fid, t_source, t_receive)
        prefix = f"{fid}|"
        for k in [k for k in opened if k.startswith(prefix) and k.split("|")[1] in rules.TELEMETRY_KINDS and k not in want]:
            out += self._resolve(k, "auto")
        return out

    def raise_condition(self, c: rules.Condition, flight_id: uuid.UUID | None, t_source=None, t_receive=None) -> list[dict]:
        """Cho dịch vụ khác (đối chiếu Remote ID, trạm RIS, giao thông có người lái) phát cảnh báo qua cùng một chỗ."""
        with self.lock:
            return self._raise(self.key(flight_id, c.kind, c.subject), c, flight_id, t_source, t_receive)

    def resolve_condition(self, flight_id: uuid.UUID | None, kind: str, subject: str | None) -> list[dict]:
        with self.lock:
            return self._resolve(self.key(flight_id, kind, subject), "auto")

    def is_open(self, flight_id: uuid.UUID | None, kind: str, subject: str | None = None) -> bool:
        return self.key(flight_id, kind, subject) in self._load_open()

    def _raise(self, k: str, c: rules.Condition, fid, t_source, t_receive) -> list[dict]:
        opened = self._load_open()
        now = _now()
        with self.sm() as s:
            if k in opened:
                aid, sev = opened[k]
                if rules.SEVERITY_RANK[c.severity] == rules.SEVERITY_RANK[sev]:
                    return []
                a = s.get(Alert, aid, with_for_update=True)
                if a is None or a.resolved_at is not None:
                    opened.pop(k, None)
                    return self._raise(k, c, fid, t_source, t_receive)
                up = rules.SEVERITY_RANK[c.severity] > rules.SEVERITY_RANK[sev]
                a.severity, a.message, a.data, a.updated_at, a.count = c.severity, c.message, _plain(c.data), now, a.count + 1
                if up:
                    a.t_source, a.t_receive, a.state = t_source, t_receive, "open"
                kind = "alert.escalated" if up else "alert.updated"
            else:
                a = Alert(id=uuid.uuid4(), dedup_key=k, flight_id=fid, subject=c.subject, kind=c.kind, severity=c.severity,
                          message=c.message, data=_plain(c.data), t_source=t_source, t_receive=t_receive, raised_at=now,
                          updated_at=now)
                s.add(a)
                kind = "alert.raised"
            ev = self._emit(s, kind, a)
            try:
                s.commit()
            except IntegrityError:  # tiến trình khác vừa mở cùng cảnh báo
                s.rollback()
                self._open = None
                return []
            opened[k] = (a.id, a.severity)
        self._track_latency(ev)
        return [ev]

    def _event_only(self, c: rules.Condition, fid, t_source, t_receive) -> list[dict]:
        """Sự kiện thông tin (ví dụ chuyển vùng phủ giữa hai trạm): ghi và phát, không chờ xử lý."""
        now = _now()
        with self.sm() as s:
            a = Alert(id=uuid.uuid4(), dedup_key=self.key(fid, c.kind, c.subject), flight_id=fid, subject=c.subject, kind=c.kind,
                      severity=c.severity, state="resolved", message=c.message, data=_plain(c.data), t_source=t_source,
                      t_receive=t_receive, raised_at=now, updated_at=now, resolved_at=now, resolved_by="auto")
            s.add(a)
            ev = self._emit(s, "alert.raised", a)
            s.commit()
        return [ev]

    def _resolve(self, k: str, by: str) -> list[dict]:
        opened = self._load_open()
        if k not in opened:
            return []
        aid, _ = opened.pop(k)
        with self.sm() as s:
            a = s.get(Alert, aid, with_for_update=True)
            if a is None or a.resolved_at is not None:
                return []
            a.state, a.resolved_at, a.resolved_by, a.updated_at = "resolved", _now(), by, _now()
            ev = self._emit(s, "alert.resolved", a)
            s.commit()
        return [ev]

    @staticmethod
    def _emit(s: Session, kind: str, a: Alert) -> dict:
        payload = alert_out(a)
        s.add(MonitorEvent(kind=kind, alert_id=a.id, payload=payload))
        return {"event": kind, **payload}

    def _track_latency(self, ev: dict) -> None:
        if ev["event"] in ("alert.raised", "alert.escalated") and ev.get("t_receive"):
            self.recent_latency.append({"event": ev["event"], "kind": ev["kind"], "alert": ev["id"], "t_source": ev["t_source"],
                                        "t_receive": ev["t_receive"], "raised_at": ev["updated_at"]})

    # ---------- kiểm định kỳ ----------

    def tick(self, now_mono: float | None = None) -> list[dict]:
        """Mất telemetry khi đang bay (> 2 giây không có mẫu), trạm RIS mất tín hiệu. Chạy mỗi 0,5 giây."""
        now_mono = time.monotonic() if now_mono is None else now_mono
        out = []
        with self.lock:
            for fid, st in list(self.live.items()):
                gap = now_mono - st["received_mono"]
                if st.get("state") == "airborne" and gap > self.s.telemetry_stale_s:
                    out += self._raise(self.key(fid, "TELEMETRY_STALE", None),
                                       rules.Condition("TELEMETRY_STALE", "critical", f"Không nhận telemetry {gap:.1f} giây",
                                                       data={"gap_s": round(gap, 1), "last_t_source": st["t_source"]}),
                                       fid, None, _now())
                if st.get("state") in ("landed", "closed", "cancelled") and gap > 60:
                    del self.live[fid]
            if self.surveillance is not None:
                out += self.surveillance.health_tick(self)
        return out

    # ---------- đọc, xác nhận ----------

    def alerts(self, *, open_only: bool = False, flight_id: uuid.UUID | None = None, kind: str | None = None,
               limit: int = 200) -> list[dict]:
        with self.sm() as s:
            q = select(Alert).order_by(Alert.raised_at.desc()).limit(limit)
            if open_only:
                q = q.where(Alert.resolved_at.is_(None))
            if flight_id:
                q = q.where(Alert.flight_id == flight_id)
            if kind:
                q = q.where(Alert.kind == kind)
            return [alert_out(a) for a in s.scalars(q)]

    def acknowledge(self, actor: Actor, alert_id: uuid.UUID, resolve: bool = False, note: str | None = None) -> dict | None:
        with self.lock, self.sm() as s:
            a = s.get(Alert, alert_id, with_for_update=True)
            if a is None:
                return None
            before = {"state": a.state}
            now = _now()
            if a.acked_at is None:
                a.acked_by, a.acked_at = actor.id, now
            a.state, a.updated_at = "acknowledged" if a.resolved_at is None else a.state, now
            kind = "alert.acknowledged"
            if resolve and a.resolved_at is None:
                a.state, a.resolved_at, a.resolved_by, kind = "resolved", now, str(actor.id), "alert.resolved"
                self._load_open().pop(a.dedup_key, None)
            self._emit(s, kind, a)
            s.commit()
            out = alert_out(a)
        self.audit.record(actor, "monitoring.alert.resolve" if resolve else "monitoring.alert.acknowledge", target_type="alert",
                          target_id=alert_id, before=before, after={"state": out["state"]}, detail={"note": note, "kind": out["kind"]})
        return out

    def events_after(self, seq: int, limit: int = 500) -> list[dict]:
        with self.sm() as s:
            rows = s.scalars(select(MonitorEvent).where(MonitorEvent.seq > seq).order_by(MonitorEvent.seq).limit(limit))
            return [{"seq": e.seq, "at": _iso(e.at), "kind": e.kind, "alert": e.payload} for e in rows]

    def last_seq(self) -> int:
        with self.sm() as s:
            return s.scalar(select(MonitorEvent.seq).order_by(MonitorEvent.seq.desc()).limit(1)) or 0

    def live_flights(self) -> list[dict]:
        now = time.monotonic()
        with self.lock:
            return [{**{k: v for k, v in st.items() if k != "received_mono"}, "age_s": round(now - st["received_mono"], 2)}
                    for st in self.live.values()]


def _plain(x):
    return json.loads(json.dumps(x, default=str, ensure_ascii=False))
