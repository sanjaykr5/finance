import os
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_PATH = Path(__file__).resolve().parent.parent / ".secret.key"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not _KEY_PATH.exists():
            fd = os.open(_KEY_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        _fernet = Fernet(_KEY_PATH.read_bytes())
    return _fernet


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
