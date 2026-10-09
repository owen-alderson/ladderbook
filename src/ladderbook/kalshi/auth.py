"""Request signing for Kalshi's API (https://docs.kalshi.com/getting_started/api_keys).

The signed message is timestamp_ms + METHOD + path, where path includes the
/trade-api/... prefix and excludes the query string. Ed25519 keys sign it directly;
RSA keys use PSS with SHA-256 and a digest-length salt. The signature is base64.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa


@dataclass(frozen=True)
class KalshiSigner:
    key_id: str
    private_key: ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey

    @classmethod
    def from_pem(cls, key_id: str, pem: bytes) -> KalshiSigner:
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
            raise ValueError("Kalshi keys must be Ed25519 or RSA")
        return cls(key_id, key)

    @classmethod
    def from_env(cls) -> KalshiSigner:
        """Read KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH."""
        try:
            key_id = os.environ["KALSHI_KEY_ID"]
            path = os.environ["KALSHI_PRIVATE_KEY_PATH"]
        except KeyError as missing:
            raise SystemExit(
                f"{missing.args[0]} is not set. Create an API key at kalshi.com → Profile → API Keys, "
                "then export KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH."
            ) from None
        return cls.from_pem(key_id, Path(path).expanduser().read_bytes())

    def sign(self, message: bytes) -> bytes:
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            return self.private_key.sign(message)
        return self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )

    def headers(self, method: str, path: str, now_ms: int | None = None) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000) if now_ms is None else now_ms)
        message = (timestamp + method.upper() + path.split("?", 1)[0]).encode()
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(self.sign(message)).decode(),
        }
