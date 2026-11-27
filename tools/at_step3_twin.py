"""Chạy AT-03 (kích hoạt khối) trên bản sao số qua API HTTP của một B1 đang chạy; kèm kiểm F-04 và rào địa lý.

AT-03 (Đặc tả B1 v0.1, Mục 9): "Trạng thái ≤5 giây; gói dữ liệu đúng định dạng". Kịch bản:

1. Một nhà khai thác hồ sơ đầy đủ đặt lịch Khối 1 ngày mai; kỳ xếp lịch xếp chuyến.
2. Gói dữ liệu: Trưởng bộ phận lập kế hoạch kích hoạt theo lịch ngày mai; tải gói văn bản, GeoJSON, ED-269; kiểm từng dạng
   (trường bắt buộc, vòng đa giác đóng và đúng chiều RFC 7946, khớp hình học khối, ED-269 qua lược đồ kiểm tra, ba dạng cùng
   mã băm); ghi hiệp đồng gắn mã băm.
3. Trạng thái ≤5 giây: một cán bộ khác mở luồng sự kiện (SSE) như bản đồ; Trưởng bộ phận lặp N lần lập → hiệp đồng →
   kích hoạt → đóng Khối 1 trong khung giờ hiện tại; đo từ lúc gửi lệnh đến lúc luồng nhận sự kiện của đúng kế hoạch.
4. F-04 trên bản sao số: chèn một chuyến của bản sao số bắt đầu sau 10 phút (`b1.admin twin-flight`, vì xếp lịch thật chỉ
   nhận khung từ ngày mai); thử cho phép cất cánh khi danh mục chưa đạt, khi Trưởng bộ phận chưa vào ca, rồi thành công;
   kiểm chữ ký bằng khoá công khai của B1; đóng khối khi chuyến đã được cho phép phải bị từ chối.
5. Phương tiện của bản sao số bay hết Khối 1; mọi mẫu telemetry phải nằm trong rào địa lý của khối đang kích hoạt và
   trong tầng độ cao; kiểm chuỗi nhật ký.

    B1_DATABASE_URL=... B1_JWKS_FILE=.dev/jwks.json B1_OIDC_ISSUER=https://idp.b1.local/realms/b1 \\
        uvicorn --factory b1.app:create_app --port 8011
    B1_DATABASE_URL=... python tools/at_step3_twin.py --base http://localhost:8011
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time
import uuid

import httpx
import jsonschema
import jwt
from pyproj import Transformer
from shapely.geometry import Point, shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b1.identity.roles import Role  # noqa: E402
from tools.dev_token import AUDIENCE, ISSUER, KID, key  # noqa: E402
from twin import sim  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=7))
ED269 = json.loads((ROOT / "api/schemas/ed269-uaszone.schema.json").read_text(encoding="utf-8"))
CHECK = {k: {"ok": True} for k in ("weather", "ris_stations", "network", "conformance_monitoring", "emergency_plan")}
LIMIT_S = 5.0


def token(sub: str, mfa: bool = True) -> str:
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 3600, "auth_time": now - 10,
                       "amr": ["pwd", "otp"] if mfa else ["pwd"]}, key(), algorithm="RS256", headers={"kid": KID})


class B1:
    def __init__(self, base: str):
        self.base = base
        self.c = httpx.Client(base_url=base, timeout=30)

    def call(self, method: str, path: str, sub: str, mfa: bool = True, **kw) -> httpx.Response:
        return self.c.request(method, path, headers={"Authorization": f"Bearer {token(sub, mfa)}"}, **kw)

    def ok(self, method: str, path: str, sub: str, **kw):
        r = self.call(method, path, sub, **kw)
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:400]}")
        return r


class Listener(threading.Thread):
    """Đọc luồng SSE như bản đồ, ghi thời điểm nhận từng sự kiện theo đồng hồ đơn điệu."""

    def __init__(self, base: str, sub: str):
        super().__init__(daemon=True)
        self.base, self.sub, self.events, self.ready = base, sub, [], threading.Event()

    def run(self):
        with httpx.stream("GET", f"{self.base}/v1/airspace/stream", headers={"Authorization": f"Bearer {token(self.sub)}"},
                          timeout=httpx.Timeout(10, read=None)) as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: "):
                    if ev == "hello":
                        self.ready.set()
                    elif ev == "airspace":
                        self.events.append((time.monotonic(), json.loads(line[6:])))

    def wait_for(self, activation_id: str, kind: str, since: float, timeout: float = 15) -> float | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for t, e in list(self.events):
                if t >= since and e["kind"] == kind and e["payload"].get("id") == activation_id:
                    return t
            time.sleep(0.01)
        return None


def ccw(ring) -> bool:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) > 0


def onboard(b1: B1, admin: str, head: str, tech: str, tag: str) -> dict:
    org = b1.ok("POST", "/v1/organizations", admin, json={"name": f"NKT AT-03 {tag}", "kind": "customer"}).json()["id"]
    cust = f"at3-kh-{tag}"
    b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": cust, "display_name": "Nhà khai thác AT-03", "org_id": org,
                                             "roles": ["customer_operator"]})
    cid = b1.ok("POST", "/v1/registry/customers", head, json={"org_id": org, "name": f"Nhà khai thác AT-03 {tag}", "tier": "B"}).json()["id"]
    uas = f"SIM0001B1TN{tag.upper()}"
    aid = b1.ok("POST", "/v1/registry/aircraft", cust, json={
        "uas_id": uas, "vn_registration_no": f"VN-AT3-{tag}", "model": "phương tiện mô phỏng của bản sao số", "mtow_kg": 25,
        "max_speed_ms": 30, "in_testing": True, "flight_mgmt_device_id": f"QLHD-AT3-{tag}", "config_ref": "fw-1.0"}).json()["id"]
    pid = b1.ok("POST", "/v1/registry/pilots", cust, json={"full_name": "Người điều khiển AT-03"}).json()["id"]
    for kind, (otype, oid) in {"registration_certificate": ("aircraft", aid), "technical_status_certificate": ("aircraft", aid),
                               "pilot_licence": ("pilot", pid), "test_permit": ("customer", cid), "insurance": ("customer", cid)}.items():
        d = b1.ok("POST", "/v1/registry/documents", cust, json={"owner_type": otype, "owner_id": oid, "kind": kind, "number": f"{kind[:3]}-1",
                                                                 "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2028-12-31T23:59:59Z"}).json()
        b1.ok("POST", f"/v1/registry/documents/{d['id']}/verify", head, json={"decision": "verified"})
    b1.ok("POST", f"/v1/registry/aircraft/{aid}/verify-device", head)
    for i in range(1, 9):
        b1.ok("POST", f"/v1/registry/aircraft/{aid}/conformance", tech, json={"test": f"CT-{i}", "passed": True,
                                                                              "tested_at": "2026-11-01T00:00:00Z"})
    return {"customer": cust, "customer_id": cid, "aircraft_id": aid, "pilot_id": pid, "uas_id": uas}


def check_package(b1: B1, head: str, act: dict, block: dict) -> dict:
    url = f"/v1/airspace/activations/{act['id']}/package"
    text_r = b1.ok("GET", url, head)
    gj_r = b1.ok("GET", url, head, params={"format": "geojson"})
    ed_r = b1.ok("GET", url, head, params={"format": "ed269"})
    text, gj, ed = text_r.text, gj_r.json(), ed_r.json()
    shas = {text_r.headers["X-Package-SHA256"], gj_r.headers["X-Package-SHA256"], ed_r.headers["X-Package-SHA256"]}
    ring = gj["features"][0]["geometry"]["coordinates"][0]
    block_ring = block["geometry"]["coordinates"][0]
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk("ba dạng cùng mã băm", len(shas) == 1, next(iter(shas)) if len(shas) == 1 else str(shas))
    sha = next(iter(shas))
    st = dt.datetime.fromisoformat(act["starts_at"]).astimezone(TZ)
    for f in act["flights"]:
        chk(f"văn bản có chuyến {f['queue_no']}", f["queue_no"] in text and f["uas_id"] in text)
    chk("văn bản có mã khối, tầng, khung giờ, mã băm", all(s in text for s in ("Khối 1", "block-1", "từ 20 m đến 150 m so với mặt đất", f"{st:%H:%M} ngày {st:%d/%m/%Y}", sha)))
    chk("văn bản có đủ toạ độ đỉnh WGS84", text.count("Đỉnh ") == len(block_ring) - 1)
    chk("GeoJSON: vòng đóng", ring[0] == ring[-1])
    chk("GeoJSON: vòng ngoài ngược chiều kim đồng hồ (RFC 7946)", ccw(ring))
    chk("GeoJSON: khớp hình học khối", shape({"type": "Polygon", "coordinates": [ring]}).equals(shape(block["geometry"])))
    props = gj["features"][0]["properties"]
    chk("GeoJSON: có tầng, khung giờ, chuyến", props["floor"]["value"] == 20 and props["ceiling"]["value"] == 150
        and props["start"] == act["starts_at"].replace("+00:00", "Z") and len(props["flights"]) == len(act["flights"]))
    try:
        jsonschema.validate(ed, ED269)
        chk("ED-269: qua lược đồ kiểm tra", True)
    except jsonschema.ValidationError as e:
        chk("ED-269: qua lược đồ kiểm tra", False, e.message)
    z = ed["features"][0]
    chk("ED-269: cùng đa giác và tầng", z["geometry"][0]["horizontalProjection"]["coordinates"][0] == ring
        and (z["geometry"][0]["lowerLimit"], z["geometry"][0]["upperLimit"]) == (20, 150))
    return {"sha256": sha, "checks": checks, "pass": all(c["ok"] for c in checks), "text": text,
            "geojson_bytes": len(gj_r.content), "ed269_bytes": len(ed_r.content), "ed269_identifier": z["identifier"]}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8011")
    ap.add_argument("--out", default="docs/acceptance/step3-at03")
    ap.add_argument("--cycles", type=int, default=20)
    a = ap.parse_args()
    tag = uuid.uuid4().hex[:6]
    b1 = B1(a.base)

    admin = f"at3-admin-{tag}"
    subprocess.run([sys.executable, "-m", "b1.admin", "bootstrap", "--issuer", ISSUER, "--subject", admin, "--name", "Quản trị AT-03"],
                   check=True, cwd=ROOT, env={**os.environ}, capture_output=True)
    corridor = b1.ok("POST", "/v1/organizations", admin, json={"name": f"Hành lang AT-03 {tag}", "kind": "corridor"}).json()["id"]
    staff = {}
    for role in (Role.CORRIDOR_HEAD, Role.DUTY_SAFETY_OFFICER, Role.FLIGHT_TEST_TECHNICIAN, Role.AUDITOR, Role.LOW_ALTITUDE_COORDINATION):
        sub = f"at3-{role.value}-{tag}"
        staff[role] = (sub, b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": sub, "display_name": role.value,
                                                                     "org_id": corridor, "roles": [role.value]}).json()["id"])
    head, head_id = staff[Role.CORRIDOR_HEAD]
    dso = staff[Role.DUTY_SAFETY_OFFICER][0]
    aud = staff[Role.AUDITOR][0]
    viewer = staff[Role.LOW_ALTITUDE_COORDINATION][0]

    # 1. Chuyến đã xếp lịch ngày mai.
    o = onboard(b1, admin, head, staff[Role.FLIGHT_TEST_TECHNICIAN][0], tag)
    tomorrow = (dt.datetime.now(TZ) + dt.timedelta(days=1)).date()
    req = b1.ok("POST", "/v1/scheduling/requests", o["customer"], json={
        "aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], "block_ids": ["block-1"], "bvlos": True,
        "items": [{"day": str(tomorrow), "slot": 1}]}).json()
    b1.ok("POST", "/v1/scheduling/runs", head)
    req = b1.ok("GET", f"/v1/scheduling/requests/{req['id']}", o["customer"]).json()
    assert req["items"][0]["status"] == "scheduled", req

    # 2. Gói dữ liệu.
    blocks = {b["id"]: b for b in b1.ok("GET", "/v1/airspace/blocks", head).json()["items"]}
    plan = b1.ok("POST", "/v1/airspace/activations/plan", head, json={"day": str(tomorrow)}).json()
    act = next(x for x in plan["created"] if x["block_id"] == "block-1" and any(f["queue_no"] == req["queue_no"] for f in x["flights"]))
    pk = check_package(b1, head, act, blocks["block-1"])
    coord = b1.ok("POST", f"/v1/airspace/activations/{act['id']}/coordination", head, json={
        "result": "approved", "package_sha256": pk["sha256"], "reference": f"CV-AT03-{tag}", "counterpart": "Bản sao số (giả lập I-05)"}).json()
    early = b1.call("POST", f"/v1/airspace/activations/{act['id']}/activate", head)
    package = {"activation_id": act["id"], "flights": len(act["flights"]), **pk, "coordinated": coord["state"] == "coordinated",
               "activate_before_window": {"http": early.status_code, "detail": early.json().get("detail")}}

    # 3. Trạng thái ≤5 giây.
    lis = Listener(a.base, viewer)
    lis.start()
    assert lis.ready.wait(10), "không nối được luồng sự kiện"
    cycles = []
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    win = (now - dt.timedelta(minutes=5), now + dt.timedelta(hours=2))
    for i in range(a.cycles):
        x = b1.ok("POST", "/v1/airspace/activations", head, json={"block_id": "block-1", "starts_at": win[0].isoformat(),
                                                                  "ends_at": win[1].isoformat()}).json()
        sha = b1.ok("GET", f"/v1/airspace/activations/{x['id']}/package", head).headers["X-Package-SHA256"]
        b1.ok("POST", f"/v1/airspace/activations/{x['id']}/coordination", head, json={
            "result": "approved", "package_sha256": sha, "reference": f"CV-AT03-{tag}-{i}", "counterpart": "Bản sao số"})
        t0 = time.monotonic()
        b1.ok("POST", f"/v1/airspace/activations/{x['id']}/activate", head)
        t_api = time.monotonic()
        t_ev = lis.wait_for(x["id"], "activation.active", t0)
        st = {b["id"]: b["status"] for b in b1.ok("GET", "/v1/airspace/state", viewer).json()["blocks"]}
        rec = {"cycle": i + 1, "activation_id": x["id"], "activate_api_ms": round((t_api - t0) * 1000, 1),
               "activate_to_map_ms": round((t_ev - t0) * 1000, 1) if t_ev else None, "state_after_activate": st["block-1"]}
        if i < a.cycles - 1:  # lần cuối để khối mở cho bước 4
            t1 = time.monotonic()
            b1.ok("POST", f"/v1/airspace/activations/{x['id']}/close", head)
            t_ev2 = lis.wait_for(x["id"], "activation.closed", t1)
            rec["close_to_map_ms"] = round((t_ev2 - t1) * 1000, 1) if t_ev2 else None
        else:
            live = x["id"]
        cycles.append(rec)
    to_map = [c["activate_to_map_ms"] for c in cycles if c["activate_to_map_ms"] is not None] + \
             [c["close_to_map_ms"] for c in cycles if c.get("close_to_map_ms") is not None]
    missing = sum(c["activate_to_map_ms"] is None for c in cycles) + sum("close_to_map_ms" in c and c["close_to_map_ms"] is None for c in cycles)
    latency = {"samples": len(to_map), "missing": missing, "p50_ms": statistics.median(to_map), "p95_ms": pct(to_map, 95),
               "max_ms": max(to_map), "limit_ms": LIMIT_S * 1000,
               "pass": missing == 0 and max(to_map) <= LIMIT_S * 1000 and all(c["state_after_activate"] == "active" for c in cycles)}
    geofence = b1.ok("GET", "/v1/airspace/geofence", o["customer"]).json()

    # 4. F-04 trên bản sao số.
    def inject() -> str:
        out = subprocess.run([sys.executable, "-m", "b1.admin", "twin-flight", "--uas-id", o["uas_id"], "--customer-id", o["customer_id"],
                              "--block", "block-1", "--start-in-min", "10", "--minutes", "20"], check=True, cwd=ROOT,
                             env={**os.environ, "B1_ALLOW_TWIN_TOOLS": "1"}, capture_output=True, text=True)
        return out.stdout.strip().splitlines()[-1]

    f1, f2 = inject(), inject()
    att = []

    def clear(fid, sub, body, label):
        r = b1.call("POST", f"/v1/flights/{fid}/takeoff-clearance", sub, json=body)
        d = r.json()
        att.append({"case": label, "http": r.status_code,
                    "failed": [x["code"] for x in d["detail"]["failed"]] if r.status_code == 409 and isinstance(d.get("detail"), dict) else [],
                    "state": d.get("state")})
        return r

    clear(f1, dso, {**CHECK, "weather": {"ok": False, "note": "gió giật vượt giới hạn"}, "confirm": True}, "Cán bộ trực ban, thời tiết chưa đạt")
    clear(f2, head, {**CHECK, "confirm": True}, "Trưởng bộ phận chưa vào ca")
    ok1 = clear(f1, dso, {**CHECK, "confirm": True}, "Cán bộ trực ban, đủ danh mục")
    now = dt.datetime.now(TZ)
    b1.ok("POST", "/v1/duty/shifts", head, json={"user_id": head_id, "role": "corridor_head", "starts_at": (now - dt.timedelta(minutes=1)).isoformat(),
                                                 "ends_at": (now + dt.timedelta(hours=4)).isoformat()})
    clear(f2, head, {**CHECK, "confirm": True}, "Trưởng bộ phận đã vào ca")
    rec = b1.ok("GET", f"/v1/flights/{f1}/clearance", aud).json()
    jwks = httpx.get(f"{a.base}/v1/signing-keys").json()
    claims = jwt.decode(rec["signature"], jwt.PyJWK(jwks["keys"][0]).key, algorithms=["ES256"])
    close_try = b1.call("POST", f"/v1/airspace/activations/{live}/close", head)
    expected = [409, 409, 200, 200]
    f04 = {"attempts": att, "expected_http": expected, "signature_valid_server": rec["signature_valid"],
           "signature_valid_external": claims["sha256"] == rec["sha256"], "signer_roles": rec["record"]["signer"]["roles"],
           "close_with_cleared_flight": {"http": close_try.status_code, "detail": close_try.json().get("detail")},
           "pass": [x["http"] for x in att] == expected and att[0]["failed"] == ["CHECKLIST"] and att[1]["failed"] == ["ON_DUTY"]
           and rec["signature_valid"] and close_try.status_code == 409}

    # 5. Bay trên bản sao số, đối chiếu rào địa lý.
    keep_in = next(f for f in geofence["features"] if f["properties"]["kind"] == "keep_in" and f["properties"]["block_id"] == "block-1")
    poly = shape(keep_in["geometry"])
    block1_m = 14_770.0
    # cất cánh cách Base Nam 50 m trong khối (Base Nam nằm đúng trên mép đầu hành lang), bay đến cách ranh khối 1/2 50 m
    args = sim.parser().parse_args(["--duration", str(int((block1_m - 100) / 30)), "--speed", "30", "--rate", "10", "--alt", "120",
                                    "--start-km", "0.05"])
    buf = io.StringIO()
    sim.run(args, buf)
    tele = [json.loads(x) for x in buf.getvalue().splitlines()]
    tele = [x for x in tele if x["interface"] == "I-07"]
    pos = [x["payload"]["position"] for x in tele]
    outside = sum(not poly.covers(Point(p["lon"], p["lat"])) for p in pos)
    alts = [x["payload"]["altitude"]["value"] for x in tele]
    floor, ceil = keep_in["properties"]["floor"]["value"], keep_in["properties"]["ceiling"]["value"]
    out_alt = sum(not (floor <= h <= ceil) for h in alts if h is not None)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True).transform
    margin = min(shape({"type": "Polygon", "coordinates": [[to_utm(*c) for c in keep_in["geometry"]["coordinates"][0]]]}).exterior.distance(
        Point(to_utm(p["lon"], p["lat"]))) for p in pos)
    twin_flight = {"uas_id": o["uas_id"], "samples_i07": len(pos), "outside_keep_in": outside, "outside_altitude": out_alt,
                   "min_distance_to_edge_m": round(margin), "hard_fences": [f["id"] for f in geofence["features"] if f["properties"]["kind"] == "hard_geofence"],
                   "can_disable": geofence["can_disable"], "pass": outside == 0 and out_alt == 0 and geofence["can_disable"] is False}
    chain = b1.ok("GET", "/v1/audit/verify", aud).json()

    report = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "base": a.base, "run_tag": tag,
              "at03": {"package": {k: v for k, v in package.items() if k != "text"}, "latency": latency, "cycles": cycles,
                       "pass": package["pass"] and latency["pass"]},
              "f04": f04, "twin_flight": twin_flight, "audit_chain": chain}
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    (out.parent / f"{out.name}-package.txt").write_text(package["text"], encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(report, package["text"], out.name), encoding="utf-8")
    print(json.dumps({"at03": report["at03"]["pass"], "f04": f04["pass"], "geofence": twin_flight["pass"], "chain": chain["ok"],
                      "p95_ms": latency["p95_ms"], "max_ms": latency["max_ms"]}))
    return 0 if report["at03"]["pass"] and f04["pass"] and twin_flight["pass"] and chain["ok"] else 1


def markdown(r: dict, text: str, name: str) -> str:
    a3, lat, pk, f4, tw = r["at03"], r["at03"]["latency"], r["at03"]["package"], r["f04"], r["twin_flight"]
    yes = lambda b: "**ĐẠT**" if b else "**TRƯỢT**"
    checks = "\n".join(f"| {c['check']} | {'đạt' if c['ok'] else 'không đạt'} | {c['detail'] and '`' + str(c['detail'])[:70] + '`'} |"
                       for c in pk["checks"])
    cyc = "\n".join(f"| {c['cycle']} | {c['activate_api_ms']} | {c['activate_to_map_ms']} | {c.get('close_to_map_ms', '— (giữ mở cho bước F-04)')} |"
                    for c in r["at03"]["cycles"])
    att = "\n".join(f"| {x['case']} | {x['http']} | {', '.join(x['failed']) or '—'} | {x['state'] or '—'} |" for x in f4["attempts"])
    return f"""# Biên bản chạy AT-03 trên bản sao số (bước 3)

Chạy lúc {r['run_at']} bằng `tools/at_step3_twin.py` qua API HTTP của B1 (`{r['base']}`), mã lần chạy `{r['run_tag']}`.
Kết quả máy đọc được: `{name}.json`; gói văn bản mẫu: `{name}-package.txt`.

## Kết quả

| Bài | Tiêu chí | Kết quả |
|---|---|---|
| AT-03 | Gói dữ liệu đúng định dạng | {yes(pk['pass'])}: {sum(c['ok'] for c in pk['checks'])}/{len(pk['checks'])} mục kiểm |
| AT-03 | Trạng thái lên bản đồ ≤5 giây | {yes(lat['pass'])}: {lat['samples']} lần đổi trạng thái, trung vị {lat['p50_ms']:.0f} ms, phân vị 95 {lat['p95_ms']:.0f} ms, lớn nhất {lat['max_ms']:.0f} ms |
| F-04 | Danh mục, ca trực, khối kích hoạt, ký điện tử | {yes(f4['pass'])} |
| Rào địa lý | Telemetry của bản sao số trong khối đang kích hoạt và trong tầng | {yes(tw['pass'])}: {tw['samples_i07']} mẫu, {tw['outside_keep_in']} mẫu ra ngoài, cách mép gần nhất {tw['min_distance_to_edge_m']} m |
| Nhật ký | Chuỗi mã băm không đứt | {'Nguyên vẹn' if r['audit_chain']['ok'] else 'ĐỨT'}, {r['audit_chain'].get('records', '?')} bản ghi |

## AT-03: gói dữ liệu kích hoạt

Kế hoạch `{pk['activation_id']}` lập từ lịch đã xếp ngày mai cho Khối 1 ({pk['flights']} chuyến), mã băm `{pk['sha256']}`,
mã vùng ED-269 `{pk['ed269_identifier']}`. Hiệp đồng ghi gắn mã băm này. Thử kích hoạt trước giờ: HTTP {pk['activate_before_window']['http']}
({pk['activate_before_window']['detail']}).

| Mục kiểm | Kết quả | Chi tiết |
|---|---|---|
{checks}

Lược đồ ED-269 dùng để kiểm (`api/schemas/ed269-uaszone.schema.json`) viết theo cấu trúc công khai của mô hình UASZone,
chưa đối chiếu văn bản chuẩn đầy đủ; cần rà lại khi có bản chuẩn và quy chế phối hợp I-05.

## AT-03: thời gian trạng thái lên bản đồ

Một tài khoản Bộ phận điều phối bay tầm thấp mở luồng sự kiện `/v1/airspace/stream` như trang bản đồ. Trưởng bộ phận lặp
lập → hiệp đồng → kích hoạt → đóng Khối 1. Thời gian đo từ lúc gửi lệnh đến lúc luồng nhận sự kiện của đúng kế hoạch
(cùng máy, không gồm đường truyền mạng của người xem).

| Lần | Lệnh kích hoạt (ms) | Kích hoạt lên luồng (ms) | Đóng lên luồng (ms) |
|---|---|---|---|
{cyc}

## F-04 trên bản sao số

Hai chuyến của bản sao số bắt đầu sau 10 phút, trong Khối 1 đang kích hoạt.

| Ca | HTTP | Điều kiện chưa đạt | Trạng thái |
|---|---|---|---|
{att}

Bản ghi đã ký: B1 tự kiểm chữ ký {'hợp lệ' if f4['signature_valid_server'] else 'KHÔNG hợp lệ'}; kiểm độc lập bằng khoá công khai
ở `/v1/signing-keys`: {'hợp lệ' if f4['signature_valid_external'] else 'KHÔNG hợp lệ'}. Đóng khối khi còn chuyến đã được cho phép:
HTTP {f4['close_with_cleared_flight']['http']}.

## Bay trên bản sao số

Phương tiện `{tw['uas_id']}` bay dọc Khối 1 (Km 0,05 đến Km 14,72) ở 30 m/s, 120 m so với mặt đất, {tw['samples_i07']} mẫu I-07. Mọi mẫu nằm trong rào
địa lý của khối đang kích hoạt (sàn – trần 20–150 m so với mặt đất); rào cứng luôn có: {', '.join(tw['hard_fences'])};
không có cách tắt rào (`can_disable: false`). Cổng telemetry làm ở bước 4, nên phép đối chiếu này chạy trong script.

## Giới hạn

- Hình học khối là dự thảo theo KML hành lang; tầng 20–150 m so với mặt đất theo demo (D3 chưa chốt).
- Hiệp đồng với cơ quan điều hành bay (I-05) chưa có quy chế: ghi tay số công văn, mã băm gói gắn với lần hiệp đồng.
- Ký điện tử là chữ ký hệ thống (ES256) gắn với người ký đã xác thực đa yếu tố và đăng nhập lại; chưa phải chữ ký số theo
  Luật Giao dịch điện tử.
- Dải hạn chế 10 km tính từ biên giới chờ dữ liệu đường biên giới chính thức.
"""


if __name__ == "__main__":
    raise SystemExit(main())
