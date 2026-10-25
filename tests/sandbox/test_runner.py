import json
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from pii_gateway.core.types import RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.sandbox.leaks import Canary, LeakScanner
from pii_gateway.sandbox.runner import (
    GatewayAddress,
    SandboxConfig,
    SandboxError,
    build_command,
    run_sandbox,
)
from pii_gateway.sandbox.upstream_spy import UpstreamSpy


@pytest.fixture
def config(tmp_path):
    # macOS TMPDIR can exceed the pathname length supported by UNIX sockets.
    directory = "/tmp" if Path("/tmp").is_dir() else tmp_path
    with tempfile.TemporaryDirectory(prefix="pii-sb-", dir=directory) as root:
        work = Path(root) / "workspace"
        work.mkdir()
        output = work / "out"
        output.mkdir()
        yield SandboxConfig(work, output)


@pytest.fixture
def linux_profile(monkeypatch):
    """Pure profile tests simulate Linux; real execution keeps the real OS guard."""
    monkeypatch.setattr(sys, "platform", "linux")


def test_non_linux_is_refused_before_launch(config, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(SandboxError, match="Linux only"):
        config.validate()


@pytest.mark.parametrize("host", ["localhost", "0.0.0.0", "192.0.2.1", "*"])
def test_gateway_has_one_numeric_loopback_target(host):
    with pytest.raises(SandboxError):
        GatewayAddress(host, 8787).validate()


@pytest.mark.usefixtures("linux_profile")
def test_export_symlink_outside_workspace_is_refused(config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = config.workspace / "bad-export"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxError, match="dedicated directory"):
        SandboxConfig(config.workspace, link).validate()


@pytest.mark.usefixtures("linux_profile")
def test_protected_file_cannot_be_inside_export(config):
    with pytest.raises(SandboxError, match="protected"):
        SandboxConfig(
            config.workspace, config.write_root, protected_paths=(config.workspace / "gateway.env",)
        ).validate()


@pytest.mark.usefixtures("linux_profile")
@pytest.mark.parametrize("path_kind", ["absolute", "relative", "symlink"])
@pytest.mark.parametrize("export_kind", ["workspace", "runtime"])
def test_configured_vault_cannot_be_exported(config, monkeypatch, path_kind, export_kind):
    exported = config.workspace
    runtimes = ()
    if export_kind == "runtime":
        exported = config.workspace.parent / "runtime"
        exported.mkdir()
        runtimes = (exported,)
    vault = exported / "private-vault"
    # Even a not-yet-created vault must be protected before its first write.
    configured = str(vault)
    if path_kind == "relative":
        monkeypatch.chdir(exported.parent)
        configured = str(vault.relative_to(exported.parent))
    elif path_kind == "symlink":
        link = config.workspace.parent / "vault-link"
        link.symlink_to(vault, target_is_directory=True)
        configured = str(link)
    monkeypatch.setenv("PII_VAULT_DIR", configured)
    with pytest.raises(SandboxError, match="protected"):
        SandboxConfig(config.workspace, config.write_root, runtime_roots=runtimes).validate()
    assert not vault.exists()


@pytest.mark.usefixtures("linux_profile")
def test_configured_vault_outside_export_is_allowed(config, monkeypatch):
    vault = config.workspace.parent / "private-vault"
    monkeypatch.setenv("PII_VAULT_DIR", str(vault))
    config.validate()
    assert not vault.exists()


@pytest.mark.usefixtures("linux_profile")
def test_host_unix_socket_inside_workspace_is_refused(config):
    with socket.socket(socket.AF_UNIX) as peer:
        peer.bind(str(config.workspace / "host-service.sock"))
        with pytest.raises(SandboxError, match="IPC"):
            config.validate()


@pytest.mark.usefixtures("linux_profile")
def test_hard_link_to_host_file_is_refused(config):
    secret = config.workspace.parent / "secret"
    secret.write_text("synthetic secret")
    (config.workspace / "alias").hardlink_to(secret)
    with pytest.raises(SandboxError, match="hard links"):
        config.validate()


@pytest.mark.usefixtures("linux_profile")
def test_no_bubblewrap_means_no_unprotected_fallback(config, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SandboxError, match="not started"):
        build_command(config, tmp_path, ["/usr/bin/true"])


@pytest.mark.usefixtures("linux_profile")
def test_command_drops_host_environment_and_all_network_namespaces(config, tmp_path, monkeypatch):
    # Command construction is portable; real execution is tested separately.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/bwrap")
    args = build_command(config, tmp_path, ["/usr/bin/true"])
    assert "--unshare-all" in args
    assert "--share-net" not in args
    assert "--clearenv" in args
    assert "--new-session" in args
    assert "PII_UPSTREAM_API_KEY" not in args
    assert "--ro-bind" in args


@pytest.fixture
def working_sandbox(config):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        pytest.skip("Linux with bubblewrap required; isolation was not tested")
    try:
        result = run_sandbox(config, ["/usr/bin/true"], capture_output=True, timeout=15)
    except OSError as exc:
        pytest.skip(f"sandbox unavailable in this execution environment: {exc.errno}")
    if result.returncode:
        if any(
            message in result.stderr
            for message in (
                "Operation not permitted",
                "No permissions to create",
                "not allowed",
            )
        ):
            pytest.skip("kernel namespace restrictions prevent bubblewrap; isolation NOT tested")
        pytest.fail(f"sandbox preflight failed unexpectedly: {result.stderr}")
    return config


def test_real_namespace_blocks_child_egress_vault_symlinks_and_host_credentials(
    working_sandbox,
    tmp_path,
    monkeypatch,
):
    config = working_sandbox
    vault = tmp_path / "private-vault"
    vault.mkdir()
    secrets = [vault / name for name in ("project.db", "project.db-wal", "project.db-shm")]
    secrets += [tmp_path / "gateway.env", tmp_path / "gateway.py"]
    for path in secrets:
        path.write_text("synthetic protected data")
    (config.workspace / "input.txt").write_text("synthetic input")
    (config.workspace / "escape").symlink_to(secrets[0])
    (config.write_root / "escape").symlink_to(secrets[0])
    monkeypatch.setenv("PII_UPSTREAM_API_KEY", "synthetic-provider-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-host-secret")
    with UpstreamSpy(LeakScanner([Canary("person", "Иван Иванов")])) as allowed:
        with UpstreamSpy(LeakScanner([Canary("person", "Иван Иванов")])) as forbidden:
            denied_port = forbidden.server.server_port
            child = (
                "import json,socket,sys\n"
                "try:\n"
                f" s=socket.create_connection(('127.0.0.1',{denied_port}),timeout=.5)\n"
                "except OSError as exc:\n"
                " print(json.dumps({'blocked':True,'errno':exc.errno})); sys.exit(0)\n"
                "s.sendall(b'synthetic leak'); print(json.dumps({'blocked':False})); sys.exit(9)\n"
            )
            # The forbidden receiver must be reachable from the host first.
            response = httpx.post(
                forbidden.base_url + "/chat/completions",
                json={"content": "Иван Иванов"},
                trust_env=False,
            )
            assert response.status_code == 200
            assert forbidden.report()["requests_with_canaries"] == 1
            script = config.workspace / "probe.py"
            gateway_source = Path(__file__).parents[2] / "src/pii_gateway/gateway/app.py"
            script.write_text(
                "import json,os,socket,subprocess,sys,urllib.request\n"
                "from pathlib import Path\n"
                "facts={}\n"
                "req=urllib.request.Request(os.environ['OPENAI_BASE_URL']+'/chat/completions',"
                "data=b'{\"content\":\"[PERSON_1]\"}',headers={'Content-Type':'application/json'})\n"
                "facts['allowed_gateway']=urllib.request.urlopen(req,timeout=5).status==200\n"
                f"child=subprocess.run([sys.executable,'-c',{child!r}],capture_output=True)\n"
                "facts['child_egress_blocked']=(child.returncode==0 and "
                "json.loads(child.stdout)['blocked'])\n"
                f"for name in {[str(p) for p in secrets]!r} + ['escape','out/escape']:\n"
                " try: Path(name).read_text(); facts[name]=False\n"
                " except OSError: facts[name]=True\n"
                # An invisible host path can have an ephemeral shadow in /tmp.
                # The host-side assertions below prove originals were not changed.
                " try: Path(name).write_text('tampered')\n"
                " except OSError: pass\n"
                f"facts['gateway_source_hidden']=not Path({str(gateway_source)!r}).exists()\n"
                "facts['input_readable']=Path('input.txt').read_text()=='synthetic input'\n"
                "try: Path('input.txt').write_text('tampered'); facts['input_readonly']=False\n"
                "except OSError: facts['input_readonly']=True\n"
                "Path('out/result.txt').write_text('synthetic restored output')\n"
                "facts['host_credentials_absent']=all(k not in os.environ for k in "
                "('PII_UPSTREAM_API_KEY','AWS_SECRET_ACCESS_KEY'))\n"
                "facts['local_token_only']=os.environ['OPENAI_API_KEY']=='local-demo-token'\n"
                "print(json.dumps(facts))\n"
            )
            run_config = SandboxConfig(
                config.workspace,
                config.write_root,
                GatewayAddress("127.0.0.1", allowed.server.server_port),
                protected_paths=tuple(secrets),
            )
            result = run_sandbox(
                run_config, ["/usr/bin/python3", str(script)], capture_output=True, timeout=20
            )
            assert result.returncode == 0, result.stderr
            facts = json.loads(result.stdout)
            assert all(facts.values()), facts
            assert allowed.report()["requests"] == 1
            assert forbidden.report()["requests"] == 1  # host positive control only
            assert (config.write_root / "result.txt").read_text() == "synthetic restored output"
            assert (config.workspace / "input.txt").read_text() == "synthetic input"
            assert all(path.read_text() == "synthetic protected data" for path in secrets)


def test_installed_demo_agent_reaches_authenticated_gateway_through_relay(working_sandbox):
    config = working_sandbox
    real = "Иван Иванов"

    class Transformer:
        def mask(self, text):
            return text.replace(real, "[PERSON_1]")

        def restore(self, text):
            return RestoreResult(text.replace("[PERSON_1]", real), RestoreStatus.COMPLETE, [])

    with UpstreamSpy(LeakScanner([Canary("person", real)]), reply_text="[PERSON_1]") as spy:
        app = create_app(
            Settings(
                upstream_base_url=spy.base_url,
                workspace=config.workspace,
                local_token="local-demo-token",
            ),
            Transformer(),
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            try:
                # Editable installation: export the interpreter's runtime, but only
                # the demo client script, never the source gateway package.
                runtimes = tuple(
                    dict.fromkeys(
                        Path(root).resolve()
                        for root in (sys.prefix, sys.base_prefix)
                        if not Path(root).resolve().is_relative_to(Path("/usr"))
                    )
                )
                run_config = SandboxConfig(
                    config.workspace,
                    config.write_root,
                    GatewayAddress("127.0.0.1", port),
                    runtime_roots=runtimes,
                    demo_agent=True,
                )
                result = run_sandbox(
                    run_config,
                    [
                        sys.executable,
                        "--base-url",
                        "http://127.0.0.1:8787/v1",
                        "--workspace",
                        str(config.workspace),
                        "--model",
                        "synthetic",
                        "--max-steps",
                        "1",
                        real,
                    ],
                    capture_output=True,
                    timeout=45,
                )
                assert result.returncode == 0, result.stderr
                assert real in result.stdout
                assert spy.report()["requests"] == 1
                assert spy.report()["requests_with_canaries"] == 0
            finally:
                server.should_exit = True
                thread.join(timeout=5)


def test_namespace_does_not_deliver_dns_datagrams_to_host(working_sandbox):
    config = working_sandbox
    # A syntactically normal query for example.test, sent only to our local receiver.
    query = bytes.fromhex("123401000001000000000000076578616d706c6504746573740000010001")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.5)
        port = receiver.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as positive:
            positive.sendto(query, ("127.0.0.1", port))
        assert receiver.recvfrom(4096)[0] == query
        script = (
            "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
            f"s.sendto(bytes.fromhex('{query.hex()}'),('127.0.0.1',{port}))"
        )
        result = run_sandbox(
            config, ["/usr/bin/python3", "-c", script], capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
        with pytest.raises(TimeoutError):
            receiver.recvfrom(4096)


def test_namespace_cannot_connect_to_ipv6_host_listener(working_sandbox):
    with socket.socket(socket.AF_INET6) as listener:
        try:
            listener.bind(("::1", 0))
        except OSError:
            pytest.skip("host has no IPv6 loopback; IPv6 isolation was not tested")
        listener.listen(1)
        listener.settimeout(0.5)
        port = listener.getsockname()[1]
        with socket.create_connection(("::1", port), timeout=1):
            positive, _ = listener.accept()
            positive.close()
        script = (
            "import socket,sys\n"
            "try:\n"
            f" s=socket.create_connection(('::1',{port}),timeout=.5)\n"
            "except OSError: sys.exit(0)\n"
            "sys.exit(9)\n"
        )
        result = run_sandbox(
            working_sandbox, ["/usr/bin/python3", "-c", script], capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
        with pytest.raises(TimeoutError):
            listener.accept()
