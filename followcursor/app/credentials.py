"""Credential storage using Windows DPAPI (Data Protection API).

Encrypts secrets so they are not stored in plaintext in the Windows
registry (QSettings).  The encrypted blob is user-scoped — only the
same Windows user account can decrypt it.

Falls back to plaintext storage on non-Windows platforms or when
DPAPI calls fail (e.g. in tests).
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# DPAPI is only available on Windows
_HAS_DPAPI = sys.platform == "win32"

if _HAS_DPAPI:
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32


def protect(plaintext: str) -> str:
    """Encrypt a string with DPAPI and return a base64-encoded blob.

    Returns the plaintext unchanged if DPAPI is unavailable.
    """
    if not _HAS_DPAPI or not plaintext:
        return plaintext

    try:
        encoded = plaintext.encode("utf-8")
        blob_in = _DATA_BLOB(
            len(encoded),
            ctypes.cast(ctypes.create_string_buffer(encoded, len(encoded)),
                        ctypes.POINTER(ctypes.c_char)),
        )
        blob_out = _DATA_BLOB()

        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in),   # pDataIn
            None,                     # szDataDescr
            None,                     # pOptionalEntropy
            None,                     # pvReserved
            None,                     # pPromptStruct
            0,                        # dwFlags
            ctypes.byref(blob_out),   # pDataOut
        )
        if not ok:
            logger.warning("CryptProtectData failed, storing plaintext")
            return plaintext

        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        logger.warning("DPAPI protect failed, storing plaintext", exc_info=True)
        return plaintext


def unprotect(stored: str) -> str:
    """Decrypt a DPAPI-protected string.  Handles both protected and legacy plaintext values."""
    if not stored:
        return ""

    if not stored.startswith("dpapi:"):
        # Legacy plaintext value — return as-is
        return stored

    if not _HAS_DPAPI:
        logger.warning("DPAPI not available, cannot decrypt stored credential")
        return ""

    try:
        encrypted = base64.b64decode(stored[6:])  # strip "dpapi:" prefix
        blob_in = _DATA_BLOB(
            len(encrypted),
            ctypes.cast(ctypes.create_string_buffer(encrypted, len(encrypted)),
                        ctypes.POINTER(ctypes.c_char)),
        )
        blob_out = _DATA_BLOB()

        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            logger.warning("CryptUnprotectData failed")
            return ""

        plaintext = ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
        _kernel32.LocalFree(blob_out.pbData)
        return plaintext
    except Exception:
        logger.warning("DPAPI unprotect failed", exc_info=True)
        return ""
