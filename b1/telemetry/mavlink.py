"""Bộ chuyển đổi MAVLink v2 trên UDP → `TelemetrySample` (kênh ưu tiên 1 của I-07).

Tự viết phần giải mã cho 7 bản tin cần dùng, không phụ thuộc pymavlink (giấy phép LGPL). Bố cục trường, CRC_EXTRA và cách
ký theo đặc tả MAVLink công khai (mavlink.io, common.xml); bài kiểm thử đối chiếu với khung do pymavlink sinh khi có sẵn.

Một phương tiện = một sysid, gán vào chuyến qua `POST /telemetry/v1/flights/{id}/mavlink`. Mỗi GLOBAL_POSITION_INT sinh
một mẫu; các trường còn lại lấy từ bản tin gần nhất (HEARTBEAT, SYS_STATUS, GPS_RAW_INT, BATTERY_STATUS, SYSTEM_TIME).
Có khoá ký thì bản tin không ký, sai chữ ký hoặc nhãn thời gian ký không tăng đều bị bỏ (chống phát lại).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import math
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("b1.mavlink")

STX = 0xFD
SIGNED = 0x01
# msgid: (tên, struct, độ dài tối thiểu phần không mở rộng, CRC_EXTRA)
MESSAGES = {
    0: ("HEARTBEAT", "<IBBBBB", 50),
    1: ("SYS_STATUS", "<IIIHHhHHHHHHb", 124),
    2: ("SYSTEM_TIME", "<QI", 137),
    24: ("GPS_RAW_INT", "<QiiiHHHHBB", 24),
    33: ("GLOBAL_POSITION_INT", "<IiiiihhhH", 104),
    74: ("VFR_HUD", "<ffffhH", 20),
    147: ("BATTERY_STATUS", "<iih10HhBBBb", 154),
}
FIELDS = {
    "HEARTBEAT": ("custom_mode", "type", "autopilot", "base_mode", "system_status", "mavlink_version"),
    "SYS_STATUS": ("sensors_present", "sensors_enabled", "sensors_health", "load", "voltage_battery", "current_battery",
                   "drop_rate_comm", "errors_comm", "errors_count1", "errors_count2", "errors_count3", "errors_count4",
                   "battery_remaining"),
    "SYSTEM_TIME": ("time_unix_usec", "time_boot_ms"),
    "GPS_RAW_INT": ("time_usec", "lat", "lon", "alt", "eph", "epv", "vel", "cog", "fix_type", "satellites_visible"),
    "GLOBAL_POSITION_INT": ("time_boot_ms", "lat", "lon", "alt", "relative_alt", "vx", "vy", "vz", "hdg"),
    "VFR_HUD": ("airspeed", "groundspeed", "alt", "climb", "heading", "throttle"),
}
MAV_MODE_FLAG_SAFETY_ARMED, MAV_MODE_FLAG_AUTO_ENABLED, MAV_MODE_FLAG_GUIDED_ENABLED = 128, 4, 8
GPS_FIX = {0: "none", 1: "none", 2: "2d", 3: "3d", 4: "dgps", 5: "rtk_float", 6: "rtk_fixed"}
ARDUCOPTER_MODES = {0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
                    9: "LAND", 16: "POSHOLD", 17: "BRAKE", 21: "SMART_RTL"}
AUTONOMY = {"STABILIZE": "manual", "ACRO": "manual", "ALT_HOLD": "assisted", "LOITER": "assisted", "POSHOLD": "assisted",
            "BRAKE": "assisted"}


def crc_accumulate(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/MCRF4XX (X.25) như MAVLink."""
    for b in data:
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


@dataclass
class Frame:
    msgid: int
    sysid: int
    compid: int
    seq: int
    payload: bytes
    signed: bool = False
    link_id: int | None = None
    sign_timestamp: int | None = None
    signature_ok: bool | None = None

    @property
    def name(self) -> str:
        return MESSAGES.get(self.msgid, (f"MSG_{self.msgid}",))[0]

    def fields(self) -> dict:
        name, fmt, _ = MESSAGES[self.msgid]
        size = struct.calcsize(fmt)
        vals = struct.unpack(fmt, self.payload.ljust(size, b"\0")[:size])  # MAVLink 2 cắt số 0 ở cuối tải
        if name == "BATTERY_STATUS":
            return {"current_consumed": vals[0], "energy_consumed": vals[1], "temperature": vals[2], "voltages": list(vals[3:13]),
                    "current_battery": vals[13], "id": vals[14], "battery_function": vals[15], "type": vals[16],
                    "battery_remaining": vals[17]}
        return dict(zip(FIELDS[name], vals))


def _signature(key: bytes, header_to_crc: bytes, link_id: int, ts: int) -> bytes:
    return hashlib.sha256(key + header_to_crc + bytes([link_id]) + ts.to_bytes(6, "little")).digest()[:6]


def encode(msgid: int, values: tuple, sysid: int = 1, compid: int = 1, seq: int = 0, key: bytes | None = None,
           link_id: int = 0, sign_timestamp: int | None = None) -> bytes:
    _, fmt, extra = MESSAGES[msgid]
    payload = struct.pack(fmt, *values).rstrip(b"\0") or b"\0"
    flags = SIGNED if key else 0
    hdr = bytes([STX, len(payload), flags, 0, seq & 0xFF, sysid, compid]) + msgid.to_bytes(3, "little")
    crc = crc_accumulate(hdr[1:] + payload)
    crc = crc_accumulate(bytes([extra]), crc)
    out = hdr + payload + crc.to_bytes(2, "little")
    if key:
        ts = sign_timestamp if sign_timestamp is not None else int((time.time() - 1420070400) * 100_000)
        out += bytes([link_id]) + ts.to_bytes(6, "little") + _signature(key, out, link_id, ts)
    return out


def decode(buf: bytes, keys: dict[int, bytes] | None = None) -> tuple[list[Frame], int]:
    """Tách các khung MAVLink v2 trong `buf`; trả (khung hợp lệ, số khung hỏng). Bản tin không biết msgid bị bỏ qua."""
    frames, bad, i = [], 0, 0
    while i < len(buf):
        if buf[i] != STX:
            i += 1
            continue
        if len(buf) - i < 12:
            break
        ln, inc = buf[i + 1], buf[i + 2]
        total = 12 + ln + (13 if inc & SIGNED else 0)
        if len(buf) - i < total:
            break
        raw = buf[i:i + total]
        msgid = int.from_bytes(raw[7:10], "little")
        if msgid not in MESSAGES:
            i += total
            continue
        crc = crc_accumulate(raw[1:10 + ln])
        crc = crc_accumulate(bytes([MESSAGES[msgid][2]]), crc)
        if crc != int.from_bytes(raw[10 + ln:12 + ln], "little"):
            bad += 1
            i += 1
            continue
        f = Frame(msgid, raw[5], raw[6], raw[4], bytes(raw[10:10 + ln]))
        if inc & SIGNED:
            f.signed = True
            f.link_id = raw[12 + ln]
            f.sign_timestamp = int.from_bytes(raw[13 + ln:19 + ln], "little")
            key = (keys or {}).get(f.sysid)
            if key is not None:
                f.signature_ok = _signature(key, raw[:12 + ln], f.link_id, f.sign_timestamp) == raw[19 + ln:25 + ln]
        frames.append(f)
        i += total
    return frames, bad


@dataclass
class VehicleState:
    """Gộp các bản tin gần nhất của một sysid thành mẫu I-07."""
    flight_id: uuid.UUID
    uas_id: str
    heartbeat: dict | None = None
    sys_status: dict | None = None
    gps: dict | None = None
    battery: dict | None = None
    boot_offset_ms: float | None = None  # unix_ms − time_boot_ms, từ SYSTEM_TIME
    last_sign_ts: dict = field(default_factory=dict)

    def update(self, f: Frame, t_receive: dt.datetime) -> dict | None:
        v = f.fields()
        if f.name == "HEARTBEAT":
            self.heartbeat = v
        elif f.name == "SYS_STATUS":
            self.sys_status = v
        elif f.name == "SYSTEM_TIME":
            if v["time_unix_usec"] > 0:
                self.boot_offset_ms = v["time_unix_usec"] / 1000.0 - v["time_boot_ms"]
        elif f.name == "GPS_RAW_INT":
            self.gps = v
        elif f.name == "BATTERY_STATUS":
            self.battery = v
        elif f.name == "GLOBAL_POSITION_INT":
            return self.sample(v, t_receive)
        return None

    def sample(self, g: dict, t_receive: dt.datetime) -> dict:
        if self.boot_offset_ms is not None:
            ts = dt.datetime.fromtimestamp((g["time_boot_ms"] + self.boot_offset_ms) / 1000.0, dt.timezone.utc)
        else:  # chưa có SYSTEM_TIME: dùng giờ nhận; CT-3 sẽ đánh trượt vì không phải giờ nguồn GNSS
            ts = t_receive
        hb = self.heartbeat or {}
        base = hb.get("base_mode", 0)
        if hb.get("autopilot") == 3:
            mode = ARDUCOPTER_MODES.get(hb.get("custom_mode", 0), f"CUSTOM_{hb.get('custom_mode', 0)}")
        else:
            mode = f"CUSTOM_{hb.get('custom_mode', 0)}"
        autonomy = AUTONOMY.get(mode) or ("automatic" if base & (MAV_MODE_FLAG_AUTO_ENABLED | MAV_MODE_FLAG_GUIDED_ENABLED)
                                          else "manual")
        vx, vy, vz = g["vx"] / 100.0, g["vy"] / 100.0, g["vz"] / 100.0
        track = g["hdg"] / 100.0 if g["hdg"] != 65535 else (math.degrees(math.atan2(vy, vx)) + 360.0) % 360.0
        gps = self.gps or {}
        ss = self.sys_status or {}
        pct = ss.get("battery_remaining", -1)
        if self.battery and self.battery.get("battery_remaining", -1) >= 0:
            pct = self.battery["battery_remaining"]
        volt = ss.get("voltage_battery", 65535)
        energy = {}
        if pct is not None and pct >= 0:
            energy["batteryPercent"] = float(pct)
        energy["voltage"] = round(volt / 1000.0, 3) if volt not in (65535, None) else 0.0
        flags = []
        if hb.get("system_status") in (5, 6):  # MAV_STATE_CRITICAL, MAV_STATE_EMERGENCY
            flags.append("emergency")
        if mode in ("RTL", "SMART_RTL"):
            flags.append("failsafe_rtl")
        if pct is not None and 0 <= pct < 20:
            flags.append("low_battery")
        return {
            "flightId": str(self.flight_id), "uasId": self.uas_id,
            "timeSource": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "position": {"lat": g["lat"] / 1e7, "lon": g["lon"] / 1e7},
            "altitude": {"value": g["alt"] / 1000.0, "reference": "AMSL", "agl": g["relative_alt"] / 1000.0},
            "velocity": {"groundSpeed": round(math.hypot(vx, vy), 2), "verticalSpeed": -vz, "track": round(track % 360.0, 2)},
            "gnss": {"fixType": GPS_FIX.get(gps.get("fix_type", 0), "none"), "satellites": gps.get("satellites_visible", 0)
                     if gps.get("satellites_visible", 255) != 255 else 0,
                     "hdop": gps.get("eph", 9999) / 100.0 if gps.get("eph", 65535) != 65535 else 99.99},
            "energy": energy,
            "flightMode": {"mode": mode, "autonomy": autonomy},
            "armed": bool(base & MAV_MODE_FLAG_SAFETY_ARMED),
            "safetyFlags": flags,
            "c2Link": {"state": "ok"},
        }


class MavlinkGateway:
    """Nghe UDP, ánh xạ sysid → chuyến, chuyển mẫu cho dịch vụ telemetry. Chạy một luồng nền."""

    def __init__(self, telemetry, sm, host: str = "0.0.0.0", port: int = 14560):
        self.telemetry, self.sm, self.host, self.port = telemetry, sm, host, port
        self.vehicles: dict[int, VehicleState] = {}
        self.keys: dict[int, bytes] = {}
        self._bindings: dict[int, tuple[uuid.UUID, str, bytes | None]] = {}
        self._bind_at = 0.0
        self.stats = {"datagrams": 0, "frames": 0, "crc_errors": 0, "unbound": 0, "signature_rejected": 0, "samples": 0}
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def refresh_bindings(self) -> None:
        from sqlalchemy import select

        from b1.telemetry.models import MavlinkBinding
        with self.sm() as s:
            rows = s.scalars(select(MavlinkBinding).where(MavlinkBinding.active.is_(True)).order_by(MavlinkBinding.created_at))
            self._bindings = {b.sysid: (b.flight_id, b.uas_id, bytes.fromhex(b.signing_key_hex) if b.signing_key_hex else None)
                              for b in rows}
        self.keys = {k: v[2] for k, v in self._bindings.items() if v[2]}
        self._bind_at = time.monotonic()

    def handle(self, data: bytes, t_receive: dt.datetime | None = None) -> list[dict]:
        t_receive = t_receive or dt.datetime.now(dt.timezone.utc)
        if time.monotonic() - self._bind_at > 2.0:
            self.refresh_bindings()
        self.stats["datagrams"] += 1
        frames, bad = decode(data, self.keys)
        self.stats["crc_errors"] += bad
        acks = []
        for f in frames:
            self.stats["frames"] += 1
            b = self._bindings.get(f.sysid)
            if b is None:
                self.stats["unbound"] += 1
                continue
            fid, uas, key = b
            v = self.vehicles.get(f.sysid)
            if v is None or v.flight_id != fid:
                v = self.vehicles[f.sysid] = VehicleState(fid, uas)
            if key is not None:
                last = v.last_sign_ts.get(f.link_id, -1)
                if not f.signed or not f.signature_ok or f.sign_timestamp <= last:
                    self.stats["signature_rejected"] += 1
                    continue
                v.last_sign_ts[f.link_id] = f.sign_timestamp
            sample = v.update(f, t_receive)
            if sample is None:
                continue
            target = self.telemetry.target(fid)
            try:
                self.telemetry.check_target(target, None, backfill=False)
            except Exception as e:  # noqa: BLE001
                log.info("MAVLink sysid %s: %s", f.sysid, e)
                continue
            ack = self.telemetry.ingest(target, [sample], "mavlink", t_receive=t_receive)
            self.stats["samples"] += ack["accepted"]
            acks.append(ack)
        return acks

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.5)
        threading.Thread(target=self._loop, name="b1-mavlink", daemon=True).start()
        log.info("MAVLink v2 UDP nghe %s:%s", self.host, self.port)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.handle(data)
            except Exception:  # noqa: BLE001
                log.exception("lỗi xử lý gói MAVLink")

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
