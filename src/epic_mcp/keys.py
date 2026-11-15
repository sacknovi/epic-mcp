"""RSA key pair for SMART confidential-client authentication (private_key_jwt).

The private key stays on this machine (mode 600). The public key is published as a
JWK Set at a URL registered with Epic, which Epic uses to verify our client assertions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import config


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]


def generate_private_key(path: Path, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists")
    config.ensure_base_dir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)


def public_jwk(path: Path) -> dict:
    pub = _load_private_key(path).public_key().public_numbers()
    e, n = _int_b64(pub.e), _int_b64(pub.n)
    # RFC 7638 thumbprint as key id
    kid = _b64url(hashlib.sha256(json.dumps({"e": e, "kty": "RSA", "n": n}, separators=(",", ":")).encode()).digest())
    return {"kty": "RSA", "use": "sig", "alg": config.JWT_ALG, "kid": kid, "e": e, "n": n}


def jwks(path: Path) -> dict:
    return {"keys": [public_jwk(path)]}


def client_assertion(client_id: str, token_endpoint: str, key_path: Path) -> str:
    """Signed JWT proving we hold the private key (RFC 7523 / SMART confidential-asymmetric)."""
    now = int(time.time())
    header = {"alg": config.JWT_ALG, "typ": "JWT", "kid": public_jwk(key_path)["kid"]}
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + 240,  # Epic requires exp no more than 5 minutes out
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    sig = _load_private_key(key_path).sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA384())
    return f"{signing_input}.{_b64url(sig)}"
