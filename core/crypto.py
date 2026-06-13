import os
from cryptography.fernet import Fernet

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Build and memoize a single Fernet instance. Validates ENCRYPTION_KEY
    once and raises a clear configuration error if it is missing/invalid."""
    global _fernet
    if _fernet is None:
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        if isinstance(key, str):
            key = key.encode()
        try:
            _fernet = Fernet(key)
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"ENCRYPTION_KEY is invalid: {e}") from e
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
