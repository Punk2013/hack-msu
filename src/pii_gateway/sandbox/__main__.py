"""Run an agent in a Linux sandbox. No network or filesystem fallback."""

import argparse
import os
import sys
from pathlib import Path

from pii_gateway.sandbox.runner import GatewayAddress, SandboxConfig, SandboxError, run_sandbox


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--write-root", type=Path, required=True)
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=8787)
    parser.add_argument("--local-port", type=int, default=8787)
    parser.add_argument("--runtime-root", type=Path, action="append", default=[])
    parser.add_argument("--protect", type=Path, action="append", default=[])
    parser.add_argument(
        "--demo-agent",
        action="store_true",
        help="run a copy of the installed demo_agent with the supplied Python",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    config = SandboxConfig(
        workspace=args.workspace,
        write_root=args.write_root,
        gateway=GatewayAddress(args.gateway_host, args.gateway_port),
        local_port=args.local_port,
        runtime_roots=tuple(args.runtime_root),
        protected_paths=tuple(args.protect),
        local_token=os.environ.get("PII_LOCAL_TOKEN", "local-demo-token"),
        demo_agent=args.demo_agent,
    )
    try:
        return run_sandbox(config, command).returncode
    except (SandboxError, OSError) as exc:
        print(f"sandbox refused to start: {exc}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
