# Xếp lịch bay (bước 2c)

Theo Đặc tả B1 v0.1: F-02, Mục 4.3 (quy tắc xếp lịch), Mục 2.3 (phạm vi khai thác), AT-01. Mã: `b1/scheduling`.

## Luồng

1. **Khách nộp yêu cầu** gồm phương tiện, người điều khiển, nhóm ưu tiên 1–4, bay ngoài tầm nhìn hay không, các khối,
   và tối đa 5 ngày liên tục, mỗi ngày một hoặc nhiều khung trong 4 khung 2 giờ. Khung phải từ ngày mai đến 60 ngày tới.
   Khối chưa mở bị từ chối (6 tháng đầu chỉ Khối 1).
2. **Kiểm hồ sơ ngay** bằng dịch vụ đăng ký (`docs/registry-and-eligibility.md`):
   - đủ: cấp số `HL-YYYYMMDD-NNNN`, ghi thời điểm đến tới từng giây;
   - thiếu: cấp số tạm `TAM-YYYYMMDD-NNNN`, giữ chỗ 3 ngày làm việc, **ghi lý do chặn từng mã quy tắc** vào bảng
     `scheduling.block_reason` và nhật ký truy vết. Khách bổ sung trong hạn thì nhận số chính thức **giữ nguyên thời điểm
     đến**; quá hạn thì yêu cầu hết hiệu lực.
3. **Kỳ xếp lịch 16:00** các ngày làm việc (`python -m b1.scheduling.cron`, hoặc Trưởng bộ phận bấm chạy):
   - kiểm lại mọi số tạm và mọi yêu cầu đang chờ; thiếu thì chặn và ghi lý do lần nữa;
   - xếp theo thứ tự: nhóm ưu tiên → quyền đặt lại còn hạn → hạng khách hàng A, B, C → thời điểm đến;
   - giới hạn: chỉ khối đang mở; mỗi khối 1 chuyến mỗi khung; toàn hành lang tối đa 2 chuyến mỗi khung;
   - khung được xếp tạo chuyến bay ở dịch vụ chuyến bay (trạng thái `scheduled`, chờ cho phép cất cánh);
   - lưu mọi quyết định và lý do vào `scheduling_run.decisions`, kèm mã băm đầu vào, để giải trình.
4. **Xác nhận lần cuối T − 3 ngày làm việc**: kiểm lại hồ sơ kèm bảo hiểm; thiếu thì huỷ khung, huỷ chuyến, ghi lý do.
5. **Công khai** cho mọi tài khoản: khung còn trống (`/availability`), số thứ tự và vị trí trong hàng chờ (`/queue`),
   không lộ tên khách hàng.

Ngày làm việc: thứ Hai đến thứ Sáu trừ ngày lễ trong thông số `holidays`. Mọi thông số ở Mục 4.3 sửa được qua
`PUT /v1/scheduling/params/{key}` (Trưởng bộ phận, có nhật ký), không sửa mã.

## AT-01 trên bản này

`tests/test_at01_block_incomplete.py` thử từng điều kiện chặn (8 ca thiếu hồ sơ, cộng bảo hiểm ở T − 3, cộng hồ sơ mất
hiệu lực sau khi đã có số): 100% bị chặn, không ca nào tạo chuyến bay, mọi ca có lý do đúng mã trong bảng lý do và
trong nhật ký truy vết. Chạy lại trên bản sao số ở bước 2e.

## Chưa làm, còn mở

1. **Hạn mức theo tuần:** khung nhóm 3 dành cho hạng C ≥30%; trần 40% khung nhóm 3 mỗi khách trên một khối khi còn
   khách chờ; trần giữ chỗ định kỳ nhóm 2 50% và mức sử dụng tối thiểu 80%. Cần định nghĩa 4 nhóm ưu tiên của Phụ lục
   05 trước; đề xuất làm cùng bước 2d hoặc ngay sau khi có Phụ lục 05.
2. **Lịch sử sử dụng** (tiêu chí xét cuối) và **ngưỡng vắng mặt, huỷ muộn** (2 lần vắng hoặc 3 lần huỷ muộn trong 90
   ngày): cần dữ liệu cất, hạ cánh thực tế của bước 4.
3. **Nhắc việc T − 7, T − 3, T − 1**: làm ở bước 2d cùng cổng khách hàng (kênh thông báo).
4. **Giờ của 4 khung** đang là đề xuất (06:00, 08:30, 13:30, 16:00), là thông số `slot_starts`.
5. **Không đẩy lịch đã xếp:** yêu cầu ưu tiên cao đến sau không lấy lại khung đã xếp cho người khác; chỉ nhiệm vụ an ninh –
   quốc phòng (F-08, bước 5) huỷ hoặc dời hàng loạt.
6. **Chuyến trong tầm nhìn:** Đặc tả chỉ giới hạn 1 chuyến ngoài tầm nhìn mỗi khối; bản này áp 1 chuyến mỗi khối cho mọi
   chuyến cho an toàn, nới bằng thông số khi có quy tắc.
