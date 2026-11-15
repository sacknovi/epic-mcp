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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import config


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def has_private_key() -> bool:
    return config.PRIVATE_KEY.exists()


def _load_private_key() -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(config.PRIVATE_KEY.read_bytes(), password=None)  # type: ignore[return-value]


def generate_private_key(overwrite: bool = False) -> None:
    if config.PRIVATE_KEY.exists() and not overwrite:
        raise FileExistsError(f"{config.PRIVATE_KEY} already exists")
    config.ensure_data_dir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(config.PRIVATE_KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)


def public_jwk() -> dict:
    pub = _load_private_key().public_key().public_numbers()
    e, n = _int_b64(pub.e), _int_b64(pub.n)
    # RFC 7638 thumbprint as key id
    kid = _b64url(hashlib.sha256(json.dumps({"e": e, "kty": "RSA", "n": n}, separators=(",", ":")).encode()).digest())
    return {"kty": "RSA", "use": "sig", "alg": config.JWT_ALG, "kid": kid, "e": e, "n": n}


def jwks() -> dict:
    return {"keys": [public_jwk()]}


def client_assertion(client_id: str, token_endpoint: str) -> str:
    """Signed JWT proving we hold the private key (RFC 7523 / SMART confidential-asymmetric)."""
    now = int(time.time())
    header = {"alg": config.JWT_ALG, "typ": "JWT", "kid": public_jwk()["kid"]}
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
    sig = _load_private_key().sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA384())
    return f"{signing_input}.{_b64url(sig)}"
