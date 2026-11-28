"""Bộ phát mẫu của khách cho phiên kiểm tra tuân thủ CT-1…8 (tham khảo cho khách tự viết bộ phát của mình).

Chạy theo đồng hồ thật, 10 Hz, gửi lô mỗi 100 ms tới `/telemetry/v1/flights/{phiên}/samples`:
- phương tiện đứng yên tại điểm RTK (nhiễu ≤ 3 m) suốt phiên, cho CT-4;
- bật cờ c2_lost và geofence_breach mỗi cờ 3 giây (CT-5);
- đoạn chạy tải: một luồng song song đẩy lô lớn liên tục lên cùng đường truyền (CT-7);
- đoạn ngắt 60 giây: không gửi, giữ mẫu trong bộ đệm; nối lại thì gửi tiếp thời gian thực và gửi bù bộ đệm qua /backfill
  với nhãn thời gian gốc (CT-8).
Đánh dấu các đoạn qua `/v1/ct-sessions/{id}/marks`.

    python tools/ct_client.py --base https://localhost:8443 --session <id> --uas-id <uas> --token-file tok.txt \\
        --cert client.pem --key client.key --ca ca.pem --duration 1860
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import threading
import time

import httpx


def iso_ms(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sample(session: str, uas: str, t: float, ref: dict, flags: list[str]) -> dict:
    r, a = random.uniform(0, 3.0), random.uniform(0, 2 * math.pi)
    lat = ref["lat"] + r * math.cos(a) / 110_600
    lon = ref["lon"] + r * math.sin(a) / (111_320 * math.cos(math.radians(ref["lat"])))
    return {"flightId": session, "uasId": uas, "timeSource": iso_ms(t), "position": {"lat": round(lat, 8), "lon": round(lon, 8)},
            "altitude": {"value": 0.3, "reference": "AGL"}, "velocity": {"groundSpeed": 0.0, "verticalSpeed": 0.0, "track": 0.0},
            "gnss": {"fixType": "rtk_fixed", "satellites": 24, "hdop": 0.5}, "energy": {"batteryPercent": 95.0, "voltage": 50.1},
            "flightMode": {"mode": "LOITER", "autonomy": "assisted"}, "armed": False, "safetyFlags": flags,
            "c2Link": {"state": "lost" if "c2_lost" in flags else "ok", "rssiDbm": -60}}


def run(a: argparse.Namespace) -> dict:
    tok = open(a.token_file).read().strip()
    kw = {"timeout": 10.0}
    if a.cert:
        import ssl
        ctx = ssl.create_default_context(cafile=a.ca)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(a.cert, a.key)
        kw["verify"] = ctx
    def client() -> httpx.Client:
        return httpx.Client(base_url=a.base, headers={"Authorization": f"Bearer {tok}"}, **kw)
    c = client()
    loadc = client()
    ref = {"lat": a.rtk_lat, "lon": a.rtk_lon}
    url = f"/telemetry/v1/flights/{a.session}"

    def mark(kind: str, at: float | None = None):
        c.post(f"/v1/ct-sessions/{a.session}/marks", json={"kind": kind, **({"at": iso_ms(at)} if at else {})}).raise_for_status()

    t0 = time.time()
    flag_at = {"c2_lost": t0 + a.flag1_s, "geofence_breach": t0 + a.flag2_s}
    load = (t0 + a.load_start_s, t0 + a.load_start_s + a.load_s)
    disc = (t0 + a.duration - a.disconnect_before_end_s, t0 + a.duration - a.disconnect_before_end_s + a.disconnect_s)
    for f, at in flag_at.items():  # cờ phải bật lúc đang gửi thời gian thực, không rơi vào đoạn ngắt hay đoạn tải
        if disc[0] - 3 < at < disc[1] or load[0] - 3 < at < load[1]:
            raise SystemExit(f"lịch thử chồng nhau: cờ {f} ở giây {at - t0:.0f} trùng đoạn ngắt/chạy tải")
    stats = {"sent": 0, "accepted": 0, "rejected": 0, "errors": 0, "backfilled": 0, "load_batches": 0, "load_throttled": 0}
    buffer: list[dict] = []
    stop_load = threading.Event()

    def flood():  # tải song song trên cùng đường truyền: lô 200 mẫu trùng, máy chủ khử trùng
        junk = [sample(a.session, a.uas_id, t0 + i / 10, ref, []) for i in range(200)]
        while not stop_load.is_set():
            try:
                r = loadc.post(f"{url}/backfill", json={"samples": junk})
                stats["load_batches"] += 1
                if r.status_code == 429:  # B1 giới hạn gửi bù: lùi theo Retry-After như bộ phát thật phải làm
                    stats["load_throttled"] += 1
                    stop_load.wait(float(r.headers.get("retry-after", "1")))
            except httpx.HTTPError:
                pass

    mark("static_start", t0)
    k, loaded, reconnected, backfill_after = 0, False, False, 0.0
    while True:
        t = t0 + k * 0.1
        if t > t0 + a.duration:
            break
        now = time.time()
        if t > now:
            time.sleep(t - now)
        if not loaded and t >= load[0]:
            mark("load_start", t)
            loaded = True
            th = [threading.Thread(target=flood, daemon=True) for _ in range(a.load_threads)]
            [x.start() for x in th]
        if loaded and not stop_load.is_set() and t >= load[1]:
            stop_load.set()
            mark("load_end", t)
        flags = [f for f, at in flag_at.items() if at <= t < at + 3]
        s = sample(a.session, a.uas_id, t, ref, flags)
        if disc[0] <= t < disc[1]:
            if not buffer:
                mark("disconnect_start", t)
            buffer.append(s)  # mất kết nối: giữ trong bộ đệm
        else:
            if buffer and not reconnected:
                c.close()
                c = client()  # nối lại: kết nối TLS mới, xác thực hai chiều lại
                mark("disconnect_end", disc[1])
                reconnected = True
            try:
                r = c.post(f"{url}/samples", json={"samples": [s]})
                stats["sent"] += 1
                stats["accepted"] += r.json().get("accepted", 0)
                stats["rejected"] += len(r.json().get("rejected", []))
            except (httpx.HTTPError, ValueError):
                stats["errors"] += 1
            if buffer and reconnected and time.time() >= backfill_after:  # gửi bù từng lô 200 mẫu xen giữa mẫu thời gian thực
                chunk = buffer[:200]
                try:
                    r = c.post(f"{url}/backfill", json={"samples": chunk})
                    if r.status_code == 429:
                        backfill_after = time.time() + float(r.headers.get("retry-after", "1"))
                    else:
                        r.raise_for_status()
                        stats["backfilled"] += r.json().get("accepted", 0)
                        buffer[:] = buffer[200:]
                except (httpx.HTTPError, ValueError):
                    stats["errors"] += 1
        k += 1
    mark("static_end", t0 + a.duration)
    return stats


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--uas-id", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--ca")
    p.add_argument("--rtk-lat", type=float, default=11.1347056)
    p.add_argument("--rtk-lon", type=float, default=106.4437417)
    p.add_argument("--duration", type=float, default=1860)
    p.add_argument("--flag1-s", type=float, default=60)
    p.add_argument("--flag2-s", type=float, default=90)
    p.add_argument("--load-start-s", type=float, default=300)
    p.add_argument("--load-s", type=float, default=300)
    p.add_argument("--load-threads", type=int, default=4)
    p.add_argument("--disconnect-s", type=float, default=60)
    p.add_argument("--disconnect-before-end-s", type=float, default=240)
    return p


if __name__ == "__main__":
    print(json.dumps(run(parser().parse_args()), ensure_ascii=False))
