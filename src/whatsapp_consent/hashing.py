"""Keyed hashes, tokens and codes. Nothing secret is stored in readable form."""

import base64
import hashlib
import hmac
import secrets

CLICK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CLICK_CODE_LENGTH = 6


def _hmac(key: str, purpose: str, value: str) -> bytes:
    return hmac.new(key.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).digest()


def keyed_hash(key: str, purpose: str, value: str) -> str:
    """HMAC-SHA256 of `value`, separated by `purpose` so one key serves several uses."""
    return _hmac(key, purpose, value).hex()


def phone_fingerprint(key: str, phone_e164: str) -> str:
    """Matches a number to anonymised records without storing the number."""
    return keyed_hash(key, "phone", phone_e164)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """A random URL-safe token and the hash to store."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def derive_token(key: str, purpose: str, value: str) -> str:
    """A URL-safe token the server can recompute (practice links, which owners re-share)."""
    return base64.urlsafe_b64encode(_hmac(key, f"token:{purpose}", value)).rstrip(b"=").decode()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_click_code() -> str:
    return "".join(secrets.choice(CLICK_CODE_ALPHABET) for _ in range(CLICK_CODE_LENGTH))


def hash_code(key: str, purpose: str, phone_e164: str, code: str) -> str:
    return keyed_hash(key, f"code:{purpose}:{phone_e164}", code)


def code_matches(key: str, purpose: str, phone_e164: str, code: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_code(key, purpose, phone_e164, code).encode(), expected_hash.encode())
