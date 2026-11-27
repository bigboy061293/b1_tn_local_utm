# Biên bản chạy AT-01 và AT-04 trên bản sao số (bước 2)

Chạy lúc 2026-11-27T08:33:24+00:00 bằng `tools/at_step2_twin.py` qua API HTTP của B1 (`http://localhost:8011`), mã lần chạy `a9549f`.
Đội phương tiện lấy từ bản sao số (`twin/sim.py`); mỗi nhà khai thác có hồ sơ thiếu đúng một điều kiện chặn, cộng một
nhà khai thác hồ sơ đầy đủ làm đối chứng. Kết quả máy đọc được: `step2-at01-at04.json`.

## Kết quả

| Bài | Tiêu chí (Đặc tả Mục 9) | Kết quả |
|---|---|---|
| AT-01 | 100% bị chặn, có nhật ký | **ĐẠT**: 9/9 ca bị chặn, 9/9 ca có lý do đúng mã trong nhật ký |
| AT-04 | Chỉ 2 vai trò được phép; các vai trò khác bị từ chối | **ĐẠT**: 23 lần thử, được phép: duty_safety_officer (MFA); Trưởng bộ phận có MFA được phép trên chuyến thứ hai: có |
| Nhật ký | Chuỗi mã băm không đứt | Nguyên vẹn, 302 bản ghi |

Kỳ xếp lịch: {"blocked": 8, "scheduled": 2, "cancelled": 1, "overdue_requests": []}. Chuyến đối chứng: scheduled.

## AT-01: từng ca thiếu hồ sơ

| Ca | Quy tắc mong đợi | Bị chặn ở | Số khi nộp | Bị chặn | Lý do đã ghi | Bản ghi nhật ký | Kết luận |
|---|---|---|---|---|---|---|---|
| thieu-dang-ky | `REGISTRATION` | khi nộp | TAM-20261127-0001 | có | kỳ xếp lịch: REGISTRATION, khi nộp: REGISTRATION | 2 | ĐẠT |
| dang-ky-chua-xac-nhan | `REGISTRATION` | khi nộp | TAM-20261127-0002 | có | kỳ xếp lịch: REGISTRATION, khi nộp: REGISTRATION | 2 | ĐẠT |
| thieu-tinh-trang-ky-thuat | `TECHNICAL_STATUS` | khi nộp | TAM-20261127-0003 | có | kỳ xếp lịch: TECHNICAL_STATUS, khi nộp: TECHNICAL_STATUS | 2 | ĐẠT |
| thieu-giay-phep-dieu-khien | `PILOT_LICENCE` | khi nộp | TAM-20261127-0004 | có | kỳ xếp lịch: PILOT_LICENCE, khi nộp: PILOT_LICENCE | 2 | ĐẠT |
| thieu-thiet-bi-quan-ly | `FLIGHT_MGMT_DEVICE` | khi nộp | TAM-20261127-0005 | có | kỳ xếp lịch: FLIGHT_MGMT_DEVICE, khi nộp: FLIGHT_MGMT_DEVICE | 2 | ĐẠT |
| thieu-phep-bay | `PERMIT` | khi nộp | TAM-20261127-0006 | có | kỳ xếp lịch: PERMIT, khi nộp: PERMIT | 2 | ĐẠT |
| thieu-ct-3 | `CONFORMANCE` | khi nộp | TAM-20261127-0007 | có | kỳ xếp lịch: CONFORMANCE, khi nộp: CONFORMANCE | 2 | ĐẠT |
| ct-mat-hieu-luc-do-doi-cau-hinh | `CONFORMANCE` | kỳ xếp lịch | HL-20261127-0002 | có | kỳ xếp lịch: CONFORMANCE | 1 | ĐẠT |
| thieu-bao-hiem | `INSURANCE` | xác nhận T − 3 | HL-20261127-0003 | có | xác nhận T − 3: INSURANCE | 1 | ĐẠT |

Ca "đổi cấu hình" có hồ sơ đủ lúc nộp (nhận số `HL-`), sau đó khách đổi phần mềm, nên CT-1…8 mất hiệu lực và kỳ xếp lịch
chặn. Ca "thiếu bảo hiểm" được xếp lịch rồi bị huỷ ở lần xác nhận cuối T − 3 ngày làm việc, chuyến bay bị huỷ.

## AT-04: thử cho phép cất cánh chuyến `cb21a4e8-4d75-4b69-899f-a139e62ae207`

| Vai trò | MFA | HTTP | Kết quả | Chi tiết |
|---|---|---|---|---|
| corridor_head | không | 403 | từ chối | thao tác đặc quyền phải xác thực đa yếu tố |
| duty_safety_officer | không | 403 | từ chối | thao tác đặc quyền phải xác thực đa yếu tố |
| radio_network_engineer | không | 403 | từ chối | vai trò không có quyền này |
| it_engineer | không | 403 | từ chối | vai trò không có quyền này |
| flight_test_technician | không | 403 | từ chối | vai trò không có quyền này |
| customer_operator | không | 403 | từ chối | vai trò không có quyền này |
| customer_training | không | 403 | từ chối | vai trò không có quyền này |
| regulator | không | 403 | từ chối | vai trò không có quyền này |
| low_altitude_coordination | không | 403 | từ chối | vai trò không có quyền này |
| auditor | không | 403 | từ chối | vai trò không có quyền này |
| province_portal | không | 403 | từ chối | vai trò không có quyền này |
| radio_network_engineer | có | 403 | từ chối | vai trò không có quyền này |
| it_engineer | có | 403 | từ chối | vai trò không có quyền này |
| flight_test_technician | có | 403 | từ chối | vai trò không có quyền này |
| customer_operator | có | 403 | từ chối | vai trò không có quyền này |
| customer_training | có | 403 | từ chối | vai trò không có quyền này |
| regulator | có | 403 | từ chối | vai trò không có quyền này |
| low_altitude_coordination | có | 403 | từ chối | vai trò không có quyền này |
| auditor | có | 403 | từ chối | vai trò không có quyền này |
| province_portal | có | 403 | từ chối | vai trò không có quyền này |
| duty_safety_officer | có | 200 | **được phép** | cleared |
| corridor_head | có | 409 | từ chối | chuyến bay đang ở trạng thái cleared |
| customer_operator (khách của chuyến) | có | 403 | từ chối | vai trò không có quyền này |

Sau lần được phép, chuyến chuyển sang `cleared`; 1 lần thử sau đó của vai trò có quyền trả 409 (đã cho phép, không cho phép lại).
21 lần từ chối có bản ghi `denied` trong nhật ký truy vết của chuyến.

## Bay trên bản sao số

Phương tiện `SIM0001B1TNA9549F` bay hết Khối 1 (14.77 km, 30 m/s,
493.0 giây), sinh 4931 mẫu telemetry I-07 ở 10 Hz. Cổng telemetry
chưa có (bước 4) nên mẫu chưa được nạp vào B1; phần này chỉ chứng minh chuỗi xếp lịch → cho phép cất cánh → bay chạy liền.

## Giới hạn

- Xác thực đa yếu tố được giả lập bằng claim `amr` trong token ký bằng khoá phát triển; với Keycloak thật cần bật OTP.
- "Khi trực" của Trưởng bộ phận chưa có lịch trực (bước 3).
