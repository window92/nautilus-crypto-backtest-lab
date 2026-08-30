"""Small Linux interprocess lock primitive for append-only authorities."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def interprocess_file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    """Hold a stable sibling lock file for the complete read/validate/write cycle."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
