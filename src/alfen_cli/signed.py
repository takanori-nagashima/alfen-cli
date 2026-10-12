"""Verify and decode Alfen (DZG meter) signed meter values (the "AP;..." envelope).

Requires the `signed` extra (cryptography).
"""

from __future__ import annotations

import base64
import re


def _b32(text: bytes) -> bytes:
    text = text.strip().rstrip(b"=")
    return base64.b32decode(text + b"=" * ((8 - len(text) % 8) % 8), casefold=True)


def parse_payload(payload: bytes) -> dict:
    out: dict = {}
    # OBIS 1-0:1.8.0*255 (total active import); value is 4 bytes LE at +8.
    idx = payload.find(b"\x01\x00\x01\x08\x00\xff")
    if idx != -1:
        out["total_energy_kwh"] = int.from_bytes(payload[idx + 8:idx + 12], "little") / 1000.0
    strings = [s.decode() for s in re.findall(rb"[A-Za-z0-9\-._]{4,}", payload) if not s.startswith(b"EUR")]
    if strings:
        out["meter_info"] = strings
    return out


def verify(public_key_b64: str, signed_data_b64: str) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    key = base64.b64decode(public_key_b64)
    if len(key) == 26:
        key = key[:25]
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP192R1(), key)

    parts = [p for p in base64.b64decode(signed_data_b64).split(b";") if p.strip()]
    if len(parts) < 6:
        return {"verified": False, "error": f"unexpected envelope ({len(parts)} fields)"}
    sig = _b32(parts[-1])
    payload = _b32(parts[4])

    sig = sig.rjust(48, b"\x00") if len(sig) < 48 else sig
    half = len(sig) // 2
    der = utils.encode_dss_signature(int.from_bytes(sig[:half], "big"), int.from_bytes(sig[half:], "big"))
    try:
        public_key.verify(der, payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return {"verified": False, "error": "signature mismatch", "payload_hex": payload.hex()}
    return {"verified": True, **parse_payload(payload), "payload_hex": payload.hex()}
