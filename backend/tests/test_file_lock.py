from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from app import file_lock


class _FakeFcntl:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 4

    def __init__(self, *, error: OSError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def flock(self, descriptor: int, flags: int) -> None:
        self.calls.append((descriptor, flags))
        if self.error is not None:
            raise self.error


class _FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, outcomes: list[OSError | None]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, descriptor: int, mode: int, length: int) -> None:
        self.calls.append((descriptor, mode, length))
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def test_native_file_lock_rejects_a_second_handle_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "native.lock"
    with lock_path.open("a+b") as first, lock_path.open("a+b") as second:
        file_lock.acquire_exclusive_file_lock(first, nonblocking=True)
        with pytest.raises(file_lock.FileLockBusyError, match="already held"):
            file_lock.acquire_exclusive_file_lock(second, nonblocking=True)
        file_lock.release_file_lock(first)
        file_lock.acquire_exclusive_file_lock(second, nonblocking=True)
        file_lock.release_file_lock(second)


def test_fcntl_backend_normalizes_busy_and_preserves_other_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = _FakeFcntl(error=BlockingIOError(errno.EAGAIN, "busy"))
    monkeypatch.setattr(file_lock, "_fcntl", busy)
    with (tmp_path / "fcntl.lock").open("a+b") as handle:
        with pytest.raises(file_lock.FileLockBusyError, match="already held"):
            file_lock.acquire_exclusive_file_lock(handle, nonblocking=True)

        failure = _FakeFcntl(error=OSError(errno.EIO, "io failure"))
        monkeypatch.setattr(file_lock, "_fcntl", failure)
        with pytest.raises(OSError, match="io failure"):
            file_lock.acquire_exclusive_file_lock(handle)

        success = _FakeFcntl()
        monkeypatch.setattr(file_lock, "_fcntl", success)
        file_lock.acquire_exclusive_file_lock(handle, nonblocking=True)
        file_lock.release_file_lock(handle)
        assert [flags for _, flags in success.calls] == [
            success.LOCK_EX | success.LOCK_NB,
            success.LOCK_UN,
        ]


def test_msvcrt_backend_retries_blocking_and_rejects_nonblocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_lock, "_fcntl", None)
    backend = _FakeMsvcrt(
        [
            PermissionError(errno.EACCES, "busy"),
            None,
            None,
            PermissionError(errno.EACCES, "busy"),
        ]
    )
    monkeypatch.setattr(file_lock, "_msvcrt", backend)
    sleeps: list[float] = []
    monkeypatch.setattr(file_lock.time, "sleep", sleeps.append)

    with (tmp_path / "msvcrt.lock").open("a+b") as handle:
        file_lock.acquire_exclusive_file_lock(handle)
        file_lock.release_file_lock(handle)
        with pytest.raises(file_lock.FileLockBusyError, match="already held"):
            file_lock.acquire_exclusive_file_lock(handle, nonblocking=True)

    assert sleeps == [0.05]
    assert [length for _, _, length in backend.calls] == [1, 1, 1, 1]


def test_lock_backend_absence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_lock, "_fcntl", None)
    monkeypatch.setattr(file_lock, "_msvcrt", None)
    with (tmp_path / "unsupported.lock").open("a+b") as handle:
        with pytest.raises(file_lock.FileLockUnsupportedError, match="No supported"):
            file_lock.acquire_exclusive_file_lock(handle)
        with pytest.raises(file_lock.FileLockUnsupportedError, match="No supported"):
            file_lock.release_file_lock(handle)
