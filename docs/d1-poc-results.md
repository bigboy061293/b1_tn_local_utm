# Kết quả thử khái niệm bước 1 và đề xuất D1

Ngày chạy: 25/11/2026. Cách dựng lại: [`poc/README.md`](../poc/README.md). Số liệu gốc: [`poc/results/`](../poc/results/).

**Kết luận:** dùng lại InterUSS DSS cho DSS hành lang, chạy trên YugabyteDB thay cho CockroachDB (đã thử, kết quả như nhau). DSS qua bộ kiểm thử công khai của InterUSS cho cả F3548 và F3411; mọi kiểm tra trượt đều do môi trường thử cục bộ (tên máy nội bộ, http), không phải lỗi chức năng. Với USS hành lang, Flight Blender qua gần hết bộ F3548 nhưng còn lỗi ở Network Remote ID (Mục 3).

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

## 3. Flight Blender làm USS hành lang

Flight Blender (Apache 2.0) được build từ mã nguồn, thay base image Wolfi bằng `python:3.12-slim` vì môi trường thử không tải được từ `cgr.dev` (xem `poc/flight_blender/`). Lần chạy cuối dùng đúng phiên bản bộ kiểm thử mà Flight Blender nhắm tới (`uss_qualifier` v0.30.0, DSS v0.22.0), qua script kiểm thử đi kèm Flight Blender.

| Bộ | Đạt | Trượt | Nguyên nhân trượt |
|---|---|---|---|
| F3548-21 | 4.606 | 11 (mức đỏ) | Cả 11 lần là Flight Blender trả **HTTP 401** khi nhận yêu cầu lập kế hoạch bay; các lần khác cùng kịch bản đạt. Nhiều khả năng do xác thực trong môi trường thử (máy chủ OAuth giả lập được build lại từ mã nguồn DSS mới hơn), chưa xác nhận |
| F3411-22a | 7.579 | 86 (mức vàng) | Chủ yếu "Missing flight" và "Disconnected flight is shown as such": chuyến bay không hiện ra khi hiển thị qua Flight Blender. Lỗi này lặp lại ở cả bộ kiểm thử v0.30.0 lẫn 0.38.0, trong khi USS giả lập của InterUSS không gặp, nên nhiều khả năng là hạn chế thật của Flight Blender ở phần Network Remote ID |

**Đánh giá:** Flight Blender lập kế hoạch bay theo F3548 tốt ở mức bộ kiểm thử, nhưng phần Network Remote ID còn lỗi mà B1 sẽ phải tự sửa, vì Remote ID toàn tuyến là yêu cầu bắt buộc (AT-05). Phương án hợp lý cho D1 là dùng Flight Blender làm nền phần xếp lịch và ý định bay, kèm một đầu việc sửa hoặc thay phần Remote ID. Cách khác là tự xây USS trên nền thư viện của InterUSS. Cần thêm một lần chạy có OAuth đúng phiên bản để loại trừ 11 lỗi 401.

## 4. Network Remote ID toàn tuyến (tiền đề AT-05)

Kịch bản `poc/netrid_full_route.py`: phương tiện của bản sao số bay hết tim tuyến 54,71 km ở 30 m/s, cao 120 m AGL
(1.825 giây). Telemetry 1 Hz được nạp vào USS cung cấp Remote ID (mock_uss `ridsp`), USS này đăng ký vùng dịch vụ
(ISA) lên DSS. Một USS hiển thị (mock_uss `riddp`) bám theo phương tiện mỗi giây với khung nhìn 0,9 × 0,9 km, đi qua
DSS để tìm và lấy dữ liệu như một màn hình giám sát thật. DSS v0.24.0 trên YugabyteDB.

| Chỉ số (lần chạy 25/11/2026, `poc/results/netrid_full_route.json`) | Kết quả | Chỉ tiêu |
|---|---|---|
| Số giây lấy mẫu / số giây bay | 1.811 / 1.825 | |
| Tỷ lệ số giây thấy chuyến bay | **100%** (1.811/1.811), không đoạn Km nào hở | 100% chiều dài |
| Độ trễ dữ liệu khi hiển thị, p50 / p95 / lớn nhất | 0,58 s / 0,70 s / 1,36 s | ≤3 s |
| Sai số vị trí so với telemetry đã nạp, p95 | 0,01 m | |
| Thời gian một truy vấn hiển thị, p95 | 0,70 s | |

Độ trễ dữ liệu = lúc USS hiển thị trả kết quả trừ thời điểm của điểm telemetry khớp vị trí hiển thị (API hiển thị
F3411 không trả thời điểm, nên suy ra từ vị trí; độ phân giải 1 s). Vị trí hiển thị luôn là điểm của giây hiện tại,
nên gần như toàn bộ độ trễ là thời gian truy vấn qua DSS và USS cung cấp. 14 giây không lấy mẫu là những giây có
truy vấn trước đó chậm hơn 1 s.

Hai lỗi của USS giả lập (không phải của DSS) gặp trong các lần chạy trước và cách tránh:

1. mock_uss duyệt toàn bộ telemetry đã nạp ở mỗi truy vấn: nạp một lần cả chuyến 30 phút làm truy vấn chậm ~2 s
   (chuyến 150 s: ~0,3 s), độ trễ p95 3,4 s. Cách tránh: nạp cuốn chiếu từng đoạn 120 s và gỡ đoạn đã bay qua.
   USS thật nhận telemetry trực tiếp nên không có độ trễ này.
2. Khi một ISA bị xoá, DSS báo cho đăng ký của USS hiển thị; mock_uss 0.38 lỗi khi đọc thông báo xoá
   (`ValueError: No valid representation was specified for ISA`) và trả HTTP 400 khoảng 15 giây (106 giây mất
   trong lần chạy gỡ đoạn sớm). Cách tránh: chỉ gỡ đoạn 300 s sau khi bay qua.

Đánh giá: luồng Network Remote ID qua DSS đạt chỉ tiêu trên toàn tuyến với một phương tiện. Còn phải kiểm khi có
USS cung cấp thật (Flight Blender hoặc USS hành lang) thay cho mock_uss, hai phương tiện đồng thời, và khung nhìn
lớn hơn 2 km (F3411 chỉ trả cụm, không trả chi tiết từng chuyến).

## 5. Đối chiếu 4 điều kiện dùng thành phần có sẵn (Mục 3.4)

| Điều kiện | InterUSS DSS | Ghi chú |
|---|---|---|
| Đạt bộ kiểm thử tuân thủ và nghiệm thu | Đạt bộ kiểm thử công khai (Mục 1) | Nghiệm thu AT-13 còn phải thử thay CSDL |
| Giấy phép dùng lâu dài, không phí ngoài dự toán | Apache 2.0 | CockroachDB là BSL 1.1, không đạt; YugabyteDB Apache 2.0, đạt |
| Thay được qua giao diện đã công bố | Đạt: giao diện DSS là API chuẩn F3548/F3411 | Có thể thay bằng DSS khác cùng chuẩn |
| Thuyết minh trong hồ sơ thiết kế | Cần làm | Dùng tài liệu này làm cơ sở |

## 6. Việc tiếp theo

1. Tìm nguyên nhân 11 lỗi 401 của Flight Blender (Mục 3), khoanh vùng lỗi Remote ID, rồi chạy kịch bản B1 với Flight Blender làm một trong hai USS.
2. ~~Network Remote ID toàn tuyến~~ (xong, Mục 4). Lặp lại với USS cung cấp thật và hai phương tiện đồng thời khi có USS hành lang.
