import base64
import json
from typing import Any, Optional, Union
import logging
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


class EncryptionService:
    """Provides symmetric Fernet encryption for delivered digital accounts/keys at rest."""

    def __init__(self, key: Optional[str] = None):
        raw_key = key or settings.ENCRYPTION_KEY
        # Ensure valid 32-byte urlsafe base64 key
        if isinstance(raw_key, str):
            raw_key = raw_key.strip().encode()
        self._fernet = Fernet(raw_key)

    def encrypt(self, data: Union[str, dict, list]) -> bytes:
        """Serialize data to JSON and encrypt returning ciphertext bytes."""
        if not isinstance(data, str):
            json_str = json.dumps(data)
        else:
            json_str = data
        return self._fernet.encrypt(json_str.encode("utf-8"))

    def decrypt(self, ciphertext: Optional[Union[bytes, str]]) -> Any:
        """Decrypt ciphertext and parse back JSON/string with graceful error handling."""
        if not ciphertext:
            return None
        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode("utf-8")
        try:
            decrypted_bytes = self._fernet.decrypt(ciphertext)
            text = decrypted_bytes.decode("utf-8")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        except InvalidToken:
            logger.error("Failed to decrypt payload: Invalid Fernet token or encryption key mismatch.")
            return None
        except Exception as exc:
            logger.error("Unexpected error during Fernet decryption: %s", exc)
            return None


encryption_service = EncryptionService()
