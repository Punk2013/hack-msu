from __future__ import annotations

import os
from pathlib import Path

import pytest

from pii_gateway.core.types import PiiType, VaultError
from pii_gateway.vault import Vault, get_vault_path


def test_vault_isolation_and_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vaults"))

    path1 = get_vault_path("project_alpha")
    path2 = get_vault_path("project_beta")
    assert path1 != path2
    assert path1.suffix == ".db"
    assert path2.suffix == ".db"


def test_vault_path_traversal_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vaults"))
    with pytest.raises(VaultError):
        get_vault_path("../evil")
    with pytest.raises(VaultError):
        get_vault_path("project/sub")


def test_vault_entity_crud_and_markers(tmp_path: Path):
    db_file = tmp_path / "test_proj.db"
    with Vault("test_proj", db_path=db_file) as vault:
        # Counter increments atomically
        idx1 = vault.get_next_marker_index(PiiType.PERSON)
        idx2 = vault.get_next_marker_index(PiiType.PERSON)
        idx_email = vault.get_next_marker_index(PiiType.EMAIL)
        assert idx1 == 1
        assert idx2 == 2
        assert idx_email == 1

        # Add entity & surfaces
        vault.add_entity("ent_1", PiiType.PERSON)
        vault.add_surface("s_1", "ent_1", side="original", text="Иван Иванов", form="nomn")
        vault.add_marker("[PERSON_1]", "ent_1", PiiType.PERSON)

        # Lookup
        found = vault.find_entity_by_original_text("Иван Иванов", PiiType.PERSON)
        assert found is not None
        assert found.entity_id == "ent_1"

        orig_from_marker = vault.find_original_by_marker("[PERSON_1]")
        assert orig_from_marker == "Иван Иванов"

        mappings = vault.get_all_marker_mappings()
        assert mappings == {"[PERSON_1]": "Иван Иванов"}

        # Purge
        vault.purge()
        assert vault.find_entity_by_original_text("Иван Иванов") is None
        assert vault.get_all_marker_mappings() == {}


def test_vault_file_permissions_posix(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("POSIX permission check only on non-Windows")
    db_file = tmp_path / "perm_test.db"
    with Vault("perm_test", db_path=db_file):
        pass
    stat = db_file.stat()
    assert (stat.st_mode & 0o777) == 0o600
