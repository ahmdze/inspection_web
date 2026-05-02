import base64
import hashlib
import hmac
import os
import re
import secrets

PREFIX = "enc:v1:"
LEGACY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _key() -> bytes:
    secret = os.getenv("PASSWORD_ENCRYPTION_KEY") or os.getenv("SECRET_KEY") or "SECRET_CHANGE_ME_IN_PROD"
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _xor(data: bytes, nonce: bytes, key: bytes) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(b ^ stream[i] for i, b in enumerate(data))


def encrypt_password(password: str) -> str:
    password = "" if password is None else str(password)
    nonce = secrets.token_bytes(16)
    key = _key()
    cipher = _xor(password.encode("utf-8"), nonce, key)
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    token = base64.urlsafe_b64encode(nonce + mac + cipher).decode("ascii").rstrip("=")
    return PREFIX + token


def decrypt_password(stored: str) -> str | None:
    if not stored or not str(stored).startswith(PREFIX):
        return None
    token = str(stored)[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        nonce, mac, cipher = raw[:16], raw[16:32], raw[32:]
        key = _key()
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac, expected):
            return None
        return _xor(cipher, nonce, key).decode("utf-8")
    except Exception:
        return None


def legacy_hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, stored: str) -> bool:
    decrypted = decrypt_password(stored)
    if decrypted is not None:
        return hmac.compare_digest(password, decrypted)
    return hmac.compare_digest(legacy_hash_password(password), stored or "")


def export_password(stored: str) -> str:
    decrypted = decrypt_password(stored)
    if decrypted is not None:
        return decrypted
    if stored and LEGACY_SHA256_RE.match(str(stored)):
        return "LEGACY_HASH_NOT_DECRYPTABLE"
    return ""
