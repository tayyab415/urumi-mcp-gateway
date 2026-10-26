"""Passwords, API keys, session tokens and upstream credential encryption.

Why two kinds of hashing:

* Passwords are chosen by people and can be guessed, so they get a slow,
  salted hash (scrypt). A stolen database still costs an attacker real work
  per guess.
* API keys and session tokens are 256 random bits we generate. Guessing is
  hopeless, so a fast SHA-256 is enough, and it lets us find the key with one
  indexed lookup.

Upstream credentials are different again: the gateway has to *send* them, so
it cannot keep only a hash. They are encrypted with a key that lives in a
Kubernetes Secret, not in the database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

API_KEY_PREFIX = "urumi_"
CHAT_TOKEN_PREFIX = "urumichat_"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """Return (plaintext key, display prefix, hash). Only the hash is stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[: len(API_KEY_PREFIX) + 6], sha256_hex(key)


def new_token() -> tuple[str, str]:
    """Return (plaintext token, hash) for sessions and invitations."""
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token)


class CredentialBox:
    """Encrypts upstream credentials at rest with a key derived from GATEWAY_SECRET_KEY."""

    def __init__(self, secret_key: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(f"urumi-upstream-credentials:{secret_key}".encode()).digest())
        self._fernet = Fernet(key)

    def seal(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def open(self, ciphertext: str) -> str | None:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            return None


class ChatTokens:
    """Credentials the dashboard hands to the chat service, one per login session.

    The browser cannot read its HttpOnly session cookie, so it cannot forward
    it. Instead the gateway signs the session's id: `urumichat_<id>_<mac>`. The
    gateway accepts it on /mcp only while that login session exists and its
    user is active, so logging out or deactivating the user ends chat access.
    The same session always gets the same token, which keeps the chat service's
    conversations (owned by a hash of the credential) intact across reloads.
    """

    def __init__(self, secret_key: str):
        self._key = hashlib.sha256(f"urumi-chat-tokens:{secret_key}".encode()).digest()

    def _mac(self, session_id: int, session_token_hash: str) -> str:
        digest = hmac.new(self._key, f"{session_id}:{session_token_hash}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def issue(self, session_id: int, session_token_hash: str) -> str:
        return f"{CHAT_TOKEN_PREFIX}{session_id}_{self._mac(session_id, session_token_hash)}"

    @staticmethod
    def session_id(token: str) -> int | None:
        if not token.startswith(CHAT_TOKEN_PREFIX):
            return None
        sid, _, _ = token[len(CHAT_TOKEN_PREFIX):].partition("_")
        return int(sid) if sid.isdigit() else None

    def verify(self, token: str, session_id: int, session_token_hash: str) -> bool:
        return hmac.compare_digest(token, self.issue(session_id, session_token_hash))
