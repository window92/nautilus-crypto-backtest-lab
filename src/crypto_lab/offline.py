"""Fail-closed Linux process network isolation for Official Runs.

The authoritative boundary is an unprivileged seccomp-BPF filter installed on
the running process with ``no_new_privs`` and ``TSYNC``.  It is inherited by
every later thread, fork, and exec, including native child programs.  The
Python socket guard remains only as observable defense in depth.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import socket
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from typing import Iterator


class NetworkAttemptBlocked(RuntimeError):
    """Raised before a Python strategy can open a network connection."""


class OfflineBoundaryUnavailable(RuntimeError):
    """Raised when the process-level boundary cannot be proved active."""


@dataclass
class OfflineNetworkEvidence:
    attempts: list[dict[str, str]]


@dataclass(frozen=True)
class ProcessIsolationEvidence:
    schema: str
    mechanism: str
    architecture: str
    no_new_privs: bool
    seccomp_mode: int
    filters_before: int
    filters_after: int
    closed_inherited_socket_descriptors: tuple[int, ...]
    current_process_probe_errno: int
    io_uring_probe_errno: int
    child_python_probe_errno: int
    child_native_probe_blocked: bool
    child_dns_probe_blocked: bool
    inherited_by_fork_exec: bool
    external_endpoint_contacted: bool

    def to_builtins(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mechanism": self.mechanism,
            "architecture": self.architecture,
            "no_new_privs": self.no_new_privs,
            "seccomp_mode": self.seccomp_mode,
            "filters_before": self.filters_before,
            "filters_after": self.filters_after,
            "closed_inherited_socket_descriptors": list(
                self.closed_inherited_socket_descriptors,
            ),
            "current_process_probe_errno": self.current_process_probe_errno,
            "io_uring_probe_errno": self.io_uring_probe_errno,
            "child_python_probe_errno": self.child_python_probe_errno,
            "child_native_probe_blocked": self.child_native_probe_blocked,
            "child_dns_probe_blocked": self.child_dns_probe_blocked,
            "inherited_by_fork_exec": self.inherited_by_fork_exec,
            "external_endpoint_contacted": self.external_endpoint_contacted,
        }


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]


_PR_SET_NO_NEW_PRIVS = 38
_SECCOMP_SET_MODE_FILTER = 1
_SECCOMP_FILTER_FLAG_TSYNC = 1
_SYS_SECCOMP_X86_64 = 317
_AUDIT_ARCH_X86_64 = 0xC000003E
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_RET_K = 0x06
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000

# x86-64 network-specific syscalls.  Blocking the complete set prevents use of
# a socket inherited from a caller as well as creation of a new one.  Generic
# read/write cannot be filtered by FD, so the dedicated Official child closes
# and records every pre-existing socket descriptor before activation.
_NETWORK_CAPABLE_SYSCALLS_X86_64 = (
    41,   # socket
    42,   # connect
    43,   # accept
    44,   # sendto
    45,   # recvfrom
    46,   # sendmsg
    47,   # recvmsg
    48,   # shutdown
    49,   # bind
    50,   # listen
    51,   # getsockname
    52,   # getpeername
    54,   # setsockopt
    55,   # getsockopt
    288,  # accept4
    299,  # recvmmsg
    307,  # sendmmsg
    # io_uring can perform socket/connect/send/receive without issuing the
    # corresponding syscall from userspace, so the complete interface is
    # unavailable inside an Official process.
    425,  # io_uring_setup
    426,  # io_uring_enter
    427,  # io_uring_register
    # Prevent importing an already-open socket descriptor from another
    # process after the inherited-descriptor sweep.
    438,  # pidfd_getfd
)

_PROCESS_ISOLATION: ProcessIsolationEvidence | None = None


def _proc_security_state() -> tuple[bool, int, int]:
    values: dict[str, int] = {}
    for line in open("/proc/self/status", encoding="utf-8"):
        key, separator, raw = line.partition(":")
        if separator and key in {"NoNewPrivs", "Seccomp", "Seccomp_filters"}:
            values[key] = int(raw.strip())
    return (
        values.get("NoNewPrivs") == 1,
        values.get("Seccomp", 0),
        values.get("Seccomp_filters", 0),
    )


def _socket_descriptors() -> tuple[tuple[int, str], ...]:
    descriptors: list[tuple[int, str]] = []
    for name in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{name}")
        except FileNotFoundError:
            continue
        if target.startswith("socket:"):
            descriptors.append((int(name), target))
    return tuple(sorted(descriptors))


def _close_inherited_sockets() -> tuple[int, ...]:
    """Close every pre-bound socket in the dedicated Official child process."""

    descriptors = _socket_descriptors()
    for descriptor, _target in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            raise OfflineBoundaryUnavailable(
                f"NETWORK_DURING_OFFICIAL_RUN: cannot close inherited socket fd {descriptor}",
            ) from exc
    remaining = _socket_descriptors()
    if remaining:
        raise OfflineBoundaryUnavailable(
            "NETWORK_DURING_OFFICIAL_RUN: inherited sockets remained after closure",
        )
    return tuple(descriptor for descriptor, _target in descriptors)


def _instruction(code: int, *, jt: int = 0, jf: int = 0, k: int = 0) -> _SockFilter:
    return _SockFilter(code=code, jt=jt, jf=jf, k=k)


def _install_seccomp_network_filter() -> tuple[int, int, tuple[int, ...]]:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise OfflineBoundaryUnavailable(
            "UNSUPPORTED_RUNTIME: seccomp boundary is qualified only for Linux x86_64",
        )
    closed_sockets = _close_inherited_sockets()
    _nnp_before, _mode_before, filters_before = _proc_security_state()
    instructions = [
        _instruction(_BPF_LD_W_ABS, k=4),
        _instruction(_BPF_JMP_JEQ_K, jt=1, jf=0, k=_AUDIT_ARCH_X86_64),
        _instruction(_BPF_RET_K, k=_SECCOMP_RET_KILL_PROCESS),
        _instruction(_BPF_LD_W_ABS, k=0),
    ]
    # Rust/Tokio uses an AF_UNIX socketpair for its local signal wakeup path.
    # Permit only that already-connected local IPC primitive.  AF_INET/INET6
    # socket creation, connect, send/receive socket syscalls, and descriptor
    # import remain denied, so the exception cannot carry network traffic.
    socketpair_check_index = len(instructions)
    instructions.append(_instruction(_BPF_JMP_JEQ_K, k=53))
    for syscall_number in _NETWORK_CAPABLE_SYSCALLS_X86_64:
        instructions.extend(
            (
                _instruction(_BPF_JMP_JEQ_K, jt=0, jf=1, k=syscall_number),
                _instruction(_BPF_RET_K, k=_SECCOMP_RET_ERRNO | errno.EPERM),
            ),
        )
    instructions.append(_instruction(_BPF_RET_K, k=_SECCOMP_RET_ALLOW))
    socketpair_handler_index = len(instructions)
    instructions[socketpair_check_index].jt = (
        socketpair_handler_index - socketpair_check_index - 1
    )
    instructions.extend(
        (
            _instruction(_BPF_LD_W_ABS, k=16),  # seccomp_data.args[0], low 32 bits
            _instruction(_BPF_JMP_JEQ_K, jt=0, jf=1, k=socket.AF_UNIX),
            _instruction(_BPF_RET_K, k=_SECCOMP_RET_ALLOW),
            _instruction(_BPF_RET_K, k=_SECCOMP_RET_ERRNO | errno.EPERM),
        ),
    )
    array_type = _SockFilter * len(instructions)
    instruction_array = array_type(*instructions)
    program = _SockFprog(
        len=len(instructions),
        filter=ctypes.cast(instruction_array, ctypes.POINTER(_SockFilter)),
    )
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OfflineBoundaryUnavailable(
            f"UNSUPPORTED_RUNTIME: PR_SET_NO_NEW_PRIVS failed with errno {error}",
        )
    result = libc.syscall(
        _SYS_SECCOMP_X86_64,
        _SECCOMP_SET_MODE_FILTER,
        _SECCOMP_FILTER_FLAG_TSYNC,
        ctypes.byref(program),
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OfflineBoundaryUnavailable(
            f"UNSUPPORTED_RUNTIME: seccomp TSYNC installation failed with errno {error}",
        )
    no_new_privs, mode_after, filters_after = _proc_security_state()
    if not no_new_privs or mode_after != 2 or filters_after <= filters_before:
        raise OfflineBoundaryUnavailable(
            "UNSUPPORTED_RUNTIME: kernel did not attest the installed seccomp filter",
        )
    raced_sockets = _socket_descriptors()
    if raced_sockets:
        for descriptor, _target in raced_sockets:
            os.close(descriptor)
        raise OfflineBoundaryUnavailable(
            "NETWORK_DURING_OFFICIAL_RUN: socket descriptor appeared during isolation",
        )
    return filters_before, filters_after, closed_sockets


def _direct_socket_errno() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(41, socket.AF_INET, socket.SOCK_STREAM, 0)
    error = ctypes.get_errno()
    if result >= 0:
        os.close(result)
        raise OfflineBoundaryUnavailable(
            "NETWORK_DURING_OFFICIAL_RUN: socket syscall remained available",
        )
    if error != errno.EPERM:
        raise OfflineBoundaryUnavailable(
            f"UNSUPPORTED_RUNTIME: socket probe failed with unexpected errno {error}",
        )
    return error


def _io_uring_errno() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(425, 1, ctypes.c_void_p())
    error = ctypes.get_errno()
    if result >= 0:
        os.close(result)
        raise OfflineBoundaryUnavailable(
            "NETWORK_DURING_OFFICIAL_RUN: io_uring remained available",
        )
    if error != errno.EPERM:
        raise OfflineBoundaryUnavailable(
            f"UNSUPPORTED_RUNTIME: io_uring probe failed with unexpected errno {error}",
        )
    return error


def _child_python_socket_errno() -> int:
    code = (
        "import errno,json,socket\n"
        "try:\n socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        "except OSError as exc:\n print(json.dumps({'errno':exc.errno}))\n"
        "else:\n print(json.dumps({'errno':0}))\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(process.stdout)
    return int(value["errno"])


def _child_native_probe() -> bool:
    process = subprocess.run(
        ["/bin/bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/9"],
        check=False,
        capture_output=True,
        text=True,
    )
    return process.returncode != 0 and (
        "Operation not permitted" in process.stderr
        or "Permission denied" in process.stderr
    )


def _child_dns_probe() -> bool:
    code = (
        "import json,socket\n"
        "try:\n socket.getaddrinfo('seccomp-probe.invalid',443)\n"
        "except socket.gaierror as exc:\n print(json.dumps({'blocked':True,'errno':exc.errno}))\n"
        "except OSError as exc:\n print(json.dumps({'blocked':True,'errno':exc.errno}))\n"
        "else:\n print(json.dumps({'blocked':False,'errno':0}))\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(json.loads(process.stdout)["blocked"])


def activate_process_network_isolation() -> ProcessIsolationEvidence:
    """Install and attest the inherited OS boundary, or fail closed."""

    global _PROCESS_ISOLATION
    if _PROCESS_ISOLATION is not None:
        if _direct_socket_errno() != errno.EPERM:
            raise OfflineBoundaryUnavailable("network boundary attestation became invalid")
        return _PROCESS_ISOLATION
    filters_before, filters_after, closed_sockets = _install_seccomp_network_filter()
    current_errno = _direct_socket_errno()
    io_uring_errno = _io_uring_errno()
    child_errno = _child_python_socket_errno()
    native_blocked = _child_native_probe()
    dns_blocked = _child_dns_probe()
    if child_errno != errno.EPERM or not native_blocked or not dns_blocked:
        raise OfflineBoundaryUnavailable(
            "UNSUPPORTED_RUNTIME: inherited child/native/DNS isolation probes did not all block",
        )
    no_new_privs, mode, observed_filters = _proc_security_state()
    if observed_filters < filters_after:
        raise OfflineBoundaryUnavailable("UNSUPPORTED_RUNTIME: seccomp filter count regressed")
    _PROCESS_ISOLATION = ProcessIsolationEvidence(
        schema="process-network-isolation-v1",
        mechanism="LINUX_SECCOMP_BPF_TSYNC_ERRNO_EPERM",
        architecture="x86_64",
        no_new_privs=no_new_privs,
        seccomp_mode=mode,
        filters_before=filters_before,
        filters_after=filters_after,
        closed_inherited_socket_descriptors=closed_sockets,
        current_process_probe_errno=current_errno,
        io_uring_probe_errno=io_uring_errno,
        child_python_probe_errno=child_errno,
        child_native_probe_blocked=native_blocked,
        child_dns_probe_blocked=dns_blocked,
        inherited_by_fork_exec=True,
        external_endpoint_contacted=False,
    )
    return _PROCESS_ISOLATION


@contextmanager
def offline_network_guard() -> Iterator[OfflineNetworkEvidence]:
    """Observable Python defense in depth; not the authoritative boundary."""

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


__all__ = [
    "NetworkAttemptBlocked",
    "OfflineBoundaryUnavailable",
    "OfflineNetworkEvidence",
    "ProcessIsolationEvidence",
    "activate_process_network_isolation",
    "offline_network_guard",
]
