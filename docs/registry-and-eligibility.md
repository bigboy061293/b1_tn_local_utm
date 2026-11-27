# Đăng ký, hồ sơ và điều kiện xếp lịch (bước 2b)

Theo Đặc tả B1 v0.1: F-01, Mục 4.3 (điều kiện chặn), Mục 5.3 (CT-1…8), Mục 6 (I-02). Mã: `b1/registry`,
`b1/connectors/i02.py`.

## Luồng hồ sơ

1. Trưởng bộ phận hoặc Bộ phận điều phối bay tầm thấp lập hồ sơ khách hàng gắn với một tổ chức, đặt hạng A/B/C.
2. Tài khoản khách hàng khai người điều khiển, phương tiện (UAS ID, số đăng ký Việt Nam, khối lượng cất cánh tối đa,
   tốc độ lớn nhất, thiết bị quản lý hoạt động bay, phiên bản phần mềm và cấu hình vô tuyến) và nộp giấy tờ.
3. Cán bộ xác nhận hoặc từ chối từng giấy tờ. Số đăng ký có thể tra qua mô-đun I-02; khớp sê-ri và còn hiệu lực thì
   giấy chứng nhận đăng ký được xác nhận tự động (`verified_via = I-02`).
4. Kỹ thuật viên bay thử ghi kết quả CT-1…8. Biên bản hiệu lực 12 tháng và gắn với cấu hình lúc thử: khách đổi
   phần mềm bay tự động hay cấu hình vô tuyến thì mọi kết quả cũ mất hiệu lực.
5. Dịch vụ xếp lịch gọi `RegistryService.eligibility(...)`; thiếu điều kiện nào thì chặn và ghi lý do (AT-01, bước 2c).

Khách hàng chỉ thấy và sửa hồ sơ của mình; cơ quan quản lý và cán bộ đọc được tất cả, không sửa được. Mọi thay đổi
và mọi lần truy cập trái phép ghi nhật ký truy vết.

## Điều kiện chặn

Chỉ giấy tờ đã xác nhận và còn hiệu lực trọn khung bay mới được tính.

| Mã | Điều kiện |
|---|---|
| `OWNERSHIP` | Phương tiện, người điều khiển phải thuộc khách hàng đang hoạt động |
| `REGISTRATION` | Thiếu Giấy chứng nhận đăng ký hoặc đăng ký tạm thời còn hiệu lực |
| `TECHNICAL_STATUS` | Phương tiện đang thử nghiệm thiếu Giấy xác nhận tình trạng kỹ thuật còn hiệu lực |
| `PILOT_LICENCE` | Người điều khiển thiếu giấy phép (bay ngoài tầm nhìn hoặc phương tiện từ 2 kg) |
| `FLIGHT_MGMT_DEVICE` | Thiếu thiết bị quản lý hoạt động bay đã được xác nhận |
| `PERMIT` | Thiếu giấy phép thử nghiệm hoặc phép bay còn hiệu lực |
| `CONFORMANCE` | Chưa đạt đủ CT-1…CT-8 còn hiệu lực với cấu hình hiện tại |
| `INSURANCE` | Thiếu bảo hiểm còn hiệu lực (kiểm lại tại T − 3 ngày làm việc) |

`INSURANCE` chỉ kiểm ở lần xác nhận cuối (T − 3 ngày làm việc, Mục 4.3); các mã còn lại kiểm ngay khi nhận yêu cầu
và khi xếp lịch.

## I-02 giả lập

I-02 chưa có giao thức. `B1_I02_CONNECTOR=mock` đọc `config/mock/i02_registry.json` (dữ liệu giả, có sẵn ca hợp
lệ, bị thu hồi, hết hạn); `manual` trả 503 và cán bộ đối chiếu thủ công. Khi Bộ Công an công bố API chỉ thêm một lớp
kết nối mới, không sửa dịch vụ đăng ký.

## Việc còn mở

1. Ai được lập hồ sơ khách và xác nhận giấy tờ: bản này cho Trưởng bộ phận và Bộ phận điều phối bay tầm thấp; Đặc tả chưa ghi.
2. Giấy phép thử nghiệm hoặc phép bay (`PERMIT`) có phải có ngay khi đặt chỗ không, hay chỉ trước T − 3: F-01 ghi
   thiếu thì chặn xếp lịch, nên bản này chặn ngay. Nếu phép bay thường được cấp sau khi có lịch thì chuyển sang kiểm ở T − 3.
3. Tệp giấy tờ: hiện chỉ lưu tham chiếu `file_ref`; kho tệp có mã hoá làm ở bước 6 cùng quy chế dữ liệu.
