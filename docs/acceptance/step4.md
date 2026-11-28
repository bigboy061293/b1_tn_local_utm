# Biên bản chạy bước 4 trên bản sao số: telemetry, giám sát, cảnh báo, CT-1…8

Chạy từ 2026-11-28T08:26:09Z đến 2026-11-28T08:57:15Z bằng `tools/at_step4_twin.py --duration 300 --ct-duration 1860 --ui`
trên CSDL mới, qua API HTTP của B1 (`http://localhost:8011`), MQTT (mosquitto 2.0.20), MAVLink v2 qua UDP, và cổng vào TLS 1.3 hai chiều
(nginx 1.25, `config/ingress/telemetry-nginx.conf`). Kết quả máy đọc được: `step4.json`. Ảnh trang `/ops/live`: `../img/ops-live.png`.

## Kết quả

| Hạng mục | Tiêu chí | Kết quả |
|---|---|---|
| Độ trễ cảnh báo | Từ giờ nguồn của mẫu vi phạm đến lúc cảnh báo tới luồng `/v1/alerts/events` ≤ 2 giây | **ĐẠT**: 46 cảnh báo, trung vị 182.5 ms, p95 251.5 ms, lớn nhất 261.7 ms |
| Độ trễ lên bản đồ | Cảnh báo hiện trên trang `/ops/live` (Playwright, trình duyệt thật) | **ĐẠT**: 49 cảnh báo, p95 270 ms từ giờ nguồn, không lỗi trang |
| Cảnh báo ngoài dự kiến | Mọi cảnh báo phải ứng với một lỗi đã cài | **ĐẠT**: 0 |
| Ghi telemetry | Từ lúc B1 nhận đến lúc ghi CSDL | trung vị 46.0 ms, p95 94.0 ms, lớn nhất 480.0 ms (2961 mẫu) |
| Đối chiếu Remote ID | Remote ID so với telemetry cùng thời điểm, ngưỡng 100.0 m | khớp 319/319 (tỉ lệ 1.0), lệch lớn nhất 2.5 m, 4 bản tin ngoài khoảng có telemetry |
| CT-1…8 | Phiên chính thức 31 phút do Kỹ thuật viên bay thử mở, khách gửi qua cổng vào TLS | **8/8 ĐẠT**, ghi 8 kết quả vào hồ sơ phương tiện |
| Nhật ký | Chuỗi mã băm không đứt | 49 bản ghi, nguyên vẹn |

## Bay trên bản sao số

Ba chuyến bay cùng lúc trong Khối 1, mỗi chuyến một kênh: HTTPS (2961 mẫu), MQTT (601), MAVLink v2
có ký (601). Chuyến HTTPS bay 300 giây ở 10 Hz và được cài lỗi theo chu kỳ 30 giây: lệch tuyến 600 m rồi 800 m
(vùng bình thường 500 m, vùng dự phòng 750 m), mất C2 2 giây, vượt trần 50 m. Cuối chuyến có pin yếu, và 40 mẫu bị cố ý bỏ để
kiểm mất telemetry. Hai chuyến MQTT và MAVLink bật cờ an toàn để đo độ trễ trên từng kênh. Năm trạm RIS gửi 3488 bản ghi I-06
(ADS-B, Remote ID, trạng thái), trong đó RIS-1 chuyển sang đường truyền di động từ giây 100 đến 140.

Độ trễ cảnh báo theo kênh (từ giờ nguồn, ms):

| Kênh | Số cảnh báo | Trung vị | p95 | Lớn nhất |
|---|---|---|---|---|
| mqtt | 2 | 133.1 | 161.8 | 161.8 |
| https | 42 | 193.9 | 251.5 | 261.7 |
| mavlink | 2 | 86.9 | 87.4 | 87.4 |

Cảnh báo theo kênh và loại:

| Kênh | Loại | Số lần |
|---|---|---|
| https | C2_LINK | 8 |
| https | CEILING | 8 |
| https | LATERAL_DEVIATION | 16 |
| https | LOW_BATTERY | 1 |
| https | SAFETY_FLAG | 9 |
| https | TELEMETRY_STALE | 2 |
| mavlink | SAFETY_FLAG | 2 |
| mavlink | TELEMETRY_STALE | 1 |
| mqtt | SAFETY_FLAG | 2 |
| mqtt | TELEMETRY_STALE | 1 |
| other | C2_LINK | 1 |
| other | SAFETY_FLAG | 2 |
| station | STATION | 1 |

"other" là cảnh báo phát ra ngoài đường mẫu thời gian thực (giải toả, nâng mức, tác vụ định kỳ). STATION là RIS-1 xuống mức L1
khi chạy bằng đường di động. Trục thời gian 10 Hz của chuyến HTTPS có 2961 điểm đo, 0 điểm nội suy và
40 điểm khoảng trống, đúng bằng 40 mẫu bị bỏ. Vết ADS-B `VNA123` được gộp từ 5 trạm, 300 lần báo.
Luồng trực tiếp `/v1/live/stream` đạt 4.68 Hz (thiết kế 5 Hz).

## CT-1…8

Bộ phát mẫu của khách (`tools/ct_client.py`) gửi trong 1860 giây ở 10 Hz qua `https://localhost:8443` với chứng chỉ khách `O=Khach AT4,CN=kh-at4`.
Phương tiện đứng yên tại điểm RTK. Cờ c2_lost bật ở giây 60, geofence_breach ở giây 90. Đoạn chạy tải từ giây 300 đến 600 dùng 4 luồng
gửi bù lô 200 mẫu liên tục. Ngắt kết nối 60 giây từ giây 1620, sau đó nối lại bằng phiên TLS mới và gửi bù.
Bộ phát: 18001 lô thời gian thực, 0 lỗi, 600 mẫu gửi bù. Đoạn tải gửi 1358 lô, trong đó 1056 lô bị B1 chặn 429.

| Bài | Đo được | Kết quả |
|---|---|---|
| CT-1 | 18601 mẫu, 0 mẫu bị loại, 1860.0 giây | **ĐẠT** |
| CT-2 | 10.0 Hz, 100.0% khoảng ≤ 300 ms, khoảng lớn nhất 0.1 s (sau khi bỏ đoạn ngắt có chủ đích) | **ĐẠT** |
| CT-3 | Nhãn nguồn tăng liên tục; giờ B1 nhận trừ giờ nguồn: trung vị 7.0 ms, p95 14.6 ms | **ĐẠT** |
| CT-4 | 100.0% mẫu trong 5 m quanh điểm RTK, p95 2.86 m, cửa sổ 1860.0 s | **ĐẠT** |
| CT-5 | c2_lost → cảnh báo sau 0.014 s; geofence_breach → cảnh báo sau 0.016 s | **ĐẠT** |
| CT-6 | TLSv1.3, chứng chỉ khách SUCCESS (tiêu đề do cổng vào gắn) | **ĐẠT** |
| CT-7 | Trong đoạn tải: theo nhãn nguồn 10.0 Hz, 100.0% ≤ 300 ms; theo giờ nhận 10.0 Hz, 99.7% ≤ 300 ms, khoảng lớn nhất 0.475 s; trễ p95 104.4 ms | **ĐẠT** |
| CT-8 | Nối lại sau 0.03 s, gửi bù 600 mẫu với nhãn gốc, khoảng lớn nhất sau gửi bù 0.1 s | **ĐẠT** |

## Phát hiện trong các lần chạy trước và đã sửa

- **Tải gửi bù làm nghẽn mẫu thời gian thực.** Lần chạy chính thức đầu chưa có hạn mức gửi bù. Bốn luồng gửi bù liên tục
  (10 822 lô trong 5 phút) chiếm hết CPU của tiến trình B1, chủ yếu ở bước kiểm lược đồ (khoảng 0,2 ms mỗi mẫu). Mẫu thời gian thực
  của phiên CT vì thế tới trễ tới 19 phút, và độ trễ ghi của các chuyến khác lên 25 giây. CT-3 phát hiện ra lỗi này. CT-7 khi ấy vẫn
  "đạt" vì chỉ chấm theo nhãn nguồn. Đã sửa ba chỗ:
  - Hạn mức gửi bù riêng từng chuyến: 200 mẫu/giây, xô 600 mẫu, vượt thì trả 429 kèm Retry-After. Lần chạy thứ hai để 1000 mẫu/giây
    thì vẫn trễ p95 1,8 giây.
  - CT-7 chấm thêm theo giờ B1 nhận.
  - Bộ phát mẫu lùi theo Retry-After.
- **Remote ID gán nhầm vào phiên CT** khi phiên CT dùng cùng UAS ID với một chuyến đang bay. Nay Remote ID chỉ đối chiếu với chuyến bay.
- **Lịch thử của bộ phát mẫu bị chồng lên nhau** khi chạy ngắn: đoạn ngắt nuốt mất hai lần bật cờ. Bộ phát nay báo lỗi ngay khi lịch chồng.

## Giới hạn

- Bản sao số chạy trên một máy với một tiến trình B1. Độ trễ ở đây chưa gồm đường truyền thật từ hành lang (4G/5G, cáp quang của trạm).
- Ngưỡng lệch tuyến 500/750 m, trần và tầng là số demo (D6, D3 chưa chốt). Cao độ địa hình và độ lệch geoid đang là một hằng số
  cho cả hành lang (cấu hình `terrain_elev_m`, `geoid_undulation_m`), chưa dùng mô hình địa hình.
- Đồng hồ: CT-3 so giờ nguồn với đồng hồ máy B1. Trên hiện trường, B1 cần đồng bộ NTP/PTP theo nguồn thời gian của hành lang.
- Phiên CT do khách tự mở chỉ cho kết quả tham khảo. Chỉ phiên do Kỹ thuật viên bay thử mở mới ghi vào hồ sơ phương tiện.
