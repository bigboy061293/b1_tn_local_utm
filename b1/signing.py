"""Khoá ký của B1 (ES256): ký bản ghi cho phép cất cánh, sau này ký webhook I-01.

Khoá công khai công bố ở /v1/signing-keys (JWKS) để bên ngoài kiểm chữ ký. Khi chưa có tệp khoá, B1 tạo mới (chỉ hợp với
môi trường phát triển); khi vận hành, khoá nằm trong HSM hoặc kho bí mật và quay vòng theo quy chế an ninh thông tin.
Đây là chữ ký của hệ thống, gắn với người ký qua danh tính đã xác thực đa yếu tố và đăng nhập lại; chưa phải chữ ký số
theo Luật Giao dịch điện tử (cần chứng thư số của tổ chức cung cấp dịch vụ chứng thực).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class Signer:
    def __init__(self, key_file: str):
        p = pathlib.Path(key_file)
        if p.exists():
            self.key = serialization.load_pem_private_key(p.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
            os.chmod(p, 0o600)
        nums = self.key.public_key().public_numbers()
        b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()
        self.jwk = {"kty": "EC", "crv": "P-256", "x": b64(nums.x), "y": b64(nums.y), "use": "sig", "alg": "ES256"}
        thumb = json.dumps({k: self.jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"), sort_keys=True)
        self.kid = base64.urlsafe_b64encode(hashlib.sha256(thumb.encode()).digest()).rstrip(b"=").decode()
        self.jwk["kid"] = self.kid

    def sign(self, payload: dict) -> str:
        return jwt.encode(payload, self.key, algorithm="ES256", headers={"kid": self.kid, "typ": "b1-clearance+jwt"})

    def verify(self, token: str) -> dict:
        return jwt.decode(token, self.key.public_key(), algorithms=["ES256"], options={"verify_aud": False, "require": []})

    def jwks(self) -> dict:
        return {"keys": [self.jwk]}
