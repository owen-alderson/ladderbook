import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from ladderbook.kalshi.auth import KalshiSigner


def _pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def test_ed25519_signature_verifies_over_timestamp_method_path():
    key = ed25519.Ed25519PrivateKey.generate()
    signer = KalshiSigner.from_pem("key-1", _pem(key))
    headers = signer.headers("get", "/trade-api/v2/portfolio/orders?limit=5", now_ms=1700000000000)
    assert headers["KALSHI-ACCESS-KEY"] == "key-1"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    # query string stripped, method upper-cased, no separators
    key.public_key().verify(signature, b"1700000000000GET/trade-api/v2/portfolio/orders")


def test_rsa_pss_signature_verifies():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = KalshiSigner.from_pem("key-2", _pem(key))
    headers = signer.headers("GET", "/trade-api/ws/v2", now_ms=1)
    key.public_key().verify(
        base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
        b"1GET/trade-api/ws/v2",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_from_env_explains_missing_keys(monkeypatch):
    monkeypatch.delenv("KALSHI_KEY_ID", raising=False)
    with pytest.raises(SystemExit, match="KALSHI_KEY_ID"):
        KalshiSigner.from_env()


def test_from_env_reads_key_file(monkeypatch, tmp_path):
    path = tmp_path / "k.pem"
    path.write_bytes(_pem(ed25519.Ed25519PrivateKey.generate()))
    monkeypatch.setenv("KALSHI_KEY_ID", "abc")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(path))
    assert KalshiSigner.from_env().key_id == "abc"
