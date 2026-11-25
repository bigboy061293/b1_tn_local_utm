"""Thử khái niệm bước 1: hai USS cùng xin một khối của hành lang B1 qua DSS (ASTM F3548-21).

Chạy trên hệ sinh thái cục bộ của InterUSS (DSS + OAuth giả lập + mock_uss), xem poc/README.md.

Kịch bản (Đặc tả B1 v0.1, Mục 4.4):
  1. USS A khai ý định bay trên Khối 1, bậc ưu tiên P.
  2. USS B khai ý định bay trùng Khối 1, cùng khung giờ, cùng bậc P  -> phải bị từ chối (cùng bậc không được trùng,
     ý định khai trước giữ khối).
  3. USS B khai ý định trên Khối 1 sau khung giờ của A                -> phải được chấp nhận.
  4. USS B khai ý định trên Khối 2 cùng khung giờ với A               -> phải được chấp nhận (khối khác).
  5. Dọn dẹp: đóng mọi ý định đã tạo.

Mỗi bước đo thời gian phản hồi; kết quả ghi ra JSON để dùng cho quyết định D1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import uuid

from pyproj import Transformer
from shapely.geometry import Polygon

ROOT = pathlib.Path(__file__).resolve().parent.parent
BLOCKS = {f["id"]: f for f in json.loads((ROOT / "config/corridor/blocks.geojson").read_text(encoding="utf-8"))["features"]}

OAUTH = "http://localhost:8085/token"
USS = {"A": "http://localhost:8074", "B": "http://localhost:8094"}
SCOPE_PLAN = "interuss.flight_planning.plan"

# Demo dùng tầng 20–150 m AGL (D3). F3548 dùng độ cao WGS84; hành lang ở độ cao địa hình ~10–30 m nên
# dải 30–180 m W84 là xấp xỉ cho thử khái niệm, không dùng cho vận hành.
ALT_LOWER_W84_M, ALT_UPPER_W84_M = 30.0, 180.0


def token(audience: str) -> str:
    q = urllib.parse.urlencode({"intended_audience": audience, "scope": SCOPE_PLAN, "sub": "b1_poc"})
    with urllib.request.urlopen(f"{OAUTH}?{q}", timeout=10) as r:
        return json.load(r)["access_token"]


TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True).transform
TO_WGS = Transformer.from_crs("EPSG:32648", "EPSG:4326", always_xy=True).transform
INSET_M = 0.0  # đặt từ --inset-m


def block_ring(block_id: str) -> list[tuple[float, float]]:
    """Đỉnh khối (lon, lat), co vào INSET_M mét nếu có, để hai khối kề nhau không chạm cạnh."""
    ring = BLOCKS[block_id]["geometry"]["coordinates"][0]
    if INSET_M > 0:
        poly = Polygon([TO_UTM(x, y) for x, y in ring]).buffer(-INSET_M, join_style="mitre")
        ring = [TO_WGS(x, y) for x, y in poly.exterior.coords]
    return [(round(x, 7), round(y, 7)) for x, y in ring[:-1]]  # bỏ đỉnh lặp cuối


def volume(block_id: str, start: dt.datetime, end: dt.datetime) -> dict:
    ring = block_ring(block_id)
    return {
        "volume": {
            "outline_polygon": {"vertices": [{"lat": lat, "lng": lon} for lon, lat in ring]},
            "altitude_lower": {"value": ALT_LOWER_W84_M, "reference": "W84", "units": "M"},
            "altitude_upper": {"value": ALT_UPPER_W84_M, "reference": "W84", "units": "M"},
        },
        "time_start": {"value": start.isoformat().replace("+00:00", "Z"), "format": "RFC3339"},
        "time_end": {"value": end.isoformat().replace("+00:00", "Z"), "format": "RFC3339"},
    }


def upsert(uss: str, plan_id: str, block_id: str, start: dt.datetime, end: dt.datetime, priority: int, state="Planned") -> tuple[dict, float]:
    body = {
        "flight_plan": {
            "basic_information": {"usage_state": state, "uas_state": "Nominal", "area": [volume(block_id, start, end)]},
            "astm_f3548_21": {"priority": priority},
        },
        "execution_style": "IfAllowed",
        "request_id": str(uuid.uuid4()),
    }
    req = urllib.request.Request(f"{USS[uss]}/flight_planning/v1/flight_plans/{plan_id}", method="PUT",
                                 data=json.dumps(body).encode(), headers={
                                     "Content-Type": "application/json", "Authorization": f"Bearer {token('localhost')}"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    return resp, (time.perf_counter() - t0) * 1000.0


def delete(uss: str, plan_id: str) -> None:
    req = urllib.request.Request(f"{USS[uss]}/flight_planning/v1/flight_plans/{plan_id}", method="DELETE",
                                 headers={"Authorization": f"Bearer {token('localhost')}"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:  # dọn dẹp tối đa có thể
        print(f"cảnh báo: xoá {plan_id} trên USS {uss} lỗi: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", type=int, default=10)
    ap.add_argument("--inset-m", type=float, default=0.0,
                    help="co ý định bay vào trong ranh khối N mét (vùng đệm biên)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    global INSET_M
    INSET_M = a.inset_m
    a.out = a.out or f"poc/results/two_uss_same_block_inset{int(a.inset_m)}m.json"

    t = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(minutes=30)
    slot_a = (t, t + dt.timedelta(hours=2))            # khung bay 2 giờ (Mục 4.3)
    slot_after = (slot_a[1] + dt.timedelta(minutes=5), slot_a[1] + dt.timedelta(hours=2, minutes=5))

    cases = [
        ("1. A xin Khối 1", "A", "block-1", slot_a, "Completed"),
        ("2. B xin Khối 1 trùng giờ, cùng bậc", "B", "block-1", slot_a, "Rejected"),
        ("3. B xin Khối 1 sau khung của A", "B", "block-1", slot_after, "Completed"),
        ("4. B xin Khối 2 cùng giờ với A", "B", "block-2", slot_a, "Completed"),
    ]
    created: list[tuple[str, str]] = []
    results, ok = [], True
    try:
        for name, uss, block, (s, e), expect in cases:
            pid = str(uuid.uuid4())
            resp, ms = upsert(uss, pid, block, s, e, a.priority)
            got = resp.get("planning_result")
            if resp.get("flight_plan_status") not in (None, "NotPlanned"):
                created.append((uss, pid))
            passed = got == expect
            ok &= passed
            results.append({"case": name, "uss": uss, "block": block, "expected": expect, "got": got,
                            "status": resp.get("flight_plan_status"), "latency_ms": round(ms, 1),
                            "notes": resp.get("notes")})
            print(f"{'ĐẠT ' if passed else 'TRƯỢT'} {name}: {got} ({ms:.0f} ms)")
    finally:
        for uss, pid in created:
            delete(uss, pid)

    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run_at": dt.datetime.now(dt.timezone.utc).isoformat(), "priority": a.priority, "inset_m": a.inset_m,
                               "passed": ok, "results": results}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"kết quả: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
