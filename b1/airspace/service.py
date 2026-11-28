"""Dịch vụ vùng trời (F-03): nạp khối và ràng buộc, kế hoạch kích hoạt, hiệp đồng, kích hoạt, đóng, rào địa lý.

Mỗi lần đổi trạng thái ghi một sự kiện vào airspace.event trong cùng giao dịch, rồi ghi nhật ký truy vết.
Bản đồ đọc sự kiện theo số thứ tự (luồng SSE), nên trạng thái lên bản đồ trong vài trăm mili giây (F-03: ≤5 giây).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from b1.airspace import package as pkg
from b1.airspace.models import Activation, AirspaceEvent, Block, Constraint
from b1.audit.service import Actor, AuditService

LIVE = ("planned", "coordinated", "active")


class AirspaceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass
class Params:
    activate_lead_min: int = 60   # được kích hoạt sớm nhất trước giờ bắt đầu bao nhiêu phút
    clearance_lead_min: int = 30  # được cho phép cất cánh sớm nhất trước giờ bắt đầu chuyến bao nhiêu phút


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def activation_out(a: Activation) -> dict:
    return {"id": str(a.id), "block_id": a.block_id, "starts_at": a.starts_at, "ends_at": a.ends_at, "state": a.state,
            "floor_m": a.floor_m, "floor_ref": a.floor_ref, "ceiling_m": a.ceiling_m, "ceiling_ref": a.ceiling_ref,
            "flights": a.flights, "package_sha256": a.package_sha256, "coordination_ref": a.coordination_ref,
            "coordination_counterpart": a.coordination_counterpart, "coordinated_at": a.coordinated_at,
            "activated_at": a.activated_at, "closed_at": a.closed_at, "version": a.version}


class AirspaceService:
    def __init__(self, sm: sessionmaker[Session], audit: AuditService, params: Params | None = None):
        self.sm, self.audit, self.params = sm, audit, params or Params()

    # ---------- dữ liệu hành lang ----------

    def load_config(self, corridor_dir: str | pathlib.Path) -> dict:
        """Nạp khối, ràng buộc, lớp tham chiếu từ GeoJSON. Giữ tầng độ cao đã chỉnh trong CSDL."""
        d = pathlib.Path(corridor_dir)
        if not d.is_absolute() and not d.exists():
            d = pathlib.Path(__file__).resolve().parents[2] / d
        if not (d / "blocks.geojson").exists():
            return {"blocks": 0, "constraints": 0}
        blocks = json.loads((d / "blocks.geojson").read_text(encoding="utf-8"))["features"]
        cons = []
        for name in ("constraints.geojson", "reference.geojson"):
            if (d / name).exists():
                cons += json.loads((d / name).read_text(encoding="utf-8"))["features"]
        with self.sm() as s:
            for f in blocks:
                p = f["properties"]
                b = s.get(Block, f["id"]) or Block(id=f["id"])
                b.name, b.km_start, b.km_end, b.geometry = p["name"], p["km_start"], p["km_end"], f["geometry"]
                b.source, b.data_status = p.get("source"), p.get("status", "draft")
                s.add(b)
            for f in cons:
                p = dict(f["properties"])
                c = s.get(Constraint, f["id"]) or Constraint(id=f["id"])
                c.kind, c.name, c.geometry, c.properties = p.pop("kind"), p.pop("name"), f.get("geometry"), p
                s.add(c)
            s.commit()
        return {"blocks": len(blocks), "constraints": len(cons)}

    def blocks(self, s: Session) -> list[Block]:
        return list(s.scalars(select(Block).order_by(Block.id)))

    def constraints(self, s: Session) -> list[Constraint]:
        return list(s.scalars(select(Constraint).order_by(Constraint.kind, Constraint.id)))

    def envelope(self, s: Session, block: Block) -> list[dict]:
        """Trần bao của hành lang chạm khối (theo đoạn Km trên tim tuyến)."""
        out = []
        for c in s.scalars(select(Constraint).where(Constraint.kind == "airspace_constraint")):
            p = c.properties
            if "ceiling" in p and p.get("km_start", 0) < block.km_end and p.get("km_end", 0) > block.km_start:
                out.append({"constraint_id": c.id, "name": c.name, **p["ceiling"]})
        return sorted(out, key=lambda e: e["constraint_id"])

    def set_block_vertical(self, actor: Actor, block_id: str, floor_m: float, floor_ref: str, ceiling_m: float,
                           ceiling_ref: str) -> Block:
        if ceiling_m <= floor_m and floor_ref == ceiling_ref:
            raise AirspaceError(422, "trần phải cao hơn sàn")
        with self.sm() as s:
            b = s.get(Block, block_id, with_for_update=True)
            if not b:
                raise AirspaceError(404, "không có khối")
            before = {"floor_m": b.floor_m, "floor_ref": b.floor_ref, "ceiling_m": b.ceiling_m, "ceiling_ref": b.ceiling_ref}
            b.floor_m, b.floor_ref, b.ceiling_m, b.ceiling_ref = floor_m, floor_ref, ceiling_m, ceiling_ref
            after = {"floor_m": floor_m, "floor_ref": floor_ref, "ceiling_m": ceiling_m, "ceiling_ref": ceiling_ref}
            self._emit(s, "block.vertical_changed", block_id, after)
            s.commit()
        self.audit.record(actor, "airspace.block.vertical", target_type="block", detail={"block_id": block_id},
                          before=before, after=after)
        return b

    # ---------- kế hoạch kích hoạt ----------

    def create_activation(self, actor: Actor, block_id: str, starts_at: dt.datetime, ends_at: dt.datetime,
                          flights: list[dict]) -> Activation:
        if ends_at <= starts_at:
            raise AirspaceError(422, "giờ kết thúc phải sau giờ bắt đầu")
        with self.sm() as s:
            b = s.get(Block, block_id)
            if not b:
                raise AirspaceError(404, f"không có khối {block_id}")
            clash = s.scalars(select(Activation).where(Activation.block_id == block_id, Activation.state.in_(LIVE),
                                                       Activation.starts_at < ends_at, Activation.ends_at > starts_at)).first()
            if clash:
                raise AirspaceError(409, f"khối {block_id} đã có kế hoạch kích hoạt trùng giờ ({clash.id})")
            a = Activation(block_id=block_id, starts_at=starts_at, ends_at=ends_at, floor_m=b.floor_m, floor_ref=b.floor_ref,
                           ceiling_m=b.ceiling_m, ceiling_ref=b.ceiling_ref, flights=flights,
                           created_by=actor.id)
            s.add(a)
            s.flush()
            self._emit(s, "activation.planned", block_id, activation_out(a))
            s.commit()
        self.audit.record(actor, "airspace.activation.plan", target_type="activation", target_id=a.id, after=activation_out(a))
        return a

    def plan_from_flights(self, actor: Actor, flights: list[dict]) -> tuple[list[Activation], list[dict]]:
        """Gom các chuyến đã xếp lịch theo (khối, khung giờ) và lập kế hoạch cho nhóm chưa có kế hoạch."""
        groups: dict[tuple, list[dict]] = {}
        for f in flights:
            for b in f["block_ids"]:
                groups.setdefault((b, f["starts_at"], f["ends_at"]), []).append(f)
        created, skipped = [], []
        for (b, st, en), fl in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            try:
                created.append(self.create_activation(actor, b, dt.datetime.fromisoformat(st), dt.datetime.fromisoformat(en), fl))
            except AirspaceError as e:
                skipped.append({"block_id": b, "starts_at": st, "reason": e.detail})
        return created, skipped

    def get(self, s: Session, activation_id: uuid.UUID) -> Activation:
        a = s.get(Activation, activation_id)
        if not a:
            raise AirspaceError(404, "không có kế hoạch kích hoạt")
        return a

    def list_activations(self, s: Session, start: dt.datetime | None = None, end: dt.datetime | None = None,
                         block_id: str | None = None, state: str | None = None) -> list[Activation]:
        q = select(Activation).order_by(Activation.starts_at, Activation.block_id)
        if start:
            q = q.where(Activation.ends_at > start)
        if end:
            q = q.where(Activation.starts_at < end)
        if block_id:
            q = q.where(Activation.block_id == block_id)
        if state:
            q = q.where(Activation.state == state)
        return list(s.scalars(q))

    def package(self, activation_id: uuid.UUID) -> dict:
        with self.sm() as s:
            a = self.get(s, activation_id)
            b = s.get(Block, a.block_id)
            return pkg.build(a, b, self.envelope(s, b))

    # ---------- hiệp đồng, kích hoạt, đóng ----------

    def coordinate(self, actor: Actor, activation_id: uuid.UUID, *, result: str, package_sha256: str, reference: str,
                   counterpart: str, note: str | None = None, now: dt.datetime | None = None) -> Activation:
        """Ghi kết quả hiệp đồng với cơ quan điều hành bay (I-05, hiện làm thủ công bằng công văn)."""
        now = now or _now()
        content = self.package(activation_id)
        current = pkg.sha256(content)
        with self.sm() as s:
            a = s.get(Activation, activation_id, with_for_update=True)
            if a.state != "planned":
                raise AirspaceError(409, f"kế hoạch đang ở trạng thái {a.state}")
            if package_sha256 != current:
                raise AirspaceError(409, "mã băm không khớp gói hiện tại: gói đã đổi sau khi gửi, cần gửi lại bản mới")
            before = activation_out(a)
            a.state = "coordinated" if result == "approved" else "rejected"
            a.package_sha256, a.coordination_ref, a.coordination_counterpart = current, reference, counterpart
            a.coordination_note, a.coordinated_at, a.coordinated_by = note, now, actor.id
            a.version += 1
            self._emit(s, f"activation.{a.state}", a.block_id, activation_out(a))
            s.commit()
        self.audit.record(actor, "airspace.activation.coordinate", target_type="activation", target_id=a.id,
                          before=before, after=activation_out(a))
        return a

    def activate(self, actor: Actor, activation_id: uuid.UUID, now: dt.datetime | None = None) -> Activation:
        now = now or _now()
        with self.sm() as s:
            a = s.get(Activation, activation_id, with_for_update=True)
            if not a:
                raise AirspaceError(404, "không có kế hoạch kích hoạt")
            reason = None
            if a.state != "coordinated":
                reason = f"chỉ kích hoạt sau hiệp đồng; kế hoạch đang ở trạng thái {a.state}"
            elif now < a.starts_at - dt.timedelta(minutes=self.params.activate_lead_min):
                reason = f"chưa đến giờ: kích hoạt sớm nhất {self.params.activate_lead_min} phút trước giờ bắt đầu"
            elif now >= a.ends_at:
                reason = "đã quá khung giờ đã hiệp đồng"
            if reason:
                s.rollback()
                self.audit.record(actor, "airspace.activation.activate", "rejected", target_type="activation",
                                  target_id=activation_id, detail={"reason": reason})
                raise AirspaceError(409, reason)
            before = activation_out(a)
            a.state, a.activated_at, a.activated_by = "active", now, actor.id
            a.version += 1
            self._emit(s, "activation.active", a.block_id, activation_out(a))
            s.commit()
        self.audit.record(actor, "airspace.activation.activate", target_type="activation", target_id=a.id,
                          before=before, after=activation_out(a))
        return a

    def close(self, actor: Actor, activation_id: uuid.UUID, flights_in_air: Callable[[str, dt.datetime, dt.datetime], list],
              now: dt.datetime | None = None, cancel: bool = False) -> Activation:
        """Đóng khối (sau khi hạ cánh, F-03 bước 10) hoặc huỷ kế hoạch chưa kích hoạt."""
        now = now or _now()
        with self.sm() as s:
            a = s.get(Activation, activation_id, with_for_update=True)
            if not a:
                raise AirspaceError(404, "không có kế hoạch kích hoạt")
            if a.state not in LIVE or (cancel and a.state == "active") or (not cancel and a.state != "active"):
                raise AirspaceError(409, f"kế hoạch đang ở trạng thái {a.state}")
            busy = flights_in_air(a.block_id, a.starts_at, a.ends_at)
            if busy:
                s.rollback()
                reason = f"còn chuyến đã cho phép cất cánh hoặc đang bay trong khối: {', '.join(map(str, busy))}"
                self.audit.record(actor, "airspace.activation.close", "rejected", target_type="activation",
                                  target_id=activation_id, detail={"reason": reason})
                raise AirspaceError(409, reason)
            before = activation_out(a)
            a.state = "cancelled" if cancel else "closed"
            a.closed_at, a.closed_by = now, actor.id
            a.version += 1
            self._emit(s, f"activation.{a.state}", a.block_id, activation_out(a))
            s.commit()
        self.audit.record(actor, "airspace.activation.close", target_type="activation", target_id=a.id, before=before,
                          after=activation_out(a))
        return a

    # ---------- truy vấn cho dịch vụ khác ----------

    def uncovered_blocks(self, block_ids: list[str], start: dt.datetime, end: dt.datetime) -> list[str]:
        """Khối nào chưa được kích hoạt phủ trọn [start, end) (điều kiện cho phép cất cánh)."""
        with self.sm() as s:
            missing = []
            for b in block_ids:
                ok = s.scalars(select(Activation.id).where(Activation.block_id == b, Activation.state == "active",
                                                           Activation.starts_at <= start, Activation.ends_at >= end)).first()
                if not ok:
                    missing.append(b)
            return missing

    def state(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or _now()
        with self.sm() as s:
            out = []
            for b in self.blocks(s):
                acts = self.list_activations(s, start=now - dt.timedelta(hours=1), end=now + dt.timedelta(days=1), block_id=b.id)
                active = next((a for a in acts if a.state == "active"), None)
                upcoming = [activation_out(a) for a in acts if a.state in ("planned", "coordinated")]
                out.append({"id": b.id, "name": b.name, "status": "active" if active else "inactive",
                            "active_activation": activation_out(active) if active else None, "upcoming": upcoming,
                            "floor_m": b.floor_m, "floor_ref": b.floor_ref, "ceiling_m": b.ceiling_m, "ceiling_ref": b.ceiling_ref,
                            "km_start": b.km_start, "km_end": b.km_end, "geometry": b.geometry, "data_status": b.data_status})
            return out

    def geofence(self, now: dt.datetime | None = None) -> dict:
        """Rào địa lý theo ranh khối đang kích hoạt, cộng rào cứng. Bắt buộc, không có cách tắt (Đặc tả Mục 4.4)."""
        now = now or _now()
        with self.sm() as s:
            feats = []
            for a in s.scalars(select(Activation).where(Activation.state == "active", Activation.starts_at <= now + dt.timedelta(
                    minutes=self.params.activate_lead_min), Activation.ends_at > now)):
                b = s.get(Block, a.block_id)
                feats.append({"type": "Feature", "id": f"keep-in:{a.id}", "geometry": b.geometry, "properties": {
                    "kind": "keep_in", "block_id": b.id, "activation_id": str(a.id), "start": a.starts_at.isoformat(),
                    "end": a.ends_at.isoformat(), "floor": {"value": a.floor_m, "reference": a.floor_ref},
                    "ceiling": {"value": a.ceiling_m, "reference": a.ceiling_ref}}})
            for c in s.scalars(select(Constraint).where(Constraint.kind == "hard_geofence")):
                feats.append({"type": "Feature", "id": c.id, "geometry": c.geometry, "properties": {
                    "kind": "hard_geofence", "name": c.name, **c.properties}})
            return {"type": "FeatureCollection", "generated_at": now.isoformat(), "can_disable": False, "features": feats}

    def events_after(self, seq: int, limit: int = 200) -> list[dict]:
        with self.sm() as s:
            rows = s.scalars(select(AirspaceEvent).where(AirspaceEvent.seq > seq).order_by(AirspaceEvent.seq).limit(limit))
            return [{"seq": e.seq, "at": e.at.isoformat(), "kind": e.kind, "block_id": e.block_id, "payload": e.payload}
                    for e in rows]

    def last_seq(self) -> int:
        with self.sm() as s:
            return s.scalar(select(AirspaceEvent.seq).order_by(AirspaceEvent.seq.desc()).limit(1)) or 0

    @staticmethod
    def _emit(s: Session, kind: str, block_id: str | None, payload: dict) -> None:
        s.add(AirspaceEvent(kind=kind, block_id=block_id, payload=json.loads(json.dumps(payload, default=str))))

    def envelope_all(self) -> list[dict]:
        """Trần bao của hành lang theo đoạn Km (cho giám sát tuân thủ)."""
        with self.sm() as s:
            out = []
            for c in s.scalars(select(Constraint).where(Constraint.kind == "airspace_constraint")):
                p = c.properties
                if "ceiling" in p:
                    out.append({"id": c.id, "km_start": p.get("km_start", 0.0), "km_end": p.get("km_end", 0.0),
                                "value": p["ceiling"]["value"], "reference": p["ceiling"]["reference"],
                                "inclusive": p["ceiling"].get("inclusive", True)})
            return sorted(out, key=lambda e: e["km_start"])
