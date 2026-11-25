# Mô hình dữ liệu B1 – bản nháp 0.1

Theo Đặc tả tóm tắt B1 v0.1, Mục 7.1 [đề xuất]. Mỗi bảng thuộc đúng một dịch vụ; dịch vụ khác chỉ đọc qua API (nguyên tắc AT-13, không lớp nào truy cập thẳng cơ sở dữ liệu của lớp khác).

**Quy ước chung:** khoá chính UUID v4; thời gian `timestamptz` UTC có mili giây; hình học PostGIS `geometry(…, 4326)`; độ cao luôn đi cặp `(value_m, reference)` với `reference ∈ {WGS84_ELLIPSOID, AMSL, AGL}`; mọi bảng có `created_at`, `updated_at`, `version`.

## 1. Danh tính và nhật ký (dịch vụ Danh tính và nhật ký, F-12)

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `user_account` | id, unified_id (I-08), display_name, org_id, mfa_enabled, expires_at, status | Cấm tài khoản dùng chung; tài khoản khách tự hết hạn khi hết đợt thử |
| `role_assignment` | user_id, role, area_scope, valid_from, valid_to | 8 vai trò theo Mục 1.1; phân quyền theo khu vực để mở rộng toàn tỉnh |
| `audit_log` | seq, at, actor_id, action, target_type, target_id, source_ip, before, after, prev_hash, hash | Chỉ ghi thêm; `hash = SHA-256(prev_hash ‖ bản ghi)`; lưu 5 năm |

## 2. Đăng ký và hồ sơ (F-01)

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `customer` | id, name, tier (A/B/C), usage_history | Hạng khách hàng dùng khi xét trùng khung |
| `pilot` | id, customer_id, unified_id, licences[] | Giấy phép bắt buộc khi bay ngoài tầm nhìn hoặc phương tiện từ 2 kg |
| `aircraft` | id, customer_id, uas_id, vn_registration_no, mtow_kg, max_speed_ms, weather_limits (jsonb) | Số đăng ký Việt Nam theo Thông tư 78/2026/TT-BCA là bắt buộc |
| `document` | id, owner_type, owner_id, kind, number, valid_from, valid_to, file_ref, verified_by, verified_via (manual / I-02) | Giấy chứng nhận đăng ký, giấy xác nhận kỹ thuật, giấy phép… |
| `conformance_result` | id, aircraft_id, test (CT-1…CT-8), passed, measured (jsonb), report_ref, valid_until | Hiệu lực 12 tháng hoặc đến khi đổi phương tiện, phần mềm, cấu hình vô tuyến |

## 3. Xếp lịch (F-02)

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `flight_request` | id, queue_no (HL-YYYYMMDD-NNNN), queue_at (đến giây), provisional, priority_group (1–4), customer_id, aircraft_id, pilot_id, slots[], status | Hồ sơ chưa đủ: số tạm, giữ chỗ 3 ngày làm việc |
| `slot` | id, area, block_ids[], starts_at, ends_at (2 giờ) | 4 khung mỗi ngày |
| `scheduling_run` | id, run_at (16:00), inputs_hash, decisions (jsonb) | Lưu lý do xếp/không xếp để giải trình |
| `block_reason` | request_id, rule, detail, at | Mọi lần chặn hoặc từ chối ghi nhật ký (AT-01) |
| `scheduling_param` | key, value, effective_from | Thông số Mục 4.3, hiệu chỉnh mỗi 6 tháng |

## 4. Vùng trời và chuyến bay (F-03, F-04, USS)

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `airspace_block` | id, name, geom, km_start, km_end, floor, ceiling, status (closed / active), config_version | Nạp từ `config/corridor/blocks.geojson` |
| `airspace_constraint` | id, kind, geom, floor, ceiling, hard (bool), source | Trần theo Km 34, ranh Bắc khối 4, dải 10 km biên giới |
| `block_activation` | id, block_id, flight_id, window, coordination_ref, package_text, package_geojson, package_ed269, activated_by, closed_by | Gói kích hoạt gửi kèm công văn hiệp đồng (I-05) |
| `flight` | id, request_id, state, takeoff_clearance_by, takeoff_clearance_sig, checklist (jsonb), landed_at | Chỉ 2 vai trò được cho phép cất cánh (AT-04) |
| `operational_intent` | id, flight_id, dss_ref, ovn, priority, volumes (4D, jsonb), state (Accepted, Activated, Nonconforming, Contingent, Ended) | Theo ASTM F3548 |

## 5. Dữ liệu thời gian thực (F-05, F-06)

Lưu chuỗi thời gian, phân vùng theo ngày.

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `telemetry_sample` | flight_id, uas_id, t_source, t_station, t_ingest, interpolated, 11 trường I-07 | Ba nhãn thời gian (D6) |
| `remote_id_msg` | source (network / broadcast), station_id, uas_id, t_source, t_station, t_ingest, position, altitude, rssi | Broadcast thu tại 5 trạm |
| `adsb_track` | icao24, t_window, best_msg, stations[] | Gộp vết cửa sổ 200 ms |
| `station_status`, `weather_obs` | station_id, t, kind (measured / interpolated / extrapolated / forecast), values | |
| `event` | id, flight_id, kind, level, raised_at, displayed_at, acked_by, outcome (true / false alarm) | Đo độ trễ cảnh báo và tỷ lệ cảnh báo đúng |
| `stop_order` | id, level (1–3), scope_id, issued_by, issued_at, delivered_at[], reopened_confirmed_by | Thang dừng (AT-08) |

## 6. Báo cáo và bằng chứng (F-09, F-10, F-11)

| Bảng | Trường then chốt | Ghi chú |
|---|---|---|
| `report` | id, kind (incident / monthly / ad hoc / flight test), flight_id, version, status (draft / signed), signed_by, sha256 | |
| `evidence_package` | id, flight_id, manifest (jsonb), sha256, storage_ref, sealed_at | Phát lại đọc đúng gói báo cáo đã dùng |
| `data_class_policy` | class (personal / technical / security), retention_days, access_roles | Thời hạn chờ UBND tỉnh |

## Câu hỏi còn mở

- Ba nhãn thời gian (D6): đề xuất như trên, chốt ở cuối bước 0.
- Dữ liệu của USS khách hàng lấy qua DSS: lưu bản sao hay chỉ tham chiếu? Quyết định cùng D1.
