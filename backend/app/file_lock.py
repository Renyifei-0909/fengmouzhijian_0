from __future__ import annotations

import errno
import time
from typing import IO, Any

try:
    import fcntl as _fcntl
except ModuleNotFoundError:
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ModuleNotFoundError:
    _msvcrt = None


_LOCK_LENGTH = 1
_BUSY_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}


class FileLockBusyError(RuntimeError):
    pass


class FileLockUnsupportedError(RuntimeError):
    pass


def _raise_if_busy(exc: OSError) -> None:
    if exc.errno in _BUSY_ERRNOS:
        raise FileLockBusyError("File lock is already held") from exc


def acquire_exclusive_file_lock(handle: IO[Any], *, nonblocking: bool = False) -> None:
    if _fcntl is not None:
        flags = _fcntl.LOCK_EX | (_fcntl.LOCK_NB if nonblocking else 0)
        try:
            _fcntl.flock(handle.fileno(), flags)
        except OSError as exc:
            _raise_if_busy(exc)
            raise
        return

    if _msvcrt is None:
        raise FileLockUnsupportedError("No supported process file-lock backend is available")

    handle.seek(0)
    while True:
        try:
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, _LOCK_LENGTH)
            return
        except OSError as exc:
            if exc.errno not in _BUSY_ERRNOS:
                raise
            if nonblocking:
                raise FileLockBusyError("File lock is already held") from exc
            time.sleep(0.05)


def release_file_lock(handle: IO[Any]) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is None:
        raise FileLockUnsupportedError("No supported process file-lock backend is available")
    handle.seek(0)
    _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, _LOCK_LENGTH)
