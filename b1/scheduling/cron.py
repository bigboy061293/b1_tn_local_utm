"""Chạy kỳ xếp lịch, gọi từ bộ hẹn giờ của hệ thống lúc 16:00 các ngày làm việc (giờ Việt Nam):

    0 16 * * 1-5  TZ=Asia/Ho_Chi_Minh  python -m b1.scheduling.cron
"""
from __future__ import annotations

import json

from b1.app import create_app
from b1.audit.service import SYSTEM
from b1.scheduling.calendar import TZ, is_workday

if __name__ == "__main__":
    app = create_app()
    svc = app.state.scheduling
    import datetime as dt

    today = dt.datetime.now(TZ).date()
    if not is_workday(today, svc.params()):
        print("hôm nay không phải ngày làm việc")
        raise SystemExit(0)
    run = svc.run(SYSTEM)
    print(json.dumps({"run_id": str(run.id), "summary": run.summary}, ensure_ascii=False))
