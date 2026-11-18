"""Named host<->guest channels (HMP monitor, serial console) over a socket.

Every channel is a QEMU ``socket`` chardev in server mode. On POSIX it listens
on a Unix socket inside the runtime directory (``<name>.sock``). On Windows,
or when forced, it listens on an ephemeral port of ``127.0.0.1`` whose number
is recorded next to where the socket would be (``<name>.port``). Clients find
the endpoint from those files, so any process can attach to a running VM.
"""
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from .config import VmError
from .paths import WINDOWS, host_os

FORCE_TCP_ENV = "LOBSTERVM_FORCE_TCP"
LOOPBACK = "127.0.0.1"
#: Conservative bound under the smallest ``sun_path`` (104 bytes on macOS).
UNIX_PATH_LIMIT = 100
CONNECT_TIMEOUT_S = 10.0
CONNECT_RETRY_S = 0.05
DRAIN_TIMEOUT_S = 10.0
DRAIN_CHUNK_BYTES = 4096
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def force_tcp():
    """True when the transport must be TCP (Windows, or the hidden env switch)."""
    return host_os() == WINDOWS or bool(os.environ.get(FORCE_TCP_ENV))


def _qemu_text(text):
    return str(text).replace(",", ",,")


def free_port():
    """An unused loopback TCP port (bind to 0, read it back, release it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


@dataclass(frozen=True)
class Endpoint:
    """Where a channel listens: a Unix socket path or a loopback TCP port."""

    path: str = ""
    port: int = 0

    @property
    def is_tcp(self):
        return self.port > 0

    def chardev_options(self):
        """The socket-chardev address options QEMU needs to listen here."""
        if self.is_tcp:
            return f"host={LOOPBACK},port={self.port},server=on,wait=off"
        return f"path={_qemu_text(self.path)},server=on,wait=off"

    def connect(self, timeout=CONNECT_TIMEOUT_S):
        """Open a client socket, retrying until the server is listening."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._connect_once(timeout)
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise VmError(f"cannot connect to {self}: {error}") from error
            time.sleep(CONNECT_RETRY_S)

    def send(self, data, drain_timeout=DRAIN_TIMEOUT_S):
        """Deliver ``data`` and wait until QEMU has consumed all of it.

        QEMU drops unread bytes when the peer closes outright, and a UART
        accepts only a few bytes at a time. So the write side is shut down
        and the call returns once QEMU has read everything and closed (or
        after ``drain_timeout``).
        """
        with self.connect() as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            sock.settimeout(CONNECT_RETRY_S)
            deadline = time.monotonic() + drain_timeout
            while time.monotonic() < deadline and _still_open(sock):
                pass

    def _connect_once(self, timeout):
        if self.is_tcp:
            return socket.create_connection((LOOPBACK, self.port), timeout)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(self.path)
        except OSError:
            sock.close()
            raise
        return sock

    def __str__(self):
        return f"{LOOPBACK}:{self.port}" if self.is_tcp else self.path


def _still_open(sock):
    """Discard what the guest printed; False once the server closed."""
    try:
        return bool(sock.recv(DRAIN_CHUNK_BYTES))
    except socket.timeout:
        return True
    except OSError:
        return False


class Channel:
    """One named channel of a VM, rooted in its runtime directory."""

    def __init__(self, runtime_dir, name):
        self.name = name
        self.socket_path = Path(runtime_dir) / f"{name}.sock"
        self.port_path = Path(runtime_dir) / f"{name}.port"

    def clear(self):
        """Remove leftovers of a previous run."""
        self.socket_path.unlink(missing_ok=True)
        self.port_path.unlink(missing_ok=True)

    def allocate(self):
        """Choose and record the endpoint for a VM that is about to start."""
        self.clear()
        if force_tcp() or len(str(self.socket_path).encode()) >= UNIX_PATH_LIMIT:
            port = free_port()
            self.port_path.write_text(json.dumps({"port": port}) + "\n")
            return Endpoint(port=port)
        return Endpoint(path=str(self.socket_path))

    def locate(self):
        """The endpoint of the running VM, from the files it left behind."""
        try:
            port = json.loads(self.port_path.read_text())["port"]
        except FileNotFoundError:
            return Endpoint(path=str(self.socket_path))
        except (ValueError, KeyError, TypeError) as error:
            raise VmError(f"corrupt port file {self.port_path}") from error
        return Endpoint(port=int(port))

    def is_ready(self):
        """True once the server accepts connections."""
        endpoint = self.locate()
        if not endpoint.is_tcp and not self.socket_path.exists():
            return False
        try:
            endpoint.connect(timeout=CONNECT_RETRY_S).close()
        except VmError:
            return False
        return True

    def restrict(self):
        """Make the Unix socket owner-only (TCP has no file to protect)."""
        if self.socket_path.exists():
            os.chmod(self.socket_path, PRIVATE_FILE_MODE)
