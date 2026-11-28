"""Nhận telemetry I-07 từ mọi kênh (HTTPS, MAVLink v2/UDP, MQTT 5) về một đường: kiểm lược đồ, khử trùng, ghi, giám sát.

Bộ chuyển đổi kênh chỉ dịch về `TelemetrySample`; mọi kiểm tra nằm ở đây, nên ba kênh cùng một quy tắc (Đặc tả Mục 5.2).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from b1.telemetry import schema
from b1.telemetry.models import CtSession, RejectedSample, Sample

LIVE_STATES = ("cleared", "airborne")
BACKFILL_STATES = ("cleared", "airborne", "landed", "closed")


class IngestError(Exception):
    def __init__(self, status: int, detail: str, retry_after: float | None = None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


@dataclass
class Target:
    """Đích của telemetry: một chuyến đã cho phép cất cánh, hoặc một phiên kiểm tra CT."""
    id: uuid.UUID
    uas_id: str
    customer_id: uuid.UUID | None
    state: str
    block_ids: list[str] = field(default_factory=list)
    request_ref: str | None = None
    ct_session: bool = False


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso_ms(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TelemetryService:
    def __init__(self, sm: sessionmaker[Session], settings, operations, monitor=None):
        self.sm, self.s, self.operations, self.monitor = sm, settings, operations, monitor
        self.lock = threading.Lock()
        self._disarmed_since: dict[uuid.UUID, dt.datetime] = {}
        self._last_live: dict[uuid.UUID, dt.datetime] = {}
        self._bucket: dict[uuid.UUID, tuple[float, float]] = {}  # chuyến → (số mẫu còn được gửi bù, thời điểm đơn điệu)

    # ---------- đích ----------

    def target(self, flight_id: uuid.UUID) -> Target | None:
        f = self.operations.flight(flight_id)
        if f is not None:
            return Target(f["id"], f["uas_id"] or "", f["customer_id"], f["state"], f["block_ids"], f["request_ref"])
        with self.sm() as s:
            c = s.get(CtSession, flight_id)
            if c is not None:
                return Target(c.id, c.uas_id, c.customer_id, "airborne" if c.state == "open" else "closed",
                              c.params.get("block_ids", []), f"CT-{str(c.id)[:8]}", ct_session=True)
        return None

    def check_target(self, t: Target | None, customer_id: uuid.UUID | None, backfill: bool) -> Target:
        """Chỉ khách sở hữu chuyến được gửi; chuyến phải đã được cho phép cất cánh (403), chưa đóng (409)."""
        if t is None:
            raise IngestError(404, "không có chuyến bay hoặc phiên kiểm tra")
        if customer_id is not None and t.customer_id != customer_id:
            raise IngestError(403, "chuyến bay không thuộc khách hàng này")
        if t.state in ("scheduled", "cancelled"):
            raise IngestError(403, "chuyến bay chưa được cho phép cất cánh")
        if not backfill and t.state not in LIVE_STATES:
            raise IngestError(409, "chuyến bay đã kết thúc; dữ liệu gửi bù dùng /backfill")
        if backfill and t.state not in BACKFILL_STATES:
            raise IngestError(409, "chuyến bay không nhận gửi bù")
        return t

    # ---------- nhận ----------

    def _take_backfill(self, flight_id: uuid.UUID, n: int) -> None:
        """Xô token cho gửi bù của một chuyến; hết hạn mức thì 429 trước khi tốn công kiểm lược đồ và ghi."""
        rate, burst = self.s.backfill_rate_per_s, self.s.backfill_burst
        if rate <= 0:
            return
        now = time.monotonic()
        with self.lock:
            tokens, at = self._bucket.get(flight_id, (burst, now))
            tokens = min(burst, tokens + (now - at) * rate)
            if tokens < n:
                self._bucket[flight_id] = (tokens, now)
                raise IngestError(429, "vượt hạn mức gửi bù của chuyến, gửi lại sau", retry_after=(n - tokens) / rate)
            self._bucket[flight_id] = (tokens - n, now)

    def ingest(self, t: Target, samples: list, channel: str, backfill: bool = False,
               t_receive: dt.datetime | None = None) -> dict:
        t_receive = t_receive or dt.datetime.now(dt.timezone.utc)
        if backfill:
            self._take_backfill(t.id, len(samples))
        rows, rejected = [], []
        for i, x in enumerate(samples):
            errs = schema.errors(x)
            if not errs:
                if x["flightId"] != str(t.id):
                    errs = ["flightId: không trùng chuyến trong đường dẫn hoặc phiên"]
                elif t.uas_id and x["uasId"] != t.uas_id:
                    errs = [f"uasId: chuyến này gán cho {t.uas_id}"]
                else:
                    ts = parse_ts(x["timeSource"])
                    if (ts - t_receive).total_seconds() > self.s.telemetry_max_future_s:
                        errs = ["timeSource: ở tương lai so với đồng hồ B1 (đồng hồ nguồn chưa đồng bộ GNSS?)"]
            if errs:
                rejected.append({"index": i, "reason": "; ".join(errs)[:500]})
                continue
            rows.append(normalize(x, t_receive, channel, backfill, self.s.terrain_elev_m, self.s.geoid_undulation_m))
        rows = list({r["t_source"]: r for r in rows}.values())  # trùng trong cùng lô
        inserted: list[dict] = []
        with self.sm() as s:
            if rows:
                res = s.execute(insert(Sample).values(rows).on_conflict_do_nothing(constraint="sample_dedup")
                                .returning(Sample.t_source, Sample.t_ingest))
                got = {r.t_source: r.t_ingest for r in res}
                for r in rows:
                    if r["t_source"] in got:
                        inserted.append({**r, "t_ingest": got[r["t_source"]]})
            for r in rejected:
                s.add(RejectedSample(flight_id=t.id, channel=channel, t_receive=t_receive, reason=r["reason"],
                                     sample=samples[r["index"]] if isinstance(samples[r["index"]], dict) else None))
            s.commit()
        alerts: list[dict] = []
        if inserted:
            alerts = self._after_insert(t, inserted, backfill)
        return {"accepted": len(inserted), "duplicates": len(rows) - len(inserted), "rejected": rejected,
                "serverReceivedAt": iso_ms(t_receive), "alerts": alerts}

    def _after_insert(self, t: Target, rows: list[dict], backfill: bool) -> list[dict]:
        rows = sorted(rows, key=lambda r: r["t_source"])
        alerts: list[dict] = []
        with self.lock:
            if not t.ct_session:
                self._flight_state(t, rows)
            if backfill or self.monitor is None:
                return alerts
            last = self._last_live.get(t.id)
            live = [r for r in rows if last is None or r["t_source"] > last]
            if not live:
                return alerts
            self._last_live[t.id] = live[-1]["t_source"]
        flight = {"id": t.id, "block_ids": t.block_ids, "request_ref": t.request_ref, "state": "ct" if t.ct_session else t.state, "ct_session": t.ct_session}
        for r in live:
            alerts += self.monitor.on_sample(flight, r)
        return alerts

    def _flight_state(self, t: Target, rows: list[dict]) -> None:
        """cleared → airborne khi armed; airborne → landed khi disarmed liên tục ≥ 10 giây (theo nhãn thời gian nguồn)."""
        for r in rows:
            if r["armed"]:
                self._disarmed_since.pop(t.id, None)
                if t.state == "cleared" and self.operations.transition(t.id, ("cleared",), "airborne"):
                    t.state = "airborne"
                    if self.monitor:
                        self.monitor.set_flight_state(t.id, "airborne")
            elif t.state == "airborne":
                since = self._disarmed_since.setdefault(t.id, r["t_source"])
                if (r["t_source"] - since).total_seconds() >= self.s.landed_after_disarm_s:
                    if self.operations.transition(t.id, ("airborne",), "landed"):
                        t.state = "landed"
                        self._disarmed_since.pop(t.id, None)
                        if self.monitor:
                            self.monitor.set_flight_state(t.id, "landed")

    # ---------- đọc ----------

    def samples(self, flight_id: uuid.UUID, start: dt.datetime | None = None, end: dt.datetime | None = None,
                limit: int = 200_000) -> list[Sample]:
        with self.sm() as s:
            q = select(Sample).where(Sample.flight_id == flight_id).order_by(Sample.t_source).limit(limit)
            if start:
                q = q.where(Sample.t_source >= start)
            if end:
                q = q.where(Sample.t_source <= end)
            return list(s.scalars(q))

    def rejected(self, flight_id: uuid.UUID) -> list[RejectedSample]:
        with self.sm() as s:
            return list(s.scalars(select(RejectedSample).where(RejectedSample.flight_id == flight_id).order_by(RejectedSample.id)))


def normalize(x: dict, t_receive: dt.datetime, channel: str, backfill: bool, terrain_m: float, geoid_m: float) -> dict:
    """Mẫu I-07 → hàng `telemetry.sample`. Suy độ cao so với mặt đất và mực nước biển khi phương tiện không gửi đủ.

    Độ cao địa hình dùng một giá trị cho cả hành lang (đồng bằng Tây Ninh), chờ mô hình số độ cao: xem docs/telemetry-and-monitoring.md.
    """
    alt, ref = float(x["altitude"]["value"]), x["altitude"]["reference"]
    agl = x["altitude"].get("agl")
    if ref == "AGL":
        agl, amsl = alt, alt + terrain_m
    elif ref == "AMSL":
        amsl = alt
    else:  # WGS84_ELLIPSOID
        amsl = alt - geoid_m
    if agl is None:
        agl = amsl - terrain_m
    e, c2 = x["energy"], x["c2Link"]
    return {
        "flight_id": uuid.UUID(x["flightId"]), "uas_id": x["uasId"], "t_source": parse_ts(x["timeSource"]), "t_receive": t_receive,
        "channel": channel, "backfill": backfill, "lat": x["position"]["lat"], "lon": x["position"]["lon"],
        "alt_value": alt, "alt_ref": ref, "alt_agl": agl, "alt_amsl": amsl,
        "ground_speed": x["velocity"]["groundSpeed"], "vertical_speed": x["velocity"]["verticalSpeed"], "track": x["velocity"]["track"],
        "fix_type": x["gnss"]["fixType"], "satellites": x["gnss"]["satellites"], "hdop": x["gnss"]["hdop"],
        "battery_pct": e.get("batteryPercent"), "voltage": e.get("voltage"), "fuel_pct": e.get("fuelPercent"),
        "mode": x["flightMode"]["mode"][:40], "autonomy": x["flightMode"]["autonomy"], "armed": x["armed"],
        "safety_flags": list(x["safetyFlags"]), "c2_state": c2["state"], "c2_rssi_dbm": c2.get("rssiDbm"),
        "c2_quality_pct": c2.get("linkQualityPercent"),
    }
