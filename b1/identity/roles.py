"""Vai trò và quyền của B1 (Đặc tả B1 v0.1, Mục 1.1; F-04; F-12).

Vai trò nội bộ do B1 giữ, tách khỏi bên cấp danh tính và khỏi phạm vi quyền F3548 (Mục 6).
"""
from __future__ import annotations

import enum


class Role(str, enum.Enum):
    CORRIDOR_HEAD = "corridor_head"                          # Trưởng bộ phận quản lý hành lang
    DUTY_SAFETY_OFFICER = "duty_safety_officer"              # Cán bộ an toàn bay trực ban
    RADIO_NETWORK_ENGINEER = "radio_network_engineer"        # Kỹ sư vô tuyến và mạng
    IT_ENGINEER = "it_engineer"                              # Kỹ sư công nghệ thông tin
    FLIGHT_TEST_TECHNICIAN = "flight_test_technician"        # Kỹ thuật viên bay thử
    CUSTOMER_OPERATOR = "customer_operator"                  # Khách hàng: nhà khai thác bay
    CUSTOMER_TRAINING = "customer_training"                  # Khách hàng: bộ phận đào tạo
    REGULATOR = "regulator"                                  # Bộ Quốc phòng, Bộ Công an, Ban công tác liên ngành
    LOW_ALTITUDE_COORDINATION = "low_altitude_coordination"  # Bộ phận điều phối bay tầm thấp
    AUDITOR = "auditor"                                      # Kiểm toán


class Perm(str, enum.Enum):
    FLIGHT_CLEAR_TAKEOFF = "flight.clear_takeoff"  # F-04, AT-04
    STOP_ISSUE = "stop.issue"                      # F-07
    SCHEDULE_MANAGE = "schedule.manage"            # F-02: xếp lịch, điều phối
    REQUEST_OWN = "request.own"                    # khách hàng: đặt lịch, xem chuyến của mình
    FLIGHT_READ_ALL = "flight.read_all"
    PORTAL_ADMIN = "portal.admin"                  # quản trị cổng đăng ký bay và dữ liệu, không sửa dữ liệu bay
    USER_MANAGE = "user.manage"
    AUDIT_READ = "audit.read"


R, P = Role, Perm
_STAFF_READ = {R.CORRIDOR_HEAD, R.DUTY_SAFETY_OFFICER, R.RADIO_NETWORK_ENGINEER, R.IT_ENGINEER, R.FLIGHT_TEST_TECHNICIAN}

GRANTS: dict[Perm, frozenset[Role]] = {
    # F-04: chỉ Cán bộ an toàn bay trực ban và Trưởng bộ phận.
    P.FLIGHT_CLEAR_TAKEOFF: frozenset({R.CORRIDOR_HEAD, R.DUTY_SAFETY_OFFICER}),
    P.STOP_ISSUE: frozenset({R.CORRIDOR_HEAD, R.DUTY_SAFETY_OFFICER}),
    P.SCHEDULE_MANAGE: frozenset({R.CORRIDOR_HEAD}),
    P.REQUEST_OWN: frozenset({R.CUSTOMER_OPERATOR, R.CUSTOMER_TRAINING}),
    P.FLIGHT_READ_ALL: frozenset(_STAFF_READ | {R.REGULATOR, R.LOW_ALTITUDE_COORDINATION, R.AUDITOR}),
    P.PORTAL_ADMIN: frozenset({R.LOW_ALTITUDE_COORDINATION}),
    P.USER_MANAGE: frozenset({R.IT_ENGINEER}),
    P.AUDIT_READ: frozenset({R.AUDITOR, R.IT_ENGINEER}),
}

# Thao tác đặc quyền: luôn đòi xác thực đa yếu tố.
PRIVILEGED: frozenset[Perm] = frozenset({P.FLIGHT_CLEAR_TAKEOFF, P.STOP_ISSUE, P.SCHEDULE_MANAGE, P.USER_MANAGE, P.PORTAL_ADMIN})


def allowed(roles: frozenset[Role] | set[Role], perm: Perm) -> bool:
    return bool(GRANTS[perm] & set(roles))
