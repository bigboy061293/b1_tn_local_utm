"""Kiểm mẫu theo đúng lược đồ `TelemetrySample` trong hợp đồng I-07 (một nguồn sự thật: api/i07-telemetry.openapi.yaml)."""
from __future__ import annotations

import functools
import pathlib

import jsonschema
import yaml

SPEC = pathlib.Path(__file__).resolve().parents[2] / "api" / "i07-telemetry.openapi.yaml"


@functools.cache
def validator() -> jsonschema.Draft202012Validator:
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    schema = dict(schemas["TelemetrySample"], components={"schemas": schemas})
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def errors(sample: object) -> list[str]:
    """Danh sách lỗi, rỗng nếu hợp lệ. Mỗi lỗi ghi đường dẫn trường để khách sửa."""
    out = []
    for e in sorted(validator().iter_errors(sample), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(x) for x in e.absolute_path) or "(gốc)"
        out.append(f"{path}: {e.message}"[:300])
    return out
