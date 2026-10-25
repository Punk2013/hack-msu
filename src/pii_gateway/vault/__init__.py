"""Project-scoped mapping storage outside the workspace (role 3, issue #11)."""

from pii_gateway.vault.storage import Vault, get_vault_dir, get_vault_path

__all__ = ["Vault", "get_vault_dir", "get_vault_path"]
