"""Xuất hợp đồng OpenAPI của dịch vụ B1 (lớp L6) ra api/l6-core.openapi.json. CI kiểm tệp trong repo khớp với mã."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b1.app import create_app  # noqa: E402

OUT = ROOT / "api" / "l6-core.openapi.json"

if __name__ == "__main__":
    spec = create_app(create_schema=False).openapi()
    spec["components"].setdefault("securitySchemes", {})["oidc"] = {
        "type": "openIdConnect", "openIdConnectUrl": "https://idp.b1.local/realms/b1/.well-known/openid-configuration"}
    spec["security"] = [{"oidc": []}]
    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"đã ghi {OUT.relative_to(ROOT)}")
