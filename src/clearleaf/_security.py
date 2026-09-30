"""The standard security handler: passwords and per-object decryption
(ISO 32000-1:2008 section 7.6; ISO 32000-2:2020 section 7.6.4 for AES-256).

Supported: revisions 2-4 (RC4 40-128 bit and AES-128, V1/V2/V4) and 5-6
(AES-256, V5), user and owner passwords, crypt filters (StmF / StrF / EFF,
CFM None / V2 / AESV2 / AESV3 / Identity), and EncryptMetadata false.
Algorithm numbers below refer to ISO 32000-1 (1-7) and ISO 32000-2 (2.A, 2.B).
"""

from __future__ import annotations

from .errors import PDFError

import hashlib
import struct
from typing import Optional

from ._crypto import aes_cbc_decrypt, aes_cbc_encrypt, rc4, strip_pkcs5

# Algorithm 2, step (a): the 32-byte padding string.
PASSWORD_PADDING = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")

USER, OWNER = 2, 4  # authenticate() result bits, as in PyMuPDF


class EncryptionError(PDFError):
    pass


def _pad(password: bytes) -> bytes:
    return (password + PASSWORD_PADDING)[:32]


def _as_bytes(value) -> bytes:
    return bytes(value) if isinstance(value, (bytes, bytearray)) else b""


class StandardSecurityHandler:
    def __init__(self, encrypt: dict, file_id: bytes, resolve=lambda x: x):
        r = resolve
        if str(r(encrypt.get("Filter"))) != "Standard":
            raise EncryptionError(f"unsupported security handler {r(encrypt.get('Filter'))!r}")
        self.v = int(r(encrypt.get("V", 0)) or 0)
        self.r = int(r(encrypt.get("R", 2)))
        self.o = _as_bytes(r(encrypt.get("O")))
        self.u = _as_bytes(r(encrypt.get("U")))
        self.oe = _as_bytes(r(encrypt.get("OE")))
        self.ue = _as_bytes(r(encrypt.get("UE")))
        self.p = int(r(encrypt.get("P", 0))) & 0xFFFFFFFF
        self.key_length = int(r(encrypt.get("Length", 40)) or 40) // 8
        if self.r >= 5:
            self.key_length = 32
        self.encrypt_metadata = r(encrypt.get("EncryptMetadata", True)) is not False
        self.file_id = file_id
        self.key: Optional[bytes] = None

        # Crypt filters (V4/V5); V1/V2 use RC4 for everything.
        self.stream_method = self.string_method = self.embedded_method = "V2" if self.v < 4 else "Identity"
        if self.v >= 4:
            filters = {str(name): r(cf) for name, cf in (r(encrypt.get("CF")) or {}).items()}

            def method(filter_name) -> str:
                name = str(r(filter_name)) if filter_name is not None else "Identity"
                if name == "Identity":
                    return "Identity"
                cfm = str(r((filters.get(name) or {}).get("CFM", "None")))
                return {"V2": "V2", "AESV2": "AESV2", "AESV3": "AESV3"}.get(cfm, "Identity")

            self.stream_method = method(encrypt.get("StmF"))
            self.string_method = method(encrypt.get("StrF"))
            self.embedded_method = method(encrypt.get("EFF")) if "EFF" in encrypt else self.stream_method

    # -- passwords -----------------------------------------------------------

    def authenticate(self, password) -> int:
        """Try ``password`` as user and as owner password. Returns 0 if it
        opens neither, else USER and/or OWNER bits; on success the file key
        is set and decryption becomes available."""
        if isinstance(password, str):
            password = password.encode("utf-8") if self.r >= 5 else password.encode("latin-1", "replace")
        result = 0
        if self.r >= 5:
            password = password[:127]
            owner_key = self._owner_key_r5(password)
            user_key = self._user_key_r5(password)
        else:
            owner_key = self._owner_key_r4(password)
            user_key = self._user_key_r4(password)
        if owner_key is not None:
            result |= OWNER
            self.key = owner_key
        if user_key is not None:
            result |= USER
            self.key = self.key or user_key
        return result

    # revisions 2-4
    def _compute_key_r4(self, password: bytes) -> bytes:
        """Algorithm 2."""
        h = hashlib.md5(_pad(password))
        h.update(self.o)
        h.update(struct.pack("<I", self.p))
        h.update(self.file_id)
        if self.r >= 4 and not self.encrypt_metadata:
            h.update(b"\xff\xff\xff\xff")
        digest = h.digest()
        n = 5 if self.r == 2 else self.key_length
        if self.r >= 3:
            for _ in range(50):
                digest = hashlib.md5(digest[:n]).digest()
        return digest[:n]

    def _user_key_r4(self, password: bytes) -> Optional[bytes]:
        """Algorithms 6 (with 4 or 5): key if ``password`` is the user password."""
        key = self._compute_key_r4(password)
        if self.r == 2:
            ok = rc4(key, PASSWORD_PADDING) == self.u[:32]
        else:
            x = rc4(key, hashlib.md5(PASSWORD_PADDING + self.file_id).digest())
            for i in range(1, 20):
                x = rc4(bytes(b ^ i for b in key), x)
            ok = x[:16] == self.u[:16]
        return key if ok else None

    def _owner_key_r4(self, password: bytes) -> Optional[bytes]:
        """Algorithm 7: recover the user password from /O, then check it."""
        digest = hashlib.md5(_pad(password)).digest()
        n = 5 if self.r == 2 else self.key_length
        if self.r >= 3:
            for _ in range(50):
                digest = hashlib.md5(digest).digest()
        key = digest[:n]
        if self.r == 2:
            user_password = rc4(key, self.o)
        else:
            user_password = self.o
            for i in range(19, -1, -1):
                user_password = rc4(bytes(b ^ i for b in key), user_password)
        return self._user_key_r4(user_password)

    # revisions 5-6
    def _hash_r6(self, password: bytes, salt: bytes, udata: bytes) -> bytes:
        """Algorithm 2.B (R6); plain SHA-256 for the deprecated R5."""
        k = hashlib.sha256(password + salt + udata).digest()
        if self.r == 5:
            return k
        i = 0
        while True:
            k1 = (password + k + udata) * 64
            e = aes_cbc_encrypt(k[:16], k[16:32], k1)
            mod = sum(e[:16]) % 3  # the first 16 bytes as a big-endian number, mod 3
            k = (hashlib.sha256, hashlib.sha384, hashlib.sha512)[mod](e).digest()
            i += 1
            if i >= 64 and e[-1] <= i - 32:
                return k[:32]

    def _user_key_r5(self, password: bytes) -> Optional[bytes]:
        """Algorithm 2.A, user password branch."""
        if len(self.u) < 48:
            return None
        if self._hash_r6(password, self.u[32:40], b"") != self.u[:32]:
            return None
        intermediate = self._hash_r6(password, self.u[40:48], b"")
        return aes_cbc_decrypt(intermediate, bytes(16), self.ue[:32])

    def _owner_key_r5(self, password: bytes) -> Optional[bytes]:
        """Algorithm 2.A, owner password branch."""
        if len(self.o) < 48 or len(self.u) < 48:
            return None
        if self._hash_r6(password, self.o[32:40], self.u[:48]) != self.o[:32]:
            return None
        intermediate = self._hash_r6(password, self.o[40:48], self.u[:48])
        return aes_cbc_decrypt(intermediate, bytes(16), self.oe[:32])

    # -- decryption -----------------------------------------------------------

    def _object_key(self, num: int, gen: int, method: str) -> bytes:
        """Algorithm 1: per-object keys for RC4/AESV2; AESV3 uses the file key."""
        if method == "AESV3":
            return self.key
        salt = b"sAlT" if method == "AESV2" else b""
        digest = hashlib.md5(self.key + num.to_bytes(3, "little") + gen.to_bytes(2, "little") + salt).digest()
        return digest[: min(len(self.key) + 5, 16)]

    def _decrypt(self, data: bytes, num: int, gen: int, method: str) -> bytes:
        if method == "Identity" or self.key is None:
            return data
        key = self._object_key(num, gen, method)
        if method == "V2":
            return rc4(key, data)
        if len(data) < 16:
            return b""
        return strip_pkcs5(aes_cbc_decrypt(key, data[:16], data[16:]))

    def decrypt_string(self, data: bytes, num: int, gen: int) -> bytes:
        return self._decrypt(data, num, gen, self.string_method)

    def decrypt_stream(self, data: bytes, num: int, gen: int, stream_dict: dict) -> bytes:
        kind = str(stream_dict.get("Type", ""))
        if kind == "XRef":
            return data  # cross-reference streams are never encrypted
        if kind == "Metadata" and not self.encrypt_metadata:
            return data
        method = self.embedded_method if kind == "EmbeddedFile" else self.stream_method
        return self._decrypt(data, num, gen, method)
