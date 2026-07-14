"""Encrypt/decrypt Walmart client secrets using a Fernet key from WALMART_SECRET_KEY."""

import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ["WALMART_SECRET_KEY"]
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
