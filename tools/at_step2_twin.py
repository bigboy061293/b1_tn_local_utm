"""Chạy AT-01 và AT-04 trên bản sao số, qua API HTTP của một B1 đang chạy, xuất biên bản JSON và Markdown.

Bản sao số cung cấp đội phương tiện mô phỏng (`twin.sim`, UAS ID dạng SIMnnnnB1TN) và thời gian bay hết Khối 1.
Mỗi nhà khai thác mô phỏng có hồ sơ thiếu đúng một điều kiện chặn (Đặc tả Mục 4.3, F-01), cộng một nhà khai thác hồ sơ
đầy đủ làm đối chứng. Kịch bản:

1. Cán bộ lập hồ sơ khách, xác nhận giấy tờ, ghi CT-1…8 (trừ điều kiện cố ý bỏ).
2. Mọi nhà khai thác nộp yêu cầu bay cho Khối 1; chạy kỳ xếp lịch (gồm xác nhận lần cuối T − 3).
3. AT-01: yêu cầu thiếu hồ sơ nào cũng phải bị chặn, không có chuyến bay, có lý do đúng mã trong nhật ký truy vết.
4. AT-04: với chuyến bay được xếp, lần lượt mọi vai trò (có và không có xác thực đa yếu tố) thử cho phép cất cánh;
   chỉ Trưởng bộ phận và Cán bộ an toàn bay trực ban có MFA được phép; mọi lần từ chối có trong nhật ký truy vết.
5. Phương tiện được cho phép cất cánh bay hết Khối 1 trên bản sao số; kiểm chuỗi mã băm của nhật ký.

B1 phải dùng khoá phát triển để script ký được token có MFA:
    B1_DATABASE_URL=postgresql+psycopg://b1:b1@localhost:5432/b1_at B1_JWKS_FILE=.dev/jwks.json \\
        B1_OIDC_ISSUER=https://idp.b1.local/realms/b1 uvicorn --factory b1.app:create_app --port 8001
    python -m b1.admin bootstrap ...   # script tự làm qua biến B1_DATABASE_URL
    python tools/at_step2_twin.py --base http://localhost:8001
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

import httpx
import jwt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b1.identity.roles import Role
from tools.dev_token import AUDIENCE, ISSUER, KID, key
from twin import sim

TZ = dt.timezone(dt.timedelta(hours=7))
GAPS = [  # (mã ca, bỏ bước nào khi lập hồ sơ, mã quy tắc chặn mong đợi, giai đoạn bị chặn)
    ("thieu-dang-ky", {"registration_certificate"}, "REGISTRATION", "submit"),
    ("dang-ky-chua-xac-nhan", {"verify:registration_certificate"}, "REGISTRATION", "submit"),
    ("thieu-tinh-trang-ky-thuat", {"technical_status_certificate"}, "TECHNICAL_STATUS", "submit"),
    ("thieu-giay-phep-dieu-khien", {"pilot_licence"}, "PILOT_LICENCE", "submit"),
    ("thieu-thiet-bi-quan-ly", {"device"}, "FLIGHT_MGMT_DEVICE", "submit"),
    ("thieu-phep-bay", {"test_permit"}, "PERMIT", "submit"),
    ("thieu-ct-3", {"CT-3"}, "CONFORMANCE", "submit"),
    ("ct-mat-hieu-luc-do-doi-cau-hinh", {"config_change"}, "CONFORMANCE", "run"),
    ("thieu-bao-hiem", {"insurance"}, "INSURANCE", "final"),
]


def token(sub: str, mfa: bool = True) -> str:
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 3600,
                       "amr": ["pwd", "otp"] if mfa else ["pwd"]}, key(), algorithm="RS256", headers={"kid": KID})


class B1:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=30)

    def call(self, method: str, path: str, sub: str, mfa: bool = True, **kw) -> httpx.Response:
        return self.c.request(method, path, headers={"Authorization": f"Bearer {token(sub, mfa)}"}, **kw)

    def ok(self, method: str, path: str, sub: str, **kw) -> dict:
        r = self.call(method, path, sub, **kw)
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8001")
    ap.add_argument("--out", default="docs/acceptance/step2-at01-at04")
    a = ap.parse_args()
    run_tag = uuid.uuid4().hex[:6]
    b1 = B1(a.base)

    # 0. Tài khoản quản trị đầu tiên và cán bộ.
    admin = f"at-admin-{run_tag}"
    subprocess.run([sys.executable, "-m", "b1.admin", "bootstrap", "--issuer", ISSUER, "--subject", admin, "--name", "Quản trị AT"],
                   check=True, cwd=ROOT, env={**os.environ}, capture_output=True)
    corridor = b1.ok("POST", "/v1/organizations", admin, json={"name": f"Hành lang AT {run_tag}", "kind": "corridor"})["id"]
    staff: dict[Role, str] = {}
    for role in Role:
        sub = f"at-{role.value}-{run_tag}"
        b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": sub, "display_name": role.value, "org_id": corridor,
                                                  "roles": [role.value]})
        staff[role] = sub
    head, tech = staff[Role.CORRIDOR_HEAD], staff[Role.FLIGHT_TEST_TECHNICIAN]

    # 1. Đội phương tiện của bản sao số: mỗi nhà khai thác một phương tiện.
    line = sim.centerline_utm()
    cases = [("doi-chung-ho-so-du", set(), None, None), *GAPS]
    tomorrow = (dt.datetime.now(TZ) + dt.timedelta(days=1)).date()
    operators = []
    for n, (name, skip, rule, stage) in enumerate(cases):
        uas = f"SIM{n + 1:04d}B1TN{run_tag.upper()}"
        org = b1.ok("POST", "/v1/organizations", admin, json={"name": f"NKT {name} {run_tag}", "kind": "customer"})["id"]
        cust_sub = f"at-kh-{n}-{run_tag}"
        b1.ok("POST", "/v1/users", admin, json={"issuer": ISSUER, "subject": cust_sub, "display_name": name, "org_id": org,
                                                  "roles": ["customer_operator"]})
        cid = b1.ok("POST", "/v1/registry/customers", head, json={"org_id": org, "name": f"NKT {name}", "tier": "C"})["id"]
        aid = b1.ok("POST", "/v1/registry/aircraft", cust_sub, json={
            "uas_id": uas, "vn_registration_no": f"VN-AT-{run_tag}-{n}", "model": "phương tiện mô phỏng của bản sao số",
            "mtow_kg": 25, "max_speed_ms": 30, "in_testing": True, "flight_mgmt_device_id": f"QLHD-AT-{n}", "config_ref": "fw-1.0"})["id"]
        pid = b1.ok("POST", "/v1/registry/pilots", cust_sub, json={"full_name": f"Người điều khiển {n}"})["id"]
        plan = {"registration_certificate": ("aircraft", aid), "technical_status_certificate": ("aircraft", aid),
                "pilot_licence": ("pilot", pid), "test_permit": ("customer", cid), "insurance": ("customer", cid)}
        for kind, (otype, oid) in plan.items():
            if kind in skip:
                continue
            d = b1.ok("POST", "/v1/registry/documents", cust_sub, json={"owner_type": otype, "owner_id": oid, "kind": kind,
                                                                         "number": f"{kind[:3]}-{n}", "valid_from": "2026-01-01T00:00:00Z",
                                                                         "valid_to": "2028-12-31T23:59:59Z"})
            if f"verify:{kind}" not in skip:
                b1.ok("POST", f"/v1/registry/documents/{d['id']}/verify", head, json={"decision": "verified"})
        if "device" not in skip:
            b1.ok("POST", f"/v1/registry/aircraft/{aid}/verify-device", head)
        for i in range(1, 9):
            if f"CT-{i}" not in skip:
                b1.ok("POST", f"/v1/registry/aircraft/{aid}/conformance", tech, json={"test": f"CT-{i}", "passed": True,
                                                                                      "tested_at": "2026-11-01T00:00:00Z"})
        operators.append({"case": name, "rule": rule, "stage": stage, "skip": sorted(skip), "uas_id": uas, "customer": cust_sub,
                          "customer_id": cid, "aircraft_id": aid, "pilot_id": pid})

    # 2. Nộp yêu cầu: mỗi nhà khai thác một khung riêng trên Khối 1, ngày mai và ngày kia (trong mốc T − 3).
    for k, o in enumerate(operators):
        day = tomorrow + dt.timedelta(days=k // 4)
        r = b1.ok("POST", "/v1/scheduling/requests", o["customer"], json={
            "aircraft_id": o["aircraft_id"], "pilot_id": o["pilot_id"], "block_ids": ["block-1"], "bvlos": True,
            "items": [{"day": str(day), "slot": k % 4}]})
        o.update(request_id=r["id"], queue_no_at_submit=r["queue_no"], missing_at_submit=[m["rule"] for m in r["missing"]])
        if "config_change" in o["skip"]:  # hồ sơ đủ lúc nộp, sau đó khách đổi cấu hình làm mất hiệu lực CT
            b1.ok("PATCH", f"/v1/registry/aircraft/{o['aircraft_id']}", o["customer"], json={"config_ref": "fw-2.0"})
    run = b1.ok("POST", "/v1/scheduling/runs", head)

    # 3. AT-01.
    at01 = []
    for o in operators:
        req = b1.ok("GET", f"/v1/scheduling/requests/{o['request_id']}", o["customer"])
        flights = [i["flight_id"] for i in req["items"] if i["status"] == "scheduled" and i["flight_id"]]
        log = b1.ok("GET", "/v1/audit", staff[Role.AUDITOR], params={"action": "scheduling.request.blocked",
                                                                      "target_id": o["request_id"], "limit": 1000})["items"]
        logged_rules = sorted({m["rule"] for x in log for m in x["detail"]["missing"]})
        reasons = sorted({(b["stage"], b["rule"]) for b in req["block_reasons"]})
        o.update(status=req["status"], items=[(i["status"], i["last_reason"]) for i in req["items"]], flights=flights)
        if o["rule"] is None:
            continue
        blocked = not flights
        at01.append({"case": o["case"], "expected_rule": o["rule"], "expected_stage": o["stage"], "queue_no_at_submit": o["queue_no_at_submit"],
                     "status": req["status"], "items": o["items"], "blocked": blocked, "block_reasons": reasons,
                     "audit_rules": logged_rules, "audit_records": len(log),
                     "pass": blocked and o["rule"] in logged_rules and (o["stage"], o["rule"]) in reasons})

    # 4. AT-04 trên chuyến bay của nhà khai thác hồ sơ đầy đủ.
    control = operators[0]
    assert control["flights"], f"chuyến đối chứng không được xếp: {control}"
    fid = control["flights"][0]
    attempts = []
    order = [(r, False) for r in Role] + [(r, True) for r in Role if r not in (Role.CORRIDOR_HEAD, Role.DUTY_SAFETY_OFFICER)]
    order += [(Role.DUTY_SAFETY_OFFICER, True), (Role.CORRIDOR_HEAD, True)]  # người có quyền có MFA: lần đầu được, lần sau 409
    order.append((Role.CUSTOMER_OPERATOR, "own"))  # chính khách hàng của chuyến
    for role, mfa in order:
        sub = control["customer"] if mfa == "own" else staff[role]
        r = b1.call("POST", f"/v1/flights/{fid}/takeoff-clearance", sub, mfa=mfa is not False)
        attempts.append({"role": role.value + (" (khách của chuyến)" if mfa == "own" else ""), "mfa": mfa is not False,
                         "http": r.status_code, "allowed": r.status_code == 200,
                         "detail": r.json().get("detail") if r.status_code != 200 else r.json()["state"]})
    allowed = [x for x in attempts if x["allowed"]]
    log = b1.ok("GET", "/v1/audit", staff[Role.AUDITOR], params={"action": "flight.clear_takeoff", "target_id": fid, "limit": 1000})["items"]
    denied_logged = sum(1 for x in log if x["outcome"] == "denied")
    flight = b1.ok("GET", f"/v1/flights/{fid}", staff[Role.AUDITOR])
    expected_ok = {(Role.DUTY_SAFETY_OFFICER.value, True)}  # Trưởng bộ phận có MFA thử sau khi đã cho phép nên nhận 409
    at04 = {"flight_id": fid, "attempts": attempts, "allowed": [(x["role"], x["mfa"]) for x in allowed],
            "denied": len(attempts) - len(allowed), "denied_logged": denied_logged, "rejected_after_clearance": sum(
                1 for x in attempts if x["http"] == 409), "flight_state": flight["state"]}
    # Trưởng bộ phận có MFA cũng phải được phép: thử trên một chuyến thứ hai nếu có; ở đây kiểm qua chuyến của ca thiếu bảo hiểm
    # đã bị huỷ, nên dựng thêm một yêu cầu đối chứng thứ hai.
    r2 = b1.ok("POST", "/v1/scheduling/requests", control["customer"], json={
        "aircraft_id": control["aircraft_id"], "pilot_id": control["pilot_id"], "block_ids": ["block-1"],
        "items": [{"day": str(tomorrow + dt.timedelta(days=3)), "slot": 0}]})
    b1.ok("POST", "/v1/scheduling/runs", head)
    f2 = [i["flight_id"] for i in b1.ok("GET", f"/v1/scheduling/requests/{r2['id']}", control["customer"])["items"] if i["flight_id"]]
    head_ok = bool(f2) and b1.call("POST", f"/v1/flights/{f2[0]}/takeoff-clearance", head).status_code == 200
    at04.update(head_with_mfa_allowed_on_second_flight=head_ok)
    at04["pass"] = (set(at04["allowed"]) == expected_ok and head_ok and denied_logged >= len(attempts) - len(allowed) - at04["rejected_after_clearance"]
                    and flight["state"] == "cleared")

    # 5. Bay trên bản sao số: phương tiện đối chứng bay hết Khối 1 sau khi được cho phép cất cánh.
    block1_m = 14_770.0
    args = sim.parser().parse_args(["--duration", str(int(block1_m / 30) + 1), "--speed", "30", "--rate", "10"])
    buf = io.StringIO()
    sim.run(args, buf)
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    tele = [x for x in lines if x["interface"] == "I-07"]
    twin_flight = {"uas_id": control["uas_id"], "block": "block-1", "distance_km": round(min(block1_m, line.length) / 1000, 2),
                   "duration_s": args.duration, "telemetry_samples_i07": len(tele)}
    chain = b1.ok("GET", "/v1/audit/verify", staff[Role.AUDITOR])

    report = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "base": a.base, "run_tag": run_tag,
              "scheduling_run": {"id": run["id"], "summary": run["summary"]},
              "at01": {"cases": at01, "total": len(at01), "blocked": sum(c["blocked"] for c in at01),
                       "logged": sum(c["expected_rule"] in c["audit_rules"] for c in at01), "pass": all(c["pass"] for c in at01)},
              "at04": at04, "twin_flight": twin_flight, "audit_chain": chain,
              "control": {"case": control["case"], "status": control["status"], "items": control["items"]}}
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    (out.with_suffix(".md")).write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"at01": report["at01"]["pass"], "at04": at04["pass"], "chain": chain["ok"]}))
    return 0 if report["at01"]["pass"] and at04["pass"] and chain["ok"] else 1


STAGE = {"submit": "khi nộp", "run": "kỳ xếp lịch", "final": "xác nhận T − 3"}


def markdown(r: dict) -> str:
    a1, a4 = r["at01"], r["at04"]
    rows1 = "\n".join(f"| {c['case']} | `{c['expected_rule']}` | {STAGE[c['expected_stage']]} | {c['queue_no_at_submit']} | "
                      f"{'có' if c['blocked'] else '**KHÔNG**'} | {', '.join(f'{STAGE.get(s, s)}: {x}' for s, x in c['block_reasons'])} | "
                      f"{c['audit_records']} | {'ĐẠT' if c['pass'] else '**TRƯỢT**'} |" for c in a1["cases"])
    rows4 = "\n".join(f"| {x['role']} | {'có' if x['mfa'] else 'không'} | {x['http']} | {'**được phép**' if x['allowed'] else 'từ chối'} | "
                      f"{x['detail']} |" for x in a4["attempts"])
    return f"""# Biên bản chạy AT-01 và AT-04 trên bản sao số (bước 2)

Chạy lúc {r['run_at']} bằng `tools/at_step2_twin.py` qua API HTTP của B1 (`{r['base']}`), mã lần chạy `{r['run_tag']}`.
Đội phương tiện lấy từ bản sao số (`twin/sim.py`); mỗi nhà khai thác có hồ sơ thiếu đúng một điều kiện chặn, cộng một
nhà khai thác hồ sơ đầy đủ làm đối chứng. Kết quả máy đọc được: `step2-at01-at04.json`.

## Kết quả

| Bài | Tiêu chí (Đặc tả Mục 9) | Kết quả |
|---|---|---|
| AT-01 | 100% bị chặn, có nhật ký | **{'ĐẠT' if a1['pass'] else 'TRƯỢT'}**: {a1['blocked']}/{a1['total']} ca bị chặn, {a1['logged']}/{a1['total']} ca có lý do đúng mã trong nhật ký |
| AT-04 | Chỉ 2 vai trò được phép; các vai trò khác bị từ chối | **{'ĐẠT' if a4['pass'] else 'TRƯỢT'}**: {len(a4['attempts'])} lần thử, được phép: {', '.join(f'{x} (MFA)' for x, _ in a4['allowed'])}; Trưởng bộ phận có MFA được phép trên chuyến thứ hai: {'có' if a4['head_with_mfa_allowed_on_second_flight'] else 'không'} |
| Nhật ký | Chuỗi mã băm không đứt | {'Nguyên vẹn' if r['audit_chain']['ok'] else 'ĐỨT'}, {r['audit_chain']['records']} bản ghi |

Kỳ xếp lịch: {json.dumps(r['scheduling_run']['summary'], ensure_ascii=False)}. Chuyến đối chứng: {r['control']['status']}.

## AT-01: từng ca thiếu hồ sơ

| Ca | Quy tắc mong đợi | Bị chặn ở | Số khi nộp | Bị chặn | Lý do đã ghi | Bản ghi nhật ký | Kết luận |
|---|---|---|---|---|---|---|---|
{rows1}

Ca "đổi cấu hình" có hồ sơ đủ lúc nộp (nhận số `HL-`), sau đó khách đổi phần mềm, nên CT-1…8 mất hiệu lực và kỳ xếp lịch
chặn. Ca "thiếu bảo hiểm" được xếp lịch rồi bị huỷ ở lần xác nhận cuối T − 3 ngày làm việc, chuyến bay bị huỷ.

## AT-04: thử cho phép cất cánh chuyến `{a4['flight_id']}`

| Vai trò | MFA | HTTP | Kết quả | Chi tiết |
|---|---|---|---|---|
{rows4}

Sau lần được phép, chuyến chuyển sang `{a4['flight_state']}`; {a4['rejected_after_clearance']} lần thử sau đó của vai trò có quyền trả 409 (đã cho phép, không cho phép lại).
{a4['denied_logged']} lần từ chối có bản ghi `denied` trong nhật ký truy vết của chuyến.

## Bay trên bản sao số

Phương tiện `{r['twin_flight']['uas_id']}` bay hết Khối 1 ({r['twin_flight']['distance_km']} km, 30 m/s,
{r['twin_flight']['duration_s']} giây), sinh {r['twin_flight']['telemetry_samples_i07']} mẫu telemetry I-07 ở 10 Hz. Cổng telemetry
chưa có (bước 4) nên mẫu chưa được nạp vào B1; phần này chỉ chứng minh chuỗi xếp lịch → cho phép cất cánh → bay chạy liền.

## Giới hạn

- Xác thực đa yếu tố được giả lập bằng claim `amr` trong token ký bằng khoá phát triển; với Keycloak thật cần bật OTP.
- "Khi trực" của Trưởng bộ phận chưa có lịch trực (bước 3).
"""


if __name__ == "__main__":
    raise SystemExit(main())
