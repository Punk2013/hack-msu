"""Command-line interface for Vault inspection and administration (docs/ARCHITECTURE.md §8).

Usage:
    python -m pii_gateway.vault.cli list-projects
    python -m pii_gateway.vault.cli stats <project_id> [--json]
    python -m pii_gateway.vault.cli inspect <project_id> [--json]
    python -m pii_gateway.vault.cli purge <project_id> [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from pii_gateway.vault.storage import Vault, get_vault_dir, get_vault_path


def _format_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def cmd_list_projects(args: argparse.Namespace) -> int:
    vault_dir = get_vault_dir()
    db_files = sorted(vault_dir.glob("*.db"))
    if args.json:
        data = [
            {
                "project_id": f.stem,
                "path": str(f),
                "size_bytes": f.stat().st_size,
            }
            for f in db_files
        ]
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if not db_files:
        print(f"No vault databases found in {vault_dir}")
        return 0

    print(f"Vault directory: {vault_dir}")
    print(f"{'PROJECT ID':<25} {'SIZE':<12} {'LAST MODIFIED'}")
    print("-" * 65)
    for f in db_files:
        stat = f.stat()
        dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{f.stem:<25} {_format_size(stat.st_size):<12} {dt}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = get_vault_path(args.project_id)
    if not db_path.exists():
        print(
            f"Error: Vault database for project '{args.project_id}' does not exist.",
            file=sys.stderr,
        )
        return 1

    try:
        with Vault(args.project_id) as vault:
            stats = vault.get_stats()
    except Exception as exc:
        print(f"Error reading vault for project '{args.project_id}': {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    print(f"=== Vault Statistics: {stats['project_id']} ===")
    print(f"Database Path:      {stats['db_path']}")
    print(f"Database Size:      {_format_size(stats['db_size_bytes'])}")
    print(f"Total Entities:     {stats['total_entities']}")
    print(f"Ambiguous Entities: {stats['ambiguous_entities']}")
    print(f"Total Surfaces:     {stats['total_surfaces']}")
    print(f"Total Markers:      {stats['total_markers']}")
    print(f"Total Pairs:        {stats['total_pairs']}")
    print(f"Total Key Lookups:  {stats['total_keys']}")
    print(f"Audit Log Events:   {stats['total_audit_events']}")

    if stats["entities_by_type"]:
        print("\nBreakdown by PII Type:")
        for p_type, count in sorted(stats["entities_by_type"].items()):
            print(f"  - {p_type:<15}: {count}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    db_path = get_vault_path(args.project_id)
    if not db_path.exists():
        print(
            f"Error: Vault database for project '{args.project_id}' does not exist.",
            file=sys.stderr,
        )
        return 1

    try:
        with Vault(args.project_id) as vault:
            entities = vault.list_entities()
    except Exception as exc:
        print(f"Error inspecting vault for project '{args.project_id}': {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(entities, indent=2, ensure_ascii=False))
        return 0

    if not entities:
        print(f"No entities found in vault for project '{args.project_id}'.")
        return 0

    print(f"=== Entity Mappings for Project: {args.project_id} ===")
    hdr = f"{'ENTITY ID':<16} {'TYPE':<12} {'AMB':<6} {'MARKER':<14} {'SURFACES':<10} {'PAIRS'}"
    print(hdr)
    print("-" * 65)
    for e in entities:
        marker = e["marker"] or "-"
        amb = "YES" if e["is_ambiguous"] else "no"
        row = (
            f"{e['entity_id']:<16} {e['pii_type']:<12} {amb:<6} "
            f"{marker:<14} {e['surface_count']:<10} {e['pair_count']}"
        )
        print(row)
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    db_path = get_vault_path(args.project_id)
    if not db_path.exists():
        print(
            f"Error: Vault database for project '{args.project_id}' does not exist.",
            file=sys.stderr,
        )
        return 1

    if not args.force:
        confirm = input(f"Are you sure you want to purge vault '{args.project_id}'? [y/N]: ")
        if confirm.strip().lower() not in ("y", "yes"):
            print("Purge cancelled.")
            return 0

    try:
        with Vault(args.project_id) as vault:
            vault.purge()
        print(f"Vault '{args.project_id}' successfully purged.")
        return 0
    except Exception as exc:
        print(f"Error purging vault '{args.project_id}': {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pii-vault",
        description="Vault inspection and administration tool for PII Gateway.",
    )
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # list-projects
    p_list = subparsers.add_parser("list-projects", help="List all project vault databases")
    p_list.set_defaults(func=cmd_list_projects)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show metrics and statistics for a project")
    p_stats.add_argument("project_id", help="Target project identifier")
    p_stats.set_defaults(func=cmd_stats)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect entity records in a project vault")
    p_inspect.add_argument("project_id", help="Target project identifier")
    p_inspect.set_defaults(func=cmd_inspect)

    # purge
    p_purge = subparsers.add_parser("purge", help="Purge all records in a project vault")
    p_purge.add_argument("project_id", help="Target project identifier")
    p_purge.add_argument("--force", "-f", action="store_true", help="Skip confirmation prompt")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    if not getattr(args, "subcommand", None):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
