"""Cifrado Fernet para credenciales sensibles en reposo."""
from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

logger = logging.getLogger(__name__)

PLAIN_PREFIX = 'plain:'
_FERNET: Fernet | None = None
_KEY_WARNED = False


def get_fernet() -> Fernet | None:
    """Devuelve instancia Fernet si SECRET_ENCRYPTION_KEY está configurada."""
    global _FERNET, _KEY_WARNED
    if _FERNET is not None:
        return _FERNET

    key = os.environ.get('SECRET_ENCRYPTION_KEY', '').strip()
    if not key:
        if not _KEY_WARNED:
            logger.warning(
                'SECRET_ENCRYPTION_KEY no configurada; los secretos se guardarán con prefijo %s',
                PLAIN_PREFIX,
            )
            _KEY_WARNED = True
        return None

    try:
        _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
        return _FERNET
    except Exception as exc:
        logger.error('SECRET_ENCRYPTION_KEY inválida: %s', exc)
        return None


def encrypt_value(plain: str | None) -> str | None:
    if plain is None or plain == '':
        return None
    if plain.startswith(PLAIN_PREFIX):
        plain = plain[len(PLAIN_PREFIX):]
    fernet = get_fernet()
    if fernet is None:
        return f'{PLAIN_PREFIX}{plain}'
    return fernet.encrypt(plain.encode('utf-8')).decode('ascii')


def decrypt_value(cipher: str | None) -> str | None:
    if cipher is None or cipher == '':
        return None
    if cipher.startswith(PLAIN_PREFIX):
        return cipher[len(PLAIN_PREFIX):]
    fernet = get_fernet()
    if fernet is None:
        return cipher
    try:
        return fernet.decrypt(cipher.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        return cipher


class EncryptedString(TypeDecorator):
    """Columna que cifra al persistir y descifra al leer (compatible con texto plano legado)."""

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._length = length

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(self._length))

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)
