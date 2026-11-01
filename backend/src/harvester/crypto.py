"""存库密文的加密格式（spec 0004），api 和 collector 共用。

密文 = 版本号（1 字节）+ nonce（12 字节）+ AES-256-GCM 密文和 tag，用途名是附加数据。
genkey 命令：python -m harvester.crypto genkey
"""

import base64
import binascii
import secrets
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
PURPOSE_TOTP = "totp"


@dataclass(frozen=True)
class KeyRing:
    current_version: int
    keys: Mapping[int, bytes]


@dataclass(frozen=True)
class DecryptError:
    reason: str


def parse_key(raw: str) -> tuple[int, bytes]:
    """解析 `<版本号>:<标准 base64>`。错误信息不带密钥内容，配置校验时会打印出来。"""
    version_text, separator, encoded = raw.strip().partition(":")
    if not separator or not version_text.isascii() or not version_text.isdigit():
        raise ValueError("key must look like <version>:<base64>")
    version = int(version_text)
    if not 1 <= version <= 255:
        raise ValueError("key version must be between 1 and 255")
    try:
        key = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise ValueError("key is not valid base64") from None
    if len(key) != KEY_BYTES:
        raise ValueError("key must decode to exactly 32 bytes")
    return version, key


def build_key_ring(current: str, old: str | None) -> KeyRing:
    version, key = parse_key(current)
    old_keys = [parse_key(item) for item in (old or "").split(",") if item.strip()]
    versions = [version, *(v for v, _ in old_keys)]
    if len(set(versions)) != len(versions):
        raise ValueError("key versions must be unique")
    return KeyRing(current_version=version, keys={version: key, **dict(old_keys)})


def encrypt(
    keys: KeyRing,
    purpose: str,
    plaintext: bytes,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> bytes:
    nonce = random_bytes(NONCE_BYTES)
    sealed = AESGCM(keys.keys[keys.current_version]).encrypt(nonce, plaintext, purpose.encode())
    return bytes([keys.current_version]) + nonce + sealed


def decrypt(keys: KeyRing, purpose: str, blob: bytes) -> bytes | DecryptError:
    if len(blob) < 1 + NONCE_BYTES:
        return DecryptError("malformed")
    key = keys.keys.get(blob[0])
    if key is None:
        return DecryptError("unknown_key_version")
    nonce, sealed = blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, sealed, purpose.encode())
    except InvalidTag:
        return DecryptError("authentication_failed")


def generate_key(version: int = 1) -> str:
    return f"{version}:{base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode()}"


if __name__ == "__main__":
    if sys.argv[1:] != ["genkey"]:
        sys.exit("usage: python -m harvester.crypto genkey")
    print(generate_key())
