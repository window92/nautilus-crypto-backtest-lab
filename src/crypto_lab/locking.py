"""Linux-safe file primitives for append-only research authorities.

All opens are relative to directory file descriptors walked with
``O_NOFOLLOW``.  This prevents an authority or its lock from being redirected
through a symlink between a pathname check and the actual open.
"""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from typing import Iterator


class PathSafetyError(OSError):
    """A persistence path is not a regular file beneath regular directories."""


_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_NOFOLLOW_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW


def _absolute_path(path: Path) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if value == Path(value.anchor) or value.name in {"", ".", ".."}:
        raise PathSafetyError(f"unsafe authority path: {value}")
    return value


def _open_parent_directory(path: Path, *, create: bool) -> tuple[Path, int]:
    """Open every parent component without following symlinks."""

    absolute = _absolute_path(path)
    descriptor = os.open(absolute.anchor, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in absolute.parts[1:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise PathSafetyError(
                    f"unsafe or unavailable parent directory for {absolute}: {component}",
                ) from exc
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise PathSafetyError(
                    f"authority parent component is not a directory: {component}",
                )
            os.close(descriptor)
            descriptor = child
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _require_regular_file(descriptor: int, path: Path) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise PathSafetyError(f"authority path is not a regular file: {path}")


def _reject_existing_nonregular(parent_fd: int, name: str, path: Path) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise PathSafetyError(f"authority path is not a regular file: {path}")


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def safe_read_bytes(path: Path, *, missing_ok: bool = False) -> bytes | None:
    """Read a regular file without following the file or parent symlinks."""

    try:
        absolute, parent_fd = _open_parent_directory(path, create=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    try:
        _reject_existing_nonregular(parent_fd, absolute.name, absolute)
        try:
            descriptor = os.open(
                absolute.name,
                os.O_RDONLY | os.O_NONBLOCK | _FILE_NOFOLLOW_FLAGS,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        except OSError as exc:
            raise PathSafetyError(f"unsafe authority file: {absolute}") from exc
        try:
            _require_regular_file(descriptor, absolute)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def safe_append_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Append and durably publish bytes to one regular file."""

    absolute, parent_fd = _open_parent_directory(path, create=True)
    try:
        _reject_existing_nonregular(parent_fd, absolute.name, absolute)
        try:
            descriptor = os.open(
                absolute.name,
                os.O_APPEND
                | os.O_CREAT
                | os.O_WRONLY
                | os.O_NONBLOCK
                | _FILE_NOFOLLOW_FLAGS,
                mode,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise PathSafetyError(f"unsafe append authority file: {absolute}") from exc
        try:
            _require_regular_file(descriptor, absolute)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError(f"short append to {absolute}")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(parent_fd)
    finally:
        os.close(parent_fd)


def safe_atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    replace_function: Callable[[str, str], None] | None = None,
) -> None:
    """Atomically replace a regular file and fsync the containing directory."""

    absolute, parent_fd = _open_parent_directory(path, create=True)
    temporary_name = f".{absolute.name}.{os.getpid()}.{secrets.token_hex(12)}.tmp"
    temporary_created = False
    try:
        try:
            existing = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise PathSafetyError(f"replacement target is not a regular file: {absolute}")
        descriptor = os.open(
            temporary_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | _FILE_NOFOLLOW_FLAGS,
            mode,
            dir_fd=parent_fd,
        )
        temporary_created = True
        try:
            _require_regular_file(descriptor, absolute.parent / temporary_name)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError(f"short atomic write to {absolute}")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if replace_function is None:
            os.replace(
                temporary_name,
                absolute.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        else:
            pinned_parent = f"/proc/self/fd/{parent_fd}"
            replace_function(
                f"{pinned_parent}/{temporary_name}",
                f"{pinned_parent}/{absolute.name}",
            )
        temporary_created = False
        _fsync_directory(parent_fd)
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


@contextmanager
def interprocess_file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    """Hold a stable sibling lock file for the complete read/validate/write cycle."""

    lock_path, parent_fd = _open_parent_directory(Path(path), create=True)
    try:
        _reject_existing_nonregular(parent_fd, lock_path.name, lock_path)
        descriptor = os.open(
            lock_path.name,
            os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | _FILE_NOFOLLOW_FLAGS,
            0o600,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        os.close(parent_fd)
        raise PathSafetyError(f"unsafe interprocess lock path: {lock_path}") from exc
    os.close(parent_fd)
    locked = False
    try:
        _require_regular_file(descriptor, lock_path)
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


__all__ = [
    "PathSafetyError",
    "interprocess_file_lock",
    "safe_append_bytes",
    "safe_atomic_write_bytes",
    "safe_read_bytes",
]
