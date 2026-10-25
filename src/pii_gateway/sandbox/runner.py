"""Linux isolation with no host network and a fixed-target UNIX relay."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pii_gateway.sandbox._bridge import tunnel

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = Path.home() / ".local/share/pii-gateway/vault"


class SandboxError(RuntimeError):
    """The agent was not launched; there is no unprotected fallback."""


@dataclass(frozen=True)
class GatewayAddress:
    host: str = "127.0.0.1"
    port: int = 8787

    def validate(self) -> None:
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise SandboxError("gateway must be an explicit loopback IP") from exc
        if not address.is_loopback or not 1 <= self.port <= 65535:
            raise SandboxError("gateway must have a loopback IP and a port from 1 to 65535")


@dataclass(frozen=True)
class SandboxConfig:
    workspace: Path
    write_root: Path
    gateway: GatewayAddress = field(default_factory=GatewayAddress)
    local_port: int = 8787
    runtime_roots: tuple[Path, ...] = ()
    protected_paths: tuple[Path, ...] = ()
    local_token: str = "local-demo-token"
    demo_agent: bool = False

    def validate(self) -> None:
        self.gateway.validate()
        if sys.platform != "linux":
            raise SandboxError("the sandbox supports Linux only")
        if not 1 <= self.local_port <= 65535:
            raise SandboxError("invalid sandbox listener port")
        workspace = self.workspace.resolve(strict=True)
        output = self.write_root.resolve(strict=True)
        if not workspace.is_dir() or not output.is_dir():
            raise SandboxError("workspace and write root must be existing directories")
        if output == workspace or not output.is_relative_to(workspace):
            raise SandboxError("write root must be a dedicated directory inside workspace")
        protected = [PACKAGE_ROOT, DEFAULT_VAULT, *self.protected_paths]
        # Match vault.storage's path semantics without creating/opening the vault.
        configured_vault = os.environ.get("PII_VAULT_DIR")
        if configured_vault:
            protected.append(Path(configured_vault).expanduser())
        roots = [workspace, *self.runtime_roots]
        roots += [
            Path(root)
            for root in ("/usr", "/bin", "/sbin", "/lib", "/lib64")
            if Path(root).exists()
        ]
        for root in roots:
            root = root.resolve(strict=True)
            if root in (
                Path("/"),
                Path("/home"),
                Path.home(),
                Path("/run"),
                Path("/tmp"),
                Path("/etc"),
                Path("/var"),
                Path("/proc"),
                Path("/sys"),
                Path("/dev"),
            ):
                raise SandboxError("mounting a whole home or host service directory is forbidden")
            for secret in protected:
                secret = secret.resolve()
                if root.is_relative_to(secret) or secret.is_relative_to(root):
                    raise SandboxError("an exported directory overlaps protected gateway data")
        for root in self.runtime_roots:
            if not root.is_dir():
                raise SandboxError("runtime roots must be directories")
        for root in (workspace, *self.runtime_roots):
            for directory, _, names in os.walk(root, followlinks=False):
                for name in names:
                    info = (Path(directory) / name).lstat()
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                        raise SandboxError("exported trees cannot contain host IPC/device files")
                    if root == workspace and stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                        raise SandboxError("workspace files cannot be hard links to host data")


class FixedRelay:
    """Expose only one fixed host TCP endpoint through a private UNIX socket."""

    def __init__(self, path: Path, target: GatewayAddress):
        target.validate()
        self.path = path
        self.target = target
        self.server = None
        self.thread = None

    def __enter__(self):
        target = self.target
        slots = threading.BoundedSemaphore(16)

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not slots.acquire(blocking=False):
                    return
                try:
                    with socket.create_connection((target.host, target.port), timeout=5) as peer:
                        tunnel(self.request, peer)
                except OSError:
                    return
                finally:
                    slots.release()

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                # Never print potentially sensitive exceptions/request contents.
                return

        self.server = Server(str(self.path), Handler)
        self.path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)


def build_command(config: SandboxConfig, state: Path, command: list[str]) -> list[str]:
    config.validate()
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxError("bubblewrap is required; agent was not started")
    if not command:
        raise SandboxError("an agent command is required")
    args = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]
    for root in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        path = Path(root)
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), root]
        elif path.exists():
            args += ["--ro-bind", root, root]
    for root in config.runtime_roots:
        root = str(root.resolve())
        args += ["--ro-bind", root, root]
    workspace = str(config.workspace.resolve())
    output = str(config.write_root.resolve())
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home/agent",
        "--dir",
        "/etc",
        "--dir",
        "/run/pii-sandbox",
        "--ro-bind",
        str(state / "passwd"),
        "/etc/passwd",
        "--ro-bind",
        str(state / "group"),
        "/etc/group",
        "--ro-bind",
        workspace,
        workspace,
        "--bind",
        output,
        output,
        "--ro-bind",
        str(state / "bridge.py"),
        "/run/pii-sandbox/bridge.py",
        "--ro-bind",
        str(state / "relay.sock"),
        "/run/pii-sandbox/relay.sock",
    ]
    if config.demo_agent:
        args += ["--ro-bind", str(state / "demo_agent.py"), "/run/pii-sandbox/demo_agent.py"]
        command = [command[0], "/run/pii-sandbox/demo_agent.py", *command[1:]]
    safe_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/agent",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{config.local_port}/v1",
        "OPENAI_API_KEY": config.local_token,
        "PII_LOCAL_TOKEN": config.local_token,
        "NO_PROXY": "127.0.0.1,localhost",
    }
    for name, value in safe_env.items():
        args += ["--setenv", name, value]
    args += [
        "--chdir",
        workspace,
        "/usr/bin/python3",
        "/run/pii-sandbox/bridge.py",
        "--socket",
        "/run/pii-sandbox/relay.sock",
        "--port",
        str(config.local_port),
        "--",
        *command,
    ]
    return args


def run_sandbox(
    config: SandboxConfig,
    command: list[str],
    *,
    capture_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="pii-sandbox-") as directory:
        state = Path(directory)
        shutil.copyfile(Path(__file__).with_name("_bridge.py"), state / "bridge.py")
        (state / "passwd").write_text(f"agent:x:{os.getuid()}:{os.getgid()}::/home/agent:/bin/sh\n")
        (state / "group").write_text(f"agent:x:{os.getgid()}:\n")
        if config.demo_agent:
            shutil.copyfile(PACKAGE_ROOT / "demo_agent.py", state / "demo_agent.py")
        args = build_command(config, state, command)
        with FixedRelay(state / "relay.sock", config.gateway):
            return subprocess.run(
                args,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                check=False,
                close_fds=True,
            )
