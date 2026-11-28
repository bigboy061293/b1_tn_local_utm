"""Phiên kiểm tra tuân thủ dữ liệu CT-1…8 (Đặc tả Mục 5.3) chạy trên chính cổng I-07.

Khách mở phiên, gửi telemetry tới mã phiên như gửi cho một chuyến (HTTPS, MQTT hoặc MAVLink), đánh dấu các đoạn thử
(đứng yên tại điểm RTK, chạy tải, ngắt kết nối) rồi yêu cầu chấm. B1 chấm từ dữ liệu đã lưu:

| Bài | Cách chấm |
|---|---|
| CT-1 | Phát lại ≥ 30 phút: không mẫu nào bị loại vì sai lược đồ (100% trường bắt buộc, đúng kiểu, đơn vị) |
| CT-2 | ≥ 5 Hz trên ≥ 30 phút; ≥ 99% khoảng cách giữa hai mẫu ≤ 300 ms; không khoảng nào > 2 giây (trừ đoạn ngắt có chủ đích) |
| CT-3 | Nhãn thời gian nguồn tăng liên tục; trung vị (giờ B1 nhận − giờ nguồn) ≤ 200 ms, đồng hồ B1 theo NTP/PTP của hành lang |
| CT-4 | Đứng yên tại điểm RTK ≥ 10 phút: ≥ 95% mẫu trong bán kính 5 m |
| CT-5 | Cờ c2_lost và geofence_breach đều xuất hiện, mỗi lần bật cờ thành cảnh báo trên B1 ≤ 2 giây sau giờ nguồn |
| CT-6 | TLS 1.3 và chứng chỉ hai chiều hợp lệ, đọc từ tiêu đề cổng vào; không có tiêu đề thì chờ Kỹ thuật viên xác nhận |
| CT-7 | Trong đoạn chạy tải đã đánh dấu: vẫn ≥ 5 Hz, ≥ 99% khoảng ≤ 300 ms, không khoảng nào > 2 giây |
| CT-8 | Ngắt ~60 giây: kết nối lại ≤ 10 giây sau khi hết ngắt; gửi bù lấp đoạn ngắt với nhãn thời gian gốc (khoảng lớn nhất ≤ 1 giây) |

Phiên khách tự mở cho kết quả tham khảo. Phiên do Kỹ thuật viên bay thử mở (`official`) ghi kết quả vào hồ sơ phương tiện.
"""
from __future__ import annotations

import datetime as dt
import statistics
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from b1 import geo
from b1.telemetry.models import CtSession, RejectedSample, Sample

ALL_TESTS = tuple(f"CT-{i}" for i in range(1, 9))
MARKS = ("static_start", "static_end", "load_start", "load_end", "disconnect_start", "disconnect_end")


class CtError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _gaps(ts: list[float]) -> list[float]:
    return [b - a for a, b in zip(ts, ts[1:])]


def _cadence(ts: list[float], exclude: tuple[float, float] | None = None) -> dict:
    """Tần suất và khoảng cách giữa các mẫu; `exclude` bỏ các khoảng nằm trong đoạn ngắt có chủ đích."""
    if len(ts) < 2:
        return {"samples": len(ts), "duration_s": 0.0, "rate_hz": 0.0, "pct_gaps_le_300ms": 0.0, "max_gap_s": None}
    gaps = []
    for a, b in zip(ts, ts[1:]):
        if exclude and a < exclude[1] and b > exclude[0]:
            continue
        gaps.append(b - a)
    dur = ts[-1] - ts[0]
    if exclude:
        dur -= max(0.0, min(exclude[1], ts[-1]) - max(exclude[0], ts[0]))
    n = len(gaps)
    return {"samples": len(ts), "duration_s": round(dur, 1), "rate_hz": round(n / dur, 2) if dur > 0 else 0.0,
            "pct_gaps_le_300ms": round(100.0 * sum(1 for g in gaps if g <= 0.3005) / n, 3) if n else 0.0,
            "max_gap_s": round(max(gaps), 3) if gaps else None}


def _cadence_ok(c: dict, min_duration_s: float) -> bool:
    return (c["duration_s"] >= min_duration_s and c["rate_hz"] >= 5.0 and c["pct_gaps_le_300ms"] >= 99.0
            and c["max_gap_s"] is not None and c["max_gap_s"] <= 2.0)


def evaluate(samples: list[dict], rejected: int, alerts: list[dict], marks: dict[str, float], params: dict,
             tests: list[str]) -> dict:
    """Chấm các bài từ dữ liệu đã lưu. `samples`: t_source, t_receive (giây epoch), lat, lon, backfill, safety_flags."""
    min_dur = float(params.get("min_duration_s", 1800))
    live = [x for x in samples if not x["backfill"]]
    all_ts = sorted(x["t_source"] for x in samples)
    out: dict[str, dict] = {}
    disc = None
    if "disconnect_start" in marks:
        disc = (marks["disconnect_start"], marks.get("disconnect_end", marks["disconnect_start"] + params.get("disconnect_s", 60)))

    def put(test, passed, measured, note=None):
        if test in tests:
            out[test] = {"test": test, "passed": passed, "measured": measured, **({"note": note} if note else {})}

    span = (all_ts[-1] - all_ts[0]) if len(all_ts) > 1 else 0.0
    put("CT-1", rejected == 0 and span >= min_dur and len(samples) > 0,
        {"accepted": len(samples), "rejected": rejected, "duration_s": round(span, 1), "min_duration_s": min_dur})

    c2 = _cadence(all_ts, disc)
    put("CT-2", _cadence_ok(c2, min_dur), {**c2, "min_duration_s": min_dur, "excluded_disconnect": bool(disc)})

    order = [x["t_source"] for x in sorted(live, key=lambda x: x["id"])]
    monotonic = all(b > a for a, b in zip(order, order[1:]))
    lag = [x["t_receive"] - x["t_source"] for x in live]
    med = statistics.median(lag) if lag else None
    put("CT-3", bool(lag) and monotonic and abs(med) <= 0.2,
        {"monotonic": monotonic, "median_offset_ms": None if med is None else round(med * 1000, 1),
         "p95_offset_ms": None if not lag else round(sorted(lag)[int(0.95 * (len(lag) - 1))] * 1000, 1)})

    ref = params.get("rtk_ref")
    if "CT-4" in tests:
        if not ref:
            put("CT-4", None, {}, "chưa khai điểm RTK (rtk_ref) cho phiên")
        else:
            a, b = marks.get("static_start", all_ts[0] if all_ts else 0), marks.get("static_end", all_ts[-1] if all_ts else 0)
            win = [x for x in samples if a <= x["t_source"] <= b]
            d = [geo.distance_m((x["lon"], x["lat"]), (ref["lon"], ref["lat"])) for x in win]
            within = sum(1 for v in d if v <= 5.0)
            pct = 100.0 * within / len(d) if d else 0.0
            put("CT-4", bool(d) and (b - a) >= 600 and pct >= 95.0,
                {"window_s": round(b - a, 1), "samples": len(d), "pct_within_5m": round(pct, 2),
                 "p95_distance_m": round(sorted(d)[int(0.95 * (len(d) - 1))], 2) if d else None})

    if "CT-5" in tests:
        onsets = []
        prev: set[str] = set()
        for x in sorted(live, key=lambda x: x["t_source"]):
            cur = set(x["safety_flags"] or [])
            for f in cur - prev:
                onsets.append((f, x["t_source"]))
            prev = cur
        lat = []
        for f, t in onsets:
            hits = [a["raised"] - t for a in alerts if a["kind"] == "SAFETY_FLAG" and a["subject"] == f and a["raised"] >= t - 0.001]
            lat.append({"flag": f, "latency_s": round(min(hits), 3) if hits else None})
        flags = {x["flag"] for x in lat}
        ok = {"c2_lost", "geofence_breach"} <= flags and all(x["latency_s"] is not None and x["latency_s"] <= 2.0 for x in lat)
        put("CT-5", ok if onsets else None, {"onsets": lat}, None if onsets else "chưa có lần bật cờ nào trong phiên")

    tls = params.get("transport") or {}
    if "CT-6" in tests:
        if tls.get("tls_version"):
            put("CT-6", tls.get("tls_version") == "TLSv1.3" and str(tls.get("client_cert_verified")).upper() == "SUCCESS", tls)
        else:
            put("CT-6", None, tls, "kết nối không qua cổng vào TLS của hành lang: Kỹ thuật viên xác nhận bằng biên bản")

    if "CT-7" in tests:
        if "load_start" in marks and "load_end" in marks:
            ts = [t for t in all_ts if marks["load_start"] <= t <= marks["load_end"]]
            c = _cadence(ts)
            put("CT-7", _cadence_ok(c, min(60.0, marks["load_end"] - marks["load_start"])), c)
        else:
            put("CT-7", None, {}, "chưa đánh dấu đoạn chạy tải (load_start, load_end)")

    if "CT-8" in tests:
        if disc is None:
            put("CT-8", None, {}, "chưa đánh dấu đoạn ngắt kết nối (disconnect_start, disconnect_end)")
        else:
            after = sorted((x for x in live if x["t_receive"] >= disc[1]), key=lambda x: x["t_receive"])
            reconnect = after[0]["t_receive"] - disc[1] if after else None
            inside = [t for t in all_ts if disc[0] - 1 <= t <= disc[1] + 1]
            backfilled = sum(1 for x in samples if x["backfill"] and disc[0] <= x["t_source"] <= disc[1])
            mg = max(_gaps(inside)) if len(inside) > 1 else None
            put("CT-8", reconnect is not None and reconnect <= 10.0 and backfilled > 0 and mg is not None and mg <= 1.0,
                {"disconnect_s": round(disc[1] - disc[0], 1), "reconnect_s": None if reconnect is None else round(reconnect, 2),
                 "backfilled": backfilled, "max_gap_after_backfill_s": None if mg is None else round(mg, 3)})
    return out


class CtService:
    def __init__(self, sm: sessionmaker[Session], registry, monitor, audit):
        self.sm, self.registry, self.monitor, self.audit = sm, registry, monitor, audit
        self._transport: dict[uuid.UUID, dict] = {}

    def start(self, actor, aircraft: dict, *, official: bool, tests: list[str], params: dict) -> CtSession:
        with self.sm() as s:
            c = CtSession(customer_id=aircraft["customer_id"], aircraft_id=aircraft["id"], uas_id=aircraft["uas_id"],
                          official=official, tests=tests, params={**params, "config_ref": aircraft["config_ref"], "marks": {}},
                          started_by=actor.id)
            s.add(c)
            s.commit()
        self.audit.record(actor, "ct.session.start", target_type="ct_session", target_id=c.id,
                          after={"aircraft_id": str(c.aircraft_id), "official": official, "tests": tests})
        return c

    def get(self, session_id: uuid.UUID) -> CtSession | None:
        with self.sm() as s:
            return s.get(CtSession, session_id)

    def note_transport(self, session_id: uuid.UUID, info: dict) -> None:
        if info.get("tls_version"):
            self._transport[session_id] = info

    def mark(self, actor, session_id: uuid.UUID, kind: str, at: dt.datetime | None) -> CtSession:
        with self.sm() as s:
            c = s.get(CtSession, session_id, with_for_update=True)
            if c is None:
                raise CtError(404, "không có phiên")
            if c.state != "open":
                raise CtError(409, "phiên đã chấm")
            at = at or dt.datetime.now(dt.timezone.utc)
            c.params = {**c.params, "marks": {**c.params.get("marks", {}), kind: at.timestamp()}}
            s.commit()
            return c

    def evaluate(self, actor, session_id: uuid.UUID, record: bool) -> CtSession:
        with self.sm() as s:
            c = s.get(CtSession, session_id, with_for_update=True)
            if c is None:
                raise CtError(404, "không có phiên")
            if c.state != "open":
                raise CtError(409, "phiên đã chấm")
            rows = s.scalars(select(Sample).where(Sample.flight_id == c.id).order_by(Sample.t_source)).all()
            rej = len(s.scalars(select(RejectedSample.id).where(RejectedSample.flight_id == c.id)).all())
            samples = [{"id": x.id, "t_source": x.t_source.timestamp(), "t_receive": x.t_receive.timestamp(), "lat": x.lat,
                        "lon": x.lon, "backfill": x.backfill, "safety_flags": x.safety_flags} for x in rows]
            alerts = [{"kind": a["kind"], "subject": a["subject"],
                       "raised": dt.datetime.fromisoformat(a["raised_at"].replace("Z", "+00:00")).timestamp()}
                      for a in self.monitor.alerts(flight_id=c.id, kind="SAFETY_FLAG", limit=5000)]
            params = {**c.params, "transport": self._transport.get(c.id) or c.params.get("transport")}
            res = evaluate(samples, rej, alerts, c.params.get("marks", {}), params, list(c.tests))
            c.results, c.state, c.evaluated_at, c.params = res, "evaluated", dt.datetime.now(dt.timezone.utc), params
            s.commit()
        if record:
            for r in res.values():
                if r["passed"] is not None:
                    self.registry.record_conformance(c.aircraft_id, r["test"], r["passed"], c.evaluated_at, r["measured"],
                                                     f"ct_session:{c.id}", actor.id)
        self.audit.record(actor, "ct.session.evaluate", target_type="ct_session", target_id=c.id,
                          after={t: r["passed"] for t, r in res.items()}, detail={"recorded": record})
        return c


def session_out(c: CtSession) -> dict:
    return {"id": str(c.id), "flight_id": str(c.id), "customer_id": str(c.customer_id), "aircraft_id": str(c.aircraft_id),
            "uas_id": c.uas_id, "official": c.official, "tests": c.tests, "state": c.state, "params": c.params,
            "results": c.results, "started_at": c.started_at, "evaluated_at": c.evaluated_at,
            "telemetry_url": f"/telemetry/v1/flights/{c.id}/samples"}
