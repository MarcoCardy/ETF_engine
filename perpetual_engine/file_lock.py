"""Cross-platform non-blocking file locking."""
from __future__ import annotations

import errno
import sys

try:
    import msvcrt
except ImportError:
    msvcrt = None

if msvcrt is not None:
    LK_UNLCK = msvcrt.LK_UNLCK
    LK_NBLCK = msvcrt.LK_NBLCK

    def lock_file_nonblocking(fileno: int) -> None:
        msvcrt.locking(fileno, msvcrt.LK_NBLCK, 1)

    def unlock_file(fileno: int) -> None:
        msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    LK_UNLCK = 0
    LK_NBLCK = 2

    def lock_file_nonblocking(fileno: int) -> None:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise OSError(errno.EACCES, "Permission denied") from e

    def unlock_file(fileno: int) -> None:
        fcntl.flock(fileno, fcntl.LOCK_UN)

    class _MsvcrtShim:
        LK_UNLCK = LK_UNLCK
        LK_NBLCK = LK_NBLCK

        @staticmethod
        def locking(fileno: int, mode: int, nbytes: int) -> None:
            if mode == 2:
                lock_file_nonblocking(fileno)
            elif mode == 0:
                unlock_file(fileno)
            else:
                raise ValueError(f"Unsupported mode: {mode}")

    msvcrt = _MsvcrtShim()
