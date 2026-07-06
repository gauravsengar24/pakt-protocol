import hashlib
import json
from typing import Union


BytesLike = Union[str, bytes, bytearray]


def sha256(data: BytesLike) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).digest()


def sha256_hex(data: BytesLike) -> str:
    return sha256(data).hex()


def sha256_int(data: BytesLike) -> int:
    return int.from_bytes(sha256(data), "big")


def hash_content(data: BytesLike) -> dict:
    raw = data if isinstance(data, bytes) else data.encode("utf-8") if isinstance(data, str) else bytes(data)
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "algorithm": "sha256",
        "digest": digest,
        "digest_bytes": list(hashlib.sha256(raw).digest()),
        "length": len(raw),
        "preview": raw[:128].hex(" ") if len(raw) > 128 else raw.hex(" "),
    }


def hash_file(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    return hash_content(raw)


def hash_json(obj: object) -> dict:
    serialized = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hash_content(serialized)


def verify_content(content: BytesLike, expected_hash_hex: str) -> bool:
    return sha256_hex(content) == expected_hash_hex.lower()


def verify_file(path: str, expected_hash_hex: str) -> bool:
    with open(path, "rb") as f:
        content = f.read()
    return verify_content(content, expected_hash_hex)


def format_for_covenant(content: BytesLike) -> tuple[bytes, str]:
    digest = sha256(content)
    hex_str = digest.hex()
    return digest, hex_str


class ContentCommitment:
    def __init__(self, content: bytes):
        self.content = content
        self._digest = hashlib.sha256(content).digest()
        self._hex = self._digest.hex()

    @classmethod
    def from_string(cls, text: str) -> "ContentCommitment":
        return cls(text.encode("utf-8"))

    @classmethod
    def from_file(cls, path: str) -> "ContentCommitment":
        with open(path, "rb") as f:
            return cls(f.read())

    @property
    def digest(self) -> bytes:
        return self._digest

    @property
    def hex(self) -> str:
        return self._hex

    @property
    def hex_bytes(self) -> list[int]:
        return list(self._digest)

    def verify(self, other: bytes) -> bool:
        return hashlib.sha256(other).digest() == self._digest

    def clone_as_commitment(self) -> dict:
        return {
            "type": "sha256",
            "value": self._hex,
            "length": len(self.content),
        }
