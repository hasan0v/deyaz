"""Native secure credential storage for DeYaz on Windows, macOS and Linux."""

import ctypes
import os

if os.name == "nt":
    from ctypes import wintypes


OPENROUTER_TARGET = "DeYaz/OpenRouter"
OPENAI_TARGET = "DeYaz/OpenAI"
LEGACY_OPENROUTER_TARGET = "Dikte/OpenRouter"
KEYRING_SERVICE = "DeYaz"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class CredentialStoreError(RuntimeError):
    pass


if os.name == "nt":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)
    _advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_PCREDENTIALW),
    ]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def _keyring():
    try:
        import keyring
        return keyring
    except Exception as exc:
        raise CredentialStoreError(
            "System keychain is unavailable. Install a supported keyring backend."
        ) from exc


def set_secret(target, value, username="DeYaz OAuth"):
    """Write a secret to the current operating system's secure credential store."""
    if not value:
        delete_secret(target)
        return
    if os.name != "nt":
        try:
            _keyring().set_password(KEYRING_SERVICE, target, value)
            return
        except Exception as exc:
            raise CredentialStoreError(f"System keychain write failed: {exc}") from exc
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialStoreError(
            f"Windows Credential Manager write failed ({ctypes.get_last_error()})."
        )


def get_secret(target):
    """Return a stored secret, or an empty string when it does not exist."""
    if os.name != "nt":
        try:
            return _keyring().get_password(KEYRING_SERVICE, target) or ""
        except Exception as exc:
            raise CredentialStoreError(f"System keychain read failed: {exc}") from exc
    pointer = _PCREDENTIALW()
    if not _advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return ""
        raise CredentialStoreError(f"Windows Credential Manager read failed ({error}).")
    try:
        credential = pointer.contents
        raw = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        return raw.decode("utf-16-le")
    finally:
        _advapi32.CredFree(pointer)


def delete_secret(target):
    """Delete a stored secret. Missing credentials count as success."""
    if os.name != "nt":
        try:
            _keyring().delete_password(KEYRING_SERVICE, target)
        except Exception as exc:
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise CredentialStoreError(f"System keychain delete failed: {exc}") from exc
        return
    if not _advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise CredentialStoreError(
                f"Windows Credential Manager delete failed ({error})."
            )


def migrate_legacy_openrouter_secret():
    """Copy the previous brand's credential once; never delete the old entry."""
    try:
        current = get_secret(OPENROUTER_TARGET)
        if current:
            return current
        legacy = get_secret(LEGACY_OPENROUTER_TARGET)
        if legacy:
            set_secret(OPENROUTER_TARGET, legacy, username="DeYaz migrated key")
        return legacy
    except CredentialStoreError:
        return ""


def read_secret(target):
    """Read a current DeYaz credential while normalizing backend failures."""
    try:
        return get_secret(target)
    except CredentialStoreError:
        return ""
