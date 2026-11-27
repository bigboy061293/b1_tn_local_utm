"""Xếp lịch bay (F-02; Đặc tả B1 v0.1, Mục 4.3).

- Nhận yêu cầu: hồ sơ đầy đủ thì cấp số HL-YYYYMMDD-NNNN kèm thời điểm đến từng giây; chưa đủ thì cấp số tạm, giữ chỗ
  3 ngày làm việc. Mọi lần chặn ghi lý do (bảng block_reason) và nhật ký truy vết (AT-01).
- Kỳ xếp lịch 16:00: kiểm lại hồ sơ, xếp theo thứ tự nhóm ưu tiên → quyền đặt lại → hạng khách hàng → số thứ tự,
  trong giới hạn khối đang mở, 1 chuyến mỗi khối mỗi khung, 2 chuyến đồng thời toàn hành lang.
- Xác nhận lần cuối T − 3 ngày làm việc: kiểm lại hồ sơ kèm bảo hiểm; thiếu thì huỷ khung và ghi lý do.

Chưa làm trong bản này (ghi trong docs/scheduling.md): hạn mức khung hạng C, trần 40% mỗi khách, trần giữ chỗ định kỳ
nhóm 2, ngưỡng vắng mặt và huỷ muộn, nhắc việc T − 7/T − 3/T − 1.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from b1.audit.service import Actor, AuditService
from b1.notify.service import NotifyService
from b1.operations.service import OperationsService
from b1.registry.models import Aircraft, Customer
from b1.registry.service import RegistryService
from b1.scheduling.calendar import TZ, Params, add_workdays, slot_window, sub_workdays
from b1.scheduling.models import BlockReason, FlightRequest, Param, QueueCounter, RequestItem, SchedulingRun

TIER_RANK = {"A": 0, "B": 1, "C": 2}


class RequestError(ValueError):
    """Yêu cầu sai (khung ngoài cửa sổ đặt chỗ, khối chưa mở...): trả 422, có ghi nhật ký."""


@dataclass
class ItemIn:
    day: dt.date
    slot: int


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def order_key(r: FlightRequest, now: dt.datetime) -> tuple:
    rebook = 0 if r.rebook_right_until and r.rebook_right_until > now else 1
    return (r.priority_group, rebook, TIER_RANK.get(r.customer_tier, 9), r.queue_at, r.queue_no)


class SchedulingService:
    def __init__(self, sm: sessionmaker[Session], registry: RegistryService, operations: OperationsService, audit: AuditService,
                 notify: NotifyService | None = None):
        self.sm, self.registry, self.ops, self.audit, self.notify = sm, registry, operations, audit, notify

    def _tell(self, r: FlightRequest, kind: str, title: str, body: str, dedup: str | None = None) -> None:
        if self.notify:
            self.notify.notify(r.customer_id, kind, title, body, ref=r.queue_no, dedup_key=dedup)

    @staticmethod
    def _missing_text(missing) -> str:
        return "; ".join(f"{m.as_dict()['message']} ({m.detail})" for m in missing)

    # --- thông số ---------------------------------------------------------------------------------------------------
    def params(self) -> Params:
        with self.sm() as s:
            return Params.from_overrides({p.key: p.value for p in s.scalars(select(Param)).all()})

    def set_param(self, actor: Actor, key: str, value) -> Params:
        if not hasattr(Params(), key):
            raise RequestError(f"không có thông số {key}")
        with self.sm() as s:
            row = s.get(Param, key)
            before = None if row is None else row.value
            if row is None:
                s.add(Param(key=key, value=value, set_by=actor.id))
            else:
                row.value, row.set_by, row.effective_from = value, actor.id, _now()
            s.commit()
        self.audit.record(actor, "scheduling.param.set", target_type="param", target_id=key, before={"value": before},
                          after={"value": value})
        return self.params()

    # --- số thứ tự --------------------------------------------------------------------------------------------------
    @staticmethod
    def _next_no(s: Session, prefix: str, at: dt.datetime) -> str:
        key = f"{prefix}-{at.astimezone(TZ):%Y%m%d}"
        row = s.get(QueueCounter, key, with_for_update=True)
        if row is None:
            row = QueueCounter(key=key, last=0)
            s.add(row)
            s.flush()
            row = s.get(QueueCounter, key, with_for_update=True)
        row.last += 1
        return f"{key}-{row.last:04d}"

    # --- kiểm hồ sơ -------------------------------------------------------------------------------------------------
    def _check(self, r: FlightRequest, items: list[RequestItem], stage: str = "request"):
        start, end = min(i.starts_at for i in items), max(i.ends_at for i in items)
        return self.registry.eligibility(r.aircraft_id, r.pilot_id, start, end, r.bvlos, stage)

    def _log_block(self, s: Session, actor: Actor, r: FlightRequest, stage: str, missing, item: RequestItem | None = None,
                   run_id: uuid.UUID | None = None) -> None:
        for m in missing:
            s.add(BlockReason(request_id=r.id, item_id=item and item.id, stage=stage, rule=m.rule, detail=m.detail[:300],
                              run_id=run_id))
        self.audit.record(actor, "scheduling.request.blocked", "rejected", target_type="flight_request", target_id=r.id,
                          detail={"queue_no": r.queue_no, "stage": stage, "item_id": item and str(item.id),
                                  "run_id": run_id and str(run_id), "missing": [m.as_dict() for m in missing]})
        titles = {"submit": "Hồ sơ chưa đủ, đã cấp số tạm", "recheck": "Hồ sơ vẫn chưa đủ", "run": "Chưa xếp lịch được do thiếu hồ sơ",
                  "final": "Khung bay bị huỷ ở lần xác nhận cuối"}
        self._tell(r, f"blocked_{stage}", f"{titles[stage]}: {r.queue_no}", self._missing_text(missing),
                   dedup=f"blocked:{stage}:{r.id}:{run_id or ''}:{item.id if item else ''}:{r.queue_no}")

    # --- nhận yêu cầu -----------------------------------------------------------------------------------------------
    def submit(self, actor: Actor, customer_id: uuid.UUID, *, aircraft_id: uuid.UUID, pilot_id: uuid.UUID, priority_group: int,
               bvlos: bool, block_ids: list[str], items: list[ItemIn], now: dt.datetime | None = None) -> tuple[FlightRequest, list]:
        now = now or _now()
        p = self.params()
        today = now.astimezone(TZ).date()
        problems = []
        days = sorted({i.day for i in items})
        if not items:
            problems.append("yêu cầu phải có ít nhất một khung bay")
        elif (days[-1] - days[0]).days >= p.max_request_days:
            problems.append(f"một yêu cầu tối đa {p.max_request_days} ngày liên tục")
        if any(i.day <= today or (i.day - today).days > p.booking_window_days for i in items):
            problems.append(f"khung bay phải từ ngày mai đến {p.booking_window_days} ngày tới")
        if any(not 0 <= i.slot < len(p.slot_starts) for i in items):
            problems.append(f"khung trong ngày từ 0 đến {len(p.slot_starts) - 1}")
        if len({(i.day, i.slot) for i in items}) != len(items):
            problems.append("khung bay bị lặp")
        if not block_ids or any(b not in p.open_blocks for b in block_ids):
            problems.append(f"khối đang mở: {', '.join(p.open_blocks)}")
        if not 1 <= priority_group <= 4:
            problems.append("nhóm ưu tiên từ 1 đến 4")
        with self.sm() as s:
            ac, cu = s.get(Aircraft, aircraft_id), s.get(Customer, customer_id)
            if ac is None or cu is None or ac.customer_id != cu.id:
                problems.append("phương tiện không thuộc khách hàng")
        if problems:
            self.audit.record(actor, "scheduling.request.submit", "rejected", target_type="customer", target_id=customer_id,
                              detail={"problems": problems})
            raise RequestError("; ".join(problems))

        with self.sm() as s:
            r = FlightRequest(queue_at=now, customer_id=cu.id, customer_tier=cu.tier, aircraft_id=ac.id, uas_id=ac.uas_id,
                              pilot_id=pilot_id, priority_group=priority_group, bvlos=bvlos, block_ids=sorted(block_ids),
                              created_by=actor.id, provisional=True, status="provisional", queue_no="")
            its = []
            for i in sorted(items, key=lambda x: (x.day, x.slot)):
                st, en = slot_window(i.day, i.slot, p)
                its.append(RequestItem(day=i.day, slot=i.slot, starts_at=st, ends_at=en))
            elig = self._check(r, its)
            if elig.eligible:
                r.provisional, r.status, r.complete_at = False, "queued", now
                r.queue_no = self._next_no(s, "HL", now)
            else:
                r.hold_until = add_workdays(now, p.provisional_hold_workdays, p).astimezone(dt.timezone.utc)
                r.queue_no = self._next_no(s, "TAM", now)
            s.add(r)
            s.flush()
            for it in its:
                it.request_id = r.id
            s.add_all(its)
            if not elig.eligible:
                self._log_block(s, actor, r, "submit", elig.missing)
            s.commit()
            s.refresh(r)
        self.audit.record(actor, "scheduling.request.submit", target_type="flight_request", target_id=r.id,
                          after={"queue_no": r.queue_no, "status": r.status, "items": [[str(i.day), i.slot] for i in items]})
        if not r.provisional:
            self._tell(r, "queued", f"Đã nhận yêu cầu {r.queue_no}", "Hồ sơ đầy đủ. Yêu cầu sẽ được xét ở kỳ xếp lịch 16:00.",
                       dedup=f"queued:{r.id}")
        return r, elig.missing

    def recheck(self, actor: Actor, request_id: uuid.UUID, now: dt.datetime | None = None) -> tuple[FlightRequest, list]:
        """Khách bổ sung hồ sơ: trong thời hạn giữ chỗ thì nhận số chính thức, giữ nguyên thời điểm đến."""
        now = now or _now()
        with self.sm() as s:
            r = s.get(FlightRequest, request_id, with_for_update=True)
            if r.status != "provisional":
                return r, []
            if r.hold_until and r.hold_until < now:
                self._expire(s, actor, r)
                s.commit()
                return r, []
            its = list(s.scalars(select(RequestItem).where(RequestItem.request_id == r.id)).all())
            elig = self._check(r, its)
            if elig.eligible:
                before = {"queue_no": r.queue_no, "status": r.status}
                r.provisional, r.status, r.complete_at = False, "queued", now
                r.queue_no = self._next_no(s, "HL", r.queue_at)
                s.commit()
                self.audit.record(actor, "scheduling.request.complete", target_type="flight_request", target_id=r.id,
                                  before=before, after={"queue_no": r.queue_no, "status": r.status})
                self._tell(r, "queued", f"Hồ sơ đã đủ, số chính thức {r.queue_no}",
                           "Giữ nguyên thời điểm đến ban đầu. Yêu cầu sẽ được xét ở kỳ xếp lịch 16:00.", dedup=f"queued:{r.id}")
            else:
                self._log_block(s, actor, r, "recheck", elig.missing)
                s.commit()
            return r, elig.missing

    def _expire(self, s: Session, actor: Actor, r: FlightRequest) -> None:
        r.status = "expired"
        for it in s.scalars(select(RequestItem).where(RequestItem.request_id == r.id)).all():
            it.status, it.last_reason = "cancelled", "HOLD_EXPIRED"
        self.audit.record(actor, "scheduling.request.expire", target_type="flight_request", target_id=r.id,
                          detail={"queue_no": r.queue_no, "hold_until": r.hold_until})
        self._tell(r, "expired", f"Số tạm {r.queue_no} hết hạn giữ chỗ", "Hồ sơ không được bổ sung trong 3 ngày làm việc. "
                   "Vui lòng nộp yêu cầu mới.", dedup=f"expired:{r.id}")

    def cancel(self, actor: Actor, request_id: uuid.UUID) -> FlightRequest:
        with self.sm() as s:
            r = s.get(FlightRequest, request_id, with_for_update=True)
            before = r.status
            for it in s.scalars(select(RequestItem).where(RequestItem.request_id == r.id)).all():
                if it.status in ("requested", "scheduled"):
                    if it.flight_id:
                        self.ops.cancel_flight(it.flight_id)
                    it.status, it.last_reason = "cancelled", "CUSTOMER_CANCELLED"
            r.status = "cancelled"
            s.commit()
        self.audit.record(actor, "scheduling.request.cancel", target_type="flight_request", target_id=r.id,
                          before={"status": before}, after={"status": "cancelled"})
        return r

    # --- kỳ xếp lịch ------------------------------------------------------------------------------------------------
    def run(self, actor: Actor, now: dt.datetime | None = None) -> SchedulingRun:
        now = now or _now()
        p = self.params()
        today = now.astimezone(TZ).date()
        tomorrow = today + dt.timedelta(days=1)
        run_id = uuid.uuid4()
        decisions: list[dict] = []
        with self.sm() as s:
            # 1. Số tạm: quá hạn giữ chỗ thì hết hiệu lực; còn hạn thì kiểm lại, đủ thì lên số chính thức, thiếu thì ghi chặn.
            for r in s.scalars(select(FlightRequest).where(FlightRequest.status == "provisional").with_for_update()).all():
                if r.hold_until and r.hold_until < now:
                    self._expire(s, actor, r)
                    decisions.append({"queue_no": r.queue_no, "decision": "expired", "reason": "HOLD_EXPIRED"})
                    continue
                its = list(s.scalars(select(RequestItem).where(RequestItem.request_id == r.id)).all())
                elig = self._check(r, its)
                if elig.eligible:
                    r.provisional, r.status, r.complete_at = False, "queued", now
                    r.queue_no = self._next_no(s, "HL", r.queue_at)
                else:
                    self._log_block(s, actor, r, "run", elig.missing, run_id=run_id)
                    decisions.append({"queue_no": r.queue_no, "decision": "blocked", "reason": [m.rule for m in elig.missing]})
            s.flush()

            # 2. Ứng viên: khung còn chờ của yêu cầu đã có số chính thức, trong cửa sổ đặt chỗ.
            reqs = {r.id: r for r in s.scalars(select(FlightRequest).where(
                FlightRequest.status.in_(("queued", "partial", "scheduled")))).all()}
            pending = [i for i in s.scalars(select(RequestItem).where(
                RequestItem.status == "requested", RequestItem.request_id.in_(reqs.keys())).with_for_update()).all()
                if i.day >= tomorrow and (i.day - today).days <= p.booking_window_days]
            stale = s.scalars(select(RequestItem).where(RequestItem.status == "requested", RequestItem.day < tomorrow,
                                                        RequestItem.request_id.in_(reqs.keys()))).all()
            for i in stale:
                i.status, i.last_reason, i.decided_in_run = "not_scheduled", "SLOT_PASSED", run_id

            # 3. Kiểm lại hồ sơ từng yêu cầu; thiếu thì chặn, ghi lý do, không xếp.
            by_req: dict[uuid.UUID, list[RequestItem]] = defaultdict(list)
            for i in pending:
                by_req[i.request_id].append(i)
            eligible_items: list[RequestItem] = []
            for rid, its in by_req.items():
                r = reqs[rid]
                elig = self._check(r, its)
                if elig.eligible:
                    eligible_items += its
                    continue
                self._log_block(s, actor, r, "run", elig.missing, run_id=run_id)
                for i in its:
                    i.last_reason = "INELIGIBLE"
                    if i.day <= tomorrow:  # không còn kỳ xếp lịch nào trước khung này
                        i.status, i.decided_in_run = "not_scheduled", run_id
                    decisions.append({"queue_no": r.queue_no, "item_id": str(i.id), "day": str(i.day), "slot": i.slot,
                                      "decision": "blocked", "reason": [m.rule for m in elig.missing]})

            # 4. Chiếm chỗ hiện có, rồi xếp theo thứ tự ưu tiên.
            occupied: dict[tuple[dt.date, int], list[set]] = defaultdict(list)
            for i in s.scalars(select(RequestItem).where(RequestItem.status == "scheduled", RequestItem.day >= tomorrow)).all():
                rr = reqs.get(i.request_id) or s.get(FlightRequest, i.request_id)
                occupied[(i.day, i.slot)].append(set(rr.block_ids))
            eligible_items.sort(key=lambda i: (order_key(reqs[i.request_id], now), i.day, i.slot))
            inputs = [[reqs[i.request_id].queue_no, str(i.day), i.slot] for i in eligible_items]
            for i in eligible_items:
                r = reqs[i.request_id]
                blocks = set(r.block_ids)
                here = occupied[(i.day, i.slot)]
                if not blocks <= set(p.open_blocks):
                    reason = "BLOCK_CLOSED"
                elif len(here) >= p.max_concurrent_corridor:
                    reason = "CORRIDOR_FULL"
                elif any(sum(b in o for o in here) >= p.max_flights_per_block for b in blocks):
                    reason = "BLOCK_TAKEN"
                else:
                    reason = None
                if reason:
                    i.status, i.last_reason, i.decided_in_run = "not_scheduled", reason, run_id
                    self._tell(r, "not_scheduled", f"Không xếp được khung {i.day} khung {i.slot + 1} ({r.queue_no})",
                               {"BLOCK_CLOSED": "Khối chưa mở.", "CORRIDOR_FULL": "Hành lang đã đủ 2 chuyến trong khung này.",
                                "BLOCK_TAKEN": "Khối đã được xếp cho yêu cầu ưu tiên hơn."}[reason], dedup=f"ns:{i.id}")
                    decisions.append({"queue_no": r.queue_no, "item_id": str(i.id), "day": str(i.day), "slot": i.slot,
                                      "decision": "not_scheduled", "reason": reason})
                    continue
                i.flight_id = self.ops.create_flight(request_ref=r.queue_no, uas_id=r.uas_id, customer_id=r.customer_id,
                                                     starts_at=i.starts_at, ends_at=i.ends_at, block_ids=r.block_ids, bvlos=r.bvlos)
                i.status, i.last_reason, i.decided_in_run = "scheduled", None, run_id
                here.append(blocks)
                self._tell(r, "scheduled", f"Đã xếp lịch {i.day} khung {i.slot + 1} ({r.queue_no})",
                           f"Từ {i.starts_at.astimezone(TZ):%H:%M} đến {i.ends_at.astimezone(TZ):%H:%M} ngày {i.day:%d/%m/%Y}, "
                           f"{', '.join(r.block_ids)}. Đây là lịch bay nội bộ của hành lang, không phải phép bay.", dedup=f"sch:{i.id}")
                decisions.append({"queue_no": r.queue_no, "item_id": str(i.id), "day": str(i.day), "slot": i.slot,
                                  "decision": "scheduled", "flight_id": str(i.flight_id)})
            s.flush()

            # 5. Xác nhận lần cuối T − 3 ngày làm việc (kể cả khung vừa xếp sát ngày).
            for i in s.scalars(select(RequestItem).where(RequestItem.status == "scheduled", RequestItem.final_checked_at.is_(None),
                                                         RequestItem.day >= tomorrow)).all():
                if sub_workdays(i.day, p.final_confirmation_workdays, p) > today:
                    continue
                r = reqs.get(i.request_id) or s.get(FlightRequest, i.request_id)
                elig = self._check(r, [i], stage="final")
                i.final_checked_at = now
                if elig.eligible:
                    continue
                self.ops.cancel_flight(i.flight_id)
                i.status, i.last_reason = "cancelled", "FINAL_CHECK_FAILED"
                self._log_block(s, actor, r, "final", elig.missing, item=i, run_id=run_id)
                decisions.append({"queue_no": r.queue_no, "item_id": str(i.id), "day": str(i.day), "slot": i.slot,
                                  "decision": "cancelled", "reason": [m.rule for m in elig.missing]})

            # 6. Nhắc việc T − 7, T − 3, T − 1 (ngày lịch) cho khung đã xếp.
            for i in s.scalars(select(RequestItem).where(RequestItem.status == "scheduled", RequestItem.day >= tomorrow)).all():
                r = reqs.get(i.request_id) or s.get(FlightRequest, i.request_id)
                for k in (1, 3, 7):  # mốc gần nhất đã tới
                    if (i.day - today).days <= k:
                        self._tell(r, f"reminder_t{k}", f"Nhắc lịch bay T − {k}: {i.day:%d/%m/%Y} khung {i.slot + 1} ({r.queue_no})",
                                   "Kiểm tra lại hồ sơ, bảo hiểm và phương tiện." + (" Hồ sơ được xác nhận lần cuối ở mốc T − 3 ngày làm việc."
                                                                                    if k == 7 else ""), dedup=f"rem{k}:{i.id}")
                        break  # mỗi kỳ chỉ nhắc mốc gần nhất

            # 7. Trạng thái yêu cầu và mốc 3 ngày làm việc.
            overdue = []
            for r in reqs.values():
                its = s.scalars(select(RequestItem).where(RequestItem.request_id == r.id)).all()
                st = {i.status for i in its}
                if "requested" not in st and r.status != "cancelled":
                    r.status = "scheduled" if st == {"scheduled"} else "partial" if "scheduled" in st else "not_scheduled"
                elif "scheduled" in st:
                    r.status = "partial"
                if "requested" in st and r.complete_at and add_workdays(r.complete_at, p.scheduling_deadline_workdays, p) < now:
                    overdue.append(r.queue_no)

            counts = defaultdict(int)
            for d in decisions:
                counts[d["decision"]] += 1
            run = SchedulingRun(id=run_id, run_at=now, kind="daily", triggered_by=actor.label,
                                inputs_hash=hashlib.sha256(json.dumps({"inputs": inputs, "params": p.__dict__},
                                                                      sort_keys=True, default=str).encode()).hexdigest(),
                                decisions=decisions, summary={**counts, "overdue_requests": overdue})
            s.add(run)
            s.commit()
        self.audit.record(actor, "scheduling.run", target_type="scheduling_run", target_id=run_id,
                          detail={"run_at": now, "summary": run.summary, "inputs_hash": run.inputs_hash})
        return run

    # --- tra cứu ----------------------------------------------------------------------------------------------------
    def queue_position(self, r: FlightRequest) -> int | None:
        """Vị trí trong hàng chờ (công khai): số yêu cầu chính thức còn chờ đứng trước, theo thứ tự xét, cộng 1."""
        if r.status not in ("queued", "partial"):
            return None
        now = _now()
        with self.sm() as s:
            waiting = s.scalars(select(FlightRequest).where(FlightRequest.status.in_(("queued", "partial")))).all()
        return 1 + sum(order_key(w, now) < order_key(r, now) for w in waiting)

    def availability(self, start: dt.date, end: dt.date) -> list[dict]:
        p = self.params()
        with self.sm() as s:
            rows = s.execute(select(RequestItem, FlightRequest).join(FlightRequest, RequestItem.request_id == FlightRequest.id)
                             .where(RequestItem.day >= start, RequestItem.day <= end,
                                    RequestItem.status.in_(("scheduled", "requested")))).all()
        used: dict[tuple, dict] = defaultdict(lambda: {"scheduled_blocks": [], "waiting": 0})
        for it, r in rows:
            k = (it.day, it.slot)
            if it.status == "scheduled":
                used[k]["scheduled_blocks"] += r.block_ids
            else:
                used[k]["waiting"] += 1
        out = []
        d = start
        while d <= end:
            for slot in range(len(p.slot_starts)):
                u = used[(d, slot)]
                st, en = slot_window(d, slot, p)
                out.append({"day": str(d), "slot": slot, "starts_at": st, "ends_at": en,
                            "free_blocks": [b for b in p.open_blocks if u["scheduled_blocks"].count(b) < p.max_flights_per_block],
                            "scheduled_blocks": sorted(u["scheduled_blocks"]), "waiting_items": u["waiting"]})
            d += dt.timedelta(days=1)
        return out
