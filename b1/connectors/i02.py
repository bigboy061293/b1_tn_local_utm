"""Mô-đun kết nối I-02: hệ thống đăng ký phương tiện bay của Bộ Công an (B1 đọc).

I-02 chưa có giao thức (Đặc tả Mục 6). Theo quy ước chung, B1 làm mô-đun kết nối chạy với dữ liệu giả lập, còn khi vận
hành thì cán bộ đối chiếu thủ công cho đến khi có API; khi có API chỉ thêm một lớp mới, không sửa dịch vụ đăng ký.
Chọn mô-đun bằng B1_I02_CONNECTOR = mock | manual.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Protocol

from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


class RegistrationRecord(BaseModel):
    registration_no: str
    uas_serial: str
    status: str  # valid | suspended | revoked
    valid_from: dt.datetime
    valid_to: dt.datetime | None = None
    flight_mgmt_device_id: str | None = None


class NotAvailable(Exception):
    """Không tra tự động được; cán bộ đối chiếu thủ công."""


class I02Connector(Protocol):
    name: str

    def lookup(self, registration_no: str) -> RegistrationRecord | None: ...


class MockI02:
    """Dữ liệu giả lập trong config/mock/i02_registry.json, dùng cho phát triển và bản sao số."""
    name = "I-02-mock"

    def __init__(self, path: str | pathlib.Path | None = None):
        self.path = pathlib.Path(path or ROOT / "config" / "mock" / "i02_registry.json")

    def lookup(self, registration_no: str) -> RegistrationRecord | None:
        for r in json.loads(self.path.read_text(encoding="utf-8"))["records"]:
            if r["registration_no"] == registration_no:
                return RegistrationRecord(**r)
        return None


class ManualI02:
    name = "manual"

    def lookup(self, registration_no: str) -> RegistrationRecord | None:
        raise NotAvailable("I-02 chưa có giao thức; cán bộ đối chiếu giấy tờ thủ công")


def make(kind: str, path: str | None = None) -> I02Connector:
    return {"mock": lambda: MockI02(path), "manual": ManualI02}[kind]()
