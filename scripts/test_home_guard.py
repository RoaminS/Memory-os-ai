"""Test-only Python write guard; not a replacement for an OS sandbox.

HOME is redirected before collection. Reads (including cached weights) remain
possible; writes through Python's audited filesystem APIs are denied under the
operator's home, except explicitly owned qualification/pytest output directories.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading


def operator_home():
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except (ImportError, KeyError):
        return Path.home().resolve()


class HomeWriteGuard:
    def __init__(self, home, allowed=()):
        self.home = os.path.realpath(os.fspath(home))
        self.allowed = tuple(os.path.realpath(os.fspath(p)) for p in allowed)
        self.violations = []
        self.local = threading.local()

    @staticmethod
    def fd_path(fd):
        if sys.platform == 'darwin':
            import fcntl
            return os.fsdecode(fcntl.fcntl(fd, 50, bytes(1024)).split(b'\0')[0])
        return os.readlink(f'/proc/self/fd/{fd}')

    def install(self):
        sys.addaudithook(self.audit)
        original = os.open

        def checked_open(path, flags, mode=0o777, *, dir_fd=None):
            # The CPython open audit event omits dir_fd. Check it before the call.
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                self.check(path, dir_fd)
            previous = getattr(self.local, 'checked_open', False)
            self.local.checked_open = True
            try:
                return original(path, flags, mode, dir_fd=dir_fd)
            finally:
                self.local.checked_open = previous

        os.open = checked_open

    def check(self, path, dir_fd=None, *, directory_entry=False):
        if path is None:
            return
        if isinstance(path, int):
            try:
                path = self.fd_path(path)
            except OSError:  # Pipe/socket, not a filesystem file.
                return
        path = os.fsdecode(path)
        if not os.path.isabs(path) and dir_fd not in (None, -1):
            parent = self.fd_path(dir_fd)
            path = os.path.join(parent, path)
        if directory_entry and os.path.basename(path) not in {'', '.', '..'}:
            # unlink/rmdir operate on an entry, not its final symlink target.
            # Resolve all parents (including aliases and ..), retaining the
            # basename only. Opening/truncating/chmod still follows the target.
            path = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        else:
            path = os.path.realpath(path)
        if path == self.home or path.startswith(self.home + os.sep):
            if any(path == p or path.startswith(p + os.sep) for p in self.allowed):
                return
            message = f'Test attempted a write under real HOME: {path}'
            self.violations.append(message)
            raise PermissionError(message)

    def audit(self, event, args):
        if event == 'open':
            if getattr(self.local, 'checked_open', False):
                return
            path, mode, flags = args
            if (mode and any(c in mode for c in 'wax+')) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                self.check(path)
        elif event in {'os.mkdir', 'os.chmod', 'os.chown'}:
            self.check(args[0], args[-1])
        elif event in {'os.remove', 'os.rmdir'}:
            self.check(args[0], args[1], directory_entry=True)
        elif event in {'os.rename', 'os.link'}:
            self.check(args[0], args[2])
            self.check(args[1], args[3])
        elif event == 'os.symlink':
            self.check(args[1], args[2])
        elif event == 'os.truncate':
            self.check(args[0])
        elif event == 'os.utime':
            self.check(args[0], args[-1])
        elif event == 'sqlite3.connect' and args[0] != ':memory:':
            # Native SQLite may create a file without emitting an open event.
            path = os.fsdecode(args[0])
            if not path.startswith('file:'):
                self.check(path)
            elif 'mode=ro' not in path.split('?', 1)[-1].split('&'):
                from urllib.parse import unquote
                self.check(unquote(path[5:].split('?', 1)[0]))
