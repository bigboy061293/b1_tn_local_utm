# Kết quả thử khái niệm bước 1 và đề xuất D1

Ngày chạy: 25/11/2026. Cách dựng lại: [`poc/README.md`](../poc/README.md). Số liệu gốc: [`poc/results/`](../poc/results/).

**Kết luận:** dùng lại InterUSS DSS cho DSS hành lang, chạy trên YugabyteDB thay cho CockroachDB (đã thử, kết quả như nhau). DSS qua bộ kiểm thử công khai của InterUSS cho cả F3548 và F3411; mọi kiểm tra trượt đều do môi trường thử cục bộ (tên máy nội bộ, http), không phải lỗi chức năng. Phần USS hành lang chưa đủ số liệu để quyết định: phép thử này dùng USS giả lập của InterUSS, chưa thử Flight Blender.

## 1. Bộ kiểm thử InterUSS `uss_qualifier`

| Bộ | Chuẩn | Thời gian | Kiểm tra đạt | Trượt | Nguyên nhân trượt |
|---|---|---|---|---|---|
| `f3548_self_contained` | ASTM F3548-21 | 1,4 phút | 6.324 | 4 | DSS dùng tên máy nội bộ, không có địa chỉ công khai |
| `netrid_v22a` | ASTM F3411-22a | 7,2 phút | 10.711 | 7 | Như trên (4); hệ sinh thái cục bộ chạy http thay vì https (3) |

Chạy lại với DSS trên **YugabyteDB 2025.1.2.1** (Apache 2.0): kết quả giống hệt (F3548 6.324 đạt / 4 trượt, 1,5 phút; F3411 10.711 đạt / 7 trượt, 7,1 phút; cùng nguyên nhân). Kịch bản B1 ở Mục 2 cũng đạt cả 4 trường hợp với ý định co vào 50 m. Như vậy đổi CSDL không làm thay đổi hành vi DSS.

Hai lỗi này sẽ hết khi triển khai có tên miền và TLS thật; cần chạy lại bộ kiểm thử trên môi trường đó trước nghiệm thu.

Lưu ý: theo đặc tả (Mục 9), bộ kiểm thử này mới tự động kiểm khoảng 30% yêu cầu phía USS của F3548. Phần giám sát tuân thủ, ràng buộc vùng trời và nhật ký phải tự viết thêm bài kiểm tra.

## 2. Kịch bản B1: hai USS cùng xin một khối

Dùng hình học `config/corridor/blocks.geojson`, khung bay 2 giờ, cùng bậc ưu tiên, hai USS giả lập (uss1, uss2) qua 2 DSS khác nhau.

| Trường hợp | Mong đợi | Ý định đúng ranh khối | Ý định co vào 50 m |
|---|---|---|---|
| A xin Khối 1 | Chấp nhận | Đạt | Đạt |
| B xin Khối 1 trùng giờ | Từ chối | Đạt | Đạt |
| B xin Khối 1 sau khung của A | Chấp nhận | Đạt | Đạt |
| B xin Khối 2 cùng giờ với A | Chấp nhận | **Trượt: bị từ chối** | Đạt |

Thời gian phản hồi mỗi yêu cầu qua DSS: 78–272 ms trên máy thử.

**Phát hiện:** hai khối kề nhau có chung cạnh (đường phân giác hoặc đường vuông góc), nên F3548 coi hai ý định bay đúng ranh khối là giao nhau. Muốn cho 2 chuyến đồng thời ở hai khối kề nhau (Mục 2.3), ý định bay phải co vào trong ranh khối một khoảng. Đề xuất: co vào bằng vùng đệm biên, tối thiểu 50 m (mức vượt ranh cho phép của AT-07), và chốt cùng D6 (độ rộng vùng bay bình thường và vùng dự phòng).

## 3. Flight Blender làm USS hành lang (chưa kết luận)

Flight Blender (Apache 2.0) được build từ mã nguồn, thay base image Wolfi bằng `python:3.12-slim` vì môi trường thử không tải được từ `cgr.dev`. Bộ kiểm thử dùng cấu hình đi kèm Flight Blender, viết cho `uss_qualifier` v0.30.0. Môi trường thử chỉ có sẵn `uss_qualifier` 0.38.0 và không tải được v0.30.0 do giới hạn lượt tải image, nên kết quả dưới đây **chưa dùng để kết luận được**:

| Bộ | Kết quả | Ghi chú |
|---|---|---|
| F3548-21 | Không chạy được | Cấu hình v0.30.0 không tương thích với 0.38.0 (tên tài nguyên đã đổi) |
| F3411-22a | 7.558 đạt, 94 trượt (99%) | 60 "Missing flight", 24 "Disconnected flight is shown as such", 8 do môi trường cục bộ. Chưa rõ trượt do Flight Blender hay do lệch phiên bản bộ kiểm thử |

README của Flight Blender công bố qua cả hai bộ trong CI của dự án đó, với `uss_qualifier` v0.30.0; mình chưa kiểm chứng độc lập con số này. Cần chạy lại đúng phiên bản, ví dụ trong GitHub Actions của repo B1, nơi tải được image.

## 4. Đối chiếu 4 điều kiện dùng thành phần có sẵn (Mục 3.4)

| Điều kiện | InterUSS DSS | Ghi chú |
|---|---|---|
| Đạt bộ kiểm thử tuân thủ và nghiệm thu | Đạt bộ kiểm thử công khai (Mục 1) | Nghiệm thu AT-13 còn phải thử thay CSDL |
| Giấy phép dùng lâu dài, không phí ngoài dự toán | Apache 2.0 | CockroachDB là BSL 1.1, không đạt; YugabyteDB Apache 2.0, đạt |
| Thay được qua giao diện đã công bố | Đạt: giao diện DSS là API chuẩn F3548/F3411 | Có thể thay bằng DSS khác cùng chuẩn |
| Thuyết minh trong hồ sơ thiết kế | Cần làm | Dùng tài liệu này làm cơ sở |

## 5. Việc tiếp theo

1. Chạy lại bộ kiểm thử cho Flight Blender đúng phiên bản `uss_qualifier` v0.30.0 (Mục 3), rồi chạy kịch bản B1 với Flight Blender làm một trong hai USS.
2. Network Remote ID toàn tuyến: đẩy telemetry từ bản sao số (`twin/sim.py`) vào USS qua F3411, kiểm ≥1 Hz trên 100% chiều dài (AT-05).
