"""Allow-listed diagnostics (SETUP_4_1_0.md §8): a closed, explicit list of exception names.

One cannot mask a secret one does not know, so nothing free-form coming from a log, a
subprocess or a foreign exception reaches a journal, a result or a command output. An exception
is reduced to a name of this list, or to the constant ``"other"``: an unknown class name is
never echoed (a foreign class that merely shares a listed name is unknown too).
"""
from __future__ import annotations

import re

OTHER = "other"

# label -> (module, qualified name) of the ONE class it stands for. No dynamic discovery.
_KNOWN = {
    "AssertionError": ("builtins", "AssertionError"),
    "AttributeError": ("builtins", "AttributeError"),
    "ConnectionError": ("builtins", "ConnectionError"),
    "FileNotFoundError": ("builtins", "FileNotFoundError"),
    "ImportError": ("builtins", "ImportError"),
    "IndexError": ("builtins", "IndexError"),
    "KeyError": ("builtins", "KeyError"),
    "KeyboardInterrupt": ("builtins", "KeyboardInterrupt"),
    "MemoryError": ("builtins", "MemoryError"),
    "ModuleNotFoundError": ("builtins", "ModuleNotFoundError"),
    "NotImplementedError": ("builtins", "NotImplementedError"),
    "OSError": ("builtins", "OSError"),
    "PermissionError": ("builtins", "PermissionError"),
    "ProfileError": ("memory_os_v4.config", "ProfileError"),
    "ProvisionError": ("memory_os_v4.ops.provision", "ProvisionError"),
    "QuickstartError": ("memory_os_v4.ops.quickstart", "QuickstartError"),
    "RuntimeError": ("builtins", "RuntimeError"),
    "SecretFileError": ("memory_os_v4.ops.auth", "SecretFileError"),
    "SyntaxError": ("builtins", "SyntaxError"),
    "SystemExit": ("builtins", "SystemExit"),
    "TimeoutError": ("builtins", "TimeoutError"),
    "TypeError": ("builtins", "TypeError"),
    "UninstallError": ("memory_os_v4.ops.uninstall", "UninstallError"),
    "ValueError": ("builtins", "ValueError"),
    "sqlite3.DatabaseError": ("sqlite3", "DatabaseError"),
    "sqlite3.OperationalError": ("sqlite3", "OperationalError"),
}
EXCEPTION_TYPES = frozenset(_KNOWN)
_BY_CLASS = {identity: label for label, identity in _KNOWN.items()}
_LOG_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
                       r"(?:Error|Exception|Failure|Exit|Interrupt)): ")


def exception_label(exc: BaseException) -> str:
    """Name of the exact class when it is on the list, else the constant ``"other"``."""
    kind = type(exc)
    return _BY_CLASS.get((getattr(kind, "__module__", None), getattr(kind, "__qualname__", None)), OTHER)


def exception_types(text: str) -> list[str]:
    """Class names found at the start of the lines of a log: listed names sorted, then ``"other"``.

    A line counts when it starts with a dotted identifier ending in Error, Exception, Failure,
    Exit or Interrupt, followed by ``": "``. The message is never returned.
    """
    known, unknown = set(), False
    for line in text.splitlines():
        match = _LOG_LINE.match(line)
        if not match:
            continue
        if match.group(1) in EXCEPTION_TYPES:
            known.add(match.group(1))
        else:
            unknown = True
    return sorted(known) + ([OTHER] if unknown else [])
