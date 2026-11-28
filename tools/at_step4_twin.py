"""Chạy bước 4 trên bản sao số qua API của một B1 đang chạy: đo độ trễ cảnh báo, chạy CT-1…8 cho một khách.

Đặc tả B1 v0.1, Mục 10.2 bước 4: "Đo được độ trễ cảnh báo trên bản sao số; CT-1…8 chạy được cho khách". Kịch bản:

1. Chuẩn bị: một nhà khai thác hồ sơ đầy đủ, Khối 1 đã hiệp đồng và kích hoạt, ba chuyến của bản sao số đã được cho phép
   cất cánh, mỗi chuyến một kênh I-07: HTTPS, MQTT 5, MAVLink v2 trên UDP (có ký bản tin).
2. Độ trễ cảnh báo: bản sao số bay chuyến HTTPS theo đồng hồ thật 10 Hz kèm Remote ID, ADS-B, thời tiết, trạng thái 5 trạm;
   tiêm lỗi lặp lại (lệch ngoài vùng bình thường, vào vùng dự phòng, mất đường truyền, vượt trần, pin yếu, mất telemetry,
   trạm chạy 4G/5G). Một cán bộ mở luồng bản đồ trực tiếp như màn hình vận hành; đo từ giờ nguồn của mẫu gây cảnh báo đến
   lúc luồng nhận cảnh báo. Chuyến MQTT và MAVLink bật cờ an toàn để đo cùng cách.
3. CT-1…8: Kỹ thuật viên mở phiên chính thức; bộ phát của khách (tools/ct_client.py) gửi 31 phút qua cổng vào TLS 1.3 có
   xác thực hai chiều, có đoạn tải, đoạn ngắt 60 giây và gửi bù; Kỹ thuật viên chấm, kết quả ghi vào hồ sơ phương tiện.

    B1_DATABASE_URL=... B1_JWKS_FILE=.dev/jwks.json B1_OIDC_ISSUER=https://idp.b1.local/realms/b1 \\
      B1_MQTT_URL=mqtt://localhost:1883 B1_MAVLINK_UDP_PORT=14560 uvicorn --factory b1.app:create_app --port 8011
    python tools/at_step4_twin.py --base http://localhost:8011 --ingress https://localhost:8443
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import queue
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid

import httpx
import jwt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b1.identity.roles import Role  # noqa: E402
from b1.telemetry import mavlink as mv  # noqa: E402
from tools.at_step3_twin import B1, onboard  # noqa: E402
from tools.dev_token import AUDIENCE, ISSUER, KID, key  # noqa: E402
from twin import sim  # noqa: E402

CHECK = {k: {"ok": True} for k in ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")}
LIMIT_MS = 2000.0
RTK = {"lat": 11.1347056, "lon": 106.4437417}


def token(sub: str, mfa: bool = True, **extra) -> str:
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 3 * 3600, "auth_time": now - 10,
                       "amr": ["pwd", "otp"] if mfa else ["pwd"], **extra}, key(), algorithm="RS256", headers={"kid": KID})


def ts(s: str | None) -> float | None:
    return None if s is None else dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50_ms": round(statistics.median(xs), 1) if xs else None, "p95_ms": pct(xs, 95),
            "max_ms": round(max(xs), 1) if xs else None}


class LiveListener(threading.Thread):
    """Đọc /v1/live/stream như màn hình vận hành; ghi giờ nhận (đồng hồ hệ thống) của từng cảnh báo và ảnh chụp."""

    def __init__(self, base: str, sub: str):
        super().__init__(daemon=True)
        self.base, self.sub, self.alerts, self.snaps, self.ready = base, sub, [], [], threading.Event()

    def run(self):
        with httpx.stream("GET", f"{self.base}/v1/live/stream", headers={"Authorization": f"Bearer {token(self.sub)}"},
                          timeout=httpx.Timeout(10, read=None)) as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: "):
                    now = time.time()
                    if ev == "hello":
                        self.ready.set()
                    elif ev == "alert":
                        self.alerts.append((now, json.loads(line[6:])))
                    elif ev == "live":
                        d = json.loads(line[6:])
                        self.snaps.append((now, len(d["flights"]), len(d["adsb"]), len(d["rid"])))


class Feeder:
    """Nhận dòng JSONL của bản sao số, gửi I-07 (một luồng, giữ thứ tự) và I-06 (một luồng mỗi trạm) theo thời gian thực."""

    def __init__(self, b1: B1, fid: str, cust_tok: str, station_tok: dict[str, str], drop: tuple[float, float], t0: float):
        self.b1, self.fid, self.drop, self.t0 = b1, fid, drop, t0
        self.h_cust = {"Authorization": f"Bearer {cust_tok}"}
        self.h_st = {k: {"Authorization": f"Bearer {v}"} for k, v in station_tok.items()}
        self.q_tel: queue.Queue = queue.Queue()
        self.q_st = {k: queue.Queue() for k in station_tok}
        self.stats = {"i07_sent": 0, "i07_accepted": 0, "i07_rejected": 0, "i07_dropped_on_purpose": 0, "i06_sent": 0, "i06_accepted": 0}
        self.workers = [threading.Thread(target=self._tel, daemon=True)] + [
            threading.Thread(target=self._st, args=(k,), daemon=True) for k in station_tok]
        [w.start() for w in self.workers]

    def write(self, line: str):
        for ln in line.splitlines():
            if not ln.strip():
                continue
            x = json.loads(ln)
            if x["interface"] == "I-07":
                t = ts(x["payload"]["timeSource"]) - self.t0
                if self.drop[0] <= t < self.drop[1]:
                    self.stats["i07_dropped_on_purpose"] += 1
                    continue
                self.q_tel.put(x["payload"])
            else:
                self.q_st[x["stationId"]].put(x["payload"])

    def flush(self):
        pass

    def _tel(self):
        c = httpx.Client(base_url=self.b1.base, timeout=10, headers=self.h_cust)
        while True:
            s = self.q_tel.get()
            if s is None:
                return
            r = c.post(f"/telemetry/v1/flights/{self.fid}/samples", json={"samples": [s]})
            self.stats["i07_sent"] += 1
            if r.status_code == 202:
                self.stats["i07_accepted"] += r.json()["accepted"]
                self.stats["i07_rejected"] += len(r.json()["rejected"])

    def _st(self, sid: str):
        c = httpx.Client(base_url=self.b1.base, timeout=10, headers=self.h_st[sid])
        while True:
            rec = self.q_st[sid].get()
            if rec is None:
                return
            r = c.post(f"/ris/v1/stations/{sid}/records", json={"records": [rec]})
            self.stats["i06_sent"] += 1
            if r.status_code == 202:
                self.stats["i06_accepted"] += r.json()["accepted"]

    def close(self):
        self.q_tel.put(None)
        for q in self.q_st.values():
            q.put(None)
        for w in self.workers:
            w.join(timeout=30)


def simple_sample(fid: str, uas: str, t: float, flags: list[str], km: float) -> dict:
    from tests.conftest import corridor_point
    lon, lat = corridor_point(km)
    return {"flightId": fid, "uasId": uas, "timeSource": dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"), "position": {"lat": round(lat, 7), "lon": round(lon, 7)},
            "altitude": {"value": 100.0, "reference": "AGL"}, "velocity": {"groundSpeed": 12.0, "verticalSpeed": 0.0, "track": 318.0},
            "gnss": {"fixType": "3d", "satellites": 15, "hdop": 0.9}, "energy": {"batteryPercent": 70.0, "voltage": 47.0},
            "flightMode": {"mode": "AUTO", "autonomy": "automatic"}, "armed": True, "safetyFlags": flags,
            "c2Link": {"state": "lost" if "c2_lost" in flags else "ok"}}


def mqtt_flight(fid: str, uas: str, host: str, port: int, seconds: float, flag_at: list[float]) -> dict:
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"kh-at4-{uuid.uuid4().hex[:6]}", protocol=mqtt.MQTTv5)
    c.connect(host, port)
    c.loop_start()
    t0, n = time.time(), 0
    while time.time() < t0 + seconds:
        t = t0 + n * 0.1
        if t > time.time():
            time.sleep(t - time.time())
        flags = ["geofence_breach"] if any(a <= t - t0 < a + 3 for a in flag_at) else []
        c.publish(f"b1/v1/telemetry/{fid}", json.dumps(simple_sample(fid, uas, t, flags, 1.0 + n * 0.0012)), qos=1)
        n += 1
    time.sleep(0.5)
    c.loop_stop()
    c.disconnect()
    return {"published": n}


def mavlink_flight(fid: str, host: str, port: int, sysid: int, sign_key: bytes, seconds: float, rtl_at: list[float]) -> dict:
    from tests.conftest import corridor_point
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0, n, sts = time.time(), 0, 1_000_000
    boot0 = 120_000
    while time.time() < t0 + seconds:
        t = t0 + n * 0.1
        if t > time.time():
            time.sleep(t - time.time())
        boot = boot0 + int((t - t0) * 1000)
        rtl = any(a <= t - t0 < a + 3 for a in rtl_at)
        lon, lat = corridor_point(2.0 + n * 0.0012)
        frames = []
        if n % 10 == 0:
            frames.append((0, (6 if rtl else 3, 2, 3, 128 | 4 | 1, 4, 3)))
            frames.append((2, (int(t * 1e6), boot)))
            frames.append((1, (1, 1, 1, 300, 47000, 120, 0, 0, 0, 0, 0, 0, 68)))
            frames.append((24, (int(t * 1e6), int(lat * 1e7), int(lon * 1e7), 125000, 80, 110, 1200, 31800, 3, 17)))
        frames.append((33, (boot, int(lat * 1e7), int(lon * 1e7), 125000, 100000, -800, 900, 0, 31800)))
        buf = b""
        for msgid, vals in frames:
            sts += 1
            buf += mv.encode(msgid, vals, sysid=sysid, seq=n, key=sign_key, link_id=1, sign_timestamp=sts)
        sock.sendto(buf, (host, port))
        n += 1
    return {"datagrams": n}


def log(*x):
    print(dt.datetime.now().strftime("%H:%M:%S"), *x, file=sys.stderr, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8011")
    ap.add_argument("--ingress", default="https://localhost:8443")
    ap.add_argument("--certs", default=".dev/ingress")
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--ct-duration", type=float, default=1860)
    ap.add_argument("--mqtt", default="localhost:1883")
    ap.add_argument("--mavlink", default="127.0.0.1:14560")
    ap.add_argument("--ui", action="store_true", help="đo thêm trên trang /ops/live bằng Playwright")
    ap.add_argument("--out", default="docs/acceptance/step4")
    a = ap.parse_args()
    tag = uuid.uuid4().hex[:6]
    b1 = B1(a.base)
    t_start = dt.datetime.now(dt.timezone.utc)

    # 1. Chuẩn bị.
    admin = f"at4-admin-{tag}"
    subprocess.run([sys.executable, "-m", "b1.admin", "bootstrap", "--issuer", ISSUER, "--subject", admin, "--name", "Quản trị AT-4"],
                   check=True, cwd=ROOT, env={**os.environ}, capture_output=True)
    corridor = b1.ok("POST", "/v1/organizations", admin, json={"name": f"Hành lang bước 4 {tag}", "kind": "corridor"}).json()["id"]
    staff = {}
    for role in (Role.CORRIDOR_HEAD, Role.DUTY_SAFETY_OFFICER, Role.FLIGHT_TEST_TECHNICIAN, Role.AUDITOR):
        sub = f"at4-{role.value}-{tag}"
        b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": sub, "display_name": role.value, "org_id": corridor,
                                                 "roles": [role.value]})
        staff[role] = sub
    head, dso, tech, aud = (staff[r] for r in (Role.CORRIDOR_HEAD, Role.DUTY_SAFETY_OFFICER, Role.FLIGHT_TEST_TECHNICIAN, Role.AUDITOR))
    st_tok = {}
    for sid in ("RIS-1", "RIS-2", "RIS-3", "RIS-4", "RIS-5"):
        sub = f"at4-{sid.lower()}-{tag}"
        b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": sub, "display_name": f"Máy tính trạm {sid}",
                                                 "org_id": corridor, "roles": ["ris_station"]})
        st_tok[sid] = token(sub, mfa=False, station_id=sid)
    o = onboard(b1, admin, head, tech, tag)
    cust_tok = token(o["customer"], scope="telemetry:write")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    live = [x for x in b1.ok("GET", "/v1/airspace/activations", head).json()["items"] if x["block_id"] == "block-1"
            and x["state"] == "active" and ts(x["ends_at"]) > time.time() + 2.5 * 3600]
    if not live:  # chạy lại trên cùng CSDL thì dùng lại kế hoạch đang kích hoạt
        act = b1.ok("POST", "/v1/airspace/activations", head, json={"block_id": "block-1", "starts_at": (now - dt.timedelta(minutes=5)).isoformat(),
                                                                    "ends_at": (now + dt.timedelta(hours=3)).isoformat()}).json()
        sha = b1.ok("GET", f"/v1/airspace/activations/{act['id']}/package", head).headers["X-Package-SHA256"]
        b1.ok("POST", f"/v1/airspace/activations/{act['id']}/coordination", head, json={
            "result": "approved", "package_sha256": sha, "reference": f"CV-AT4-{tag}", "counterpart": "Bản sao số (giả lập I-05)"})
        b1.ok("POST", f"/v1/airspace/activations/{act['id']}/activate", head)

    def inject(uas: str) -> str:
        out = subprocess.run([sys.executable, "-m", "b1.admin", "twin-flight", "--uas-id", uas, "--customer-id", o["customer_id"],
                              "--block", "block-1", "--start-in-min", "2", "--minutes", "120"], check=True, cwd=ROOT,
                             env={**os.environ, "B1_ALLOW_TWIN_TOOLS": "1"}, capture_output=True, text=True)
        fid = out.stdout.strip().splitlines()[-1]
        b1.ok("POST", f"/v1/flights/{fid}/takeoff-clearance", dso, json={**CHECK, "confirm": True})
        return fid

    uas_https, uas_mqtt, uas_mav = o["uas_id"], f"SIM0002MQ{tag.upper()}", f"SIM0003MV{tag.upper()}"
    f_https, f_mqtt, f_mav = inject(uas_https), inject(uas_mqtt), inject(uas_mav)
    channel_of = {f_https: "https", f_mqtt: "mqtt", f_mav: "mavlink"}
    sign_key = os.urandom(32)
    b1.c.post(f"/telemetry/v1/flights/{f_mav}/mavlink", headers={"Authorization": f"Bearer {cust_tok}"},
              json={"sysid": 31, "signing_key_hex": sign_key.hex()}).raise_for_status()

    log("chuẩn bị xong")
    # 3. Phiên CT chính thức, chạy song song (31 phút).
    ct = b1.ok("POST", "/v1/ct-sessions", tech, json={"aircraft_id": o["aircraft_id"], "official": True, "rtk_ref": RTK}).json()
    tokf = ROOT / ".dev" / f"at4-cust-{tag}.tok"
    tokf.write_text(cust_tok)
    certs = ROOT / a.certs
    ct_proc = subprocess.Popen([sys.executable, "tools/ct_client.py", "--base", a.ingress, "--session", ct["id"], "--uas-id", o["uas_id"],
                                "--token-file", str(tokf), "--cert", str(certs / "client.pem"), "--key", str(certs / "client.key"),
                                "--ca", str(certs / "ca.pem"), "--duration", str(a.ct_duration)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # 2. Độ trễ cảnh báo.
    lis = LiveListener(a.base, dso)
    lis.start()
    assert lis.ready.wait(10), "không nối được luồng bản đồ trực tiếp"
    ui_proc = None
    if a.ui:
        (ROOT / ".dev" / "at4-ui.tok").write_text(token(dso))
        ui_proc = subprocess.Popen(["node", "tools/at_step4_ui.js", a.base, str(ROOT / ".dev" / "at4-ui.tok"), str(int(a.duration - 20)),
                                    str(ROOT / "docs/img/ops-live.png")], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(8)
    faults, windows = [], []
    for c in range(20, int(a.duration) - 40, 30):
        faults += [f"offset:{c}:{c + 5}:600", f"offset:{c + 5}:{c + 10}:800", f"c2_lost:{c + 15}:{c + 17}", f"climb:{c + 20}:{c + 23}:50"]
        windows += [("LATERAL_DEVIATION", c, c + 10), ("C2_LINK", c + 15, c + 17), ("SAFETY_FLAG", c + 15, c + 17),
                    ("CEILING", c + 20, c + 23)]
    lowb = (a.duration - 30, a.duration - 20)
    faults += [f"low_battery:{lowb[0]:.0f}:{lowb[1]:.0f}", "backhaul_cellular:100:140:1"]
    windows += [("LOW_BATTERY", *lowb), ("SAFETY_FLAG", *lowb)]
    drop = (a.duration - 12, a.duration - 8)
    windows += [("TELEMETRY_STALE", drop[0], drop[1] + 1)]
    t0 = time.time()
    feeder = Feeder(b1, f_https, cust_tok, st_tok, drop, t0)
    sim_args = sim.parser().parse_args(["--duration", str(a.duration), "--rate", "10", "--start-km", "3", "--speed", "25",
                                        "--alt", "120", "--realtime", "--adsb", "--flight-id", f_https, "--uas-id", uas_https,
                                        "--start", dt.datetime.fromtimestamp(t0, dt.timezone.utc).isoformat()]
                                       + sum((["--inject", f] for f in faults), []))
    mq_host, mq_port = a.mqtt.split(":")
    mav_host, mav_port = a.mavlink.split(":")
    side = {}
    th_mq = threading.Thread(target=lambda: side.update(mqtt=mqtt_flight(f_mqtt, uas_mqtt, mq_host, int(mq_port), 60, [20, 40])))
    th_mv = threading.Thread(target=lambda: side.update(mavlink=mavlink_flight(f_mav, mav_host, int(mav_port), 31, sign_key, 60, [25, 45])))
    th_mq.start()
    th_mv.start()
    log("bắt đầu bay")
    sim.run(sim_args, out=feeder)
    log("bay xong")
    feeder.close()
    th_mq.join()
    th_mv.join()
    time.sleep(3)

    # Kết quả độ trễ.
    raised = [(t, e) for t, e in lis.alerts if e["kind"] in ("alert.raised", "alert.escalated")]
    by_channel: dict[str, list[float]] = {}
    from_receive, kinds_seen, unexpected = [], {}, []
    for t, e in raised:
        al = e["alert"]
        if al["kind"] == "HANDOVER":
            continue
        kinds_seen.setdefault((channel_of.get(al["flight_id"], "station" if al["kind"] == "STATION" else "other"), al["kind"]), 0)
        kinds_seen[(channel_of.get(al["flight_id"], "station" if al["kind"] == "STATION" else "other"), al["kind"])] += 1
        if al["t_source"] and al["flight_id"] in channel_of:
            by_channel.setdefault(channel_of[al["flight_id"]], []).append((t - ts(al["t_source"])) * 1000)
        if al["t_receive"]:
            from_receive.append((t - ts(al["t_receive"])) * 1000)
        if al["flight_id"] == f_https and al["t_source"]:
            rel = ts(al["t_source"]) - t0
            if not any(k == al["kind"] and s - 0.2 <= rel <= e2 + 1.0 for k, s, e2 in windows):
                unexpected.append({"kind": al["kind"], "t_rel_s": round(rel, 1), "message": al["message"]})
    all_src = sum(by_channel.values(), [])
    latency = {"from_source": summary(all_src), "from_b1_receive": summary(from_receive),
               "by_channel": {k: summary(v) for k, v in by_channel.items()}, "limit_ms": LIMIT_MS,
               "pass": bool(all_src) and pct(all_src, 95) <= LIMIT_MS}
    snaps = lis.snaps
    live_hz = round(len(snaps) / (snaps[-1][0] - snaps[0][0]), 2) if len(snaps) > 1 else None

    tel = b1.ok("GET", f"/v1/flights/{f_https}/telemetry", aud).json()["items"]
    ingest = [(ts(x["t_ingest"]) - ts(x["t_source"])) * 1000 for x in tel]
    flight_states = {ch: b1.ok("GET", f"/v1/flights/{fid}", aud).json()["state"] for fid, ch in channel_of.items()}
    stored = {ch: b1.ok("GET", f"/v1/flights/{fid}/telemetry", aud).json()["count"] for fid, ch in channel_of.items()}
    rid = b1.call("GET", f"/v1/surveillance/flights/{f_https}/rid-reconciliation", aud).json()
    tl = b1.ok("GET", f"/v1/flights/{f_https}/timeline", aud).json()["counts"]
    tracks = b1.ok("GET", "/v1/surveillance/tracks", aud).json()
    adsb = [x for x in tracks["adsb"] if x["key"] == "888123"]
    stations = {s["station_id"]: s["level"] for s in b1.ok("GET", "/v1/surveillance/stations", aud).json()["items"]}
    ui = None
    if ui_proc:
        out, err = ui_proc.communicate(timeout=120)
        try:
            ui = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            ui = {"error": (err or out)[-500:]}

    log("chờ phiên CT")
    # CT-1…8.
    ct_out, ct_err = ct_proc.communicate(timeout=a.ct_duration + 600)
    client_stats = json.loads(ct_out.strip().splitlines()[-1]) if ct_out.strip() else {"error": ct_err[-500:]}
    log("chấm CT", client_stats)
    ev = b1.ok("POST", f"/v1/ct-sessions/{ct['id']}/evaluate", tech).json()
    recorded = [x for x in b1.ok("GET", f"/v1/registry/aircraft/{o['aircraft_id']}/conformance", aud).json().get("items", [])
                if x.get("report_ref") == f"ct_session:{ct['id']}"]
    audit = subprocess.run([sys.executable, "-m", "b1.admin", "verify-audit"], cwd=ROOT, env={**os.environ}, capture_output=True, text=True)

    result = {
        "run": {"started_at": t_start.isoformat(), "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), "base": a.base,
                "duration_s": a.duration, "ct_duration_s": a.ct_duration},
        "flights": {"https": f_https, "mqtt": f_mqtt, "mavlink": f_mav}, "flight_states": flight_states, "stored_samples": stored,
        "feeder": feeder.stats, "side_channels": side, "faults": faults,
        "alert_latency": latency, "alerts_by_channel_kind": {f"{k[0]}:{k[1]}": v for k, v in sorted(kinds_seen.items())},
        "unexpected_alerts": unexpected, "live_stream_hz": live_hz, "ui": ui,
        "telemetry_to_db_ms": summary(ingest), "rid_reconciliation": rid, "timeline_counts": tl,
        "adsb_track": adsb[0] if adsb else None, "station_levels_end": stations,
        "ct_session": {"id": ct["id"], "client": client_stats, "results": ev["results"], "recorded": len(recorded),
                       "transport": ev["params"].get("transport")},
        "audit_chain": audit.stdout.strip()[-300:],
    }
    out = ROOT / a.out
    out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"latency": latency, "ct": {k: v["passed"] for k, v in ev["results"].items()}, "unexpected": len(unexpected)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
