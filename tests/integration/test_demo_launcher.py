"""Exercise shell orchestration with stub processes, without a model installation."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="bash demo launcher")
def test_demo_launch_keeps_private_artifacts_outside_agent_workspace(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "demo/clients").mkdir(parents=True)
    (project / "demo/clients/client.txt").write_text("synthetic")
    source = Path(__file__).resolve().parents[2] / "scripts/demo.sh"
    shutil.copyfile(source, project / "scripts/demo.sh")
    binaries = project / ".venv/bin"
    binaries.mkdir(parents=True)
    # uvicorn only needs to stay alive while readiness and client stubs run.
    scripts = {
        "uvicorn": "#!/bin/sh\nexec sleep 10\n",
        "curl": "#!/bin/sh\nexit 0\n",
        "python": ('#!/bin/sh\n'
                   'printf \'%s\\n\' "$PII_VAULT_DIR" "$PII_DUMP_DIR" "$*" >> "$CHECK_LOG"\n'),
    }
    for name, content in scripts.items():
        path = binaries / name
        path.write_text(content)
        path.chmod(0o700)
    log = tmp_path / "calls"
    subprocess.run(
        ["bash", "scripts/demo.sh"],
        cwd=project,
        check=True,
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(tmp_path),
            "CHECK_LOG": str(log),
        },
        capture_output=True,
        timeout=15,
    )
    vault, dumps, agent_args, *_ = log.read_text().splitlines()
    workspace = Path(agent_args.split("--workspace ", 1)[1].split(" ", 1)[0])
    for private in (Path(vault), Path(dumps)):
        assert private != workspace and workspace not in private.parents
    assert (workspace / "clients/client.txt").read_text() == "synthetic"
    assert not list((project / "demo").glob(".run*"))
    assert (workspace.parent.stat().st_mode & 0o777) == 0o700
