# Biên bản chạy AT-03 trên bản sao số (bước 3)

Chạy lúc 2026-11-27T19:53:59+00:00 bằng `tools/at_step3_twin.py` qua API HTTP của B1 (`http://localhost:8011`), mã lần chạy `f50ce3`.
Kết quả máy đọc được: `step3-at03.json`; gói văn bản mẫu: `step3-at03-package.txt`.

## Kết quả

| Bài | Tiêu chí | Kết quả |
|---|---|---|
| AT-03 | Gói dữ liệu đúng định dạng | **ĐẠT**: 10/10 mục kiểm |
| AT-03 | Trạng thái lên bản đồ ≤5 giây | **ĐẠT**: 39 lần đổi trạng thái, trung vị 116 ms, phân vị 95 208 ms, lớn nhất 210 ms |
| F-04 | Danh mục, ca trực, khối kích hoạt, ký điện tử | **ĐẠT** |
| Rào địa lý | Telemetry của bản sao số trong khối đang kích hoạt và trong tầng | **ĐẠT**: 4891 mẫu, 0 mẫu ra ngoài, cách mép gần nhất 47 m |
| Nhật ký | Chuỗi mã băm không đứt | Nguyên vẹn, 146 bản ghi |

## AT-03: gói dữ liệu kích hoạt

Kế hoạch `edda25c4-574c-4245-928c-3afc3826d5ad` lập từ lịch đã xếp ngày mai cho Khối 1 (1 chuyến), mã băm `06c99121b439b97928b1c3265c7b1e160414c3aca6923e7c9a0bed589e7a5f36`,
mã vùng ED-269 `B1106C9`. Hiệp đồng ghi gắn mã băm này. Thử kích hoạt trước giờ: HTTP 409
(chưa đến giờ: kích hoạt sớm nhất 60 phút trước giờ bắt đầu).

| Mục kiểm | Kết quả | Chi tiết |
|---|---|---|
| ba dạng cùng mã băm | đạt | `06c99121b439b97928b1c3265c7b1e160414c3aca6923e7c9a0bed589e7a5f36` |
| văn bản có chuyến HL-20261128-0001 | đạt |  |
| văn bản có mã khối, tầng, khung giờ, mã băm | đạt |  |
| văn bản có đủ toạ độ đỉnh WGS84 | đạt |  |
| GeoJSON: vòng đóng | đạt |  |
| GeoJSON: vòng ngoài ngược chiều kim đồng hồ (RFC 7946) | đạt |  |
| GeoJSON: khớp hình học khối | đạt |  |
| GeoJSON: có tầng, khung giờ, chuyến | đạt |  |
| ED-269: qua lược đồ kiểm tra | đạt |  |
| ED-269: cùng đa giác và tầng | đạt |  |

Lược đồ ED-269 dùng để kiểm (`api/schemas/ed269-uaszone.schema.json`) viết theo cấu trúc công khai của mô hình UASZone,
chưa đối chiếu văn bản chuẩn đầy đủ; cần rà lại khi có bản chuẩn và quy chế phối hợp I-05.

## AT-03: thời gian trạng thái lên bản đồ

Một tài khoản Bộ phận điều phối bay tầm thấp mở luồng sự kiện `/v1/airspace/stream` như trang bản đồ. Trưởng bộ phận lặp
lập → hiệp đồng → kích hoạt → đóng Khối 1. Thời gian đo từ lúc gửi lệnh đến lúc luồng nhận sự kiện của đúng kế hoạch
(cùng máy, không gồm đường truyền mạng của người xem).

| Lần | Lệnh kích hoạt (ms) | Kích hoạt lên luồng (ms) | Đóng lên luồng (ms) |
|---|---|---|---|
| 1 | 46.0 | 115.4 | 203.5 |
| 2 | 46.6 | 102.8 | 205.1 |
| 3 | 43.7 | 110.6 | 208.4 |
| 4 | 46.9 | 88.6 | 191.5 |
| 5 | 54.5 | 90.0 | 201.5 |
| 6 | 44.9 | 105.8 | 197.6 |
| 7 | 47.7 | 108.0 | 210.2 |
| 8 | 43.5 | 115.8 | 201.3 |
| 9 | 43.6 | 106.4 | 200.2 |
| 10 | 47.8 | 100.2 | 185.7 |
| 11 | 49.8 | 92.5 | 196.8 |
| 12 | 47.4 | 95.2 | 198.1 |
| 13 | 44.1 | 87.2 | 204.2 |
| 14 | 64.2 | 92.0 | 188.4 |
| 15 | 46.9 | 111.6 | 203.7 |
| 16 | 56.7 | 98.3 | 198.9 |
| 17 | 42.3 | 108.3 | 203.9 |
| 18 | 52.6 | 101.2 | 203.7 |
| 19 | 44.6 | 112.2 | 207.6 |
| 20 | 49.2 | 80.1 | — (giữ mở cho bước F-04) |

## F-04 trên bản sao số

Hai chuyến của bản sao số bắt đầu sau 10 phút, trong Khối 1 đang kích hoạt.

| Ca | HTTP | Điều kiện chưa đạt | Trạng thái |
|---|---|---|---|
| Cán bộ trực ban, thời tiết chưa đạt | 409 | CHECKLIST | — |
| Trưởng bộ phận chưa vào ca | 409 | ON_DUTY | — |
| Cán bộ trực ban, đủ danh mục | 200 | — | cleared |
| Trưởng bộ phận đã vào ca | 200 | — | cleared |

Bản ghi đã ký: B1 tự kiểm chữ ký hợp lệ; kiểm độc lập bằng khoá công khai
ở `/v1/signing-keys`: hợp lệ. Đóng khối khi còn chuyến đã được cho phép:
HTTP 409.

## Bay trên bản sao số

Phương tiện `SIM0001B1TNF50CE3` bay dọc Khối 1 (Km 0,05 đến Km 14,72) ở 30 m/s, 120 m so với mặt đất, 4891 mẫu I-07. Mọi mẫu nằm trong rào
địa lý của khối đang kích hoạt (sàn – trần 20–150 m so với mặt đất); rào cứng luôn có: geofence-north-block-4;
không có cách tắt rào (`can_disable: false`). Cổng telemetry làm ở bước 4, nên phép đối chiếu này chạy trong script.

## Giới hạn

- Hình học khối là dự thảo theo KML hành lang; tầng 20–150 m so với mặt đất theo demo (D3 chưa chốt).
- Hiệp đồng với cơ quan điều hành bay (I-05) chưa có quy chế: ghi tay số công văn, mã băm gói gắn với lần hiệp đồng.
- Ký điện tử là chữ ký hệ thống (ES256) gắn với người ký đã xác thực đa yếu tố và đăng nhập lại; chưa phải chữ ký số theo
  Luật Giao dịch điện tử.
- Dải hạn chế 10 km tính từ biên giới chờ dữ liệu đường biên giới chính thức.
