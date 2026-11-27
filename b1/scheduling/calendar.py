"""Lịch khai thác: khung bay, ngày làm việc, thông số xếp lịch (Đặc tả B1 v0.1, Mục 4.3, 2.3)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class Params:
    """Giá trị ban đầu, hiệu chỉnh ở kỳ đánh giá 6 tháng. Lưu đè trong bảng scheduling.param."""
    slot_starts: list[str] = field(default_factory=lambda: ["06:00", "08:30", "13:30", "16:00"])  # [đề xuất] 4 khung 2 giờ
    slot_hours: float = 2.0
    booking_window_days: int = 60
    max_request_days: int = 5             # độ dài tối đa một yêu cầu: 5 ngày liên tục
    provisional_hold_workdays: int = 3    # hồ sơ chưa đủ: số tạm, giữ chỗ 3 ngày làm việc
    scheduling_deadline_workdays: int = 3  # xếp lịch ≤3 ngày làm việc từ khi hồ sơ đầy đủ
    final_confirmation_workdays: int = 3  # xác nhận lần cuối T − 3 ngày làm việc
    run_time: str = "16:00"               # kỳ xếp lịch
    open_blocks: list[str] = field(default_factory=lambda: ["block-1"])  # 6 tháng đầu chỉ Khối 1 (Mục 2.3)
    max_concurrent_corridor: int = 2      # tối đa 2 chuyến đồng thời trên toàn hành lang
    max_flights_per_block: int = 1        # mỗi khối tối đa 1 chuyến bay ngoài tầm nhìn; bản này áp cho mọi chuyến
    rebook_right_days: int = 14
    holidays: list[str] = field(default_factory=list)  # ngày nghỉ lễ YYYY-MM-DD, do bộ phận quản lý hành lang nhập

    @classmethod
    def from_overrides(cls, overrides: dict) -> "Params":
        p = cls()
        for k, v in overrides.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


def is_workday(d: dt.date, p: Params) -> bool:
    return d.weekday() < 5 and d.isoformat() not in p.holidays


def add_workdays(start: dt.datetime, n: int, p: Params) -> dt.datetime:
    """Cộng n ngày làm việc (giữ giờ trong ngày, theo giờ Việt Nam)."""
    t = start.astimezone(TZ)
    while n > 0:
        t += dt.timedelta(days=1)
        if is_workday(t.date(), p):
            n -= 1
    return t


def sub_workdays(day: dt.date, n: int, p: Params) -> dt.date:
    d = day
    while n > 0:
        d -= dt.timedelta(days=1)
        if is_workday(d, p):
            n -= 1
    return d


def slot_window(day: dt.date, slot: int, p: Params) -> tuple[dt.datetime, dt.datetime]:
    hh, mm = (int(x) for x in p.slot_starts[slot].split(":"))
    start = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
    return start.astimezone(dt.timezone.utc), (start + dt.timedelta(hours=p.slot_hours)).astimezone(dt.timezone.utc)
