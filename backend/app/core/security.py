"""Crypto and auth primitives.

Three separate keys are used, because they protect against different things:

* ``secret_key``      signs JWTs.
* ``vault_key``       encrypts `PrivateVault` payloads (AES-256-GCM).
* ``index_key``       builds HMAC blind indexes over vault fields so the matching
                      engine can test equality (blocklist domains, hidden intent)
                      without ever decrypting every vault.

Blind indexes are deliberately *equality only*. There is no way to enumerate a
user's plaintext intent from the index, and a stolen database alone cannot
resolve which hash belongs to which intent without `index_key`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_SCRYPT_MAX = 2**20


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PasswordHash:
    algo: str
    salt: bytes
    n: int
    r: int
    p: int
    digest: bytes

    def encode(self) -> str:
        return "$".join(
            [
                self.algo,
                str(self.n),
                str(self.r),
                str(self.p),
                base64.b64encode(self.salt).decode(),
                base64.b64encode(self.digest).decode(),
            ]
        )

    @classmethod
    def decode(cls, raw: str) -> "PasswordHash":
        algo, n, r, p, salt, digest = raw.split("$")
        return cls(
            algo=algo,
            n=int(n),
            r=int(r),
            p=int(p),
            salt=base64.b64decode(salt),
            digest=base64.b64decode(digest),
        )


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    n = min(max(n, 2**12), _SCRYPT_MAX)
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, settings.scrypt_n, settings.scrypt_r, settings.scrypt_p)
    return PasswordHash("scrypt", salt, settings.scrypt_n, settings.scrypt_r, settings.scrypt_p, digest).encode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        parsed = PasswordHash.decode(encoded)
    except (ValueError, TypeError):
        return False
    candidate = _scrypt(password, parsed.salt, parsed.n, parsed.r, parsed.p)
    return hmac.compare_digest(candidate, parsed.digest)


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
def create_token(subject: str, kind: str = "access", ttl: int | None = None, **claims: Any) -> str:
    now = int(time.time())
    ttl = ttl if ttl is not None else (settings.access_token_ttl if kind == "access" else settings.refresh_token_ttl)
    payload = {"sub": subject, "kind": kind, "iat": now, "exp": now + ttl, "jti": secrets.token_urlsafe(8), **claims}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str, kind: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if payload.get("kind") != kind:
        raise jwt.InvalidTokenError(f"expected {kind} token")
    return payload


def opaque_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# blind index
# ---------------------------------------------------------------------------
def blind_index(kind: str, value: str) -> str:
    """Deterministic, keyed, non-reversible token used for equality matching."""
    msg = f"{kind}\x00{value.strip().lower()}".encode()
    mac = hmac.new(settings.index_key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode()


def blind_set(kind: str, values: list[str]) -> list[str]:
    return sorted({blind_index(kind, v) for v in values if v and v.strip()})


# ---------------------------------------------------------------------------
# vault envelope encryption
# ---------------------------------------------------------------------------
def _user_key(user_id: str) -> bytes:
    """Per-user data key via HKDF-style derivation from the master key."""
    return hmac.new(settings.vault_key, f"user::{user_id}".encode(), hashlib.sha256).digest()


def encrypt_vault(user_id: str, payload: dict[str, Any]) -> str:
    aad = f"vault::{user_id}".encode()
    nonce = os.urandom(12)
    ct = AESGCM(_user_key(user_id)).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(), aad)
    return base64.b64encode(nonce + ct).decode()


def decrypt_vault(user_id: str, blob: str) -> dict[str, Any]:
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    aad = f"vault::{user_id}".encode()
    plain = AESGCM(_user_key(user_id)).decrypt(nonce, ct, aad)
    return json.loads(plain.decode())


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
