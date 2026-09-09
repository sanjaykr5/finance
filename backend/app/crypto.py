from pathlib import Path

from cryptography.fernet import Fernet

_KEY_PATH = Path(__file__).resolve().parent.parent / ".secret.key"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not _KEY_PATH.exists():
            _KEY_PATH.write_bytes(Fernet.generate_key())
            _KEY_PATH.chmod(0o600)
        _fernet = Fernet(_KEY_PATH.read_bytes())
    return _fernet


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
