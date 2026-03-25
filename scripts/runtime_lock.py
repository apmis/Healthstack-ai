import atexit
import os
import sys
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_LOCK_HANDLES: dict[Path, object] = {}


def acquire_script_lock(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        message = "Another instance is already running."
        try:
            handle.seek(0)
            existing_pid = handle.read().decode("utf-8", errors="ignore").strip()
            if existing_pid:
                message = f"Another instance is already running with PID {existing_pid}."
        except OSError:
            pass
        handle.close()
        print(message, file=sys.stderr)
        raise SystemExit(0)

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("utf-8"))
    handle.flush()
    _LOCK_HANDLES[lock_path] = handle

    def release() -> None:
        lock_handle = _LOCK_HANDLES.pop(lock_path, None)
        if lock_handle is None:
            return
        try:
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()

    atexit.register(release)
