from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import unittest


class Aud008OfflineEnforcementTests(unittest.TestCase):
    def test_seccomp_boundary_blocks_current_child_python_native_and_dns(self) -> None:
        code = """
import json
from crypto_lab.offline import activate_process_network_isolation
evidence = activate_process_network_isolation()
print(json.dumps(evidence.to_builtins(), sort_keys=True))
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["mechanism"], "LINUX_SECCOMP_BPF_TSYNC_ERRNO_EPERM")
        self.assertEqual(evidence["current_process_probe_errno"], 1)
        self.assertEqual(evidence["io_uring_probe_errno"], 1)
        self.assertEqual(evidence["child_python_probe_errno"], 1)
        self.assertTrue(evidence["child_native_probe_blocked"])
        self.assertTrue(evidence["child_dns_probe_blocked"])
        self.assertTrue(evidence["inherited_by_fork_exec"])
        self.assertFalse(evidence["external_endpoint_contacted"])

    def test_inherited_network_socket_is_closed_before_filter_activation(self) -> None:
        inherited, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            code = """
import json
import os
import sys
from crypto_lab.offline import activate_process_network_isolation
descriptor = int(sys.argv[1])
evidence = activate_process_network_isolation()
try:
    os.write(descriptor, b'network-write-probe')
except OSError as exc:
    write_errno = exc.errno
else:
    write_errno = 0
print(json.dumps({
    'closed': descriptor in evidence.closed_inherited_socket_descriptors,
    'write_errno': write_errno,
    'external_endpoint_contacted': evidence.external_endpoint_contacted,
}, sort_keys=True))
"""
            environment = dict(os.environ)
            environment["PYTHONPATH"] = "src"
            process = subprocess.run(
                [sys.executable, "-c", code, str(inherited.fileno())],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                pass_fds=(inherited.fileno(),),
            )
            result = json.loads(process.stdout)
            self.assertTrue(result["closed"])
            self.assertEqual(result["write_errno"], errno.EBADF)
            self.assertFalse(result["external_endpoint_contacted"])
        finally:
            inherited.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
