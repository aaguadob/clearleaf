"""Cipher primitives for PDF decryption: RC4 and AES (128/256, CBC).

The standard library has hashes (MD5, SHA-2) but no ciphers, and this
project keeps zero runtime dependencies, so both are implemented here:

- RC4: the well-known key-scheduling + keystream algorithm.
- AES: FIPS-197. The S-box is *computed* from its definition (multiplicative
  inverse in GF(2^8) followed by the affine transform) rather than typed in,
  and the implementation is checked against the FIPS-197 Appendix C
  known-answer vectors in the test suite.

Pure Python -- correct first, not fast (~tens of microseconds per block).
"""

from __future__ import annotations


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for k, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


# -- AES ------------------------------------------------------------------------


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _build_sboxes():
    inverse = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if _gmul(a, b) == 1:
                inverse[a] = b
                break
    sbox = [0] * 256
    for a in range(256):
        x = inverse[a]
        # affine transform: b ^ rotl(b,1) ^ rotl(b,2) ^ rotl(b,3) ^ rotl(b,4) ^ 0x63
        y = x
        for shift in range(1, 5):
            y ^= ((x << shift) | (x >> (8 - shift))) & 0xFF
        sbox[a] = y ^ 0x63
    inv_sbox = [0] * 256
    for a, v in enumerate(sbox):
        inv_sbox[v] = a
    return sbox, inv_sbox


SBOX, INV_SBOX = _build_sboxes()
_MUL = {k: [_gmul(a, k) for a in range(256)] for k in (2, 3, 9, 11, 13, 14)}
_M2, _M3, _M9, _M11, _M13, _M14 = (_MUL[k] for k in (2, 3, 9, 11, 13, 14))


def _expand_key(key: bytes) -> list:
    """Round keys as a list of 16-byte lists (FIPS-197 section 5.2)."""
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    rounds = nk + 6
    words = [list(key[4 * i : 4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (rounds + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        words.append([a ^ b for a, b in zip(words[i - nk], temp)])
    return [sum(words[4 * r : 4 * r + 4], []) for r in range(rounds + 1)]


# State is 16 bytes in column-major order (state[r + 4c]), as in FIPS-197.
_SHIFT = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]      # ShiftRows source indices
_INV_SHIFT = [0, 13, 10, 7, 4, 1, 14, 11, 8, 5, 2, 15, 12, 9, 6, 3]  # InvShiftRows source indices


def _encrypt_block(block: bytes, round_keys: list) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    last = len(round_keys) - 1
    for r in range(1, last + 1):
        s = [SBOX[s[i]] for i in _SHIFT]  # SubBytes + ShiftRows
        if r != last:
            t = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c : 4 * c + 4]
                t += [
                    _M2[a0] ^ _M3[a1] ^ a2 ^ a3,
                    a0 ^ _M2[a1] ^ _M3[a2] ^ a3,
                    a0 ^ a1 ^ _M2[a2] ^ _M3[a3],
                    _M3[a0] ^ a1 ^ a2 ^ _M2[a3],
                ]
            s = t
        s = [b ^ k for b, k in zip(s, round_keys[r])]
    return bytes(s)


def _decrypt_block(block: bytes, round_keys: list) -> bytes:
    last = len(round_keys) - 1
    s = [b ^ k for b, k in zip(block, round_keys[last])]
    for r in range(last - 1, -1, -1):
        s = [INV_SBOX[s[i]] for i in _INV_SHIFT]  # InvShiftRows + InvSubBytes
        s = [b ^ k for b, k in zip(s, round_keys[r])]
        if r != 0:
            t = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c : 4 * c + 4]
                t += [
                    _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3],
                    _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                    _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3],
                    _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3],
                ]
            s = t
    return bytes(s)


def aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    return _encrypt_block(block, _expand_key(key))


def aes_decrypt_block(key: bytes, block: bytes) -> bytes:
    return _decrypt_block(block, _expand_key(key))


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """CBC without padding (``data`` must be a multiple of 16 bytes)."""
    if len(data) % 16:
        raise ValueError("CBC input must be a multiple of 16 bytes")
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        prev = _encrypt_block(bytes(a ^ b for a, b in zip(data[i : i + 16], prev)), rk)
        out += prev
    return bytes(out)


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """CBC without padding removal; a trailing partial block is dropped."""
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data) - len(data) % 16, 16):
        block = data[i : i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, rk), prev))
        prev = block
    return bytes(out)


def strip_pkcs5(data: bytes) -> bytes:
    """Remove PKCS#5 padding if it's well-formed; otherwise return as-is."""
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data
