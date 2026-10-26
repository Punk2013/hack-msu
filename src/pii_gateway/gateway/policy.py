"""Tool call policy (docs/ARCHITECTURE.md §6).

Decides whether a tool call from the model reaches the agent and whether
real values are substituted into its arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pii_gateway.core.types import RestoreStatus
from pii_gateway.gateway.transform import Transformer


class ToolClass(StrEnum):
    LOCAL_READ = "local_read"
    LOCAL_WRITE = "local_write"
    SHELL = "shell"
    NETWORK = "network"
    DENY = "deny"


@dataclass(frozen=True)
class ToolRule:
    cls: ToolClass
    path_args: tuple[str, ...] = ()


# Tools of the built-in demo agent; other clients add their own names in config.
DEFAULT_RULES: dict[str, ToolRule] = {
    "read_file": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "list_files": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "write_file": ToolRule(ToolClass.LOCAL_WRITE, ("path",)),
    # pi coding agent (scripts/pi.sh runs it without bash)
    "read": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "ls": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "grep": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "find": ToolRule(ToolClass.LOCAL_READ, ("path",)),
    "write": ToolRule(ToolClass.LOCAL_WRITE, ("path",)),
    "edit": ToolRule(ToolClass.LOCAL_WRITE, ("path",)),
}


@dataclass
class Decision:
    allowed: bool
    arguments: dict[str, Any] | None = None
    reason: str = ""


def map_strings(obj: Any, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [map_strings(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    return obj


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


@dataclass
class ToolPolicy:
    workspace: Path
    write_roots: list[Path]
    rules: dict[str, ToolRule] = field(default_factory=lambda: dict(DEFAULT_RULES))

    def _resolve(self, value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        # resolve() follows symlinks, so a link pointing outside the root is caught
        return (self.workspace / value).resolve()

    def decide(self, name: str, arguments: dict[str, Any], t: Transformer) -> Decision:
        rule = self.rules.get(name, ToolRule(ToolClass.DENY))
        if rule.cls in (ToolClass.NETWORK, ToolClass.DENY):
            return Decision(False, reason=f"tool class {rule.cls}")
        if rule.cls is ToolClass.SHELL:
            return Decision(True, arguments)  # pseudonyms stay as they are

        complete = True

        def restore(s: str) -> str:
            nonlocal complete
            result = t.restore(s)
            complete = complete and result.status is RestoreStatus.COMPLETE
            return result.text

        restored = map_strings(arguments, restore)
        roots = [self.workspace.resolve()]
        if rule.cls is ToolClass.LOCAL_WRITE:
            if not complete:
                return Decision(False, reason="incomplete restore")
            roots = [r.resolve() for r in self.write_roots]
        default = "." if rule.cls is ToolClass.LOCAL_READ else None
        for arg in rule.path_args:
            path = self._resolve(restored.get(arg, default))
            if path is None or not _inside(path, roots):
                return Decision(False, reason=f"path argument {arg!r} outside allowed roots")
        return Decision(True, restored)
