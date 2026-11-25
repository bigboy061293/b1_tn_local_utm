"""Thử khái niệm bước 1: Network Remote ID toàn tuyến (ASTM F3411-22a), tiền đề cho AT-05.

Phương tiện bay của bản sao số (`twin.sim.Aircraft`) bay hết tim tuyến 54,7 km. Telemetry 1 Hz được nạp vào
USS cung cấp Remote ID (mock_uss `ridsp`) qua API kiểm thử tự động của InterUSS; USS này đăng ký vùng dịch vụ
(ISA) lên DSS. Một USS hiển thị (mock_uss `riddp`) bám theo phương tiện mỗi giây với khung nhìn 0,9 km, đi qua
DSS để tìm và lấy dữ liệu, như một màn hình theo dõi thật.

Đo được:
- tỷ lệ số giây thấy chuyến bay trên toàn chiều dài tuyến (mục tiêu 100%);
- độ trễ dữ liệu: thời điểm hiển thị trừ thời điểm của điểm telemetry khớp vị trí hiển thị (API hiển thị không
  trả thời điểm, nên suy ra từ vị trí; độ phân giải 1 s; chỉ tiêu giám sát ≤3 giây);
- sai số vị trí so với điểm telemetry khớp nhất;
- những đoạn Km bị hở.

Chạy khi hệ sinh thái InterUSS đang chạy (xem poc/README.md):
    python3 poc/netrid_full_route.py --speed 30     # ~30 phút cho 54,7 km
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from twin import sim  # noqa: E402

OAUTH = "http://localhost:8085/token"
RIDSP = "http://localhost:8081"
RIDDP = "http://localhost:8083"
SCOPE_INJECT = "rid.inject_test_data"
SCOPE_READ = "dss.read.identification_service_areas"
VIEW_HALF_M = 450.0  # khung nhìn 0,9 km (đường chéo 1,27 km < 2 km): F3411-22a chỉ trả chi tiết từng chuyến khi đường chéo ≤2 km, lớn hơn thì gộp cụm


def token(scope: str) -> str:
    q = urllib.parse.urlencode({"intended_audience": "localhost", "scope": scope, "sub": "b1_poc"})
    with urllib.request.urlopen(f"{OAUTH}?{q}", timeout=10) as r:
        return json.load(r)["access_token"]


def call(method: str, url: str, scope: str, body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token(scope)}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:500]}


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=30.0, help="m/s; 30 m/s bay hết tuyến trong ~30 phút")
    ap.add_argument("--alt", type=float, default=120.0, help="m AGL")
    ap.add_argument("--chunk-s", type=int, default=120, help="nạp cuốn chiếu từng đoạn N giây; 0 = nạp một lần")
    ap.add_argument("--retire-after-s", type=int, default=300, help="gỡ đoạn đã nạp N giây sau khi bay qua")
    ap.add_argument("--out", default="poc/results/netrid_full_route.json")
    a = ap.parse_args()

    line = sim.centerline_utm()
    ac = sim.Aircraft(line, 0.0, a.speed, a.alt, "B1TNSIM0001")
    duration = math.ceil(line.length / a.speed)
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(seconds=20)

    # 1. Dựng telemetry 1 Hz cho cả chuyến (chuẩn F3411 yêu cầu ≥1 Hz).
    track: list[tuple[float, float, float]] = []  # (lat, lon, km)
    telemetry = []
    for k in range(duration + 1):
        s = ac.state(float(k), [])
        lon, lat = sim.TO_WGS(s["x"], s["y"])
        track.append((lat, lon, s["km"]))
        telemetry.append({
            "timestamp": iso(t0 + dt.timedelta(seconds=k)),
            "timestamp_accuracy": 0.1,
            "operational_status": "Airborne",
            "position": {"lat": round(lat, 7), "lng": round(lon, 7), "alt": a.alt + 20.0,
                         "accuracy_h": "HA3m", "accuracy_v": "VA3m", "extrapolated": False,
                         "height": {"distance": a.alt, "reference": "TakeoffLocation"}},
            "track": round(s["track"], 1), "speed": s["speed"], "speed_accuracy": "SA1mps",
            "vertical_speed": 0.0,
            "height": {"distance": a.alt, "reference": "TakeoffLocation"},
        })
    details = {"id": str(uuid.uuid4()), "operator_id": "VNM-OP-B1TN-0001",
               "operation_description": "B1 thử khái niệm Network Remote ID toàn tuyến",
               "serial_number": "1596F3YB1234567890", "registration_number": "VN-TEST-0001"}

    # mock_uss duyệt toàn bộ telemetry đã nạp ở mỗi truy vấn, nên chuyến 30 phút làm truy vấn chậm ~2 s (chuyến
    # 150 s: ~0,3 s). USS thật nhận telemetry trực tiếp, không có độ trễ này. Vì vậy nạp cuốn chiếu từng đoạn
    # --chunk-s giây (chồng 10 s), mỗi đoạn nạp trước 20 s; --chunk-s 0 để nạp một lần cả chuyến.
    chunk = a.chunk_s if a.chunk_s > 0 else duration + 1
    segments = [(i, min(i + chunk + 10, duration)) for i in range(0, duration + 1, chunk)]
    injected: list[tuple[str, str]] = []
    errors: list[str] = []

    def inject(lo: int, hi: int) -> None:
        flight = {"injection_id": str(uuid.uuid4()), "aircraft_type": "HybridLift", "telemetry": telemetry[lo:hi + 1],
                  "details_responses": [{"effective_after": iso(t0 + dt.timedelta(seconds=lo)), "details": details}]}
        test_id = str(uuid.uuid4())
        code, resp = call("PUT", f"{RIDSP}/ridsp/injection/tests/{test_id}", SCOPE_INJECT, {"requested_flights": [flight]})
        if code != 200:
            errors.append(f"đoạn {lo}-{hi} s: HTTP {code} {resp}")
            return
        injected.append((test_id, resp.get("version"), hi))

    def retire() -> None:
        # Gỡ đoạn đã bay xong để mock_uss không phải duyệt telemetry cũ. Chỉ gỡ khi phương tiện đã đi xa
        # (--retire-after-s): khi ISA bị xoá, DSS báo cho đăng ký của USS hiển thị, và mock_uss 0.38 lỗi khi đọc
        # thông báo xoá đó (ValueError "No valid representation was specified for ISA", trả HTTP 400 ~15 s).
        el = (dt.datetime.now(dt.timezone.utc) - t0).total_seconds()
        for item in [x for x in injected if x[2] + a.retire_after_s < el]:
            injected.remove(item)
            call("DELETE", f"{RIDSP}/ridsp/injection/tests/{item[0]}/{item[1]}", SCOPE_INJECT)

    def feeder() -> None:
        for lo, hi in segments[1:]:
            wait = (t0 + dt.timedelta(seconds=lo - 20) - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            inject(lo, hi)
            retire()

    inject(*segments[0])
    if errors:
        print(f"nạp chuyến bay thất bại: {errors[0]}", file=sys.stderr)
        return 2
    threading.Thread(target=feeder, daemon=True).start()
    track_xy = [sim.TO_UTM(lon, lat) for lat, lon, _ in track]
    print(f"chuyến bay {duration} s ({line.length / 1000:.2f} km), {len(segments)} đoạn nạp, bắt đầu lúc {iso(t0)}")

    # 2. Bám theo phương tiện mỗi giây qua USS hiển thị.
    samples = []
    try:
        while dt.datetime.now(dt.timezone.utc) < t0:
            time.sleep(0.2)
        # Lấy mẫu theo đồng hồ thật, tối đa 1 lần/giây; dừng khi chuyến bay kết thúc. Nếu một truy vấn chậm hơn
        # 1 s thì giây đó bị bỏ qua (không lấy mẫu), không phải là không thấy.
        k = 0
        end = t0 + dt.timedelta(seconds=duration)
        while True:
            target = t0 + dt.timedelta(seconds=k)
            wait = (target - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            now = dt.datetime.now(dt.timezone.utc)
            if now > end:
                break
            el = (now - t0).total_seconds()
            idx = min(max(int(el), 0), duration)
            lat, lon, km = track[idx]
            x, y = sim.TO_UTM(lon, lat)
            lo = sim.TO_WGS(x - VIEW_HALF_M, y - VIEW_HALF_M)
            hi = sim.TO_WGS(x + VIEW_HALF_M, y + VIEW_HALF_M)
            view = f"{lo[1]:.7f},{lo[0]:.7f},{hi[1]:.7f},{hi[0]:.7f}"
            q0 = time.perf_counter()
            asked = dt.datetime.now(dt.timezone.utc)
            code, data = call("GET", f"{RIDDP}/riddp/observation/display_data?view={view}", SCOPE_READ, timeout=10)
            q_ms = (time.perf_counter() - q0) * 1000.0
            seen, age_s, err_m, age_req_s = False, None, None, None
            for f in data.get("flights", []) if code == 200 else []:
                p = f.get("most_recent_position") or {}
                if "lat" not in p:
                    continue
                seen = True
                # API hiển thị F3411 không trả thời điểm của vị trí. Suy ra: tìm điểm telemetry đã nạp gần vị trí
                # hiển thị nhất (quanh giây hiện tại), độ trễ = bây giờ - thời điểm của điểm đó (độ phân giải 1 s).
                px, py = sim.TO_UTM(p["lng"], p["lat"])
                best = None
                for j in range(max(idx - 30, 0), min(idx + 3, duration) + 1):
                    d = math.hypot(px - track_xy[j][0], py - track_xy[j][1])
                    if best is None or d < best[1]:
                        best = (j, d)
                j, err_m = best
                age_s = (dt.datetime.now(dt.timezone.utc) - (t0 + dt.timedelta(seconds=j))).total_seconds()
                age_req_s = (asked - (t0 + dt.timedelta(seconds=j))).total_seconds()
            samples.append({"k": k, "km": round(km, 3), "http": code, "seen": seen,
                            "data_age_s": None if age_s is None else round(age_s, 3),
                            "age_at_request_s": None if age_req_s is None else round(age_req_s, 3),
                            "pos_err_m": None if err_m is None else round(err_m, 2), "query_ms": round(q_ms, 1)})
            if len(samples) % 60 == 1:
                print(f"  Km {km:6.2f}: {'thấy' if seen else 'KHÔNG thấy'} (HTTP {code}, {q_ms:.0f} ms)")
            k = max(k + 1, math.ceil((dt.datetime.now(dt.timezone.utc) - t0).total_seconds()))
    finally:
        for test_id, version, _ in list(injected):
            call("DELETE", f"{RIDSP}/ridsp/injection/tests/{test_id}/{version}", SCOPE_INJECT)

    # 3. Tổng hợp.
    n = len(samples)
    seen = [s for s in samples if s["seen"]]
    ages = sorted(s["data_age_s"] for s in seen if s["data_age_s"] is not None)
    errs = sorted(s["pos_err_m"] for s in seen if s["pos_err_m"] is not None)
    p95 = lambda xs: xs[min(len(xs) - 1, int(math.ceil(0.95 * len(xs))) - 1)] if xs else None
    gaps, cur = [], None
    for s in samples:
        if not s["seen"]:
            cur = cur or [s["km"], s["km"]]
            cur[1] = s["km"]
        elif cur:
            gaps.append(cur)
            cur = None
    if cur:
        gaps.append(cur)
    summary = {
        "run_at": iso(dt.datetime.now(dt.timezone.utc)), "route_km": round(line.length / 1000, 2),
        "speed_ms": a.speed, "telemetry_hz": 1, "samples": n, "seen": len(seen),
        "coverage_percent": round(100.0 * len(seen) / n, 2) if n else 0.0,
        "sampled_seconds": n, "flight_seconds": duration + 1,
        "data_age_s_p50": ages[len(ages) // 2] if ages else None, "data_age_s_p95": p95(ages),
        "age_at_request_s_p95": p95(sorted(s["age_at_request_s"] for s in seen if s["age_at_request_s"] is not None)),
        "pos_err_m_p95": p95(errs), "query_ms_p95": p95(sorted(s["query_ms"] for s in samples)),
        "gaps_km": gaps, "chunk_s": a.chunk_s, "retire_after_s": a.retire_after_s, "segments": len(segments), "injection_errors": errors,
    }
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "samples": samples}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if summary["coverage_percent"] == 100.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
