"""Bên cấp danh tính giả lập cho phát triển cục bộ (không dùng khi vận hành).

Lần đầu chạy sinh cặp khoá vào .dev/ và tệp .dev/jwks.json; B1 đọc tệp này qua B1_JWKS_FILE.
    python tools/dev_token.py --sub it-admin --mfa        # in token
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEV = pathlib.Path(__file__).resolve().parent.parent / ".dev"
ISSUER, AUDIENCE, KID = "https://idp.b1.local/realms/b1", "b1-api", "dev-key"


def key() -> rsa.RSAPrivateKey:
    DEV.mkdir(exist_ok=True)
    pem = DEV / "dev-signing-key.pem"
    if not pem.exists():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key()))
        jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
        (DEV / "jwks.json").write_text(json.dumps({"keys": [jwk]}, indent=1))
    return serialization.load_pem_private_key(pem.read_bytes(), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", required=True)
    ap.add_argument("--mfa", action="store_true")
    ap.add_argument("--ttl", type=int, default=3600, help="giây, tối đa 1 giờ (Đặc tả Mục 6)")
    a = ap.parse_args()
    now = int(time.time())
    print(jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": a.sub, "iat": now, "exp": now + min(a.ttl, 3600), "auth_time": now,
                      "amr": ["pwd", "otp"] if a.mfa else ["pwd"]}, key(), algorithm="RS256", headers={"kid": KID}))


if __name__ == "__main__":
    main()
