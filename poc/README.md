# Bước 1: thử khái niệm DSS/USS

Dựng hệ sinh thái cục bộ của InterUSS (2 DSS, OAuth giả lập, các USS giả lập `mock_uss`), chạy bộ kiểm thử
`uss_qualifier` cho ASTM F3548-21 và F3411-22a, rồi chạy kịch bản riêng của B1 trên hình học hành lang thật.
Kết quả và đánh giá cho quyết định D1: [`docs/d1-poc-results.md`](../docs/d1-poc-results.md).

## Phiên bản đã dùng

| Thành phần | Phiên bản | Giấy phép |
|---|---|---|
| InterUSS DSS (image `interuss/dss`) | v0.24.0 | Apache 2.0 |
| InterUSS monitoring (`uss_qualifier`, `mock_uss`) | 0.38.0, commit `4dc36dd9` | Apache 2.0 |
| CockroachDB (CSDL của DSS, lần chạy 1) | v24.1.3 | BSL 1.1, **chỉ dùng thử** |
| YugabyteDB (CSDL của DSS, lần chạy 2, đề xuất cho vận hành) | 2025.1.2.1 | Apache 2.0 |

## Dựng lại

```bash
git clone https://github.com/interuss/monitoring && cd monitoring
git checkout 4dc36dd9cb7cae45a97e3e0506aff99388de722b
DB_TYPE=ybdb DSS_IMAGE=interuss/dss:v0.24.0 NUM_USS=2 build/dev/run_locally.sh up --wait   # bỏ DB_TYPE để dùng CockroachDB
./monitoring/mock_uss/run_locally.sh up --wait --wait-timeout 180 -d
CI=true monitoring/uss_qualifier/run_locally.sh configurations.dev.f3548_self_contained
CI=true monitoring/uss_qualifier/run_locally.sh configurations.dev.netrid_v22a
```

Từ thư mục repo B1, khi hệ sinh thái đang chạy:

```bash
python3 poc/two_uss_same_block.py              # ý định bay trùng đúng ranh khối
python3 poc/two_uss_same_block.py --inset-m 50 # co ý định vào trong ranh khối 50 m
```

### Ghi chú môi trường (môi trường cloud của dự án)

- Docker Hub giới hạn lượt tải ẩn danh (HTTP 429). Kéo image qua `mirror.gcr.io/<repo>` rồi `docker tag` về tên gốc.
- Không tải được base image từ `ghcr.io` nên không build lại `interuss/monitoring`; dùng image có sẵn và
  checkout monitoring đúng commit của image (`GIT_COMMIT_HASH` trong image), bỏ dòng `make image` trong các script chạy.
- `interuss/dummy-oauth` build từ mã nguồn DSS: `CGO_ENABLED=0 go build ./cmds/dummy-oauth`, đóng vào image
  có `sh` và `wget` (healthcheck cần).
- `DB_TYPE=raft` chưa có trong DSS v0.24.0.
