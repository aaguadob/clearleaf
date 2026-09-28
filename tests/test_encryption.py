"""Encrypted PDFs: cipher primitives against published vectors, and the
standard security handler on files written by an independent
implementation (pypdf + cryptography, see generate_encrypted_fixtures.py)."""

import os

import pytest

import clearleaf as pdf
from clearleaf._crypto import (
    SBOX, aes_cbc_decrypt, aes_cbc_encrypt, aes_decrypt_block, aes_encrypt_block, rc4, strip_pkcs5,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "encrypted")
SCHEMES = ["rc4_40", "rc4_128", "aes_128", "aes_256"]

PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")


def test_aes_fips197_appendix_c_vectors():
    k128 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    k256 = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    c128 = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    c256 = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    assert aes_encrypt_block(k128, PLAIN) == c128 and aes_decrypt_block(k128, c128) == PLAIN
    assert aes_encrypt_block(k256, PLAIN) == c256 and aes_decrypt_block(k256, c256) == PLAIN
    assert SBOX[0x00] == 0x63 and SBOX[0x53] == 0xED  # FIPS-197 figure 7 / section 5.1.1


def test_aes_cbc_round_trip_and_padding():
    key, iv = bytes(range(16)), bytes(range(16, 32))
    data = b"exactly thirty-two bytes long!!!"
    assert aes_cbc_decrypt(key, iv, aes_cbc_encrypt(key, iv, data)) == data
    assert strip_pkcs5(b"abc" + b"\x05" * 5) == b"abc"
    assert strip_pkcs5(b"abc\x07") == b"abc\x07"  # malformed padding left alone


def test_rc4_vectors():
    assert rc4(b"Key", b"Plaintext").hex() == "bbf316e8d940af0ad3"
    assert rc4(b"Wiki", b"pedia").hex() == "1021bf0420"
    assert rc4(b"Secret", b"Attack at dawn").hex() == "45a01f645fc35b383552544b9bf5"


def open_fixture(name):
    return pdf.open(os.path.join(FIXTURES, f"{name}.pdf"))


@pytest.mark.parametrize("scheme", SCHEMES)
def test_locked_until_authenticated(scheme):
    doc = open_fixture(scheme)
    assert doc.is_encrypted and doc.needs_pass
    with pytest.raises(ValueError, match="encrypted"):
        doc[0].get_text()
    assert doc.authenticate("wrong") == 0
    assert doc.needs_pass


@pytest.mark.parametrize("scheme", SCHEMES)
def test_user_password_decrypts_streams_and_strings(scheme):
    doc = open_fixture(scheme)
    assert doc.authenticate("user") == 2
    assert not doc.needs_pass
    assert doc[0].get_text() == "Secret text 42"             # stream decryption
    info = doc.resolve(doc.trailer["Info"])
    assert info["Title"] == b"Encrypted fixture"               # string decryption


@pytest.mark.parametrize("scheme", SCHEMES)
def test_owner_password_also_unlocks(scheme):
    doc = open_fixture(scheme)
    assert doc.authenticate("owner") == 4
    assert doc[0].get_text() == "Secret text 42"


def test_empty_user_password_opens_automatically():
    doc = open_fixture("aes_128_no_user_password")
    assert doc.is_encrypted and not doc.needs_pass
    assert doc[0].get_text() == "Secret text 42"


def test_unencrypted_document_reports_so():
    doc = pdf.open(os.path.join(os.path.dirname(__file__), "fixtures", "classic_simple.pdf"))
    assert not doc.is_encrypted and not doc.needs_pass
    assert doc.authenticate("anything") == 0
