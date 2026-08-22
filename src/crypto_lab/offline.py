"""Process-local network guard for isolated qualification and Official Runs."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


class NetworkAttemptBlocked(RuntimeError):
    """Raised before a Python strategy can open a network connection."""


@dataclass
class OfflineNetworkEvidence:
    attempts: list[dict[str, str]]


@contextmanager
def offline_network_guard() -> Iterator[OfflineNetworkEvidence]:
    """Block Python socket connection entry points without touching host policy."""

    original_socket = socket.socket
    original_create_connection = socket.create_connection
    evidence = OfflineNetworkEvidence(attempts=[])

    def blocked(endpoint: object, *args: object, **kwargs: object) -> None:
        del args, kwargs
        evidence.attempts.append(
            {"api": "socket.create_connection", "endpoint": repr(endpoint)},
        )
        raise NetworkAttemptBlocked("network access is forbidden during this Run")

    class OfflineSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, address: object) -> None:
            evidence.attempts.append({"api": "socket.socket.connect", "endpoint": repr(address)})
            raise NetworkAttemptBlocked("network access is forbidden during this Run")

        def connect_ex(self, address: object) -> int:
            evidence.attempts.append(
                {"api": "socket.socket.connect_ex", "endpoint": repr(address)},
            )
            raise NetworkAttemptBlocked("network access is forbidden during this Run")

    socket.socket = OfflineSocket
    socket.create_connection = blocked
    try:
        yield evidence
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create_connection


__all__ = ["NetworkAttemptBlocked", "OfflineNetworkEvidence", "offline_network_guard"]
